# Self-service profile fields — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user edit their own status line, GitHub and Codeforces handles from an inline screen, keeping the roster's version of each account field side by side with theirs.

**Architecture:** Each account field becomes two columns (`*_sheet` owned by Google Sheets, `*_self` owned by the bot), joined at render time by the existing `FIELDS` table. A new `features/directory/edit.py` owns an inline screen shaped exactly like `/privacy`, plus one FSM state that captures the next text message as the new value. Validation and the GitHub/Codeforces existence check live in a separate `accounts.py` whose network call is an injectable parameter.

**Tech Stack:** Python 3.12, aiogram 3.30, SQLAlchemy 2.0, alembic, aiohttp (already an aiogram dependency), pytest + pytest-asyncio (`asyncio_mode = auto`).

**Spec:** `docs/superpowers/specs/2026-07-27-self-service-profile-fields-design.md`

## Global Constraints

- Run everything through uv: `uv run pytest`, `uv run python -m jbcub_bot`.
- All bot-facing copy is English. Comments explain *why*, not *what* (see `AGENTS.md`).
- A feature is a package under `src/jbcub_bot/features/<name>/` exporting `router` + `manifest`; commands are registered with `CommandRegistrar`, never with in-handler role checks.
- Profile reads go through `features/directory/visibility.py`. Never read a profile column off the model in a handler.
- Adding or changing a profile field is **one line in `FIELDS`** (`visibility.py`). Nothing else may enumerate profile fields.
- `user.visibility` must be reassigned, not mutated.
- The bot never writes to a Google Sheet. Sheet-owned columns are listed in `sheets.SHEET_OWNED`; everything else must survive `/sync`.
- Don't swallow unexpected exceptions in a handler. Answer only failures the user can act on; let the rest reach `dp.errors`.
- Blocking I/O in an async handler freezes the whole bot. Network calls need a deadline.
- Commit after every task. Tests must pass before each commit.

---

### Task 1: Two columns per account field

Renames `users.github` / `users.codeforces` to `*_sheet` and adds `*_self`, then teaches the field table to read both. The rename is safe because no mapping fills those columns today — every cell is NULL.

**Files:**
- Modify: `src/jbcub_bot/core/models.py:30-31`
- Create: `alembic/versions/f3c1a9b47e21_split_account_fields.py`
- Modify: `src/jbcub_bot/core/sheets.py:95-99` (`SHEET_OWNED`)
- Modify: `src/jbcub_bot/features/directory/visibility.py` (`FieldSpec`, `FIELDS`, `field_value`, new `editable_column`, `EDITABLE_FIELDS`)
- Test: `tests/test_visibility.py`, `tests/test_init_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `User.github_sheet`, `User.github_self`, `User.codeforces_sheet`, `User.codeforces_self` — all `Mapped[str | None]`.
  - `FieldSpec(name, label, category, default=None, sources=(), editable=False, edit_hint="")` — `sources` is `(self_column, sheet_column)`.
  - `visibility.editable_column(spec: FieldSpec) -> str`
  - `visibility.EDITABLE_FIELDS: tuple[FieldSpec, ...]`
  - `visibility.ROSTER_NOTE: str` (`"roster"`)
  - `field_value(user, "github")` → `"alice-dev (roster: alice)"` when both differ.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_visibility.py`:

```python
def test_field_value_prefers_the_self_reported_account():
    assert field_value(_u(github_self="alice", github_sheet=None), "github") == "alice"
    assert field_value(_u(github_self=None, github_sheet="alice"), "github") == "alice"
    assert field_value(_u(github_self="alice", github_sheet="alice"), "github") == "alice"
    assert field_value(_u(), "github") is None


def test_field_value_shows_the_roster_value_next_to_a_differing_own_one():
    u = _u(github_self="alice-dev", github_sheet="alice")
    assert field_value(u, "github") == "alice-dev (roster: alice)"


def test_field_value_treats_a_blank_sheet_cell_as_missing():
    # normalize_rows writes "" for an empty cell, not None.
    assert field_value(_u(codeforces_self="alice", codeforces_sheet=""),
                       "codeforces") == "alice"


def test_editable_column_is_the_self_column_for_two_source_fields():
    assert editable_column(visibility.BY_NAME["github"]) == "github_self"
    assert editable_column(visibility.BY_NAME["codeforces"]) == "codeforces_self"
    assert editable_column(visibility.BY_NAME["status_line"]) == "status_line"


def test_editable_fields_are_the_three_a_user_owns():
    assert [f.name for f in visibility.EDITABLE_FIELDS] == [
        "status_line", "github", "codeforces",
    ]


def test_every_editable_field_is_configurable_and_has_a_hint():
    # An editable ALWAYS field could not be hidden; an editable ADMIN_ONLY one
    # would tell its owner it exists.
    for spec in visibility.EDITABLE_FIELDS:
        assert spec.category is Category.CONFIGURABLE, spec.name
        assert spec.edit_hint, spec.name
```

Extend the import at the top of the file with `editable_column`.

Add to `tests/test_init_db.py`:

```python
def test_migrations_produce_exactly_the_columns_the_model_declares(db_path):
    # A model change without a migration takes the bot down at boot, where the
    # only symptom is a failing query far from the edit that caused it.
    from jbcub_bot.core.models import User

    db.init_db()

    columns = {c["name"] for c in inspect(db.get_engine()).get_columns("users")}
    assert columns == {c.name for c in User.__table__.columns}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_visibility.py tests/test_init_db.py -v`
Expected: FAIL — `TypeError: 'github_self' is an invalid keyword argument for User`, and `ImportError: cannot import name 'editable_column'`.

- [ ] **Step 3: Split the columns on the model**

In `src/jbcub_bot/core/models.py`, replace the two account lines:

```python
    gmail: Mapped[str | None] = mapped_column(String)
    github_sheet: Mapped[str | None] = mapped_column(String)
    github_self: Mapped[str | None] = mapped_column(String)
    codeforces_sheet: Mapped[str | None] = mapped_column(String)
    codeforces_self: Mapped[str | None] = mapped_column(String)
```

- [ ] **Step 4: Write the migration by hand**

Create `alembic/versions/f3c1a9b47e21_split_account_fields.py`. Hand-written rather than autogenerated because `uv run alembic revision` needs a populated `.env`, and autogenerate would emit drop+add (losing data) instead of a rename.

```python
"""split account fields into roster and self-reported columns

Revision ID: f3c1a9b47e21
Revises: c72c6d99f0c1
Create Date: 2026-07-27

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3c1a9b47e21'
down_revision: Union[str, Sequence[str], None] = 'c72c6d99f0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Rename, don't drop: the roster keeps whatever it already imported."""
    op.alter_column('users', 'github', new_column_name='github_sheet')
    op.alter_column('users', 'codeforces', new_column_name='codeforces_sheet')
    op.add_column('users', sa.Column('github_self', sa.String(), nullable=True))
    op.add_column('users', sa.Column('codeforces_self', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'codeforces_self')
    op.drop_column('users', 'github_self')
    op.alter_column('users', 'codeforces_sheet', new_column_name='codeforces')
    op.alter_column('users', 'github_sheet', new_column_name='github')
```

- [ ] **Step 5: Point `SHEET_OWNED` at the roster columns**

In `src/jbcub_bot/core/sheets.py`:

```python
SHEET_OWNED = (
    "last_name", "first_name", "handle_sheet", "gmail",
    "github_sheet", "codeforces_sheet",
    "birthday", "citizenship", "comment",
    "primary_cohort", "past_cohorts", "role",
)
```

A mapping YAML key is a `User` field name, so a roster column becomes readable by adding `github_sheet: "GitHub"` to a cohort mapping. No mapping does that today; nothing else has to change.

- [ ] **Step 6: Teach the field table about two-source fields**

In `src/jbcub_bot/features/directory/visibility.py`, extend `FieldSpec`:

```python
@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    category: Category
    default: str | None = None       # CONFIGURABLE only
    sources: tuple[str, ...] = ()    # (self-reported column, roster column)
    editable: bool = False           # the owner may set it from the bot
    edit_hint: str = ""              # what the edit prompt asks for
```

Replace the three field lines:

```python
    FieldSpec("status_line", "Status", Category.CONFIGURABLE, EVERYONE,
              editable=True,
              edit_hint="Send your new status — one line, up to 120 characters."),
    FieldSpec("gmail", "Gmail", Category.CONFIGURABLE, COHORT),
    FieldSpec("github", "GitHub", Category.CONFIGURABLE, COHORT,
              sources=("github_self", "github_sheet"), editable=True,
              edit_hint="Send your GitHub username, or a link to your profile."),
    FieldSpec("codeforces", "Codeforces", Category.CONFIGURABLE, COHORT,
              sources=("codeforces_self", "codeforces_sheet"), editable=True,
              edit_hint="Send your Codeforces handle, or a link to your profile."),
```

Add next to `CONFIGURABLE_FIELDS`:

```python
EDITABLE_FIELDS = tuple(spec for spec in FIELDS if spec.editable)

ROSTER_NOTE = "roster"


def editable_column(spec: FieldSpec) -> str:
    """The column an owner's own edit writes.

    A two-source field is edited in its self-reported column; the roster's
    column belongs to the sheet and the bot never writes it.
    """
    return spec.sources[0] if spec.sources else spec.name
```

Rewrite `field_value`:

```python
def field_value(user: User, name: str):
    """The displayable value of a field.

    `telegram` is the one field that isn't a column: it picks the observed
    handle over the sheet's hint and prefixes the @.

    A field with `sources` has two: what its owner told the bot and what the
    roster says. The owner's wins, but when both are set and disagree the
    roster's is shown alongside it — a profile that silently drops one of two
    conflicting claims keeps the disagreement invisible until it matters.
    Telegram is deliberately not rendered this way: there, an observed handle
    is the truth and the sheet's is merely stale.
    """
    if name == "telegram":
        handle = user.handle_observed or user.handle_sheet
        return f"@{handle}" if handle else None
    spec = BY_NAME[name]
    if spec.sources:
        own, roster = (getattr(user, column) or None for column in spec.sources)
        if own and roster and own != roster:
            return f"{own} ({ROSTER_NOTE}: {roster})"
        return own or roster
    return getattr(user, name)
```

- [ ] **Step 7: Fix the tests that used the old column name**

Two places construct users with `github="gh"` (both in `tests/test_visibility.py`, and they mean the roster's value now):

- `test_student_sees_cohort_mate_configurable_by_default`: `github="gh"` → `github_sheet="gh"`
- `test_everyone_level_crosses_cohorts`: `github="gh"` → `github_sheet="gh"`

Leave `visibility={"github": ...}` keys alone everywhere: the field's *name* did not change, only its columns, so stored visibility settings and callback payloads stay valid.

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/jbcub_bot/core/models.py src/jbcub_bot/core/sheets.py \
        src/jbcub_bot/features/directory/visibility.py \
        alembic/versions/f3c1a9b47e21_split_account_fields.py \
        tests/test_visibility.py tests/test_init_db.py
git commit -m "feat: split github/codeforces into roster and self-reported columns"
```

---

### Task 2: Report drift on the new pairs

`/sync` already tells an admin when the roster's Telegram handle disagrees with the observed one. Generalize that to every two-source field, and say which field disagreed.

**Files:**
- Modify: `src/jbcub_bot/core/sheets.py:131-149` (`reconcile`)
- Test: `tests/test_sheets_upsert.py:51-63`

**Interfaces:**
- Consumes: `User.github_sheet` / `github_self` / `codeforces_sheet` / `codeforces_self` (Task 1).
- Produces: `sheets.DRIFT_PAIRS: tuple[tuple[str, str, str], ...]` — `(record_key, own_column, label)`. `ReconcileReport.drift` entries become `"<key>:<label>"`.

- [ ] **Step 1: Write the failing test**

Replace `test_reconcile_reports_drift_unmatched_duplicates` in `tests/test_sheets_upsert.py`:

```python
def test_reconcile_reports_drift_unmatched_duplicates(session):
    session.add(User(matriculation="1", last_name="Ivan",
                     handle_observed="ivan_new"))
    session.commit()
    records = [
        {"matriculation": "1", "handle_sheet": "ivan_old"},   # drift
        {"matriculation": "2", "handle_sheet": "x"},          # unmatched
        {"matriculation": "2", "handle_sheet": "x"},          # duplicate key
    ]
    report = sheets.reconcile(session, records)
    assert "1:telegram" in report.drift
    assert "2" in report.unmatched
    assert "2" in report.duplicates


def test_reconcile_names_the_account_field_that_drifted(session):
    session.add(User(matriculation="1", last_name="Ivan",
                     github_self="alice-dev", codeforces_self="alice"))
    session.commit()
    records = [{"matriculation": "1", "github_sheet": "alice",
                "codeforces_sheet": "alice"}]

    report = sheets.reconcile(session, records)

    assert report.drift == ["1:github"]  # codeforces agrees, so it is not listed


def test_reconcile_ignores_a_field_only_one_side_filled(session):
    session.add(User(matriculation="1", last_name="Ivan", github_self="alice"))
    session.commit()
    records = [{"matriculation": "1", "github_sheet": ""}]

    assert sheets.reconcile(session, records).drift == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_sheets_upsert.py -v`
Expected: FAIL — drift holds `"1"`, not `"1:telegram"`.

- [ ] **Step 3: Generalize `reconcile`**

In `src/jbcub_bot/core/sheets.py`, above `reconcile`:

```python
# Fields the roster and the bot can both hold a value for: (the record key the
# sheet fills, the column the bot fills, the profile field's name). The bot
# never resolves a disagreement itself -- an admin edits the sheet.
DRIFT_PAIRS = (
    ("handle_sheet", "handle_observed", "telegram"),
    ("github_sheet", "github_self", "github"),
    ("codeforces_sheet", "codeforces_self", "codeforces"),
)
```

Replace the drift block inside the loop:

```python
        for sheet_key, own_column, label in DRIFT_PAIRS:
            sheet_value = record.get(sheet_key)
            own_value = getattr(user, own_column)
            if sheet_value and own_value and sheet_value != own_value:
                report.drift.append(f"{key_value}:{label}")
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/sheets.py tests/test_sheets_upsert.py
git commit -m "feat: report /sync drift per two-source field"
```

---

### Task 3: Normalize what the user typed

Pure text handling, no network. A normalizer either returns the canonical value or raises `ValueError` carrying the message the user will read.

**Files:**
- Create: `src/jbcub_bot/features/directory/accounts.py`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `accounts.normalize(field: str, text: str) -> str` — raises `ValueError(user_facing_message)`
  - `accounts.NORMALIZERS: dict[str, Callable[[str], str]]` keyed by profile field name
  - `accounts.STATUS_MAX_LEN: int` (120)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_accounts.py`:

```python
import pytest

from jbcub_bot.features.directory import accounts


@pytest.mark.parametrize("typed", [
    "alice", " alice ", "@alice", "github.com/alice",
    "https://github.com/alice", "https://www.github.com/alice/",
    "https://github.com/alice?tab=repositories",
])
def test_github_accepts_a_username_or_any_link_to_it(typed):
    assert accounts.normalize("github", typed) == "alice"


@pytest.mark.parametrize("typed", [
    "", "   ", "-alice", "alice-", "ali--ce", "a" * 40, "alice bob",
    "alice/bob", "https://github.com/",
])
def test_github_refuses_what_cannot_be_a_username(typed):
    with pytest.raises(ValueError, match="GitHub"):
        accounts.normalize("github", typed)


@pytest.mark.parametrize("typed", [
    "alice", "@alice", "codeforces.com/profile/alice",
    "https://codeforces.com/profile/alice",
])
def test_codeforces_accepts_a_handle_or_a_profile_link(typed):
    assert accounts.normalize("codeforces", typed) == "alice"


def test_codeforces_keeps_the_characters_it_allows():
    assert accounts.normalize("codeforces", "al_ice.1-x") == "al_ice.1-x"


@pytest.mark.parametrize("typed", ["", "ab", "a" * 25, "ali ce", "ali/ce"])
def test_codeforces_refuses_what_cannot_be_a_handle(typed):
    with pytest.raises(ValueError, match="Codeforces"):
        accounts.normalize("codeforces", typed)


def test_status_collapses_whitespace_into_one_line():
    assert accounts.normalize("status_line", " looking\nfor  a \n team ") == \
        "looking for a team"


def test_status_refuses_an_empty_text():
    with pytest.raises(ValueError, match="Clear"):
        accounts.normalize("status_line", "   \n ")


def test_status_refuses_a_too_long_text_and_says_how_long_it_was():
    with pytest.raises(ValueError) as err:
        accounts.normalize("status_line", "x" * 154)
    assert "120" in str(err.value)
    assert "154" in str(err.value)


def test_status_at_the_limit_is_accepted():
    text = "x" * accounts.STATUS_MAX_LEN
    assert accounts.normalize("status_line", text) == text


def test_an_unknown_field_is_a_programming_error():
    with pytest.raises(KeyError):
        accounts.normalize("birthday", "whatever")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: FAIL — `ModuleNotFoundError: jbcub_bot.features.directory.accounts`.

- [ ] **Step 3: Write the normalizers**

Create `src/jbcub_bot/features/directory/accounts.py`:

```python
"""Reading a GitHub or Codeforces account out of whatever the user sent.

Normalization is pure and lives apart from the existence check so the parsing
rules can be tested without a network, and so a handler can reject a typo
before spending a request on it. A normalizer raises `ValueError` whose message
is shown to the user verbatim.
"""

import re

_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([^/?#\s]+)", re.IGNORECASE)
_CODEFORCES_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?codeforces\.com/(?:profile/)?([^/?#\s]+)",
    re.IGNORECASE)

# GitHub's own rule: alphanumerics and single inner hyphens, 39 characters max.
_GITHUB_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_CODEFORCES_RE = re.compile(r"^[A-Za-z0-9_.-]{3,24}$")

STATUS_MAX_LEN = 120

_GITHUB_HELP = ("That doesn't look like a GitHub username. Send something "
                "like alice or github.com/alice.")
_CODEFORCES_HELP = ("That doesn't look like a Codeforces handle. Send "
                    "something like alice or codeforces.com/profile/alice.")


def _unwrap(value: str, url_re: re.Pattern) -> str:
    handle = value.strip()
    match = url_re.search(handle)
    if match:
        handle = match.group(1)
    return handle.lstrip("@").strip()


def normalize_github(text: str) -> str:
    handle = _unwrap(text, _GITHUB_URL_RE)
    if not _GITHUB_RE.match(handle):
        raise ValueError(_GITHUB_HELP)
    return handle


def normalize_codeforces(text: str) -> str:
    handle = _unwrap(text, _CODEFORCES_URL_RE)
    if not _CODEFORCES_RE.match(handle):
        raise ValueError(_CODEFORCES_HELP)
    return handle


def normalize_status(text: str) -> str:
    """One line: the status shares a line with a label in three screens."""
    status = " ".join(text.split())
    if not status:
        raise ValueError("Send some text, or tap Clear to remove your status.")
    if len(status) > STATUS_MAX_LEN:
        raise ValueError(
            f"Too long — {STATUS_MAX_LEN} characters max, you sent {len(status)}."
        )
    return status


NORMALIZERS = {
    "status_line": normalize_status,
    "github": normalize_github,
    "codeforces": normalize_codeforces,
}


def normalize(field: str, text: str) -> str:
    """Canonical value for `field`, or ValueError with a message for the user."""
    return NORMALIZERS[field](text)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: PASS (all parametrized cases).

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/accounts.py tests/test_accounts.py
git commit -m "feat: normalize typed github/codeforces handles and status text"
```

---

### Task 4: Ask GitHub and Codeforces whether the account exists

Three outcomes, not two: an explicit "no such user" is the only one that blocks a save.

**Files:**
- Modify: `src/jbcub_bot/features/directory/accounts.py`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Consumes: `accounts.normalize` (Task 3).
- Produces:
  - `accounts.Verdict` — enum `EXISTS` / `MISSING` / `UNKNOWN`
  - `accounts.FetchFailed(Exception)`
  - `async accounts.verify(field: str, handle: str, fetch=_http_fetch) -> Verdict`
    where `fetch(url: str) -> tuple[int, str]` returns `(http_status, body)` or raises `FetchFailed`
  - `accounts.HTTP_TIMEOUT: float` (5.0)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_accounts.py`:

```python
from jbcub_bot.features.directory.accounts import Verdict


def _answers(status, body=""):
    async def fetch(url):
        _answers.url = url
        return status, body
    return fetch


def _fails():
    async def fetch(url):
        raise accounts.FetchFailed("connection reset")
    return fetch


async def test_github_200_means_the_account_exists():
    fetch = _answers(200, '{"login": "alice"}')
    assert await accounts.verify("github", "alice", fetch=fetch) is Verdict.EXISTS
    assert _answers.url == "https://api.github.com/users/alice"


async def test_github_404_means_no_such_account():
    assert await accounts.verify("github", "nope", fetch=_answers(404)) \
        is Verdict.MISSING


async def test_github_rate_limit_is_unknown_not_missing():
    # 60 anonymous requests an hour per IP: a shared IP running out of them
    # must not look like "this user doesn't exist".
    assert await accounts.verify("github", "alice", fetch=_answers(403)) \
        is Verdict.UNKNOWN


async def test_a_server_error_is_unknown():
    assert await accounts.verify("github", "alice", fetch=_answers(500)) \
        is Verdict.UNKNOWN


async def test_an_unreachable_service_is_unknown():
    assert await accounts.verify("github", "alice", fetch=_fails()) \
        is Verdict.UNKNOWN


async def test_codeforces_ok_status_means_the_account_exists():
    fetch = _answers(200, '{"status":"OK","result":[{"handle":"alice"}]}')
    assert await accounts.verify("codeforces", "alice", fetch=fetch) \
        is Verdict.EXISTS
    assert _answers.url == \
        "https://codeforces.com/api/user.info?handles=alice"


async def test_codeforces_failed_body_means_missing_despite_the_400():
    # Codeforces answers 400 for a handle it doesn't know, so the body decides.
    fetch = _answers(400, '{"status":"FAILED","comment":"handles: User with '
                          'handle nope not found"}')
    assert await accounts.verify("codeforces", "nope", fetch=fetch) \
        is Verdict.MISSING


async def test_codeforces_unparseable_body_is_unknown():
    fetch = _answers(200, "<html>maintenance</html>")
    assert await accounts.verify("codeforces", "alice", fetch=fetch) \
        is Verdict.UNKNOWN


async def test_a_field_with_nothing_to_check_verifies_trivially():
    async def never_called(url):
        raise AssertionError("status_line needs no network call")

    assert await accounts.verify("status_line", "hi", fetch=never_called) \
        is Verdict.EXISTS
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_accounts.py -v -k verify or Verdict`
(or simply `uv run pytest tests/test_accounts.py -v`)
Expected: FAIL — `ImportError: cannot import name 'Verdict'`.

- [ ] **Step 3: Implement `verify`**

Append to `src/jbcub_bot/features/directory/accounts.py` (and add `import asyncio`, `import enum`, `import json` at the top, plus `import aiohttp`):

```python
class Verdict(enum.Enum):
    EXISTS = "exists"
    MISSING = "missing"    # the service said there is no such user
    UNKNOWN = "unknown"    # the service didn't say


class FetchFailed(Exception):
    """The service could not be reached."""


HTTP_TIMEOUT = 5.0


async def _http_fetch(url: str) -> tuple[int, str]:
    """GET `url` with a deadline, as (status, body).

    aiohttp arrives with aiogram, so this adds no dependency. The deadline is
    the point: one event loop runs the whole bot, and a request that never
    answers would hold up every other update.
    """
    try:
        async with asyncio.timeout(HTTP_TIMEOUT):
            async with aiohttp.ClientSession() as http:
                async with http.get(url) as response:
                    return response.status, await response.text()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise FetchFailed(str(exc)) from exc


async def _verify_github(handle: str, fetch) -> Verdict:
    try:
        status, _ = await fetch(f"https://api.github.com/users/{handle}")
    except FetchFailed:
        return Verdict.UNKNOWN
    if status == 200:
        return Verdict.EXISTS
    if status == 404:
        return Verdict.MISSING
    return Verdict.UNKNOWN  # 403 rate limit, 5xx, anything unexpected


async def _verify_codeforces(handle: str, fetch) -> Verdict:
    try:
        _, body = await fetch(
            f"https://codeforces.com/api/user.info?handles={handle}")
    except FetchFailed:
        return Verdict.UNKNOWN
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return Verdict.UNKNOWN  # an HTML error page, not the API
    status = payload.get("status") if isinstance(payload, dict) else None
    if status == "OK":
        return Verdict.EXISTS
    # FAILED also covers malformed requests, but the handle passed our own
    # format check before we got here, so "not found" is what's left.
    if status == "FAILED":
        return Verdict.MISSING
    return Verdict.UNKNOWN


_VERIFIERS = {"github": _verify_github, "codeforces": _verify_codeforces}


async def verify(field: str, handle: str, fetch=_http_fetch) -> Verdict:
    """Does the account exist? EXISTS for a field with nothing to check.

    `handle` has been through `normalize`, so it holds only characters that are
    safe in a URL path or query and needs no escaping.

    `fetch` is a parameter so tests never touch the network.
    """
    verifier = _VERIFIERS.get(field)
    if verifier is None:
        return Verdict.EXISTS
    return await verifier(handle, fetch)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_accounts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/accounts.py tests/test_accounts.py
git commit -m "feat: verify a github/codeforces handle, tolerating a silent service"
```

---

### Task 5: Extract what the two screens share

`/privacy` already owns the guard, the "screen expired" copy and the value shortener that the edit screen needs. Move them before copying them.

**Files:**
- Create: `src/jbcub_bot/features/directory/screens.py`
- Modify: `src/jbcub_bot/features/directory/privacy.py`
- Modify: `src/jbcub_bot/features/directory/render.py` (callback constants)
- Test: `tests/test_privacy_handlers.py` (imports), `tests/test_privacy.py` (imports), `tests/test_screens.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `screens.NOT_LINKED`, `screens.NO_ROW`, `screens.EXPIRED`, `screens.UNKNOWN_FIELD`, `screens.EMPTY` (`"—"`)
  - `screens.short_value(value) -> str`
  - `screens.require_linked(fn)` — decorator for callback handlers
  - `render.PROFILE_CALLBACK` (`"dir:profile"`), `render.EDIT_CALLBACK` (`"dir:edit"`), `render.PRIVACY_CALLBACK` (unchanged)

- [ ] **Step 1: Write the failing test**

Create `tests/test_screens.py`:

```python
from jbcub_bot.features.directory.screens import EMPTY, short_value


def test_short_value_shows_a_dash_for_nothing():
    assert short_value(None) == EMPTY
    assert short_value("") == EMPTY


def test_short_value_keeps_a_short_value_verbatim():
    assert short_value("alice") == "alice"


def test_short_value_truncates_a_long_one_with_an_ellipsis():
    shortened = short_value("x" * 80)
    assert len(shortened) < 80
    assert shortened.endswith("…")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_screens.py -v`
Expected: FAIL — `ModuleNotFoundError: ...directory.screens`.

- [ ] **Step 3: Create the shared module**

Create `src/jbcub_bot/features/directory/screens.py` by moving code out of `privacy.py` unchanged:

```python
"""Pieces every self-service screen needs: refusals, the value shortener, and
the "is this caller usable" guard.

Two screens (`privacy.py`, `edit.py`) write only the caller's own row, so they
share one guard and one vocabulary of refusals rather than each inventing its
own wording.
"""

import functools

from aiogram.types import CallbackQuery

from jbcub_bot.core.models import User

NOT_LINKED = "You are not linked yet. Contact an admin."
NO_ROW = "Your account has no saved profile yet. Ask an admin to link you."
EXPIRED = "This screen expired — send the command again."
UNKNOWN_FIELD = "Unknown field."

EMPTY = "—"
_MAX_VALUE_LEN = 40


def short_value(value) -> str:
    """A field value that fits on one line of a screen."""
    if value in (None, ""):
        return EMPTY
    text = str(value)
    if len(text) <= _MAX_VALUE_LEN:
        return text
    return text[:_MAX_VALUE_LEN - 1] + "…"


def require_linked(fn):
    """Wrap a callback handler so it refuses an unusable caller before running.

    Mirrors CommandRegistrar._guard in core/commands.py. Uses functools.wraps
    so aiogram unwraps __wrapped__ and injects the original handler's declared
    params (principal, session, ...); guarded handlers must declare
    `principal`.

    Two distinct "not usable yet" cases: no principal at all (unlinked), and a
    bootstrap admin whose principal is a transient row never written to the
    database (`id is None` -- see identity.apply_bootstrap). The latter must
    not be silently materialized into a real row just because a button was
    tapped, so it gets refused here rather than persisted.
    """
    @functools.wraps(fn)
    async def wrapper(cb: CallbackQuery, **kwargs):
        principal: User | None = kwargs.get("principal")
        if principal is None:
            await cb.answer(NOT_LINKED, show_alert=True)
            return
        if principal.id is None:
            await cb.answer(NO_ROW, show_alert=True)
            return
        return await fn(cb, **kwargs)

    return wrapper
```

- [ ] **Step 4: Point `privacy.py` at it**

In `src/jbcub_bot/features/directory/privacy.py`: delete `_require_linked`, `_short`, `_EMPTY`, `_MAX_VALUE_LEN`, `_NOT_LINKED`, `_NO_ROW`, `_EXPIRED`, `BACK_CALLBACK` and the `functools` import. Then:

```python
from jbcub_bot.features.directory.render import (
    PRIVACY_CALLBACK,
    PROFILE_CALLBACK,
    me_keyboard,
    render_profile,
)
from jbcub_bot.features.directory.screens import (
    EXPIRED,
    UNKNOWN_FIELD,
    require_linked,
    short_value,
)
```

Replace every use: `_short(` → `short_value(`, `@_require_linked` → `@require_linked`, `BACK_CALLBACK` → `PROFILE_CALLBACK`, `_EXPIRED` → `EXPIRED`, `"Unknown field."` → `UNKNOWN_FIELD`.

In `src/jbcub_bot/features/directory/render.py`, next to `PRIVACY_CALLBACK`:

```python
PRIVACY_CALLBACK = "dir:privacy"
PROFILE_CALLBACK = "dir:profile"
EDIT_CALLBACK = "dir:edit"
```

and use `PROFILE_CALLBACK` nowhere else yet.

- [ ] **Step 5: Update the two test modules' imports**

In `tests/test_privacy_handlers.py`, replace

```python
from jbcub_bot.features.directory.privacy import _EXPIRED, _NO_ROW, _NOT_LINKED
```

with

```python
from jbcub_bot.features.directory.screens import EXPIRED, NO_ROW, NOT_LINKED
```

and rename the three usages (`_EXPIRED` → `EXPIRED`, and so on).

In `tests/test_privacy.py`, replace `BACK_CALLBACK` in the import with `PROFILE_CALLBACK` from `render`, and use it in `test_keyboard_puts_two_fields_per_row_and_back_alone`.

- [ ] **Step 6: Run the suite — a pure refactor must not change any behaviour**

Run: `uv run pytest`
Expected: PASS, with the `/privacy` tests unchanged in meaning. One exception: `EXPIRED` now says "send the command again" instead of "send /privacy again"; the tests compare against the constant, so nothing else moves.

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/features/directory/screens.py \
        src/jbcub_bot/features/directory/privacy.py \
        src/jbcub_bot/features/directory/render.py \
        tests/test_screens.py tests/test_privacy.py tests/test_privacy_handlers.py
git commit -m "refactor: share the screen guard, refusals and value shortener"
```

---

### Task 6: The edit screen, as pure functions

Text and keyboards only — no router, no handlers. Everything a test can check without a dispatcher.

**Files:**
- Create: `src/jbcub_bot/features/directory/edit.py`
- Test: `tests/test_edit.py`

**Interfaces:**
- Consumes: `visibility.EDITABLE_FIELDS`, `visibility.editable_column`, `visibility.field_value`, `visibility.BY_NAME`, `screens.short_value`, `screens.EMPTY`, `render.PROFILE_CALLBACK` (Tasks 1, 5).
- Produces:
  - `edit.FIELD_CALLBACK_PREFIX` = `"dir:edit:f:"`, `edit.CLEAR_CALLBACK_PREFIX` = `"dir:edit:clear:"`, `edit.CLEAR_DO_CALLBACK_PREFIX` = `"dir:edit:clear_do:"`, `edit.CANCEL_CALLBACK` = `"dir:edit:cancel"`
  - `edit.editable_spec(name: str) -> FieldSpec | None`
  - `edit.render_edit(user, notice="") -> str`
  - `edit.edit_keyboard(user) -> InlineKeyboardMarkup`
  - `edit.render_prompt(user, spec) -> str`
  - `edit.prompt_keyboard(spec) -> InlineKeyboardMarkup`
  - `edit.render_clear_confirm(spec) -> str`
  - `edit.clear_confirm_keyboard(spec) -> InlineKeyboardMarkup`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_edit.py`:

```python
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory import accounts, edit
from jbcub_bot.features.directory.render import PROFILE_CALLBACK
from jbcub_bot.features.directory.visibility import EDITABLE_FIELDS, BY_NAME


def _me(**kw):
    return User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                primary_cohort="2024", **kw)


def test_screen_lists_every_editable_field_with_its_value():
    text = edit.render_edit(_me(status_line="open to teams",
                                github_self="alice"))
    assert "Edit your profile" in text
    assert "Status: open to teams" in text
    assert "GitHub: alice" in text
    assert "Codeforces: —" in text


def test_screen_never_offers_a_field_the_user_does_not_own():
    text = edit.render_edit(_me(gmail="i@gmail.com", birthday="2000-01-02"))
    assert "Gmail" not in text
    assert "Birthday" not in text


def test_screen_shows_the_roster_value_next_to_a_differing_own_one():
    text = edit.render_edit(_me(github_self="alice-dev", github_sheet="alice"))
    assert "GitHub: alice-dev (roster: alice)" in text


def test_screen_carries_a_notice_above_the_header():
    text = edit.render_edit(_me(), notice="✅ GitHub updated.")
    assert text.startswith("✅ GitHub updated.\n\nEdit your profile")


def test_keyboard_puts_two_fields_per_row_and_back_alone():
    kb = edit.edit_keyboard(_me())
    assert [len(row) for row in kb.inline_keyboard] == [2, 1, 1]
    assert kb.inline_keyboard[-1][0].callback_data == PROFILE_CALLBACK
    assert kb.inline_keyboard[-1][0].text == "← Back to profile"


def test_keyboard_buttons_carry_their_field():
    kb = edit.edit_keyboard(_me())
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert f"{edit.FIELD_CALLBACK_PREFIX}github" in data
    assert f"{edit.FIELD_CALLBACK_PREFIX}status_line" in data


def test_every_callback_data_fits_telegram_s_64_byte_limit():
    keyboards = [edit.edit_keyboard(_me()),
                 edit.prompt_keyboard(BY_NAME["codeforces"]),
                 edit.clear_confirm_keyboard(BY_NAME["codeforces"])]
    for kb in keyboards:
        for row in kb.inline_keyboard:
            for button in row:
                assert len(button.callback_data.encode()) <= 64


def test_prompt_asks_for_the_field_and_shows_what_is_being_replaced():
    text = edit.render_prompt(_me(github_self="alice-dev", github_sheet="alice"),
                              BY_NAME["github"])
    assert "Send your GitHub username" in text
    # The prompt replaces the user's own value, so the roster's is not shown
    # here -- it is not what a new value would overwrite.
    assert "Now: alice-dev" in text
    assert "roster" not in text


def test_prompt_shows_a_dash_when_there_is_nothing_to_replace():
    assert "Now: —" in edit.render_prompt(_me(), BY_NAME["codeforces"])


def test_prompt_shows_a_long_status_in_full_so_it_can_be_retyped():
    status = "x" * 100
    assert status in edit.render_prompt(_me(status_line=status),
                                       BY_NAME["status_line"])


def test_prompt_keyboard_offers_clear_and_cancel():
    kb = edit.prompt_keyboard(BY_NAME["github"])
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        f"{edit.CLEAR_CALLBACK_PREFIX}github", edit.CANCEL_CALLBACK,
    ]


def test_clear_asks_before_removing_the_value():
    spec = BY_NAME["github"]
    assert "Clear your GitHub?" in edit.render_clear_confirm(spec)
    kb = edit.clear_confirm_keyboard(spec)
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        f"{edit.CLEAR_DO_CALLBACK_PREFIX}github", edit.CANCEL_CALLBACK,
    ]
    assert kb.inline_keyboard[0][0].text == "Yes, clear GitHub"


def test_clear_prefix_does_not_match_the_clear_do_payload():
    # Both handlers filter by prefix; one must not swallow the other's taps.
    assert not f"{edit.CLEAR_DO_CALLBACK_PREFIX}github".startswith(
        edit.CLEAR_CALLBACK_PREFIX)


def test_editable_spec_refuses_a_field_the_user_may_not_edit():
    assert edit.editable_spec("github") is BY_NAME["github"]
    assert edit.editable_spec("gmail") is None      # configurable, not editable
    assert edit.editable_spec("birthday") is None   # admin-only
    assert edit.editable_spec("nonsense") is None


def test_every_editable_field_has_a_normalizer():
    # The field table decides what is editable; accounts.py must keep up.
    for spec in EDITABLE_FIELDS:
        assert spec.name in accounts.NORMALIZERS, spec.name
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_edit.py -v`
Expected: FAIL — `ModuleNotFoundError: ...directory.edit`.

- [ ] **Step 3: Write the pure renderers**

Create `src/jbcub_bot/features/directory/edit.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_edit.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/edit.py tests/test_edit.py
git commit -m "feat: render the profile edit screen"
```

---

### Task 7: Make the screen work — command, prompt, saved value

The FSM state and the one core change that makes free-text input reach a feature at all.

**Files:**
- Modify: `src/jbcub_bot/features/directory/edit.py` (router, states, handlers)
- Modify: `src/jbcub_bot/features/directory/__init__.py`
- Modify: `src/jbcub_bot/main.py:64-67` (`nl_fallback`)
- Test: `tests/test_edit_handlers.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 3, 4, 6; `screens.require_linked`, `screens.EXPIRED`, `screens.UNKNOWN_FIELD`, `screens.NOT_LINKED`.
- Produces:
  - `edit.router` (`Router(name="directory.edit")`), `edit.cmd` (`CommandRegistrar`)
  - `edit.EditProfile` — `StatesGroup` with a single `value` state
  - commands `/edit`, `/cancel`
  - `directory.manifest.commands` includes both.

- [ ] **Step 1: Write the failing integration tests**

Create `tests/test_edit_handlers.py`:

```python
"""End-to-end coverage for the edit screen: real dispatcher, real FSM.

The renderers are covered in test_edit.py. What needs proving here is the
wiring -- that a tap opens a prompt, that the *next* message reaches this
feature instead of the name search, and that what lands in the database is the
normalized value.
"""

from datetime import datetime, timezone

from aiogram.methods import AnswerCallbackQuery, EditMessageText
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import jbcub_bot.features.directory as directory
from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory import accounts, edit
from jbcub_bot.features.directory.accounts import Verdict
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
                   handle_observed="ivanov", **kw))
    setup.commit()
    setup.close()


def _seed_admin_and_student(factory):
    setup = factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT, primary_cohort="cohort-x",
                   status_line="target status"))
    setup.commit()
    setup.close()


def _message_update(fake_bot, telegram_id: int, text: str, update_id=1) -> Update:
    msg = Message(
        message_id=100 + update_id,
        date=datetime.now(timezone.utc),
        chat=Chat(id=telegram_id, type="private"),
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        text=text,
    ).as_(fake_bot)
    return Update(update_id=update_id, message=msg).as_(fake_bot)


def _callback_update(fake_bot, telegram_id: int, data: str, update_id=2) -> Update:
    chat = Chat(id=telegram_id, type="private")
    shown = Message(
        message_id=7,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=TgUser(id=1, is_bot=True, first_name="bot"),
        text="whatever was on screen",
    ).as_(fake_bot)
    cb = CallbackQuery(
        id=f"cb-{update_id}",
        from_user=TgUser(id=telegram_id, is_bot=False, first_name="tg"),
        chat_instance="chat-instance",
        data=data,
        message=shown,
    ).as_(fake_bot)
    return Update(update_id=update_id, callback_query=cb).as_(fake_bot)


def _edits(fake_bot):
    return [m for m in fake_bot.sent if isinstance(m, EditMessageText)]


def _alerts(fake_bot):
    return [m for m in fake_bot.sent if isinstance(m, AnswerCallbackQuery)]


def _stored(factory, column: str, telegram_id=222):
    read = factory()
    user = read.scalars(select(User).where(User.telegram_id == telegram_id)).one()
    value = getattr(user, column)
    read.close()
    return value


def _verdict(monkeypatch, verdict: Verdict):
    """Answer every existence check with `verdict`; never touch the network."""
    async def fake_verify(field, handle, fetch=None):
        return verdict

    monkeypatch.setattr(accounts, "verify", fake_verify)


async def _open_prompt(dp, fake_bot, field: str, telegram_id=222):
    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, telegram_id,
                         f"{edit.FIELD_CALLBACK_PREFIX}{field}"),
        dispatcher=dp)


async def test_edit_command_shows_the_screen():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/edit"),
                         dispatcher=dp)

    assert "Edit your profile" in fake_bot.sent[0].text


async def test_tapping_a_field_turns_the_screen_into_a_prompt():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")

    edits = _edits(fake_bot)
    assert len(edits) == 1
    assert edits[0].message_id == 7  # the message that carried the button
    assert "Send your GitHub username" in edits[0].text


async def test_the_next_message_becomes_the_value(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.EXISTS)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222,
                                         "https://github.com/alice", update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") == "alice"  # normalized, not raw
    assert _stored(factory, "github_sheet") is None    # the roster is untouched
    last = _edits(fake_bot)[-1]
    assert last.message_id == 7                        # the screen, redrawn
    assert "✅ GitHub updated." in last.text
    assert "GitHub: alice" in last.text


async def test_a_saved_value_ends_the_state(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.EXISTS)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "status_line")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "open to teams",
                                         update_id=3),
                         dispatcher=dp)
    fake_bot.sent.clear()
    # A second message is an ordinary one again: the name search answers it.
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "Ivanov", update_id=4),
                         dispatcher=dp)

    assert _stored(factory, "status_line") == "open to teams"
    assert any("Ivan Ivanov" in getattr(m, "text", "") for m in fake_bot.sent)


async def test_a_value_while_editing_never_reaches_the_name_search(monkeypatch):
    # The whole reason nl_fallback needs StateFilter(None): a Dispatcher's own
    # handlers run before its sub-routers, so the `.+` search intent would
    # otherwise swallow every value.
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.EXISTS)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "Ivanov", update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") == "Ivanov"
    texts = [getattr(m, "text", "") for m in fake_bot.sent]
    assert not any("No one found." in t for t in texts)
    assert not any("Several people match" in t for t in texts)


async def test_an_unverifiable_account_is_saved_with_a_warning(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.UNKNOWN)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "alice", update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") == "alice"
    assert "couldn't verify alice" in _edits(fake_bot)[-1].text


async def test_a_missing_account_is_refused_and_the_prompt_stays(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)
    _verdict(monkeypatch, Verdict.MISSING)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "nope", update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") is None
    redraw = _edits(fake_bot)[-1]
    assert "GitHub has no user nope." in redraw.text
    assert "Send your GitHub username" in redraw.text  # still asking


async def test_a_malformed_value_is_refused_without_a_network_call(monkeypatch):
    factory = _session_factory()
    _seed_student(factory)

    async def never_called(field, handle, fetch=None):
        raise AssertionError("a value that cannot be a handle must not be checked")

    monkeypatch.setattr(accounts, "verify", never_called)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "not a username!",
                                         update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "github_self") is None
    assert "GitHub username" in _edits(fake_bot)[-1].text


async def test_a_too_long_status_is_refused():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "status_line")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "x" * 200, update_id=3),
                         dispatcher=dp)

    assert _stored(factory, "status_line") is None
    assert "120 characters max" in _edits(fake_bot)[-1].text


async def test_cancel_button_puts_the_screen_back():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, edit.CANCEL_CALLBACK,
                                          update_id=3),
                         dispatcher=dp)

    assert "Edit your profile" in _edits(fake_bot)[-1].text


async def test_cancel_command_leaves_the_state_and_restores_the_screen():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "/cancel", update_id=3),
                         dispatcher=dp)
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 222, "alice", update_id=4),
                         dispatcher=dp)

    assert _stored(factory, "github_self") is None  # no longer editing
    assert "Edit your profile" in _edits(fake_bot)[-1].text


async def test_cancel_outside_a_state_says_there_is_nothing_to_cancel():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/cancel"),
                         dispatcher=dp)

    assert "Nothing to cancel." in fake_bot.sent[0].text


async def test_an_unknown_field_is_refused():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    # gmail is configurable but not editable; birthday is admin-only.
    await _open_prompt(dp, fake_bot, "gmail")

    assert _edits(fake_bot) == []
    alerts = _alerts(fake_bot)
    assert len(alerts) == 1
    assert alerts[0].show_alert is True


async def test_an_unlinked_user_gets_no_prompt():
    factory = _session_factory()  # nobody seeded
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github", telegram_id=999)

    assert _edits(fake_bot) == []
    assert len(_alerts(fake_bot)) == 1


async def test_plain_text_still_searches_when_nobody_is_editing():
    # Regression: StateFilter(None) must narrow the fallback, not disable it.
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "Ivanov"),
                         dispatcher=dp)

    assert any("Ivan Ivanov" in getattr(m, "text", "")
               for m in fake_bot.sent)


async def test_a_search_under_impersonation_still_reaches_the_fallback():
    # StateFilter(None) resolves raw_state from the handler data, and /as
    # propagates a message event straight to dp.message -- past the outer
    # middleware that would have put raw_state there. Absent must read as
    # "no state", or /as stops finding anyone.
    factory = _session_factory()
    _seed_admin_and_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 Zhukovsky"),
                         dispatcher=dp)

    assert any("Zakhar Zhukovsky" in getattr(m, "text", "")
               for m in fake_bot.sent)


async def test_cancel_under_impersonation_does_not_crash():
    # Same missing-state path, reached by a command that needs the state.
    factory = _session_factory()
    _seed_admin_and_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 /cancel"),
                         dispatcher=dp)

    assert "Nothing to cancel." in fake_bot.sent[1].text


async def test_opening_the_screen_from_a_callback():
    factory = _session_factory()
    _seed_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 222, "dir:edit"),
                         dispatcher=dp)

    assert "Edit your profile" in _edits(fake_bot)[-1].text


def test_manifest_lists_the_new_commands():
    names = {c.name for c in directory.manifest.commands}
    assert {"edit", "cancel"} <= names
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_edit_handlers.py -v`
Expected: FAIL — `AttributeError: module ...edit has no attribute 'router'`.

- [ ] **Step 3: Add the router, the state and the handlers**

Append to `src/jbcub_bot/features/directory/edit.py`, extending its imports:

```python
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Message

from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.features.directory import accounts
from jbcub_bot.features.directory.accounts import Verdict
from jbcub_bot.features.directory.screens import (
    EXPIRED,
    NOT_LINKED,
    UNKNOWN_FIELD,
    require_linked,
)
```

```python
router = Router(name="directory.edit")
cmd = CommandRegistrar(router)

_NOTHING_TO_CANCEL = "Nothing to cancel."
_CANCELLED = "Editing cancelled."
_STALE_STATE = "That edit screen is from an older version — send /edit again."


class EditProfile(StatesGroup):
    # One state for every field: which field is being edited lives in the FSM
    # data, so adding an editable field adds no state.
    value = State()


async def _redraw(message: Message, data: dict, text: str, keyboard) -> None:
    """Put `text` on the screen the prompt came from, or send a fresh one.

    Goes through bot(EditMessageText(...)) because the value arrives as the
    user's own message -- there is no bot message here to call edit_text on,
    only the chat and message ids stashed when the prompt was drawn.

    That message may be gone (the user deleted it, or the state outlived the
    deploy that stored the ids). Deleting your own message is not a bug worth a
    traceback, so a new screen is sent instead.
    """
    chat_id, message_id = data.get("chat_id"), data.get("message_id")
    if chat_id is not None and message_id is not None:
        try:
            await message.bot(EditMessageText(
                chat_id=chat_id, message_id=message_id,
                text=text, reply_markup=keyboard))
            return
        except TelegramBadRequest:
            pass
    await message.answer(text, reply_markup=keyboard)


@cmd.command("edit", "Edit your status, GitHub or Codeforces.")
async def cmd_edit(message: Message, principal: User, session,
                   state: FSMContext | None = None, impersonator=None):
    # `state` is optional because /as reaches this handler through
    # dispatcher.propagate_event("message", ...), which skips the Dispatcher's
    # outer middlewares -- FSMContextMiddleware among them. A required `state`
    # would make every `/as <ref> /edit` a TypeError.
    #
    # `impersonator` is only in the handler context while /as is in flight.
    # Mirrors cmd_me and cmd_privacy: the follow-up callback would arrive
    # without the impersonation ref and land on the admin's own row, so show
    # the target's screen with nothing tappable instead of a live keyboard.
    if state is not None:
        await state.clear()
    await message.answer(
        render_edit(principal),
        reply_markup=None if impersonator is not None else edit_keyboard(principal),
    )


@cmd.command("cancel", "Stop editing a profile field.")
async def cmd_cancel(message: Message, principal: User, session,
                     state: FSMContext | None = None):
    if state is None:  # propagated by /as, where no state exists -- see cmd_edit
        await message.answer(_NOTHING_TO_CANCEL)
        return
    data = await state.get_data()
    if await state.get_state() is None:
        await message.answer(_NOTHING_TO_CANCEL)
        return
    await state.clear()
    await _redraw(message, data, render_edit(principal, _CANCELLED),
                  edit_keyboard(principal))


async def _show_screen(cb: CallbackQuery, user: User, notice: str = "") -> None:
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_edit(user, notice),
                               reply_markup=edit_keyboard(user))
    await cb.answer()


@router.callback_query(F.data == EDIT_CALLBACK)
@require_linked
async def cb_open(cb: CallbackQuery, principal: User, session,
                  state: FSMContext):
    await state.clear()
    await _show_screen(cb, principal)


@router.callback_query(F.data == CANCEL_CALLBACK)
@require_linked
async def cb_cancel(cb: CallbackQuery, principal: User, session,
                    state: FSMContext):
    await state.clear()
    await _show_screen(cb, principal)


@router.callback_query(F.data.startswith(FIELD_CALLBACK_PREFIX))
@require_linked
async def cb_field(cb: CallbackQuery, principal: User, session,
                   state: FSMContext):
    spec = editable_spec(cb.data[len(FIELD_CALLBACK_PREFIX):])
    if spec is None:
        # A keyboard left over from an older deploy, or a hand-crafted payload.
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await state.set_state(EditProfile.value)
    await state.update_data(field=spec.name, chat_id=cb.message.chat.id,
                            message_id=cb.message.message_id)
    await cb.message.edit_text(render_prompt(principal, spec),
                               reply_markup=prompt_keyboard(spec))
    await cb.answer()


@router.message(EditProfile.value, F.text & ~F.text.startswith("/"))
async def on_value(message: Message, principal: User, session,
                   state: FSMContext):
    """Save what the user typed, or explain why it can't be saved.

    Commands are excluded from this handler rather than intercepted, so /cancel
    -- and anything else -- still works while a prompt is open.
    """
    if principal is None or principal.id is None:
        await state.clear()
        await message.answer(NOT_LINKED)
        return
    data = await state.get_data()
    spec = editable_spec(data.get("field", ""))
    if spec is None:
        await state.clear()
        await message.answer(_STALE_STATE)
        return
    try:
        value = accounts.normalize(spec.name, message.text)
    except ValueError as exc:
        await _reprompt(message, data, principal, spec, str(exc))
        return
    verdict = await accounts.verify(spec.name, value)
    if verdict is Verdict.MISSING:
        await _reprompt(message, data, principal, spec,
                        f"{spec.label} has no user {value}.")
        return
    setattr(principal, editable_column(spec), value)
    session.commit()
    await state.clear()
    notice = (f"✅ {spec.label} updated." if verdict is Verdict.EXISTS else
              f"⚠️ Saved. {spec.label} didn't answer, so I couldn't "
              f"verify {value}.")
    await _redraw(message, data, render_edit(principal, notice),
                  edit_keyboard(principal))


async def _reprompt(message: Message, data: dict, user: User, spec: FieldSpec,
                    problem: str) -> None:
    """Say what was wrong and keep asking -- the state stays open."""
    await _redraw(message, data,
                  f"{problem}\n\n{render_prompt(user, spec)}",
                  prompt_keyboard(spec))
```

Add `EDIT_CALLBACK` to the `render` import at the top of the module.

- [ ] **Step 4: Include the router and publish the commands**

In `src/jbcub_bot/features/directory/__init__.py`:

```python
from jbcub_bot.features.directory import edit, privacy
from jbcub_bot.features.directory.handlers import cmd, name_search_intent, router

# The privacy and edit screens keep their own routers so they can live in their
# own modules; the loader only ever sees the feature's single top-level router.
router.include_router(privacy.router)
router.include_router(edit.router)

manifest = Manifest(
    name="directory",
    commands=cmd.specs + privacy.cmd.specs + edit.cmd.specs,
    intents=[name_search_intent],
    min_role=Role.STUDENT,
    help_text="Find classmates and manage your own profile.",
)
```

- [ ] **Step 5: Narrow the NL fallback to callers who aren't mid-edit**

In `src/jbcub_bot/main.py`, add `from aiogram.filters import StateFilter` and change the fallback:

```python
    # NL fallback: any non-command text runs through the intent router --
    # unless the sender is in a state. A Dispatcher's own handlers run before
    # its sub-routers, so without StateFilter(None) the `.+` search intent
    # would swallow every value a feature is waiting for.
    @dp.message(StateFilter(None), F.text & ~F.text.startswith("/"))
    async def nl_fallback(message: Message, principal, session):
        await _intent_router.dispatch(message.text, message, principal, session)
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_edit_handlers.py -v`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest`
Expected: PASS. If `/help` snapshots list commands, `edit` and `cancel` now appear — update the expectation, don't hide the commands.

- [ ] **Step 8: Commit**

```bash
git add src/jbcub_bot/features/directory/edit.py \
        src/jbcub_bot/features/directory/__init__.py \
        src/jbcub_bot/main.py tests/test_edit_handlers.py
git commit -m "feat: /edit screen writes a typed status, github or codeforces"
```

---

### Task 8: Clearing a value, with a confirmation

**Files:**
- Modify: `src/jbcub_bot/features/directory/edit.py`
- Test: `tests/test_edit_handlers.py`

**Interfaces:**
- Consumes: `edit.render_clear_confirm`, `edit.clear_confirm_keyboard`, `edit.CLEAR_CALLBACK_PREFIX`, `edit.CLEAR_DO_CALLBACK_PREFIX` (Task 6).
- Produces: no new names.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_edit_handlers.py`:

```python
async def test_clear_asks_before_removing_anything():
    factory = _session_factory()
    _seed_student(factory, github_self="alice")
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await _open_prompt(dp, fake_bot, "github")
    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, 222,
                         f"{edit.CLEAR_CALLBACK_PREFIX}github", update_id=3),
        dispatcher=dp)

    assert _stored(factory, "github_self") == "alice"  # nothing gone yet
    assert "Clear your GitHub?" in _edits(fake_bot)[-1].text


async def test_confirming_clears_the_value_and_leaves_the_roster_alone():
    factory = _session_factory()
    _seed_student(factory, github_self="alice", github_sheet="alice-roster")
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, 222,
                         f"{edit.CLEAR_DO_CALLBACK_PREFIX}github", update_id=3),
        dispatcher=dp)

    assert _stored(factory, "github_self") is None
    assert _stored(factory, "github_sheet") == "alice-roster"
    redraw = _edits(fake_bot)[-1]
    assert "✅ GitHub cleared." in redraw.text
    assert "GitHub: alice-roster" in redraw.text  # the roster's value shows now


async def test_clearing_an_unknown_field_is_refused():
    factory = _session_factory()
    _seed_student(factory, gmail="i@gmail.com")
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, 222,
                         f"{edit.CLEAR_DO_CALLBACK_PREFIX}gmail", update_id=3),
        dispatcher=dp)

    assert _stored(factory, "gmail") == "i@gmail.com"
    assert _edits(fake_bot) == []
    assert len(_alerts(fake_bot)) == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_edit_handlers.py -v -k clear`
Expected: FAIL — no handler answers, so `_edits(fake_bot)` stays empty / the value survives.

- [ ] **Step 3: Add the two callbacks**

Append to `src/jbcub_bot/features/directory/edit.py`:

```python
@router.callback_query(F.data.startswith(CLEAR_CALLBACK_PREFIX))
@require_linked
async def cb_clear(cb: CallbackQuery, principal: User, session,
                   state: FSMContext):
    """Ask first: removing a value is destructive, however small."""
    spec = editable_spec(cb.data[len(CLEAR_CALLBACK_PREFIX):])
    if spec is None:
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(render_clear_confirm(spec),
                               reply_markup=clear_confirm_keyboard(spec))
    await cb.answer()


@router.callback_query(F.data.startswith(CLEAR_DO_CALLBACK_PREFIX))
@require_linked
async def cb_clear_do(cb: CallbackQuery, principal: User, session,
                      state: FSMContext):
    spec = editable_spec(cb.data[len(CLEAR_DO_CALLBACK_PREFIX):])
    if spec is None:
        await cb.answer(UNKNOWN_FIELD, show_alert=True)
        return
    setattr(principal, editable_column(spec), None)
    session.commit()
    await state.clear()
    await _show_screen(cb, principal, f"✅ {spec.label} cleared.")
```

Order matters only for readability — `"dir:edit:clear_do:x"` does not start with `"dir:edit:clear:"`, which `test_clear_prefix_does_not_match_the_clear_do_payload` pins down.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_edit_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/edit.py tests/test_edit_handlers.py
git commit -m "feat: clear an edited profile field after confirming"
```

---

### Task 9: Reach the screen from `/me`, and write down what isn't obvious

**Files:**
- Modify: `src/jbcub_bot/features/directory/render.py:45-61` (`me_keyboard`)
- Modify: `src/jbcub_bot/features/directory/handlers.py:58-65` (`cmd_me`)
- Modify: `AGENTS.md`
- Test: `tests/test_directory_render.py`, `tests/test_me_keyboard_integration.py`

**Interfaces:**
- Consumes: `render.EDIT_CALLBACK` (Task 5), `edit.cb_open` (Task 7).
- Produces: `me_keyboard(user, *, interactive: bool = True)` — the `allow_privacy` keyword is gone.

- [ ] **Step 1: Write the failing tests**

In `tests/test_directory_render.py`, replace these four tests (lines 61-88) —
`test_me_keyboard_offers_the_privacy_screen`,
`test_me_keyboard_without_privacy_is_empty_for_a_student`,
`test_me_keyboard_puts_privacy_above_the_admin_buttons`,
`test_me_keyboard_for_an_admin_without_matriculation_has_only_privacy` — with:

```python
def test_me_keyboard_offers_editing_and_privacy():
    kb = me_keyboard(User(first_name="S", last_name="Student",
                          role=Role.STUDENT))
    assert [b.callback_data for row in kb.inline_keyboard for b in row] == [
        EDIT_CALLBACK, PRIVACY_CALLBACK,
    ]


def test_me_keyboard_has_nothing_for_a_student_when_not_interactive():
    assert me_keyboard(User(first_name="S", last_name="Student",
                            role=Role.STUDENT), interactive=False) is None


def test_me_keyboard_puts_self_service_above_the_admin_buttons():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN,
                 matriculation="30000001")
    kb = me_keyboard(admin)
    assert [b.callback_data for b in kb.inline_keyboard[0]] == [
        EDIT_CALLBACK, PRIVACY_CALLBACK,
    ]
    assert [b.callback_data for b in kb.inline_keyboard[1]] == [
        "dir:link:30000001", "dir:reset:30000001",
    ]


def test_me_keyboard_for_an_admin_without_matriculation_has_only_self_service():
    kb = me_keyboard(User(first_name="A", last_name="Admin", role=Role.ADMIN))
    assert len(kb.inline_keyboard) == 1
```

Extend the import with `EDIT_CALLBACK`.

In `tests/test_me_keyboard_integration.py`, add `EDIT_CALLBACK` to the import and two cases:

```python
async def test_me_offers_the_edit_screen():
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 222, "/me"),
                         dispatcher=dp)

    assert EDIT_CALLBACK in _callbacks(fake_bot.sent[0])


async def test_me_under_impersonation_has_no_edit_button():
    factory = _session_factory()
    _seed(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 /me"),
                         dispatcher=dp)

    assert EDIT_CALLBACK not in _callbacks(fake_bot.sent[1])
```

In `tests/test_edit_handlers.py`, add the impersonation case (`_seed_admin_and_student` is already there from Task 7):

```python
async def test_edit_under_impersonation_shows_the_target_read_only():
    factory = _session_factory()
    _seed_admin_and_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999 /edit"),
                         dispatcher=dp)

    # sent[0] is cmd_as's "Showing as ..." notice; sent[1] is /edit's answer.
    shown = fake_bot.sent[1]
    assert "target status" in shown.text   # the target's row, not the admin's
    assert shown.reply_markup is None      # nothing tappable while impersonating
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_directory_render.py tests/test_me_keyboard_integration.py tests/test_edit_handlers.py -v`
Expected: FAIL — `TypeError: me_keyboard() got an unexpected keyword argument 'interactive'`.

- [ ] **Step 3: Put both buttons on the profile**

In `src/jbcub_bot/features/directory/render.py`:

```python
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
```

In `src/jbcub_bot/features/directory/handlers.py`, `cmd_me`:

```python
        reply_markup=me_keyboard(principal, interactive=impersonator is None),
```

- [ ] **Step 4: Run the suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 5: Record the conventions this feature added**

In `AGENTS.md`, under "Conventions that aren't obvious":

- Update the field-ownership bullet: bot-owned fields are `telegram_id`, `handle_observed`, `status_line`, `github_self`, `codeforces_self`, `visibility`. Add: an account field the user can set has two columns — `*_sheet` (roster, in `SHEET_OWNED`) and `*_self` (the user's). `field_value` prefers the user's and shows the roster's beside it when they disagree; `sheets.DRIFT_PAIRS` makes `/sync` report the disagreement. A mapping YAML key is a `User` field name, so a roster GitHub column becomes `github_sheet: "GitHub"`.
- Extend the field-table bullet: `editable=True` + `edit_hint` puts a field on the `/edit` screen, and `accounts.NORMALIZERS` must gain an entry for it (a test in `test_edit.py` enforces the pairing).
- Add a bullet: **A feature that waits for free text must own an FSM state.** `nl_fallback` in `main.py` is registered on the `Dispatcher`, whose own handlers run before every sub-router, so plain text reaches a feature only while `StateFilter(None)` fails — that is, only while the sender is in a state. Exclude commands from a state handler (`~F.text.startswith("/")`) so `/cancel` still works.

- [ ] **Step 6: Verify the bot actually starts**

Run: `uv run python -m jbcub_bot` (needs `.env`), then in Telegram: `/me` → `✏️ Edit my profile` → GitHub → send `github.com/<something real>` → expect `✅ GitHub updated.` Press `q` + Enter to stop.

If a real `.env` isn't available, at minimum confirm the migration applies and the schema matches: `uv run pytest tests/test_init_db.py -v`.

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/features/directory/render.py \
        src/jbcub_bot/features/directory/handlers.py AGENTS.md \
        tests/test_directory_render.py tests/test_me_keyboard_integration.py \
        tests/test_edit_handlers.py
git commit -m "feat: reach the edit screen from /me, document the new conventions"
```

---

## Notes for the implementer

- **`/as` has no FSM state, and a handler must survive that.** `cmd_as` calls
  `dispatcher.propagate_event("message", ...)`, which enters at `dp.message` and
  therefore skips the Dispatcher's *outer* middlewares — `FSMContextMiddleware`
  included. So in that path `state` is missing from the handler data and
  `raw_state` is missing from filter data. A required `state` parameter turns
  every `/as <ref> /edit` into a TypeError, which is why `cmd_edit` and
  `cmd_cancel` default it to `None`. `StateFilter(None)` is fine: absent
  `raw_state` reads as no state, so `/as <ref> <name>` still searches.
- **`tests/test_directory_sync.py` needs no change.** The design doc lists it,
  but it never asserts on drift entries — the only assertion on the drift list
  is in `tests/test_sheets_upsert.py`, updated in Task 2. Don't invent a change
  there.
- **Don't add a mapping for the roster's GitHub column.** No sheet is known to have one; `github_sheet` exists so that adding `github_sheet: "GitHub"` to a cohort YAML later is the whole change.
- **`status_line` is not a two-source field.** It has no roster counterpart, so `editable_column` returns the column name itself. Don't invent `status_line_self`.
- **The user's own message stays in the chat** after a value is saved. Deleting it needs extra rights and surprises people; out of scope.
- **Nothing re-verifies a stored handle.** A `⚠️ Saved` value that was a typo stays a typo until its owner fixes it.
