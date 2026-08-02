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

import time

from aiogram import Bot, F, Router
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
from jbcub_bot.features.kb.agent import (
    KbRuntime,
    ask,
    build_runtime,
    render_answer,
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


def now() -> float:
    """Wall clock, in one place so a test can move it."""
    return time.time()


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
    await state.set_data({"asked": 0, "last_at": now(), "history": []})


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
                   state: FSMContext):
    if principal is None or principal.role is Role.STUDENT:
        await cb.answer("Staff only.", show_alert=True)
        return
    if runtime() is None:
        await cb.answer(_NOT_CONFIGURED, show_alert=True)
        return
    await _open(state)
    if isinstance(cb.message, Message):
        await cb.message.answer(_OPENED, reply_markup=_session_keyboard())
    await cb.answer()


@router.callback_query(F.data == EXIT_CALLBACK)
async def cb_exit(cb: CallbackQuery, principal: User, session,
                  state: FSMContext, bot: Bot):
    data = await state.get_data()
    await _close(state, bot, principal, cb.from_user, data.get("asked", 0))
    if isinstance(cb.message, Message):
        await cb.message.answer(_CLOSED)
    await cb.answer()


@router.message(KbChat.active, F.text & ~F.text.startswith("/"))
async def on_question(message: Message, principal: User, session,
                      state: FSMContext, bot: Bot):
    """One question in an open session.

    Commands are excluded rather than intercepted, so /ask and every other
    command still work while a session is open.
    """
    live = runtime()
    if live is None:  # redeployed without the settings while a session was open
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

    await message.answer(_THINKING)
    snapshot = await live.store.get()
    answer, history = await ask(live.agent, snapshot, message.text,
                                data.get("history", []))
    asked = data.get("asked", 0) + 1
    await message.answer(render_answer(answer, live.repo, snapshot.sha))

    if asked >= MAX_QUESTIONS:
        await _close(state, bot, principal, message.from_user, asked)
        await message.answer(_EXHAUSTED)
        return
    await state.update_data(asked=asked, last_at=now(), history=history)
