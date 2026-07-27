"""End-to-end coverage for the edit screen: real dispatcher, real FSM.

The renderers are covered in test_edit.py. What needs proving here is the
wiring -- that a tap opens a prompt, that the *next* message reaches this
feature instead of the name search, and that what lands in the database is the
normalized value.
"""

from datetime import datetime, timezone

from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import jbcub_bot.features.directory as directory
from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory import accounts, edit
from jbcub_bot.features.directory.accounts import Verdict
from jbcub_bot.main import build_dispatcher


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_student(factory, telegram_id=222, **kw):
    setup = factory()
    setup.add(User(last_name="Ivanov", first_name="Ivan",
                   matriculation="30001111", telegram_id=telegram_id,
                   role=Role.STUDENT, primary_cohort="2024",
                   handle_observed="ivanov", **kw))
    setup.commit()
    setup.close()


def _seed_admin_and_student(factory):
    setup = factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT, primary_cohort="cohort-x",
                   status_line="target status"))
    setup.commit()
    setup.close()


def _message_update(fake_bot, telegram_id: int, text: str, update_id=1) -> Update:
    msg = Message(
        message_id=100 + update_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=update_id, message=msg).as_(fake_bot)


def _callback_update(fake_bot, telegram_id: int, data: str, update_id=2) -> Update:
    chat = Chat(id=telegram_id, type="private")
    shown = Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=TgUser(id=1, is_bot=True, first_name="bot"),
        text="whatever was on screen",
    ).as_(fake_bot)
    cb = CallbackQuery(
        id=f"cb-{update_id}",
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        chat_instance="chat-instance",
        data=data,
        message=shown,
    ).as_(fake_bot)
    return Update(update_id=update_id, callback_query=cb).as_(fake_bot)


def _edits(fake_bot):
    return [m for m in fake_bot.sent if isinstance(m, EditMessageText)]


def _alerts(fake_bot):
    return [m for m in fake_bot.sent if isinstance(m, AnswerCallbackQuery)]


def _stored(factory, column: str, telegram_id=222):
    read = factory()
    user = read.scalars(select(User).where(User.telegram_id == telegram_id)).one()
    value = getattr(user, column)
    read.close()
    return value


def _verdict(monkeypatch, verdict: Verdict):
    """Answer every existence check with `verdict`; never touch the network."""
    async def fake_verify(field, handle, fetch=None):
        return verdict

    monkeypatch.setattr(accounts, "verify", fake_verify)


async def _open_prompt(dp, fake_bot, field: str, telegram_id=222):
    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, telegram_id,
                         f"{edit.FIELD_CALLBACK_PREFIX}{field}"),
        dispatcher=dp)


async def test_edit_command_shows_the_screen():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/edit"),
                         dispatcher=dp)

    assert "Edit your profile" in fake_bot.sent[0].text


async def test_tapping_a_field_turns_the_screen_into_a_prompt():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")

    edits = _edits(fake_bot)
    assert len(edits) == 1
    assert edits[0].message_id == 7  # the message that carried the button
    assert "Send your GitHub username" in edits[0].text


async def test_the_next_message_becomes_the_value(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.EXISTS)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222,
                                         "https://github.com/alice", update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") == "alice"  # normalized, not raw
    assert _stored(factory, "github_sheet") is None    # the roster is untouched
    last = _edits(fake_bot)[-1]
    assert last.message_id == 7                        # the screen, redrawn
    assert "✅ GitHub updated." in last.text
    assert "GitHub: alice" in last.text


async def test_a_saved_value_ends_the_state(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.EXISTS)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "status_line")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "open to teams",
                                         update_id=3),
                         dispatcher=dp)
    fake_bot.sent.clear()
    # A second message is an ordinary one again: the name search answers it.
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "Ivanov", update_id=4),
                         dispatcher=dp)

    assert _stored(factory, "status_line") == "open to teams"
    assert any("Ivan Ivanov" in getattr(m, "text", "") for m in fake_bot.sent)


async def test_a_value_while_editing_never_reaches_the_name_search(monkeypatch):
    # The whole reason nl_fallback needs StateFilter(None): a Dispatcher's own
    # handlers run before its sub-routers, so the `.+` search intent would
    # otherwise swallow every value.
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.EXISTS)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "Ivanov", update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") == "Ivanov"
    # `or ""`: the tap's AnswerCallbackQuery carries text=None.
    texts = [getattr(m, "text", "") or "" for m in fake_bot.sent]
    assert not any("No one found." in t for t in texts)
    assert not any("Several people match" in t for t in texts)


async def test_an_unverifiable_account_is_saved_with_a_warning(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.UNKNOWN)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "alice", update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") == "alice"
    assert "couldn't verify alice" in _edits(fake_bot)[-1].text


async def test_a_missing_account_is_refused_and_the_prompt_stays(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.MISSING)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "nope", update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") is None
    redraw = _edits(fake_bot)[-1]
    assert "GitHub has no user nope." in redraw.text
    assert "Send your GitHub username" in redraw.text  # still asking


async def test_a_malformed_value_is_refused_without_a_network_call(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)

    async def never_called(field, handle, fetch=None):
        raise AssertionError("a value that cannot be a handle must not be checked")

    monkeypatch.setattr(accounts, "verify", never_called)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "not a username!",
                                         update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") is None
    assert "GitHub username" in _edits(fake_bot)[-1].text


async def test_a_too_long_status_is_refused():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "status_line")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "x" * 200, update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "status_line") is None
    assert "120 characters max" in _edits(fake_bot)[-1].text


async def test_cancel_button_puts_the_screen_back():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, edit.CANCEL_CALLBACK,
                                          update_id=3),
                         dispatcher=dp)

    assert "Edit your profile" in _edits(fake_bot)[-1].text


async def test_cancel_command_leaves_the_state_and_restores_the_screen():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "/cancel", update_id=3),
                         dispatcher=dp)
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "alice", update_id=4),
                         dispatcher=dp)

    assert _stored(factory, "github_self") is None  # no longer editing
    assert "Edit your profile" in _edits(fake_bot)[-1].text


async def test_cancel_outside_a_state_says_there_is_nothing_to_cancel():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/cancel"),
                         dispatcher=dp)

    assert "Nothing to cancel." in fake_bot.sent[0].text


async def test_an_unknown_field_is_refused():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    # gmail is configurable but not editable; birthday is admin-only.
    await _open_prompt(dp, fake_bot, "gmail")

    assert _edits(fake_bot) == []
    alerts = _alerts(fake_bot)
    assert len(alerts) == 1
    assert alerts[0].show_alert is True


async def test_an_unlinked_user_gets_no_prompt():
    factory = _session_factory()  # nobody seeded
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github", telegram_id=999)

    assert _edits(fake_bot) == []
    assert len(_alerts(fake_bot)) == 1


async def test_plain_text_still_searches_when_nobody_is_editing():
    # Regression: StateFilter(None) must narrow the fallback, not disable it.
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "Ivanov"),
                         dispatcher=dp)

    assert any("Ivan Ivanov" in getattr(m, "text", "")
               for m in fake_bot.sent)


async def test_a_search_under_impersonation_still_reaches_the_fallback():
    # StateFilter(None) resolves raw_state from the handler data, and /as
    # propagates a message event straight to dp.message -- past the outer
    # middleware that would have put raw_state there. Absent must read as
    # "no state", or /as stops finding anyone.
    factory = _session_factory()
    _seed_admin_and_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 Zhukovsky"),
                         dispatcher=dp)

    assert any("Zakhar Zhukovsky" in getattr(m, "text", "")
               for m in fake_bot.sent)


async def test_cancel_under_impersonation_does_not_crash():
    # Same missing-state path, reached by a command that needs the state.
    factory = _session_factory()
    _seed_admin_and_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 /cancel"),
                         dispatcher=dp)

    assert "Nothing to cancel." in fake_bot.sent[1].text


async def test_edit_under_impersonation_shows_the_target_read_only():
    factory = _session_factory()
    _seed_admin_and_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 /edit"),
                         dispatcher=dp)

    # sent[0] is cmd_as's "Showing as ..." notice; sent[1] is /edit's answer.
    shown = fake_bot.sent[1]
    assert "target status" in shown.text   # the target's row, not the admin's
    assert shown.reply_markup is None      # nothing tappable while impersonating


async def test_opening_the_screen_from_a_callback():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:edit"),
                         dispatcher=dp)

    assert "Edit your profile" in _edits(fake_bot)[-1].text


async def test_clear_asks_before_removing_anything():
    factory = _session_factory()
    _seed_student(factory, github_self="alice")
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, 222,
                         f"{edit.CLEAR_CALLBACK_PREFIX}github", update_id=3),
        dispatcher=dp)

    assert _stored(factory, "github_self") == "alice"  # nothing gone yet
    assert "Clear your GitHub?" in _edits(fake_bot)[-1].text


async def test_confirming_clears_the_value_and_leaves_the_roster_alone():
    factory = _session_factory()
    _seed_student(factory, github_self="alice", github_sheet="alice-roster")
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, 222,
                         f"{edit.CLEAR_DO_CALLBACK_PREFIX}github", update_id=3),
        dispatcher=dp)

    assert _stored(factory, "github_self") is None
    assert _stored(factory, "github_sheet") == "alice-roster"
    redraw = _edits(fake_bot)[-1]
    assert "✅ GitHub cleared." in redraw.text
    assert "GitHub: alice-roster" in redraw.text  # the roster's value shows now


async def test_clearing_an_unknown_field_is_refused():
    factory = _session_factory()
    _seed_student(factory, gmail="i@gmail.com")
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, 222,
                         f"{edit.CLEAR_DO_CALLBACK_PREFIX}gmail", update_id=3),
        dispatcher=dp)

    assert _stored(factory, "gmail") == "i@gmail.com"
    assert _edits(fake_bot) == []
    assert len(_alerts(fake_bot)) == 1


def test_manifest_lists_the_new_commands():
    names = {c.name for c in directory.manifest.commands}
    assert {"edit", "cancel"} <= names
