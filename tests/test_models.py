import pytest
from sqlalchemy.exc import IntegrityError

from jbcub_bot.core.models import Role, User


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
