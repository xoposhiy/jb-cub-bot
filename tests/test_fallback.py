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
        self.logged: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None

    async def send_message(self, chat_id, text, **kwargs):
        self.logged.append(SimpleNamespace(chat_id=chat_id, text=text))
        return None


LOG_CHAT = "-1009999"


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


# --- what reaches the log chat ------------------------------------------------

async def test_an_unmatched_query_is_logged_with_the_answer_it_got():
    dp = build_dispatcher(_factory(), bootstrap_ids=set(), log_chat_id=LOG_CHAT)
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, text="Иванов Пётр"), dispatcher=dp)
    assert len(bot.logged) == 1
    entry = bot.logged[0]
    assert entry.chat_id == LOG_CHAT
    assert "Иванов Пётр" in entry.text
    assert "No one found." in entry.text
    assert "777" in entry.text  # who asked


async def test_a_photo_is_logged_by_its_content_type():
    dp = build_dispatcher(_factory(), bootstrap_ids=set(), log_chat_id=LOG_CHAT)
    bot = FakeBot()
    photo = [PhotoSize(file_id="f", file_unique_id="u", width=1, height=1)]
    await dp.feed_update(bot, _update(bot, photo=photo), dispatcher=dp)
    assert len(bot.logged) == 1
    # Lowercase: `content_type` is a ContentType enum, and interpolating it
    # directly would read "ContentType.PHOTO".
    assert "«photo»" in bot.logged[0].text


async def test_an_unknown_command_is_not_logged():
    # The bot answered correctly -- that is not a gap in what it can do.
    dp = build_dispatcher(_factory(), bootstrap_ids=set(), log_chat_id=LOG_CHAT)
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, text="/nosuchthing"), dispatcher=dp)
    assert bot.logged == []


async def test_a_query_that_found_someone_is_not_logged():
    dp = build_dispatcher(_factory(), bootstrap_ids=set(), log_chat_id=LOG_CHAT)
    bot = FakeBot()
    await dp.feed_update(bot, _update(bot, text="Ivan"), dispatcher=dp)
    assert bot.logged == []
