from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jbcub_bot.core.models import User
from jbcub_bot.features.directory.visibility import visible_fields

_LABELS = {
    "name": "Name",
    "role": "Role",
    "primary_cohort": "Cohort",
    "telegram": "Telegram",
    "telegram_id": "Telegram ID",
    "status_line": "Status",
    "gmail": "Gmail",
    "github": "GitHub",
    "codeforces": "Codeforces",
    "matriculation": "Matriculation",
    "birthday": "Birthday",
    "citizenship": "Citizenship",
    "comment": "Comment",
}
_ORDER = ["name", "role", "primary_cohort", "telegram", "telegram_id",
          "status_line", "gmail", "github", "codeforces", "matriculation",
          "birthday", "citizenship", "comment"]


def render_profile(viewer: User, target: User) -> str:
    fields = visible_fields(viewer, target)
    lines = []
    for key in _ORDER:
        if key == "name":  # synthetic: combine first + last into one line
            name = f"{fields.get('first_name') or ''} " \
                   f"{fields.get('last_name') or ''}".strip()
            if name:
                lines.append(f"{_LABELS['name']}: {name}")
            continue
        if key not in fields or fields[key] in (None, ""):
            continue
        value = fields[key]
        if hasattr(value, "value"):  # enum -> its value
            value = value.value
        lines.append(f"{_LABELS[key]}: {value}")
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
