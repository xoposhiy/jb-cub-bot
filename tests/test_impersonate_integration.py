"""End-to-end coverage for the mode: real dispatcher, real Message, real
handlers.

Every other impersonate test mocks the handler directly, so none of them prove
a real aiogram dispatcher carries the mode across separate updates and renders
the *target's* view. This test builds a real Dispatcher
(jbcub_bot.main.build_dispatcher), feeds it real aiogram Messages for /as,
/me and /unas from an admin across several updates, and asserts on each
handler's real side effect.
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


def _make_message(fake_bot, telegram_id: int, text: str,
                  message_id: int = 1) -> Message:
    chat = Chat(id=telegram_id, type="private")
    tg_user = TgUser(id=telegram_id, is_bot=False, first_name="tg")
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=tg_user,
        text=text,
    ).as_(fake_bot)


def _feed(dp, fake_bot, telegram_id, text, update_id):
    msg = _make_message(fake_bot, telegram_id, text, message_id=update_id)
    return dp.feed_update(fake_bot, Update(update_id=update_id,
                                           message=msg).as_(fake_bot))


async def test_the_mode_lasts_across_messages_and_then_ends():
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

    await _feed(dp, fake_bot, 777, "/as 30009999", 1)
    await _feed(dp, fake_bot, 777, "/me", 2)
    texts = [m.text for m in fake_bot.sent]
    # The key assertion: cmd_me really ran with the principal swapped, one
    # whole update after the command that swapped it.
    assert any("Zakhar Zhukovsky" in t and "Cohort: cohort-x" in t
               for t in texts)

    # Still in the mode two updates later -- this is what "sticky" means.
    await _feed(dp, fake_bot, 777, "/me", 3)
    assert sum("Cohort: cohort-x" in t for t in
               [m.text for m in fake_bot.sent]) == 2

    await _feed(dp, fake_bot, 777, "/unas", 4)
    await _feed(dp, fake_bot, 777, "/me", 5)
    assert "Anna Adminova" in [m.text for m in fake_bot.sent][-1]


async def test_an_admin_command_is_refused_inside_the_mode():
    session_factory = _build_session_factory()
    setup = session_factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT))
    setup.commit()
    setup.close()

    dp = build_dispatcher(session_factory=session_factory)
    fake_bot = FakeBot()

    await _feed(dp, fake_bot, 777, "/as 30009999", 1)
    await _feed(dp, fake_bot, 777, "/help", 2)
    await _feed(dp, fake_bot, 777, "/as 30009999", 3)

    texts = [m.text for m in fake_bot.sent]
    assert "Admins only." in texts        # /as itself, inside the mode
    help_text = next(t for t in texts if "/me" in t)
    assert "/sync" not in help_text       # the student's /help, not the admin's


async def test_every_answer_inside_the_mode_is_announced():
    session_factory = _build_session_factory()
    setup = session_factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT))
    setup.commit()
    setup.close()

    dp = build_dispatcher(session_factory=session_factory)
    fake_bot = FakeBot()
    banner = "\U0001f464 Viewing as Zakhar Zhukovsky · /unas to return"

    await _feed(dp, fake_bot, 777, "/as 30009999", 1)
    # Entering is not itself impersonated, so it gets no banner -- the
    # confirmation already says who you have become.
    assert banner not in [m.text for m in fake_bot.sent]

    await _feed(dp, fake_bot, 777, "/me", 2)
    await _feed(dp, fake_bot, 777, "/me", 3)
    texts = [m.text for m in fake_bot.sent]
    assert texts.count(banner) == 2
    # It comes first, so the answer below it is already labelled. Match the
    # profile by a line only it has: the banner and the /as confirmation both
    # carry the student's name.
    assert texts.index(banner) < texts.index(
        next(t for t in texts if "Role: Student" in t))

    await _feed(dp, fake_bot, 777, "/unas", 4)
    await _feed(dp, fake_bot, 777, "/me", 5)
    assert [m.text for m in fake_bot.sent].count(banner) == 2  # no more
