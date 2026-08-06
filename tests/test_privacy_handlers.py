"""End-to-end coverage for the privacy screen: real dispatcher, real callbacks.

The pure renderers are covered in test_privacy.py. What needs proving here is
the wiring -- that a tap really reaches the handler through a real aiogram
dispatcher, advances the level, commits it, and edits the same message.
"""

from datetime import datetime, timezone

from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import jbcub_bot.features.directory as directory
from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.screens import EXPIRED, NO_ROW, NOT_LINKED
from jbcub_bot.features.directory.visibility import COHORT, EVERYONE, STAFF_ONLY
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
                   handle_observed="ivanov", gmail="i@gmail.com", **kw))
    setup.commit()
    setup.close()


def _callback_update(fake_bot, telegram_id: int, data: str) -> Update:
    chat = Chat(id=telegram_id, type="private")
    shown = Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=TgUser(id=1, is_bot=True, first_name="bot"),
        text="whatever was on screen",
    ).as_(fake_bot)
    cb = CallbackQuery(
        id="cb-1",
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        chat_instance="chat-instance",
        data=data,
        message=shown,
    ).as_(fake_bot)
    return Update(update_id=1, callback_query=cb).as_(fake_bot)


def _seed_admin_and_student(factory):
    setup = factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT, primary_cohort="cohort-x",
                   gmail="student@gmail.com"))
    setup.commit()
    setup.close()


def _stale_callback_update(fake_bot, telegram_id: int, data: str) -> Update:
    """A callback on a message Telegram can no longer deliver (>48h old)."""
    stale = InaccessibleMessage(
        chat=Chat(id=telegram_id, type="private"), message_id=7,
    ).as_(fake_bot)
    cb = CallbackQuery(
        id="cb-1",
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        chat_instance="chat-instance",
        data=data,
        message=stale,
    ).as_(fake_bot)
    return Update(update_id=1, callback_query=cb).as_(fake_bot)


def _message_update(fake_bot, telegram_id: int, text: str, update_id=2) -> Update:
    msg = Message(
        message_id=100 + update_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=update_id, message=msg).as_(fake_bot)


def _stored_level(factory, field: str):
    read = factory()
    user = read.scalars(select(User).where(User.telegram_id == 222)).one()
    level = (user.visibility or {}).get(field)
    read.close()
    return level


def _edits(fake_bot):
    return [m for m in fake_bot.sent if isinstance(m, EditMessageText)]


def _alerts(fake_bot):
    return [m for m in fake_bot.sent if isinstance(m, AnswerCallbackQuery)]


async def test_privacy_command_shows_the_screen():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/privacy"),
                         dispatcher=dp)

    texts = [getattr(m, "text", "") for m in fake_bot.sent]
    assert any("Who sees your data" in t for t in texts)


async def test_tapping_a_field_advances_the_level_and_persists_it():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    # gmail defaults to `cohort`; one tap must move it to `everyone`.
    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:vis:gmail"),
                         dispatcher=dp)

    assert _stored_level(factory, "gmail") == EVERYONE


async def test_tapping_redraws_the_same_message():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:vis:gmail"),
                         dispatcher=dp)

    edits = _edits(fake_bot)
    assert len(edits) == 1
    assert edits[0].message_id == 7  # the message that carried the button
    assert "Who sees your data" in edits[0].text


async def test_three_taps_return_to_the_starting_level():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    for i in range(3):
        await dp.feed_update(fake_bot,
                             _callback_update(fake_bot, 222, "dir:vis:gmail"),
                             dispatcher=dp)

    assert _stored_level(factory, "gmail") == COHORT


async def test_back_button_redraws_the_profile():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:profile"),
                         dispatcher=dp)

    edits = _edits(fake_bot)
    assert len(edits) == 1
    assert "Name: Ivan Ivanov" in edits[0].text
    assert "Who sees your data" not in edits[0].text


async def test_opening_the_screen_from_the_profile_button():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:privacy"),
                         dispatcher=dp)

    edits = _edits(fake_bot)
    assert len(edits) == 1
    assert "Who sees your data" in edits[0].text


async def test_an_unknown_field_is_refused_without_touching_the_row():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    # A keyboard from an older deploy, or an admin-only field smuggled in.
    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:vis:birthday"),
                         dispatcher=dp)

    assert _edits(fake_bot) == []
    assert _stored_level(factory, "birthday") is None
    alerts = _alerts(fake_bot)
    assert len(alerts) == 1
    assert alerts[0].text == "Unknown field."
    assert alerts[0].show_alert is True


async def test_an_unlinked_user_gets_no_screen():
    factory = _session_factory()  # nobody seeded
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 999, "dir:vis:gmail"),
                         dispatcher=dp)

    assert _edits(fake_bot) == []
    alerts = _alerts(fake_bot)
    assert len(alerts) == 1
    assert alerts[0].text == NOT_LINKED
    assert alerts[0].show_alert is True


async def test_a_hidden_field_still_shows_on_the_owner_s_own_screen():
    factory = _session_factory()
    _seed_student(factory, visibility={"gmail": STAFF_ONLY})
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:privacy"),
                         dispatcher=dp)

    assert "i@gmail.com" in _edits(fake_bot)[0].text


def test_manifest_lists_the_privacy_command():
    names = {c.name for c in directory.manifest.commands}
    assert "privacy" in names


# --- Interactive /as keeps every privacy action on the target ---------------

async def test_privacy_under_impersonation_updates_the_target():
    factory = _session_factory()
    _seed_admin_and_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999",
                                         update_id=1),
                         dispatcher=dp)
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/me", update_id=2),
                         dispatcher=dp)

    # Start where the reported bug starts: the target's own profile.
    shown = next(m for m in fake_bot.sent
                 if "Zakhar Zhukovsky" in getattr(m, "text", "")
                 and getattr(m, "reply_markup", None) is not None)
    privacy_button = "dir:privacy"
    assert privacy_button in [
        button.callback_data
        for row in shown.reply_markup.inline_keyboard
        for button in row
    ]

    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, 777, privacy_button),
        dispatcher=dp,
    )
    privacy_screen = _edits(fake_bot)[-1]
    assert "student@gmail.com" in privacy_screen.text
    gmail = "dir:vis:gmail"
    assert gmail in [
        button.callback_data
        for row in privacy_screen.reply_markup.inline_keyboard
        for button in row
    ]

    await dp.feed_update(
        fake_bot, _callback_update(fake_bot, 777, gmail), dispatcher=dp
    )

    assert _stored_level(factory, "gmail") == EVERYONE
    redraw = _edits(fake_bot)[-1]
    assert "dir:profile" in [
        button.callback_data
        for row in redraw.reply_markup.inline_keyboard
        for button in row
    ]


# --- Fix 2: a bootstrap admin with no DB row can't silently "save" a tap ----

async def test_bootstrap_admin_without_a_row_is_refused_and_persists_nothing():
    factory = _session_factory()  # nobody seeded at all
    dp = build_dispatcher(session_factory=factory, bootstrap_ids={555})
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 555, "dir:vis:gmail"),
                         dispatcher=dp)

    assert _edits(fake_bot) == []
    alerts = _alerts(fake_bot)
    assert len(alerts) == 1
    assert alerts[0].text == NO_ROW
    assert alerts[0].show_alert is True

    check = factory()
    assert check.scalars(select(User)).all() == []  # nothing was created
    check.close()


# --- Fix 3: a stale (>48h) privacy screen answers instead of crashing ------

async def test_opening_a_stale_screen_answers_instead_of_crashing():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _stale_callback_update(fake_bot, 222, "dir:privacy"),
                         dispatcher=dp)

    assert _edits(fake_bot) == []
    alerts = _alerts(fake_bot)
    assert len(alerts) == 1
    assert alerts[0].text == EXPIRED
    assert alerts[0].show_alert is True


async def test_back_button_on_a_stale_screen_answers_instead_of_crashing():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _stale_callback_update(fake_bot, 222, "dir:profile"),
                         dispatcher=dp)

    assert _edits(fake_bot) == []
    alerts = _alerts(fake_bot)
    assert len(alerts) == 1
    assert alerts[0].text == EXPIRED
    assert alerts[0].show_alert is True
