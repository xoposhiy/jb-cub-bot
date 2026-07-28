"""Access ends when the roster stops naming you.

Hiding a departed student from other people's searches is not the same as
taking their own access away: their telegram_id is still bound, so every
command still authenticated them. These go through a real dispatcher because
the guard has to hold for every entry point at once -- commands, plain-text
intents and button taps -- and a unit test of one handler would prove none of
that.
"""

from datetime import datetime, timezone

from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core.db import Base
from jbcub_bot.core.middleware import DEPARTED_NOTICE
from jbcub_bot.core.models import Role, User
from jbcub_bot.main import build_dispatcher

DEPARTED_TID = 222
ACTIVE_TID = 333


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


def _seed(factory):
    setup = factory()
    setup.add(User(last_name="Expelled", first_name="Eve",
                   matriculation="30000009", telegram_id=DEPARTED_TID,
                   role=Role.STUDENT, primary_cohort="2024",
                   handle_sheet="eve", handle_observed="eve",
                   departed_at="2026-07-28"))
    setup.add(User(last_name="Ivanov", first_name="Ivan",
                   matriculation="30000001", telegram_id=ACTIVE_TID,
                   role=Role.STUDENT, primary_cohort="2024",
                   handle_sheet="ivan", handle_observed="ivan"))
    setup.commit()
    setup.close()


def _message_update(fake_bot, telegram_id: int, text: str) -> Update:
    msg = Message(
        message_id=5,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=1, message=msg).as_(fake_bot)


def _callback_update(fake_bot, telegram_id: int, data: str) -> Update:
    shown = Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
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


def _texts(fake_bot) -> list[str]:
    return [m.text for m in fake_bot.sent if isinstance(m, SendMessage)]


async def test_a_departed_student_cannot_read_their_own_profile():
    factory = _session_factory()
    _seed(factory)
    bot, dp = FakeBot(), build_dispatcher(factory)
    await dp.feed_update(bot, _message_update(bot, DEPARTED_TID, "/me"))
    assert _texts(bot) == [DEPARTED_NOTICE]  # no profile rendered


async def test_a_departed_student_cannot_search_for_anyone():
    # Plain text goes through the intent router, a different entry point from a
    # command -- and the one that would hand out someone else's profile.
    factory = _session_factory()
    _seed(factory)
    bot, dp = FakeBot(), build_dispatcher(factory)
    await dp.feed_update(bot, _message_update(bot, DEPARTED_TID, "Ivanov"))
    said = _texts(bot)
    assert said == [DEPARTED_NOTICE]
    assert not any("Ivan" in text for text in said)


async def test_a_departed_student_gets_an_alert_when_tapping_a_button():
    factory = _session_factory()
    _seed(factory)
    bot, dp = FakeBot(), build_dispatcher(factory)
    await dp.feed_update(bot, _callback_update(bot, DEPARTED_TID, "dir:privacy"))
    alerts = [m for m in bot.sent if isinstance(m, AnswerCallbackQuery)]
    assert [a.text for a in alerts] == [DEPARTED_NOTICE]
    assert all(a.show_alert for a in alerts)  # not a toast that scrolls past


async def test_a_student_still_on_the_roster_is_unaffected():
    # The guard must not cost everyone else their access.
    factory = _session_factory()
    _seed(factory)
    bot, dp = FakeBot(), build_dispatcher(factory)
    await dp.feed_update(bot, _message_update(bot, ACTIVE_TID, "/me"))
    said = _texts(bot)
    assert DEPARTED_NOTICE not in said
    assert any("Ivan" in text for text in said)


async def test_a_bootstrap_admin_is_never_locked_out_by_the_mark():
    # BOOTSTRAP_ADMIN_IDS is the way back in when the roster is wrong. If the
    # mark could shut that door, a bad /sync would leave nobody able to fix it.
    factory = _session_factory()
    _seed(factory)
    bot, dp = FakeBot(), build_dispatcher(factory, {DEPARTED_TID})
    await dp.feed_update(bot, _message_update(bot, DEPARTED_TID, "/me"))
    assert DEPARTED_NOTICE not in _texts(bot)
