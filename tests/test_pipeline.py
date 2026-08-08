"""The order every incoming message goes through, and who answers when nothing
took it.

Real `Message` objects bound to a fake bot, and the real FSM behind `Dialog`:
the four steps are about what actually reaches a handler and what the sender
reads afterwards, and a stub of either would only prove the stub works.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, PhotoSize
from aiogram.types import User as TgUser

from jbcub_bot.core.contract import ANY, PHOTO, TEXT, Registry
from jbcub_bot.core.dialogs import Dialog
from jbcub_bot.core.guards import ADMIN_REFUSAL, NOT_LINKED
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.oplog import OpsLog
from jbcub_bot.core.pipeline import (
    AGENT,
    CANCELLED,
    LOOKUP,
    NOTHING_MATCHED,
    NOTHING_TO_CANCEL,
    dispatch,
    handle_message,
    take_message,
)

LOG_CHAT = "-1009999"
STUDENT = User(last_name="Ivanov", role=Role.STUDENT)
ADMIN = User(last_name="Egorov", role=Role.ADMIN)


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []
        self.logged: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None

    async def send_message(self, chat_id, text, **kwargs):
        self.logged.append(SimpleNamespace(chat_id=chat_id, text=text))
        return None


async def _noop(message):
    return None


async def _declines(message):
    return False


def _api(registry, feature="directory"):
    bot = registry.api_for(feature)
    bot.describe("📒", "Directory", "Find classmates.")
    return bot


def _message(fake_bot, text=None, chat_id=777, **kwargs) -> Message:
    return Message(message_id=1, date=datetime.now(timezone.utc),
                   chat=Chat(id=chat_id, type="private"),
                   from_user=TgUser(id=chat_id, is_bot=False, first_name="tg"),
                   text=text, **kwargs).as_(fake_bot)


def _photo(fake_bot, chat_id=777, caption=None) -> Message:
    return _message(fake_bot, chat_id=chat_id, caption=caption, photo=[
        PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)])


def _dialog(user_id: int = 777) -> Dialog:
    return Dialog(FSMContext(MemoryStorage(),
                             StorageKey(bot_id=1, chat_id=user_id,
                                        user_id=user_id)))


def _replies(fake_bot) -> str:
    return "\n".join(getattr(m, "text", "") or "" for m in fake_bot.sent)


async def _handle(registry, message, principal=STUDENT, **over):
    kwargs = dict(principal=principal, session="SESSION", bot=message.bot,
                  impersonator=None, dialog=_dialog(),
                  oplog=OpsLog(message.bot, LOG_CHAT))
    kwargs.update(over)
    return await handle_message(registry, message, **kwargs)


# --- the chain, in `at` order -----------------------------------------------------

async def test_the_walk_follows_at_and_not_registration_order():
    calls = []

    def record(name):
        async def handler(message):
            calls.append(name)
            return False
        return handler

    registry = Registry()
    _api(registry, "kb").message(at=AGENT, when=ANY)(record("agent"))
    _api(registry, "directory").message(at=LOOKUP, when=ANY)(record("lookup"))
    await dispatch(registry, _message(FakeBot(), "Ivan"), principal=STUDENT)
    assert calls == ["lookup", "agent"]


async def test_a_declining_handler_passes_the_turn_on():
    calls = []

    async def declines(message):
        calls.append("first")
        return False

    async def accepts(message):
        calls.append("second")
        return True

    registry = Registry()
    bot = _api(registry)
    bot.message(at=LOOKUP)(declines)
    bot.message(at=AGENT)(accepts)
    took = await dispatch(registry, _message(FakeBot(), "Ivan"),
                          principal=STUDENT)
    assert calls == ["first", "second"]
    assert took.at == AGENT


async def test_a_handler_returning_none_still_takes_the_message():
    # A handler that forgets to return must not go silently unhandled.
    calls = []

    async def silent(message):
        calls.append("first")

    async def never(message):
        calls.append("second")

    registry = Registry()
    bot = _api(registry)
    bot.message(at=LOOKUP)(silent)
    bot.message(at=AGENT)(never)
    took = await dispatch(registry, _message(FakeBot(), "Ivan"),
                          principal=STUDENT)
    assert calls == ["first"]
    assert took.at == LOOKUP


async def test_nothing_taking_it_is_no_taker():
    registry = Registry()
    _api(registry).message(at=LOOKUP)(_declines)
    assert await dispatch(registry, _message(FakeBot(), "Ivan"),
                          principal=STUDENT) is None


async def test_an_exception_aborts_the_walk():
    """The crashed handler may already have answered; a second answer is worse.

    No try around the walk, so the exception reaches aiogram's dp.errors.
    """
    calls = []

    async def crashes(message):
        raise RuntimeError("boom")

    async def never(message):
        calls.append("second")

    registry = Registry()
    bot = _api(registry)
    bot.message(at=LOOKUP)(crashes)
    bot.message(at=AGENT)(never)
    with pytest.raises(RuntimeError, match="boom"):
        await dispatch(registry, _message(FakeBot(), "Ivan"), principal=STUDENT)
    assert calls == []


async def test_a_predicate_that_fails_means_the_handler_is_never_offered():
    calls = []

    async def on_photo(message):
        calls.append(message)

    registry = Registry()
    _api(registry).message(at=LOOKUP, when=PHOTO)(on_photo)
    took = await dispatch(registry, _message(FakeBot(), "Ivan"),
                          principal=STUDENT)
    assert (calls, took) == ([], None)


async def test_a_guard_that_refuses_skips_the_handler_and_walks_on():
    """In the chain a guard filters rather than refuses out loud.

    Nothing was addressed to this handler -- the sender typed a name, not a
    command -- so answering "Admins only." would be answering a question
    nobody asked. The next handler gets its turn instead.
    """
    calls = []

    async def admin_only(message):
        calls.append("admin")

    async def anyone(message):
        calls.append("anyone")

    registry = Registry()
    _api(registry, "kb").message(at=LOOKUP, role=Role.ADMIN)(admin_only)
    _api(registry, "directory").message(at=AGENT)(anyone)
    fake_bot = FakeBot()
    took = await dispatch(registry, _message(fake_bot, "Ivan"),
                          principal=STUDENT)
    assert calls == ["anyone"]
    assert took.at == AGENT
    assert fake_bot.sent == [], "the chain answered a refusal nobody asked for"


async def test_an_unlinked_sender_reaches_a_public_handler_only():
    calls = []

    async def linked_only(message):
        calls.append("linked")

    async def public(message):
        calls.append("public")

    registry = Registry()
    _api(registry, "kb").message(at=LOOKUP)(linked_only)
    _api(registry, "directory").message(at=AGENT, public=True)(public)
    await dispatch(registry, _message(FakeBot(), "Ivan"), principal=None)
    assert calls == ["public"]


async def test_every_injectable_name_reaches_a_chain_handler():
    """The core supplies all seven, so `declared ⇒ injected` has no exception.

    `call_handler` injects a declared name only when the core offered it, so a
    name missing here would silently keep its default -- or, without one, raise
    a TypeError from inside dispatch.
    """
    seen = {}

    async def name_search(message, principal, session, bot, impersonator,
                          dialog, arg, oplog):
        seen.update(principal=principal, session=session, bot=bot,
                    impersonator=impersonator, dialog=dialog, arg=arg,
                    oplog=oplog)

    registry = Registry()
    _api(registry).message(at=LOOKUP)(name_search)
    fake_bot = FakeBot()
    dialog, oplog = _dialog(), OpsLog(None)
    await _handle(registry, _message(fake_bot, "Ivan"), principal=ADMIN,
                  dialog=dialog, oplog=oplog)
    assert seen == {"principal": ADMIN, "session": "SESSION", "bot": fake_bot,
                    "impersonator": None, "dialog": dialog, "arg": "",
                    "oplog": oplog}


# --- 1. a command ------------------------------------------------------------------

async def test_a_command_reaches_its_handler_with_the_tail_as_arg():
    seen = {}

    async def cmd_as(message, arg):
        seen["arg"] = arg

    registry = Registry()
    _api(registry).command("as", "See the bot as another user.")(cmd_as)
    await _handle(registry, _message(FakeBot(), "/as 30000001"))
    assert seen == {"arg": "30000001"}


async def test_a_command_with_nothing_after_it_gets_an_empty_arg():
    seen = {}

    async def cmd_me(message, arg):
        seen["arg"] = arg

    registry = Registry()
    _api(registry).command("me", "Show your profile.")(cmd_me)
    await _handle(registry, _message(FakeBot(), "/me"))
    assert seen == {"arg": ""}


async def test_a_pasted_command_with_a_newline_after_it_is_still_that_command():
    calls = []

    async def cmd_me(message, arg):
        calls.append(arg)

    registry = Registry()
    _api(registry).command("me", "Show your profile.")(cmd_me)
    await _handle(registry, _message(FakeBot(), "/me\n"))
    assert calls == [""]


async def test_a_command_in_a_caption_is_still_a_command():
    """A photo captioned "/sync 2024" is as deliberate an address as typing it.

    aiogram's own Command filter, which the core replaces here, matches text
    *or* caption, and `core/middleware.py` already turns on that distinction.
    """
    seen = {}

    async def cmd_sync(message, arg):
        seen["arg"] = arg

    registry = Registry()
    _api(registry).command("sync", "Refresh the roster.")(cmd_sync)
    fake_bot = FakeBot()
    await _handle(registry, _photo(fake_bot, caption="/sync 2024"))
    assert seen == {"arg": "2024"}


async def test_an_unknown_command_in_a_caption_is_answered_as_a_command():
    registry = Registry()
    _api(registry)
    fake_bot = FakeBot()
    await _handle(registry, _photo(fake_bot, caption="/nosuchthing"))
    replies = _replies(fake_bot)
    assert "/nosuchthing" in replies
    assert "I only read text." not in replies
    assert fake_bot.logged == []


async def test_a_caption_that_is_not_a_command_is_still_not_text():
    registry = Registry()
    _api(registry).message(at=LOOKUP)(_noop)
    fake_bot = FakeBot()
    await _handle(registry, _photo(fake_bot, caption="Ivan"))
    assert "I only read text." in _replies(fake_bot)


async def test_a_command_addressed_to_the_bot_by_name_is_still_that_command():
    calls = []

    async def cmd_me(message):
        calls.append(message)

    registry = Registry()
    _api(registry).command("me", "Show your profile.")(cmd_me)
    await _handle(registry, _message(FakeBot(), "/me@jbcub_bot"))
    assert len(calls) == 1


async def test_a_command_never_reaches_the_chain():
    # This is what removes `~F.text.startswith("/")` from every feature.
    calls = []

    async def cmd_me(message):
        calls.append("command")

    async def chain(message):
        calls.append("chain")

    registry = Registry()
    bot = _api(registry)
    bot.command("me", "Show your profile.")(cmd_me)
    bot.message(at=LOOKUP, when=ANY)(chain)
    await _handle(registry, _message(FakeBot(), "/me"))
    assert calls == ["command"]


async def test_a_command_beats_an_open_dialog():
    calls = []

    async def cmd_me(message):
        calls.append("command")

    async def on_text(message):
        calls.append("dialog")

    registry = Registry()
    bot = _api(registry)
    bot.command("me", "Show your profile.")(cmd_me)
    bot.dialog("edit", on_text=on_text)
    dialog = _dialog()
    await dialog.start("directory:edit", field="github")
    await _handle(registry, _message(FakeBot(), "/me"), dialog=dialog)
    assert calls == ["command"]
    assert await dialog.owner() == "directory:edit", \
        "the command was not asked to close the dialog"


async def test_a_command_whose_guard_refuses_answers_and_never_runs():
    calls = []

    async def cmd_sync(message):
        calls.append(message)

    registry = Registry()
    _api(registry).command("sync", "Refresh the roster.",
                           role=Role.ADMIN)(cmd_sync)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "/sync"), principal=STUDENT)
    assert calls == []
    assert _replies(fake_bot) == ADMIN_REFUSAL


async def test_an_unlinked_sender_is_told_so_by_a_command():
    registry = Registry()
    _api(registry).command("me", "Show your profile.")(_noop)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "/me"), principal=None)
    assert _replies(fake_bot) == NOT_LINKED


async def test_every_injectable_name_reaches_a_command_handler():
    seen = {}

    async def cmd_as(message, principal, session, bot, impersonator, dialog,
                     arg, oplog):
        seen.update(principal=principal, session=session, bot=bot,
                    impersonator=impersonator, dialog=dialog, arg=arg,
                    oplog=oplog)

    registry = Registry()
    _api(registry).command("as", "See the bot as another user.")(cmd_as)
    fake_bot = FakeBot()
    dialog, oplog = _dialog(), OpsLog(None)
    await _handle(registry, _message(fake_bot, "/as 30000001"), principal=ADMIN,
                  dialog=dialog, oplog=oplog)
    assert seen == {"principal": ADMIN, "session": "SESSION", "bot": fake_bot,
                    "impersonator": None, "dialog": dialog, "arg": "30000001",
                    "oplog": oplog}


# --- /cancel, the core's own -------------------------------------------------------

async def test_cancel_ends_the_dialog_and_calls_its_on_cancel():
    calls = []

    async def on_cancel(message):
        calls.append("on_cancel")

    registry = Registry()
    _api(registry).dialog("edit", on_text=_noop, on_cancel=on_cancel)
    dialog = _dialog()
    await dialog.start("directory:edit", field="github")
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "/cancel"), dialog=dialog)
    assert calls == ["on_cancel"]
    assert await dialog.owner() is None
    assert _replies(fake_bot) == "", "the feature owns what cancelling says"


async def test_cancel_ends_a_dialog_that_declared_no_hook_and_says_so():
    registry = Registry()
    _api(registry).dialog("edit", on_text=_noop)
    dialog = _dialog()
    await dialog.start("directory:edit")
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "/cancel"), dialog=dialog)
    assert await dialog.owner() is None
    assert _replies(fake_bot) == CANCELLED


async def test_cancel_with_no_dialog_open_says_so_instead_of_failing():
    registry = Registry()
    _api(registry).dialog("edit", on_text=_noop)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "/cancel"), dialog=_dialog())
    assert _replies(fake_bot) == NOTHING_TO_CANCEL


async def test_cancel_ends_a_dialog_no_feature_claims():
    # A state left over from an older deploy: there is nothing to redraw, but
    # the sender is still stuck in it until something clears it.
    registry = Registry()
    _api(registry)
    dialog = _dialog()
    await dialog.start("gone:from:an:old:deploy")
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "/cancel"), dialog=dialog)
    assert await dialog.owner() is None
    assert _replies(fake_bot) == CANCELLED


async def test_cancel_is_not_the_unknown_command():
    registry = Registry()
    _api(registry)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "/cancel"), dialog=_dialog())
    assert "I don't know" not in _replies(fake_bot)


# --- 2. the sender's own dialog ------------------------------------------------------

async def test_text_in_the_senders_own_dialog_reaches_its_on_text():
    calls = []

    async def on_text(message):
        calls.append("dialog")

    async def chain(message):
        calls.append("chain")

    registry = Registry()
    bot = _api(registry)
    bot.dialog("edit", on_text=on_text)
    bot.message(at=LOOKUP, when=ANY)(chain)
    dialog = _dialog()
    await dialog.start("directory:edit", field="github")
    await _handle(registry, _message(FakeBot(), "xoposhiy"), dialog=dialog)
    assert calls == ["dialog"]


async def test_text_while_a_dialog_nobody_registered_is_open_still_runs_the_chain():
    """A dialog must not silence other features.

    An open state the registry does not route -- a legacy feature's own FSM,
    or one left behind by an older deploy -- is exactly what `StateFilter(None)`
    used to turn into a bot that answers nothing.
    """
    calls = []

    async def chain(message):
        calls.append("chain")

    registry = Registry()
    _api(registry).message(at=LOOKUP, when=ANY)(chain)
    dialog = _dialog()
    await dialog.start("kb:ask", question="when is the exam?")
    await _handle(registry, _message(FakeBot(), "Ivan"), dialog=dialog)
    assert calls == ["chain"]


async def test_a_dialog_whose_guard_refuses_answers_rather_than_routing():
    calls = []

    async def on_text(message):
        calls.append(message)

    registry = Registry()
    _api(registry).dialog("edit", on_text=on_text, role=Role.ADMIN)
    dialog = _dialog()
    await dialog.start("directory:edit")
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "xoposhiy"), dialog=dialog,
                  principal=STUDENT)
    assert calls == []
    assert _replies(fake_bot) == ADMIN_REFUSAL


async def test_a_photo_is_not_offered_to_on_text():
    # `on_text` is what it says: a dialog waiting for a value must not be
    # handed a photo to save.
    calls = []

    async def on_text(message):
        calls.append("dialog")

    async def on_photo(message):
        calls.append("chain")

    registry = Registry()
    bot = _api(registry)
    bot.dialog("edit", on_text=on_text)
    bot.message(at=LOOKUP, when=PHOTO)(on_photo)
    dialog = _dialog()
    await dialog.start("directory:edit")
    await _handle(registry, _photo(FakeBot()), dialog=dialog)
    assert calls == ["chain"]


async def test_every_injectable_name_reaches_a_dialog_handler():
    seen = {}

    async def on_text(message, principal, session, bot, impersonator, dialog,
                      arg, oplog):
        seen.update(principal=principal, session=session, bot=bot,
                    impersonator=impersonator, dialog=dialog, arg=arg,
                    oplog=oplog)

    registry = Registry()
    _api(registry).dialog("edit", on_text=on_text)
    fake_bot = FakeBot()
    dialog, oplog = _dialog(), OpsLog(None)
    await dialog.start("directory:edit")
    await _handle(registry, _message(fake_bot, "xoposhiy"), principal=ADMIN,
                  dialog=dialog, oplog=oplog)
    assert seen == {"principal": ADMIN, "session": "SESSION", "bot": fake_bot,
                    "impersonator": None, "dialog": dialog, "arg": "",
                    "oplog": oplog}


async def test_every_injectable_name_reaches_an_on_cancel_hook():
    seen = {}

    async def on_cancel(message, principal, session, bot, impersonator, dialog,
                        arg, oplog):
        seen.update(principal=principal, session=session, bot=bot,
                    impersonator=impersonator, dialog=dialog, arg=arg,
                    oplog=oplog)

    registry = Registry()
    _api(registry).dialog("edit", on_text=_noop, on_cancel=on_cancel)
    fake_bot = FakeBot()
    dialog, oplog = _dialog(), OpsLog(None)
    await dialog.start("directory:edit")
    await _handle(registry, _message(fake_bot, "/cancel"), principal=ADMIN,
                  dialog=dialog, oplog=oplog)
    assert seen == {"principal": ADMIN, "session": "SESSION", "bot": fake_bot,
                    "impersonator": None, "dialog": dialog, "arg": "",
                    "oplog": oplog}


# --- 4. the last word ----------------------------------------------------------------

async def test_text_nothing_took_gets_the_answer_and_an_ops_log_miss():
    registry = Registry()
    _api(registry).message(at=LOOKUP)(_declines)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "Иванов Пётр"))
    assert _replies(fake_bot) == NOTHING_MATCHED
    [entry] = fake_bot.logged
    assert entry.chat_id == LOG_CHAT
    assert "Иванов Пётр" in entry.text
    assert NOTHING_MATCHED in entry.text
    assert "777" in entry.text  # who asked


async def test_an_unknown_command_says_which_one_and_is_not_logged():
    # The bot answered correctly, so this is not a gap worth logging.
    registry = Registry()
    _api(registry).message(at=LOOKUP, when=ANY)(_noop)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "/nosuchthing"))
    replies = _replies(fake_bot)
    assert "/nosuchthing" in replies
    assert "/help" in replies
    assert fake_bot.logged == []


async def test_a_message_that_is_not_text_says_so_and_is_logged_by_its_type():
    registry = Registry()
    _api(registry).message(at=LOOKUP)(_noop)
    fake_bot = FakeBot()
    await _handle(registry, _photo(fake_bot))
    assert "I only read text." in _replies(fake_bot)
    # Lowercase: `content_type` is a (str, Enum), and interpolating it directly
    # would read "ContentType.PHOTO".
    assert "«photo»" in fake_bot.logged[0].text


async def test_a_message_something_took_is_not_answered_twice_or_logged():
    registry = Registry()
    _api(registry).message(at=LOOKUP)(_noop)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "Ivan"))
    assert fake_bot.sent == []
    assert fake_bot.logged == []


async def test_take_message_reports_that_nothing_took_it():
    """What lets a caller offer the message elsewhere before anything answers."""
    registry = Registry()
    _api(registry).message(at=LOOKUP)(_declines)
    fake_bot = FakeBot()
    took = await take_message(registry, _message(fake_bot, "Ivan"),
                              principal=STUDENT, session="SESSION",
                              bot=fake_bot, impersonator=None,
                              dialog=_dialog(), oplog=OpsLog(None))
    assert took is False
    assert fake_bot.sent == [], "the last word is the caller's to give"


# --- the last taker -------------------------------------------------------------------

async def test_the_last_taker_is_the_spec_that_took_the_last_message():
    registry = Registry()
    _api(registry).message(at=LOOKUP)(_noop)
    await _handle(registry, _message(FakeBot(), "Ivan"))
    assert registry.last_taker(777).feature == "directory"
    assert registry.last_taker(777).at == LOOKUP


async def test_the_last_taker_is_per_chat():
    registry = Registry()
    _api(registry).message(at=LOOKUP)(_noop)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "Ivan", chat_id=777))
    assert registry.last_taker(777) is not None
    assert registry.last_taker(888) is None, \
        "one chat's taker leaked into another's"


async def test_the_last_taker_belongs_to_the_registry_that_answered():
    # A second build_dispatcher must not inherit the first one's answers.
    registry = Registry()
    _api(registry).message(at=LOOKUP)(_noop)
    await _handle(registry, _message(FakeBot(), "Ivan"))
    assert Registry().last_taker(777) is None


async def test_a_message_nobody_took_leaves_no_taker_standing():
    # Otherwise a stale "a profile was just shown" outlives the profile.
    registry = Registry()
    _api(registry).message(at=LOOKUP, when=TEXT)(_noop)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "Ivan"))
    assert registry.last_taker(777) is not None
    await _handle(registry, _photo(fake_bot))
    assert registry.last_taker(777) is None


async def test_a_command_taking_the_message_is_not_a_chain_taker():
    # /me shows a profile too, and phase F must not read that as the chain
    # having answered.
    registry = Registry()
    bot = _api(registry)
    bot.message(at=LOOKUP, when=TEXT)(_noop)
    bot.command("me", "Show your profile.")(_noop)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "Ivan"))
    await _handle(registry, _message(fake_bot, "/me"))
    assert registry.last_taker(777) is None


async def test_a_dialog_taking_the_message_is_not_a_chain_taker():
    registry = Registry()
    bot = _api(registry)
    bot.message(at=LOOKUP, when=TEXT)(_noop)
    bot.dialog("edit", on_text=_noop)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "Ivan"))
    dialog = _dialog()
    await dialog.start("directory:edit")
    await _handle(registry, _message(fake_bot, "xoposhiy"), dialog=dialog)
    assert registry.last_taker(777) is None


async def test_a_crash_in_the_chain_leaves_no_stale_taker():
    async def takes_then_crashes(message):
        if message.text == "boom":
            raise RuntimeError("boom")

    registry = Registry()
    _api(registry).message(at=LOOKUP)(takes_then_crashes)
    fake_bot = FakeBot()
    await _handle(registry, _message(fake_bot, "Ivan"))
    assert registry.last_taker(777) is not None
    with pytest.raises(RuntimeError, match="boom"):
        await _handle(registry, _message(fake_bot, "boom"))
    assert registry.last_taker(777) is None
