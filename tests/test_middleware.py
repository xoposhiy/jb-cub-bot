from datetime import datetime, timezone
from types import SimpleNamespace

from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core import impersonation
from jbcub_bot.core.db import Base
from jbcub_bot.core.middleware import (
    GROUP_NOTICE, HasRole, PrincipalMiddleware, role_rank,
)
from jbcub_bot.core.models import Role, User
from jbcub_bot.main import build_dispatcher


def test_role_rank_ordering():
    assert role_rank(Role.STUDENT) < role_rank(Role.TEACHER) < role_rank(Role.ADMIN)


def test_has_role_allows_equal_or_higher():
    guard = HasRole(Role.ADMIN)
    assert guard(User(role=Role.ADMIN)) is True
    assert guard(User(role=Role.STUDENT)) is False


def test_has_role_none_principal_denied():
    assert HasRole(Role.STUDENT)(None) is False


async def test_middleware_injects_principal(session):
    session.add(User(last_name="Ivan", telegram_id=777, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["tid"] = data["principal"].telegram_id

    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="ivan"))
    await mw(handler, event, {})
    assert captured["tid"] == 777


async def test_middleware_bootstrap_admin(session):
    mw = PrincipalMiddleware(session_factory=lambda: session,
                             bootstrap_ids={4242})
    captured = {}

    async def handler(event, data):
        captured["principal"] = data["principal"]

    event = SimpleNamespace(from_user=SimpleNamespace(id=4242, username="boss"))
    await mw(handler, event, {})
    assert captured["principal"].role is Role.ADMIN


async def test_middleware_impersonation_swaps_for_admin(session):
    from jbcub_bot.core.models import User
    session.add(User(last_name="Admin", telegram_id=777, role=Role.ADMIN))
    session.add(User(last_name="Stud", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_matriculation"] = data["principal"].matriculation
        captured["impersonator_tid"] = data.get("impersonator").telegram_id

    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="a"))
    await mw(handler, event, {"impersonate_ref": "30000001"})
    assert captured["principal_matriculation"] == "30000001"
    assert captured["impersonator_tid"] == 777


async def test_middleware_impersonation_ignored_for_non_admin(session):
    from jbcub_bot.core.models import User
    session.add(User(last_name="Stud", telegram_id=777, role=Role.STUDENT))
    session.add(User(last_name="Other", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_tid"] = data["principal"].telegram_id

    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="s"))
    await mw(handler, event, {"impersonate_ref": "30000001"})
    assert captured["principal_tid"] == 777  # not swapped


async def test_middleware_swaps_the_principal_for_an_admin_in_the_mode(session):
    from jbcub_bot.core.models import User
    session.add(User(last_name="Admin", telegram_id=777, role=Role.ADMIN))
    session.add(User(last_name="Stud", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()
    mw = PrincipalMiddleware(lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_tid"] = data["principal"].telegram_id
        captured["impersonator_tid"] = data["impersonator"].telegram_id

    impersonation.begin(777, "30000001")
    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="a"))
    await mw(handler, event, {})

    assert captured == {"principal_tid": 111, "impersonator_tid": 777}


async def test_a_students_own_mode_entry_is_ignored(session):
    # Belt and braces: only /as writes the map and only an admin may run it,
    # but the swap must not depend on that being true.
    from jbcub_bot.core.models import User
    session.add(User(last_name="Stud", telegram_id=777, role=Role.STUDENT))
    session.add(User(last_name="Other", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()
    mw = PrincipalMiddleware(lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_tid"] = data["principal"].telegram_id

    impersonation.begin(777, "30000001")
    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="s"))
    await mw(handler, event, {})

    assert captured["principal_tid"] == 777  # not swapped


async def test_middleware_reads_impersonation_from_admin_callback(session):
    session.add(User(last_name="Admin", telegram_id=777, role=Role.ADMIN))
    session.add(User(last_name="Stud", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_tid"] = data["principal"].telegram_id
        captured["ref"] = data["impersonate_ref"]

    event = CallbackQuery(
        id="cb-1",
        from_user=TgUser(id=777, is_bot=False, first_name="Admin", username="a"),
        chat_instance="chat",
        data=impersonation.callback_data("dir:privacy", "30000001"),
    )
    await mw(handler, event, {})
    assert captured == {"principal_tid": 111, "ref": "30000001"}


async def test_non_admin_cannot_forge_an_impersonated_callback(session):
    session.add(User(last_name="Stud", telegram_id=777, role=Role.STUDENT))
    session.add(User(last_name="Other", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_tid"] = data["principal"].telegram_id
        captured["ref"] = data.get("impersonate_ref")

    event = CallbackQuery(
        id="cb-1",
        from_user=TgUser(id=777, is_bot=False, first_name="Student"),
        chat_instance="chat",
        data=impersonation.callback_data("dir:privacy", "30000001"),
    )
    await mw(handler, event, {})
    assert captured == {"principal_tid": 777, "ref": None}


# --- the bot serves private chats only ----------------------------------


class FakeBot:
    """Minimal stand-in for aiogram.Bot: records outgoing methods, so a test
    can see whether a refusal actually reached .answer() rather than just
    trusting that it would have.
    """

    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _message(fake_bot, telegram_id: int, text: str, chat_type: str) -> Message:
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type=chat_type),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)


def _callback(fake_bot, telegram_id: int, chat_type: str) -> CallbackQuery:
    shown = Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type=chat_type),
        from_user=TgUser(id=1, is_bot=True, first_name="bot"),
        text="whatever was on screen",
    ).as_(fake_bot)
    return CallbackQuery(
        id="cb-1",
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        chat_instance="chat-instance",
        data="dir:privacy",
        message=shown,
    ).as_(fake_bot)


async def test_group_command_is_refused_and_the_handler_never_runs(session):
    mw = PrincipalMiddleware(session_factory=lambda: session)
    fake_bot = FakeBot()
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    await mw(handler, _message(fake_bot, 777, "/me", "group"), {})

    assert called is False
    texts = [m.text for m in fake_bot.sent if isinstance(m, SendMessage)]
    assert texts == [GROUP_NOTICE]


async def test_group_plain_text_is_silently_dropped(session):
    mw = PrincipalMiddleware(session_factory=lambda: session)
    fake_bot = FakeBot()
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    await mw(handler, _message(fake_bot, 777, "hello there", "group"), {})

    assert called is False
    assert fake_bot.sent == []  # a busy group must not get a reply per line


async def test_group_callback_is_refused_as_an_alert(session):
    mw = PrincipalMiddleware(session_factory=lambda: session)
    fake_bot = FakeBot()
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    await mw(handler, _callback(fake_bot, 777, "supergroup"), {})

    assert called is False
    alerts = [m for m in fake_bot.sent if isinstance(m, AnswerCallbackQuery)]
    assert len(alerts) == 1
    assert alerts[0].show_alert is True
    assert alerts[0].text == GROUP_NOTICE


async def test_private_command_still_runs_the_handler(session):
    # The regression that matters: every entry point goes through here.
    mw = PrincipalMiddleware(session_factory=lambda: session)
    fake_bot = FakeBot()
    ran = False

    async def handler(event, data):
        nonlocal ran
        ran = True

    await mw(handler, _message(fake_bot, 777, "/me", "private"), {})

    assert ran is True


async def test_group_guard_runs_before_the_identity_lookup(session, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("identity lookup ran despite the group guard")

    monkeypatch.setattr("jbcub_bot.core.identity.resolve", boom)
    mw = PrincipalMiddleware(session_factory=lambda: session)
    fake_bot = FakeBot()

    async def handler(event, data):
        raise AssertionError("handler ran despite the group guard")

    # telegram_id 999999 has no row at all -- if the guard needed a lookup
    # to refuse, this would blow up before reaching the assertion above.
    await mw(handler, _message(fake_bot, 999999, "/me", "group"), {})


async def test_group_command_as_a_photo_caption_is_still_refused(session):
    # aiogram's Command filter matches message.text or message.caption, so a
    # command sent as a photo caption is just as deliberate an address as
    # typing it -- reading only .text would mistake it for background chatter.
    mw = PrincipalMiddleware(session_factory=lambda: session)
    fake_bot = FakeBot()
    called = False

    async def handler(event, data):
        nonlocal called
        called = True

    event = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=777, type="group"),
        from_user=TgUser(id=777, is_bot=False, first_name="tg"),
        caption="/cohort 2024",
    ).as_(fake_bot)

    await mw(handler, event, {})

    assert called is False
    texts = [m.text for m in fake_bot.sent if isinstance(m, SendMessage)]
    assert texts == [GROUP_NOTICE]


# --- the same guard, through a real dispatcher --------------------------
#
# The tests above call mw() directly with a stub handler, which proves the
# guard itself decides right but not what aiogram does with that decision.
# Returning None from an outer middleware is supposed to mean "handled" --
# but if that ever changed (e.g. to some UNHANDLED sentinel meant to "let
# other routers decide"), main.py's catch-all fallback router would answer
# every group message right alongside the guard, which is the exact spam
# this feature exists to prevent, and the mw()-only tests above would never
# notice. test_departed_access.py sets the standard for this: route through
# build_dispatcher() and a real Update, the same as production.


def _dispatcher_session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _update(fake_bot, telegram_id: int, text: str, chat_type: str) -> Update:
    msg = _message(fake_bot, telegram_id, text, chat_type)
    return Update(update_id=1, message=msg).as_(fake_bot)


async def test_group_plain_text_gets_no_send_through_a_real_dispatcher():
    dp = build_dispatcher(_dispatcher_session_factory())
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _update(fake_bot, 777, "hello there", "group"))

    # Not just "the guard's handler wasn't called" -- nothing else in the
    # dispatcher (in particular the fallback router) answered either.
    assert fake_bot.sent == []


async def test_group_command_gets_exactly_one_send_through_a_real_dispatcher():
    dp = build_dispatcher(_dispatcher_session_factory())
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _update(fake_bot, 777, "/me", "group"))

    # Exactly one -- the guard's refusal, not the guard's refusal *plus* the
    # fallback router's "I don't know /me" on top of it.
    texts = [m.text for m in fake_bot.sent if isinstance(m, SendMessage)]
    assert texts == [GROUP_NOTICE]
