from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jbcub_bot.core.models import Role, User
from jbcub_bot.core.tokens import issue_link_token, verify_link_token
from jbcub_bot.features.directory import handlers

SECRET = "unit-secret"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(
        handlers, "get_settings",
        lambda: SimpleNamespace(link_secret=SECRET, link_ttl_seconds=86400),
    )


def _message(telegram_id: int, username: str | None):
    return SimpleNamespace(
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=telegram_id, username=username),
    )


async def _start(message, session, principal=None, payload=None):
    await handlers.cmd_start(message, principal=principal, session=session,
                             command=SimpleNamespace(args=payload))


async def test_invite_links_an_account_whose_handle_differs_from_the_sheet(session):
    """The whole point of an invite: the handle need not match the roster."""
    ivan = User(first_name="I", last_name="Ivan", matriculation="30000001",
                handle_sheet="ivan_old", role=Role.STUDENT)
    session.add(ivan)
    session.commit()
    token = issue_link_token(session, "30000001", SECRET)

    message = _message(777, "ivan_new")
    await _start(message, session, payload=token)

    message.answer.assert_awaited_once_with("Linked as I Ivan.")
    assert ivan.telegram_id == 777
    assert ivan.handle_observed == "ivan_new"  # observed handle wins
    assert ivan.handle_sheet == "ivan_old"  # /sync's column is left alone


async def test_invite_refuses_an_account_already_linked_elsewhere(session):
    """telegram_id is unique, so binding it twice would blow up on commit.

    Without the guard the person gets no reply at all and the admins get an
    IntegrityError traceback — so answer instead, and leave the invite unused.
    """
    petya = User(first_name="P", last_name="Petya", matriculation="30000002",
                 telegram_id=111, handle_observed="petya", role=Role.STUDENT)
    ivan = User(first_name="I", last_name="Ivan", matriculation="30000001",
                role=Role.STUDENT)
    session.add_all([petya, ivan])
    session.commit()
    token = issue_link_token(session, "30000001", SECRET)

    message = _message(111, "petya")
    await _start(message, session, principal=petya, payload=token)

    text = message.answer.await_args.args[0]
    assert "P Petya" in text  # says who holds the account
    assert petya.telegram_id == 111  # old binding intact
    assert ivan.telegram_id is None  # nothing stolen
    # The invite is still good — an admin can hand it to the right person.
    assert verify_link_token(session, token, SECRET, 86400) is not None


async def test_invite_for_your_own_profile_is_harmless(session):
    ivan = User(first_name="I", last_name="Ivan", matriculation="30000001",
                telegram_id=777, role=Role.STUDENT)
    session.add(ivan)
    session.commit()
    token = issue_link_token(session, "30000001", SECRET)

    message = _message(777, "ivan")
    await _start(message, session, principal=ivan, payload=token)

    message.answer.assert_awaited_once_with("Linked as I Ivan.")
    assert ivan.telegram_id == 777


async def test_invite_works_for_an_account_without_a_username(session):
    ivan = User(first_name="I", last_name="Ivan", matriculation="30000001",
                handle_sheet="ivan_old", role=Role.STUDENT)
    session.add(ivan)
    session.commit()
    token = issue_link_token(session, "30000001", SECRET)

    message = _message(778, None)
    await _start(message, session, payload=token)

    assert ivan.telegram_id == 778
    assert ivan.handle_observed is None


async def test_invalid_payload_is_reported(session):
    message = _message(779, "nobody")
    await _start(message, session, payload="not-a-token")
    message.answer.assert_awaited_once_with("This link is invalid or expired.")
