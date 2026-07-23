import pytest
from sqlalchemy.exc import IntegrityError

from sdt_bot.core.models import Role, User


def test_create_and_read_user(session):
    u = User(
        role=Role.STUDENT,
        name="Ivan Ivanov",
        matriculation="30000001",
        handle_sheet="ivanov",
        primary_cohort="2024",
        past_cohorts=["2023"],
        visibility={"gmail": "cohort"},
    )
    session.add(u)
    session.commit()
    got = session.get(User, u.id)
    assert got.name == "Ivan Ivanov"
    assert got.past_cohorts == ["2023"]
    assert got.visibility == {"gmail": "cohort"}
    assert got.role is Role.STUDENT


def test_matriculation_unique(session):
    session.add(User(name="A", matriculation="1"))
    session.commit()
    session.add(User(name="B", matriculation="1"))
    with pytest.raises(IntegrityError):
        session.commit()
