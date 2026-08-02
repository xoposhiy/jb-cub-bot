"""/ask, the session that keeps free text flowing to the agent, and /kb_reload.

A feature that waits for free text must own an FSM state: the Dispatcher's own
nl_fallback runs before every sub-router and only steps aside while the sender
is in a state.

Note what is *not* here: /cancel. `directory.edit` already registers it and
`directory` precedes `kb` in the loader's alphabetical walk, so that name is
taken. A session ends with the Exit button, with a fresh /ask, or on the last
allowed answer.
"""
from __future__ import annotations

import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core import oplog as oplog_mod
from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.config import get_settings
from jbcub_bot.core.intents import Intent
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.kb import pdf as pdf_mod
from jbcub_bot.features.kb import render as render_mod
from jbcub_bot.features.kb.agent import (
    KbRuntime,
    ask,
    build_runtime,
)

router = Router(name="kb")
cmd = CommandRegistrar(router)

MAX_QUESTIONS = 12
IDLE_SECONDS = 900

START_CALLBACK = "kb:start"
EXIT_CALLBACK = "kb:exit"

_NOT_CONFIGURED = ("Knowledge base search is not configured on this bot. "
                   "An admin needs to set KB_BASE_URL, KB_API_KEY and "
                   "KB_MODEL.")
_OPENED = ("Ask me anything about the program and I'll answer from the "
           "knowledge base. Tap Exit when you're done.")
_CLOSED = "Knowledge base session closed."
_EXHAUSTED = ("That was the last question in this session — send /ask to start "
              "a fresh one.")
_OFFER = "I didn't find anyone by that name. Search the knowledge base instead?"
_THINKING = "Searching the knowledge base…"


logger = logging.getLogger(__name__)


def now() -> float:
    """Wall clock, in one place so a test can move it."""
    return time.time()


async def _answer_html(message: Message, text: str) -> None:
    """Send as HTML; on a parse failure send the same words with no markup.

    Telegram rejects a whole message over one bad tag. Losing the answer to a
    stray `</b>` would be far worse than losing the bold.
    """
    try:
        await message.answer(text, parse_mode="HTML")
    except TelegramBadRequest:
        logger.warning("Telegram rejected an HTML answer; retrying as plain")
        await message.answer(render_mod.plain(text))


async def _attach_sources(bot, message: Message, live, snapshot,
                          pdfs, already: list[str]) -> list[str]:
    """Send each cited PDF this session has not seen yet.

    Returns the updated list. A source document is evidence for an answer that
    has already been sent, so a failure here changes nothing the reader needs.
    """
    sent = list(already)
    for ref in pdfs:
        if ref.file in sent:
            continue
        url = pdf_mod.raw_url(live.repo, snapshot.sha, ref.file)
        if await pdf_mod.send(bot, message.chat.id, url, ref.caption):
            sent.append(ref.file)
    return sent


# The runtime is process-wide and built on first use: get_settings() must not
# run at import time, or importing this feature would require a populated .env.
_runtime: KbRuntime | None = None
_built = False


def runtime() -> KbRuntime | None:
    global _runtime, _built
    if not _built:
        _runtime = build_runtime(get_settings())
        _built = True
    return _runtime


def set_runtime(value: KbRuntime | None) -> None:
    """Test seam: install a runtime (or None) without touching settings."""
    global _runtime, _built
    _runtime, _built = value, True


def reset_runtime() -> None:
    global _runtime, _built
    _runtime, _built = None, False


# The question that earned the offer button, kept until the tap. Without it the
# button opens an empty session and the person has to type the question again,
# which is the whole reason they were offered a button.
#
# Keyed by chat, so a second unanswered question replaces the first: the tap is
# always about the most recent thing they said. Process-wide like the runtime,
# and cleared wholesale if it ever grows -- a convenience, not a record.
_PENDING: dict[int, str] = {}
_PENDING_MAX = 500


def remember_question(chat_id: int, text: str) -> None:
    if len(_PENDING) >= _PENDING_MAX:
        _PENDING.clear()
    _PENDING[chat_id] = text


def take_question(chat_id: int) -> str:
    return _PENDING.pop(chat_id, "")


def reset_pending() -> None:
    _PENDING.clear()


def describe_asker(principal) -> str:
    """The one line about the caller that the agent gets.

    A cohort implies a programme and an academic year, which is most of what a
    "which courses do I have" question needs in order to pick a handbook.
    """
    if principal is None:
        return ""
    bits = [f"role: {principal.role.value}"]
    cohort = getattr(principal, "primary_cohort", "")
    if cohort:
        bits.append(f"cohort: {cohort}")
    return " · ".join(bits)


class KbChat(StatesGroup):
    active = State()


def _session_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Exit", callback_data=EXIT_CALLBACK)
    ]])


def _offer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Search the knowledge base",
                             callback_data=START_CALLBACK)
    ]])


async def _open(state: FSMContext) -> None:
    await state.set_state(KbChat.active)
    await state.set_data({"asked": 0, "last_at": now(), "history": [],
                          "sent_pdfs": []})


async def _close(state: FSMContext, bot: Bot | None, principal, tg_user,
                 asked: int) -> None:
    """End the session and report how much it was used.

    A session that asked nothing is not worth an entry, which also keeps a bare
    Exit tap off the ops log.
    """
    await state.clear()
    live = runtime()
    if bot is None or live is None or not asked:
        return
    log = oplog_mod.OpsLog(bot, live.log_chat_id, live.admin_ids)
    await log.send(oplog_mod.format_kb_session(asked, principal, tg_user))


@cmd.command("ask", "Ask the knowledge base a question.",
             min_role=Role.TEACHER)
async def cmd_ask(message: Message, principal: User, session,
                  state: FSMContext | None = None):
    # `state` is optional for the same reason as in directory/edit.py: /as
    # propagates through the Dispatcher without its outer middlewares.
    if runtime() is None:
        await message.answer(_NOT_CONFIGURED)
        return
    if state is None:
        await message.answer("Open a direct chat with me and send /ask there.")
        return
    await _open(state)
    await message.answer(_OPENED, reply_markup=_session_keyboard())


@cmd.command("kb_reload", "Re-download the knowledge base now.",
             min_role=Role.ADMIN)
async def cmd_kb_reload(message: Message, principal: User, session):
    live = runtime()
    if live is None:
        await message.answer(_NOT_CONFIGURED)
        return
    snapshot = await live.store.get(force=True)
    await message.answer(
        f"Knowledge base reloaded: {len(snapshot.notes)} notes at "
        f"{snapshot.sha[:7]}."
    )


async def kb_offer(message: Message, principal, session) -> bool:
    """Offer a knowledge base search for staff text nothing else took.

    Registered after the directory feature, so the name search keeps its right
    of first refusal. Answers with a line and a button; tokens are spent only
    after the tap.
    """
    if runtime() is None:
        return False
    remember_question(message.chat.id, message.text or "")
    await message.answer(_OFFER, reply_markup=_offer_keyboard())
    return True


kb_offer_intent = Intent(
    name="kb.offer",
    pattern=r".+",
    handler=kb_offer,
    description="ask the knowledge base a question",
    min_role=Role.TEACHER,
)


@router.callback_query(F.data == START_CALLBACK)
async def cb_start(cb: CallbackQuery, principal: User, session,
                   state: FSMContext, bot: Bot):
    if principal is None or principal.role is Role.STUDENT:
        await cb.answer("Staff only.", show_alert=True)
        return
    if runtime() is None:
        await cb.answer(_NOT_CONFIGURED, show_alert=True)
        return
    await _open(state)
    if not isinstance(cb.message, Message):
        await cb.answer()
        return
    pending = take_question(cb.message.chat.id)
    await cb.message.answer(_OPENED, reply_markup=_session_keyboard())
    # Answered before the agent runs: the button would otherwise spin for the
    # whole search.
    await cb.answer()
    if pending:
        await _answer_question(cb.message, principal, state, bot, pending,
                               cb.from_user)


@router.callback_query(F.data == EXIT_CALLBACK)
async def cb_exit(cb: CallbackQuery, principal: User, session,
                  state: FSMContext, bot: Bot):
    data = await state.get_data()
    await _close(state, bot, principal, cb.from_user, data.get("asked", 0))
    if isinstance(cb.message, Message):
        await cb.message.answer(_CLOSED)
    await cb.answer()


async def _answer_question(target: Message, principal: User, state: FSMContext,
                           bot: Bot, question: str, tg_user) -> None:
    """Put one question to the agent and send back what it says.

    `target` is whatever message the reply hangs off -- the person's own message
    in a session, or the bot's offer message when the button was tapped -- so
    this serves both entry points without either duplicating the other.
    """
    live = runtime()
    if live is None:  # redeployed without the settings while a session was open
        await state.clear()
        await target.answer(_NOT_CONFIGURED)
        return
    data = await state.get_data()

    await target.answer(_THINKING)
    snapshot = await live.store.get()
    answer, history, stats = await ask(live.agent, snapshot, question,
                                       data.get("history", []),
                                       about=describe_asker(principal))
    asked = data.get("asked", 0) + 1
    rendered = render_mod.render(answer, snapshot, stats)
    await _answer_html(target, rendered.html)
    sent_pdfs = await _attach_sources(bot, target, live, snapshot,
                                      rendered.pdfs,
                                      data.get("sent_pdfs", []))

    if asked >= MAX_QUESTIONS:
        await _close(state, bot, principal, tg_user, asked)
        await target.answer(_EXHAUSTED)
        return
    await state.update_data(asked=asked, last_at=now(), history=history,
                            sent_pdfs=sent_pdfs)


@router.message(KbChat.active, F.text & ~F.text.startswith("/"))
async def on_question(message: Message, principal: User, session,
                      state: FSMContext, bot: Bot):
    """One question in an open session.

    Commands are excluded rather than intercepted, so /ask and every other
    command still work while a session is open.
    """
    if runtime() is None:
        await state.clear()
        await message.answer(_NOT_CONFIGURED)
        return
    data = await state.get_data()
    if now() - data.get("last_at", 0.0) > IDLE_SECONDS:
        await _close(state, bot, principal, message.from_user,
                     data.get("asked", 0))
        await message.answer(
            "That knowledge base session went idle. Send /ask to start a new one."
        )
        return
    await _answer_question(message, principal, state, bot, message.text,
                           message.from_user)
