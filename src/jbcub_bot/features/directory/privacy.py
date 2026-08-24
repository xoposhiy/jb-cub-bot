"""The "who sees my data" screen.

One cycling button per configurable field; a tap advances that field's level
and redraws this same message. Only the caller's own row is ever written, so
there is nothing to authorize beyond being linked -- which the contract's
default guard does. What is left here are the two callers being linked cannot
cover: a bootstrap admin whose principal was never saved, and a first-year
whose row is a placeholder the next /sync rebuilds. Both are refused by the tap
that writes and by nothing else.
"""

from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from jbcub_bot.core import identity
from jbcub_bot.core.models import User
from jbcub_bot.features.directory.render import (
    PROFILE_CALLBACK,
    me_keyboard,
    profile_entities,
    render_profile,
)
from jbcub_bot.features.directory.screens import (
    EXPIRED,
    NO_ROW,
    PROVISIONAL,
    UNKNOWN_FIELD,
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

# The button key, without the separator that divides it from the field name:
# the core matches the key and hands the rest over as `arg`.
FIELD_CALLBACK = "dir:vis"

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


def privacy_keyboard(user: User) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{spec.label} {LEVEL_EMOJI[level_of(user, spec.name)]}",
            callback_data=f"{FIELD_CALLBACK}:{spec.name}",
        )
        for spec in CONFIGURABLE_FIELDS
    ]
    rows = [buttons[i:i + _BUTTONS_PER_ROW]
            for i in range(0, len(buttons), _BUTTONS_PER_ROW)]
    rows.append([InlineKeyboardButton(
        text="← Back to profile",
        callback_data=PROFILE_CALLBACK,
    )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def cmd_privacy(message: Message, principal: User, session):
    await message.answer(
        render_privacy(principal),
        reply_markup=privacy_keyboard(principal),
    )


async def _show_privacy(cb: CallbackQuery, principal: User) -> None:
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_privacy(principal),
                               reply_markup=privacy_keyboard(principal))
    await cb.answer()


async def cb_open(cb: CallbackQuery, principal: User, session):
    await _show_privacy(cb, principal)


async def cb_back(cb: CallbackQuery, principal: User, session):
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    text = render_profile(principal, principal)
    await cb.message.edit_text(
        text,
        reply_markup=me_keyboard(principal),
        entities=profile_entities(principal, principal, text),
    )
    await cb.answer()


async def cb_cycle(cb: CallbackQuery, principal: User, session, arg: str):
    # The one place this screen writes, so the one place it has to care that a
    # bootstrap admin's principal was never saved: `identity.apply_bootstrap`
    # attaches it to no session, so the commit below would change nothing while
    # the screen redrew as if it had. Not a contract guard -- that would hide
    # the button from exactly the person who needs to be told.
    if principal.id is None:
        await cb.answer(NO_ROW, show_alert=True)
        return
    # The other row a tap must not write: a placeholder built from the cohort
    # sheet, which the next /sync deletes and builds again. Refused here rather
    # than hidden, for the same reason as above.
    if identity.is_provisional(principal):
        await cb.answer(PROVISIONAL, show_alert=True)
        return
    name = arg
    spec = BY_NAME.get(name)
    if spec is None or spec.category is not Category.CONFIGURABLE:
        # A keyboard left over from an older deploy, or a hand-crafted payload.
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    set_level(principal, name, next_level(level_of(principal, name)))
    session.commit()
    await _show_privacy(cb, principal)
