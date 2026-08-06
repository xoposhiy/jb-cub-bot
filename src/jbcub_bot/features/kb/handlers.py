"""/ask, the session that keeps free text flowing to the agent, and /kb_reload.

A feature that waits for free text must own an FSM state: the Dispatcher's own
nl_fallback runs before every sub-router and only steps aside while the sender
is in a state.

Leaving that state is the one thing a reader has to be able to find, so there is
always exactly one pair of exit buttons in the chat -- rating the last answer
doubles as leaving -- and it is always under the newest thing the bot said. It
is not redrawn on every message -- that would pepper the chat with buttons --
but moved: after each exchange it is attached to the last message sent and
stripped from wherever it was before. `button_at` in the FSM data remembers
where that is.

Note what is *not* here: /cancel. `directory.edit` already registers it and
`directory` precedes `kb` in the loader's alphabetical walk, so that name is
taken. A session ends with a rating, with a fresh /ask, or on the last allowed
answer.
"""
from __future__ import annotations

import logging
import time
from collections import deque

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandObject
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
EXIT_GOOD_CALLBACK = "kb:exit:good"
EXIT_BAD_CALLBACK = "kb:exit:bad"
EXIT_THINKING_CALLBACK = "kb:exit:thinking"
GOOD_TEXT = "✅ Good answer, exit"
BAD_TEXT = "❌ Bad answer, exit"
THINKING_EXIT_TEXT = "🚪 Exit AI chat"

_NOT_CONFIGURED = ("Knowledge base search is not configured on this bot. "
                   "An admin needs to set KB_LLM_API_KEY.")
_OPENED = ("Ask me anything about the program and I'll answer from the "
           "knowledge base. Rate an answer when you're done to end the "
           "session.")
_CLOSED = "Knowledge base session closed."
_EXHAUSTED = ("That was the last question in this session — send /ask to start "
              "a fresh one.")
_IDLE = "That knowledge base session went idle. Send /ask to start a new one."
_OFFER = "I didn't find anyone by that name. Ask AI instead?"
_THINKING = "AI is thinking…"
_RATE_LIMITED = ("The knowledge base is getting a lot of questions right now — "
                 "try again in a few minutes.")
_NEEDS_A_QUESTION = ('Add the question after the command, e.g. '
                     '"/ask when is the deadline?".')


logger = logging.getLogger(__name__)


def now() -> float:
    """Wall clock, in one place so a test can move it."""
    return time.time()


async def _answer_html(message: Message, text: str):
    """Send as HTML; on a parse failure send the same words with no markup.

    Telegram rejects a whole message over one bad tag. Losing the answer to a
    stray `</b>` would be far worse than losing the bold. Returns whichever
    message landed, so the caller can hang the Exit button off it.
    """
    try:
        return await message.answer(text, parse_mode="HTML")
    except TelegramBadRequest:
        logger.warning("Telegram rejected an HTML answer; retrying as plain")
        return await message.answer(render_mod.plain(text))


async def _reveal_answer(bot: Bot, target: Message, thinking: Message,
                         text: str):
    """Turn the "AI is thinking" placeholder into the answer, in place.

    Editing it rather than sending a second message keeps the "thinking" line
    from lingering in the chat once there is something to read. Bad markup
    gets the same plain-text retry `_answer_html` uses; if the edit itself
    fails -- the placeholder was deleted, say -- a fresh message still gets
    the answer through.
    """
    chat_id, message_id = target.chat.id, thinking.message_id
    try:
        return await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                           text=text, parse_mode="HTML")
    except TelegramBadRequest:
        logger.warning("Telegram rejected an HTML edit; retrying as plain")
    except TelegramAPIError:
        logger.warning("could not edit the thinking placeholder", exc_info=True)
        return await _answer_html(target, text)
    try:
        return await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                           text=render_mod.plain(text))
    except TelegramAPIError:
        logger.warning("could not edit the thinking placeholder as plain text",
                       exc_info=True)
        return await _answer_html(target, text)


async def _send_trace(target: Message, principal, stats, complaints=()):
    """What the agent did to earn that answer — admins only.

    A teacher wants the answer; whoever runs the bot wants to see which tools
    ran, on what, and what came back. Sent plain, after the answer and its
    attachments, so it never delays or endangers the answer itself. A trace
    that fails to send is a diagnostic that failed, not a question that failed.
    """
    if principal is None or principal.role is not Role.ADMIN:
        return None
    try:
        return await target.answer(render_mod.trace_message(stats, complaints))
    except TelegramAPIError:
        logger.warning("could not send the knowledge base trace",
                       exc_info=True)
        return None


async def _attach_sources(bot, message: Message, live, snapshot,
                          sources, already: list[str]) -> tuple[list[str],
                                                                object]:
    """Give the reader each source the agent named, once per session.

    A PDF is uploaded; a web page is linked, because its frontmatter carries the
    address and a 100 KB scrape of the page would be no use to anybody. The
    links go out together in one message rather than one apiece.

    Returns the updated list and the last thing that landed. All of this is
    evidence for an answer that has already been sent, so a failure here changes
    nothing the reader needs.
    """
    sent, last = list(already), None
    links: list[str] = []
    for ref in sources:
        if ref.file in sent:
            continue
        if ref.is_pdf:
            url = pdf_mod.raw_url(live.repo, snapshot.sha, ref.file)
            attached = await pdf_mod.send(bot, message.chat.id, url,
                                          ref.caption)
            if attached is not None:
                sent.append(ref.file)
                last = attached
        elif ref.url:
            links.append(f"🌐 {ref.caption}\n{ref.url}")
            sent.append(ref.file)
    if links:
        try:
            last = await message.answer("\n\n".join(links)) or last
        except TelegramAPIError:
            logger.warning("could not send the source links", exc_info=True)
    return sent, last


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


# When each of the last window's questions went to the agent. Process-wide
# like `_PENDING` above, and for the same reason: there is one bot, so one
# clock is enough. A deque rather than a count that resets on the hour, so the
# window always looks back from now instead of resetting on a schedule nobody
# chose. `limit`/`window_seconds` come from the runtime -- see
# `KbRuntime.rate_limit` -- so they live in settings with the rest of the
# feature's configuration rather than as constants here.
_ASK_TIMES: deque[float] = deque()


def _budget_spent(limit: int, window_seconds: int) -> bool:
    """True once `limit` questions have already gone to the agent within the
    last `window_seconds` -- the caller is the one that still gets to decide
    what to do about it."""
    cutoff = now() - window_seconds
    while _ASK_TIMES and _ASK_TIMES[0] < cutoff:
        _ASK_TIMES.popleft()
    if len(_ASK_TIMES) >= limit:
        return True
    _ASK_TIMES.append(now())
    return False


def reset_rate_limit() -> None:
    _ASK_TIMES.clear()


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
        InlineKeyboardButton(text=GOOD_TEXT, callback_data=EXIT_GOOD_CALLBACK),
        InlineKeyboardButton(text=BAD_TEXT, callback_data=EXIT_BAD_CALLBACK),
    ]])


def _offer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Ask AI", callback_data=START_CALLBACK)
    ]])


def _thinking_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=THINKING_EXIT_TEXT,
                             callback_data=EXIT_THINKING_CALLBACK)
    ]])


async def _set_markup(bot, chat_id: int, message_id: int, markup) -> bool:
    """Put `markup` on a message that has already been sent, or take it off.

    Every failure here is expected traffic rather than a fault: the reader may
    have deleted the message, it may be older than Telegram allows editing, or
    the markup may already be what we are asking for. None of that is worth
    losing an answer over.
    """
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id,
                                            message_id=message_id,
                                            reply_markup=markup)
    except TelegramAPIError:
        logger.debug("could not move the knowledge base Exit button",
                     exc_info=True)
        return False
    return True


async def _park_exit_button(bot, chat_id: int, state: FSMContext,
                            message) -> None:
    """Move the one Exit button so that it sits under `message`.

    An inline button scrolls away with the message that carries it, and drawing
    a fresh one on every message would fill the chat with them. So there is
    exactly one, and it walks forward: attached to the last thing the bot said,
    stripped from wherever it was before.
    """
    new_id = getattr(message, "message_id", 0) or 0
    data = await state.get_data()
    previous = data.get("button_at") or 0
    if not new_id or new_id == previous:
        return
    if not await _set_markup(bot, chat_id, new_id, _session_keyboard()):
        return  # the old button is still live; better there than nowhere
    if previous:
        await _set_markup(bot, chat_id, previous, None)
    await state.update_data(button_at=new_id)


async def _clear_exit_button(bot, chat_id: int, data: dict) -> None:
    """Take the button down for good: the session it belonged to is over."""
    previous = data.get("button_at") or 0
    if bot is not None and previous:
        await _set_markup(bot, chat_id, previous, None)


async def _open(state: FSMContext, bot, chat_id: int) -> None:
    """Start a session, first taking down any button the last one left."""
    await _clear_exit_button(bot, chat_id, await state.get_data())
    await state.set_state(KbChat.active)
    await state.set_data({"asked": 0, "last_at": now(), "history": [],
                          "sent_pdfs": [], "button_at": 0})


async def _greet(target: Message, state: FSMContext) -> None:
    """The opening line, carrying the session's first Exit button."""
    opened = await target.answer(_OPENED, reply_markup=_session_keyboard())
    await state.update_data(button_at=getattr(opened, "message_id", 0) or 0)


async def _close(state: FSMContext, bot: Bot | None, chat_id: int,
                 data: dict) -> None:
    """End the session and take its button down.

    Nothing is reported: the ops log now carries every question as it is asked,
    which says everything a closing tally used to and says it while the team is
    still typing.
    """
    await _clear_exit_button(bot, chat_id, data)
    await state.clear()


async def _log_question(bot, live, principal, tg_user, question,
                        result) -> None:
    """Put the question, and what it cost, in the ops chat.

    Sent after the answer, for the same reason the admin trace is: whoever
    asked is waiting on the answer, and a report is never worth delaying it.
    `OpsLog` swallows its own delivery failures, so there is nothing to guard.
    """
    head = oplog_mod.format_kb_question(question, principal, tg_user)
    room = render_mod.CLIP_LIMIT - len(head) - 1
    trace = render_mod.trace_message(result.stats, result.complaints,
                                     limit=room)
    log = oplog_mod.OpsLog(bot, live.log_chat_id, live.admin_ids)
    await log.send(f"{head}\n{trace}")


async def _log_rate_limit(bot, live, principal, tg_user) -> None:
    """Ping the admins by name: the hourly AI budget just ran out.

    Nobody is meant to hit this in normal use, so it gets the same treatment
    as a crash -- a mention, not just another line in the question feed --
    because someone should go find out why rather than let it pass.
    """
    ping, entities = oplog_mod.admin_mention(live.admin_ids)
    prefix = f"{ping}\n" if ping else ""
    text = prefix + oplog_mod.format_kb_rate_limited(live.rate_limit, principal,
                                                      tg_user)
    log = oplog_mod.OpsLog(bot, live.log_chat_id, live.admin_ids)
    await log.send(text, entities=entities)


@cmd.command("ask", "Ask the knowledge base a question.", usage="[question]")
async def cmd_ask(message: Message, principal: User, session, bot: Bot,
                  command: CommandObject,
                  state: FSMContext | None = None):
    # `state` is optional for the same reason as in directory/edit.py: /as
    # propagates through the Dispatcher without its outer middlewares --
    # FSMContext among them -- so a multi-turn session has nowhere to live.
    # /as can still ask, just one question at a time; see _answer_one_shot.
    if runtime() is None:
        await message.answer(_NOT_CONFIGURED)
        return
    question = (command.args or "").strip()
    if state is None:
        if not question:
            await message.answer(_NEEDS_A_QUESTION)
            return
        await _answer_one_shot(message, principal, bot, question,
                               message.from_user)
        return
    await _open(state, bot, message.chat.id)
    if question:
        await _answer_question(message, principal, state, bot, question,
                               message.from_user)
    else:
        await _greet(message, state)


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
    remember_question(message.chat.id, (message.text or "").strip())
    await message.answer(_OFFER, reply_markup=_offer_keyboard())
    return True


kb_offer_intent = Intent(
    name="kb.offer",
    pattern=r".+",
    handler=kb_offer,
    description="ask the knowledge base a question",
)


@router.callback_query(F.data == START_CALLBACK)
async def cb_start(cb: CallbackQuery, principal: User, session,
                   state: FSMContext, bot: Bot):
    if principal is None:
        await cb.answer("You are not linked yet. Contact an admin.",
                        show_alert=True)
        return
    if runtime() is None:
        await cb.answer(_NOT_CONFIGURED, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await _open(state, bot, cb.from_user.id)
        await cb.answer()
        return
    # Tapped once, its job is done -- leaving it up invites a second, empty tap.
    await _set_markup(bot, cb.message.chat.id, cb.message.message_id, None)
    await _open(state, bot, cb.message.chat.id)
    pending = take_question(cb.message.chat.id)
    if not pending:
        # A question is already on its way to the agent -- greeting the person
        # again would just repeat what the offer already told them.
        await _greet(cb.message, state)
    # Answered before the agent runs: the button would otherwise spin for the
    # whole search.
    await cb.answer()
    if pending:
        await _answer_question(cb.message, principal, state, bot, pending,
                               cb.from_user)


async def _log_feedback(bot, good: bool, principal, tg_user) -> None:
    """Put the reader's rating of the last answer in the ops chat.

    A rating is worth nothing without a chat to read it in, so this shares the
    same destination as every question -- one feed, not a second inbox to
    remember to check.
    """
    live = runtime()
    if live is None:
        return
    log = oplog_mod.OpsLog(bot, live.log_chat_id, live.admin_ids)
    await log.send(oplog_mod.format_kb_feedback(good, principal, tg_user))


_RATING = {EXIT_GOOD_CALLBACK: True, EXIT_BAD_CALLBACK: False}


@router.callback_query(F.data.in_({EXIT_GOOD_CALLBACK, EXIT_BAD_CALLBACK,
                                   EXIT_THINKING_CALLBACK}))
async def cb_exit(cb: CallbackQuery, principal: User, session,
                  state: FSMContext, bot: Bot):
    # None while the agent hasn't answered yet -- "Exit AI chat" leaves without
    # rating anything, because there is nothing yet to rate.
    rating = _RATING.get(cb.data)
    data = await state.get_data()
    if not isinstance(cb.message, Message):
        await _close(state, bot, cb.from_user.id, data)
        if rating is not None:
            await _log_feedback(bot, rating, principal, cb.from_user)
        await cb.answer()
        return
    # The tapped button is the one the session was tracking, so taking the
    # markup off that very message is what `_close` is about to do anyway.
    await _close(state, bot, cb.message.chat.id, data)
    await cb.message.answer(_CLOSED)
    if rating is not None:
        await _log_feedback(bot, rating, principal, cb.from_user)
    await cb.answer()


async def _answer_one_shot(target: Message, principal: User, bot: Bot,
                           question: str, tg_user) -> None:
    """Answer one question with no session behind it.

    /as reaches /ask with no FSMContext to hold a multi-turn session in (see
    the note in `cmd_ask`), so there is nowhere to keep history, an
    asked-count, or an Exit button between messages. This does exactly what a
    session would for a single exchange -- budget check, the answer, its
    sources, the admin trace, the ops log -- and stops there.
    """
    live = runtime()
    if live is None:
        await target.answer(_NOT_CONFIGURED)
        return
    if _budget_spent(live.rate_limit, live.rate_window_seconds):
        await target.answer(_RATE_LIMITED)
        await _log_rate_limit(bot, live, principal, tg_user)
        return

    thinking = await target.answer(_THINKING)
    snapshot = await live.store.get()
    result = await ask(live.agent, snapshot, question, [],
                       about=describe_asker(principal))
    await _reveal_answer(bot, target, thinking, render_mod.clip(result.text))
    await _attach_sources(bot, target, live, snapshot, result.sources, [])
    await _send_trace(target, principal, result.stats, result.complaints)
    await _log_question(bot, live, principal, tg_user, question, result)


async def _answer_question(target: Message, principal: User, state: FSMContext,
                           bot: Bot, question: str, tg_user) -> None:
    """Put one question to the agent and send back what it says.

    `target` is whatever message the reply hangs off -- the person's own message
    in a session, or the bot's offer message when the button was tapped -- so
    this serves both entry points without either duplicating the other.
    """
    data = await state.get_data()
    live = runtime()
    if live is None:  # redeployed without the settings while a session was open
        await _close(state, bot, target.chat.id, data)
        await target.answer(_NOT_CONFIGURED)
        return
    if _budget_spent(live.rate_limit, live.rate_window_seconds):
        # Closed rather than left open: nothing here draws a fresh Exit
        # button, and leaving the session active would strand the asker in it
        # with no way out until the budget frees up.
        await _close(state, bot, target.chat.id, data)
        await target.answer(_RATE_LIMITED)
        await _log_rate_limit(bot, live, principal, tg_user)
        return

    thinking = await target.answer(_THINKING, reply_markup=_thinking_keyboard())
    snapshot = await live.store.get()
    result = await ask(live.agent, snapshot, question, data.get("history", []),
                       about=describe_asker(principal))
    asked = data.get("asked", 0) + 1
    # The agent's own words, clipped only if it wrote past what Telegram takes.
    # Each of these may or may not be the last word of the exchange; the Exit
    # button goes under whichever one actually was.
    last = await _reveal_answer(bot, target, thinking, render_mod.clip(result.text))
    # "Exit AI chat" was only ever meant for the wait -- an attachment or the
    # admin trace may yet become the message the rating buttons land on below,
    # so this message must not be left carrying a button of its own.
    await _set_markup(bot, target.chat.id, thinking.message_id, None)
    sent_pdfs, attached = await _attach_sources(bot, target, live, snapshot,
                                                result.sources,
                                                data.get("sent_pdfs", []))
    last = attached or last
    last = await _send_trace(target, principal, result.stats,
                             result.complaints) or last
    await _log_question(bot, live, principal, tg_user, question, result)
    history = result.history

    if asked >= MAX_QUESTIONS:
        # No button to move: the session ends here, so the one it had goes.
        await _close(state, bot, target.chat.id, data)
        await target.answer(_EXHAUSTED)
        return
    await state.update_data(asked=asked, last_at=now(), history=history,
                            sent_pdfs=sent_pdfs)
    await _park_exit_button(bot, target.chat.id, state, last)


@router.message(KbChat.active, F.text & ~F.text.startswith("/"))
async def on_question(message: Message, principal: User, session,
                      state: FSMContext, bot: Bot):
    """One question in an open session.

    Commands are excluded rather than intercepted, so /ask and every other
    command still work while a session is open.
    """
    data = await state.get_data()
    if runtime() is None:
        await _close(state, bot, message.chat.id, data)
        await message.answer(_NOT_CONFIGURED)
        return
    if now() - data.get("last_at", 0.0) > IDLE_SECONDS:
        await _close(state, bot, message.chat.id, data)
        await message.answer(_IDLE)
        return
    await _answer_question(message, principal, state, bot, message.text,
                           message.from_user)
