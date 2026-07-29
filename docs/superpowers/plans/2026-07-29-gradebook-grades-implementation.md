# Gradebook grades — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/sync` imports each cohort's `Gradebook` tab into a new `grades` table, and an admin or teacher can open a matched student's grades (grouped by semester) from their profile; separately, an admin sees a link to the spreadsheet a profile came from.

**Architecture:** `core/gradebook.py` is a pure, dependency-free parser (rows/lists in, rows/lists out) that turns a Gradebook tab into `(term, category, label, value, position)` cells, mirroring `core/matching.py`'s discipline. `features/directory/grades.py` resolves those cells to `User` rows (folded-name matching scoped to `primary_cohort`, replace-the-cohort storage) and also owns the staff-only grades screen (a Router + rendering, alongside `sync_cohort`). `cmd_sync` reads the Gradebook tab per cohort, after that cohort's roster has already committed, and reports failures per-cohort without aborting the sync. The source-link feature reuses the existing `FIELDS`/`visible_fields` gate (`source_link` as an `ADMIN_ONLY` field) and renders its hyperlink via Telegram message entities rather than `parse_mode`, keeping `render_profile`'s plain-text contract and its exact-equality regression test intact.

**Tech Stack:** Python 3.12, aiogram 3, SQLAlchemy 2.0 (ORM + Core `select`/`delete`), Alembic, pytest + pytest-asyncio, uv.

## Global Constraints

- No `parse_mode` anywhere in the bot; the grades screen and the source-link both stay plain text, using aiogram `entities`/`MessageEntity` where a hyperlink is needed.
- `core/gradebook.py` must import neither `aiogram` nor `sqlalchemy` — pure rows-and-lists, testable with `str.lower`.
- `core/gradebook.py` must not import `jbcub_bot.features.directory.matching` — `fold` is passed into `features/directory/grades.py`'s `sync_cohort` as a parameter, the way `mark_departed` takes `today`.
- The Gradebook read range is `A:ZZ` (not `A:Z`/`A:CZ`) — the widest tab is 115 columns.
- Every row in the Gradebook is read; only nameless ones are skipped. Do **not** reuse `sheets._ends_the_roster` — that rule does not apply here.
- Fuzzy name matching is out of scope for grades resolution: unmatched and duplicate names are reported, never guessed.
- `grades` sync replaces a whole cohort at once (`DELETE WHERE cohort = ?`, then insert), scoped by the cohort just read.
- The grades screen is staff-only (admin or teacher) and never reachable from a student's own `/me` — `me_keyboard` never gains the button.
- The grades-sync pass for one cohort must not be able to abort `/sync` or roll back that cohort's roster write — wrap it in `try/except Exception`, log the traceback, tell the admin, continue to the next cohort.
- `render_profile` keeps returning a plain `str`; a separate `profile_entities(viewer, target, text)` returns the entity list. Do not change `render_profile`'s signature.
- Entity offsets/lengths are counted in UTF-16 code units, not Python `len()`.

---

## Task 1: `Grade` model, `User.source_link`, and the Alembic migration

**Files:**
- Modify: `src/jbcub_bot/core/models.py`
- Create: `alembic/versions/e19f6c0a5d3b_gradebook_grades.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `jbcub_bot.core.models.Grade` (columns: `id`, `user_id` FK→`users.id` indexed, `cohort` indexed, `term`, `category`, `label`, `value`, `position`); `User.source_link: str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`:

```python
from jbcub_bot.core.models import Grade


def test_create_and_read_grade(session):
    u = User(last_name="Ivanov", first_name="Ivan", matriculation="30000001")
    session.add(u)
    session.commit()
    g = Grade(user_id=u.id, cohort="2024", term="Fall 2025", category="Mandatory",
              label="Math", value="91%", position=3)
    session.add(g)
    session.commit()
    got = session.get(Grade, g.id)
    assert got.user_id == u.id
    assert got.cohort == "2024"
    assert got.term == "Fall 2025"
    assert got.category == "Mandatory"
    assert got.label == "Math"
    assert got.value == "91%"
    assert got.position == 3


def test_user_source_link_defaults_to_none(session):
    u = User(last_name="Ivanov", first_name="Ivan")
    session.add(u)
    session.commit()
    assert u.source_link is None
    u.source_link = "https://docs.google.com/spreadsheets/d/ABC"
    session.commit()
    session.refresh(u)
    assert u.source_link == "https://docs.google.com/spreadsheets/d/ABC"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_models.py -k "grade or source_link" -v`
Expected: FAIL — `ImportError: cannot import name 'Grade'` (and `source_link` is not a valid keyword for `User`).

- [ ] **Step 3: Add the model**

In `src/jbcub_bot/core/models.py`, change the import line and add the column + class:

```python
from sqlalchemy import JSON, BigInteger, Enum, ForeignKey, Integer, String
```

Add `source_link` right after `primary_cohort`/`past_cohorts` on `User`:

```python
    primary_cohort: Mapped[str | None] = mapped_column(String, index=True)
    past_cohorts: Mapped[list] = mapped_column(JSON, default=list)
    # The sheet row this profile came from: a Cohorts 'Link' for a cohort
    # student, the Rights spreadsheet's id/URL for a Rights-only row. Set by
    # /sync the way primary_cohort already is.
    source_link: Mapped[str | None] = mapped_column(String)
```

Append at the end of the file:

```python
class Grade(Base):
    """One non-empty cell from a cohort's Gradebook tab.

    `position` is the sheet column index and the only ordering: semesters
    sort by the lowest position among their cells, courses by position within
    a semester -- column order is chronological in every cohort, so no date
    is ever parsed.
    """
    __tablename__ = "grades"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    cohort: Mapped[str] = mapped_column(String, index=True)
    term: Mapped[str] = mapped_column(String)
    category: Mapped[str] = mapped_column(String, default="")
    label: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    position: Mapped[int] = mapped_column(Integer)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (all tests, including the two new ones).

- [ ] **Step 5: Write the Alembic migration**

Create `alembic/versions/e19f6c0a5d3b_gradebook_grades.py`:

```python
"""gradebook grades and source_link

Revision ID: e19f6c0a5d3b
Revises: d1a6f04b9c73
Create Date: 2026-07-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e19f6c0a5d3b'
down_revision: Union[str, Sequence[str], None] = 'd1a6f04b9c73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('source_link', sa.String(), nullable=True))
    op.create_table(
        'grades',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('cohort', sa.String(), nullable=False),
        sa.Column('term', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=False),
        sa.Column('label', sa.String(), nullable=False),
        sa.Column('value', sa.String(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_grades_user_id'), 'grades', ['user_id'], unique=False)
    op.create_index(op.f('ix_grades_cohort'), 'grades', ['cohort'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_grades_cohort'), table_name='grades')
    op.drop_index(op.f('ix_grades_user_id'), table_name='grades')
    op.drop_table('grades')
    op.drop_column('users', 'source_link')
```

- [ ] **Step 6: Sanity-check the migration against a scratch database**

Run (from repo root, with a populated `.env` as `AGENTS.md` requires):
`uv run alembic upgrade head`
Expected: applies cleanly, ends on `e19f6c0a5d3b`. Then run `uv run pytest tests/test_init_db.py -v` — `test_migrations_produce_exactly_the_columns_the_model_declares` must still pass (it only compares `users` columns, which now include `source_link` on both sides).

If you ran the upgrade against your real dev database, downgrade it back out afterward: `uv run alembic downgrade d1a6f04b9c73` (skip this if you used a throwaway `DATABASE_URL`).

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/core/models.py alembic/versions/e19f6c0a5d3b_gradebook_grades.py tests/test_models.py
git commit -m "feat: add Grade model and User.source_link"
```

---

## Task 2: `core/gradebook.py` — pure Gradebook tab parsing

**Files:**
- Create: `src/jbcub_bot/core/gradebook.py`
- Test: `tests/test_gradebook_parse.py`

**Interfaces:**
- Produces: `gradebook.MappingError`; `gradebook.Column(index: int, term: str, category: str, label: str)`; `gradebook.GradebookRow(last_name: str, first_name: str, cells: dict[int, str])`; `gradebook.ParsedGradebook(columns: list[Column], rows: list[GradebookRow], ignored_columns: int)`; `gradebook.parse_gradebook(rows: list[list[str]], last_name_column: str, first_name_column: str) -> ParsedGradebook`.

This is the trickiest logic in the feature. The three header rows are, from the top: **term** (row 0, merged cells — Sheets API returns the merge's value only in its leftmost cell and empty strings elsewhere), **category** (row 1, same merge behavior), **label** (row 2, one cell per column). The header row is *found* by scanning the first five rows for the one containing both `last_name_column` and `first_name_column` as literal cell values — this sidesteps needing to know whether identity columns sit at 0–1 or 1–2, because the search is by value, not position. Once found at index `h`, term lives at `rows[h-2]`, category at `rows[h-1]`, label at `rows[h]`.

Per column, forward-fill term and category rightward (reproducing what the merge represents), with three rules:
1. `term`: carry the last non-empty `row0` cell rightward; where no `row0` cell has appeared *yet* (i.e. the carry is still empty), fall back to that column's own `row1` cell. This is what makes the leading identity columns (`Status`, `Last name`, `First name`) end up with an empty term — for them, both `row0` and `row1` are blank until the first semester merge begins.
2. `category`: carry the last non-empty `row1` cell rightward, but the carry resets at every new `term` boundary (whenever `row0` is non-empty). Then, if the resulting `category` equals `term`, drop it to `""` — this is the credit-block columns, where `row1` literally repeats the semester name.
3. `label`: `row2`'s own cell (newlines flattened to spaces), falling back to `category` when blank (this is how the unnamed `CSC Seminars` band gets its name).

A column is kept only if its resolved `term` is non-empty; every column with no term is dropped and counted in `ignored_columns` (this is what discards `Status`, the name columns, and any stray column like `Credits Failed 1st Year (after make-up)` that sits left of a cohort's first band). A column with a non-empty term but no resolvable label (blank `row2` and blank `category`) is dropped too, silently — it isn't expected to occur, but it must not crash or fabricate a label.

Below the header row, every row is read (no `_ends_the_roster`-style break). A row with both `last_name` and `first_name` blank is skipped; every other row is kept, and empty cells are not carried into `GradebookRow.cells`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gradebook_parse.py`:

```python
import pytest

from jbcub_bot.core.gradebook import MappingError, parse_gradebook

# Deliberately mirrors the shape described in the design doc: Status at
# column 0 pushes Last/First name to 1-2 (the offset the other two cohorts
# don't have); row 0 is term, row 1 category, row 2 label, data from row 3.
TERM_ROW = ["", "", "", "Fall 2025", "", "", "Spring 2026", ""]
CATEGORY_ROW = ["", "", "", "Mandatory", "Mandatory", "Fall 2025", "Methods",
                "CSC Seminars"]
LABEL_ROW = ["Status", "Last name", "First name", "Math", "CS 101\nTutorial",
             "Credits EARNED", "Physics", ""]

ROWS = [
    TERM_ROW,
    CATEGORY_ROW,
    LABEL_ROW,
    ["Active", "Ivanov", "Ivan", "91%", "4.33", "", "pass", "IS, CL"],
    [],  # nameless row: must be skipped, not treated as the end of the roster
    ["Departed", "Petrov", "Petr", "", "incomplete", "TC", "", ""],
]


def test_header_row_found_by_content_not_position():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    # Both data rows survived the nameless row in between.
    assert [r.last_name for r in parsed.rows] == ["Ivanov", "Petrov"]


def test_header_row_found_when_identity_columns_start_at_zero():
    rows = [
        ["", "Fall 2025"],
        ["", "Mandatory"],
        ["Last name", "First name", "Math"][:2] + ["Math"],
        ["Ivanov", "Ivan", "91%"],
    ]
    # rebuild explicitly to keep column widths obviously aligned
    rows = [
        ["", "", "Fall 2025"],
        ["", "", "Mandatory"],
        ["Last name", "First name", "Math"],
        ["Ivanov", "Ivan", "91%"],
    ]
    parsed = parse_gradebook(rows, "Last name", "First name")
    assert parsed.rows == [
        __import__("jbcub_bot.core.gradebook", fromlist=["GradebookRow"])
        .GradebookRow(last_name="Ivanov", first_name="Ivan", cells={2: "91%"})
    ]


def test_header_row_not_found_names_what_it_looked_for():
    with pytest.raises(MappingError) as err:
        parse_gradebook([["a", "b"], ["c", "d"]], "Last name", "First name")
    assert "Last name" in str(err.value)
    assert "First name" in str(err.value)


def test_term_fills_rightward_and_falls_back_to_row1_before_any_term():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    by_index = {c.index: c for c in parsed.columns}
    assert by_index[3].term == "Fall 2025"
    assert by_index[4].term == "Fall 2025"  # forward-filled, row 0 blank here
    assert by_index[6].term == "Spring 2026"
    assert by_index[7].term == "Spring 2026"


def test_category_resets_at_term_boundary_and_drops_when_it_equals_term():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    by_index = {c.index: c for c in parsed.columns}
    assert by_index[3].category == "Mandatory"
    assert by_index[4].category == "Mandatory"
    # column 5's row-1 cell repeats the term name -- dropped, not "Fall 2025".
    assert by_index[5].category == ""
    assert by_index[5].label == "Credits EARNED"
    assert by_index[6].category == "Methods"  # reset at the new term


def test_blank_label_falls_back_to_category():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    by_index = {c.index: c for c in parsed.columns}
    assert by_index[7].label == "CSC Seminars"  # row 2 blank there


def test_columns_with_no_term_are_skipped_and_counted():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    kept = {c.index for c in parsed.columns}
    assert kept == {3, 4, 5, 6, 7}  # 0 (Status), 1, 2 (names) excluded
    assert parsed.ignored_columns == 3


def test_newlines_are_flattened_in_the_label():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    by_index = {c.index: c for c in parsed.columns}
    assert by_index[4].label == "CS 101 Tutorial"


def test_empty_cells_are_not_stored():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    ivanov = next(r for r in parsed.rows if r.last_name == "Ivanov")
    assert 5 not in ivanov.cells  # blank Credits EARNED cell for Ivanov
    assert ivanov.cells == {3: "91%", 4: "4.33", 6: "pass", 7: "IS, CL"}


def test_rows_below_a_nameless_row_are_still_imported():
    parsed = parse_gradebook(ROWS, "Last name", "First name")
    petrov = next(r for r in parsed.rows if r.last_name == "Petrov")
    assert petrov.first_name == "Petr"
    assert petrov.cells == {4: "incomplete", 5: "TC"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_gradebook_parse.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jbcub_bot.core.gradebook'`.

- [ ] **Step 3: Implement `core/gradebook.py`**

```python
"""Pure parsing of a cohort's Gradebook tab: rows and lists in, rows and
lists out. No aiogram, no sqlalchemy -- see matching.py for the same
discipline. Resolving a row to a User is features/directory/grades.py's job.
"""

from dataclasses import dataclass


class MappingError(Exception):
    pass


# Scanning the top rows by content, not a fixed index, means the cohort's own
# 0-1 vs 1-2 offset for Last/First name never has to be hardcoded here.
_HEADER_SEARCH_ROWS = 5


def _cell(row: list[str], i: int) -> str:
    return row[i].strip() if i < len(row) else ""


def _flatten(text: str) -> str:
    return " ".join(text.split())


def _find_header_row(rows: list[list[str]], last_col: str, first_col: str) -> int:
    for i, row in enumerate(rows[:_HEADER_SEARCH_ROWS]):
        cells = {c.strip() for c in row}
        if last_col in cells and first_col in cells:
            return i
    raise MappingError(
        f"Gradebook header row not found: expected {last_col!r} and "
        f"{first_col!r} together in one of the first {_HEADER_SEARCH_ROWS} rows"
    )


def _find_identity_columns(label_row: list[str], last_col: str,
                           first_col: str) -> tuple[int, int]:
    last_idx = first_idx = None
    for i, cell in enumerate(label_row):
        stripped = cell.strip()
        if stripped == last_col:
            last_idx = i
        elif stripped == first_col:
            first_idx = i
    if last_idx is None or first_idx is None:
        raise MappingError(
            f"Gradebook header row is missing {last_col!r} or {first_col!r}"
        )
    return last_idx, first_idx


@dataclass(frozen=True)
class Column:
    index: int
    term: str
    category: str
    label: str


def _parse_columns(rows: list[list[str]], header_row: int) -> tuple[list[Column], int]:
    term_row = rows[header_row - 2] if header_row >= 2 else []
    category_row = rows[header_row - 1] if header_row >= 1 else []
    label_row = rows[header_row]
    width = max(len(term_row), len(category_row), len(label_row))

    columns: list[Column] = []
    ignored = 0
    term_carry = ""
    category_carry = ""
    for i in range(width):
        term_cell = _cell(term_row, i)
        category_cell = _cell(category_row, i)
        label_cell = _flatten(_cell(label_row, i))

        if term_cell:
            term_carry = term_cell
            category_carry = ""  # a new term starts a fresh category run
        term = term_carry or category_cell

        if category_cell:
            category_carry = category_cell
        category = category_carry
        if category == term:
            category = ""  # the credit blocks repeat the term as category

        if not term:
            ignored += 1
            continue
        label = label_cell or category
        if not label:
            continue
        columns.append(Column(index=i, term=term, category=category, label=label))
    return columns, ignored


@dataclass(frozen=True)
class GradebookRow:
    last_name: str
    first_name: str
    cells: dict[int, str]


@dataclass(frozen=True)
class ParsedGradebook:
    columns: list[Column]
    rows: list[GradebookRow]
    ignored_columns: int


def parse_gradebook(rows: list[list[str]], last_name_column: str,
                    first_name_column: str) -> ParsedGradebook:
    """Turn a Gradebook tab's raw rows into columns and per-student cells.

    Every row past the header is read -- the roster-end rule
    (sheets._ends_the_roster) does not apply here: departed students sit
    below a blank row too, and their grades are wanted just as much.
    """
    header_row = _find_header_row(rows, last_name_column, first_name_column)
    columns, ignored = _parse_columns(rows, header_row)
    last_idx, first_idx = _find_identity_columns(
        rows[header_row], last_name_column, first_name_column)

    data_rows = []
    for raw in rows[header_row + 1:]:
        last = _cell(raw, last_idx)
        first = _cell(raw, first_idx)
        if not last and not first:
            continue
        cells = {}
        for col in columns:
            value = _cell(raw, col.index)
            if value:
                cells[col.index] = value
        data_rows.append(GradebookRow(last_name=last, first_name=first, cells=cells))

    return ParsedGradebook(columns=columns, rows=data_rows, ignored_columns=ignored)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_gradebook_parse.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/gradebook.py tests/test_gradebook_parse.py
git commit -m "feat: parse a cohort's Gradebook tab into columns and rows"
```

---

## Task 3: `source_link` in `sheets.py`, `sheet_url`, and `gradebook_tab` setting

**Files:**
- Modify: `src/jbcub_bot/core/sheets.py`, `src/jbcub_bot/core/config.py`, `.env.example`
- Test: `tests/test_sheets_normalize.py`, `tests/test_config.py`

**Interfaces:**
- Produces: `sheets.sheet_url(link: str) -> str`; `"source_link"` added to `sheets.SHEET_OWNED` (and therefore `sheets.KNOWN_FIELDS`); `Settings.gradebook_tab: str = "Gradebook"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sheets_normalize.py`:

```python
from jbcub_bot.core.sheets import SHEET_OWNED, sheet_url


def test_source_link_is_sheet_owned():
    assert "source_link" in SHEET_OWNED


def test_sheet_url_keeps_a_full_url_as_is():
    url = "https://docs.google.com/spreadsheets/d/ABC123/edit#gid=42"
    assert sheet_url(url) == url


def test_sheet_url_builds_a_url_from_a_bare_id():
    assert sheet_url("ABC123") == "https://docs.google.com/spreadsheets/d/ABC123"


def test_sheet_url_strips_whitespace_around_a_bare_id():
    assert sheet_url("  ABC123  ") == "https://docs.google.com/spreadsheets/d/ABC123"
```

Append to `tests/test_config.py` (inside `test_settings_load_from_env`, add one assertion; the plan shows it as a new line in that existing test):

```python
    assert s.gradebook_tab == "Gradebook"  # default
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_sheets_normalize.py tests/test_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'sheet_url'`, `AssertionError` for `gradebook_tab`, and `"source_link" in SHEET_OWNED` is `False`.

- [ ] **Step 3: Implement**

In `src/jbcub_bot/core/sheets.py`, update `SHEET_OWNED` and add `sheet_url`:

```python
SHEET_OWNED = (
    "last_name", "first_name", "handle_sheet", "gmail", "cubemail",
    "github_sheet", "codeforces_sheet",
    "birthday", "citizenship", "comment",
    "primary_cohort", "past_cohorts", "role", "source_link",
)
```

Add near `extract_sheet_id`:

```python
def sheet_url(link: str) -> str:
    """A `Link`/id string, normalized into a clickable spreadsheet URL.

    A full URL is kept exactly as given -- including any `#gid` an admin
    aimed at a particular tab. A bare spreadsheet id (what extract_sheet_id
    already accepts) is turned into one.
    """
    link = (link or "").strip()
    if link.startswith("http://") or link.startswith("https://"):
        return link
    return f"https://docs.google.com/spreadsheets/d/{extract_sheet_id(link)}"
```

In `src/jbcub_bot/core/config.py`, add the new setting next to `rights_tab`:

```python
    cohorts_tab: str = "Cohorts"
    rights_tab: str = "Rights"
    gradebook_tab: str = "Gradebook"
```

In `.env.example`, add after `RIGHTS_TAB`:

```
GRADEBOOK_TAB=Gradebook
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_sheets_normalize.py tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/sheets.py src/jbcub_bot/core/config.py .env.example \
        tests/test_sheets_normalize.py tests/test_config.py
git commit -m "feat: source_link as a sheet-owned field, sheet_url, gradebook_tab setting"
```

---

## Task 4: `features/directory/grades.py` — resolve and store a cohort's grades

**Files:**
- Create: `src/jbcub_bot/features/directory/grades.py`
- Test: `tests/test_gradebook_store.py`

**Interfaces:**
- Consumes: `jbcub_bot.core.gradebook.parse_gradebook`, `.MappingError`; `jbcub_bot.core.models.Grade`, `User`.
- Produces: `grades.GradesSyncReport(matched: int, cells: int, unmatched: list[str], duplicates: list[str], ignored_columns: int)`; `grades.sync_cohort(session, cohort: str, rows: list[list[str]], mapping: dict, fold) -> GradesSyncReport`.

`mapping` is the same per-cohort dict `sheets.parse_cohort_index` already produces (e.g. `{"last_name": "Last name", "first_name": "First name", "matriculation": "Matriculation Num.", ...}`) — only `mapping["last_name"]` and `mapping["first_name"]` are used here, to tell `parse_gradebook` which column names to look for.

Candidates are every `User` whose `primary_cohort == cohort` (departed included) — the same scoping `sheets.mark_departed` uses, because a name is only unambiguous inside one cohort. A sheet row whose folded `(last_name, first_name)` is not unique *within the sheet itself* is a duplicate (reported, skipped, never resolved); one that doesn't match exactly one candidate is unmatched (reported, skipped) — this also covers a name that matches a user in a *different* cohort, since that user isn't in this cohort's candidate set at all.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gradebook_store.py`:

```python
from jbcub_bot.core.models import Grade, User
from jbcub_bot.features.directory import matching
from jbcub_bot.features.directory.grades import sync_cohort

TERM_ROW = ["", "", "", "Fall 2025", "", "Spring 2026"]
CATEGORY_ROW = ["", "", "", "Mandatory", "Fall 2025", "Methods"]
LABEL_ROW = ["Status", "Last name", "First name", "Math", "Credits EARNED",
             "Physics"]

MAPPING = {"last_name": "Last name", "first_name": "First name"}


def _rows(*data_rows):
    return [TERM_ROW, CATEGORY_ROW, LABEL_ROW, *data_rows]


def test_a_matched_row_is_stored_with_folded_name_including_departed(session):
    session.add(User(last_name="Ivanov", first_name="Ivan",
                     primary_cohort="2024", departed_at="2026-07-28"))
    session.commit()
    rows = _rows(["Active", "ivanov", "IVAN", "91%", "", "pass"])

    report = sync_cohort(session, "2024", rows, MAPPING, matching.fold)

    assert report.matched == 1
    assert report.cells == 2  # Math + Physics; Credits EARNED cell is blank
    assert report.unmatched == []
    assert report.duplicates == []
    user = session.query(User).filter_by(last_name="Ivanov").one()
    stored = session.query(Grade).filter_by(user_id=user.id).order_by(Grade.position).all()
    assert [(g.term, g.category, g.label, g.value, g.position) for g in stored] == [
        ("Fall 2025", "Mandatory", "Math", "91%", 3),
        ("Spring 2026", "Methods", "Physics", "pass", 5),
    ]


def test_a_name_belonging_to_another_cohort_is_not_matched(session):
    session.add(User(last_name="Sidorov", first_name="Sergey",
                     primary_cohort="2099"))
    session.commit()
    rows = _rows(["Active", "Sidorov", "Sergey", "91%", "", "pass"])

    report = sync_cohort(session, "2024", rows, MAPPING, matching.fold)

    assert report.matched == 0
    assert report.unmatched == ["Sidorov Sergey"]
    other_cohort_user = session.query(User).filter_by(last_name="Sidorov").one()
    assert session.query(Grade).filter_by(user_id=other_cohort_user.id).count() == 0


def test_an_unmatched_name_is_reported_not_guessed(session):
    rows = _rows(["Active", "Nobody", "Really", "91%", "", "pass"])

    report = sync_cohort(session, "2024", rows, MAPPING, matching.fold)

    assert report.matched == 0
    assert report.unmatched == ["Nobody Really"]


def test_duplicate_names_within_one_gradebook_are_reported_and_skipped(session):
    session.add_all([
        User(last_name="Kuznetsov", first_name="Ivan", primary_cohort="2024"),
    ])
    session.commit()
    rows = _rows(
        ["Active", "Kuznetsov", "Ivan", "91%", "", "pass"],
        ["Active", "Kuznetsov", "Ivan", "50%", "", "fail"],
    )

    report = sync_cohort(session, "2024", rows, MAPPING, matching.fold)

    assert report.matched == 0
    assert report.duplicates == ["Kuznetsov Ivan", "Kuznetsov Ivan"]
    user = session.query(User).filter_by(last_name="Kuznetsov").one()
    assert session.query(Grade).filter_by(user_id=user.id).count() == 0


def test_columns_outside_a_semester_band_are_counted_in_the_report(session):
    session.add(User(last_name="Ivanov", first_name="Ivan", primary_cohort="2024"))
    session.commit()
    rows = _rows(["Active", "Ivanov", "Ivan", "91%", "", "pass"])

    report = sync_cohort(session, "2024", rows, MAPPING, matching.fold)

    assert report.ignored_columns == 3  # Status, Last name, First name


def test_replacing_a_cohort_drops_its_stale_rows_and_leaves_others_alone(session):
    ivan = User(last_name="Ivanov", first_name="Ivan", primary_cohort="2024")
    session.add(ivan)
    session.commit()
    session.add_all([
        Grade(user_id=ivan.id, cohort="2024", term="Fall 2024", category="",
              label="Old Course", value="stale", position=99),
        Grade(user_id=ivan.id, cohort="2023", term="Fall 2023", category="",
              label="Other Cohort", value="untouched", position=1),
    ])
    session.commit()
    rows = _rows(["Active", "Ivanov", "Ivan", "91%", "", "pass"])

    sync_cohort(session, "2024", rows, MAPPING, matching.fold)

    remaining = session.query(Grade).filter_by(user_id=ivan.id).all()
    labels = {g.label for g in remaining}
    assert "Old Course" not in labels
    assert "Other Cohort" in labels
    assert "Math" in labels
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_gradebook_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jbcub_bot.features.directory.grades'`.

- [ ] **Step 3: Implement `sync_cohort`**

Create `src/jbcub_bot/features/directory/grades.py` with this content (the screen — Task 7 — appends to the same file):

```python
"""Storage and resolution for a cohort's Gradebook tab, and (from Task 7 on)
the staff-only grades screen reached from a profile.

Resolution lives here rather than in core/gradebook.py: it needs
matching.fold, which core must not import. fold is passed in as a parameter,
the way sheets.mark_departed takes today, so core/gradebook.py stays
dependency-free.
"""

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import delete, select

from jbcub_bot.core import gradebook
from jbcub_bot.core.models import Grade, User


@dataclass
class GradesSyncReport:
    matched: int = 0
    cells: int = 0
    unmatched: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)
    ignored_columns: int = 0


def sync_cohort(session, cohort: str, rows: list[list[str]], mapping: dict,
                fold) -> GradesSyncReport:
    """Replace `cohort`'s grades with what `rows` says.

    Candidates are every User whose primary_cohort is this cohort, departed
    included -- the same scoping mark_departed uses, and for the same reason:
    a name is only unambiguous inside one cohort. A student listed in two
    cohorts' Gradebooks matches only where their primary_cohort points, and is
    reported unmatched here if this isn't that cohort.
    """
    parsed = gradebook.parse_gradebook(
        rows, mapping["last_name"], mapping["first_name"])
    report = GradesSyncReport(ignored_columns=parsed.ignored_columns)

    names = [(fold(r.last_name), fold(r.first_name)) for r in parsed.rows]
    dup_keys = {key for key, count in Counter(names).items() if count > 1}

    candidates = session.scalars(
        select(User).where(User.primary_cohort == cohort)).all()
    by_name: dict[tuple[str, str], list[User]] = {}
    for user in candidates:
        by_name.setdefault((fold(user.last_name), fold(user.first_name)), []) \
            .append(user)

    session.execute(delete(Grade).where(Grade.cohort == cohort))

    columns_by_index = {c.index: c for c in parsed.columns}
    for row, key in zip(parsed.rows, names):
        label = f"{row.last_name} {row.first_name}"
        if key in dup_keys:
            report.duplicates.append(label)
            continue
        matches = by_name.get(key)
        if not matches or len(matches) > 1:
            report.unmatched.append(label)
            continue
        user = matches[0]
        report.matched += 1
        for index, value in row.cells.items():
            col = columns_by_index[index]
            session.add(Grade(
                user_id=user.id, cohort=cohort, term=col.term,
                category=col.category, label=col.label, value=value,
                position=index,
            ))
            report.cells += 1
    return report
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_gradebook_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/grades.py tests/test_gradebook_store.py
git commit -m "feat: resolve and store a cohort's grades"
```

---

## Task 5: `is_staff` and the `source_link` profile field

**Files:**
- Modify: `src/jbcub_bot/features/directory/visibility.py`
- Test: `tests/test_visibility.py`

**Interfaces:**
- Produces: `visibility.is_staff(user: User) -> bool`; `"source_link"` added to `FIELDS` (category `ADMIN_ONLY`, positioned right after `primary_cohort`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_visibility.py`, update the existing field-order test and add a new one for `is_staff`:

```python
def test_field_order_matches_the_rendered_profile_order():
    assert [f.name for f in visibility.FIELDS] == [
        "departed_at",
        "first_name", "last_name", "role", "primary_cohort", "source_link",
        "telegram", "telegram_id", "status_line",
        "gmail", "cubemail", "github", "codeforces",
        "matriculation", "birthday", "citizenship", "comment",
    ]
```

Add near the top-of-file tests:

```python
def test_is_staff_true_for_admin_and_teacher_false_for_student():
    assert visibility.is_staff(_u(role=Role.ADMIN)) is True
    assert visibility.is_staff(_u(role=Role.TEACHER)) is True
    assert visibility.is_staff(_u(role=Role.STUDENT)) is False


def test_source_link_is_admin_only_and_not_shown_to_a_student():
    viewer = _u(role=Role.STUDENT, primary_cohort="2024")
    target = _u(role=Role.STUDENT, primary_cohort="2024",
                source_link="https://docs.google.com/spreadsheets/d/ABC")
    assert "source_link" not in visible_fields(viewer, target)


def test_source_link_is_shown_to_an_admin():
    viewer = _u(role=Role.ADMIN)
    target = _u(role=Role.STUDENT,
                source_link="https://docs.google.com/spreadsheets/d/ABC")
    assert visible_fields(viewer, target)["source_link"] == \
        "https://docs.google.com/spreadsheets/d/ABC"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_visibility.py -v`
Expected: FAIL — the field-order test fails (no `source_link` in `FIELDS`), and `is_staff` doesn't exist.

- [ ] **Step 3: Implement**

In `src/jbcub_bot/features/directory/visibility.py`, add `source_link` to `FIELDS` right after `primary_cohort`:

```python
    FieldSpec("primary_cohort", "Cohort", Category.ALWAYS),
    # Folded into the cohort line (or its own fallback) by render_profile --
    # never printed as "Source: <label>: <value>" on its own.
    FieldSpec("source_link", "Source", Category.ADMIN_ONLY),
```

Add near `_is_self` (or any other small helper — grouping by "who may see this" fits beside the category constants):

```python
def is_staff(user: User) -> bool:
    return user.role is Role.ADMIN or user.role is Role.TEACHER
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_visibility.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/visibility.py tests/test_visibility.py
git commit -m "feat: is_staff helper and source_link as an admin-only profile field"
```

---

## Task 6: `render.py` — `profile_keyboard`, `profile_entities`, the `Source:` line

**Files:**
- Modify: `src/jbcub_bot/features/directory/render.py`
- Test: `tests/test_directory_render.py`

**Interfaces:**
- Consumes: `visibility.is_staff`, `visibility.BY_NAME`; `jbcub_bot.core.sheets.sheet_url`.
- Produces: `render.GRADES_CALLBACK = "dir:grades"`; `render.GRADES_BACK_CALLBACK = "dir:grades_back"`; `render.profile_keyboard(viewer: User, target: User, *, show_grades: bool) -> InlineKeyboardMarkup | None`; `render.profile_entities(viewer: User, target: User, text: str) -> list[MessageEntity]`; `render_profile` gains the `Source: Rights sheet` fallback line for an admin viewing a target with no `primary_cohort`.

`profile_keyboard` does **not** replace `admin_keyboard`/`me_keyboard` — it's a new function used where a profile is rendered for *someone other than the viewer themselves* (`name_search`, `cb_admin_back`'s non-self branch, the grades screen's Back button). `me_keyboard` is untouched: a student must never see their own grades button, and neither does anyone viewing their own `/me`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_directory_render.py`:

```python
from jbcub_bot.features.directory.render import profile_entities, profile_keyboard


# --- profile_keyboard -------------------------------------------------------

def test_profile_keyboard_shows_grades_row_for_a_teacher_when_target_has_rows():
    viewer = User(first_name="T", last_name="Teacher", role=Role.TEACHER)
    target = User(first_name="I", last_name="Ivanov", role=Role.STUDENT,
                  matriculation="30000001")
    kb = profile_keyboard(viewer, target, show_grades=True)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "dir:grades:30000001:-1" in datas


def test_profile_keyboard_omits_grades_row_for_a_student_viewer():
    viewer = User(first_name="S", last_name="Student", role=Role.STUDENT)
    target = User(first_name="I", last_name="Ivanov", role=Role.STUDENT,
                  matriculation="30000001")
    kb = profile_keyboard(viewer, target, show_grades=True)
    assert kb is None


def test_profile_keyboard_omits_grades_row_when_the_target_has_no_rows():
    viewer = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    target = User(first_name="I", last_name="Ivanov", role=Role.STUDENT,
                  matriculation="30000001")
    kb = profile_keyboard(viewer, target, show_grades=False)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert datas == ["dir:admin:30000001"]  # admin row only


def test_profile_keyboard_combines_grades_and_admin_rows_for_an_admin():
    viewer = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    target = User(first_name="I", last_name="Ivanov", role=Role.STUDENT,
                  matriculation="30000001")
    kb = profile_keyboard(viewer, target, show_grades=True)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert datas == ["dir:grades:30000001:-1", "dir:admin:30000001"]


# --- render_profile's Source: fallback --------------------------------------

def test_render_profile_gives_a_cohortless_staff_row_a_source_line():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    staff = User(first_name="Petya", last_name="Teacher", role=Role.TEACHER,
                 source_link="RIGHTS_ID")
    text = render_profile(admin, staff)
    assert "Source: Rights sheet" in text


def test_render_profile_non_admin_gets_no_source_line_for_a_cohortless_row():
    student = User(first_name="S", last_name="Student", role=Role.STUDENT,
                   primary_cohort="2024")
    staff = User(first_name="Petya", last_name="Teacher", role=Role.TEACHER,
                 source_link="RIGHTS_ID")
    assert "Source:" not in render_profile(student, staff)


# --- profile_entities --------------------------------------------------------

def test_profile_entities_empty_for_a_non_admin():
    viewer = User(first_name="V", last_name="Viewer", role=Role.STUDENT,
                  primary_cohort="2024")
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  primary_cohort="2024", source_link="ABC")
    text = render_profile(viewer, target)
    assert profile_entities(viewer, target, text) == []


def test_profile_entities_link_covers_exactly_the_cohort_value():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    target = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
                  primary_cohort="sdt-2023-2026", source_link="ABC123")
    text = render_profile(admin, target)
    entities = profile_entities(admin, target, text)
    assert len(entities) == 1
    e = entities[0]
    assert e.type == "text_link"
    assert e.url == "https://docs.google.com/spreadsheets/d/ABC123"
    value = "sdt-2023-2026"
    prefix = text[:text.index(f"Cohort: {value}")] + "Cohort: "
    assert e.offset == len(prefix.encode("utf-16-le")) // 2
    assert e.length == len(value.encode("utf-16-le")) // 2


def test_profile_entities_offset_accounts_for_the_departed_marker_above_it():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    target = User(first_name="Eve", last_name="Expelled", role=Role.STUDENT,
                  primary_cohort="2024", source_link="ABC123",
                  departed_at="2026-07-28")
    text = render_profile(admin, target)
    entities = profile_entities(admin, target, text)
    assert len(entities) == 1
    value = "2024"
    prefix = text[:text.index(f"Cohort: {value}")] + "Cohort: "
    assert entities[0].offset == len(prefix.encode("utf-16-le")) // 2


def test_profile_entities_covers_the_source_label_for_a_cohortless_staff_row():
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    staff = User(first_name="Petya", last_name="Teacher", role=Role.TEACHER,
                 source_link="RIGHTS_ID")
    text = render_profile(admin, staff)
    entities = profile_entities(admin, staff, text)
    assert len(entities) == 1
    e = entities[0]
    assert e.url == "https://docs.google.com/spreadsheets/d/RIGHTS_ID"
    label = "Rights sheet"
    prefix = text[:text.index(f"Source: {label}")] + "Source: "
    assert e.offset == len(prefix.encode("utf-16-le")) // 2
    assert e.length == len(label.encode("utf-16-le")) // 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_directory_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'profile_keyboard'` (and `profile_entities`).

- [ ] **Step 3: Implement**

In `src/jbcub_bot/features/directory/render.py`, replace the current top-of-file imports —

```python
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.visibility import FIELDS, visible_fields
```

— with:

```python
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
)

from jbcub_bot.core import sheets
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.directory.visibility import BY_NAME, FIELDS, is_staff, visible_fields

GRADES_CALLBACK = "dir:grades"
GRADES_BACK_CALLBACK = "dir:grades_back"

_SOURCE_LABEL = "Source"
_RIGHTS_SHEET_LABEL = "Rights sheet"
```

Then replace the whole `render_profile` function with:

```python
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
            continue  # folded into the cohort line, or its fallback below
        if spec.name == "primary_cohort":
            value = fields.get("primary_cohort")
            if value:
                lines.append(f"{spec.label}: {value}")
            elif "source_link" in fields:
                # An admin viewing a Rights-only row: no cohort to attach the
                # link to, so it gets its own line instead.
                lines.append(f"{_SOURCE_LABEL}: {_RIGHTS_SHEET_LABEL}")
            continue
        value = fields.get(spec.name)
        if value in (None, ""):
            continue
        if hasattr(value, "value"):  # enum -> its value
            value = value.value
        lines.append(f"{spec.label}: {value}")
    return "\n".join(lines)
```

Add the new keyboard and entities functions (near `admin_keyboard`):

```python
def grades_row(matriculation: str) -> list[InlineKeyboardButton]:
    # -1 asks the grades screen for the latest semester -- see grades.py.
    return [InlineKeyboardButton(
        text="\U0001F4CA Grades",
        callback_data=f"{GRADES_CALLBACK}:{matriculation}:-1")]


def profile_keyboard(viewer: User, target: User, *,
                     show_grades: bool) -> InlineKeyboardMarkup | None:
    """Keyboard for a profile rendered for someone other than the viewer.

    Distinct from me_keyboard, which never offers Grades -- a student must
    not see their own, and /as's non-interactive /me shares that keyboard.
    """
    rows = []
    if show_grades and is_staff(viewer) and target.matriculation:
        rows.append(grades_row(target.matriculation))
    if viewer.role is Role.ADMIN:
        admin = admin_keyboard(target)
        if admin is not None:
            rows.extend(admin.inline_keyboard)
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def profile_entities(viewer: User, target: User, text: str) -> list[MessageEntity]:
    """The one hyperlink render_profile's text can carry: the source sheet.

    Entities, not parse_mode -- the bot sets no parse_mode anywhere, and
    turning HTML on for this one line would mean escaping every other value
    in every message. render_profile's text is unaffected either way, which
    is what keeps its five tests -- one an exact-equality anchor -- holding.
    """
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
    idx = text.index(line)
    offset = _utf16_len(text[:idx] + marker)
    length = _utf16_len(value)
    url = sheets.sheet_url(target.source_link)
    return [MessageEntity(type="text_link", offset=offset, length=length, url=url)]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_directory_render.py -v`
Expected: PASS (including the pre-existing exact-equality anchor test, unaffected since neither target in that test lacks a cohort).

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/render.py tests/test_directory_render.py
git commit -m "feat: profile_keyboard, profile_entities, and the Source: fallback line"
```

---

## Task 7: The grades screen — grouping, rendering, keyboard, router

**Files:**
- Modify: `src/jbcub_bot/features/directory/grades.py`
- Test: `tests/test_grades_screen.py`

**Interfaces:**
- Consumes: `render.GRADES_CALLBACK`, `render.GRADES_BACK_CALLBACK`, `render.profile_keyboard`, `render.profile_entities`, `render.render_profile`; `visibility.is_staff`; `screens.EXPIRED`; `identity.find_by_matriculation`.
- Produces: `grades.has_grades(session, user_id: int) -> bool`; `grades.load_grades(session, user_id: int) -> list[Grade]`; `grades.group_by_term(rows: list[Grade]) -> dict[str, list[Grade]]`; `grades.router` (aiogram `Router`, callbacks on `dir:grades:` and `dir:grades_back:`).

The callback format is `dir:grades:<matriculation>:<term_index>`. `<term_index>` is the semester's position in that student's *own* ordered term list (terms sorted by the lowest `position` among their `Grade` rows — which falls out for free by loading grades pre-sorted by `position` and grouping into a `dict`, since Python dicts preserve first-insertion order). `-1` is the sentinel the profile's own `📊 Grades` button uses to mean "the latest semester" (Python's own list-indexing convention for "last"); the semester-switch buttons on the grades screen itself use real `0..N-1` indices. Any index that doesn't resolve (`IndexError`/`ValueError`, e.g. grades were re-synced smaller between render and tap) answers `screens.EXPIRED`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_grades_screen.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

from jbcub_bot.core.models import Grade, Role, User
from jbcub_bot.features.directory import grades
from jbcub_bot.features.directory.render import profile_keyboard
from jbcub_bot.features.directory.screens import EXPIRED


def _student_with_grades(session):
    u = User(first_name="Ivan", last_name="Ivanov", role=Role.STUDENT,
             matriculation="30000001")
    session.add(u)
    session.commit()
    session.add_all([
        Grade(user_id=u.id, cohort="2024", term="Fall 2025", category="Mandatory",
              label="Math", value="91%", position=3),
        Grade(user_id=u.id, cohort="2024", term="Fall 2025", category="Mandatory",
              label="CS", value="4.33", position=4),
        Grade(user_id=u.id, cohort="2024", term="Spring 2026", category="Methods",
              label="Physics", value="pass", position=6),
    ])
    session.commit()
    return u


# --- has_grades / grouping / ordering ---------------------------------------

def test_has_grades_true_only_with_at_least_one_row(session):
    u = _student_with_grades(session)
    assert grades.has_grades(session, u.id) is True
    other = User(first_name="No", last_name="Grades", role=Role.STUDENT)
    session.add(other)
    session.commit()
    assert grades.has_grades(session, other.id) is False


def test_grouping_and_order_come_from_position(session):
    u = _student_with_grades(session)
    groups = grades.group_by_term(grades.load_grades(session, u.id))
    assert list(groups) == ["Fall 2025", "Spring 2026"]
    assert [g.label for g in groups["Fall 2025"]] == ["Math", "CS"]


# --- keyboard visibility (also exercised via profile_keyboard, Task 6) -----

def test_button_present_for_teacher_and_admin_absent_for_student(session):
    target = _student_with_grades(session)
    show = grades.has_grades(session, target.id)
    for role in (Role.TEACHER, Role.ADMIN):
        viewer = User(first_name="V", last_name="Viewer", role=role)
        kb = profile_keyboard(viewer, target, show_grades=show)
        datas = [b.callback_data for row in kb.inline_keyboard for b in row]
        assert "dir:grades:30000001:-1" in datas
    student_viewer = User(first_name="V", last_name="Viewer", role=Role.STUDENT)
    assert profile_keyboard(student_viewer, target, show_grades=show) is None


def test_button_absent_when_the_target_has_no_rows(session):
    target = User(first_name="No", last_name="Grades", role=Role.STUDENT,
                  matriculation="30000002")
    session.add(target)
    session.commit()
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    show = grades.has_grades(session, target.id)
    kb = profile_keyboard(admin, target, show_grades=show)
    assert kb is None


# --- the router --------------------------------------------------------------

def _cb(data):
    return SimpleNamespace(
        data=data, answer=AsyncMock(),
        message=SimpleNamespace(edit_text=AsyncMock()),
    )


async def test_opening_with_the_sentinel_shows_the_latest_semester(session):
    _student_with_grades(session)
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    cb = _cb("dir:grades:30000001:-1")

    await grades.cb_grades(cb, principal=admin, session=session)

    text = cb.message.edit_text.await_args.args[0]
    assert text.startswith("Spring 2026")
    assert "Physics: pass" in text
    cb.answer.assert_awaited_once()


async def test_switching_to_an_earlier_semester_by_explicit_index(session):
    _student_with_grades(session)
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    cb = _cb("dir:grades:30000001:0")

    await grades.cb_grades(cb, principal=admin, session=session)

    text = cb.message.edit_text.await_args.args[0]
    assert text.startswith("Fall 2025")
    assert "Math: 91%" in text
    assert "CS: 4.33" in text


async def test_a_stale_index_answers_expired(session):
    _student_with_grades(session)
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    cb = _cb("dir:grades:30000001:7")

    await grades.cb_grades(cb, principal=admin, session=session)

    cb.answer.assert_awaited_once_with(EXPIRED, show_alert=True)
    cb.message.edit_text.assert_not_awaited()


async def test_a_student_tapping_the_grades_callback_is_refused(session):
    _student_with_grades(session)
    student = User(first_name="S", last_name="Student", role=Role.STUDENT)
    cb = _cb("dir:grades:30000001:-1")

    await grades.cb_grades(cb, principal=student, session=session)

    cb.answer.assert_awaited_once_with("Staff only.", show_alert=True)
    cb.message.edit_text.assert_not_awaited()


async def test_a_bootstrap_admin_with_no_row_is_allowed_through(session):
    """A bootstrap admin's principal has id is None -- require_linked would
    refuse it, so this screen checks role only (see render.py's docstring)."""
    _student_with_grades(session)
    bootstrap_admin = User(last_name="Bootstrap", role=Role.ADMIN)  # id is None
    cb = _cb("dir:grades:30000001:-1")

    await grades.cb_grades(cb, principal=bootstrap_admin, session=session)

    cb.message.edit_text.assert_awaited_once()
    cb.answer.assert_awaited_once()


async def test_back_returns_to_the_profile_with_keyboard_and_entities(session):
    target = _student_with_grades(session)
    target.source_link = "ABC"
    session.commit()
    admin = User(first_name="A", last_name="Admin", role=Role.ADMIN)
    cb = _cb("dir:grades_back:30000001")

    await grades.cb_grades_back(cb, principal=admin, session=session)

    kwargs = cb.message.edit_text.await_args.kwargs
    assert "Name: Ivan Ivanov" in cb.message.edit_text.await_args.args[0]
    assert kwargs["entities"]  # the cohort/source link entity survived the round trip
    datas = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard
             for b in row]
    assert "dir:grades:30000001:-1" in datas
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_grades_screen.py -v`
Expected: FAIL — `AttributeError`/`ImportError` for `has_grades`, `load_grades`, `group_by_term`, `cb_grades`, `cb_grades_back`, `router`.

- [ ] **Step 3: Implement**

Append to `src/jbcub_bot/features/directory/grades.py` (imports go at the top of the file, alongside the existing ones):

```python
from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from jbcub_bot.core import identity
from jbcub_bot.features.directory.render import (
    GRADES_BACK_CALLBACK,
    GRADES_CALLBACK,
    profile_entities,
    profile_keyboard,
    render_profile,
)
from jbcub_bot.features.directory.screens import EXPIRED
from jbcub_bot.features.directory.visibility import is_staff
```

Then append the screen logic:

```python
_TERM_BUTTONS_PER_ROW = 3
_TEXT_LIMIT = 4096
_TRUNCATE_MARK = "\n… (truncated)"


def load_grades(session, user_id: int) -> list[Grade]:
    return list(session.scalars(
        select(Grade).where(Grade.user_id == user_id).order_by(Grade.position)
    ).all())


def group_by_term(rows: list[Grade]) -> dict:
    """Terms in position order -- dicts keep first-insertion order, and
    `rows` is already sorted by position, so this is the order for free."""
    groups: dict[str, list[Grade]] = {}
    for g in rows:
        groups.setdefault(g.term, []).append(g)
    return groups


def has_grades(session, user_id: int) -> bool:
    return session.scalar(
        select(Grade.id).where(Grade.user_id == user_id).limit(1)) is not None


def _render_body(rows: list[Grade]) -> str:
    lines = []
    last_category = None
    for g in rows:
        if g.category:
            if g.category != last_category:
                lines.append(g.category)
                last_category = g.category
        else:
            last_category = None
        lines.append(f"• {g.label}: {g.value}")
    return "\n".join(lines)


def render_screen(term: str, rows: list[Grade]) -> str:
    text = f"{term}\n\n{_render_body(rows)}"
    if len(text) > _TEXT_LIMIT:
        text = text[:_TEXT_LIMIT - len(_TRUNCATE_MARK)] + _TRUNCATE_MARK
    return text


def semester_keyboard(matriculation: str, terms: list[str]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=term,
                             callback_data=f"{GRADES_CALLBACK}:{matriculation}:{i}")
        for i, term in enumerate(terms)
    ]
    rows = [buttons[i:i + _TERM_BUTTONS_PER_ROW]
            for i in range(0, len(buttons), _TERM_BUTTONS_PER_ROW)]
    rows.append([InlineKeyboardButton(
        text="⬅️ Back", callback_data=f"{GRADES_BACK_CALLBACK}:{matriculation}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


router = Router(name="directory.grades")


@router.callback_query(F.data.startswith(f"{GRADES_CALLBACK}:"))
async def cb_grades(cb: CallbackQuery, principal: User, session):
    if principal is None or not is_staff(principal):
        await cb.answer("Staff only.", show_alert=True)
        return
    _, _, matriculation, index_s = cb.data.split(":")
    target = identity.find_by_matriculation(session, matriculation)
    if target is None:
        await cb.answer("Not found.", show_alert=True)
        return
    groups = group_by_term(load_grades(session, target.id))
    terms = list(groups)
    try:
        term = terms[int(index_s)]
    except (ValueError, IndexError):
        await cb.answer(EXPIRED, show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    await cb.message.edit_text(
        render_screen(term, groups[term]),
        reply_markup=semester_keyboard(matriculation, terms),
    )
    await cb.answer()


@router.callback_query(F.data.startswith(f"{GRADES_BACK_CALLBACK}:"))
async def cb_grades_back(cb: CallbackQuery, principal: User, session):
    if principal is None or not is_staff(principal):
        await cb.answer("Staff only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    target = identity.find_by_matriculation(session, matriculation)
    if target is None:
        await cb.answer("Not found.", show_alert=True)
        return
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    text = render_profile(principal, target)
    show = has_grades(session, target.id)
    await cb.message.edit_text(
        text,
        reply_markup=profile_keyboard(principal, target, show_grades=show),
        entities=profile_entities(principal, target, text),
    )
    await cb.answer()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_grades_screen.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/grades.py tests/test_grades_screen.py
git commit -m "feat: staff-only grades screen (grouping, rendering, router)"
```

---

## Task 8: Wire `cmd_sync`'s grades pass and adopt `profile_keyboard`/`profile_entities`

**Files:**
- Modify: `src/jbcub_bot/features/directory/handlers.py`
- Test: `tests/test_directory_sync.py`

**Interfaces:**
- Consumes: `grades.sync_cohort`, `grades.has_grades`; `render.profile_keyboard`, `render.profile_entities`; `matching.fold`.

This task has two independent halves that happen to live in the same file: (a) `cmd_sync` reads each cohort's Gradebook tab after that cohort's roster commits, and reports failures per-cohort; (b) `cmd_me`, `name_search`, and `cb_admin_back` switch from `admin_keyboard`/hand-built markup to `profile_keyboard` + `profile_entities`.

For (a), the write phase changes from one trailing `session.commit()` to a **per-cohort** commit: the roster upsert/mark_departed/reconcile for a cohort commits before that cohort's grades pass runs, so a grades failure's `session.rollback()` cannot undo the roster write that already landed. The Rights pass is unaffected (still its own commit at the end, since Rights rows have no Gradebook).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_directory_sync.py`. These need a `Gradebook` tab read alongside the existing `Cohorts`/cohort/`Rights` ones — the helper `_cohorts_row`/`_cohort_row` fixtures already exist above; add gradebook rows inline per test.

```python
from jbcub_bot.core.models import Grade

GRADEBOOK_TERM_ROW = ["", "", "Fall 2024"]
GRADEBOOK_CATEGORY_ROW = ["", "", "Mandatory"]
GRADEBOOK_LABEL_ROW = ["Last name", "First name", "Math"]


def _gradebook_rows(*data_rows):
    return [GRADEBOOK_TERM_ROW, GRADEBOOK_CATEGORY_ROW, GRADEBOOK_LABEL_ROW,
            *data_rows]


async def test_sync_reports_a_broken_gradebook_but_the_roster_still_commits(
        session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            raise ConnectionResetError("boom")
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["30000001", "Ivanov", "Ivan", "Admin", "ivan"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())

    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)

    u = session.query(User).filter_by(matriculation="30000001").one()
    assert u.primary_cohort == "2024"  # roster write survived the grades failure
    said = [c.args[0] for c in msg.answer.await_args_list]
    assert any(m.startswith("Grades for 2024 skipped:") for m in said)
    assert "Sync done." in said[-1]


async def test_sync_continues_to_the_next_cohort_after_a_gradebook_failure(
        session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA"),
                    _cohorts_row("2025", "BBB")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return [["no", "header", "here"]]  # gradebook.MappingError
        if sheet_id == "BBB" and range_ == "A:Z":
            return [COHORT_HEADER, _cohort_row("30000002", "Petrov", "Petr", "petr")]
        if sheet_id == "BBB" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Petrov", "Petr", "91%"])
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["30000001", "Ivanov", "Ivan", "Admin", "ivan"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())

    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)

    petrov = session.query(User).filter_by(matriculation="30000002").one()
    assert session.query(Grade).filter_by(user_id=petrov.id).count() == 1
    said = [c.args[0] for c in msg.answer.await_args_list]
    assert any(m.startswith("Grades for 2024 skipped:") for m in said)
    assert any("1 rows matched" in m for m in said)


async def test_sync_stores_source_link_per_cohort_and_for_rights(session, monkeypatch):
    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [COHORT_HEADER, _cohort_row("30000001", "Ivanov", "Ivan", "ivan")]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(["Ivanov", "Ivan", "91%"])
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER, ["", "Sidorov", "Sergey", "Admin", "sidorov"]]
        return []
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.fetch_rows", fake_fetch)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.get_settings", _settings)
    monkeypatch.setattr("jbcub_bot.features.directory.handlers.build_credentials",
                        lambda *a: None)
    msg = SimpleNamespace(answer=AsyncMock())

    await cmd_sync(msg, principal=User(last_name="A", role=Role.ADMIN), session=session)

    ivanov = session.query(User).filter_by(matriculation="30000001").one()
    assert ivanov.source_link == "AAA"
    sidorov = session.query(User).filter_by(last_name="Sidorov").one()
    assert sidorov.source_link == "RIGHTS"  # _settings().rights_sheet_id
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_directory_sync.py -v`
Expected: FAIL — the three new tests fail (no Gradebook read happens yet, no `source_link` written, no `Grade` rows created); all pre-existing tests in this file still pass at this point.

- [ ] **Step 3: Implement**

In `src/jbcub_bot/features/directory/handlers.py`, add imports:

```python
import logging

from jbcub_bot.features.directory import grades, matching
from jbcub_bot.features.directory.render import (
    ADMIN_BACK_CALLBACK,
    ADMIN_CALLBACK,
    admin_actions_keyboard,
    admin_keyboard,
    admin_row,
    invite_row,
    me_keyboard,
    profile_entities,
    profile_keyboard,
    render_cohort_list,
    render_profile,
)
```

(`from jbcub_bot.features.directory import matching` already exists — just add `grades` alongside it, and add `profile_entities`/`profile_keyboard` to the `render` import list.)

Add a module logger near the top, alongside `SHEET_READ_TIMEOUT`:

```python
_log = logging.getLogger(__name__)
```

Update `cmd_me`:

```python
@cmd.command("me", "Show your own profile.")
async def cmd_me(message: Message, principal: User, session, impersonator=None):
    text = render_profile(principal, principal)
    await message.answer(
        text,
        reply_markup=me_keyboard(principal, interactive=impersonator is None),
        entities=profile_entities(principal, principal, text),
    )
```

Update `name_search`'s matched-profile branch:

```python
    if best - runner_up >= matching.LEAD:
        show = grades.has_grades(session, target.id) if target.id is not None else False
        text = render_profile(principal, target)
        await message.answer(
            text,
            reply_markup=profile_keyboard(principal, target, show_grades=show),
            entities=profile_entities(principal, target, text),
        )
        return True
```

Update `cb_admin_back`'s non-self branch:

```python
@router.callback_query(F.data.startswith(f"{ADMIN_BACK_CALLBACK}:"))
async def cb_admin_back(cb: CallbackQuery, principal: User, session):
    if principal is None or principal.role is not Role.ADMIN:
        await cb.answer("Admins only.", show_alert=True)
        return
    matriculation = cb.data.split(":", 2)[2]
    if principal.matriculation and principal.matriculation == matriculation:
        markup = me_keyboard(principal)
    else:
        target = identity.find_by_matriculation(session, matriculation)
        show = target is not None and grades.has_grades(session, target.id)
        markup = profile_keyboard(
            principal, target or User(matriculation=matriculation), show_grades=show)
    await cb.message.edit_reply_markup(reply_markup=markup)
    await cb.answer()
```

Restructure `cmd_sync`'s parse phase to carry `link`/`mapping` forward, and its write phase to commit per cohort and run the grades pass. Replace:

```python
        for record in records:
            record["primary_cohort"] = entry["cohort"]
        parsed_cohorts.append((entry["cohort"], records))
```

with:

```python
        for record in records:
            record["primary_cohort"] = entry["cohort"]
            record["source_link"] = entry["link"]
        parsed_cohorts.append((entry["cohort"], records, entry["link"], entry["mapping"]))
```

The Rights tab is already read earlier, in the parse phase (the block starting `await message.answer("Reading Rights…")` and ending `await message.answer(f"Rights: {len(rights_records)} rows read.")`) — that block is untouched. Only the **write phase** after it changes. Replace this exact block (from the `# --- Write phase` comment down to the final `await message.answer("Sync done.")`):

```python
    # --- Write phase: everything parsed OK, now upsert + reconcile. ---
    await message.answer("All sheets read. Writing to database…")
    try:
        today = date.today().isoformat()
        for cohort_name, records in parsed_cohorts:
            sheets.upsert_users(session, records)
            # After the upsert, so anyone the roster names again is already back
            # before the ones it dropped get marked.
            departed = sheets.mark_departed(session, cohort_name, records, today)
            rep = sheets.reconcile(session, records)
            await message.answer(
                f"{cohort_name}: {len(records)} rows, "
                f"{departed} marked departed, drift={rep.drift or '-'}, "
                f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")
        # Rights rows have no matriculation — key on the Telegram handle so
        # admins/teachers get matched (or created) as searchable rows.
        sheets.upsert_users(session, rights_records, key="handle_sheet")
        rep = sheets.reconcile(session, rights_records, key="handle_sheet")
        await message.answer(
            f"rights: {len(rights_records)} rows, drift={rep.drift or '-'}, "
            f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")
        session.commit()
    except Exception as exc:
        session.rollback()  # the roster keeps its last good state
        raise RuntimeError("/sync failed in the write phase") from exc
    await message.answer("Sync done.")
```

with:

```python
    # --- Write phase: everything parsed OK, now upsert + reconcile. ---
    await message.answer("All sheets read. Writing to database…")
    today = date.today().isoformat()
    for cohort_name, records, link, mapping in parsed_cohorts:
        try:
            sheets.upsert_users(session, records)
            # After the upsert, so anyone the roster names again is already
            # back before the ones it dropped get marked.
            departed = sheets.mark_departed(session, cohort_name, records, today)
            rep = sheets.reconcile(session, records)
            session.commit()
        except Exception as exc:
            session.rollback()
            raise RuntimeError(f"/sync failed writing cohort {cohort_name}") from exc
        await message.answer(
            f"{cohort_name}: {len(records)} rows, "
            f"{departed} marked departed, drift={rep.drift or '-'}, "
            f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")

        # Grades pass: after the roster commit, so a bad header here can
        # never roll back the roster write, and a failure never delays a
        # departure taking effect. Deliberate exception to the rule against
        # swallowing unexpected exceptions in a handler -- see AGENTS.md.
        try:
            sheet_id = sheets.extract_sheet_id(link)
            gb_rows = await read_rows(
                sheet_id, sa, f"{settings.gradebook_tab}!A:ZZ")
            report = grades.sync_cohort(
                session, cohort_name, gb_rows, mapping, matching.fold)
            session.commit()
            await message.answer(
                f"{report.matched} rows matched, {report.cells} cells, "
                f"unmatched={report.unmatched or '-'}, "
                f"dup={report.duplicates or '-'}, "
                f"{report.ignored_columns} columns outside a semester band ignored")
        except Exception as exc:
            session.rollback()
            _log.exception("Grades sync failed for cohort %s", cohort_name)
            await message.answer(f"Grades for {cohort_name} skipped: {exc}")

    # Rights rows have no matriculation — key on the Telegram handle so
    # admins/teachers get matched (or created) as searchable rows.
    try:
        for record in rights_records:
            record["source_link"] = settings.rights_sheet_id
        sheets.upsert_users(session, rights_records, key="handle_sheet")
        rep = sheets.reconcile(session, rights_records, key="handle_sheet")
        session.commit()
    except Exception as exc:
        session.rollback()
        raise RuntimeError("/sync failed in the write phase") from exc
    await message.answer(
        f"rights: {len(rights_records)} rows, drift={rep.drift or '-'}, "
        f"unmatched={rep.unmatched or '-'}, dup={rep.duplicates or '-'}")
    await message.answer("Sync done.")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_directory_sync.py tests/test_directory_handlers.py tests/test_search_integration.py tests/test_directory_admin.py -v`
Expected: PASS — the three new tests, and every pre-existing test in these files (the restructuring must not change behavior for the happy path or any existing failure-mode test).

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/handlers.py tests/test_directory_sync.py
git commit -m "feat: sync a cohort's grades after its roster commits; adopt profile_keyboard"
```

---

## Task 9: `privacy.py` entities pass-through, and registering `grades.router`

**Files:**
- Modify: `src/jbcub_bot/features/directory/privacy.py`, `src/jbcub_bot/features/directory/__init__.py`
- Test: none new — covered by existing `tests/test_privacy.py`/`tests/test_privacy_handlers.py` continuing to pass, plus a manual check below.

**Interfaces:**
- Consumes: `render.profile_entities`.

`privacy.py`'s `cb_back` re-renders the caller's own profile (`render_profile(principal, principal)`) — it needs `entities` too, for the same reason `cmd_me` does (an admin's own `/me` also carries the cohort/source hyperlink). This is a narrow, mechanical change with no new branch to test — the existing `render_profile`/`profile_entities` tests already cover the underlying logic; here we're just confirming the call site passes it through.

- [ ] **Step 1: Update `privacy.py`**

In `src/jbcub_bot/features/directory/privacy.py`, update the import and `cb_back`:

```python
from jbcub_bot.features.directory.render import (
    PRIVACY_CALLBACK,
    PROFILE_CALLBACK,
    me_keyboard,
    profile_entities,
    render_profile,
)
```

```python
@router.callback_query(F.data == PROFILE_CALLBACK)
@require_linked
async def cb_back(cb: CallbackQuery, principal: User, session):
    if not isinstance(cb.message, Message):
        await cb.answer(EXPIRED, show_alert=True)
        return
    text = render_profile(principal, principal)
    await cb.message.edit_text(
        text, reply_markup=me_keyboard(principal),
        entities=profile_entities(principal, principal, text))
    await cb.answer()
```

- [ ] **Step 2: Register `grades.router` in the feature's `__init__.py`**

In `src/jbcub_bot/features/directory/__init__.py`:

```python
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role
from jbcub_bot.features.directory import edit, grades, privacy
from jbcub_bot.features.directory.handlers import cmd, name_search_intent, router

router.include_router(privacy.router)
router.include_router(edit.router)
router.include_router(grades.router)

manifest = Manifest(
    name="directory",
    commands=cmd.specs + privacy.cmd.specs + edit.cmd.specs,
    intents=[name_search_intent],
    min_role=Role.STUDENT,
    help_text="Find classmates and manage your own profile.",
)

__all__ = ["router", "manifest"]
```

- [ ] **Step 3: Run the full existing directory test suite**

Run: `uv run pytest tests/ -k "directory or privacy" -v`
Expected: PASS — no behavior changed for privacy/edit, and the feature module still loads (the `_reset_feature_routers` fixture in `conftest.py` detaches routers between tests, so `grades.router` being newly attached here must not break `build_dispatcher()` being called more than once across the suite).

- [ ] **Step 4: Commit**

```bash
git add src/jbcub_bot/features/directory/privacy.py src/jbcub_bot/features/directory/__init__.py
git commit -m "feat: privacy screen carries profile entities; register the grades router"
```

---

## Task 10: `AGENTS.md`, `.env.example` sanity check, and full-suite verification

**Files:**
- Modify: `docs/superpowers/specs/2026-07-29-gradebook-grades-design.md` is the spec (no change needed — already approved); modify `AGENTS.md`.

- [ ] **Step 1: Document the new conventions in `AGENTS.md`**

Add a bullet to the "Conventions that aren't obvious" list in `AGENTS.md`, after the name-matching bullet:

```markdown
- **Gradebook grades live beside the roster, not inside it.** `core/gradebook.py`
  parses a cohort's `Gradebook` tab (pure, no aiogram/sqlalchemy, like
  `matching.py`); `features/directory/grades.py` resolves each row to a `User`
  by folded name scoped to `primary_cohort` (the same scoping `mark_departed`
  uses) and replaces that cohort's `grades` rows wholesale
  (`DELETE WHERE cohort = ?`, then insert). `/sync` runs this pass **after**
  a cohort's roster has committed, one cohort at a time, and wraps it in
  `try/except Exception` deliberately: the roster governs access
  (`departed_at` closes it), so a typo in a grades header must never delay a
  departure taking effect or roll back a roster write that already landed.
```

- [ ] **Step 2: Run the entire test suite**

Run: `uv run pytest -v`
Expected: PASS, all tests (new and pre-existing).

- [ ] **Step 3: Manually verify `/sync`'s new settings surface**

Confirm `.env.example` and `Settings` agree: `grep GRADEBOOK_TAB .env.example` should show `GRADEBOOK_TAB=Gradebook`, matching `Settings.gradebook_tab`'s default.

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs: record the gradebook grades conventions in AGENTS.md"
```
