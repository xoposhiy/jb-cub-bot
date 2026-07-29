import pytest
from sqlalchemy.exc import IntegrityError

from jbcub_bot.core.models import Grade, Role, User


def test_create_and_read_user(session):
    u = User(
        role=Role.STUDENT,
        first_name="Gabriel Jose",
        last_name="Garcia Marquez",
        matriculation="30000001",
        handle_sheet="ivanov",
        primary_cohort="2024",
        past_cohorts=["2023"],
        visibility={"gmail": "cohort"},
    )
    session.add(u)
    session.commit()
    got = session.get(User, u.id)
    assert got.first_name == "Gabriel Jose"
    assert got.last_name == "Garcia Marquez"
    assert got.full_name == "Gabriel Jose Garcia Marquez"
    assert got.past_cohorts == ["2023"]
    assert got.visibility == {"gmail": "cohort"}
    assert got.role is Role.STUDENT


def test_create_and_read_grade(session):
    user = User(last_name="Ivanov", first_name="Ivan", matriculation="30000001")
    session.add(user)
    session.commit()
    grade = Grade(
        user_id=user.id,
        cohort="2024",
        term="Fall 2025",
        category="Mandatory",
        label="Math",
        value="91%",
        position=3,
    )
    session.add(grade)
    session.commit()
    got = session.get(Grade, grade.id)
    assert (got.user_id, got.cohort, got.term, got.category) == (
        user.id, "2024", "Fall 2025", "Mandatory"
    )
    assert (got.label, got.value, got.position) == ("Math", "91%", 3)


def test_user_source_link_defaults_to_none(session):
    user = User(last_name="Ivanov", first_name="Ivan")
    session.add(user)
    session.commit()
    assert user.source_link is None
    user.source_link = "https://docs.google.com/spreadsheets/d/ABC"
    session.commit()
    session.refresh(user)
    assert user.source_link == "https://docs.google.com/spreadsheets/d/ABC"


def test_matriculation_unique(session):
    session.add(User(last_name="A", matriculation="1"))
    session.commit()
    session.add(User(last_name="B", matriculation="1"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_telegram_id_unique(session):
    session.add(User(last_name="A", telegram_id=123))
    session.commit()
    session.add(User(last_name="B", telegram_id=123))
    with pytest.raises(IntegrityError):
        session.commit()
