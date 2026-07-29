"""The "who sees my data" screen.

One cycling button per configurable field; a tap advances that field's level
and redraws this same message. Only the caller's own row is ever written, so
there is nothing to authorize beyond being linked.
"""

from aiogram import Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core import impersonation
from jbcub_bot.core.models import User
from jbcub_bot.features.directory.render import (
    PRIVACY_CALLBACK,
    PROFILE_CALLBACK,
    me_keyboard,
    profile_entities,
    render_profile,
)
from jbcub_bot.features.directory.screens import (
    EXPIRED,
    UNKNOWN_FIELD,
    require_linked,
    short_value,
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

FIELD_CALLBACK_PREFIX = "dir:vis:"

_HEADER = "Who sees your data"
_LEGEND = " · ".join(f"{LEVEL_EMOJI[lv]} {LEVEL_LABELS[lv]}" for lv in LEVELS)
_ALWAYS_NOTE = "Name, role and cohort are always visible."
_BUTTONS_PER_ROW = 2


def render_privacy(user: User) -> str:
    lines = [_HEADER, "", _LEGEND, _ALWAYS_NOTE, ""]
    for spec in CONFIGURABLE_FIELDS:
        emoji = LEVEL_EMOJI[level_of(user, spec.name)]
        lines.append(
            f"{emoji} {spec.label}: {short_value(field_value(user, spec.name))}")
    return "\n".join(lines)


def privacy_keyboard(
    user: User, impersonate_ref: str | None = None
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{spec.label} {LEVEL_EMOJI[level_of(user, spec.name)]}",
            callback_data=impersonation.callback_data(
                f"{FIELD_CALLBACK_PREFIX}{spec.name}", impersonate_ref
            ),
        )
        for spec in CONFIGURABLE_FIELDS
    ]
    rows = [buttons[i:i + _BUTTONS_PER_ROW]
            for i in range(0, len(buttons), _BUTTONS_PER_ROW)]
    rows.append([InlineKeyboardButton(
        text="← Back to profile",
        callback_data=impersonation.callback_data(
            PROFILE_CALLBACK, impersonate_ref
        ),
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


router = Router(name="directory.privacy")
cmd = CommandRegistrar(router)


@cmd.command("privacy", "Choose who sees each of your profile fields.")
async def cmd_privacy(message: Message, principal: User, session,
                      impersonate_ref: str | None = None):
    await message.answer(
        render_privacy(principal),
        reply_markup=privacy_keyboard(principal, impersonate_ref),
    )


async def _show_privacy(
    cb: CallbackQuery, principal: User, impersonate_ref: str | None = None
) -> None:
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_privacy(principal),
                               reply_markup=privacy_keyboard(
                                   principal, impersonate_ref
                               ))
    await cb.answer()


@router.callback_query(
    lambda cb: impersonation.split_callback(cb.data)[0] == PRIVACY_CALLBACK
)
@require_linked
async def cb_open(cb: CallbackQuery, principal: User, session,
                  impersonate_ref: str | None = None):
    await _show_privacy(cb, principal, impersonate_ref)


@router.callback_query(
    lambda cb: impersonation.split_callback(cb.data)[0] == PROFILE_CALLBACK
)
@require_linked
async def cb_back(cb: CallbackQuery, principal: User, session,
                  impersonate_ref: str | None = None):
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    text = render_profile(principal, principal)
    await cb.message.edit_text(
        text,
        reply_markup=me_keyboard(
            principal, impersonate_ref=impersonate_ref
        ),
        entities=profile_entities(principal, principal, text),
    )
    await cb.answer()


@router.callback_query(
    lambda cb: impersonation.split_callback(cb.data)[0].startswith(
        FIELD_CALLBACK_PREFIX
    )
)
@require_linked
async def cb_cycle(cb: CallbackQuery, principal: User, session,
                   impersonate_ref: str | None = None):
    payload, _ = impersonation.split_callback(cb.data)
    name = payload[len(FIELD_CALLBACK_PREFIX):]
    spec = BY_NAME.get(name)
    if spec is None or spec.category is not Category.CONFIGURABLE:
        # A keyboard left over from an older deploy, or a hand-crafted payload.
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    set_level(principal, name, next_level(level_of(principal, name)))
    session.commit()
    await _show_privacy(cb, principal, impersonate_ref)
