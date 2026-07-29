from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity

from jbcub_bot.core import impersonation, sheets
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.visibility import (
    BY_NAME,
    FIELDS,
    is_staff,
    visible_fields,
)

PRIVACY_CALLBACK = "dir:privacy"
PROFILE_CALLBACK = "dir:profile"
EDIT_CALLBACK = "dir:edit"
ADMIN_CALLBACK = "dir:admin"
ADMIN_BACK_CALLBACK = "dir:admin_back"
GRADES_CALLBACK = "dir:grades"
GRADES_BACK_CALLBACK = "dir:grades_back"

# first_name and last_name render as one "Name" line; every other label comes
# from the field table.
_NAME_LABEL = "Name"
_SOURCE_LABEL = "Source"
_RIGHTS_SHEET_LABEL = "Rights sheet"


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
        if spec.name == "source_link":
            continue
        if spec.name == "primary_cohort":
            value = fields.get("primary_cohort")
            if value:
                lines.append(f"{spec.label}: {value}")
            elif "source_link" in fields:
                lines.append(f"{_SOURCE_LABEL}: {_RIGHTS_SHEET_LABEL}")
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


def grades_row(matriculation: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(
        text="📊 Grades",
        callback_data=f"{GRADES_CALLBACK}:{matriculation}:-1",
    )]


def profile_keyboard(
    viewer: User, target: User, *, show_grades: bool
) -> InlineKeyboardMarkup | None:
    """Keyboard for a profile rendered for someone other than the viewer."""
    rows = []
    if show_grades and is_staff(viewer) and target.matriculation:
        rows.append(grades_row(target.matriculation))
    if viewer.role is Role.ADMIN:
        admin = admin_keyboard(target)
        if admin is not None:
            rows.extend(admin.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def profile_entities(
    viewer: User, target: User, text: str
) -> list[MessageEntity]:
    """Return the admin-only hyperlink covering this profile's source label."""
    if viewer.role is not Role.ADMIN or not target.source_link:
        return []
    if target.primary_cohort:
        marker = f"{BY_NAME['primary_cohort'].label}: "
        value = target.primary_cohort
    else:
        marker = f"{_SOURCE_LABEL}: "
        value = _RIGHTS_SHEET_LABEL
    line = marker + value
    if line not in text:
        return []
    index = text.index(line)
    return [MessageEntity(
        type="text_link",
        offset=_utf16_len(text[:index] + marker),
        length=_utf16_len(value),
        url=sheets.sheet_url(target.source_link),
    )]


def invite_row(matriculation: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="✉️ Issue Invite",
                                 callback_data=f"dir:link:{matriculation}")]


def admin_actions_keyboard(target: User) -> InlineKeyboardMarkup:
    """The one action that applies, plus Back.

    Linking is exclusive: an invite for a profile that already has a
    telegram_id would hand it to whoever taps the link and drop the current
    holder without a word. So a linked profile only offers Reset — which asks
    for confirmation and says what it does — and the invite appears once the
    profile is free. Nothing to reset on an unlinked profile either, so the
    two are never on screen together.
    """
    m = target.matriculation
    if target.telegram_id is None:
        rows = [invite_row(m)]
    else:
        rows = [[InlineKeyboardButton(text="♻️ Reset telegram_id",
                                      callback_data=f"dir:reset:{m}")]]
    rows.append([InlineKeyboardButton(text="⬅️ Back",
                                      callback_data=f"{ADMIN_BACK_CALLBACK}:{m}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def me_keyboard(user: User, *,
                impersonate_ref: str | None = None) -> InlineKeyboardMarkup | None:
    """Keyboard for a user's own profile.

    Impersonated buttons carry their target so every follow-up resolves the
    same student instead of falling back to the admin who tapped them.
    """
    rows = []
    rows.append([
        InlineKeyboardButton(
            text="✏️ Edit my profile",
            callback_data=impersonation.callback_data(
                EDIT_CALLBACK, impersonate_ref
            ),
        ),
        InlineKeyboardButton(
            text="\U0001f512 Who sees my data",
            callback_data=impersonation.callback_data(
                PRIVACY_CALLBACK, impersonate_ref
            ),
        ),
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
    owner hid. `departed_at` comes through the same gate, so the mark appears
    for the admin who was shown the person and for nobody else -- an unmarked
    line would read as "still in the cohort".
    """
    lines = []
    for mate in mates:
        fields = visible_fields(viewer, mate)
        line = f"- {mate.full_name}"
        if fields.get("telegram"):
            line += f" ({fields['telegram']})"
        if fields.get("departed_at"):
            line += f" — ⚠️ departed {fields['departed_at']}"
        lines.append(line)
    return "\n".join(lines)
