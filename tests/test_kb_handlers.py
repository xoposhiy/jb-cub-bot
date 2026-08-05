"""End-to-end wiring: real dispatcher, real FSM, a fake runtime.

The agent itself is covered in test_kb_agent.py. What needs proving here is
that any recognized user's text reaches it, that an unlinked visitor's does
not, and that the session opens, counts and closes.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import (
    CallbackQuery,
    Chat,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from aiogram.types import User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core.db import Base
from jbcub_bot.core.kb_snapshot import Note, Snapshot, Source
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.kb import agent as kb_agent
from jbcub_bot.features.kb import handlers as kb
from jbcub_bot.features.kb import tools
from jbcub_bot.main import build_dispatcher

TEACHER_ID = 555
STUDENT_ID = 222
ADMIN_ID = 999


class FakeBot:
    """Enough of a Bot to answer with real message ids.

    The ids matter: the Exit button is moved from message to message, so a
    stub that answers None to every send would make that untestable. `events`
    is the chronological record of every markup this chat has been shown.
    """

    def __init__(self, reject_html=False):
        self.id = 1
        self.sent: list = []
        self.documents: list = []
        self.events: list[tuple[str, int, object]] = []
        self.reject_html = reject_html
        self._last_id = 500

    def _reply(self, chat_id, text="", message_id=None) -> Message:
        # An edit answers under the id it was given; a send mints a new one --
        # that distinction is what lets `_reveal_answer`'s edit be told apart
        # from a message that only looks similar.
        if message_id is None:
            self._last_id += 1
            message_id = self._last_id
        return Message(message_id=message_id,
                       date=datetime.now(timezone.utc),
                       chat=Chat(id=chat_id or 0, type="private"),
                       from_user=TgUser(id=self.id, is_bot=True,
                                        first_name="bot"),
                       text=text).as_(self)

    async def __call__(self, method, request_timeout=None):
        if self.reject_html and getattr(method, "parse_mode", None) == "HTML":
            raise TelegramBadRequest(method=method,
                                     message="can't parse entities")
        self.sent.append(method)
        edited_id = getattr(method, "message_id", None)
        sent = self._reply(getattr(method, "chat_id", 0),
                           getattr(method, "text", "") or "",
                           message_id=edited_id)
        kind = "edit" if edited_id else "send"
        self.events.append((kind, sent.message_id,
                            getattr(method, "reply_markup", None)))
        return sent

    async def send_message(self, chat_id, text, entities=None):
        self.sent.append(SendMessage(chat_id=chat_id, text=text))

    async def edit_message_text(self, chat_id, message_id, text,
                                parse_mode=None):
        if self.reject_html and parse_mode == "HTML":
            raise TelegramBadRequest(
                method=SendMessage(chat_id=chat_id, text=text),
                message="can't parse entities")
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=text,
                                         parse_mode=parse_mode))
        sent = self._reply(chat_id, text, message_id=message_id)
        self.events.append(("edit", sent.message_id, None))
        return sent

    async def send_document(self, chat_id, document, caption=None):
        self.documents.append(document)
        sent = self._reply(chat_id)
        self.events.append(("send", sent.message_id, None))
        return SimpleNamespace(message_id=sent.message_id,
                               document=SimpleNamespace(file_id="FILE-1"))

    async def edit_message_reply_markup(self, chat_id, message_id,
                                        reply_markup=None):
        self.events.append(("edit", message_id, reply_markup))
        return None


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


_PDF = tools.SourceRef(file="sources/policies/bachelor_policies_v8.pdf",
                       caption="Policies for Bachelor Studies")
_WEB = tools.SourceRef(
    file="sources/academic-calendars/2026-2027.html",
    caption="Academic Calendar 2026/2027",
    url="https://constructor.university/student-life/academic-calendars/2026-2027")
# The agent writes its own citation now, so the stub answer carries one.
_ANSWER = ("Retakes once.\n"
           "📄 Policies for Bachelor Studies v8 — §III.4 Grading, pp. 18–20")


def _install_runtime(monkeypatch, answer=_ANSWER, pdfs=(_PDF,), complaints=(),
                     log_chat_id="", admin_ids=(), rate_limit=100,
                     rate_window_seconds=3600):
    store = FakeStore()
    asked: list[str] = []

    async def fake_ask(agent, snapshot, question, history, about=""):
        asked.append(question)
        return kb_agent.Answer(
            text=answer,
            history=history + [{"role": "user", "content": question}],
            stats=kb_agent.AskStats(
                steps=2, tool_calls=1, notes_read=1, input_tokens=1200,
                output_tokens=310,
                calls=(kb_agent.ToolCall(
                    "read_note", {"path": "kb/policies/exams.md"},
                    "1.2k chars"),)),
            sources=tuple(pdfs), complaints=tuple(complaints))

    # handlers.py imported `ask` by name, so that binding is the one in play.
    monkeypatch.setattr(kb, "ask", fake_ask)
    kb.set_runtime(kb_agent.KbRuntime(agent=object(), store=store,
                                      repo="xoposhiy/cub-kb",
                                      log_chat_id=log_chat_id,
                                      admin_ids=tuple(admin_ids),
                                      rate_limit=rate_limit,
                                      rate_window_seconds=rate_window_seconds))
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
    setup.add(User(last_name="Admin", first_name="Ada",
                   telegram_id=ADMIN_ID, role=Role.ADMIN))
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


def _is_exit(markup) -> bool:
    return isinstance(markup, InlineKeyboardMarkup) and any(
        button.callback_data in (kb.EXIT_GOOD_CALLBACK, kb.EXIT_BAD_CALLBACK)
        for row in markup.inline_keyboard for button in row)


def _exit_buttons(fake_bot) -> list[int]:
    """Which messages show an Exit button right now, replaying every event.

    A message's markup is whatever it was last set to, whether that was at
    send time or by a later edit — so the last event wins per message id.
    """
    shown: dict[int, bool] = {}
    for _, message_id, markup in fake_bot.events:
        shown[message_id] = _is_exit(markup)
    return [message_id for message_id, has in shown.items() if has]


def _is_thinking_exit(markup) -> bool:
    return isinstance(markup, InlineKeyboardMarkup) and any(
        button.callback_data == kb.EXIT_THINKING_CALLBACK
        for row in markup.inline_keyboard for button in row)


def _thinking_buttons(fake_bot) -> list[int]:
    """Which messages show the "Exit AI chat" button right now."""
    shown: dict[int, bool] = {}
    for _, message_id, markup in fake_bot.events:
        shown[message_id] = _is_thinking_exit(markup)
    return [message_id for message_id, has in shown.items() if has]


def _last_message_id(fake_bot) -> int:
    return max((message_id for _, message_id, _ in fake_bot.events), default=0)


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


async def test_the_reader_gets_the_agents_words_unedited(monkeypatch):
    """The bot no longer rewrites the answer or bolts a sources block on: the
    agent cites the document itself and this is sent as it stands."""
    dp, bot, _, _ = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    answer = _texts(bot)[-1]
    assert answer == _ANSWER
    assert "steps" not in answer, "cost is an admin's business, not a teacher's"


async def test_a_student_can_ask_too(monkeypatch):
    """Ask AI is open to every recognized user, students included."""
    dp, bot, _, asked = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, STUDENT_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, STUDENT_ID, "how many retakes?",
                                       update_id=2), dispatcher=dp)

    assert asked == ["how many retakes?"]
    assert "Policies for Bachelor Studies" in _texts(bot)[-1]


async def test_a_students_unmatched_text_gets_the_offer_too(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, STUDENT_ID, "zzzz qqqq"),
                         dispatcher=dp)

    assert asked == [], "tokens are spent only after the tap"
    assert any(getattr(m, "reply_markup", None) is not None for m in bot.sent)


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


async def test_tapping_the_offer_hides_it(monkeypatch):
    """Nothing left to tap a second time for a question already spent."""
    dp, bot, _, _ = _setup(monkeypatch)

    cb_update = _callback(bot, TEACHER_ID, kb.START_CALLBACK)
    offer_id = cb_update.callback_query.message.message_id
    await dp.feed_update(bot, cb_update, dispatcher=dp)

    cleared = [markup for kind, mid, markup in bot.events if mid == offer_id]
    assert cleared[-1] is None


async def test_the_tap_answers_the_question_that_earned_the_button(monkeypatch):
    """The whole point of the button: not to have to retype the question."""
    dp, bot, _, asked = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "how many retakes?"),
                         dispatcher=dp)
    assert asked == [], "still nothing spent before the tap"

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.START_CALLBACK),
                         dispatcher=dp)

    assert asked == ["how many retakes?"]
    assert "Policies for Bachelor Studies" in _texts(bot)[-1]


async def test_the_tap_with_a_pending_question_skips_the_greeting(monkeypatch):
    """Already said what they wanted -- repeating "ask me anything" is noise."""
    dp, bot, _, asked = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "how many retakes?"),
                         dispatcher=dp)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.START_CALLBACK),
                         dispatcher=dp)

    assert asked == ["how many retakes?"]
    assert not any("Ask me anything" in t for t in _texts(bot))


async def test_the_tap_uses_the_most_recent_unanswered_question(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "first thing"),
                         dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "second thing",
                                       update_id=2), dispatcher=dp)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.START_CALLBACK,
                                        update_id=3), dispatcher=dp)

    assert asked == ["second thing"]


async def test_a_bare_tap_with_nothing_pending_just_opens_the_session(
        monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.START_CALLBACK),
                         dispatcher=dp)

    assert asked == []
    assert any("Ask me anything" in t for t in _texts(bot))


async def test_the_agent_is_told_the_asker_role_and_cohort(monkeypatch):
    seen: list[str] = []
    factory = _session_factory()
    _seed(factory)
    store = FakeStore()

    async def fake_ask(agent, snapshot, question, history, about=""):
        seen.append(about)
        return kb_agent.Answer("ok", history, kb_agent.AskStats())

    monkeypatch.setattr(kb, "ask", fake_ask)
    kb.set_runtime(kb_agent.KbRuntime(agent=object(), store=store,
                                      repo="xoposhiy/cub-kb"))
    dp, bot = build_dispatcher(session_factory=factory), FakeBot()

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "q", update_id=2),
                         dispatcher=dp)

    assert seen == ["role: Teacher"], "a teacher has no cohort to pass on"


def test_a_students_cohort_is_what_picks_the_programme():
    student = User(last_name="I", first_name="I", telegram_id=1,
                   role=Role.STUDENT, primary_cohort="2024")

    assert kb.describe_asker(student) == "role: Student · cohort: 2024"
    assert kb.describe_asker(None) == ""


async def test_exit_closes_the_session(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.EXIT_GOOD_CALLBACK,
                                        update_id=2), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=3),
                         dispatcher=dp)

    assert asked == [], "text after Exit is no longer the agent's"
    assert kb._CLOSED in _texts(bot)


# --- what the ops chat sees ---------------------------------------------------

LOG_CHAT = "-1009999"


def _logged(fake_bot) -> list[str]:
    return [m.text for m in fake_bot.sent
            if str(getattr(m, "chat_id", "")) == LOG_CHAT]


async def test_every_question_reaches_the_ops_chat_with_its_cost(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, log_chat_id=LOG_CHAT)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "how many retakes?",
                                       update_id=2), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "and resits?",
                                       update_id=3), dispatcher=dp)

    entries = _logged(bot)
    assert len(entries) == 2, "one entry per question, not per session"
    assert "Tanya Teacher" in entries[0], "who asked"
    assert "«how many retakes?»" in entries[0], "and what they asked"
    assert "1 tool call" in entries[0] and "1.2k in / 310 out" in entries[0]
    assert "«and resits?»" in entries[1]


async def test_a_students_text_never_reaches_the_ops_chat(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, log_chat_id=LOG_CHAT)

    await dp.feed_update(bot, _message(bot, STUDENT_ID, "how many retakes?"),
                         dispatcher=dp)

    assert _logged(bot) == []


async def test_closing_a_session_logs_the_rating_not_a_cost_tally(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, log_chat_id=LOG_CHAT)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "how many retakes?",
                                       update_id=2), dispatcher=dp)
    before = len(_logged(bot))

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.EXIT_GOOD_CALLBACK,
                                        update_id=3), dispatcher=dp)

    entries = _logged(bot)
    assert len(entries) == before + 1, "one feedback entry, not a cost recap"
    assert "👍" in entries[-1]
    assert "tool call" not in entries[-1], "feedback is not the admin trace"


async def test_a_bad_rating_logs_the_thumbs_down_icon(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, log_chat_id=LOG_CHAT)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.EXIT_BAD_CALLBACK,
                                        update_id=2), dispatcher=dp)

    assert "👎" in _logged(bot)[-1]


# --- the global rate limit, configured on the runtime --------------------------

async def test_a_question_past_the_budget_is_turned_away(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch, rate_limit=1)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "first", update_id=2),
                         dispatcher=dp)
    assert asked == ["first"]

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "second", update_id=3),
                         dispatcher=dp)

    assert asked == ["first"], "the shared budget is spent, not theirs"
    assert kb._RATE_LIMITED in _texts(bot)


async def test_the_budget_frees_up_once_the_window_passes(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch, rate_limit=1,
                               rate_window_seconds=5)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "first", update_id=2),
                         dispatcher=dp)

    real_now = kb.now
    monkeypatch.setattr(kb, "now", lambda: real_now() + 6)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "second", update_id=3),
                         dispatcher=dp)

    assert asked == ["first", "second"]


async def test_a_turned_away_question_pings_the_admins(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch, rate_limit=1, log_chat_id=LOG_CHAT,
                               admin_ids=(ADMIN_ID,))
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "first", update_id=2),
                         dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "second", update_id=3),
                         dispatcher=dp)

    entries = _logged(bot)
    assert any("hourly limit" in entry for entry in entries)
    assert asked == ["first"]


async def test_a_turned_away_question_closes_the_session(monkeypatch):
    """Nothing here draws a fresh Exit button, so a session left open would
    strand the asker in it with no way out until the budget frees up."""
    dp, bot, _, asked = _setup(monkeypatch, rate_limit=1)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "first", update_id=2),
                         dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "second", update_id=3),
                         dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "third", update_id=4),
                         dispatcher=dp)

    assert asked == ["first"], "the session closed, so 'third' is not the agent's"
    assert _exit_buttons(bot) == []


# --- one Exit button, always under the newest message -------------------------

async def test_opening_a_session_draws_the_exit_button(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    assert _exit_buttons(bot) == [_last_message_id(bot)]


async def test_tapping_the_offer_also_draws_it(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.START_CALLBACK),
                         dispatcher=dp)

    assert len(_exit_buttons(bot)) == 1


async def test_the_button_moves_to_the_newest_message_after_an_answer(
        monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    greeting = _last_message_id(bot)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    assert _exit_buttons(bot) == [_last_message_id(bot)]
    assert greeting not in _exit_buttons(bot), "the old one was taken down"


async def test_the_chat_never_holds_two_exit_buttons(monkeypatch):
    """The whole point: one button, not one per message."""
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    for i in range(4):
        await dp.feed_update(bot, _message(bot, TEACHER_ID, f"q{i}",
                                           update_id=10 + i), dispatcher=dp)
        assert _exit_buttons(bot) == [_last_message_id(bot)]


async def test_the_button_lands_on_the_attachment_when_that_came_last(
        monkeypatch):
    """A source PDF is sent after the answer, so the answer is not the last
    word and must not be where the button waits."""
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    assert bot.documents, "this answer does attach a PDF"
    assert _exit_buttons(bot) == [_last_message_id(bot)]


async def test_the_button_lands_on_the_trace_for_an_admin(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, ADMIN_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, ADMIN_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    trace_at = max(mid for kind, mid, _ in bot.events if kind == "send")
    assert _exit_buttons(bot) == [trace_at]


async def test_the_last_answer_takes_the_button_down_with_the_session(
        monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    for i in range(kb.MAX_QUESTIONS):
        await dp.feed_update(bot, _message(bot, TEACHER_ID, f"q{i}",
                                           update_id=10 + i), dispatcher=dp)

    assert _exit_buttons(bot) == []


async def test_exiting_takes_the_button_down(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "q", update_id=2),
                         dispatcher=dp)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID, kb.EXIT_GOOD_CALLBACK,
                                        update_id=3), dispatcher=dp)

    assert _exit_buttons(bot) == []


# --- "Exit AI chat", the button on the thinking placeholder --------------------

async def test_the_thinking_message_offers_its_own_exit(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    thinking_sends = [m for m in bot.sent
                      if getattr(m, "text", "") == kb._THINKING]
    assert len(thinking_sends) == 1
    assert _is_thinking_exit(thinking_sends[0].reply_markup)


async def test_the_thinking_button_is_gone_once_the_answer_lands(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    assert _thinking_buttons(bot) == [], \
        "a PDF or the admin trace may have taken the rating buttons elsewhere"


async def test_exiting_while_thinking_closes_without_a_rating(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, log_chat_id=LOG_CHAT)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID,
                                        kb.EXIT_THINKING_CALLBACK,
                                        update_id=2), dispatcher=dp)

    assert kb._CLOSED in _texts(bot)
    assert _logged(bot) == [], "nothing was answered yet, so nothing to rate"


async def test_an_idle_session_takes_the_button_down(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "first", update_id=2),
                         dispatcher=dp)

    real_now = kb.now
    monkeypatch.setattr(kb, "now", lambda: real_now() + kb.IDLE_SECONDS + 1)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "second", update_id=3),
                         dispatcher=dp)

    assert _exit_buttons(bot) == []


async def test_a_fresh_ask_does_not_leave_the_old_button_behind(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "q", update_id=2),
                         dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask", update_id=3),
                         dispatcher=dp)

    assert _exit_buttons(bot) == [_last_message_id(bot)]


async def test_a_button_telegram_refuses_to_move_is_left_where_it_is(
        monkeypatch):
    """Better a button one message too high than no way out at all."""
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    greeting = _last_message_id(bot)

    async def refuse(chat_id, message_id, reply_markup=None):
        raise TelegramBadRequest(method=SendMessage(chat_id=chat_id, text="x"),
                                 message="message can't be edited")

    monkeypatch.setattr(bot, "edit_message_reply_markup", refuse)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "q", update_id=2),
                         dispatcher=dp)

    assert _exit_buttons(bot) == [greeting]


# --- the admin trace ----------------------------------------------------------

async def test_an_admin_is_shown_what_the_agent_did(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, ADMIN_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, ADMIN_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    trace = _texts(bot)[-1]
    assert "read_note kb/policies/exams.md — 1.2k chars" in trace
    assert "2 steps · 1 tool call · 1 note read · 1.2k in / 310 out" in trace


async def test_the_trace_comes_after_the_answer_it_explains(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, ADMIN_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, ADMIN_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    texts = _texts(bot)
    answer_at = next(i for i, t in enumerate(texts)
                     if "Policies for Bachelor Studies" in t)
    assert answer_at < len(texts) - 1


async def test_a_teacher_is_shown_the_answer_and_nothing_else(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "retakes?", update_id=2),
                         dispatcher=dp)

    assert not any("read_note" in t for t in _texts(bot))
    assert not any("tool call" in t for t in _texts(bot))


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


async def test_a_web_source_arrives_as_a_link_rather_than_a_file(monkeypatch):
    """The academic calendar is a web page. Its frontmatter carries the address,
    and a 100 KB scrape of that page would be no use to anybody."""
    dp, bot, _, _ = _setup(monkeypatch, pdfs=(_WEB,))
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)

    await dp.feed_update(bot, _message(bot, TEACHER_ID, "when?", update_id=2),
                         dispatcher=dp)

    assert bot.documents == [], "nothing to upload"
    posted = [t for t in _texts(bot) if _WEB.url in t]
    assert len(posted) == 1
    assert "Academic Calendar 2026/2027" in posted[0]


async def test_a_link_is_posted_once_per_session_like_a_file(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, pdfs=(_WEB,))
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "/ask"), dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "a", update_id=2),
                         dispatcher=dp)
    await dp.feed_update(bot, _message(bot, TEACHER_ID, "b", update_id=3),
                         dispatcher=dp)

    assert sum(_WEB.url in t for t in _texts(bot)) == 1


async def test_an_agent_that_named_no_source_gets_nothing_attached(monkeypatch):
    """Attachments follow the sources the agent chose, not a reading of its
    prose, so an answer that named none sends none."""
    dp, bot, _, _ = _setup(monkeypatch,
                           answer="The base does not cover this.", pdfs=())
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
