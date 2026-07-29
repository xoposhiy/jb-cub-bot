from jbcub_bot.core.models import Grade, User
from jbcub_bot.features.directory import matching
from jbcub_bot.features.directory.grades import sync_cohort


TERM_ROW = ["", "", "", "Fall 2025", "", "Spring 2026"]
CATEGORY_ROW = ["", "", "", "Mandatory", "Fall 2025", "Methods"]
LABEL_ROW = ["Status", "Last name", "First name", "Math", "Credits EARNED", "Physics"]
MAPPING = {"last_name": "Last name", "first_name": "First name"}


def _rows(*data_rows):
    return [TERM_ROW, CATEGORY_ROW, LABEL_ROW, *data_rows]


def test_matched_departed_row_is_stored(session):
    session.add(User(last_name="Ivanov", first_name="Ivan",
                     primary_cohort="2024", departed_at="2026-07-28"))
    session.commit()
    report = sync_cohort(
        session, "2024", _rows(["Active", "ivanov", "IVAN", "91%", "", "pass"]),
        MAPPING, matching.fold,
    )
    assert (report.matched, report.cells, report.unmatched) == (1, 2, [])
    stored = session.query(Grade).order_by(Grade.position).all()
    assert [(grade.term, grade.label, grade.value) for grade in stored] == [
        ("Fall 2025", "Math", "91%"),
        ("Spring 2026", "Physics", "pass"),
    ]


def test_other_cohort_and_unknown_names_are_unmatched(session):
    session.add(User(last_name="Sidorov", first_name="Sergey", primary_cohort="2099"))
    session.commit()
    report = sync_cohort(
        session, "2024", _rows(["Active", "Sidorov", "Sergey", "91%", "", "pass"]),
        MAPPING, matching.fold,
    )
    assert report.unmatched == ["Sidorov Sergey"]
    assert session.query(Grade).count() == 0


def test_duplicate_gradebook_names_are_reported_and_skipped(session):
    session.add(User(last_name="Kuznetsov", first_name="Ivan", primary_cohort="2024"))
    session.commit()
    report = sync_cohort(session, "2024", _rows(
        ["Active", "Kuznetsov", "Ivan", "91%", "", "pass"],
        ["Active", "Kuznetsov", "Ivan", "50%", "", "fail"],
    ), MAPPING, matching.fold)
    assert report.duplicates == ["Kuznetsov Ivan", "Kuznetsov Ivan"]
    assert session.query(Grade).count() == 0


def test_replace_is_bounded_to_cohort(session):
    user = User(last_name="Ivanov", first_name="Ivan", primary_cohort="2024")
    session.add(user)
    session.commit()
    session.add_all([
        Grade(user_id=user.id, cohort="2024", term="Old", category="",
              label="Stale", value="x", position=99),
        Grade(user_id=user.id, cohort="2023", term="Older", category="",
              label="Other", value="y", position=1),
    ])
    session.commit()
    report = sync_cohort(
        session, "2024", _rows(["Active", "Ivanov", "Ivan", "91%", "", "pass"]),
        MAPPING, matching.fold,
    )
    assert report.ignored_columns == 3
    assert {grade.label for grade in session.query(Grade).all()} == {
        "Other", "Math", "Physics"
    }
