from jbcub_bot.features.directory import sheets
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


# --- the key a record carries: injected when missing, stripped always -------

def test_an_unkeyed_record_gets_a_provisional_key_and_a_keyed_one_keeps_its(session):
    records = [
        {"matriculation": "", "last_name": "Nova"},
        {"matriculation": "30000001", "last_name": "Real"},
    ]

    sheets.assign_provisional_keys(records, lambda: "TMP-AAA")

    assert [record["matriculation"] for record in records] == [
        "TMP-AAA", "30000001",
    ]


def test_each_unkeyed_record_gets_its_own_key(session):
    # Two first-years must not merge into one row, whatever they are called.
    keys = iter(["TMP-AAA", "TMP-BBB"])
    records = [{"matriculation": ""}, {"matriculation": ""}]

    sheets.assign_provisional_keys(records, lambda: next(keys))

    assert [record["matriculation"] for record in records] == [
        "TMP-AAA", "TMP-BBB",
    ]


def test_upsert_inserts_a_record_the_sheet_left_unkeyed_once_a_key_is_injected(session):
    records = [{"matriculation": "", "first_name": "Nina", "last_name": "Nova",
                "handle_sheet": "nina", "primary_cohort": "2026"}]
    sheets.assign_provisional_keys(records, lambda: "TMP-AAA")

    sheets.upsert_users(session, records)

    u = session.query(User).filter_by(matriculation="TMP-AAA").one()
    assert u.full_name == "Nina Nova"
    assert u.handle_sheet == "nina"


def test_a_key_with_surrounding_whitespace_matches_the_stripped_row(session):
    session.add(User(matriculation="30000001", last_name="Old"))
    session.commit()

    sheets.upsert_users(session, [
        {"matriculation": " 30000001 ", "last_name": "New"},
    ])

    assert [u.last_name for u in session.query(User).all()] == ["New"]


def test_a_provisional_row_is_rebuilt_under_a_new_key_by_the_next_sync(session):
    def sheet_row():
        return {"matriculation": "", "first_name": "Nina", "last_name": "Nova",
                "handle_sheet": "nina", "primary_cohort": "2026"}

    for key in ("TMP-AAA", "TMP-BBB"):
        records = [sheet_row()]
        sheets.assign_provisional_keys(records, lambda: key)
        sheets.upsert_users(session, records)
        sheets.settle_absentees(session, "2026", records, "2026-09-01")
        session.commit()

    assert [u.matriculation for u in session.query(User).all()] == ["TMP-BBB"]


def test_the_rebuilt_row_is_claimed_again_by_the_same_handle(session):
    from jbcub_bot.core import identity

    def sheet_row():
        return {"matriculation": "", "first_name": "Nina", "last_name": "Nova",
                "handle_sheet": "nina", "primary_cohort": "2026"}

    records = [sheet_row()]
    sheets.assign_provisional_keys(records, lambda: "TMP-AAA")
    sheets.upsert_users(session, records)
    session.commit()
    assert identity.resolve(session, 555, "nina").matriculation == "TMP-AAA"

    # The binding dies with the row, and nobody has to put it back: the
    # replacement arrives with the handle set and no telegram_id, so the next
    # message claims it.
    records = [sheet_row()]
    sheets.assign_provisional_keys(records, lambda: "TMP-BBB")
    sheets.upsert_users(session, records)
    sheets.settle_absentees(session, "2026", records, "2026-09-01")
    session.commit()

    rebuilt = session.query(User).one()
    assert (rebuilt.matriculation, rebuilt.telegram_id) == ("TMP-BBB", None)
    assert identity.resolve(session, 555, "nina").id == rebuilt.id
    assert session.query(User).one().telegram_id == 555
