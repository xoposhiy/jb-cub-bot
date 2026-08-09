"""End-to-end wiring: real dispatcher, real chain, a fake agent.

The agent itself is covered in test_kb_agent.py and the conversation in
test_kb_history.py. What needs proving here is that anything the roster search
declined reaches the agent with no tap in between, that a name the roster does
not carry comes back as "No one found.", and that the two refusals on the chain
say nothing at all.
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
from jbcub_bot.core.pipeline import NOTHING_MATCHED
from jbcub_bot.features.kb import agent as kb_agent
from jbcub_bot.features.kb import handlers as kb
from jbcub_bot.features.kb import history, tools
from jbcub_bot.main import build_dispatcher

TEACHER_ID = 555
STUDENT_ID = 222
ADMIN_ID = 999

# Three words, so `matching.score` returns 0 against every roster name and the
# search at LOOKUP declines it before the agent is ever offered anything.
QUESTION = "how many retakes are allowed?"


class FakeBot:
    """Enough of a Bot to answer with real message ids.

    The ids matter: the rating pair is moved from message to message, so a stub
    that answers None to every send would make that untestable. `events` is the
    chronological record of every markup this chat has been shown.
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
        # that distinction is what lets `_reveal`'s edit be told apart from a
        # message that only looks similar.
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

_STATS = kb_agent.AskStats(
    steps=2, tool_calls=1, notes_read=1, input_tokens=1200, output_tokens=310,
    calls=(kb_agent.ToolCall("read_note", {"path": "kb/policies/exams.md"},
                             "1.2k chars"),))


def _install_runtime(monkeypatch, answer=_ANSWER, pdfs=(_PDF,), complaints=(),
                     log_chat_id="", admin_ids=(), rate_limit=100,
                     rate_window_seconds=3600, verdict=""):
    """A runtime whose agent is a function, and a record of what it was given.

    `verdict` makes that function decline the way the real one does when the
    message was a name -- but only while the name check is on, so a test can
    watch /ask switch it off.
    """
    store = FakeStore()
    run = SimpleNamespace(asked=[], carried=[], checks=[])

    async def fake_ask(agent, snapshot, question, history_, about="",
                       check_names=True, now=None):
        run.asked.append(question)
        run.carried.append(list(history_))
        run.checks.append(check_names)
        stamped = kb_agent.stamp(question, now)
        if verdict and check_names:
            return kb_agent.Answer("", stamped, _STATS, person_name=verdict)
        return kb_agent.Answer(text=answer, question=stamped, stats=_STATS,
                               sources=tuple(pdfs),
                               complaints=tuple(complaints))

    # handlers.py imported `ask` by name, so that binding is the one in play.
    monkeypatch.setattr(kb, "ask", fake_ask)
    kb.set_runtime(kb_agent.KbRuntime(agent=object(), store=store,
                                      repo="xoposhiy/cub-kb",
                                      log_chat_id=log_chat_id,
                                      admin_ids=tuple(admin_ids),
                                      rate_limit=rate_limit,
                                      rate_window_seconds=rate_window_seconds))
    return store, run


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


def _callback(fake_bot, telegram_id: int, data: str, update_id=2,
              on_message=7) -> Update:
    chat = Chat(id=telegram_id, type="private")
    shown = Message(message_id=on_message, date=datetime.now(timezone.utc),
                    chat=chat,
                    from_user=TgUser(id=1, is_bot=True, first_name="bot"),
                    text="an answer").as_(fake_bot)
    cb = CallbackQuery(id=f"cb-{update_id}",
                       from_user=TgUser(id=telegram_id, is_bot=False,
                                        first_name="tg"),
                       chat_instance="ci", data=data, message=shown).as_(fake_bot)
    return Update(update_id=update_id, callback_query=cb).as_(fake_bot)


def _texts(fake_bot) -> list[str]:
    return [getattr(m, "text", "") or "" for m in fake_bot.sent]


def _is_rating(markup) -> bool:
    return isinstance(markup, InlineKeyboardMarkup) and any(
        (button.callback_data or "").startswith(kb.RATE_CALLBACK)
        for row in markup.inline_keyboard for button in row)


def _rating_buttons(fake_bot) -> list[int]:
    """Which messages show the rating pair right now, replaying every event.

    A message's markup is whatever it was last set to, whether that was at send
    time or by a later edit — so the last event wins per message id.
    """
    shown: dict[int, bool] = {}
    for _, message_id, markup in fake_bot.events:
        shown[message_id] = _is_rating(markup)
    return [message_id for message_id, has in shown.items() if has]


def _last_message_id(fake_bot) -> int:
    return max((message_id for _, message_id, _ in fake_bot.events), default=0)


def _setup(monkeypatch, **kw):
    factory = _session_factory()
    _seed(factory)
    store, run = _install_runtime(monkeypatch, **kw)
    return build_dispatcher(session_factory=factory), FakeBot(), store, run


async def _say(dp, bot, text, telegram_id=TEACHER_ID, update_id=1):
    await dp.feed_update(bot, _message(bot, telegram_id, text, update_id),
                         dispatcher=dp)


# --- no tap, no mode ------------------------------------------------------------

async def test_unmatched_text_is_answered_by_the_agent_with_no_tap(monkeypatch):
    """The whole point: the offer button and the session behind it are gone."""
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)

    assert run.asked == [QUESTION]
    assert "Policies for Bachelor Studies" in _texts(bot)[-1]
    assert not any("Ask AI" in str(getattr(m, "reply_markup", "") or "")
                   for m in bot.sent)


async def test_a_student_is_answered_too_and_needed_no_role_for_it(monkeypatch):
    """The chain slot carries no role, so this reaches the agent exactly as a
    teacher's does. The hourly budget is the only guard."""
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, QUESTION, telegram_id=STUDENT_ID)

    assert run.asked == [QUESTION]


async def test_a_found_name_still_shows_a_profile_and_never_asks_the_agent(
        monkeypatch):
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, "Ivanov")

    assert run.asked == [], "the search took it at LOOKUP"
    assert any("Ivan Ivanov" in text for text in _texts(bot))


async def test_the_placeholder_is_neutral_and_becomes_the_answer(monkeypatch):
    """A reader who typed a surname should not be told an AI is thinking about
    it, and the placeholder is edited rather than left standing above."""
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)

    sends = [m for m in bot.sent if isinstance(m, SendMessage)]
    assert sends[0].text == "🔎 Looking…"
    edited = [m for m in bot.sent if getattr(m, "parse_mode", None) == "HTML"]
    assert edited[0].chat_id == TEACHER_ID
    assert "Retakes once." in edited[0].text


async def test_the_reader_gets_the_agents_words_unedited(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)

    answer = _texts(bot)[-1]
    assert answer == _ANSWER
    assert "steps" not in answer, "cost is an admin's business, not a teacher's"


async def test_the_knowledge_base_answers_inside_the_impersonation_mode(
        monkeypatch):
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, f"/as {STUDENT_ID}", telegram_id=ADMIN_ID)
    await _say(dp, bot, QUESTION, telegram_id=ADMIN_ID, update_id=2)

    assert run.asked == [QUESTION]


async def test_the_agent_is_told_the_asker_role_and_cohort(monkeypatch):
    seen: list[str] = []
    factory = _session_factory()
    _seed(factory)
    store = FakeStore()

    async def fake_ask(agent, snapshot, question, history_, about="",
                       check_names=True, now=None):
        seen.append(about)
        return kb_agent.Answer("ok", question, kb_agent.AskStats())

    monkeypatch.setattr(kb, "ask", fake_ask)
    kb.set_runtime(kb_agent.KbRuntime(agent=object(), store=store,
                                      repo="xoposhiy/cub-kb"))
    dp, bot = build_dispatcher(session_factory=factory), FakeBot()

    await _say(dp, bot, QUESTION)

    assert seen == ["role: Teacher"], "a teacher has no cohort to pass on"


def test_a_students_cohort_is_what_picks_the_programme():
    student = User(last_name="I", first_name="I", telegram_id=1,
                   role=Role.STUDENT, primary_cohort="2024")

    assert kb.describe_asker(student) == "role: Student · cohort: 2024"
    assert kb.describe_asker(None) == ""


# --- the conversation -----------------------------------------------------------

async def test_a_second_question_carries_the_first_pair(monkeypatch):
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)
    await _say(dp, bot, "and what about resits?", update_id=2)

    assert run.carried[0] == [], "the first question had nothing behind it"
    assert [item["role"] for item in run.carried[1]] == ["user", "assistant"]
    assert run.carried[1][0]["content"].endswith(QUESTION)
    assert run.carried[1][1]["content"] == _ANSWER


async def test_a_profile_shown_between_two_questions_drops_the_conversation(
        monkeypatch):
    """The reader changed the subject. `kb` learns it from the taker record and
    never from anything that knows `directory` exists."""
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)
    await _say(dp, bot, "Ivanov", update_id=2)
    await _say(dp, bot, "and what about resits?", update_id=3)

    assert run.carried[-1] == []


async def test_a_question_after_one_of_its_own_answers_keeps_the_thread(
        monkeypatch):
    """The other half of the rule: this feature answering is not a change of
    subject, however many turns it runs for."""
    dp, bot, _, run = _setup(monkeypatch)

    for update_id in range(1, 4):
        await _say(dp, bot, f"{QUESTION} {update_id}", update_id=update_id)

    assert len(run.carried[-1]) == 4, "two pairs behind the third question"


async def test_nothing_of_the_run_is_carried_but_the_words(monkeypatch):
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)
    await _say(dp, bot, "and resits?", update_id=2)

    assert "read_note" not in str(run.carried[-1])
    assert "kb/policies/exams.md" not in str(run.carried[-1])


# --- the name the roster does not carry -------------------------------------------

async def test_a_name_verdict_edits_the_placeholder_into_no_one_found(
        monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, verdict="Егоров")

    await _say(dp, bot, "Егоров")

    assert _texts(bot)[-1] == NOTHING_MATCHED
    assert "Policies" not in "".join(_texts(bot)), "no answer went out"


async def test_a_name_verdict_drops_the_conversation(monkeypatch):
    """The reader was asking about a person, so whatever came before is over."""
    dp, bot, _, run = _setup(monkeypatch, verdict="Егоров")

    await _say(dp, bot, "Егоров")

    assert history.as_input(history.conversation(TEACHER_ID)) == []
    assert run.carried[-1] == []


async def test_a_name_verdict_attaches_nothing_and_traces_nothing(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, verdict="Егоров")

    await _say(dp, bot, "Егоров", telegram_id=ADMIN_ID)

    assert bot.documents == []
    assert not any("read_note" in text for text in _texts(bot))


LOG_CHAT = "-1009999"


def _logged(fake_bot) -> list[str]:
    return [m.text for m in fake_bot.sent
            if str(getattr(m, "chat_id", "")) == LOG_CHAT]


async def test_a_name_verdict_is_logged_as_a_miss_with_the_runs_cost(
        monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, verdict="Егоров", log_chat_id=LOG_CHAT)

    await _say(dp, bot, "Егоров")

    [entry] = _logged(bot)
    assert "Nothing matched" in entry
    assert "«Егоров»" in entry
    assert NOTHING_MATCHED in entry
    assert "1.2k in / 310 out" in entry, "the miss cost a model turn"


# --- /ask, which starts clean -------------------------------------------------------

async def test_ask_with_a_question_bypasses_the_search(monkeypatch):
    """"Ivanov" would have shown a profile; after /ask it is a question."""
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, "/ask Ivanov")

    assert run.asked == ["Ivanov"]
    assert not any("Ivan Ivanov" in text for text in _texts(bot))


async def test_ask_turns_the_name_check_off(monkeypatch):
    """Which is what makes it the only way to ask the agent about a person by
    name: on the chain the same words would come back "No one found."."""
    dp, bot, _, run = _setup(monkeypatch, verdict="Dr Weber")

    await _say(dp, bot, "/ask who supervises Dr Weber's students?")

    assert run.checks == [False]
    assert _texts(bot)[-1] == _ANSWER


async def test_the_chain_leaves_the_name_check_on(monkeypatch):
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)

    assert run.checks == [True]


async def test_ask_drops_whatever_conversation_was_going(monkeypatch):
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)
    await _say(dp, bot, "/ask and what about resits?", update_id=2)

    assert run.carried[-1] == []


async def test_a_bare_ask_says_so_and_drops_the_conversation(monkeypatch):
    dp, bot, _, run = _setup(monkeypatch)
    await _say(dp, bot, QUESTION)

    await _say(dp, bot, "/ask", update_id=2)
    await _say(dp, bot, "and what about resits?", update_id=3)

    assert any("just type it" in text for text in _texts(bot))
    assert run.carried[-1] == [], "the thread was dropped, not merely paused"


async def test_a_bare_ask_spends_nothing(monkeypatch):
    dp, bot, _, run = _setup(monkeypatch)

    await _say(dp, bot, "/ask")

    assert run.asked == []


async def test_ask_without_a_configured_endpoint_says_so(monkeypatch):
    factory = _session_factory()
    _seed(factory)
    kb.set_runtime(None)
    dp, bot = build_dispatcher(session_factory=factory), FakeBot()

    await _say(dp, bot, "/ask what are the retake rules?")

    assert "not configured" in _texts(bot)[-1]


# --- the two refusals on the chain ----------------------------------------------------

async def test_an_unconfigured_runtime_declines_rather_than_explaining(
        monkeypatch):
    """The chain slot returns False, so the core's last word answers -- which
    is the right thing to say to somebody who mistyped a surname."""
    factory = _session_factory()
    _seed(factory)
    kb.set_runtime(None)
    dp, bot = build_dispatcher(session_factory=factory), FakeBot()

    await _say(dp, bot, QUESTION)

    assert _texts(bot) == [NOTHING_MATCHED]
    assert not any("KB_LLM_API_KEY" in text for text in _texts(bot))


async def test_an_exhausted_budget_declines_without_calling_the_agent(
        monkeypatch):
    dp, bot, _, run = _setup(monkeypatch, rate_limit=1)
    await _say(dp, bot, QUESTION)
    assert run.asked == [QUESTION]

    await _say(dp, bot, "and what about resits?", update_id=2)

    assert run.asked == [QUESTION], "the shared budget is spent"
    assert _texts(bot)[-1] == NOTHING_MATCHED
    assert kb._RATE_LIMITED not in _texts(bot)


async def test_an_exhausted_budget_still_pings_the_admins(monkeypatch):
    """The reader is told nothing, so this entry is the only place it shows."""
    dp, bot, _, _ = _setup(monkeypatch, rate_limit=1, log_chat_id=LOG_CHAT,
                           admin_ids=(ADMIN_ID,))
    await _say(dp, bot, QUESTION)

    await _say(dp, bot, "and what about resits?", update_id=2)

    assert any("hourly limit" in entry for entry in _logged(bot))


async def test_ask_gets_the_honest_reason_because_it_asked_outright(monkeypatch):
    dp, bot, _, run = _setup(monkeypatch, rate_limit=1)
    await _say(dp, bot, QUESTION)

    await _say(dp, bot, "/ask and what about resits?", update_id=2)

    assert run.asked == [QUESTION]
    assert _texts(bot)[-1] == kb._RATE_LIMITED


async def test_the_budget_frees_up_once_the_window_passes(monkeypatch):
    dp, bot, _, run = _setup(monkeypatch, rate_limit=1, rate_window_seconds=5)
    await _say(dp, bot, QUESTION)

    real_now = kb.now
    monkeypatch.setattr(kb, "now", lambda: real_now() + 6)
    await _say(dp, bot, "and what about resits?", update_id=2)

    assert run.asked == [QUESTION, "and what about resits?"]


# --- what the ops chat sees -------------------------------------------------------

async def test_every_question_reaches_the_ops_chat_with_its_cost(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, log_chat_id=LOG_CHAT)

    await _say(dp, bot, QUESTION)
    await _say(dp, bot, "and what about resits?", update_id=2)

    entries = _logged(bot)
    assert len(entries) == 2, "one entry per question"
    assert "Tanya Teacher" in entries[0], "who asked"
    assert f"«{QUESTION}»" in entries[0], "and what they asked"
    assert "1 tool call" in entries[0] and "1.2k in / 310 out" in entries[0]
    assert "«and what about resits?»" in entries[1]


async def test_a_students_question_is_logged_like_anybody_elses(monkeypatch):
    """It reaches the agent without a tap now, so it is one of the questions
    the ops chat exists to show."""
    dp, bot, _, _ = _setup(monkeypatch, log_chat_id=LOG_CHAT)

    await _say(dp, bot, QUESTION, telegram_id=STUDENT_ID)

    assert len(_logged(bot)) == 1


# --- the rating pair --------------------------------------------------------------

async def test_an_answer_carries_the_rating_pair(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)

    assert _rating_buttons(bot) == [_last_message_id(bot)]


async def test_the_chat_never_holds_two_rating_pairs(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    for update_id in range(1, 5):
        await _say(dp, bot, f"{QUESTION} {update_id}", update_id=update_id)
        assert _rating_buttons(bot) == [_last_message_id(bot)]


async def test_the_pair_lands_on_the_attachment_when_that_came_last(monkeypatch):
    """A source PDF is sent after the answer, so the answer is not the last
    word and must not be where the buttons wait."""
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)

    assert bot.documents, "this answer does attach a PDF"
    assert _rating_buttons(bot) == [_last_message_id(bot)]


async def test_the_pair_lands_on_the_trace_for_an_admin(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION, telegram_id=ADMIN_ID)

    trace_at = max(mid for kind, mid, _ in bot.events if kind == "send")
    assert _rating_buttons(bot) == [trace_at]


async def test_a_pair_telegram_refuses_to_move_is_left_where_it_is(monkeypatch):
    """Better a rating one message too high than no way to rate at all."""
    dp, bot, _, _ = _setup(monkeypatch)
    await _say(dp, bot, QUESTION)
    first = _rating_buttons(bot)[0]

    async def refuse(chat_id, message_id, reply_markup=None):
        raise TelegramBadRequest(method=SendMessage(chat_id=chat_id, text="x"),
                                 message="message can't be edited")

    monkeypatch.setattr(bot, "edit_message_reply_markup", refuse)
    await _say(dp, bot, "and what about resits?", update_id=2)

    assert _rating_buttons(bot) == [first]


async def test_dropping_a_conversation_leaves_no_second_pair_behind(monkeypatch):
    """The pair's position is remembered outside the conversation for exactly
    this: a profile in between must not cost the chat a stray keyboard."""
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)
    await _say(dp, bot, "Ivanov", update_id=2)
    await _say(dp, bot, "and what about resits?", update_id=3)

    assert _rating_buttons(bot) == [_last_message_id(bot)]


async def test_a_rating_tap_logs_it_and_strips_the_buttons(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, log_chat_id=LOG_CHAT)
    await _say(dp, bot, QUESTION)
    rated = _rating_buttons(bot)[0]
    before = len(_logged(bot))

    await dp.feed_update(bot, _callback(bot, TEACHER_ID,
                                        f"{kb.RATE_CALLBACK}:good",
                                        update_id=2, on_message=rated),
                         dispatcher=dp)

    assert _rating_buttons(bot) == []
    assert "👍" in _logged(bot)[-1]
    assert len(_logged(bot)) == before + 1


async def test_a_bad_rating_logs_the_thumbs_down_icon(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, log_chat_id=LOG_CHAT)
    await _say(dp, bot, QUESTION)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID,
                                        f"{kb.RATE_CALLBACK}:bad",
                                        update_id=2), dispatcher=dp)

    assert "👎" in _logged(bot)[-1]


async def test_a_rating_tap_keeps_the_conversation(monkeypatch):
    """Rating the answer on the screen is not a way of changing the subject."""
    dp, bot, _, run = _setup(monkeypatch)
    await _say(dp, bot, QUESTION)

    await dp.feed_update(bot, _callback(bot, TEACHER_ID,
                                        f"{kb.RATE_CALLBACK}:good",
                                        update_id=2), dispatcher=dp)
    await _say(dp, bot, "and what about resits?", update_id=3)

    assert len(run.carried[-1]) == 2


async def test_the_rating_pair_is_the_only_key_this_feature_claims(monkeypatch):
    """"Ask AI" is gone, key and all. The bot has never been deployed with it,
    so there is no keyboard anywhere to answer and no stub to keep for one."""
    dp, bot, _, _ = _setup(monkeypatch)

    keys = [spec.key for spec in dp["registry"].buttons()
            if spec.feature == "kb"]

    assert keys == [kb.RATE_CALLBACK]


# --- the admin trace ----------------------------------------------------------------

async def test_an_admin_is_shown_what_the_agent_did(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION, telegram_id=ADMIN_ID)

    trace = _texts(bot)[-1]
    assert "read_note kb/policies/exams.md — 1.2k chars" in trace
    assert "2 steps · 1 tool call · 1 note read · 1.2k in / 310 out" in trace


async def test_the_trace_comes_after_the_answer_it_explains(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION, telegram_id=ADMIN_ID)

    texts = _texts(bot)
    answer_at = next(i for i, t in enumerate(texts)
                     if "Policies for Bachelor Studies" in t)
    assert answer_at < len(texts) - 1


async def test_a_teacher_is_shown_the_answer_and_nothing_else(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)

    assert not any("read_note" in t for t in _texts(bot))
    assert not any("tool call" in t for t in _texts(bot))


# --- the sources ----------------------------------------------------------------------

async def test_the_source_pdf_is_attached_once_per_conversation(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)

    await _say(dp, bot, QUESTION)
    await _say(dp, bot, "and what about resits?", update_id=2)

    assert len(bot.documents) == 1, "the second answer references, not re-sends"


async def test_a_fresh_conversation_gets_the_pdf_again(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch)
    await _say(dp, bot, QUESTION)

    await _say(dp, bot, "/ask and what about resits?", update_id=2)

    assert len(bot.documents) == 2


async def test_a_web_source_arrives_as_a_link_rather_than_a_file(monkeypatch):
    """The academic calendar is a web page. Its frontmatter carries the address,
    and a 100 KB scrape of that page would be no use to anybody."""
    dp, bot, _, _ = _setup(monkeypatch, pdfs=(_WEB,))

    await _say(dp, bot, QUESTION)

    assert bot.documents == [], "nothing to upload"
    posted = [t for t in _texts(bot) if _WEB.url in t]
    assert len(posted) == 1
    assert "Academic Calendar 2026/2027" in posted[0]


async def test_a_link_is_posted_once_per_conversation_like_a_file(monkeypatch):
    dp, bot, _, _ = _setup(monkeypatch, pdfs=(_WEB,))

    await _say(dp, bot, QUESTION)
    await _say(dp, bot, "and what about resits?", update_id=2)

    assert sum(_WEB.url in t for t in _texts(bot)) == 1


async def test_an_agent_that_named_no_source_gets_nothing_attached(monkeypatch):
    """Attachments follow the sources the agent chose, not a reading of its
    prose, so an answer that named none sends none."""
    dp, bot, _, _ = _setup(monkeypatch, answer="The base does not cover this.",
                           pdfs=())

    await _say(dp, bot, QUESTION)

    assert bot.documents == []


async def test_a_rejected_html_message_is_resent_as_plain_text(monkeypatch):
    factory = _session_factory()
    _seed(factory)
    _install_runtime(monkeypatch, answer="<b>Retakes</b> once.")
    dp, bot = build_dispatcher(session_factory=factory), FakeBot()
    bot.reject_html = True

    await _say(dp, bot, QUESTION)

    answer = _texts(bot)[-1]
    assert "Retakes once." in answer
    assert "<b>" not in answer, "the fallback carries the words, not the markup"


# --- /kb_reload -----------------------------------------------------------------------

async def test_kb_reload_is_admin_only(monkeypatch):
    dp, bot, store, _ = _setup(monkeypatch)

    await _say(dp, bot, "/kb_reload")

    assert store.forced == 0
    assert "Admins only." in _texts(bot)


async def test_kb_reload_refetches_and_says_what_it_got(monkeypatch):
    dp, bot, store, _ = _setup(monkeypatch)

    await _say(dp, bot, "/kb_reload", telegram_id=ADMIN_ID)

    assert store.forced == 1
    assert "1 notes at abc123" in _texts(bot)[-1]
