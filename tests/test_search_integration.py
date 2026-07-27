"""End-to-end coverage for name search: real dispatcher, real ranking.

Scoring itself is covered in test_matching.py. What needs proving here is the
wiring -- that a clear winner opens a profile, that a tie opens a list, and
that text which is not a name gets the fallback instead of a wrong person.
"""

from datetime import datetime, timezone

from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.main import NOTHING_MATCHED, build_dispatcher


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
    setup.add_all([
        User(last_name="Ivanov", first_name="Ivan", telegram_id=222,
             role=Role.STUDENT, primary_cohort="2024", matriculation="30001111"),
        User(last_name="Belozerov", first_name="Iaroslav",
             role=Role.STUDENT, primary_cohort="2024", matriculation="30002222"),
        User(last_name="Redko", first_name="Mikhail",
             role=Role.STUDENT, primary_cohort="2024", matriculation="30003333"),
        User(last_name="Efremenko", first_name="Mikhail",
             role=Role.STUDENT, primary_cohort="2024", matriculation="30004444"),
    ])
    setup.commit()
    setup.close()


def _message_update(fake_bot, text: str, telegram_id=222, update_id=1) -> Update:
    msg = Message(
        message_id=100 + update_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=update_id, message=msg).as_(fake_bot)


async def _say(text: str) -> FakeBot:
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()
    await dp.feed_update(fake_bot, _message_update(fake_bot, text), dispatcher=dp)
    return fake_bot


async def test_a_clear_winner_opens_a_profile():
    fake_bot = await _say("Ярослав")
    assert "Iaroslav Belozerov" in fake_bot.sent[0].text
    assert "Several people match" not in fake_bot.sent[0].text


async def test_a_tie_lists_everyone_close():
    fake_bot = await _say("Михаил")
    text = fake_bot.sent[0].text
    assert text.startswith("Several people match:")
    assert "Mikhail Redko" in text
    assert "Mikhail Efremenko" in text
    assert "Iaroslav Belozerov" not in text


async def test_text_that_is_not_a_name_gets_the_fallback():
    fake_bot = await _say("как дела")
    assert fake_bot.sent[0].text == NOTHING_MATCHED
