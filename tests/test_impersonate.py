"""/as and /unas: entering and leaving the mode."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jbcub_bot.core import guards, impersonation
from jbcub_bot.core.contract import Registry, call_handler
from jbcub_bot.features.help.render import render_help
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.impersonate import register
from jbcub_bot.features.impersonate.handlers import (
    _NOT_IMPERSONATING,
    _USAGE,
    cmd_as,
    cmd_unas,
)


def _registry() -> Registry:
    registry = Registry()
    register(registry.api_for("impersonate"))
    return registry


def _msg(telegram_id=777):
    return SimpleNamespace(answer=AsyncMock(),
                           from_user=SimpleNamespace(id=telegram_id))


def _dialog():
    return SimpleNamespace(end=AsyncMock())


async def _dispatch(spec, message, **available):
    """What `core/pipeline.py`'s `_run_command` does for one command: refuse
    out loud, or hand the message to the handler. Reproduced here rather than
    imported so the test reaches the real handler through its real
    declaration -- the guard the contract attached to `/as` and `/unas` --
    instead of calling either function bypassing it."""
    refused = guards.refusal(spec.guard, available.get("principal"))
    if refused is not None:
        await message.answer(refused)
        return
    await call_handler(spec.handler, message, **available)


async def test_as_is_denied_for_a_non_admin(session):
    spec = _registry().commands()["as"]
    msg = _msg()
    await _dispatch(spec, msg, principal=User(last_name="S", role=Role.STUDENT),
                    session=session, dialog=_dialog(), arg="30000001")
    msg.answer.assert_awaited_once_with("Admins only.")
    assert impersonation.ref_for(777) is None


async def test_as_is_denied_for_an_unlinked_caller(session):
    spec = _registry().commands()["as"]
    msg = _msg()
    await _dispatch(spec, msg, principal=None, session=session,
                    dialog=_dialog(), arg="30000001")
    msg.answer.assert_awaited_once_with(
        "You are not linked yet. Contact an admin.")
    assert impersonation.ref_for(777) is None


# --- what the exit's guard has to be, and what it must not be ----------------
# It carries no `role`, because inside the mode `principal` *is* the target and
# any rank would refuse the one command that gets you out of a student's view.
# It is not `public` either, though it used to be: that was the only way to say
# "no role check" before it was noticed the default guard already says exactly
# that. `public` means "written for strangers", and this is not.


@pytest.mark.parametrize("role", [Role.STUDENT, Role.TEACHER, Role.ADMIN])
async def test_unas_is_refused_to_no_rank(role, session):
    impersonation.begin(777, "30000001")
    spec = _registry().commands()["unas"]
    msg = _msg()

    await _dispatch(spec, msg, principal=User(last_name="T", role=role),
                    dialog=_dialog())

    # The principal here is the *target* -- a student under /as on a student.
    assert impersonation.ref_for(777) is None
    assert "own view" in msg.answer.await_args.args[0]


async def test_unas_is_refused_to_a_stranger_like_anything_else(session):
    """Regression: `public=True` let anybody who had never been seen before
    send it and be told "You are not viewing as anyone." -- a reply confirming
    the command exists, to someone the bot has no business answering."""
    spec = _registry().commands()["unas"]
    msg = _msg(telegram_id=999)

    await _dispatch(spec, msg, principal=None, dialog=_dialog())

    msg.answer.assert_awaited_once_with(guards.NOT_LINKED)


async def test_as_without_a_reference_shows_usage(session):
    admin = User(last_name="A", role=Role.ADMIN)
    for arg in ("", "   "):
        msg = _msg()
        await cmd_as(msg, principal=admin, session=session, dialog=_dialog(),
                    arg=arg)
        msg.answer.assert_awaited_once_with(_USAGE)
    assert impersonation.ref_for(777) is None


async def test_as_with_an_unknown_reference_starts_nothing(session):
    msg = _msg()
    await cmd_as(msg, principal=User(last_name="A", role=Role.ADMIN),
                session=session, dialog=_dialog(), arg="nope")
    msg.answer.assert_awaited_once_with("No user found for nope.")
    assert impersonation.ref_for(777) is None


async def test_as_enters_the_mode_and_clears_the_dialog(session):
    session.add(User(last_name="Ivanov", first_name="Ivan",
                     matriculation="30000001", telegram_id=111,
                     role=Role.STUDENT))
    session.commit()
    msg, dialog = _msg(), _dialog()

    await cmd_as(msg, principal=User(last_name="A", role=Role.ADMIN),
                session=session, dialog=dialog, arg="30000001")

    assert impersonation.ref_for(777) == "30000001"
    dialog.end.assert_awaited_once()
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
                session=session, dialog=_dialog(), arg="111")

    assert impersonation.ref_for(777) == "30000001"


async def test_unas_leaves_the_mode(session):
    impersonation.begin(777, "30000001")
    msg, dialog = _msg(), _dialog()

    await cmd_unas(msg, dialog=dialog)

    assert impersonation.ref_for(777) is None
    dialog.end.assert_awaited_once()
    assert "own view" in msg.answer.await_args.args[0]


async def test_unas_outside_the_mode_says_so(session):
    msg = _msg()
    await cmd_unas(msg, dialog=_dialog())
    msg.answer.assert_awaited_once_with(_NOT_IMPERSONATING)


# --- the payoff: what the contract lets /unas have that CommandRegistrar could
# not ---------------------------------------------------------------------

async def test_impersonate_gets_a_help_heading_for_an_admin():
    # The pre-contract renderer required a student-visible line to print a
    # feature's heading at all, so `impersonate` -- every line of it elevated --
    # never got one. Task 4 fixed that in the renderer that replaced it (now
    # `features/help/render.py`); this is the first time it is visible on the
    # real feature.
    out = render_help(_registry().features(),
                      User(last_name="A", role=Role.ADMIN))
    assert "🕵️ Impersonate" in out
    assert "/as <ref> — See the bot as another user, until /unas. (admin)" in out


async def test_unas_is_absent_from_a_students_help_but_does_not_refuse_them():
    # The two halves the old registration could not have at once: `listed=False`
    # keeps /unas out of a student's /help, and `public=True` still lets that
    # same student run it without being turned away.
    registry = _registry()
    out = render_help(registry.features(), User(last_name="S", role=Role.STUDENT))
    assert "/unas" not in out

    spec = registry.commands()["unas"]
    msg = _msg()
    await _dispatch(spec, msg, principal=User(last_name="S", role=Role.STUDENT),
                    dialog=_dialog())
    msg.answer.assert_awaited_once_with(_NOT_IMPERSONATING)
