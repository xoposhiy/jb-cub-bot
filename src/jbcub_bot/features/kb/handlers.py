"""The Telegram face of the knowledge base: one chain slot, /ask and /kb_reload.

There is no mode to be in and no button in between. The roster search runs
first on every free-text message -- `directory` at `pipeline.LOOKUP` -- and
this feature sits behind it at `pipeline.AGENT`, so a name shows a person and
everything else becomes a question, whether or not a conversation is already
going. The one case code cannot separate is a name the roster does not carry,
which looks exactly like a one-word question; that one just reaches the agent
as a question too, and `search_people` telling it nobody matches is what it
answers back with -- there is no code-level decline for it to detour through.

A question that names a specific person, rather than a bare name, reaches the
agent the same way. `search_people` and `show_profile` let it find and show a
profile itself: this feature renders and sends the profile it picked and drops
the conversation, the same way a bare name found on the chain would have.

Both refusals on the chain -- an unconfigured runtime, an exhausted hourly
budget -- decline rather than explain. Someone who mistyped a surname is not
helped by "the knowledge base is busy"; they get "No one found." and the admins
get the ping. `/ask` is explicit, so it gets the honest reason.
"""
from __future__ import annotations

import logging
import time
from collections import deque

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core import oplog as oplog_mod
from jbcub_bot.core.config import get_settings
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.pipeline import NOTHING_MATCHED
from jbcub_bot.features.directory import grades
from jbcub_bot.features.directory.handlers import is_admin
from jbcub_bot.features.directory.render import (
    profile_entities,
    profile_keyboard,
    render_profile,
)
from jbcub_bot.features.help.render import render_help
from jbcub_bot.features.kb import history
from jbcub_bot.features.kb import pdf as pdf_mod
from jbcub_bot.features.kb import render as render_mod
from jbcub_bot.features.kb.agent import (
    KbRuntime,
    ask,
    build_runtime,
)

FEATURE = "kb"

# One key with the verdict as its payload, so `core/buttons.py` splits it.
RATE_CALLBACK = "kb:rate"
GOOD_TEXT = "👍"
BAD_TEXT = "👎"

_NOT_CONFIGURED = ("Knowledge base search is not configured on this bot. "
                   "An admin needs to set KB_LLM_API_KEY.")
# Neutral on purpose: it is edited into the answer or into "No one found.", and
# somebody who typed a surname should not be told an AI is thinking about it.
_LOOKING = "🔎 Looking…"
_ASK_ALONE = ("Ask me anything about the program — just type it, or put the "
              "question right after /ask.")
_RATE_LIMITED = ("The knowledge base is getting a lot of questions right now — "
                 "try again in a few minutes.")
# What the placeholder becomes once show_profile picked somebody -- the
# profile itself follows as its own message, so this is never the last word.
# The tip only fires here, on the slow agent path: a bare name never reaches
# the agent at all, so this is the one place worth teaching that shortcut.
_FOUND_SOMEONE = ("🔎 Found them — see below.\n\n"
                  "💡 Tip: next time, just send a name or Telegram handle — "
                  "it skips the AI and answers faster.")


logger = logging.getLogger(__name__)


def now() -> float:
    """Wall clock, in one place so a test can move it."""
    return time.time()


async def _answer_html(message: Message, text: str):
    """Send as HTML; on a parse failure send the same words with no markup.

    Telegram rejects a whole message over one bad tag. Losing the answer to a
    stray `</b>` would be far worse than losing the bold. Returns whichever
    message landed, so the caller can hang the rating buttons off it.
    """
    try:
        return await message.answer(text, parse_mode="HTML")
    except TelegramBadRequest:
        logger.warning("Telegram rejected an HTML answer; retrying as plain")
        return await message.answer(render_mod.plain(text))


async def _reveal(bot: Bot, target: Message, placeholder: Message, text: str):
    """Turn "🔎 Looking…" into whatever came back, in place.

    Editing it rather than sending a second message keeps the placeholder from
    lingering in the chat once there is something to read. Bad markup gets the
    same plain-text retry `_answer_html` uses; if the edit itself fails -- the
    placeholder was deleted, say -- a fresh message still gets the answer
    through.
    """
    chat_id, message_id = target.chat.id, placeholder.message_id
    try:
        return await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                           text=text, parse_mode="HTML")
    except TelegramBadRequest:
        logger.warning("Telegram rejected an HTML edit; retrying as plain")
    except TelegramAPIError:
        logger.warning("could not edit the Looking placeholder", exc_info=True)
        return await _answer_html(target, text)
    try:
        return await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                           text=render_mod.plain(text))
    except TelegramAPIError:
        logger.warning("could not edit the Looking placeholder as plain text",
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
    """Give the reader each source the agent named, once per conversation.

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


async def _show_profile(bot: Bot, target: Message, looking: Message,
                        principal: User, session, live, tg_user,
                        impersonator, question: str, result) -> None:
    """Render the profile show_profile picked, and drop the conversation.

    A second message rather than an edit of the placeholder: a profile carries
    a keyboard and a source hyperlink, and Telegram takes those through
    `reply_markup` and `entities`, not through the parse mode `_reveal` edits
    the placeholder with.
    """
    chat_id = target.chat.id
    person = session.get(User, result.profile_id)
    if person is None:
        # The row was gone by the time this ran -- deleted between the tool
        # call and here. Nothing to show; the honest answer is the one a
        # roster miss already gives.
        await _reveal(bot, target, looking, NOTHING_MATCHED)
        history.drop(chat_id)
        await _log_miss(bot, live, principal, tg_user, impersonator, question,
                        result)
        return
    await _reveal(bot, target, looking, _FOUND_SOMEONE)
    show_grades = grades.has_grades(session, person.id)
    text = render_profile(principal, person)
    await target.answer(
        text,
        reply_markup=profile_keyboard(principal, person, show_grades=show_grades),
        entities=profile_entities(principal, person, text),
    )
    history.drop(chat_id)
    await _log_profile_shown(bot, live, principal, tg_user, question, person,
                             result)


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


# What `register` hands over, and the only thing this feature knows about the
# rest of the bot. Asked one question -- what took the last message in this
# chat -- which is how a conversation learns a profile was shown in between two
# of its questions without `kb` knowing that `directory` exists.
_registry = None


def set_registry(value) -> None:
    global _registry
    _registry = value


def _subject_changed(chat_id: int) -> bool:
    """Whether something other than this feature answered the last message.

    A command or a dialog taking it reads as no taker at all, and that is
    deliberate: `/ask` is a command, and it has already dropped the
    conversation on its own terms by the time anything reads this.
    """
    if _registry is None:
        return False
    taker = _registry.last_taker(chat_id)
    return taker is not None and taker.feature != FEATURE


# When each of the last window's questions went to the agent. Process-wide
# like the runtime above, and for the same reason: there is one bot, so one
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


def describe_bot(principal) -> str:
    """/help rendered for this same caller, so the agent can answer "what can
    you do?" from the bot's real commands rather than a guess.

    Empty before `set_registry` has run -- a test that builds the agent
    without one gets the same prompt as before this existed.
    """
    if _registry is None:
        return ""
    return render_help(_registry.features(), principal)


# --- the rating pair ----------------------------------------------------------
# It rates the last answer and does nothing else -- there is no session for it
# to close any more. Still one pair in the chat, walking forward under the
# newest thing the bot said, because an inline button scrolls away with its
# message and drawing a fresh pair every time would fill the chat with them.

# Where that pair is sitting, per chat. Outside the conversation on purpose:
# dropping a conversation must not leave a second live pair behind, and the
# rating is about the answer on the screen rather than about the thread.
_RATING_AT: dict[int, int] = {}
_RATING_MAX = 500


def reset_ratings() -> None:
    _RATING_AT.clear()


def _rating_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=GOOD_TEXT,
                             callback_data=f"{RATE_CALLBACK}:good"),
        InlineKeyboardButton(text=BAD_TEXT,
                             callback_data=f"{RATE_CALLBACK}:bad"),
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
        logger.debug("could not move the knowledge base rating buttons",
                     exc_info=True)
        return False
    return True


async def _park_rating(bot, chat_id: int, message) -> None:
    """Move the one rating pair so that it sits under `message`."""
    new_id = getattr(message, "message_id", 0) or 0
    previous = _RATING_AT.get(chat_id, 0)
    if not new_id or new_id == previous:
        return
    if not await _set_markup(bot, chat_id, new_id, _rating_keyboard()):
        return  # the old pair is still live; better there than nowhere
    if previous:
        await _set_markup(bot, chat_id, previous, None)
    if len(_RATING_AT) >= _RATING_MAX:
        # A convenience, not a record: whichever chats lose their position get
        # a fresh pair under their next answer rather than a moved one.
        _RATING_AT.clear()
    _RATING_AT[chat_id] = new_id


async def _log_feedback(bot, good: bool, principal, tg_user) -> None:
    """Put the reader's rating of the last answer in the ops chat.

    A rating is worth nothing without a chat to read it in, so this shares the
    same destination as every question -- one feed, not a second inbox to
    remember to check.
    """
    live = runtime()
    if live is None or _is_bootstrap_admin(live, tg_user):
        return
    log = oplog_mod.OpsLog(bot, live.log_chat_id, live.admin_ids)
    await log.send(oplog_mod.format_kb_feedback(good, principal, tg_user))


async def cb_rate(cb: CallbackQuery, principal: User, bot: Bot, arg: str):
    """A tap on ✅ or ❌. Logs it, takes the pair down, and nothing else.

    The conversation is untouched: rating the answer on the screen is not a way
    of saying the subject has changed.
    """
    good = arg == "good"
    if isinstance(cb.message, Message):
        await _set_markup(bot, cb.message.chat.id, cb.message.message_id, None)
        _RATING_AT.pop(cb.message.chat.id, None)
    await _log_feedback(bot, good, principal, cb.from_user)
    await cb.answer("Thanks.")


# --- what the ops chat is told -------------------------------------------------

def _is_bootstrap_admin(live, tg_user) -> bool:
    """Whether the real sender is a bootstrap admin poking at the bot.

    The ops chat is for watching what everybody else asks; a bootstrap admin
    debugging or experimenting would otherwise read their own traffic back to
    themselves on every question. `/as` does not change who this is, since it
    only relabels the principal, not who actually sent the message.
    """
    return tg_user is not None and tg_user.id in live.admin_ids


async def _log_question(bot, live, principal, tg_user, question,
                        result) -> None:
    """Put the question, and what it cost, in the ops chat.

    Sent after the answer, for the same reason the admin trace is: whoever
    asked is waiting on the answer, and a report is never worth delaying it.
    `OpsLog` swallows its own delivery failures, so there is nothing to guard.
    """
    if _is_bootstrap_admin(live, tg_user):
        return
    head = oplog_mod.format_kb_question(question, principal, tg_user)
    await _send_with_trace(bot, live, head, result)


async def _log_miss(bot, live, principal, tg_user, impersonator, question,
                    result) -> None:
    """The name verdict, in the same feed the core's last word writes to.

    The reader saw "No one found.", which is exactly what text nothing matched
    has always produced, so the entry reads the same -- what is added is the
    run's cost, because a miss that spent a model turn is the one worth
    counting.
    """
    if _is_bootstrap_admin(live, tg_user):
        return
    head = oplog_mod.format_miss(query=question, answer=NOTHING_MATCHED,
                                 principal=principal, tg_user=tg_user,
                                 impersonator=impersonator)
    await _send_with_trace(bot, live, head, result)


async def _log_profile_shown(bot, live, principal, tg_user, question,
                             person: User, result) -> None:
    """search_people and show_profile, in the same feed a question costs.

    A hit rather than a miss, but the same reasoning as `_log_miss`: the
    reader already has their answer, and this is where whoever runs the bot
    finds out the agent spent a turn finding somebody rather than answering
    from the base.
    """
    if _is_bootstrap_admin(live, tg_user):
        return
    head = oplog_mod.format_kb_person_found(question, person.full_name,
                                            principal, tg_user)
    await _send_with_trace(bot, live, head, result)


async def _send_with_trace(bot, live, head: str, result) -> None:
    room = render_mod.CLIP_LIMIT - len(head) - 1
    trace = render_mod.trace_message(result.stats, result.complaints,
                                     limit=room)
    log = oplog_mod.OpsLog(bot, live.log_chat_id, live.admin_ids)
    await log.send(f"{head}\n{trace}")


async def _log_rate_limit(bot, live, principal, tg_user) -> None:
    """Ping the admins by name: the hourly AI budget just ran out.

    Nobody is meant to hit this in normal use, so it gets the same treatment
    as a crash -- a mention, not just another line in the question feed --
    because someone should go find out why rather than let it pass. It matters
    more now than it did: on the chain the reader is told nothing at all, so
    this entry is the only place an exhausted budget shows up.
    """
    ping, entities = oplog_mod.admin_mention(live.admin_ids)
    prefix = f"{ping}\n" if ping else ""
    text = prefix + oplog_mod.format_kb_rate_limited(live.rate_limit, principal,
                                                     tg_user)
    log = oplog_mod.OpsLog(bot, live.log_chat_id, live.admin_ids)
    await log.send(text, entities=entities)


# --- the two ways in -----------------------------------------------------------

async def answer_question(message: Message, principal: User, bot: Bot,
                          session, impersonator) -> bool:
    """The chain slot at `AGENT`: whatever the roster search declined.

    `False` back means nothing here answered and the core's last word gets to,
    which is what both refusals want: "No one found." is the right thing to
    tell somebody who mistyped a surname, and explaining the knowledge base to
    them would answer a question they never asked.
    """
    live = runtime()
    if live is None:
        return False
    chat_id = message.chat.id
    if _subject_changed(chat_id):
        history.drop(chat_id)
    if _budget_spent(live.rate_limit, live.rate_window_seconds):
        await _log_rate_limit(bot, live, principal, message.from_user)
        return False
    await _put(message, principal, bot, session, live, message.text,
               message.from_user, impersonator)
    return True


async def cmd_ask(message: Message, principal: User, bot: Bot, session,
                  arg: str, impersonator):
    """Start clean, and say the honest reason when nothing runs.

    Unlike the chain, which stays silent and lets the core's own "No one
    found." speak instead, this was asked outright: an unconfigured runtime or
    an exhausted budget gets the real reason rather than a refusal that reads
    like a roster miss.
    """
    live = runtime()
    if live is None:
        await message.answer(_NOT_CONFIGURED)
        return
    history.drop(message.chat.id)
    question = arg.strip()
    if not question:
        await message.answer(_ASK_ALONE)
        return
    if _budget_spent(live.rate_limit, live.rate_window_seconds):
        # Told outright rather than declined: this caller asked the knowledge
        # base a question in so many words, so the honest reason is the useful
        # answer.
        await message.answer(_RATE_LIMITED)
        await _log_rate_limit(bot, live, principal, message.from_user)
        return
    await _put(message, principal, bot, session, live, question,
               message.from_user, impersonator)


async def cmd_kb_reload(message: Message, principal: User):
    live = runtime()
    if live is None:
        await message.answer(_NOT_CONFIGURED)
        return
    snapshot = await live.store.get(force=True)
    await message.answer(
        f"Knowledge base reloaded: {len(snapshot.notes)} notes at "
        f"{snapshot.sha[:7]}."
    )


async def _put(target: Message, principal: User, bot: Bot, session, live,
               question: str, tg_user, impersonator) -> None:
    """One question to the agent, and whatever it says back."""
    chat_id = target.chat.id
    chat = history.conversation(chat_id)
    looking = await target.answer(_LOOKING)
    snapshot = await live.store.get()
    result = await ask(live.agent, snapshot, question, history.as_input(chat),
                       about=describe_asker(principal),
                       help_text=describe_bot(principal), session=session,
                       include_departed=is_admin(principal))

    if result.profile_id is not None:
        await _show_profile(bot, target, looking, principal, session, live,
                            tg_user, impersonator, question, result)
        return

    # The agent's own words, clipped only if it wrote past what Telegram takes.
    # Each of these may or may not be the last word of the exchange; the rating
    # pair goes under whichever one actually was.
    last = await _reveal(bot, target, looking, render_mod.clip(result.text))
    chat.sent_sources, attached = await _attach_sources(
        bot, target, live, snapshot, result.sources, chat.sent_sources)
    last = attached or last
    last = await _send_trace(target, principal, result.stats,
                             result.complaints) or last
    await _log_question(bot, live, principal, tg_user, question, result)
    history.remember(chat_id, result.question, result.text)
    await _park_rating(bot, chat_id, last)
