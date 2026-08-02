"""End-to-end wiring: real dispatcher, real FSM, a fake runtime.

The agent itself is covered in test_kb_agent.py. What needs proving here is
that a teacher's text reaches it, a student's does not, and that the session
opens, counts and closes.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core.db import Base
from jbcub_bot.core.kb_snapshot import Note, Snapshot, Source
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.kb import agent as kb_agent
from jbcub_bot.features.kb import handlers as kb
from jbcub_bot.main import build_dispatcher

TEACHER_ID = 555
STUDENT_ID = 222


class FakeBot:
    def __init__(self, reject_html=False):
        self.id = 1
        self.sent: list = []
        self.documents: list = []
        self.reject_html = reject_html

    async def __call__(self, method, request_timeout=None):
        if self.reject_html and getattr(method, "parse_mode", None) == "HTML":
            raise TelegramBadRequest(method=method,
                                     message="can't parse entities")
        self.sent.append(method)
        return None

    async def send_message(self, chat_id, text):
        self.sent.append(SendMessage(chat_id=chat_id, text=text))

    async def send_document(self, chat_id, document, caption=None):
        self.documents.append(document)
        return SimpleNamespace(document=SimpleNamespace(file_id="FILE-1"))


class FakeStore:
    def __init__(self):
        self.snapshot = Snapshot(sha="abc123", repo="xoposhiy/cub-kb", notes={
            "kb/policies/exams.md": Note(
                path="kb/policies/exams.md",
                text="Retakes are allowed once.\n", title="Exam rules",
                source=Source(
                    file="sources/policies/bachelor_policies_v8.pdf",
                    document="Policies for Bachelor Studies", version="8",
                    sections=("III.4 Grading",), pdf_pages="18-20")),
        })
        self.forced = 0

    async def get(self, *, force: bool = False):
        if force:
            self.forced += 1
        return self.snapshot


def _install_runtime(monkeypatch,
                     answer="Retakes once.\nSources: kb/policies/exams.md"):
    store = FakeStore()
    asked: list[str] = []

    async def fake_ask(agent, snapshot, question, history):
        asked.append(question)
        return (answer, history + [{"role": "user", "content": question}],
                kb_agent.AskStats(steps=2, tool_calls=1, notes_read=1,
                                  input_tokens=1200, output_tokens=310))

    # handlers.py imported `ask` by name, so that binding is the one in play.
    monkeypatch.setattr(kb, "ask", fake_ask)
    kb.set_runtime(kb_agent.KbRuntime(agent=object(), store=store,
                                      repo="xoposhiy/cub-kb"))
    return store, asked


def _session_factory():
    engine = create_engine("sqlite://",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(factory):
    setup = factory()
    setup.add(User(last_name="Teacher", first_name="Tanya",
                   telegram_id=TEACHER_ID, role=Role.TEACHER))
    setup.add(User(last_name="Ivanov", first_name="Ivan",
                   matriculation="30001111", telegram_id=STUDENT_ID,
                   role=Role.STUDENT, primary_cohort="2024"))
    setup.commit()
    setup.close()


def _message(fake_bot, telegram_id: int, text: str, update_id=1) -> Update:
    msg = Message(
        message_id=100 + update_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=update_id, message=msg).as_(fake_bot)


def _callback(fake_bot, telegram_id: int, data: str, update_id=2) -> Update:
    chat = Chat(id=telegram_id, type="private")
    shown = Message(message_id=7, date=datetime.now(timezone.utc), chat=chat,
                    from_user=TgUser(id=1, is_bot=True, first_name="bot"),
                    text="offer").as_(fake_bot)
    cb = CallbackQuery(id=f"cb-{update_id}",
                       from_user=TgUser(id=telegram_id, is_bot=False,
                                        first_name="tg"),
                       chat_instance="ci", data=data, message=shown).as_(fake_bot)
    return Update(update_id=update_id, callback_query=cb).as_(fake_bot)


def _texts(fake_bot) -> list[str]:
    return [getattr(m, "text", "") or "" for m in fake_bot.sent]


def _setup(monkeypatch, **kw):
    factory = _session_factory()
    _seed(factory)
    store, asked = _install_runtime(monkeypatch, **kw)
    return build_dispatcher(session_factory=factory), FakeBot(), store, asked


async def test_a_teacher_ask_opens_the_session(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "how many retakes?",
                                       update_id=2), dispatcher=dp)

    assert asked == ["how many retakes?"]
    assert "Policies for Bachelor Studies" in _texts(bot)[-1]


async def test_the_answer_cites_the_document_section_and_pages(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    answer = _texts(bot)[-1]
    assert "Policies for Bachelor Studies v8" in answer
    assert "pp. 18–20" in answer
    assert "kb/policies/exams.md" not in answer, "no raw paths in the answer"
    assert "2 steps · 1 tool call · 1 note" in answer


async def test_a_student_is_refused_and_still_gets_the_name_search(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, STUDENT_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, STUDENT_ID, "zzzz qqqq",
                                       update_id=2), dispatcher=dp)

    assert asked == []
    assert "No one found." in _texts(bot)


async def test_unmatched_teacher_text_gets_the_offer_button(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "zzzz qqqq"),
                         dispatcher=dp)

    assert asked == [], "tokens are spent only after the tap"
    assert any(getattr(m, "reply_markup", None) is not None for m in bot.sent)


async def test_tapping_the_offer_opens_the_session(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.START_CALLBACK),
                         dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=3),
                         dispatcher=dp)

    assert asked == ["retakes?"]


async def test_exit_closes_the_session(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.EXIT_CALLBACK,
                                        update_id=2), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=3),
                         dispatcher=dp)

    assert asked == [], "text after Exit is no longer the agent's"


async def test_the_twelfth_answer_closes_the_session(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    for i in range(kb.MAX_QUESTIONS + 2):
        await dp.feed_update(bot, _message(bot, TEACHER_ID, f"q{i}",
                                           update_id=10 + i), dispatcher=dp)

    assert len(asked) == kb.MAX_QUESTIONS
    assert any("/ask" in t for t in _texts(bot)[-4:])


async def test_a_stale_session_starts_fresh(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "first", update_id=2),
                         dispatcher=dp)

    # The next message arrives after the idle cut. Capture the real clock
    # first: `lambda: kb.now() + ...` would call the patched one and recurse.
    real_now = kb.now
    monkeypatch.setattr(kb, "now", lambda: real_now() + kb.IDLE_SECONDS + 1)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "second", update_id=3),
                         dispatcher=dp)

    assert asked == ["first"], "a stale session does not take the next message"


async def test_cancel_belongs_to_directory_and_leaves_the_session_open(
        monkeypatch):
    """The deviation from the spec, pinned down.

    `directory.edit` owns /cancel and is loaded first, so the KB feature cannot
    claim that name. What it can insist on is that directory's handler no longer
    clears a state that is not its own.
    """
    dp, bot, _, asked = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/cancel", update_id=2),
                         dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=3),
                         dispatcher=dp)

    assert "Nothing to cancel." in _texts(bot)
    assert asked == ["retakes?"], "/cancel must not end a knowledge base session"


async def test_kb_reload_is_admin_only(monkeypatch):
    dp, bot, store, _ = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/kb_reload"),
                         dispatcher=dp)

    assert store.forced == 0
    assert "Admins only." in _texts(bot)


async def test_the_source_pdf_is_attached_once_per_session(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "a", update_id=2),
                         dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "b", update_id=3),
                         dispatcher=dp)

    assert len(bot.documents) == 1, "the second answer references, not re-sends"


async def test_a_fresh_session_gets_the_pdf_again(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "a", update_id=2),
                         dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask", update_id=3),
                         dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "b", update_id=4),
                         dispatcher=dp)

    assert len(bot.documents) == 2


async def test_an_answer_citing_nothing_attaches_nothing(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch,
                           answer="The base does not cover this.")
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "a", update_id=2),
                         dispatcher=dp)

    assert bot.documents == []


async def test_a_rejected_html_message_is_resent_as_plain_text(monkeypatch):
    factory = _session_factory()
    _seed(factory)
    _install_runtime(monkeypatch, answer="<b>Retakes</b> once.")
    dp, bot = build_dispatcher(session_factory=factory), FakeBot()

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    bot.reject_html = True
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "a", update_id=2),
                         dispatcher=dp)

    answer = _texts(bot)[-1]
    assert "Retakes once." in answer
    assert "<b>" not in answer, "the fallback carries the words, not the markup"


async def test_ask_without_a_configured_endpoint_says_so(monkeypatch):
    factory = _session_factory()
    _seed(factory)
    kb.set_runtime(None)
    dp, bot = build_dispatcher(session_factory=factory), FakeBot()

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    assert "not configured" in _texts(bot)[-1]
