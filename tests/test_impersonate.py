from types import SimpleNamespace
from unittest.mock import AsyncMock

import jbcub_bot.features.impersonate as impersonate
from jbcub_bot.features.impersonate.handlers import cmd_as
from jbcub_bot.core.models import Role, User


def _cmd(args):
    return SimpleNamespace(args=args)


def test_manifest_exposes_as_command():
    assert impersonate.manifest.name == "impersonate"
    assert any(c.name == "as" for c in impersonate.manifest.commands)
    assert impersonate.router is not None


async def test_cmd_as_denied_for_non_admin(session):
    msg = SimpleNamespace(answer=AsyncMock())
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    await cmd_as(msg, principal=User(last_name="S", role=Role.STUDENT),
                 session=session, bot=object(), dispatcher=dispatcher,
                 command=_cmd("30000001 /me"))
    msg.answer.assert_awaited_once_with("Admins only.")
    dispatcher.propagate_event.assert_not_awaited()


async def test_cmd_as_denied_for_none_principal(session):
    msg = SimpleNamespace(answer=AsyncMock())
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    await cmd_as(msg, principal=None, session=session, bot=object(),
                 dispatcher=dispatcher, command=_cmd("30000001 /me"))
    msg.answer.assert_awaited_once_with("You are not linked yet. Contact an admin.")
    dispatcher.propagate_event.assert_not_awaited()


async def test_cmd_as_usage_on_missing_args(session):
    admin = User(last_name="A", role=Role.ADMIN)
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    for args in (None, "", "30000001", "30000001   "):
        msg = SimpleNamespace(answer=AsyncMock())
        await cmd_as(msg, principal=admin, session=session, bot=object(),
                     dispatcher=dispatcher, command=_cmd(args))
        msg.answer.assert_awaited_once_with(
            "Usage: /as <matriculation|telegram_id> <query>")
    dispatcher.propagate_event.assert_not_awaited()


async def test_cmd_as_not_found(session):
    admin = User(last_name="A", role=Role.ADMIN)
    msg = SimpleNamespace(answer=AsyncMock())
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    await cmd_as(msg, principal=admin, session=session, bot=object(),
                 dispatcher=dispatcher, command=_cmd("nope /me"))
    msg.answer.assert_awaited_once_with("No user found for nope.")
    dispatcher.propagate_event.assert_not_awaited()


async def test_cmd_as_success_refeeds_query(session):
    admin = User(last_name="A", role=Role.ADMIN)
    session.add(User(last_name="Ivanov", first_name="Ivan",
                     matriculation="30000001", telegram_id=111,
                     role=Role.STUDENT))
    session.commit()

    new_msg = SimpleNamespace()
    new_msg.as_ = lambda bot: new_msg
    msg = SimpleNamespace(
        answer=AsyncMock(),
        model_copy=lambda update: (setattr(new_msg, "update", update) or new_msg),
    )
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    bot = object()

    await cmd_as(msg, principal=admin, session=session, bot=bot,
                 dispatcher=dispatcher, command=_cmd("30000001 /me"))

    msg.answer.assert_awaited_once_with("\U0001f464 Showing as Ivan Ivanov:")
    assert new_msg.update == {"text": "/me", "entities": None}
    dispatcher.propagate_event.assert_awaited_once()
    call = dispatcher.propagate_event.await_args
    assert call.args[0] == "message"
    assert call.kwargs["impersonate_ref"] == "30000001"
    assert call.kwargs["bot"] is bot
    assert call.kwargs["dispatcher"] is dispatcher
