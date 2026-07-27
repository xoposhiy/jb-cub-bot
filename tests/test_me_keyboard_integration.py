"""/me offers the privacy screen -- except when an admin is impersonating.

Under /as the profile belongs to the target but a later button press arrives
without the impersonation ref, so the callback would edit the *admin's* own
settings while the screen shows a student. The button must not be there.
"""

from datetime import datetime, timezone

from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.render import EDIT_CALLBACK, PRIVACY_CALLBACK
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


def _seed(factory):
    setup = factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT, primary_cohort="cohort-x"))
    setup.commit()
    setup.close()


def _message_update(fake_bot, telegram_id: int, text: str) -> Update:
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=1, message=msg).as_(fake_bot)


def _callbacks(method):
    markup = getattr(method, "reply_markup", None)
    if markup is None:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def test_me_offers_the_privacy_screen():
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/me"),
                         dispatcher=dp)

    assert PRIVACY_CALLBACK in _callbacks(fake_bot.sent[0])


async def test_me_under_impersonation_has_no_privacy_button():
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 /me"),
                         dispatcher=dp)

    assert "Zakhar Zhukovsky" in fake_bot.sent[1].text  # the target's profile
    assert PRIVACY_CALLBACK not in _callbacks(fake_bot.sent[1])


async def test_me_offers_the_edit_screen():
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/me"),
                         dispatcher=dp)

    assert EDIT_CALLBACK in _callbacks(fake_bot.sent[0])


async def test_me_under_impersonation_has_no_edit_button():
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 /me"),
                         dispatcher=dp)

    assert EDIT_CALLBACK not in _callbacks(fake_bot.sent[1])
