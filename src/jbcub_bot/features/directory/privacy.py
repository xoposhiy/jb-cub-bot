"""The "who sees my data" screen.

One cycling button per configurable field; a tap advances that field's level
and redraws this same message. Only the caller's own row is ever written, so
there is nothing to authorize beyond being linked.
"""

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.models import User
from jbcub_bot.features.directory.render import (
    PRIVACY_CALLBACK,
    me_keyboard,
    render_profile,
)
from jbcub_bot.features.directory.visibility import (
    BY_NAME,
    CONFIGURABLE_FIELDS,
    LEVEL_EMOJI,
    LEVEL_LABELS,
    LEVELS,
    Category,
    field_value,
    level_of,
    next_level,
    set_level,
)

BACK_CALLBACK = "dir:profile"
FIELD_CALLBACK_PREFIX = "dir:vis:"

_HEADER = "Who sees your data"
_LEGEND = " · ".join(f"{LEVEL_EMOJI[lv]} {LEVEL_LABELS[lv]}" for lv in LEVELS)
_ALWAYS_NOTE = "Name, role and cohort are always visible."
_EMPTY = "—"
_MAX_VALUE_LEN = 40
_BUTTONS_PER_ROW = 2


def _short(value) -> str:
    if value in (None, ""):
        return _EMPTY
    text = str(value)
    if len(text) <= _MAX_VALUE_LEN:
        return text
    return text[:_MAX_VALUE_LEN - 1] + "…"


def render_privacy(user: User) -> str:
    lines = [_HEADER, "", _LEGEND, _ALWAYS_NOTE, ""]
    for spec in CONFIGURABLE_FIELDS:
        emoji = LEVEL_EMOJI[level_of(user, spec.name)]
        lines.append(f"{emoji} {spec.label}: {_short(field_value(user, spec.name))}")
    return "\n".join(lines)


def privacy_keyboard(user: User) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{spec.label} {LEVEL_EMOJI[level_of(user, spec.name)]}",
            callback_data=f"{FIELD_CALLBACK_PREFIX}{spec.name}",
        )
        for spec in CONFIGURABLE_FIELDS
    ]
    rows = [buttons[i:i + _BUTTONS_PER_ROW]
            for i in range(0, len(buttons), _BUTTONS_PER_ROW)]
    rows.append([InlineKeyboardButton(text="← Back to profile",
                                      callback_data=BACK_CALLBACK)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


router = Router(name="directory.privacy")
cmd = CommandRegistrar(router)

_NOT_LINKED = "You are not linked yet. Contact an admin."


@cmd.command("privacy", "Choose who sees each of your profile fields.")
async def cmd_privacy(message: Message, principal: User, session):
    await message.answer(render_privacy(principal),
                         reply_markup=privacy_keyboard(principal))


async def _show_privacy(cb: CallbackQuery, principal: User) -> None:
    await cb.message.edit_text(render_privacy(principal),
                               reply_markup=privacy_keyboard(principal))
    await cb.answer()


@router.callback_query(F.data == PRIVACY_CALLBACK)
async def cb_open(cb: CallbackQuery, principal: User, session):
    if principal is None:
        await cb.answer(_NOT_LINKED, show_alert=True)
        return
    await _show_privacy(cb, principal)


@router.callback_query(F.data == BACK_CALLBACK)
async def cb_back(cb: CallbackQuery, principal: User, session):
    if principal is None:
        await cb.answer(_NOT_LINKED, show_alert=True)
        return
    await cb.message.edit_text(render_profile(principal, principal),
                               reply_markup=me_keyboard(principal))
    await cb.answer()


@router.callback_query(F.data.startswith(FIELD_CALLBACK_PREFIX))
async def cb_cycle(cb: CallbackQuery, principal: User, session):
    if principal is None:
        await cb.answer(_NOT_LINKED, show_alert=True)
        return
    name = cb.data[len(FIELD_CALLBACK_PREFIX):]
    spec = BY_NAME.get(name)
    if spec is None or spec.category is not Category.CONFIGURABLE:
        # A keyboard left over from an older deploy, or a hand-crafted payload.
        await cb.answer("Unknown field.", show_alert=True)
        return
    set_level(principal, name, next_level(level_of(principal, name)))
    session.commit()
    await _show_privacy(cb, principal)
