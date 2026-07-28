from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.visibility import FIELDS, visible_fields

PRIVACY_CALLBACK = "dir:privacy"
PROFILE_CALLBACK = "dir:profile"
EDIT_CALLBACK = "dir:edit"
ADMIN_CALLBACK = "dir:admin"
ADMIN_BACK_CALLBACK = "dir:admin_back"

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


def admin_row(matriculation: str) -> list[InlineKeyboardButton]:
    """The collapsed entry point: admin actions live one tap away.

    Keeping them behind a button stops a plain profile from looking like a
    control panel — and stops "Reset telegram_id" from sitting under a
    thumb that only wanted to read a phone number.
    """
    return [InlineKeyboardButton(text="🛠 Admin",
                                 callback_data=f"{ADMIN_CALLBACK}:{matriculation}")]


def admin_keyboard(target: User) -> InlineKeyboardMarkup | None:
    if not target.matriculation:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[admin_row(target.matriculation)])


def admin_actions_keyboard(target: User) -> InlineKeyboardMarkup:
    m = target.matriculation
    rows = [[InlineKeyboardButton(text="✉️ Issue Invite",
                                  callback_data=f"dir:link:{m}")]]
    # Nothing to reset on an unlinked profile — the button would only invite a
    # confirmation dialog that then reports success for a no-op.
    if target.telegram_id is not None:
        rows.append([InlineKeyboardButton(text="♻️ Reset telegram_id",
                                          callback_data=f"dir:reset:{m}")])
    rows.append([InlineKeyboardButton(text="⬅️ Back",
                                      callback_data=f"{ADMIN_BACK_CALLBACK}:{m}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def me_keyboard(user: User, *,
                interactive: bool = True) -> InlineKeyboardMarkup | None:
    """Keyboard for a user's own profile.

    `interactive=False` is for an impersonated view: the follow-up callback
    would arrive without the impersonation ref, so the admin would edit their
    own profile while looking at someone else's.
    """
    rows = []
    if interactive:
        rows.append([
            InlineKeyboardButton(text="✏️ Edit my profile",
                                 callback_data=EDIT_CALLBACK),
            InlineKeyboardButton(text="\U0001f512 Who sees my data",
                                 callback_data=PRIVACY_CALLBACK),
        ])
    if user.role is Role.ADMIN:
        admin = admin_keyboard(user)
        if admin is not None:
            rows.extend(admin.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def render_cohort_list(viewer: User, mates: list[User]) -> str:
    """One line per cohort mate, with the handle only when `viewer` may see it.

    Goes through visible_fields rather than reading handle_observed: telegram
    is a configurable field, so this list would otherwise leak a handle its
    owner hid.
    """
    lines = []
    for mate in mates:
        handle = visible_fields(viewer, mate).get("telegram")
        name = mate.full_name
        lines.append(f"- {name} ({handle})" if handle else f"- {name}")
    return "\n".join(lines)
