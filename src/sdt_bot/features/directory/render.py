from sdt_bot.core.models import User
from sdt_bot.features.directory.visibility import visible_fields

_LABELS = {
    "name": "Name",
    "role": "Role",
    "primary_cohort": "Cohort",
    "telegram": "Telegram",
    "status_line": "Status",
    "gmail": "Gmail",
    "github": "GitHub",
    "codeforces": "Codeforces",
    "matriculation": "Matriculation",
}
_ORDER = ["name", "role", "primary_cohort", "telegram", "status_line",
          "gmail", "github", "codeforces", "matriculation"]


def render_profile(viewer: User, target: User) -> str:
    fields = visible_fields(viewer, target)
    lines = []
    for key in _ORDER:
        if key not in fields or fields[key] in (None, ""):
            continue
        value = fields[key]
        if hasattr(value, "value"):  # enum -> its value
            value = value.value
        lines.append(f"{_LABELS[key]}: {value}")
    return "\n".join(lines)
