"""No write reaches a row that is only a projection of the cohort sheet.

The guard is this one enumeration rather than a change to the feature contract:
`identity.is_provisional` is a free function, so a future screen that writes and
forgets to ask is allowed by default. Adding a write screen means adding it
here.
"""

from datetime import datetime, timezone

from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory import accounts, edit, privacy
from jbcub_bot.features.directory.accounts import Verdict
from jbcub_bot.features.directory.screens import PROVISIONAL
from jbcub_bot.features.directory.visibility import (
    CONFIGURABLE_FIELDS,
    EDITABLE_FIELDS,
    editable_column,
    level_of,
)
from jbcub_bot.main import build_dispatcher

# A value each editable field accepts, so a refusal is the only thing that can
# stop it from being saved.
SENT_VALUE = {
    "status_line": "open to teams",
    "github": "alice",
    "codeforces": "alice",
}

PROVISIONAL_ID = 222
REAL_ID = 333


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
    """One first-year awaiting a number, one ordinary student beside them."""
    setup = factory()
    setup.add(User(last_name="Nova", first_name="Nina",
                   matriculation="TMP-A1B2", telegram_id=PROVISIONAL_ID,
                   role=Role.STUDENT, primary_cohort="2026",
                   handle_sheet="nina", status_line="placeholder status",
                   github_self="nina-dev", codeforces_self="nina"))
    setup.add(User(last_name="Ivanov", first_name="Ivan",
                   matriculation="30000001", telegram_id=REAL_ID,
                   role=Role.STUDENT, primary_cohort="2024",
                   status_line="real status", github_self="ivan-dev",
                   codeforces_self="ivan"))
    setup.commit()
    setup.close()


_next_id = [0]


def _update_id() -> int:
    _next_id[0] += 1
    return _next_id[0]


def _message_update(fake_bot, telegram_id: int, text: str) -> Update:
    update_id = _update_id()
    msg = Message(
        message_id=100 + update_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=update_id, message=msg).as_(fake_bot)


def _callback_update(fake_bot, telegram_id: int, data: str) -> Update:
    update_id = _update_id()
    shown = Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
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


def _row(factory, telegram_id: int) -> User:
    read = factory()
    user = read.scalars(select(User).where(User.telegram_id == telegram_id)).one()
    read.expunge(user)
    read.close()
    return user


def _texts(fake_bot) -> list[str]:
    return [getattr(method, "text", None) or "" for method in fake_bot.sent
            if isinstance(method, (SendMessage, EditMessageText,
                                   AnswerCallbackQuery))]


def _verdict_always_exists(monkeypatch):
    async def fake_verify(field, handle, fetch=None):
        return Verdict.EXISTS

    monkeypatch.setattr(accounts, "verify", fake_verify)


async def _cycle_a_level(dp, fake_bot, telegram_id: int, field: str):
    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, telegram_id,
                         f"{privacy.FIELD_CALLBACK}:{field}"),
        dispatcher=dp)


async def _send_a_value(dp, fake_bot, telegram_id: int, field: str):
    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, telegram_id,
                         f"{edit.FIELD_CALLBACK}:{field}"),
        dispatcher=dp)
    await dp.feed_update(
        fake_bot,
        _message_update(fake_bot, telegram_id, SENT_VALUE[field]),
        dispatcher=dp)


async def _clear_a_value(dp, fake_bot, telegram_id: int, field: str):
    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, telegram_id,
                         f"{edit.CLEAR_DO_CALLBACK}:{field}"),
        dispatcher=dp)


async def test_a_provisional_principal_writes_nothing_and_is_told_why(monkeypatch):
    factory = _session_factory()
    _seed(factory)
    _verdict_always_exists(monkeypatch)
    dp = build_dispatcher(session_factory=factory)
    before = _row(factory, PROVISIONAL_ID)

    for spec in CONFIGURABLE_FIELDS:
        fake_bot = FakeBot()
        await _cycle_a_level(dp, fake_bot, PROVISIONAL_ID, spec.name)
        assert PROVISIONAL in _texts(fake_bot), spec.name
        after = _row(factory, PROVISIONAL_ID)
        assert level_of(after, spec.name) == level_of(before, spec.name), spec.name

    for spec in EDITABLE_FIELDS:
        column = editable_column(spec)
        for attempt in (_send_a_value, _clear_a_value):
            fake_bot = FakeBot()
            await attempt(dp, fake_bot, PROVISIONAL_ID, spec.name)
            assert PROVISIONAL in _texts(fake_bot), (spec.name, attempt.__name__)
            after = _row(factory, PROVISIONAL_ID)
            assert getattr(after, column) == getattr(before, column), spec.name


async def test_the_same_screens_still_write_for_an_ordinary_student(monkeypatch):
    factory = _session_factory()
    _seed(factory)
    _verdict_always_exists(monkeypatch)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    for spec in CONFIGURABLE_FIELDS:
        before = level_of(_row(factory, REAL_ID), spec.name)
        await _cycle_a_level(dp, fake_bot, REAL_ID, spec.name)
        assert level_of(_row(factory, REAL_ID), spec.name) != before, spec.name

    for spec in EDITABLE_FIELDS:
        column = editable_column(spec)
        await _send_a_value(dp, fake_bot, REAL_ID, spec.name)
        assert getattr(_row(factory, REAL_ID), column) == SENT_VALUE[spec.name]
        await _clear_a_value(dp, fake_bot, REAL_ID, spec.name)
        assert getattr(_row(factory, REAL_ID), column) is None, spec.name

    assert PROVISIONAL not in _texts(fake_bot)
