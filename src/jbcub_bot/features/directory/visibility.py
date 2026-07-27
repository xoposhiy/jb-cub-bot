"""Who may see which profile field.

`FIELDS` is the single source of truth for the profile: which fields exist,
what they are called, which category they fall in, and (for configurable ones)
who sees them until their owner says otherwise. Every reader -- this module's
`visible_fields`, the profile renderer, the privacy screen -- derives its
behaviour from that table, so adding a field is one line in one place.
"""

import enum
from dataclasses import dataclass

from jbcub_bot.core.models import Role, User


class Category(enum.Enum):
    ALWAYS = "always"              # unhideable: every linked user sees it
    CONFIGURABLE = "configurable"  # the owner chooses who sees it
    ADMIN_ONLY = "admin_only"      # admins only -- the owner is not told it exists


# Levels, narrowest first: staff_only < cohort < everyone. `staff_only` is not
# called `nobody` because program staff see the field regardless, and a level
# that lies to the person choosing it is worse than a longer name.
STAFF_ONLY = "staff_only"
COHORT = "cohort"
EVERYONE = "everyone"

LEVELS = (STAFF_ONLY, COHORT, EVERYONE)
LEVEL_LABELS = {STAFF_ONLY: "Staff only", COHORT: "My cohort", EVERYONE: "Everyone"}
LEVEL_EMOJI = {STAFF_ONLY: "\U0001f512", COHORT: "\U0001f465", EVERYONE: "\U0001f310"}

# Written by versions that predate the rename. Read, never written.
_LEGACY_LEVELS = {"nobody": STAFF_ONLY, "all_students": EVERYONE}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    category: Category
    default: str | None = None  # CONFIGURABLE only


# Order here is the order the profile renders in.
FIELDS = (
    FieldSpec("first_name", "First name", Category.ALWAYS),
    FieldSpec("last_name", "Last name", Category.ALWAYS),
    FieldSpec("role", "Role", Category.ALWAYS),
    FieldSpec("primary_cohort", "Cohort", Category.ALWAYS),
    FieldSpec("telegram", "Telegram", Category.CONFIGURABLE, EVERYONE),
    FieldSpec("telegram_id", "Telegram ID", Category.ADMIN_ONLY),
    FieldSpec("status_line", "Status", Category.CONFIGURABLE, EVERYONE),
    FieldSpec("gmail", "Gmail", Category.CONFIGURABLE, COHORT),
    FieldSpec("github", "GitHub", Category.CONFIGURABLE, COHORT),
    FieldSpec("codeforces", "Codeforces", Category.CONFIGURABLE, COHORT),
    FieldSpec("matriculation", "Matriculation", Category.ADMIN_ONLY),
    FieldSpec("birthday", "Birthday", Category.ADMIN_ONLY),
    FieldSpec("citizenship", "Citizenship", Category.ADMIN_ONLY),
    FieldSpec("comment", "Comment", Category.ADMIN_ONLY),
)

BY_NAME = {spec.name: spec for spec in FIELDS}
CONFIGURABLE_FIELDS = tuple(
    spec for spec in FIELDS if spec.category is Category.CONFIGURABLE
)


def _cohorts(u: User) -> set:
    cohorts = set(u.past_cohorts or [])
    if u.primary_cohort:
        cohorts.add(u.primary_cohort)
    return cohorts


def are_cohort_mates(a: User, b: User) -> bool:
    return bool(_cohorts(a) & _cohorts(b))


def field_value(user: User, name: str):
    """The displayable value of a field.

    `telegram` is the one field that isn't a column: it picks the observed
    handle over the sheet's hint and prefixes the @.
    """
    if name == "telegram":
        handle = user.handle_observed or user.handle_sheet
        return f"@{handle}" if handle else None
    return getattr(user, name)


def level_of(user: User, name: str) -> str:
    """The field's effective level for `user`: their choice, else the default."""
    spec = BY_NAME[name]
    stored = (user.visibility or {}).get(name)
    level = _LEGACY_LEVELS.get(stored, stored)
    return level if level in LEVELS else spec.default


def next_level(level: str) -> str:
    return LEVELS[(LEVELS.index(level) + 1) % len(LEVELS)]


def set_level(user: User, name: str, level: str) -> None:
    """Record an explicit choice.

    Stores the level even when it equals the current default: the default is a
    code constant that may change, and a deliberate choice must outlive it.

    Reassigns the dict rather than mutating it -- `visibility` is a plain JSON
    column, so an in-place `d[k] = v` leaves the instance clean and the commit
    writes nothing.
    """
    updated = dict(user.visibility or {})
    updated[name] = level
    user.visibility = updated


def _is_self(viewer: User, target: User) -> bool:
    """Same person? Compare identity first, then primary keys.

    The `is not None` guard is load-bearing: unsaved User objects all have
    `id is None`, so comparing ids alone would treat any two of them as the
    same person and hand out everything.
    """
    if viewer is target:
        return True
    return viewer.id is not None and viewer.id == target.id


def visible_fields(viewer: User, target: User) -> dict:
    """Every field of `target` that `viewer` may see, keyed by field name.

    A key may map to None -- callers decide whether to render an empty value.
    """
    is_admin = viewer.role is Role.ADMIN
    is_staff = is_admin or viewer.role is Role.TEACHER
    own = _is_self(viewer, target)
    mates = are_cohort_mates(viewer, target)

    fields: dict = {}
    for spec in FIELDS:
        if spec.category is Category.ADMIN_ONLY:
            if not is_admin:
                continue
        elif spec.category is Category.CONFIGURABLE and not (own or is_staff):
            # Levels govern student-to-student visibility only; staff and the
            # owner are past this gate already.
            level = level_of(target, spec.name)
            if level == STAFF_ONLY:
                continue
            if level == COHORT and not mates:
                continue
        fields[spec.name] = field_value(target, spec.name)
    return fields
