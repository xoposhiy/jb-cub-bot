"""Which handler a tap belongs to, and what the rest of `callback_data` means.

`match` is pure, so most of this needs no bot at all. The entry point gets a
real `CallbackQuery` bound to a bot, because "a callback nothing matched is
still answered" is only worth proving against the object Telegram delivers --
against a stub it would prove nothing about the spinner in the client.
"""
from datetime import datetime, timezone

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, Chat, Message
from aiogram.types import User as TgUser

from jbcub_bot.core.buttons import handle_callback, match
from jbcub_bot.core.contract import ButtonSpec, Guard, Registry
from jbcub_bot.core.dialogs import Dialog
from jbcub_bot.core.guards import ADMIN_REFUSAL, NOT_LINKED
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.oplog import OpsLog


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


async def _noop(cb):
    return None


def _spec(key: str, guard: Guard = Guard(), handler=_noop) -> ButtonSpec:
    return ButtonSpec(feature="directory", key=key, guard=guard,
                      handler=handler)


def _api(registry, feature="directory"):
    bot = registry.api_for(feature)
    bot.describe("📒", "Directory", "Find classmates.")
    return bot


def _callback(fake_bot, data: str) -> CallbackQuery:
    chat = Chat(id=777, type="private")
    shown = Message(message_id=7, date=datetime.now(timezone.utc), chat=chat,
                    from_user=TgUser(id=1, is_bot=True, first_name="bot"),
                    text="whatever was on screen").as_(fake_bot)
    return CallbackQuery(id="cb-1",
                         from_user=TgUser(id=777, is_bot=False,
                                          first_name="tg"),
                         chat_instance="chat-instance", data=data,
                         message=shown).as_(fake_bot)


def _dialog(user_id: int = 777) -> Dialog:
    return Dialog(FSMContext(MemoryStorage(),
                             StorageKey(bot_id=1, chat_id=user_id,
                                        user_id=user_id)))


STUDENT = User(last_name="Ivanov", role=Role.STUDENT)


async def _handle(registry, cb, principal=STUDENT, **over):
    kwargs = dict(principal=principal, session="SESSION", bot=cb.bot,
                  impersonator=None, dialog=_dialog(), oplog=OpsLog(None))
    kwargs.update(over)
    return await handle_callback(registry, cb, **kwargs)


# --- match ----------------------------------------------------------------------

def test_an_exact_key_leaves_no_payload():
    admin = _spec("dir:admin")
    assert match([admin], "dir:admin") == (admin, "")


def test_what_follows_the_key_is_the_arg():
    link = _spec("dir:link")
    assert match([link], "dir:link:30000001") == (link, "30000001")


def test_the_longest_registered_key_wins():
    admin = _spec("dir:admin")
    page = _spec("dir:admin:page")
    assert match([admin, page], "dir:admin:page:2") == (page, "2")
    # ... and the shorter one still gets what is only its own.
    assert match([admin, page], "dir:admin:30000001") == (admin, "30000001")


def test_registration_order_does_not_decide_it():
    admin = _spec("dir:admin")
    page = _spec("dir:admin:page")
    assert match([page, admin], "dir:admin:page:2") == (page, "2")


def test_a_key_written_with_its_separator_works_too():
    # `dir:reset:` and `dir:reset_do:` are the shapes features already use.
    reset = _spec("dir:reset:")
    reset_do = _spec("dir:reset_do:")
    assert match([reset, reset_do], "dir:reset_do:30000001") == \
        (reset_do, "30000001")


def test_data_nobody_registered_matches_nothing():
    assert match([_spec("dir:admin")], "kb:page:2") is None


def test_a_key_the_data_only_starts_within_does_not_match():
    assert match([_spec("dir:admin:page")], "dir:admin") is None


# --- the entry point --------------------------------------------------------------

async def test_the_matching_handler_gets_the_tap_and_its_payload():
    seen = {}

    async def cb_admin(cb, arg):
        seen.update(data=cb.data, arg=arg)

    registry = Registry()
    _api(registry).button("dir:admin")(cb_admin)
    fake_bot = FakeBot()
    took = await _handle(registry, _callback(fake_bot, "dir:admin:30000001"))
    assert seen == {"data": "dir:admin:30000001", "arg": "30000001"}
    assert took.key == "dir:admin"


async def test_a_callback_nothing_matched_is_still_answered():
    # An unanswered callback leaves the button spinning in the client.
    registry = Registry()
    _api(registry).button("dir:admin")(_noop)
    fake_bot = FakeBot()
    took = await _handle(registry, _callback(fake_bot, "gone:from:an:old:deploy"))
    assert took is None
    assert [type(m).__name__ for m in fake_bot.sent] == ["AnswerCallbackQuery"]


async def test_a_refused_tap_alerts_and_never_reaches_the_handler():
    calls = []

    async def cb_sync(cb):
        calls.append(cb)

    registry = Registry()
    _api(registry).button("dir:sync", role=Role.ADMIN)(cb_sync)
    fake_bot = FakeBot()
    took = await _handle(registry, _callback(fake_bot, "dir:sync"),
                         principal=User(last_name="S", role=Role.STUDENT))
    assert calls == []
    assert took is None
    [answer] = fake_bot.sent
    assert answer.text == ADMIN_REFUSAL
    assert answer.show_alert is True


async def test_an_unlinked_caller_is_refused_the_same_way():
    registry = Registry()
    _api(registry).button("dir:admin")(_noop)
    fake_bot = FakeBot()
    await _handle(registry, _callback(fake_bot, "dir:admin"), principal=None)
    [answer] = fake_bot.sent
    assert answer.text == NOT_LINKED
    assert answer.show_alert is True


async def test_a_public_button_runs_for_an_unlinked_caller():
    calls = []

    async def cb_help(cb):
        calls.append(cb)

    registry = Registry()
    _api(registry).button("help:open", public=True)(cb_help)
    fake_bot = FakeBot()
    await _handle(registry, _callback(fake_bot, "help:open"), principal=None)
    assert len(calls) == 1


async def test_every_injectable_name_reaches_a_button_handler():
    """The core supplies all seven, so `declared ⇒ injected` has no exception.

    `call_handler` passes a declared name only when the core offered it, so a
    name missing here would silently keep its default -- or, without one, raise
    a TypeError from inside dispatch. This is the test that keeps that check
    belt-and-braces rather than a hole.
    """
    seen = {}

    async def cb_admin(cb, principal, session, bot, impersonator, dialog, arg,
                       oplog):
        seen.update(principal=principal, session=session, bot=bot,
                    impersonator=impersonator, dialog=dialog, arg=arg,
                    oplog=oplog)

    registry = Registry()
    _api(registry).button("dir:admin")(cb_admin)
    fake_bot = FakeBot()
    principal = User(last_name="A", role=Role.ADMIN)
    dialog, oplog = _dialog(), OpsLog(None)
    await _handle(registry, _callback(fake_bot, "dir:admin:30000001"),
                  principal=principal, dialog=dialog, oplog=oplog)
    assert seen == {"principal": principal, "session": "SESSION",
                    "bot": fake_bot, "impersonator": None, "dialog": dialog,
                    "arg": "30000001", "oplog": oplog}


async def test_a_handler_that_wants_nothing_injected_still_runs():
    calls = []

    async def cb_admin(cb):
        calls.append(cb.data)

    registry = Registry()
    _api(registry).button("dir:admin")(cb_admin)
    fake_bot = FakeBot()
    await _handle(registry, _callback(fake_bot, "dir:admin"))
    assert calls == ["dir:admin"]


async def test_a_crashing_handler_is_not_swallowed():
    async def cb_admin(cb):
        raise RuntimeError("boom")

    registry = Registry()
    _api(registry).button("dir:admin")(cb_admin)
    fake_bot = FakeBot()
    # No try around the call: it has to reach aiogram's dp.errors handler.
    with pytest.raises(RuntimeError, match="boom"):
        await _handle(registry, _callback(fake_bot, "dir:admin"))
