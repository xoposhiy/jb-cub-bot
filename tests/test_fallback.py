"""Every message gets an answer — including the ones nothing understood."""
from datetime import datetime, timezone
from types import SimpleNamespace

from aiogram.types import Chat, Message, PhotoSize, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(SimpleNamespace(text=text))
        return None


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine)
    s = maker()
    s.add(User(first_name="I", last_name="Ivan", matriculation="30000001",
               telegram_id=777, role=Role.STUDENT))
    s.commit()
    s.close()
    return maker


def _update(bot, **message_kwargs):
    message = Message(
        message_id=1, date=datetime.now(timezone.utc),
        chat=Chat(id=777, type="private"),
        from_user=TgUser(id=777, is_bot=False, first_name="t"),
        **message_kwargs,
    ).as_(bot)
    return Update(update_id=9, message=message).as_(bot)


def _replies(bot) -> str:
    return "\n".join(getattr(m, "text", "") or "" for m in bot.sent)


async def test_unknown_command_says_so(monkeypatch):
    dp = build_dispatcher(_factory(), bootstrap_ids=set())
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, text="/nosuchthing"), dispatcher=dp)
    replies = _replies(bot)
    assert "/nosuchthing" in replies
    assert "/help" in replies


async def test_a_photo_gets_an_answer_too():
    dp = build_dispatcher(_factory(), bootstrap_ids=set())
    bot = FakeBot()
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    await dp.feed_update(bot, _update(bot, photo=photo), dispatcher=dp)
    assert "/help" in _replies(bot), "a non-text message went unanswered"


async def test_plain_text_still_reaches_the_search_intent():
    """The catch-all must sit behind everything, not in front of it."""
    dp = build_dispatcher(_factory(), bootstrap_ids=set())
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, text="Ivan"), dispatcher=dp)
    replies = _replies(bot)
    assert "Ivan" in replies
    assert "/help" not in replies


async def test_a_known_command_is_not_second_guessed():
    dp = build_dispatcher(_factory(), bootstrap_ids=set())
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, text="/me"), dispatcher=dp)
    replies = _replies(bot)
    assert "I don't know" not in replies
