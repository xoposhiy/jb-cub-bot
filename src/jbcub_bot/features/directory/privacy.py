"""The "who sees my data" screen.

One cycling button per configurable field; a tap advances that field's level
and redraws this same message. Only the caller's own row is ever written, so
there is nothing to authorize beyond being linked.
"""

import functools

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
_NO_ROW = "Your account has no saved profile yet. Ask an admin to link you."
_EXPIRED = "This screen expired — send /privacy again."


def _require_linked(fn):
    """Wrap a callback handler so it refuses an unusable caller before running.

    Mirrors CommandRegistrar._guard in core/commands.py. Uses functools.wraps
    so aiogram unwraps __wrapped__ and injects the original handler's
    declared params (principal, session, ...); guarded handlers must declare
    `principal`.

    Two distinct "not usable yet" cases: no principal at all (unlinked), and
    a bootstrap admin whose principal is a transient row never written to the
    database (`id is None` -- see identity.apply_bootstrap). The latter must
    not be silently materialized into a real row just because a button was
    tapped, so it gets refused here rather than persisted.
    """
    @functools.wraps(fn)
    async def wrapper(cb: CallbackQuery, **kwargs):
        principal: User | None = kwargs.get("principal")
        if principal is None:
            await cb.answer(_NOT_LINKED, show_alert=True)
            return
        if principal.id is None:
            await cb.answer(_NO_ROW, show_alert=True)
            return
        return await fn(cb, **kwargs)

    return wrapper


@cmd.command("privacy", "Choose who sees each of your profile fields.")
async def cmd_privacy(message: Message, principal: User, session, impersonator=None):
    # Mirrors cmd_me: under /as the callback would arrive without the
    # impersonation ref and land on the admin's own row, so show the
    # target's screen with nothing tappable instead of a live keyboard.
    await message.answer(
        render_privacy(principal),
        reply_markup=None if impersonator is not None else privacy_keyboard(principal),
    )


async def _show_privacy(cb: CallbackQuery, principal: User) -> None:
    if not isinstance(cb.message, Message):
        await cb.answer(_EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_privacy(principal),
                               reply_markup=privacy_keyboard(principal))
    await cb.answer()


@router.callback_query(F.data == PRIVACY_CALLBACK)
@_require_linked
async def cb_open(cb: CallbackQuery, principal: User, session):
    await _show_privacy(cb, principal)


@router.callback_query(F.data == BACK_CALLBACK)
@_require_linked
async def cb_back(cb: CallbackQuery, principal: User, session):
    if not isinstance(cb.message, Message):
        await cb.answer(_EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_profile(principal, principal),
                               reply_markup=me_keyboard(principal))
    await cb.answer()


@router.callback_query(F.data.startswith(FIELD_CALLBACK_PREFIX))
@_require_linked
async def cb_cycle(cb: CallbackQuery, principal: User, session):
    name = cb.data[len(FIELD_CALLBACK_PREFIX):]
    spec = BY_NAME.get(name)
    if spec is None or spec.category is not Category.CONFIGURABLE:
        # A keyboard left over from an older deploy, or a hand-crafted payload.
        await cb.answer("Unknown field.", show_alert=True)
        return
    set_level(principal, name, next_level(level_of(principal, name)))
    session.commit()
    await _show_privacy(cb, principal)
