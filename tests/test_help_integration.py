"""End-to-end /help through a real dispatcher: admin vs student vs unlinked.

`help` is the first migrated feature, so this file is also the proof that the
legacy bridge (`core/legacy.py`'s `_declare`) works: `features/help` reads the
contract registry and nothing else, and three of the four features here are
still legacy. Every heading and every line below a `📒`, `🕵️` or `📚` comes
from a `Manifest` republished as a real declaration -- which is what makes
tasks 8 and 10 judgeable as behaviour-preserving.
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


def _with_admin():
    f = _factory()
    s = f()
    s.add(User(last_name="A", first_name="Anna", telegram_id=777, role=Role.ADMIN))
    s.commit(); s.close()
    return f


def _with_student():
    f = _factory()
    s = f()
    s.add(User(last_name="Z", first_name="Zed", matriculation="30001",
               telegram_id=222, role=Role.STUDENT, primary_cohort="c"))
    s.commit(); s.close()
    return f


async def test_admin_help_keeps_an_elevated_line_under_its_own_heading():
    out = await _run_help(_with_admin(), 777)
    assert "/sync" in out
    assert "/as" in out
    # `/sync` is directory's and `/as` is impersonate's, so they render under
    # their own headings instead of pooling into a trailing "🔐 Admin" block.
    assert "🔐 Admin" not in out
    blocks = out.split("\n\n")
    directory_block = next(b for b in blocks if b.startswith("📒 Directory"))
    impersonate_block = next(b for b in blocks if b.startswith("🕵️ Impersonate"))
    assert "/sync" in directory_block
    assert "/as" in impersonate_block


async def test_student_help_hides_admin_section():
    out = await _run_help(_with_student(), 222)
    assert "/me" in out
    assert "/sync" not in out


async def test_unlinked_help_shows_notice():
    f = _factory()
    out = await _run_help(f, 999)  # no user row for this telegram id
    assert "You're not linked yet — ask a program admin for a one-time link." in out


# --- the bridge: a legacy feature reads like a migrated one -------------------

async def test_an_admin_sees_every_legacy_feature_bridged_into_the_registry():
    out = await _run_help(_with_admin(), 777)

    assert "❓ Help — Commands you can use." in out
    assert "  /help — List the commands you can use." in out
    assert "📒 Directory — Find classmates and manage your own profile." in out
    assert "  /me — Show your own profile." in out
    assert "  /sync — Re-sync roster from Google Sheets. (admin)" in out
    # `/cancel` is the core's since task 10, listed under the feature whose
    # dialog it ends and worded by the core rather than by `directory`.
    assert "  /cancel — Cancel what you are in the middle of." in out
    assert "  💬 just type a name — search people" in out
    assert ("🕵️ Impersonate — Admin: see the bot as a given user "
            "(/as <ref>, /unas to return).") in out
    assert "  /as <ref> — See the bot as another user, until /unas. (admin)" in out
    # `/unas` is deliberately absent from the manifest, so it stays absent.
    assert "/unas —" not in out
    assert "📚 Kb — Ask the program's knowledge base a question." in out
    assert "  /ask [question] — Ask the knowledge base a question." in out
    assert "  /kb_reload — Re-download the knowledge base now. (admin)" in out
    assert "  💬 ask the knowledge base a question" in out
    # The shim's own chain slot is not a feature anybody can use.
    assert "Legacy" not in out


async def test_a_student_sees_the_legacy_lines_their_role_allows():
    out = await _run_help(_with_student(), 222)

    assert "📒 Directory — Find classmates and manage your own profile." in out
    assert "  /me — Show your own profile." in out
    assert "  💬 just type a name — search people" in out
    assert "📚 Kb — Ask the program's knowledge base a question." in out
    assert "  /ask [question] — Ask the knowledge base a question." in out
    assert "  💬 ask the knowledge base a question" in out
    assert "/kb_reload" not in out
    # Every line impersonate declares is admin-only, so its heading is absent
    # for a student -- a heading over no lines says nothing twice.
    assert "🕵️" not in out
    assert "/as <ref>" not in out  # not bare "/as": "/ask" contains it


async def test_an_unlinked_caller_sees_the_public_commands_and_the_notice():
    """The one path the middleware stack lets through with `principal=None`.

    Public commands and nothing else. `/help` filters on exactly the guard that
    would refuse you, so what a stranger reads here is what actually works for
    them -- no `💬` line, because the chain is for people the bot knows and
    every chain handler is filtered out for a stranger. Listing the name search
    would advertise the one thing that answers them "you are not linked".
    """
    out = await _run_help(_factory(), 999)

    assert out == (
        "📒 Directory — Find classmates and manage your own profile.\n"
        "  /start — Start / link your account.\n"
        "\n"
        "❓ Help — Commands you can use.\n"
        "  /help — List the commands you can use.\n"
        "\n"
        "You're not linked yet — ask a program admin for a one-time link."
    )


async def test_the_features_are_listed_in_discovery_order():
    """Alphabetical by package name, which is what `pkgutil.iter_modules`
    yields -- and what phases C to E broke while a migrated feature claimed its
    slot during `load_features` and a bridged one only during `adopt`. With
    `directory` migrated, `kb` is the last one left and it also sorts last, so
    the order is right again before the bridge is deleted."""
    out = await _run_help(_with_admin(), 777)

    headings = [line for line in out.splitlines() if not line.startswith("  ")
                and line]
    assert [heading.split(" — ")[0] for heading in headings] == [
        "📒 Directory", "❓ Help", "🕵️ Impersonate", "📚 Kb",
    ]
