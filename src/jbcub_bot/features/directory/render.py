from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.visibility import FIELDS, visible_fields

PRIVACY_CALLBACK = "dir:privacy"

# first_name and last_name render as one "Name" line; every other label comes
# from the field table.
_NAME_LABEL = "Name"


def render_profile(viewer: User, target: User) -> str:
    fields = visible_fields(viewer, target)
    lines = []
    for spec in FIELDS:
        if spec.name == "last_name":
            continue  # folded into the Name line below
        if spec.name == "first_name":
            name = f"{fields.get('first_name') or ''} " \
                   f"{fields.get('last_name') or ''}".strip()
            if name:
                lines.append(f"{_NAME_LABEL}: {name}")
            continue
        value = fields.get(spec.name)
        if value in (None, ""):
            continue
        if hasattr(value, "value"):  # enum -> its value
            value = value.value
        lines.append(f"{spec.label}: {value}")
    return "\n".join(lines)


def admin_keyboard(target: User) -> InlineKeyboardMarkup | None:
    if not target.matriculation:
        return None
    m = target.matriculation
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Issue link", callback_data=f"dir:link:{m}"),
        InlineKeyboardButton(text="Reset telegram_id",
                             callback_data=f"dir:reset:{m}"),
    ]])


def me_keyboard(user: User, *,
                allow_privacy: bool = True) -> InlineKeyboardMarkup | None:
    """Keyboard for a user's own profile.

    `allow_privacy=False` is for an impersonated view: the follow-up callback
    would arrive without the impersonation ref, so the admin would edit their
    own settings while looking at someone else's profile.
    """
    rows = []
    if allow_privacy:
        rows.append([InlineKeyboardButton(text="\U0001f512 Who sees my data",
                                          callback_data=PRIVACY_CALLBACK)])
    if user.role is Role.ADMIN:
        admin = admin_keyboard(user)
        if admin is not None:
            rows.extend(admin.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
