"""The "edit my profile" screen.

One button per editable field. A tap turns this same message into a prompt and
the next text message becomes the value, so the whole flow happens in one
message. Which fields appear, what each prompt asks for and which column a
value lands in all come from `FIELDS` -- this module lists no field names.

Only the caller's own row is ever written, so there is nothing to authorize
beyond being linked.
"""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.methods import EditMessageText
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.models import User
from jbcub_bot.features.directory import accounts
from jbcub_bot.features.directory.accounts import Verdict
from jbcub_bot.features.directory.render import EDIT_CALLBACK, PROFILE_CALLBACK
from jbcub_bot.features.directory.screens import (
    EMPTY,
    EXPIRED,
    NOT_LINKED,
    UNKNOWN_FIELD,
    require_linked,
    short_value,
)
from jbcub_bot.features.directory.visibility import (
    BY_NAME,
    EDITABLE_FIELDS,
    FieldSpec,
    editable_column,
    field_value,
)

FIELD_CALLBACK_PREFIX = "dir:edit:f:"
CLEAR_CALLBACK_PREFIX = "dir:edit:clear:"
CLEAR_DO_CALLBACK_PREFIX = "dir:edit:clear_do:"
CANCEL_CALLBACK = "dir:edit:cancel"

_HEADER = "Edit your profile"
_BUTTONS_PER_ROW = 2
_BACK = "← Back to profile"


def editable_spec(name: str) -> FieldSpec | None:
    """The field a callback payload names, if its owner may edit it."""
    spec = BY_NAME.get(name)
    return spec if spec is not None and spec.editable else None


def render_edit(user: User, notice: str = "") -> str:
    lines = [notice, ""] if notice else []
    lines += [_HEADER, ""]
    for spec in EDITABLE_FIELDS:
        lines.append(f"{spec.label}: {short_value(field_value(user, spec.name))}")
    return "\n".join(lines)


def edit_keyboard(user: User) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{spec.label} ✏️",
            callback_data=f"{FIELD_CALLBACK_PREFIX}{spec.name}",
        )
        for spec in EDITABLE_FIELDS
    ]
    rows = [buttons[i:i + _BUTTONS_PER_ROW]
            for i in range(0, len(buttons), _BUTTONS_PER_ROW)]
    rows.append([InlineKeyboardButton(
        text=_BACK,
        callback_data=PROFILE_CALLBACK,
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def render_prompt(user: User, spec: FieldSpec) -> str:
    """Ask for a new value, showing the one it would replace.

    Reads the column being written rather than `field_value`: the roster's
    version of a two-source field is not what a new value overwrites, and
    showing it here would suggest otherwise. Not shortened either -- a long
    status is easier to adjust than to retype.
    """
    current = getattr(user, editable_column(spec)) or EMPTY
    return f"{spec.edit_hint}\n\nNow: {current}"


def prompt_keyboard(spec: FieldSpec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="\U0001f5d1 Clear",
                             callback_data=f"{CLEAR_CALLBACK_PREFIX}{spec.name}"),
        InlineKeyboardButton(text="Cancel", callback_data=CANCEL_CALLBACK),
    ]])


def render_clear_confirm(spec: FieldSpec) -> str:
    return (f"Clear your {spec.label}? It disappears from your profile; the "
            "roster's value, if there is one, stays.")


def clear_confirm_keyboard(spec: FieldSpec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"Yes, clear {spec.label}",
            callback_data=f"{CLEAR_DO_CALLBACK_PREFIX}{spec.name}"),
        InlineKeyboardButton(text="Cancel", callback_data=CANCEL_CALLBACK),
    ]])


router = Router(name="directory.edit")
cmd = CommandRegistrar(router)

_NOTHING_TO_CANCEL = "Nothing to cancel."
_CANCELLED = "Editing cancelled."
_STALE_STATE = "That edit screen is from an older version — send /edit again."


class EditProfile(StatesGroup):
    # One state for every field: which field is being edited lives in the FSM
    # data, so adding an editable field adds no state.
    value = State()


async def _redraw(message: Message, data: dict, text: str, keyboard) -> None:
    """Put `text` on the screen the prompt came from, or send a fresh one.

    Goes through bot(EditMessageText(...)) because the value arrives as the
    user's own message -- there is no bot message here to call edit_text on,
    only the chat and message ids stashed when the prompt was drawn.

    That message may be gone (the user deleted it, or the state outlived the
    deploy that stored the ids). Deleting your own message is not a bug worth a
    traceback, so a new screen is sent instead.
    """
    chat_id, message_id = data.get("chat_id"), data.get("message_id")
    if chat_id is not None and message_id is not None:
        try:
            await message.bot(EditMessageText(
                chat_id=chat_id, message_id=message_id,
                text=text, reply_markup=keyboard))
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=keyboard)


@cmd.command("edit", "Edit your status, GitHub or Codeforces.")
async def cmd_edit(message: Message, principal: User, session,
                   state: FSMContext | None = None):
    # `state` is optional because /as reaches this handler through
    # dispatcher.propagate_event("message", ...), which skips the Dispatcher's
    # outer middlewares -- FSMContextMiddleware among them. A required `state`
    # would make every `/as <ref> /edit` a TypeError.
    #
    if state is not None:
        await state.clear()
    await message.answer(
        render_edit(principal),
        reply_markup=edit_keyboard(principal),
    )


@cmd.command("cancel", "Stop editing a profile field.")
async def cmd_cancel(message: Message, principal: User, session,
                     state: FSMContext | None = None):
    if state is None:  # propagated by /as, where no state exists -- see cmd_edit
        await message.answer(_NOTHING_TO_CANCEL)
        return
    data = await state.get_data()
    # Only this feature's own state: another feature may be waiting for text,
    # and clearing that would end its session while showing an edit screen.
    if await state.get_state() != EditProfile.value.state:
        await message.answer(_NOTHING_TO_CANCEL)
        return
    await state.clear()
    await _redraw(message, data, render_edit(principal, _CANCELLED),
                  edit_keyboard(principal))


async def _show_screen(cb: CallbackQuery, user: User, notice: str = "") -> None:
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_edit(user, notice),
                               reply_markup=edit_keyboard(user))
    await cb.answer()


@router.callback_query(F.data == EDIT_CALLBACK)
@require_linked
async def cb_open(cb: CallbackQuery, principal: User, session,
                  state: FSMContext):
    await state.clear()
    await _show_screen(cb, principal)


@router.callback_query(F.data == CANCEL_CALLBACK)
@require_linked
async def cb_cancel(cb: CallbackQuery, principal: User, session,
                    state: FSMContext):
    await state.clear()
    await _show_screen(cb, principal)


@router.callback_query(F.data.startswith(FIELD_CALLBACK_PREFIX))
@require_linked
async def cb_field(cb: CallbackQuery, principal: User, session,
                   state: FSMContext):
    spec = editable_spec(cb.data[len(FIELD_CALLBACK_PREFIX):])
    if spec is None:
        # A keyboard left over from an older deploy, or a hand-crafted payload.
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await state.set_state(EditProfile.value)
    await state.update_data(field=spec.name, chat_id=cb.message.chat.id,
                            message_id=cb.message.message_id)
    await cb.message.edit_text(render_prompt(principal, spec),
                               reply_markup=prompt_keyboard(spec))
    await cb.answer()


@router.message(EditProfile.value, F.text & ~F.text.startswith("/"))
async def on_value(message: Message, principal: User, session,
                   state: FSMContext):
    """Save what the user typed, or explain why it can't be saved.

    Commands are excluded from this handler rather than intercepted, so /cancel
    -- and anything else -- still works while a prompt is open.
    """
    if principal is None or principal.id is None:
        await state.clear()
        await message.answer(NOT_LINKED)
        return
    data = await state.get_data()
    spec = editable_spec(data.get("field", ""))
    if spec is None:
        await state.clear()
        await message.answer(_STALE_STATE)
        return
    try:
        value = accounts.normalize(spec.name, message.text)
    except ValueError as exc:
        await _reprompt(message, data, principal, spec, str(exc))
        return
    verdict = await accounts.verify(spec.name, value)
    if verdict is Verdict.MISSING:
        await _reprompt(message, data, principal, spec,
                        f"{spec.label} has no user {value}.")
        return
    setattr(principal, editable_column(spec), value)
    session.commit()
    await state.clear()
    notice = (f"✅ {spec.label} updated." if verdict is Verdict.EXISTS else
              f"⚠️ Saved. {spec.label} didn't answer, so I couldn't "
              f"verify {value}.")
    await _redraw(message, data, render_edit(principal, notice),
                  edit_keyboard(principal))


async def _reprompt(message: Message, data: dict, user: User, spec: FieldSpec,
                    problem: str) -> None:
    """Say what was wrong and keep asking -- the state stays open."""
    await _redraw(message, data,
                  f"{problem}\n\n{render_prompt(user, spec)}",
                  prompt_keyboard(spec))


@router.callback_query(F.data.startswith(CLEAR_CALLBACK_PREFIX))
@require_linked
async def cb_clear(cb: CallbackQuery, principal: User, session,
                   state: FSMContext):
    """Ask first: removing a value is destructive, however small."""
    spec = editable_spec(cb.data[len(CLEAR_CALLBACK_PREFIX):])
    if spec is None:
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_clear_confirm(spec),
                               reply_markup=clear_confirm_keyboard(spec))
    await cb.answer()


@router.callback_query(F.data.startswith(CLEAR_DO_CALLBACK_PREFIX))
@require_linked
async def cb_clear_do(cb: CallbackQuery, principal: User, session,
                      state: FSMContext):
    spec = editable_spec(cb.data[len(CLEAR_DO_CALLBACK_PREFIX):])
    if spec is None:
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    setattr(principal, editable_column(spec), None)
    session.commit()
    await state.clear()
    await _show_screen(cb, principal, f"✅ {spec.label} cleared.")
