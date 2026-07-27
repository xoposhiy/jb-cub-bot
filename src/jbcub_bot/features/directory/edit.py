"""The "edit my profile" screen.

One button per editable field. A tap turns this same message into a prompt and
the next text message becomes the value, so the whole flow happens in one
message. Which fields appear, what each prompt asks for and which column a
value lands in all come from `FIELDS` -- this module lists no field names.

Only the caller's own row is ever written, so there is nothing to authorize
beyond being linked.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jbcub_bot.core.models import User
from jbcub_bot.features.directory.render import PROFILE_CALLBACK
from jbcub_bot.features.directory.screens import EMPTY, short_value
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
    rows.append([InlineKeyboardButton(text=_BACK,
                                      callback_data=PROFILE_CALLBACK)])
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
