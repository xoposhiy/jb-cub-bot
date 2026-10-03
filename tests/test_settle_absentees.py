"""One pass settles everyone this cohort's roster no longer names.

An ordinary row is marked with the date and kept; a provisional row is removed
outright, because it holds nothing Google Sheets does not hold and the next
/sync builds it again.
"""

from jbcub_bot.core.models import Grade, Role, User
from jbcub_bot.features.directory import sheets


def _settle(session, records=({"matriculation": "1"},), cohort="2024",
            today="2026-07-28"):
    return sheets.settle_absentees(session, cohort, list(records), today)


def test_the_member_this_roster_no_longer_names_is_marked(session):
    session.add_all([
        User(matriculation="1", last_name="Stays", primary_cohort="2024"),
        User(matriculation="2", first_name="Eve", last_name="Expelled",
             primary_cohort="2024"),
    ])
    session.commit()

    report = _settle(session)

    assert report.marked == [
        sheets.DepartedUser(matriculation="2", full_name="Eve Expelled")
    ]
    assert report.removed == []
    stays, left = (session.query(User).filter_by(matriculation=m).one()
                   for m in ("1", "2"))
    assert stays.departed_at is None
    assert left.departed_at == "2026-07-28"


def test_a_rights_only_admin_is_left_alone(session):
    # Admins and teachers come from the Rights tab: no cohort, keyed on their
    # handle. Every cohort roster is missing them, so a sync that swept up
    # whoever it could not find would hide the program's own staff.
    session.add(User(handle_sheet="boss", last_name="Boss", role=Role.ADMIN))
    session.commit()

    report = _settle(session)

    assert (report.marked, report.removed) == ([], [])
    assert session.query(User).filter_by(handle_sheet="boss").one().departed_at is None


def test_another_cohort_is_never_reached_into(session):
    # 2023's students are absent from 2024's roster by definition.
    session.add_all([
        User(matriculation="9", last_name="Older", primary_cohort="2023"),
        User(matriculation="TMP-9", last_name="Younger", primary_cohort="2023"),
    ])
    session.commit()

    report = _settle(session)

    assert (report.marked, report.removed) == ([], [])
    assert session.query(User).count() == 2


def test_a_member_who_has_no_key_yet_is_spared(session):
    # The roster is keyed on matriculation, so a row without one was never
    # matched against it and its absence there says nothing.
    session.add(User(matriculation=None, last_name="Pending",
                     primary_cohort="2024"))
    session.commit()

    report = _settle(session)

    assert (report.marked, report.removed) == ([], [])
    assert session.query(User).filter_by(last_name="Pending").one().departed_at is None


def test_the_date_of_the_sync_that_first_missed_them_is_kept(session):
    # The date answers "when did they leave the roster?" -- a later sync
    # overwriting it with today would turn the answer into "just now, always".
    session.add(User(matriculation="2", last_name="Left", primary_cohort="2024"))
    session.commit()
    _settle(session, today="2026-07-01")

    report = _settle(session, today="2026-07-28")

    assert report.marked == []  # nothing new to report on a repeat sync
    assert session.query(User).filter_by(matriculation="2").one().departed_at == \
        "2026-07-01"


def test_a_provisional_row_is_removed_rather_than_marked(session):
    session.add(User(matriculation="TMP-A1B2", first_name="Nina",
                     last_name="Nova", primary_cohort="2024"))
    session.commit()

    report = _settle(session)

    assert report.marked == []
    assert report.removed == [
        sheets.DepartedUser(matriculation="TMP-A1B2", full_name="Nina Nova")
    ]
    assert session.query(User).filter_by(matriculation="TMP-A1B2").count() == 0


def test_a_provisional_row_that_already_carries_a_date_is_still_removed(session):
    # Selecting only unmarked rows would leave this one behind for good: no
    # later sync would look at it again and it would linger forever.
    session.add(User(matriculation="TMP-A1B2", last_name="Nova",
                     primary_cohort="2024", departed_at="2026-07-01"))
    session.commit()

    report = _settle(session)

    assert len(report.removed) == 1
    assert session.query(User).filter_by(matriculation="TMP-A1B2").count() == 0


def test_the_two_outcomes_are_reported_as_separate_lists(session):
    session.add_all([
        User(matriculation="2", first_name="Eve", last_name="Expelled",
             primary_cohort="2024"),
        User(matriculation="TMP-A1B2", first_name="Nina", last_name="Nova",
             primary_cohort="2024"),
    ])
    session.commit()

    report = _settle(session)

    assert [item.full_name for item in report.marked] == ["Eve Expelled"]
    assert [item.full_name for item in report.removed] == ["Nina Nova"]


def test_the_grades_of_a_removed_row_go_with_it(session):
    # users.id is an INTEGER PRIMARY KEY, so SQLite hands the number out again;
    # a left-behind grade would then belong to whoever gets it next.
    gone = User(matriculation="TMP-A1B2", last_name="Nova",
                primary_cohort="2024")
    kept = User(matriculation="1", last_name="Stays", primary_cohort="2024")
    session.add_all([gone, kept])
    session.flush()
    session.add_all([
        Grade(user_id=gone.id, cohort="2024", term="Fall 2026", label="Math",
              value="90%", position=2),
        Grade(user_id=kept.id, cohort="2024", term="Fall 2026", label="Math",
              value="80%", position=2),
    ])
    session.commit()

    _settle(session)

    remaining = session.query(Grade).all()
    assert [grade.value for grade in remaining] == ["80%"]


def test_the_prefix_is_matched_whatever_case_the_sheet_used(session):
    # A hand-typed placeholder must leave the person restricted, which means
    # this pass has to treat it as one too.
    session.add(User(matriculation="tmp-typed", last_name="Nova",
                     primary_cohort="2024"))
    session.commit()

    report = _settle(session)

    assert len(report.removed) == 1
    assert session.query(User).count() == 0


def test_a_row_keyed_by_a_typed_placeholder_is_removed_once_the_number_arrives(session):
    # An admin typed "not in CN" where the number goes; marking that row
    # departed would leave it beside the real one for good.
    session.add_all([
        User(matriculation="not in CN", first_name="Milan",
             last_name="Pliasovskikh", primary_cohort="2024"),
        User(matriculation="1", first_name="Milan", last_name="Pliasovskikh",
             primary_cohort="2024"),
    ])
    session.commit()

    report = _settle(session)

    assert report.marked == []
    assert [u.matriculation for u in session.query(User).all()] == ["1"]
