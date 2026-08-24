"""The "edit my profile" screen.

One button per editable field. A tap turns this same message into a prompt and
the next text message becomes the value, so the whole flow happens in one
message. Which fields appear, what each prompt asks for and which column a
value lands in all come from `FIELDS` -- this module lists no field names.

Only the caller's own row is ever written, so there is nothing to authorize
beyond being linked -- which the contract's default guard does. What is left
here is the callers being linked cannot cover: a bootstrap admin whose
principal was never saved, and a first-year whose row is a placeholder the next
/sync rebuilds. Both are refused where a write happens and nowhere else.
"""

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core import identity
from jbcub_bot.core.dialogs import DialogHandle
from jbcub_bot.core.models import User
from jbcub_bot.features.directory import accounts
from jbcub_bot.features.directory.accounts import Verdict
from jbcub_bot.features.directory.render import PROFILE_CALLBACK
from jbcub_bot.features.directory.screens import (
    EMPTY,
    EXPIRED,
    NO_ROW,
    PROVISIONAL,
    UNKNOWN_FIELD,
    short_value,
)
from jbcub_bot.features.directory.visibility import (
    BY_NAME,
    EDITABLE_FIELDS,
    FieldSpec,
    editable_column,
    field_value,
)

# Button keys, without the separator that divides each from the field name it
# carries: the core matches the longest key and hands the rest over as `arg`,
# which is what lets `dir:edit`, `dir:edit:clear` and `dir:edit:clear_do`
# coexist without any of them swallowing the others.
FIELD_CALLBACK = "dir:edit:f"
CLEAR_CALLBACK = "dir:edit:clear"
CLEAR_DO_CALLBACK = "dir:edit:clear_do"
CANCEL_CALLBACK = "dir:edit:cancel"

# What `bot.dialog(...)` handed back, put here by `register`. A feature never
# writes the state name out -- deriving it is `core/dialogs.state_name`'s job --
# so `cb_field` opens the prompt through this handle instead.
PROMPT: DialogHandle | None = None

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
            callback_data=f"{FIELD_CALLBACK}:{spec.name}",
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
                             callback_data=f"{CLEAR_CALLBACK}:{spec.name}"),
        InlineKeyboardButton(text="Cancel", callback_data=CANCEL_CALLBACK),
    ]])


def render_clear_confirm(spec: FieldSpec) -> str:
    return (f"Clear your {spec.label}? It disappears from your profile; the "
            "roster's value, if there is one, stays.")


def clear_confirm_keyboard(spec: FieldSpec) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text=f"Yes, clear {spec.label}",
            callback_data=f"{CLEAR_DO_CALLBACK}:{spec.name}"),
        InlineKeyboardButton(text="Cancel", callback_data=CANCEL_CALLBACK),
    ]])


_CANCELLED = "Editing cancelled."
_STALE_STATE = "That edit screen is from an older version — send /edit again."


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


async def cmd_edit(message: Message, principal: User, session, dialog):
    await dialog.end()
    await message.answer(
        render_edit(principal),
        reply_markup=edit_keyboard(principal),
    )


async def on_cancel(message: Message, principal: User, dialog):
    """What `/cancel` leaves on the screen. The core owns the command.

    Reads the dialog while it is still open, because the prompt's chat and
    message ids are the only way back to the screen the sender is looking at:
    without them `_redraw` sends a fresh one and the dead prompt stays up with
    live buttons. `/cancel` with nothing open never gets here -- that answer is
    the core's, and it is the same one this feature used to give.
    """
    await _redraw(message, await dialog.data(),
                  render_edit(principal, _CANCELLED), edit_keyboard(principal))


async def _show_screen(cb: CallbackQuery, user: User, notice: str = "") -> None:
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_edit(user, notice),
                               reply_markup=edit_keyboard(user))
    await cb.answer()


async def cb_open(cb: CallbackQuery, principal: User, session, dialog):
    await dialog.end()
    await _show_screen(cb, principal)


async def cb_cancel(cb: CallbackQuery, principal: User, session, dialog):
    await dialog.end()
    await _show_screen(cb, principal)


async def cb_field(cb: CallbackQuery, principal: User, session, dialog,
                   arg: str):
    spec = editable_spec(arg)
    if spec is None:
        # A keyboard left over from an older deploy, or a hand-crafted payload.
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    # Which field is being edited, and where the prompt is drawn, are the whole
    # of the dialog's data -- so adding an editable field adds no state.
    await PROMPT.start(dialog, field=spec.name, chat_id=cb.message.chat.id,
                       message_id=cb.message.message_id)
    await cb.message.edit_text(render_prompt(principal, spec),
                               reply_markup=prompt_keyboard(spec))
    await cb.answer()


async def on_value(message: Message, principal: User, session, dialog):
    """Save what the user typed, or explain why it can't be saved.

    The core routes text here only while this feature's own dialog is open, and
    a command never gets this far -- so nothing has to exclude one.
    """
    # The write this screen exists for, so the one place it has to care that a
    # bootstrap admin's principal was never saved: see `privacy.cb_cycle`.
    if principal.id is None:
        await dialog.end()
        await message.answer(NO_ROW)
        return
    if identity.is_provisional(principal):
        await dialog.end()
        await message.answer(PROVISIONAL)
        return
    data = await dialog.data()
    spec = editable_spec(data.get("field", ""))
    if spec is None:
        await dialog.end()
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
    await dialog.end()
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


async def cb_clear(cb: CallbackQuery, principal: User, session, arg: str):
    """Ask first: removing a value is destructive, however small."""
    spec = editable_spec(arg)
    if spec is None:
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_clear_confirm(spec),
                               reply_markup=clear_confirm_keyboard(spec))
    await cb.answer()


async def cb_clear_do(cb: CallbackQuery, principal: User, session, dialog,
                      arg: str):
    # A write, so the bootstrap admin with no saved row is turned away here
    # too: see `privacy.cb_cycle` for why that is not a contract guard.
    if principal.id is None:
        await cb.answer(NO_ROW, show_alert=True)
        return
    if identity.is_provisional(principal):
        await cb.answer(PROVISIONAL, show_alert=True)
        return
    spec = editable_spec(arg)
    if spec is None:
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    setattr(principal, editable_column(spec), None)
    session.commit()
    await dialog.end()
    await _show_screen(cb, principal, f"✅ {spec.label} cleared.")
