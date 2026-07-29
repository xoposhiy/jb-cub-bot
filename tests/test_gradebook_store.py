from jbcub_bot.core.models import Grade, User
from jbcub_bot.features.directory import matching
from jbcub_bot.features.directory.grades import CountedName, sync_cohort


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
    assert report.source_people == 1
    assert report.matched_people == 1
    assert report.cells == 2
    assert report.no_roster_match == []
    stored = session.query(Grade).order_by(Grade.position).all()
    assert [(grade.term, grade.label, grade.value) for grade in stored] == [
        ("Fall 2025", "Math", "91%"),
        ("Spring 2026", "Physics", "pass"),
    ]


def test_unknown_gradebook_name_is_only_in_no_roster_match(session):
    session.add(User(
        last_name="Sidorov",
        first_name="Sergey",
        primary_cohort="2099",
    ))
    session.commit()
    report = sync_cohort(
        session,
        "2024",
        _rows(["Active", "Sidorov", "Sergey", "91%", "", "pass"]),
        MAPPING,
        matching.fold,
    )

    assert report.source_people == 1
    assert report.matched_people == 0
    assert report.no_roster_match == ["Sidorov Sergey"]
    assert report.ambiguous_roster_match == []
    assert session.query(Grade).count() == 0


def test_duplicate_gradebook_name_is_grouped_once(session):
    session.add(User(
        last_name="Kuznetsov",
        first_name="Ivan",
        primary_cohort="2024",
    ))
    session.commit()
    report = sync_cohort(
        session,
        "2024",
        _rows(
            ["Active", "Kuznetsov", "Ivan", "91%", "", "pass"],
            ["Active", "Kuznetsov", "Ivan", "50%", "", "fail"],
        ),
        MAPPING,
        matching.fold,
    )

    assert report.duplicate_rows == [
        CountedName(name="Kuznetsov Ivan", count=2)
    ]
    assert report.no_roster_match == []
    assert session.query(Grade).count() == 0


def test_ambiguous_roster_name_is_not_called_missing(session):
    session.add_all([
        User(last_name="Lee", first_name="Alex", primary_cohort="2024"),
        User(last_name="Lee", first_name="Alex", primary_cohort="2024"),
    ])
    session.commit()

    report = sync_cohort(
        session,
        "2024",
        _rows(["Active", "Lee", "Alex", "91%", "", "pass"]),
        MAPPING,
        matching.fold,
    )

    assert report.ambiguous_roster_match == [
        CountedName(name="Lee Alex", count=2)
    ]
    assert report.no_roster_match == []


def test_only_current_roster_students_without_source_rows_are_reported(session):
    session.add_all([
        User(last_name="Current", first_name="Student", primary_cohort="2024"),
        User(
            last_name="Former",
            first_name="Student",
            primary_cohort="2024",
            departed_at="2026-07-01",
        ),
        User(last_name="Other", first_name="Cohort", primary_cohort="2025"),
    ])
    session.commit()

    report = sync_cohort(
        session,
        "2024",
        _rows(["Active", "Known", "Person", "91%", "", "pass"]),
        MAPPING,
        matching.fold,
    )

    assert report.missing_gradebook_rows == ["Current Student"]


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
    assert report.ignored_columns == []
    assert {grade.label for grade in session.query(Grade).all()} == {
        "Other", "Math", "Physics"
    }
