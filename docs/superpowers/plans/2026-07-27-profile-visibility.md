# Self-Service Profile Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user choose who sees each of their configurable profile fields, from an inline screen reached from `/me` or `/privacy`.

**Architecture:** Collapse the profile-field metadata that is currently spread across `visibility.py` and `render.py` into one ordered `FIELDS` table of `FieldSpec(name, label, category, default)`. Every reader — the visibility service, the profile renderer, the new settings screen — derives its behaviour from that table. The settings screen lives in a new `features/directory/privacy.py` with its own child router; its buttons cycle one field's level per tap and redraw the same message in place.

**Tech Stack:** Python 3.12, aiogram 3.x, SQLAlchemy 2.x (SQLite), pytest + pytest-asyncio (`asyncio_mode = "auto"`), `uv` for everything.

**Spec:** `docs/superpowers/specs/2026-07-27-profile-visibility-design.md` — read it first.

## Global Constraints

- **All user-facing strings are English.** The bot is English throughout; no Russian in code or output.
- **Levels are exactly** `staff_only`, `cohort`, `everyone`, cycled in that order. Labels: `Staff only`, `My cohort`, `Everyone`. Emoji: 🔒, 👥, 🌐.
- **Per-field defaults:** `telegram` → `everyone`, `status_line` → `everyone`, `gmail`/`github`/`codeforces` → `cohort`. Never a single global default.
- **Legacy stored values are read tolerantly**: `nobody` → `staff_only`, `all_students` → `everyone`. Never written.
- **`users.visibility` is bot-owned** and must survive `/sync`. No migration is needed or allowed — the column already exists.
- **`user.visibility` must be reassigned, never mutated in place.** The column is plain `JSON`, not `MutableDict`, so SQLAlchemy will not notice `d[k] = v` on the existing dict and the commit silently does nothing.
- **Profile reads go through `visibility.py`.** No handler may read a field off the model directly (AGENTS.md).
- **Never swallow unexpected exceptions in a handler** (AGENTS.md). Answer only failures the user can act on; let the rest reach `dp.errors`.
- Run tests with `uv run pytest`.

---

### Task 1: Field table and level helpers in `visibility.py`

**Files:**
- Modify: `src/jbcub_bot/features/directory/visibility.py` (full rewrite of the module body)
- Test: `tests/test_visibility.py`

**Interfaces:**
- Consumes: `jbcub_bot.core.models.Role`, `User`.
- Produces, all from `jbcub_bot.features.directory.visibility`:
  - `class Category(enum.Enum)` with members `ALWAYS`, `CONFIGURABLE`, `ADMIN_ONLY`
  - `STAFF_ONLY: str`, `COHORT: str`, `EVERYONE: str`, `LEVELS: tuple[str, ...]`
  - `LEVEL_LABELS: dict[str, str]`, `LEVEL_EMOJI: dict[str, str]`
  - `@dataclass(frozen=True) class FieldSpec` with `name: str`, `label: str`, `category: Category`, `default: str | None = None`
  - `FIELDS: tuple[FieldSpec, ...]`, `BY_NAME: dict[str, FieldSpec]`, `CONFIGURABLE_FIELDS: tuple[FieldSpec, ...]`
  - `field_value(user: User, name: str) -> object | None`
  - `level_of(user: User, name: str) -> str`
  - `next_level(level: str) -> str`
  - `set_level(user: User, name: str, level: str) -> None`
  - `are_cohort_mates(a: User, b: User) -> bool` (unchanged signature)
  - `visible_fields(viewer: User, target: User) -> dict`

- [ ] **Step 1: Write the failing tests**

Replace the whole of `tests/test_visibility.py` with this file. The two renamed-level tests replace the old `nobody` / `all_students` ones, and legacy reads get their own test.

```python
import pytest

from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.visibility import (
    COHORT,
    EVERYONE,
    STAFF_ONLY,
    Category,
    are_cohort_mates,
    field_value,
    level_of,
    next_level,
    set_level,
    visible_fields,
)
from jbcub_bot.features.directory import visibility


def _u(**kw):
    return User(last_name=kw.pop("last_name", "U"),
                first_name=kw.pop("first_name", ""), **kw)


# --- the field table -------------------------------------------------------

def test_configurable_fields_are_the_five_expected_ones():
    names = [f.name for f in visibility.CONFIGURABLE_FIELDS]
    assert names == ["telegram", "status_line", "gmail", "github", "codeforces"]


def test_every_configurable_field_has_a_default_and_others_do_not():
    for spec in visibility.FIELDS:
        if spec.category is Category.CONFIGURABLE:
            assert spec.default in visibility.LEVELS, spec.name
        else:
            assert spec.default is None, spec.name


def test_field_order_matches_the_rendered_profile_order():
    assert [f.name for f in visibility.FIELDS] == [
        "first_name", "last_name", "role", "primary_cohort",
        "telegram", "telegram_id", "status_line",
        "gmail", "github", "codeforces",
        "matriculation", "birthday", "citizenship", "comment",
    ]


# --- levels ---------------------------------------------------------------

def test_next_level_cycles_and_wraps():
    assert next_level(STAFF_ONLY) == COHORT
    assert next_level(COHORT) == EVERYONE
    assert next_level(EVERYONE) == STAFF_ONLY


def test_level_of_falls_back_to_the_per_field_default():
    u = _u(visibility={})
    assert level_of(u, "telegram") == EVERYONE
    assert level_of(u, "status_line") == EVERYONE
    assert level_of(u, "gmail") == COHORT


def test_level_of_reads_legacy_values():
    u = _u(visibility={"gmail": "nobody", "github": "all_students"})
    assert level_of(u, "gmail") == STAFF_ONLY
    assert level_of(u, "github") == EVERYONE


def test_level_of_ignores_a_value_it_cannot_understand():
    u = _u(visibility={"gmail": "friends-of-friends"})
    assert level_of(u, "gmail") == COHORT  # the field's default


def test_set_level_reassigns_the_dict_so_sqlalchemy_sees_it(session):
    u = _u(telegram_id=1, visibility={})
    session.add(u)
    session.commit()
    before = u.visibility
    set_level(u, "gmail", STAFF_ONLY)
    assert u.visibility is not before  # a new dict, not an in-place mutation
    session.commit()
    session.expire(u)
    assert u.visibility == {"gmail": STAFF_ONLY}


def test_set_level_keeps_other_fields():
    u = _u(visibility={"gmail": COHORT})
    set_level(u, "github", EVERYONE)
    assert u.visibility == {"gmail": COHORT, "github": EVERYONE}


def test_field_value_renders_telegram_with_an_at_sign():
    assert field_value(_u(handle_observed="tg"), "telegram") == "@tg"
    assert field_value(_u(handle_sheet="sheet"), "telegram") == "@sheet"
    assert field_value(_u(), "telegram") is None
    assert field_value(_u(gmail="a@b.c"), "gmail") == "a@b.c"


# --- cohort mates ---------------------------------------------------------

def test_cohort_mates_by_intersection():
    a = _u(primary_cohort="2024", past_cohorts=["2023"])
    b = _u(primary_cohort="2022", past_cohorts=["2023"])
    c = _u(primary_cohort="2021", past_cohorts=[])
    assert are_cohort_mates(a, b) is True   # shared 2023
    assert are_cohort_mates(a, c) is False


# --- visible_fields -------------------------------------------------------

def test_student_sees_cohort_mate_configurable_by_default():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                github="gh", visibility={})  # default -> cohort
    fields = visible_fields(viewer, target)
    assert fields["gmail"] == "t@gmail.com"
    assert fields["github"] == "gh"


def test_student_non_cohort_sees_telegram_but_not_gmail():
    # telegram defaults to `everyone`, gmail to `cohort` -- this is the whole
    # point of per-field defaults.
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                handle_observed="tg", status_line="hi")
    fields = visible_fields(viewer, target)
    assert fields["telegram"] == "@tg"
    assert fields["status_line"] == "hi"
    assert "gmail" not in fields
    assert fields["last_name"] == target.last_name
    assert fields["first_name"] == target.first_name


def test_student_cannot_see_a_staff_only_field():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                visibility={"gmail": STAFF_ONLY})
    assert "gmail" not in visible_fields(viewer, target)


def test_student_cannot_see_a_hidden_telegram_handle():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", handle_observed="tg",
                visibility={"telegram": STAFF_ONLY})
    assert "telegram" not in visible_fields(viewer, target)


def test_everyone_level_crosses_cohorts():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2021", github="gh",
                visibility={"github": EVERYONE})
    assert visible_fields(viewer, target)["github"] == "gh"


def test_owner_always_sees_their_own_staff_only_field():
    # Regression: hiding a field used to hide it from its owner's own /me.
    me = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
            visibility={"gmail": STAFF_ONLY})
    assert visible_fields(me, me)["gmail"] == "t@gmail.com"


def test_two_unsaved_users_are_not_mistaken_for_the_same_person():
    # Both have id None; identifying "self" by id alone would leak everything.
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", gmail="t@gmail.com",
                visibility={"gmail": STAFF_ONLY})
    assert viewer.id is None and target.id is None
    assert "gmail" not in visible_fields(viewer, target)


def test_same_person_loaded_twice_counts_as_self(session):
    me = _u(role=Role.STUDENT, telegram_id=5, gmail="t@gmail.com",
            visibility={"gmail": STAFF_ONLY})
    session.add(me)
    session.commit()
    twin = User(id=me.id, role=Role.STUDENT, last_name=me.last_name,
                first_name=me.first_name, gmail=me.gmail,
                visibility=dict(me.visibility))
    assert visible_fields(twin, me)["gmail"] == "t@gmail.com"


def test_teacher_sees_full_set_across_cohorts_ignoring_staff_only():
    viewer = _u(role=Role.TEACHER, primary_cohort="9999")
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                visibility={"gmail": STAFF_ONLY})
    assert visible_fields(viewer, target)["gmail"] == "t@gmail.com"


def test_admin_overrides_staff_only_configurable():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT, primary_cohort="2021", gmail="t@gmail.com",
                visibility={"gmail": STAFF_ONLY})
    assert visible_fields(viewer, target)["gmail"] == "t@gmail.com"


def test_admin_sees_admin_only_fields():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT, matriculation="30000001")
    assert visible_fields(viewer, target)["matriculation"] == "30000001"


def test_admin_sees_personal_admin_only_fields():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT, birthday="2000-01-02",
                citizenship="RU", comment="note")
    fields = visible_fields(viewer, target)
    assert fields["birthday"] == "2000-01-02"
    assert fields["citizenship"] == "RU"
    assert fields["comment"] == "note"


def test_student_never_sees_personal_admin_only_fields():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", birthday="2000-01-02",
                citizenship="RU", comment="note")
    fields = visible_fields(viewer, target)
    assert "birthday" not in fields
    assert "citizenship" not in fields
    assert "comment" not in fields


def test_student_never_sees_admin_only():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024", matriculation="30000001")
    assert "matriculation" not in visible_fields(viewer, target)


def test_owner_never_sees_their_own_admin_only_fields_are_not_promoted():
    # A student is not told hidden fields exist -- but they are their own row,
    # so the ADMIN_ONLY rule must beat the self rule.
    me = _u(role=Role.STUDENT, birthday="2000-01-02", matriculation="30000001")
    fields = visible_fields(me, me)
    assert "birthday" not in fields
    assert "matriculation" not in fields


def test_teacher_never_sees_admin_only():
    viewer = _u(role=Role.TEACHER, primary_cohort="9999")
    target = _u(role=Role.STUDENT, primary_cohort="2021", matriculation="30000001")
    assert "matriculation" not in visible_fields(viewer, target)


def test_unknown_field_name_is_a_programming_error():
    with pytest.raises(KeyError):
        level_of(_u(), "no_such_field")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_visibility.py -q`
Expected: collection error — `ImportError: cannot import name 'COHORT' from 'jbcub_bot.features.directory.visibility'`.

- [ ] **Step 3: Rewrite `visibility.py`**

Replace the entire contents of `src/jbcub_bot/features/directory/visibility.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_visibility.py -q`
Expected: PASS, 28 tests.

- [ ] **Step 5: Run the full suite — nothing else may break**

Run: `uv run pytest -q`
Expected: PASS. `tests/test_directory_render.py` in particular still passes: it stores the legacy `{"gmail": "nobody"}` and expects the address hidden, which the legacy read map preserves.

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/features/directory/visibility.py tests/test_visibility.py
git commit -m "feat: single profile field table with per-field visibility levels"
```

---

### Task 2: `render.py` reads the table; add `me_keyboard`

**Files:**
- Modify: `src/jbcub_bot/features/directory/render.py` (drop `_LABELS`/`_ORDER`, add `me_keyboard`)
- Test: `tests/test_directory_render.py`, `tests/test_directory_admin.py`

**Interfaces:**
- Consumes: `FIELDS`, `visible_fields` from `jbcub_bot.features.directory.visibility`; `Role`, `User` from `jbcub_bot.core.models`.
- Produces, from `jbcub_bot.features.directory.render`:
  - `render_profile(viewer: User, target: User) -> str` (unchanged signature and output)
  - `admin_keyboard(target: User) -> InlineKeyboardMarkup | None` (unchanged)
  - `me_keyboard(user: User, *, allow_privacy: bool = True) -> InlineKeyboardMarkup | None`
  - `PRIVACY_CALLBACK = "dir:privacy"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_directory_render.py`:

```python
from jbcub_bot.features.directory.render import (
    PRIVACY_CALLBACK,
    me_keyboard,
    render_profile,
)
from jbcub_bot.features.directory.visibility import STAFF_ONLY


def test_render_order_and_labels_are_unchanged():
    # Regression anchor: telegram and status_line moved from unhideable to
    # configurable, and both default to `everyone`, so a stranger's view of a
    # profile must look exactly as it did before the move.
    viewer = User(first_name="V", last_name="Viewer", role=Role.STUDENT,
                  primary_cohort="2024")
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  primary_cohort="2021", handle_observed="ivanov",
                  status_line="open to teams", gmail="i@gmail.com")
    assert render_profile(viewer, target) == (
        "Name: Ivan Ivanov\n"
        "Role: Student\n"
        "Cohort: 2021\n"
        "Telegram: @ivanov\n"
        "Status: open to teams"
    )


def test_render_omits_a_hidden_telegram_handle():
    viewer = User(first_name="V", last_name="Viewer", role=Role.STUDENT,
                  primary_cohort="2024")
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  primary_cohort="2024", handle_observed="ivanov",
                  visibility={"telegram": STAFF_ONLY})
    text = render_profile(viewer, target)
    assert "ivanov" not in text
    assert "Name: Ivan Ivanov" in text


def test_render_shows_admin_only_fields_to_an_admin():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  matriculation="30000001", birthday="2000-01-02")
    text = render_profile(admin, target)
    assert "Matriculation: 30000001" in text
    assert "Birthday: 2000-01-02" in text


def test_me_keyboard_offers_the_privacy_screen():
    kb = me_keyboard(User(first_name="S", last_name="Student",
                          role=Role.STUDENT))
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == [
        PRIVACY_CALLBACK
    ]


def test_me_keyboard_without_privacy_is_empty_for_a_student():
    assert me_keyboard(User(first_name="S", last_name="Student",
                            role=Role.STUDENT), allow_privacy=False) is None


def test_me_keyboard_puts_privacy_above_the_admin_buttons():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN,
                 matriculation="30000001")
    kb = me_keyboard(admin)
    assert kb.inline_keyboard[0][0].callback_data == PRIVACY_CALLBACK
    assert [b.callback_data for b in kb.inline_keyboard[1]] == [
        "dir:link:30000001", "dir:reset:30000001",
    ]


def test_me_keyboard_for_an_admin_without_matriculation_has_only_privacy():
    kb = me_keyboard(User(first_name="A", last_name="Admin", role=Role.ADMIN))
    assert len(kb.inline_keyboard) == 1
    assert kb.inline_keyboard[0][0].callback_data == PRIVACY_CALLBACK
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_directory_render.py -q`
Expected: collection error — `ImportError: cannot import name 'PRIVACY_CALLBACK'`.

- [ ] **Step 3: Rewrite `render.py`**

Replace the entire contents of `src/jbcub_bot/features/directory/render.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_directory_render.py tests/test_directory_admin.py -q`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/features/directory/render.py tests/test_directory_render.py
git commit -m "refactor: render profiles from the field table, add me_keyboard"
```

---

### Task 3: `/cohort` stops bypassing the visibility service

**Files:**
- Modify: `src/jbcub_bot/features/directory/handlers.py:59-67` (`cmd_cohort`)
- Test: `tests/test_directory_cohort.py` (create)

**Interfaces:**
- Consumes: `visible_fields` from `jbcub_bot.features.directory.visibility`; `list_cohort` from `...search`.
- Produces: `render_cohort_list(viewer: User, mates: list[User]) -> str` in `jbcub_bot.features.directory.render` — a pure renderer, so the leak is testable without a dispatcher.

- [ ] **Step 1: Write the failing test**

Create `tests/test_directory_cohort.py`:

```python
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.render import render_cohort_list
from jbcub_bot.features.directory.visibility import STAFF_ONLY


def _student(first, last, **kw):
    return User(first_name=first, last_name=last, role=Role.STUDENT,
                primary_cohort="2024", **kw)


def test_cohort_list_shows_visible_handles():
    viewer = _student("V", "Viewer")
    mates = [_student("Ivan", "Ivanov", handle_observed="ivanov")]
    assert render_cohort_list(viewer, mates) == "- Ivan Ivanov (@ivanov)"


def test_cohort_list_drops_a_handle_its_owner_hid():
    # The leak this task closes: /cohort used to print handle_observed straight
    # off the model, so `staff_only` on telegram meant nothing here.
    viewer = _student("V", "Viewer")
    mates = [_student("Ivan", "Ivanov", handle_observed="ivanov",
                      visibility={"telegram": STAFF_ONLY})]
    assert render_cohort_list(viewer, mates) == "- Ivan Ivanov"


def test_cohort_list_omits_the_handle_when_there_is_none():
    viewer = _student("V", "Viewer")
    assert render_cohort_list(viewer, [_student("Ivan", "Ivanov")]) == \
        "- Ivan Ivanov"


def test_admin_still_sees_a_hidden_handle_in_the_cohort_list():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN,
                 primary_cohort="2024")
    mates = [_student("Ivan", "Ivanov", handle_observed="ivanov",
                      visibility={"telegram": STAFF_ONLY})]
    assert render_cohort_list(admin, mates) == "- Ivan Ivanov (@ivanov)"


def test_cohort_list_keeps_one_line_per_mate():
    viewer = _student("V", "Viewer")
    mates = [_student("A", "One", handle_observed="a"), _student("B", "Two")]
    assert render_cohort_list(viewer, mates) == "- A One (@a)\n- B Two"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_directory_cohort.py -q`
Expected: collection error — `ImportError: cannot import name 'render_cohort_list'`.

- [ ] **Step 3: Add the renderer**

Append to `src/jbcub_bot/features/directory/render.py`:

```python
def render_cohort_list(viewer: User, mates: list[User]) -> str:
    """One line per cohort mate, with the handle only when `viewer` may see it.

    Goes through visible_fields rather than reading handle_observed: telegram
    is a configurable field, so this list would otherwise leak a handle its
    owner hid.
    """
    lines = []
    for mate in mates:
        handle = visible_fields(viewer, mate).get("telegram")
        name = f"{mate.first_name} {mate.last_name}".strip()
        lines.append(f"- {name} ({handle})" if handle else f"- {name}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_directory_cohort.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Use it in the handler**

In `src/jbcub_bot/features/directory/handlers.py`, change the import on line 18 to add `render_cohort_list`:

```python
from jbcub_bot.features.directory.render import (
    admin_keyboard,
    render_cohort_list,
    render_profile,
)
```

and replace the body of `cmd_cohort`:

```python
@cmd.command("cohort", "List the people in your cohort.")
async def cmd_cohort(message: Message, principal: User, session):
    if not principal.primary_cohort:
        await message.answer("No cohort on file.")
        return
    mates = list_cohort(session, principal.primary_cohort)
    await message.answer("Your cohort:\n" + render_cohort_list(principal, mates))
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/features/directory/render.py \
        src/jbcub_bot/features/directory/handlers.py \
        tests/test_directory_cohort.py
git commit -m "fix: /cohort hides a handle its owner made staff-only"
```

---

### Task 4: The privacy screen, as pure functions

**Files:**
- Create: `src/jbcub_bot/features/directory/privacy.py`
- Test: `tests/test_privacy.py` (create)

**Interfaces:**
- Consumes: `CONFIGURABLE_FIELDS`, `LEVELS`, `LEVEL_EMOJI`, `LEVEL_LABELS`, `field_value`, `level_of` from `jbcub_bot.features.directory.visibility`.
- Produces, from `jbcub_bot.features.directory.privacy`:
  - `render_privacy(user: User) -> str`
  - `privacy_keyboard(user: User) -> InlineKeyboardMarkup`
  - `BACK_CALLBACK = "dir:profile"`
  - `FIELD_CALLBACK_PREFIX = "dir:vis:"`

This task adds no router and no handlers — Task 5 does the wiring.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_privacy.py`:

```python
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.privacy import (
    BACK_CALLBACK,
    FIELD_CALLBACK_PREFIX,
    privacy_keyboard,
    render_privacy,
)
from jbcub_bot.features.directory.visibility import (
    COHORT,
    EVERYONE,
    LEVEL_EMOJI,
    STAFF_ONLY,
)


def _me(**kw):
    return User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                primary_cohort="2024", **kw)


def test_screen_explains_the_levels_and_the_unhideable_fields():
    text = render_privacy(_me())
    assert "Who sees your data" in text
    for level in (STAFF_ONLY, COHORT, EVERYONE):
        assert LEVEL_EMOJI[level] in text
    assert "Staff only" in text
    assert "My cohort" in text
    assert "Everyone" in text
    assert "Name, role and cohort are always visible." in text


def test_screen_lists_every_configurable_field_with_its_level():
    me = _me(handle_observed="ivanov", gmail="i@gmail.com",
             visibility={"github": STAFF_ONLY})
    text = render_privacy(me)
    assert f"{LEVEL_EMOJI[EVERYONE]} Telegram: @ivanov" in text
    assert f"{LEVEL_EMOJI[COHORT]} Gmail: i@gmail.com" in text
    assert f"{LEVEL_EMOJI[STAFF_ONLY]} GitHub: —" in text


def test_screen_shows_an_empty_field_as_a_dash():
    # github/codeforces are in no sheet mapping yet, so they are empty for
    # everyone -- the level is still worth setting ahead of time.
    text = render_privacy(_me())
    assert "Codeforces: —" in text


def test_screen_truncates_a_long_status():
    me = _me(status_line="x" * 80)
    line = next(l for l in render_privacy(me).splitlines() if "Status:" in l)
    assert len(line) < 80
    assert line.endswith("…")


def test_screen_never_mentions_an_admin_only_field():
    me = _me(matriculation="30000001", birthday="2000-01-02", comment="note")
    text = render_privacy(me)
    assert "30000001" not in text
    assert "Matriculation" not in text
    assert "Birthday" not in text
    assert "Comment" not in text


def test_keyboard_puts_two_fields_per_row_and_back_alone():
    kb = privacy_keyboard(_me())
    widths = [len(row) for row in kb.inline_keyboard]
    assert widths == [2, 2, 1, 1]  # 5 configurable fields, then Back
    assert kb.inline_keyboard[-1][0].callback_data == BACK_CALLBACK
    assert kb.inline_keyboard[-1][0].text == "← Back to profile"


def test_keyboard_buttons_carry_the_field_and_show_its_level():
    kb = privacy_keyboard(_me(visibility={"gmail": STAFF_ONLY}))
    buttons = {b.callback_data: b.text
               for row in kb.inline_keyboard for b in row}
    assert buttons[f"{FIELD_CALLBACK_PREFIX}telegram"] == \
        f"Telegram {LEVEL_EMOJI[EVERYONE]}"
    assert buttons[f"{FIELD_CALLBACK_PREFIX}gmail"] == \
        f"Gmail {LEVEL_EMOJI[STAFF_ONLY]}"


def test_staff_configure_their_own_fields_too():
    # Admins and teachers are ordinary rows with the same configurable fields;
    # nothing about the screen is student-only.
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN,
                 gmail="a@gmail.com")
    assert "Gmail: a@gmail.com" in render_privacy(admin)
    assert len(privacy_keyboard(admin).inline_keyboard) == 4


def test_every_callback_data_fits_telegram_s_64_byte_limit():
    kb = privacy_keyboard(_me())
    for row in kb.inline_keyboard:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_privacy.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'jbcub_bot.features.directory.privacy'`.

- [ ] **Step 3: Create `privacy.py` with the renderers**

Create `src/jbcub_bot/features/directory/privacy.py`:

```python
"""The "who sees my data" screen.

One cycling button per configurable field; a tap advances that field's level
and redraws this same message. Only the caller's own row is ever written, so
there is nothing to authorize beyond being linked.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jbcub_bot.core.models import User
from jbcub_bot.features.directory.visibility import (
    CONFIGURABLE_FIELDS,
    LEVEL_EMOJI,
    LEVEL_LABELS,
    LEVELS,
    field_value,
    level_of,
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_privacy.py -q`
Expected: PASS, 9 tests.

Note on `test_keyboard_puts_two_fields_per_row_and_back_alone`: five fields chunk into `[2, 2, 1]`, then the Back row makes `[2, 2, 1, 1]`. If a later change adds a sixth configurable field this test must be updated to `[2, 2, 2, 1]` — that is the point of asserting it.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/privacy.py tests/test_privacy.py
git commit -m "feat: render the profile visibility screen"
```

---

### Task 5: Wire the screen up — `/privacy`, the callbacks, the child router

**Files:**
- Modify: `src/jbcub_bot/features/directory/privacy.py` (add router, registrar, handlers)
- Modify: `src/jbcub_bot/features/directory/__init__.py`
- Test: `tests/test_privacy_handlers.py` (create)

**Interfaces:**
- Consumes: `render_privacy`, `privacy_keyboard`, `BACK_CALLBACK`, `FIELD_CALLBACK_PREFIX` (Task 4); `render_profile`, `me_keyboard`, `PRIVACY_CALLBACK` (Task 2); `BY_NAME`, `Category`, `level_of`, `next_level`, `set_level` (Task 1); `CommandRegistrar` from `jbcub_bot.core.commands`.
- Produces, from `jbcub_bot.features.directory.privacy`: `router: Router` (named `directory.privacy`), `cmd: CommandRegistrar` whose `.specs` holds the `privacy` command, and the four handlers `cmd_privacy`, `cb_open`, `cb_back`, `cb_cycle`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_privacy_handlers.py`:

```python
"""End-to-end coverage for the privacy screen: real dispatcher, real callbacks.

The pure renderers are covered in test_privacy.py. What needs proving here is
the wiring -- that a tap really reaches the handler through a real aiogram
dispatcher, advances the level, commits it, and edits the same message.
"""

from datetime import datetime, timezone

from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import jbcub_bot.features.directory as directory
from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.visibility import COHORT, EVERYONE, STAFF_ONLY
from jbcub_bot.main import build_dispatcher


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_student(factory, telegram_id=222, **kw):
    setup = factory()
    setup.add(User(last_name="Ivanov", first_name="Ivan",
                   matriculation="30001111", telegram_id=telegram_id,
                   role=Role.STUDENT, primary_cohort="2024",
                   handle_observed="ivanov", gmail="i@gmail.com", **kw))
    setup.commit()
    setup.close()


def _callback_update(fake_bot, telegram_id: int, data: str) -> Update:
    chat = Chat(id=telegram_id, type="private")
    shown = Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=TgUser(id=1, is_bot=True, first_name="bot"),
        text="whatever was on screen",
    ).as_(fake_bot)
    cb = CallbackQuery(
        id="cb-1",
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        chat_instance="chat-instance",
        data=data,
        message=shown,
    ).as_(fake_bot)
    return Update(update_id=1, callback_query=cb).as_(fake_bot)


def _message_update(fake_bot, telegram_id: int, text: str) -> Update:
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=2, message=msg).as_(fake_bot)


def _stored_level(factory, field: str):
    read = factory()
    user = read.scalars(select(User).where(User.telegram_id == 222)).one()
    level = (user.visibility or {}).get(field)
    read.close()
    return level


def _edits(fake_bot):
    return [m for m in fake_bot.sent if isinstance(m, EditMessageText)]


async def test_privacy_command_shows_the_screen():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/privacy"),
                         dispatcher=dp)

    texts = [getattr(m, "text", "") for m in fake_bot.sent]
    assert any("Who sees your data" in t for t in texts)


async def test_tapping_a_field_advances_the_level_and_persists_it():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    # gmail defaults to `cohort`; one tap must move it to `everyone`.
    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:vis:gmail"),
                         dispatcher=dp)

    assert _stored_level(factory, "gmail") == EVERYONE


async def test_tapping_redraws_the_same_message():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:vis:gmail"),
                         dispatcher=dp)

    edits = _edits(fake_bot)
    assert len(edits) == 1
    assert edits[0].message_id == 7  # the message that carried the button
    assert "Who sees your data" in edits[0].text


async def test_three_taps_return_to_the_starting_level():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    for i in range(3):
        await dp.feed_update(fake_bot,
                             _callback_update(fake_bot, 222, "dir:vis:gmail"),
                             dispatcher=dp)

    assert _stored_level(factory, "gmail") == COHORT


async def test_back_button_redraws_the_profile():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:profile"),
                         dispatcher=dp)

    edits = _edits(fake_bot)
    assert len(edits) == 1
    assert "Name: Ivan Ivanov" in edits[0].text
    assert "Who sees your data" not in edits[0].text


async def test_opening_the_screen_from_the_profile_button():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:privacy"),
                         dispatcher=dp)

    edits = _edits(fake_bot)
    assert len(edits) == 1
    assert "Who sees your data" in edits[0].text


async def test_an_unknown_field_is_refused_without_touching_the_row():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    # A keyboard from an older deploy, or an admin-only field smuggled in.
    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:vis:birthday"),
                         dispatcher=dp)

    assert _edits(fake_bot) == []
    assert _stored_level(factory, "birthday") is None


async def test_an_unlinked_user_gets_no_screen():
    factory = _session_factory()  # nobody seeded
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 999, "dir:vis:gmail"),
                         dispatcher=dp)

    assert _edits(fake_bot) == []


async def test_a_hidden_field_still_shows_on_the_owner_s_own_screen():
    factory = _session_factory()
    _seed_student(factory, visibility={"gmail": STAFF_ONLY})
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:privacy"),
                         dispatcher=dp)

    assert "i@gmail.com" in _edits(fake_bot)[0].text


def test_manifest_lists_the_privacy_command():
    names = {c.name for c in directory.manifest.commands}
    assert "privacy" in names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_privacy_handlers.py -q`
Expected: FAIL. `test_manifest_lists_the_privacy_command` fails on the missing command; the callback tests fail because no handler matches, so `_edits(fake_bot)` is empty.

- [ ] **Step 3: Add the router and handlers to `privacy.py`**

Replace the whole import block at the top of `src/jbcub_bot/features/directory/privacy.py` with this (it is the Task 4 block plus the new names):

```python
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
```

Then append the router, registrar, and handlers to the end of the module:

```python
router = Router(name="directory.privacy")
cmd = CommandRegistrar(router)

_NOT_LINKED = "You are not linked yet. Contact an admin."


@cmd.command("privacy", "Choose who sees each of your profile fields.")
async def cmd_privacy(message: Message, principal: User, session):
    await message.answer(render_privacy(principal),
                         reply_markup=privacy_keyboard(principal))


async def _show_privacy(cb: CallbackQuery, principal: User) -> None:
    await cb.message.edit_text(render_privacy(principal),
                               reply_markup=privacy_keyboard(principal))
    await cb.answer()


@router.callback_query(F.data == PRIVACY_CALLBACK)
async def cb_open(cb: CallbackQuery, principal: User, session):
    if principal is None:
        await cb.answer(_NOT_LINKED, show_alert=True)
        return
    await _show_privacy(cb, principal)


@router.callback_query(F.data == BACK_CALLBACK)
async def cb_back(cb: CallbackQuery, principal: User, session):
    if principal is None:
        await cb.answer(_NOT_LINKED, show_alert=True)
        return
    await cb.message.edit_text(render_profile(principal, principal),
                               reply_markup=me_keyboard(principal))
    await cb.answer()


@router.callback_query(F.data.startswith(FIELD_CALLBACK_PREFIX))
async def cb_cycle(cb: CallbackQuery, principal: User, session):
    if principal is None:
        await cb.answer(_NOT_LINKED, show_alert=True)
        return
    name = cb.data[len(FIELD_CALLBACK_PREFIX):]
    spec = BY_NAME.get(name)
    if spec is None or spec.category is not Category.CONFIGURABLE:
        # A keyboard left over from an older deploy, or a hand-crafted payload.
        await cb.answer("Unknown field.", show_alert=True)
        return
    set_level(principal, name, next_level(level_of(principal, name)))
    session.commit()
    await _show_privacy(cb, principal)
```

- [ ] **Step 4: Wire the child router into the feature**

Replace the entire contents of `src/jbcub_bot/features/directory/__init__.py`:

```python
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role
from jbcub_bot.features.directory import privacy
from jbcub_bot.features.directory.handlers import cmd, name_search_intent, router

# The privacy screen keeps its own router so it can live in its own module;
# the loader only ever sees the feature's single top-level router.
router.include_router(privacy.router)

manifest = Manifest(
    name="directory",
    commands=cmd.specs + privacy.cmd.specs,
    intents=[name_search_intent],
    min_role=Role.STUDENT,
    help_text="Find classmates and manage your own profile.",
)

__all__ = ["router", "manifest"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_privacy_handlers.py -q`
Expected: PASS, 10 tests.

If `include_router` raises `RuntimeError: Router is already attached`, the cause is `conftest.py`'s `_reset_feature_routers` fixture clearing `_parent_router` on the *feature* router only. That is correct and sufficient — `include_router` here runs once at import, not per test. A failure here instead means something re-imported the package; do not "fix" it by resetting `privacy.router._parent_router`.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. `/help` now lists `/privacy` too; `tests/test_help_integration.py` only asserts on substrings, not on an exact command list, so it needs no change.

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/features/directory/privacy.py \
        src/jbcub_bot/features/directory/__init__.py \
        tests/test_privacy_handlers.py
git commit -m "feat: /privacy command and cycling visibility buttons"
```

---

### Task 6: The button on `/me`, suppressed under `/as`

**Files:**
- Modify: `src/jbcub_bot/features/directory/handlers.py:53-56` (`cmd_me`)
- Test: `tests/test_me_keyboard_integration.py` (create)

**Interfaces:**
- Consumes: `me_keyboard` from `jbcub_bot.features.directory.render` (Task 2); `impersonator` from the handler context, set by `PrincipalMiddleware` only while impersonating.
- Produces: nothing new — `cmd_me` gains an `impersonator=None` parameter.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_me_keyboard_integration.py`:

```python
"""/me offers the privacy screen -- except when an admin is impersonating.

Under /as the profile belongs to the target but a later button press arrives
without the impersonation ref, so the callback would edit the *admin's* own
settings while the screen shows a student. The button must not be there.
"""

from datetime import datetime, timezone

from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.render import PRIVACY_CALLBACK
from jbcub_bot.main import build_dispatcher


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(factory):
    setup = factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT, primary_cohort="cohort-x"))
    setup.commit()
    setup.close()


def _message_update(fake_bot, telegram_id: int, text: str) -> Update:
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=1, message=msg).as_(fake_bot)


def _callbacks(method):
    markup = getattr(method, "reply_markup", None)
    if markup is None:
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row]


async def test_me_offers_the_privacy_screen():
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/me"),
                         dispatcher=dp)

    assert PRIVACY_CALLBACK in _callbacks(fake_bot.sent[0])


async def test_me_under_impersonation_has_no_privacy_button():
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 /me"),
                         dispatcher=dp)

    assert "Zakhar Zhukovsky" in fake_bot.sent[1].text  # the target's profile
    assert PRIVACY_CALLBACK not in _callbacks(fake_bot.sent[1])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_me_keyboard_integration.py -q`
Expected: `test_me_offers_the_privacy_screen` FAILS (`/me` still sends `admin_keyboard(...)` or `None`, so the list has no `dir:privacy`). The impersonation test passes already — it is the regression guard for the next step.

- [ ] **Step 3: Use `me_keyboard` in `cmd_me`**

In `src/jbcub_bot/features/directory/handlers.py`, add `me_keyboard` to the render import:

```python
from jbcub_bot.features.directory.render import (
    admin_keyboard,
    me_keyboard,
    render_cohort_list,
    render_profile,
)
```

and replace `cmd_me`:

```python
@cmd.command("me", "Show your own profile.")
async def cmd_me(message: Message, principal: User, session, impersonator=None):
    # `impersonator` is only in the handler context while /as is in flight; a
    # button press afterwards would arrive as the admin, so hide the screen.
    await message.answer(
        render_profile(principal, principal),
        reply_markup=me_keyboard(principal, allow_privacy=impersonator is None),
    )
```

Leave the rest of `handlers.py` alone. `admin_keyboard` and `Role` both stay imported — `name_search` and the admin callbacks still use them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_me_keyboard_integration.py -q`
Expected: PASS, 2 tests.

- [ ] **Step 5: Walk the loop by hand**

Run: `uv run pytest -q`
Expected: PASS, whole suite.

Then, with a populated `.env`, run `uv run python -m jbcub_bot` and in Telegram: `/me` → tap **🔒 Who sees my data** → tap **Gmail** three times → confirm the emoji cycles 👥 → 🌐 → 🔒 → 👥 in the same message → tap **← Back to profile** → confirm the profile returns in that same message with the button back. Then `/privacy` as a second entry point.

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/features/directory/handlers.py \
        tests/test_me_keyboard_integration.py
git commit -m "feat: reach the visibility screen from /me"
```

---

### Task 7: Record the convention in `AGENTS.md`

**Files:**
- Modify: `AGENTS.md` (the "Conventions that aren't obvious" list)

**Interfaces:**
- Consumes: nothing. Produces: nothing. Documentation only.

- [ ] **Step 1: Extend the conventions list**

In `AGENTS.md`, replace the bullet:

```markdown
- **Profile reads go through `features/directory/visibility.py`** — never bypass it.
```

with:

```markdown
- **Profile reads go through `features/directory/visibility.py`** — never bypass it.
  A handler that reads a profile column off the model leaks whatever its owner
  hid (`/cohort` did exactly this until telegram became hideable).
- **Adding a profile field = one line in `FIELDS`** (`features/directory/visibility.py`):
  name, label, category (`ALWAYS` / `CONFIGURABLE` / `ADMIN_ONLY`), and a default
  level for configurable ones. The visibility service, the profile renderer, and
  the `/privacy` screen all read that table; nothing else lists profile fields.
  `ADMIN_ONLY` fields are never shown or hinted at to their owner.
- **`user.visibility` must be reassigned, not mutated** — it is a plain `JSON`
  column, so `user.visibility[k] = v` leaves the instance clean and the commit
  writes nothing. Use `visibility.set_level`.
```

- [ ] **Step 2: Verify the claims are still true**

Run: `grep -rn "handle_observed\|handle_sheet" src/jbcub_bot/features/`
Expected: matches only in `visibility.py` (`field_value`) and `identity`-related writes — no handler reading a handle to display it.

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "docs: record the profile field table convention"
```

---

## Definition of done

- `uv run pytest -q` passes.
- `/me` shows **🔒 Who sees my data**; `/privacy` opens the same screen; `/help` lists `/privacy`.
- Tapping a field cycles 🔒 → 👥 → 🌐 in place and survives a restart.
- A field set to **Staff only** disappears from a cohort-mate's profile view *and* from `/cohort`, stays visible to teachers, admins, and its owner.
- `/as <ref> /me` shows no privacy button.
- Rendered profiles are unchanged for a user who has touched nothing.
