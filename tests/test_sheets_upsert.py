from jbcub_bot.core import sheets
from jbcub_bot.core.models import User


def test_upsert_inserts_new(session):
    sheets.upsert_users(session, [
        {"matriculation": "1", "first_name": "Ivan", "last_name": "Ivanov",
         "handle_sheet": "ivan", "primary_cohort": "2024"},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.first_name == "Ivan"
    assert u.last_name == "Ivanov"
    assert u.primary_cohort == "2024"


def test_upsert_preserves_bot_owned_fields(session):
    session.add(User(matriculation="1", last_name="Old", telegram_id=777,
                     status_line="hi", handle_observed="ivan_obs",
                     visibility={"gmail": "nobody"}))
    session.commit()
    sheets.upsert_users(session, [
        {"matriculation": "1", "last_name": "New", "handle_sheet": "ivan_sheet"},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.last_name == "New"          # sheet-owned updated
    assert u.handle_sheet == "ivan_sheet"
    assert u.telegram_id == 777          # bot-owned preserved
    assert u.status_line == "hi"
    assert u.handle_observed == "ivan_obs"
    assert u.visibility == {"gmail": "nobody"}


def test_upsert_converts_role_string(session):
    from jbcub_bot.core.models import Role
    sheets.upsert_users(session, [
        {"matriculation": "1", "last_name": "Boss", "role": "Admin"},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.role is Role.ADMIN


def test_upsert_blank_role_keeps_default(session):
    from jbcub_bot.core.models import Role
    sheets.upsert_users(session, [
        {"matriculation": "1", "last_name": "Stud", "role": ""},
    ])
    u = session.query(User).filter_by(matriculation="1").one()
    assert u.role is Role.STUDENT


def test_upsert_clears_the_departed_mark_when_the_roster_names_them_again(session):
    # Re-appearing in the roster is the only way back: nobody clears the mark by
    # hand, and a returning student's fields must start updating again.
    session.add(User(matriculation="1", last_name="Back", primary_cohort="2024",
                     departed_at="2026-07-01"))
    session.commit()

    sheets.upsert_users(session, [{"matriculation": "1", "last_name": "Back"}])

    assert session.query(User).filter_by(matriculation="1").one().departed_at is None


def test_mark_departed_marks_the_member_this_roster_no_longer_names(session):
    session.add_all([
        User(matriculation="1", last_name="Stays", primary_cohort="2024"),
        User(matriculation="2", first_name="Eve", last_name="Expelled",
             primary_cohort="2024"),
    ])
    session.commit()

    marked = sheets.mark_departed(session, "2024", [{"matriculation": "1"}],
                                  "2026-07-28")

    assert marked == [
        sheets.DepartedUser(matriculation="2", full_name="Eve Expelled")
    ]
    stays, left = (session.query(User).filter_by(matriculation=m).one()
                   for m in ("1", "2"))
    assert stays.departed_at is None
    assert left.departed_at == "2026-07-28"


def test_mark_departed_leaves_a_rights_only_admin_alone(session):
    from jbcub_bot.core.models import Role

    # Admins and teachers come from the Rights tab: no cohort, keyed on their
    # handle. Every cohort roster is missing them, so a sync that swept up
    # whoever it could not find would hide the program's own staff.
    session.add(User(handle_sheet="boss", last_name="Boss", role=Role.ADMIN))
    session.commit()

    marked = sheets.mark_departed(session, "2024", [{"matriculation": "1"}],
                                  "2026-07-28")

    assert marked == []
    assert session.query(User).filter_by(handle_sheet="boss").one().departed_at is None


def test_mark_departed_never_reaches_into_another_cohort(session):
    # 2023's students are absent from 2024's roster by definition.
    session.add(User(matriculation="9", last_name="Older", primary_cohort="2023"))
    session.commit()

    marked = sheets.mark_departed(session, "2024", [{"matriculation": "1"}],
                                  "2026-07-28")

    assert marked == []
    assert session.query(User).filter_by(matriculation="9").one().departed_at is None


def test_mark_departed_spares_a_member_who_has_no_matriculation_yet(session):
    # The roster is keyed on matriculation, so a row without one was never
    # matched against it and its absence there says nothing.
    session.add(User(matriculation=None, last_name="Pending",
                     primary_cohort="2024"))
    session.commit()

    marked = sheets.mark_departed(session, "2024", [{"matriculation": "1"}],
                                  "2026-07-28")

    assert marked == []
    assert session.query(User).filter_by(last_name="Pending").one().departed_at is None


def test_mark_departed_keeps_the_date_of_the_sync_that_first_missed_them(session):
    # The date answers "when did they leave the roster?" -- a later sync
    # overwriting it with today would turn the answer into "just now, always".
    session.add(User(matriculation="2", last_name="Left", primary_cohort="2024"))
    session.commit()
    sheets.mark_departed(session, "2024", [{"matriculation": "1"}], "2026-07-01")

    marked = sheets.mark_departed(session, "2024", [{"matriculation": "1"}],
                                  "2026-07-28")

    assert marked == []  # nothing new to report on a repeat sync
    assert session.query(User).filter_by(matriculation="2").one().departed_at == \
        "2026-07-01"


def test_reconcile_reports_duplicate_keys_once_with_row_count(session):
    session.add(User(matriculation="2", last_name="Petrov"))
    session.commit()
    records = [
        {"matriculation": "2", "handle_sheet": "petr_a"},
        {"matriculation": "2", "handle_sheet": "petr_b"},
    ]

    report = sheets.reconcile(session, records)

    assert report.duplicates == [sheets.DuplicateKey(value="2", rows=2)]
    assert report.differences == []


def test_reconcile_reports_both_values_for_a_profile_difference(session):
    session.add(User(
        matriculation="1",
        last_name="Ivan",
        handle_observed="ivan_new",
        github_self="alice-dev",
    ))
    session.commit()
    records = [{
        "matriculation": "1",
        "handle_sheet": "ivan_old",
        "github_sheet": "alice",
    }]

    report = sheets.reconcile(session, records)

    assert report.differences == [
        sheets.FieldDifference(
            key="1",
            field="telegram",
            sheet_value="ivan_old",
            profile_value="ivan_new",
        ),
        sheets.FieldDifference(
            key="1",
            field="github",
            sheet_value="alice",
            profile_value="alice-dev",
        ),
    ]


def test_reconcile_ignores_a_field_only_one_side_filled(session):
    session.add(User(matriculation="1", last_name="Ivan", github_self="alice"))
    session.commit()
    records = [{"matriculation": "1", "github_sheet": ""}]

    report = sheets.reconcile(session, records)

    assert report.differences == []
    assert report.duplicates == []
