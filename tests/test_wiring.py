"""What `build_dispatcher` mounts, proved by feeding real updates through it.

Two entry points and nothing else: every update is the core's, and the question
here is whether the whole of it -- a command, a button, a dialog, the chain, the
last word -- reaches a handler through a real `Dispatcher` built by
`jbcub_bot.main`. A unit test of the entry points would prove the parts
`tests/test_pipeline.py` already proves; what only this file can show is the
mounting and the middleware order behind it.

The probe feature (`_probe`) registers itself into the *live* registry after the
dispatcher was built. That it takes effect at all is the point: the entry points
read the registry per update, which is why every feature migration in turn
needed no edit to `main.py`.
"""
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import jbcub_bot.features as features_pkg
from jbcub_bot.core.contract import Registry, TEXT
from jbcub_bot.core.db import Base
from jbcub_bot.core.dialogs import Dialog
from jbcub_bot.core.loader import load_features
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.oplog import OpsLog
from jbcub_bot.core.pipeline import AGENT, LOOKUP
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


# --- every feature declares itself --------------------------------------------

def test_every_feature_registers_through_the_contract():
    """The assertion that fires when a contributor adds a package of the old
    `router` + `manifest` shape. Nothing hosts one any more: the shim,
    `core/intents.py` and `core/commands.py` went with `kb`'s migration.
    """
    registry = Registry()
    loaded = load_features(features_pkg, registry)

    assert {feature.name for feature in loaded} == \
        {reg.name for reg in registry.features()}


# --- the registry belongs to the build ----------------------------------------

async def test_building_twice_does_not_double_the_chain():
    first, second = _build(), _build()

    assert len(first["registry"].chain()) == len(second["registry"].chain()) == 2


async def test_building_twice_does_not_inherit_the_taker_record():
    first = _build()
    second = _build()
    bot = FakeBot()

    await first.feed_update(bot, _message(bot, "Ivanov"))

    assert first["registry"].last_taker(STUDENT_ID) is not None
    # The record is per-build state, so the second dispatcher has never seen
    # this chat -- the module global it replaces could not say that.
    assert second["registry"].last_taker(STUDENT_ID) is None


# --- what the core owns, end to end -------------------------------------------

async def test_a_migrated_command_is_dispatched_by_the_core():
    dp = _build()
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, "/me"))

    assert any("Ivan Ivanov" in text for text in _replies(bot))


async def test_a_migrated_button_is_dispatched_by_the_core():
    dp = _build()
    bot = FakeBot()

    await dp.feed_update(bot, _callback(bot, render.EDIT_CALLBACK))

    assert any("Edit your profile" in text for text in _edits(bot))


async def test_free_text_reaches_the_name_search_at_lookup():
    dp = _build()
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, "Ivanov"))

    assert any("Ivan Ivanov" in text for text in _replies(bot))
    assert not any("No one found." in text for text in _replies(bot))
    assert dp["registry"].last_taker(STUDENT_ID).feature == "directory"


async def test_free_text_nothing_takes_gets_the_last_word_and_the_ops_log_miss():
    dp = _build(log_chat_id=LOG_CHAT)
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, "как дела"))

    assert _replies(bot) == ["No one found."]
    assert len(bot.logged) == 1
    assert "как дела" in bot.logged[0].text


async def test_the_cores_cancel_runs_the_dialogs_own_on_cancel(monkeypatch):
    """The constraint task 10 lifted. `directory` owned a `/cancel` of its own
    while it was legacy, so the core's never ran; now it does, and the notice
    on the screen is still the feature's -- edited into the prompt message,
    which is what needs the dialog's data to outlive the end of the dialog."""
    _no_network(monkeypatch)
    dp = _build()
    bot = FakeBot()
    await _open_prompt(dp, bot)

    await dp.feed_update(bot, _message(bot, "/cancel", update_id=3))

    redraw = [m for m in bot.sent if isinstance(m, EditMessageText)][-1]
    assert "Editing cancelled." in redraw.text
    assert redraw.message_id == 7  # the prompt, not a fresh message


# --- one answer, never two ----------------------------------------------------

async def _open_prompt(dp, bot) -> None:
    """Tap "GitHub" on the edit screen, which opens `EditProfile.value`."""
    await dp.feed_update(bot, _callback(bot, render.EDIT_CALLBACK, update_id=1))
    await dp.feed_update(bot, _callback(bot, "dir:edit:f:github", update_id=2))


def _no_network(monkeypatch):
    async def fake_verify(field, handle, fetch=None):
        return Verdict.EXISTS

    monkeypatch.setattr(accounts, "verify", fake_verify)


async def test_text_in_a_registered_dialog_is_answered_once_by_its_on_text(
        monkeypatch):
    """A dialog takes the text meant for it, and nothing else answers.

    `directory:edit` is a registered dialog, so the pipeline routes the value to
    `on_value` itself and never walks the chain -- which is what stops the name
    search answering the value as well.
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

async def test_the_resolved_chain_is_logged_at_startup(caplog):
    """Both slots and their order, so a misplaced `at=` is visible in the
    deploy log rather than only in what the bot answers."""
    with caplog.at_level(logging.INFO):
        _build()

    messages = [record.getMessage() for record in caplog.records]
    assert f"chain at={LOOKUP}: directory.name_search" in messages
    assert f"chain at={AGENT}: kb.answer_question" in messages


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

        # Ahead of LOOKUP rather than at it: `directory` owns that position
        # now, and two handlers at one `at=` is a startup error.
        @bot.message(at=LOOKUP - 1, when=TEXT)
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
async def test_a_command_is_read_the_same_way_however_it_was_written(
        probe, written, arg):
    """`pipeline.command_of` is the one reading, and these are the forms that
    would break a second one: the bot's own name appended, whitespace that is
    not a space, a trailing newline off a pasted wiki page. The handler running
    *at all* is half the assertion, and `arg` is the other half."""
    dp = _build()
    probe.install(dp)
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, written))

    assert _replies(bot) == ["probe command"]
    assert [(kind, seen_arg) for kind, _, _, seen_arg in probe.seen] == \
        [("command", arg)]


async def test_a_command_in_a_caption_reads_the_same_way_too(probe):
    """The one form that is not text at all: a photo posted with "/probe tail"
    as its caption is as deliberate an address as typing it, so `command_of`
    reads `message.caption` when `text` is None."""
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


async def test_a_chain_handler_runs_ahead_of_everything_behind_it(probe):
    dp = _build()
    probe.install(dp)
    bot = FakeBot()

    await dp.feed_update(bot, _message(bot, "Ivanov"))

    # Ahead of both the name search and the shim, so neither one sees it.
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


async def test_a_tap_no_registered_key_matches_is_answered_rather_than_left(
        probe):
    """Telegram spins the button until the callback is answered, and there is
    nobody left to decline to: with no legacy router owning unknown keys, the
    core answers a leftover keyboard itself."""
    dp = _build()
    probe.install(dp)
    bot = FakeBot()

    await dp.feed_update(bot, _callback(bot, "gone:with:an:older:deploy"))

    assert probe.seen == []
    [answered] = [m for m in bot.sent if isinstance(m, AnswerCallbackQuery)]
    assert answered.text is None, "answered, and with nothing to say"
