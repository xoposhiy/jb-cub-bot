"""End-to-end coverage for /as: real dispatcher, real Message, real handlers.

Every other impersonate test mocks model_copy/.as_/propagate_event, so none
of them prove the re-fed message actually reaches a real handler through a
real aiogram dispatcher and renders the *target's* view. This test builds a
real Dispatcher (jbcub_bot.main.build_dispatcher), feeds it a real aiogram
Message for "/as <matriculation> /me" from an admin, and asserts on the
handler's real side effect: the student's own /me profile text coming back
out, proving cmd_me ran with principal swapped to the student.
"""

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
    """Minimal stand-in for aiogram.Bot: just records outgoing methods.

    aiogram routes `await message.answer(text)` through `bot(method)`
    (Bot.__call__), so a bot only needs to be callable to make the real
    send path work end to end without hitting the network.
    """

    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _build_session_factory():
    # StaticPool shares one underlying connection across every session the
    # factory hands out, so data committed by one session (setup, or one
    # middleware-scoped session per event) is visible to the next -- even
    # though PrincipalMiddleware closes its session after each event.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _make_message(fake_bot, telegram_id: int, text: str) -> Message:
    chat = Chat(id=telegram_id, type="private")
    tg_user = TgUser(id=telegram_id, is_bot=False, first_name="tg")
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=tg_user,
        text=text,
    ).as_(fake_bot)


async def test_as_command_reaches_real_handler_as_target_student():
    session_factory = _build_session_factory()

    setup = session_factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT, primary_cohort="cohort-x"))
    setup.commit()
    setup.close()

    dp = build_dispatcher(session_factory=session_factory)

    fake_bot = FakeBot()
    msg = _make_message(fake_bot, telegram_id=777, text="/as 30009999 /me")
    update = Update(update_id=1, message=msg).as_(fake_bot)

    await dp.feed_update(fake_bot, update, dispatcher=dp)

    texts = [m.text for m in fake_bot.sent]
    assert len(texts) == 2
    assert texts[0] == "\U0001f464 Showing as Zakhar Zhukovsky:"
    # This is the key assertion: the *target* handler (cmd_me) really ran
    # with the swapped principal and rendered the student's own profile --
    # not just that propagate_event was awaited.
    assert "Zakhar Zhukovsky" in texts[1]
    assert "Cohort: cohort-x" in texts[1]
    assert "Role: Student" in texts[1]
