"""End-to-end /help through a real dispatcher: admin vs student vs unlinked."""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.main import build_dispatcher


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _msg(bot, tid, text):
    chat = Chat(id=tid, type="private")
    tg = TgUser(id=tid, is_bot=False, first_name="t")
    return Message(message_id=1, date=datetime.now(timezone.utc),
                   chat=chat, from_user=tg, text=text).as_(bot)


async def _run_help(factory, tid):
    dp = build_dispatcher(session_factory=factory)
    bot = FakeBot()
    upd = Update(update_id=1, message=_msg(bot, tid, "/help")).as_(bot)
    await dp.feed_update(bot, upd, dispatcher=dp)
    return "\n".join(m.text for m in bot.sent)


async def test_admin_help_has_admin_section():
    f = _factory()
    s = f()
    s.add(User(last_name="A", first_name="Anna", telegram_id=777, role=Role.ADMIN))
    s.commit(); s.close()
    out = await _run_help(f, 777)
    assert "🔐 Admin" in out
    assert "/sync" in out
    assert "/as" in out


async def test_student_help_hides_admin_section():
    f = _factory()
    s = f()
    s.add(User(last_name="Z", first_name="Zed", matriculation="30001",
               telegram_id=222, role=Role.STUDENT, primary_cohort="c"))
    s.commit(); s.close()
    out = await _run_help(f, 222)
    assert "/me" in out
    assert "🔐 Admin" not in out
    assert "/sync" not in out


async def test_unlinked_help_shows_notice():
    f = _factory()
    out = await _run_help(f, 999)  # no user row for this telegram id
    assert "You're not linked yet — ask a program admin for a one-time link." in out
    assert "🔐 Admin" not in out
