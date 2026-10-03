"""End-to-end coverage for name search: real dispatcher, real ranking.

Scoring itself is covered in test_matching.py. What needs proving here is the
wiring -- that a clear winner opens a profile, that a tie opens a list, and
that text which is not a name gets the fallback instead of a wrong person.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.pipeline import NOTHING_MATCHED
from jbcub_bot.features.kb import handlers as kb
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


def _labels(method) -> list[str]:
    return [button.text for row in method.reply_markup.inline_keyboard
            for button in row]


async def test_a_tie_lists_everyone_close_as_buttons():
    fake_bot = await _say("Михаил")
    assert fake_bot.sent[0].text == "Several people match:"
    assert _labels(fake_bot.sent[0]) == [
        "Mikhail Efremenko · 2024", "Mikhail Redko · 2024",
    ]


async def test_text_that_is_not_a_name_gets_the_fallback():
    """With no agent runtime configured the slot behind the search declines,
    so this is the core's last word rather than anything the agent said."""
    fake_bot = await _say("как дела")
    assert fake_bot.sent[0].text == NOTHING_MATCHED


async def test_unmatched_text_offers_no_button_to_press():
    """It used to answer "I didn't find anyone by that name. Ask AI instead?"
    with a button, and the tap opened a mode in which the next name typed was
    read as a question. Both halves of that are gone: the agent is simply next
    in the chain."""
    fake_bot = await _say("как дела")

    assert all(getattr(m, "reply_markup", None) is None for m in fake_bot.sent)
    assert not any("Ask AI" in (getattr(m, "text", "") or "")
                   for m in fake_bot.sent)


async def test_a_found_name_is_never_put_to_the_agent(monkeypatch):
    """The search has first refusal on every free-text message, and taking one
    ends the chain -- so the agent behind it is never asked, configured or
    not."""
    asked = []

    async def never(*args, **kwargs):
        asked.append(args)

    monkeypatch.setattr(kb, "ask", never)
    kb.set_runtime(SimpleNamespace(agent=object(), store=None,
                                   repo="", log_chat_id="", admin_ids=(),
                                   rate_limit=100, rate_window_seconds=3600))

    fake_bot = await _say("Ярослав")

    assert "Iaroslav Belozerov" in fake_bot.sent[0].text
    assert asked == []


def _callback_update(fake_bot, data: str, telegram_id=222, update_id=2) -> Update:
    msg = Message(
        message_id=1, date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"), text="Several people match:",
    ).as_(fake_bot)
    callback = CallbackQuery(
        id="1", chat_instance="1", data=data, message=msg,
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
    ).as_(fake_bot)
    return Update(update_id=update_id, callback_query=callback).as_(fake_bot)


def _seed_a_returned_person(factory):
    """One person twice: the old row departed, the new one active."""
    setup = factory()
    setup.add_all([
        User(last_name="Egorov", first_name="Egor", telegram_id=333,
             role=Role.ADMIN, matriculation="30009999"),
        User(last_name="Pliasovskikh", first_name="Milan", primary_cohort="2026",
             matriculation="not in CN", departed_at="2026-08-28"),
        User(last_name="Pliasovskikh", first_name="Milan", primary_cohort="2026",
             matriculation="30010811"),
    ])
    setup.commit()
    setup.close()


async def test_an_admin_gets_the_active_row_with_the_departed_one_beneath():
    factory = _session_factory()
    _seed(factory)
    _seed_a_returned_person(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, "Milan", 333),
                         dispatcher=dp)

    [profile] = fake_bot.sent
    assert "Departed" not in profile.text
    assert _labels(profile)[-1] ==         "Milan Pliasovskikh · 2026 · departed 2026-08-28"


async def test_a_shortlist_button_opens_that_profile():
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()
    await dp.feed_update(fake_bot, _message_update(fake_bot, "Михаил"),
                         dispatcher=dp)
    data = fake_bot.sent[0].reply_markup.inline_keyboard[1][0].callback_data

    await dp.feed_update(fake_bot, _callback_update(fake_bot, data),
                         dispatcher=dp)

    assert "Mikhail Redko" in fake_bot.sent[1].text


async def test_a_student_cannot_open_a_departed_row_by_its_button():
    factory = _session_factory()
    _seed(factory)
    _seed_a_returned_person(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()
    setup = factory()
    departed = setup.query(User).filter_by(matriculation="not in CN").one().id
    setup.close()

    await dp.feed_update(
        fake_bot, _callback_update(fake_bot, f"dir:person:{departed}"),
        dispatcher=dp)

    [answer] = fake_bot.sent
    assert (answer.text, answer.show_alert) == ("Not found.", True)
