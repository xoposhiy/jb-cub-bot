"""What `build_dispatcher` mounts, proved by feeding real updates through it.

Phase B puts the new pipeline in front of routers that still own their own
commands, callbacks and FSM states, so the two questions here are "does a
legacy feature still work" and "does anything answer twice". Both are asked of
a real `Dispatcher` built by `jbcub_bot.main`, because the wiring is the thing
under test -- a unit test of the entry points would prove the parts task 3
already proved.

The probe feature (`_probe`) registers itself into the *live* registry after the
dispatcher was built. That it takes effect at all is the point: the entry points
read the registry per update, which is why migrating `help`, `impersonate` and
`directory` needs no edit to `main.py`.
"""
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram import Router
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import jbcub_bot.features as features_pkg
from jbcub_bot.core import legacy
from jbcub_bot.core.commands import CommandSpec as LegacyCommandSpec
from jbcub_bot.core.contract import ContractError, Registry, TEXT
from jbcub_bot.core.db import Base
from jbcub_bot.core.dialogs import Dialog
from jbcub_bot.core.intents import Intent
from jbcub_bot.core.loader import LoadedFeature, Manifest, load_features
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.oplog import OpsLog
from jbcub_bot.core.pipeline import LEGACY, LOOKUP
from jbcub_bot.features.directory import accounts, render
from jbcub_bot.features.directory.accounts import Verdict
from jbcub_bot.main import build_dispatcher

LOG_CHAT = "-1009999"
STUDENT_ID = 222


class FakeBot:
    """Callable stand-in for `Bot`: `message.answer` goes through `bot(method)`,
    so the real send path runs without a network."""

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


def _factory():
    # StaticPool shares one connection, so what the setup session commits is
    # visible to every per-update session PrincipalMiddleware opens and closes.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    setup = maker()
    setup.add(User(last_name="Ivanov", first_name="Ivan",
                   matriculation="30001111", telegram_id=STUDENT_ID,
                   role=Role.STUDENT, primary_cohort="2024"))
    setup.commit()
    setup.close()
    return maker


def _detach() -> None:
    """Un-parent every legacy router so `build_dispatcher` may run again.

    Feature routers are module-level singletons and aiogram refuses to
    re-attach one; `tests/conftest.py` does this before every test, and a test
    that builds twice has to do it in between as well.
    """
    for feature in load_features(features_pkg, Registry()):
        if feature.router is not None:
            feature.router._parent_router = None


def _build(**kwargs):
    return build_dispatcher(_factory(), bootstrap_ids=set(), **kwargs)


def _message(bot, text, *, update_id=1, telegram_id=STUDENT_ID, **kwargs):
    message = Message(
        message_id=100 + update_id, date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text, **kwargs,
    ).as_(bot)
    return Update(update_id=update_id, message=message).as_(bot)


def _callback(bot, data, *, update_id=2, telegram_id=STUDENT_ID):
    chat = Chat(id=telegram_id, type="private")
    shown = Message(
        message_id=7, date=datetime.now(timezone.utc), chat=chat,
        from_user=TgUser(id=1, is_bot=True, first_name="bot"),
        text="whatever was on screen",
    ).as_(bot)
    tap = CallbackQuery(
        id=f"cb-{update_id}",
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        chat_instance="chat-instance", data=data, message=shown,
    ).as_(bot)
    return Update(update_id=update_id, callback_query=tap).as_(bot)


def _replies(bot) -> list[str]:
    return [getattr(m, "text", "") or "" for m in bot.sent]


def _edits(bot) -> list[str]:
    return [m.text for m in bot.sent if isinstance(m, EditMessageText)]


# --- the registry belongs to the build ----------------------------------------

async def test_building_twice_does_not_double_the_chain(caplog):
    with caplog.at_level(logging.INFO):
        first = _build()
        _detach()
        second = _build()

    assert len(first["registry"].chain()) == len(second["registry"].chain()) == 1
    # And the shim inside that one slot hosts the same two intents both times,
    # rather than four: it is built per call, like everything else here.
    hosted = [record.getMessage() for record in caplog.records
              if "legacy shim" in record.getMessage()]
    assert hosted == [
        f"legacy shim at={LEGACY} hosts 2 intents: "
        f"directory.search, kb.offer",
    ] * 2


async def test_building_twice_does_not_inherit_the_taker_record():
    first = _build()
    _detach()
    second = _build()
    bot = FakeBot()

    await first.feed_update(bot, _message(bot, "Ivanov"))

    assert first["registry"].last_taker(STUDENT_ID) is not None
    # The record is per-build state, so the second dispatcher has never seen
    # this chat -- the module global it replaces could not say that.
    assert second["registry"].last_taker(STUDENT_ID) is None


# --- the legacy routers still get their turn ----------------------------------

async def test_a_legacy_command_still_reaches_its_router():
    dp = _build()
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, "/me"))

    assert any("Ivan Ivanov" in text for text in _replies(bot))


async def test_a_legacy_callback_still_reaches_its_router():
    dp = _build()
    bot = FakeBot()

    await dp.feed_update(bot, _callback(bot, render.EDIT_CALLBACK))

    assert any("Edit your profile" in text for text in _edits(bot))


async def test_free_text_reaches_the_shim_and_a_legacy_intent_takes_it():
    dp = _build()
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, "Ivanov"))

    assert any("Ivan Ivanov" in text for text in _replies(bot))
    assert not any("No one found." in text for text in _replies(bot))


async def test_free_text_nothing_takes_gets_the_last_word_and_the_ops_log_miss():
    dp = _build(log_chat_id=LOG_CHAT)
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, "как дела"))

    assert _replies(bot) == ["No one found."]
    assert len(bot.logged) == 1
    assert "как дела" in bot.logged[0].text


async def test_a_legacy_cancel_is_left_to_the_router_that_owns_it(monkeypatch):
    """`/cancel` is the core's own command, but `directory` still has one until
    task 10 -- and only its answer can redraw the edit screen."""
    _no_network(monkeypatch)
    dp = _build()
    bot = FakeBot()
    await _open_prompt(dp, bot)

    await dp.feed_update(bot, _message(bot, "/cancel", update_id=3))

    assert any("Editing cancelled." in text for text in _edits(bot))


# --- one answer, never two ----------------------------------------------------

async def _open_prompt(dp, bot) -> None:
    """Tap "GitHub" on the edit screen, which opens `EditProfile.value`."""
    await dp.feed_update(bot, _callback(bot, render.EDIT_CALLBACK, update_id=1))
    await dp.feed_update(bot, _callback(bot, "dir:edit:f:github", update_id=2))


def _no_network(monkeypatch):
    async def fake_verify(field, handle, fetch=None):
        return Verdict.EXISTS

    monkeypatch.setattr(accounts, "verify", fake_verify)


async def test_text_in_a_legacy_state_is_answered_once_by_the_legacy_handler(
        monkeypatch):
    """The transitional stand-in for `StateFilter(None)`.

    `EditProfile.value` is a state no registered dialog owns, so the entry point
    declines the message and the chain never runs -- otherwise the shim's `.+`
    intents would answer the value as well as `on_value` saving it.
    """
    _no_network(monkeypatch)
    factory = _factory()
    dp = build_dispatcher(factory, bootstrap_ids=set(), log_chat_id=LOG_CHAT)
    bot = FakeBot()
    await _open_prompt(dp, bot)
    bot.sent.clear()

    # A value that is also a name the search would have found.
    await dp.feed_update(bot, _message(bot, "Ivanov", update_id=3))

    read = factory()
    saved = read.scalars(select(User).where(
        User.telegram_id == STUDENT_ID)).one().github_self
    read.close()
    assert saved == "Ivanov"
    assert all("GitHub: Ivanov" in text for text in _edits(bot))
    assert len(_edits(bot)) == 1
    assert not any("No one found." in text or "Several people match" in text
                   for text in _replies(bot))
    assert bot.logged == []


# --- the startup log ----------------------------------------------------------

async def test_the_resolved_chain_and_the_shim_are_logged_at_startup(caplog):
    with caplog.at_level(logging.INFO):
        _build()

    messages = [record.getMessage() for record in caplog.records]
    # The shim is a chain entry like any other, and it says it is the shim.
    assert f"chain at={LEGACY}: legacy.offer" in messages
    assert any("legacy shim" in message and "directory.search, kb.offer" in message
               for message in messages)


# --- a migrated feature needs no change here ----------------------------------

@pytest.fixture
def probe():
    """A feature that registers itself into a *built* dispatcher's registry.

    Everything the entry points read -- the commands, the buttons, the dialogs,
    the chain -- is read per update, so a feature that appears after the mount
    still works. That is what makes tasks 7, 8 and 10 changes to `features/`
    alone.
    """
    seen: list = []

    def install(dp):
        bot = dp["registry"].api_for("probe")
        bot.describe("🔬", "Probe", "A feature registered after the mount.")

        @bot.command("probe", "Prove a command reaches the core.")
        async def cmd_probe(message, dialog, oplog, arg):
            seen.append(("command", dialog, oplog, arg))
            await message.answer("probe command")

        @bot.button("probe:key")
        async def cb_probe(callback, dialog, oplog, arg):
            seen.append(("button", dialog, oplog, arg))
            await callback.answer("probe button")

        @bot.message(at=LOOKUP, when=TEXT)
        async def on_text(message, oplog):
            seen.append(("chain", None, oplog, ""))
            await message.answer("probe chain")

        handle = bot.dialog("ask", on_text=_on_dialog_text)
        return handle

    async def _on_dialog_text(message, dialog):
        seen.append(("dialog", dialog, None, ""))
        await message.answer("probe dialog")

    return SimpleNamespace(install=install, seen=seen)


async def test_a_command_registered_after_the_mount_is_dispatched(probe):
    dp = _build()
    probe.install(dp)
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, "/probe tail"))

    assert _replies(bot) == ["probe command"]
    kind, dialog, oplog, arg = probe.seen[0]
    assert (kind, arg) == ("command", "tail")
    # Both middlewares mounted for messages, in an order that works: `dialog`
    # is built from the FSMContext aiogram resolved, and `oplog` from the
    # per-update Bot.
    assert isinstance(dialog, Dialog) and isinstance(oplog, OpsLog)


@pytest.mark.parametrize("written, arg", [
    ("/probe tail", "tail"),
    # Telegram appends the bot's name to every command tapped in a group.
    ("/probe@jbcub_bot tail", "tail"),
    # Any whitespace splits, so a command pasted off a wiki still arrives.
    ("/probe\ntail", "tail"),
    ("/probe\n", ""),
])
async def test_the_filter_and_the_dispatcher_read_a_command_the_same_way(
        probe, written, arg):
    """Both sides call `pipeline.command_of`, and this is why they must.

    The filter answering True is a promise that `_run_command` will find the
    same name. Two readings that drifted apart would fail silently and only on
    the awkward forms: the entry point takes the update off the legacy router
    that owned it, finds nothing under its own name for it, and answers
    "I don't know /probe." So the handler running *at all* here is the
    assertion -- it means both readings agreed -- and `arg` is the second one.
    """
    dp = _build()
    probe.install(dp)
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, written))

    assert _replies(bot) == ["probe command"]
    assert [(kind, seen_arg) for kind, _, _, seen_arg in probe.seen] == \
        [("command", arg)]


async def test_a_command_in_a_caption_reads_the_same_way_too(probe):
    """The one form that is not text at all. `nl_fallback` never saw it; both
    the filter and the pipeline read `message.caption` when `text` is None."""
    dp = _build()
    probe.install(dp)
    bot = FakeBot()
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]

    await dp.feed_update(bot, _message(bot, None, caption="/probe tail",
                                       photo=photo))

    assert _replies(bot) == ["probe command"]
    assert probe.seen[0][3] == "tail"


async def test_a_button_registered_after_the_mount_is_dispatched(probe):
    dp = _build()
    probe.install(dp)
    bot = FakeBot()

    await dp.feed_update(bot, _callback(bot, "probe:key:42"))

    kind, dialog, oplog, arg = probe.seen[0]
    assert (kind, arg) == ("button", "42")
    # The same for callbacks, which is the event kind a misordered middleware
    # stack would break on its own.
    assert isinstance(dialog, Dialog) and isinstance(oplog, OpsLog)


async def test_a_chain_handler_runs_ahead_of_the_legacy_shim(probe):
    dp = _build()
    probe.install(dp)
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, "Ivanov"))

    # at=LOOKUP is ahead of at=LEGACY, so the name search never sees it.
    assert _replies(bot) == ["probe chain"]
    assert dp["registry"].last_taker(STUDENT_ID).feature == "probe"


async def test_a_registered_dialog_takes_the_text_the_chain_would_have(probe):
    dp = _build()
    handle = probe.install(dp)
    bot = FakeBot()
    storage_dialog = None

    await dp.feed_update(bot, _message(bot, "/probe"))
    # Open the dialog through the same Dialog the entry point injected, which
    # is the FSMContext aiogram will resolve for the next message too.
    storage_dialog = probe.seen[0][1]
    await handle.start(storage_dialog)
    bot.sent.clear()
    probe.seen.clear()

    await dp.feed_update(bot, _message(bot, "Ivanov", update_id=2))

    assert _replies(bot) == ["probe dialog"]
    assert [kind for kind, *_ in probe.seen] == ["dialog"]


async def test_a_photo_is_still_answered_once_the_registry_owns_the_chain(probe):
    dp = _build()
    probe.install(dp)
    bot = FakeBot()
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]

    await dp.feed_update(bot, _message(bot, None, photo=photo))

    # `when=TEXT` on the probe and on the shim, so neither takes a photo and
    # the core's own last word answers it.
    assert any("/help" in text for text in _replies(bot))
    assert probe.seen == []


async def test_an_unmatched_callback_is_left_to_the_legacy_routers(probe):
    """A tap the registry does not know is declined while anything is legacy:
    answering it here as well as in the router that owns the key would answer
    one tap twice."""
    dp = _build()
    probe.install(dp)
    bot = FakeBot()

    await dp.feed_update(bot, _callback(bot, render.PRIVACY_CALLBACK))

    assert probe.seen == []
    assert any("Who sees your data" in text for text in _edits(bot))


# --- what the filters will say once the migrations are done -------------------
# The dispatcher-level tests above can only show the filters as today's four
# legacy features leave them. These ask them what they will say in tasks 7, 8
# and 10 -- which is the claim that `main.py` needs no edit then, and the one
# claim a green suite today cannot make on its own.

def _legacy(name: str, *command_names: str, intents=()) -> LoadedFeature:
    return LoadedFeature(
        name=name, module=object(), router=Router(name=name),
        manifest=Manifest(name=name, commands=[
            LegacyCommandSpec(command, "Whatever.") for command in command_names
        ], intents=list(intents)),
    )


async def test_the_core_takes_cancel_once_no_legacy_feature_owns_one():
    """`/cancel` is the core's; `directory` only borrows it until task 10.

    Nothing may register `cancel` -- `validate()` refuses it -- so a filter that
    just asked `registry.commands()` would decline `/cancel` for ever and the
    fallback router would answer "I don't know /cancel." the day `directory`
    hands it back.
    """
    registry = Registry()
    still_borrowed = legacy.core_owns_message(registry, [_legacy("d", "cancel")])
    handed_back = legacy.core_owns_message(registry, [_legacy("d", "me")])
    bot = FakeBot()

    assert await still_borrowed(_message(bot, "/cancel").message) is False
    assert await handed_back(_message(bot, "/cancel").message) is True


async def test_a_command_the_registry_learns_stops_being_the_routers():
    """One filter over one registry, asked twice: the answer changes because
    the registry did. That is the whole of "task 7 edits no main.py"."""
    registry = Registry()
    owned = legacy.core_owns_message(registry, [_legacy("help")])
    bot = FakeBot()
    message = _message(bot, "/help").message

    assert await owned(message) is False

    api = registry.api_for("help")
    api.command("help", "What I can do.")(lambda m: None)

    assert await owned(message) is True


async def test_the_last_legacy_router_leaving_gives_the_core_every_tap():
    """An unmatched tap is declined only because a legacy router may own the
    key. With none left there is nobody to decline to, and
    `buttons.handle_callback` answers it rather than leaving it spinning."""
    registry = Registry()
    bot = FakeBot()
    tap = _callback(bot, "nothing:registered").callback_query

    assert await legacy.core_owns_callback(registry, [_legacy("d")])(tap) is False
    assert await legacy.core_owns_callback(registry, [])(tap) is True


# --- the bridge: a legacy manifest, declared like a real feature --------------
# `core/help.py` reads the registry and knows nothing about legacy, so `adopt`
# republishes every manifest through a real `BotApi`. What that must not do is
# take the update off the router that still owns the handler.

def _adopted(*loaded: LoadedFeature) -> Registry:
    registry = Registry()
    shim = legacy.install(registry)
    shim.adopt(list(loaded))
    return registry


async def test_a_bridged_command_is_declared_but_still_left_to_its_router():
    loaded = [_legacy("directory", "me")]
    registry = _adopted(*loaded)
    bot = FakeBot()

    # Declared, so /help lists it under directory's own heading...
    assert registry.commands()["me"].feature == "directory"
    # ...and declined, because the handler is on the router, not in the spec.
    owned = legacy.core_owns_message(registry, loaded)
    assert await owned(_message(bot, "/me").message) is False


async def test_a_legacy_intent_is_bridged_as_a_note_not_a_second_chain_entry():
    """A `bot.message` would be offered the text a second time, on top of the
    shim that already routes it -- so an intent becomes a bare `💬` line."""
    intent = Intent("d.search", r".+", handler=None, description="type a name")
    registry = _adopted(_legacy("directory", intents=[intent]))

    assert len(registry.chain()) == 1  # the shim's slot, and only it
    notes = [note.text for reg in registry.features() for note in reg.notes]
    assert notes == ["💬 type a name"]


async def test_the_bridge_refuses_a_name_a_migrated_feature_already_declared():
    """The one thing the bridge validates for itself: `registry.validate()` ran
    before these declarations existed, and re-running it would refuse a legacy
    `/cancel` that `directory` legitimately still owns."""
    registry = Registry()
    api = registry.api_for("directory")
    api.command("me", "Show your profile.")(lambda message: None)
    shim = legacy.install(registry)

    with pytest.raises(ContractError) as raised:
        shim.adopt([_legacy("relic", "me")])

    assert "/me" in str(raised.value)
    assert "directory" in str(raised.value) and "relic" in str(raised.value)
