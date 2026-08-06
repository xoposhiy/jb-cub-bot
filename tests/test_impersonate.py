"""/as and /unas: entering and leaving the mode."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import jbcub_bot.features.impersonate as impersonate
from jbcub_bot.core import impersonation
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.impersonate.handlers import cmd_as, cmd_unas


def _cmd(args):
    return SimpleNamespace(args=args)


def _msg(telegram_id=777):
    return SimpleNamespace(answer=AsyncMock(),
                           from_user=SimpleNamespace(id=telegram_id))


def _state():
    return SimpleNamespace(clear=AsyncMock())


def test_manifest_exposes_as_but_not_unas():
    # /unas is deliberately off the registrar: see the docstring on cmd_unas.
    assert impersonate.manifest.name == "impersonate"
    names = {c.name for c in impersonate.manifest.commands}
    assert names == {"as"}


async def test_as_is_denied_for_a_non_admin(session):
    msg = _msg()
    await cmd_as(msg, principal=User(last_name="S", role=Role.STUDENT),
                 session=session, state=_state(), command=_cmd("30000001"))
    msg.answer.assert_awaited_once_with("Admins only.")
    assert impersonation.ref_for(777) is None


async def test_as_is_denied_for_an_unlinked_caller(session):
    msg = _msg()
    await cmd_as(msg, principal=None, session=session, state=_state(),
                 command=_cmd("30000001"))
    msg.answer.assert_awaited_once_with(
        "You are not linked yet. Contact an admin.")
    assert impersonation.ref_for(777) is None


async def test_as_without_a_reference_shows_usage(session):
    admin = User(last_name="A", role=Role.ADMIN)
    for args in (None, "", "   "):
        msg = _msg()
        await cmd_as(msg, principal=admin, session=session, state=_state(),
                     command=_cmd(args))
        msg.answer.assert_awaited_once_with(
            "Usage: /as <matriculation|telegram_id>")
    assert impersonation.ref_for(777) is None


async def test_as_with_an_unknown_reference_starts_nothing(session):
    msg = _msg()
    await cmd_as(msg, principal=User(last_name="A", role=Role.ADMIN),
                 session=session, state=_state(), command=_cmd("nope"))
    msg.answer.assert_awaited_once_with("No user found for nope.")
    assert impersonation.ref_for(777) is None


async def test_as_enters_the_mode_and_clears_the_state(session):
    session.add(User(last_name="Ivanov", first_name="Ivan",
                     matriculation="30000001", telegram_id=111,
                     role=Role.STUDENT))
    session.commit()
    msg, state = _msg(), _state()

    await cmd_as(msg, principal=User(last_name="A", role=Role.ADMIN),
                 session=session, state=state, command=_cmd("30000001"))

    assert impersonation.ref_for(777) == "30000001"
    state.clear.assert_awaited_once()
    said = msg.answer.await_args.args[0]
    assert "Ivan Ivanov" in said
    assert "/unas" in said


async def test_as_stores_the_canonical_ref_not_what_was_typed(session):
    # Typed as a telegram id, stored as the matriculation, so the ref outlives
    # a rebinding of the target's telegram account.
    session.add(User(last_name="Ivanov", first_name="Ivan",
                     matriculation="30000001", telegram_id=111,
                     role=Role.STUDENT))
    session.commit()

    await cmd_as(_msg(), principal=User(last_name="A", role=Role.ADMIN),
                 session=session, state=_state(), command=_cmd("111"))

    assert impersonation.ref_for(777) == "30000001"


async def test_unas_leaves_the_mode(session):
    impersonation.begin(777, "30000001")
    msg, state = _msg(), _state()

    await cmd_unas(msg, state=state)

    assert impersonation.ref_for(777) is None
    state.clear.assert_awaited_once()
    assert "own view" in msg.answer.await_args.args[0]


async def test_unas_outside_the_mode_says_so(session):
    msg = _msg()
    await cmd_unas(msg, state=_state())
    msg.answer.assert_awaited_once_with("You are not viewing as anyone.")
