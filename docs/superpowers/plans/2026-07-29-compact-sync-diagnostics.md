# Compact `/sync` Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `/sync`'s phase-by-phase message flood with one actionable, grouped report per cohort and one explicit final summary.

**Architecture:** Sheet and Gradebook services return structured facts rather than display strings. A new pure `sync_diagnostics.py` module turns those facts into English issue groups, bounded Telegram text or a complete text attachment, and the final all-cohort summary; `handlers.cmd_sync` only orchestrates reads, transactions, sends, and source-link keyboards.

**Tech Stack:** Python 3.12, aiogram 3, SQLAlchemy 2.0, pytest + pytest-asyncio, uv.

## Global Constraints

- Execute from a branch containing both `0a470dc` (Gradebook implementation) and `54bf3f1` (approved diagnostics spec and English-interface rule). Do not run this plan against either commit alone.
- All user-facing bot text is English, including progress, diagnostics, errors, captions, and buttons.
- A healthy three-cohort run sends five bot messages: one start message, one result per cohort, and one final summary. Editing the start message does not add a message.
- Each cohort produces exactly one result message after its roster and Gradebook work finish.
- A problem group explains impact and repair once, then lists every affected item.
- User-facing copy never uses the ambiguous labels `unmatched`, `dup`, or `drift`.
- A normal cohort report stays below 3900 characters; a longer report becomes one UTF-8 `.txt` document message containing every item.
- Every actionable source has one Google Sheets inline button. The bot never writes to Sheets.
- Gradebook failures keep the previous committed grades, do not roll back roster access changes, and do not stop later cohorts.
- Expected Gradebook metadata columns outside semester bands are not diagnostics.
- Unexpected exceptions still propagate to the central error reporter.

---

## File Structure

- Modify `src/jbcub_bot/core/sheets.py`: structured roster/Rights differences, duplicate keys, and newly departed people.
- Modify `src/jbcub_bot/core/gradebook.py`: retain suspicious unbanded column identities instead of a noisy count.
- Modify `src/jbcub_bot/features/directory/grades.py`: distinguish every Gradebook matching direction and aggregate duplicate names once.
- Create `src/jbcub_bot/features/directory/sync_diagnostics.py`: issue-group construction and all sync rendering/overflow policy.
- Modify `src/jbcub_bot/features/directory/handlers.py`: orchestration and Telegram transport only.
- Modify `tests/test_sheets_upsert.py`, `tests/test_gradebook_parse.py`, `tests/test_gradebook_store.py`, and `tests/test_directory_sync.py`.
- Create `tests/test_sync_diagnostics.py`.

---

### Task 1: Structured roster and Rights reconciliation facts

**Files:**
- Modify: `src/jbcub_bot/core/sheets.py`
- Modify: `tests/test_sheets_upsert.py`
- Modify: `tests/test_directory_sync.py`

**Interfaces:**
- Produces: `DuplicateKey(value: str, rows: int)`.
- Produces: `FieldDifference(key: str, field: str, sheet_value: str, profile_value: str)`.
- Produces: `DepartedUser(matriculation: str, full_name: str)`.
- Produces: `ReconcileReport(differences: list[FieldDifference], duplicates: list[DuplicateKey])`.
- Changes: `mark_departed(...) -> list[DepartedUser]`; callers use `len(result)` for the count.

- [ ] **Step 1: Replace the reconciliation regression tests with structured expectations**

In `tests/test_sheets_upsert.py`, replace the three `test_reconcile_*` tests with:

```python
def test_reconcile_reports_duplicate_keys_once_with_row_count(session):
    session.add(User(matriculation="2", last_name="Petrov"))
    session.commit()
    records = [
        {"matriculation": "2", "handle_sheet": "petr_a"},
        {"matriculation": "2", "handle_sheet": "petr_b"},
    ]

    report = sheets.reconcile(session, records)

    assert report.duplicates == [sheets.DuplicateKey(value="2", rows=2)]
    assert report.differences == []


def test_reconcile_reports_both_values_for_a_profile_difference(session):
    session.add(User(
        matriculation="1",
        last_name="Ivan",
        handle_observed="ivan_new",
        github_self="alice-dev",
    ))
    session.commit()
    records = [{
        "matriculation": "1",
        "handle_sheet": "ivan_old",
        "github_sheet": "alice",
    }]

    report = sheets.reconcile(session, records)

    assert report.differences == [
        sheets.FieldDifference(
            key="1",
            field="telegram",
            sheet_value="ivan_old",
            profile_value="ivan_new",
        ),
        sheets.FieldDifference(
            key="1",
            field="github",
            sheet_value="alice",
            profile_value="alice-dev",
        ),
    ]


def test_reconcile_ignores_a_field_only_one_side_filled(session):
    session.add(User(matriculation="1", last_name="Ivan", github_self="alice"))
    session.commit()
    records = [{"matriculation": "1", "github_sheet": ""}]

    report = sheets.reconcile(session, records)

    assert report.differences == []
    assert report.duplicates == []
```

- [ ] **Step 2: Change departed tests to expect identities, not integers**

In the test that newly marks matriculation `2`, assert:

```python
marked = sheets.mark_departed(
    session,
    "2024",
    [{"matriculation": "1"}],
    "2026-07-28",
)

assert marked == [
    sheets.DepartedUser(matriculation="2", full_name="Eve Expelled")
]
```

Change every unaffected/repeated-sync assertion from `assert marked == 0` to:

```python
assert marked == []
```

In `tests/test_directory_sync.py`, change count assertions to use the rendered
result later; for now change the handler's temporary count use to
`len(departed)` so this task remains green before Task 5.

- [ ] **Step 3: Run the tests to verify they fail**

Run:

```bash
uv run pytest tests/test_sheets_upsert.py tests/test_directory_sync.py -q
```

Expected: FAIL because the dataclasses do not exist, `reconcile()` still returns
string lists, and `mark_departed()` returns an integer.

- [ ] **Step 4: Add the structured dataclasses and return values**

In `src/jbcub_bot/core/sheets.py`, replace `ReconcileReport` with:

```python
@dataclass(frozen=True)
class DuplicateKey:
    value: str
    rows: int


@dataclass(frozen=True)
class FieldDifference:
    key: str
    field: str
    sheet_value: str
    profile_value: str


@dataclass(frozen=True)
class DepartedUser:
    matriculation: str
    full_name: str


@dataclass
class ReconcileReport:
    differences: list[FieldDifference] = field(default_factory=list)
    duplicates: list[DuplicateKey] = field(default_factory=list)
```

Change `mark_departed()` to collect the identity before returning:

```python
marked: list[DepartedUser] = []
for user in session.scalars(stmt).all():
    key_value = getattr(user, key)
    if key_value and key_value not in present:
        user.departed_at = today
        marked.append(DepartedUser(
            matriculation=str(key_value),
            full_name=user.full_name,
        ))
return marked
```

Replace `reconcile()` with:

```python
def reconcile(
    session,
    records: list[dict],
    key: str = "matriculation",
) -> ReconcileReport:
    report = ReconcileReport()
    keys = [str(record.get(key)) for record in records if record.get(key)]
    counts = Counter(keys)
    report.duplicates = [
        DuplicateKey(value=value, rows=count)
        for value, count in counts.items()
        if count > 1
    ]
    duplicate_values = {item.value for item in report.duplicates}

    for record in records:
        raw_key = record.get(key)
        if not raw_key or str(raw_key) in duplicate_values:
            continue
        user = session.scalar(
            select(User).where(getattr(User, key) == raw_key)
        )
        if user is None:
            continue
        for sheet_key, own_column, label in DRIFT_PAIRS:
            sheet_value = record.get(sheet_key)
            profile_value = getattr(user, own_column)
            if (
                sheet_value
                and profile_value
                and sheet_value != profile_value
            ):
                report.differences.append(FieldDifference(
                    key=str(raw_key),
                    field=label,
                    sheet_value=str(sheet_value),
                    profile_value=str(profile_value),
                ))
    return report
```

Update the `mark_departed()` return annotation and docstring to
`list[DepartedUser]`. Remove `unmatched` from `ReconcileReport`; post-upsert
roster/Rights reconciliation cannot use it meaningfully.

- [ ] **Step 5: Run the focused tests**

Run:

```bash
uv run pytest tests/test_sheets_upsert.py tests/test_directory_sync.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/core/sheets.py tests/test_sheets_upsert.py tests/test_directory_sync.py
git commit -m "refactor: structure roster sync diagnostics"
```

---

### Task 2: Keep only actionable unbanded Gradebook columns

**Files:**
- Modify: `src/jbcub_bot/core/gradebook.py`
- Modify: `tests/test_gradebook_parse.py`

**Interfaces:**
- Produces: `IgnoredColumn(index: int, label: str)`.
- Produces: `sheet_column_name(index: int) -> str`, where `0 -> "A"` and `26 -> "AA"`.
- Changes: `ParsedGradebook.ignored_columns` from `int` to `list[IgnoredColumn]`.

- [ ] **Step 1: Replace the noisy ignored-count test**

In `tests/test_gradebook_parse.py`, replace
`test_columns_resolve_bands_categories_labels_and_ignored_count` with:

```python
def test_columns_report_only_named_non_metadata_columns_without_a_term():
    rows = [
        ["", "", "", "", "Fall 2025"],
        ["", "", "", "", "Mandatory"],
        [
            "Status",
            "Last name",
            "First name",
            "Credits Failed after make-up",
            "Math",
        ],
        ["Active", "Ivanov", "Ivan", "3", "91%"],
    ]

    parsed = parse_gradebook(rows, "Last name", "First name")

    assert parsed.ignored_columns == [
        IgnoredColumn(index=3, label="Credits Failed after make-up")
    ]
    assert parsed.columns == [
        Column(
            index=4,
            term="Fall 2025",
            category="Mandatory",
            label="Math",
        )
    ]
```

Add imports and column-name coverage:

```python
from jbcub_bot.core.gradebook import (
    Column,
    GradebookRow,
    IgnoredColumn,
    MappingError,
    parse_gradebook,
    sheet_column_name,
)


def test_sheet_column_name_uses_spreadsheet_letters():
    assert sheet_column_name(0) == "A"
    assert sheet_column_name(25) == "Z"
    assert sheet_column_name(26) == "AA"
    assert sheet_column_name(51) == "AZ"
```

In the existing fixture-based test, replace `assert parsed.ignored_columns == 3`
with:

```python
assert parsed.ignored_columns == []
```

- [ ] **Step 2: Run the parser tests to verify they fail**

Run:

```bash
uv run pytest tests/test_gradebook_parse.py -q
```

Expected: FAIL because `IgnoredColumn` and `sheet_column_name` do not exist and
`ignored_columns` is still an integer.

- [ ] **Step 3: Return column identities and filter expected metadata**

In `src/jbcub_bot/core/gradebook.py`, add:

```python
_EXPECTED_METADATA_LABELS = frozenset({"Status", "Location /Arr.Date"})


@dataclass(frozen=True)
class IgnoredColumn:
    index: int
    label: str


def sheet_column_name(index: int) -> str:
    value = index + 1
    letters = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters
```

Change `_parse_columns()` to accept `metadata_labels: frozenset[str]` and
replace the `ignored` integer with `ignored: list[IgnoredColumn]`. In its
`if not term` branch use:

```python
if not term:
    if label_cell and label_cell not in metadata_labels:
        ignored.append(IgnoredColumn(index=index, label=label_cell))
    continue
```

In `parse_gradebook()`, build and pass the complete metadata set:

```python
metadata_labels = frozenset({
    *_EXPECTED_METADATA_LABELS,
    last_name_column,
    first_name_column,
})
columns, ignored = _parse_columns(rows, header_row, metadata_labels)
```

Change `ParsedGradebook.ignored_columns` to `list[IgnoredColumn]`.

- [ ] **Step 4: Run the parser tests**

Run:

```bash
uv run pytest tests/test_gradebook_parse.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/gradebook.py tests/test_gradebook_parse.py
git commit -m "feat: identify actionable Gradebook columns"
```

---

### Task 3: Distinguish every Gradebook matching direction

**Files:**
- Modify: `src/jbcub_bot/features/directory/grades.py`
- Modify: `tests/test_gradebook_store.py`

**Interfaces:**
- Produces: `CountedName(name: str, count: int)`.
- Produces: `GradesSyncReport(source_people, matched_people, cells, no_roster_match, ambiguous_roster_match, missing_gradebook_rows, duplicate_rows, ignored_columns)`.
- Keeps: exact folded matching scoped to `primary_cohort`; no fuzzy assignment.

- [ ] **Step 1: Replace report tests with directional assertions**

In `tests/test_gradebook_store.py`, retain the storage assertions and add these
tests:

```python
def test_unknown_gradebook_name_is_only_in_no_roster_match(session):
    session.add(User(
        last_name="Sidorov",
        first_name="Sergey",
        primary_cohort="2099",
    ))
    session.commit()

    report = sync_cohort(
        session,
        "2024",
        _rows(["Active", "Sidorov", "Sergey", "91%", "", "pass"]),
        MAPPING,
        matching.fold,
    )

    assert report.source_people == 1
    assert report.matched_people == 0
    assert report.no_roster_match == ["Sidorov Sergey"]
    assert report.ambiguous_roster_match == []
    assert session.query(Grade).count() == 0


def test_duplicate_gradebook_name_is_grouped_once(session):
    session.add(User(
        last_name="Kuznetsov",
        first_name="Ivan",
        primary_cohort="2024",
    ))
    session.commit()

    report = sync_cohort(
        session,
        "2024",
        _rows(
            ["Active", "Kuznetsov", "Ivan", "91%", "", "pass"],
            ["Active", "Kuznetsov", "Ivan", "50%", "", "fail"],
        ),
        MAPPING,
        matching.fold,
    )

    assert report.duplicate_rows == [
        CountedName(name="Kuznetsov Ivan", count=2)
    ]
    assert report.no_roster_match == []
    assert session.query(Grade).count() == 0


def test_ambiguous_roster_name_is_not_called_missing(session):
    session.add_all([
        User(last_name="Lee", first_name="Alex", primary_cohort="2024"),
        User(last_name="Lee", first_name="Alex", primary_cohort="2024"),
    ])
    session.commit()

    report = sync_cohort(
        session,
        "2024",
        _rows(["Active", "Lee", "Alex", "91%", "", "pass"]),
        MAPPING,
        matching.fold,
    )

    assert report.ambiguous_roster_match == [
        CountedName(name="Lee Alex", count=2)
    ]
    assert report.no_roster_match == []


def test_only_current_roster_students_without_source_rows_are_reported(session):
    session.add_all([
        User(last_name="Current", first_name="Student", primary_cohort="2024"),
        User(
            last_name="Former",
            first_name="Student",
            primary_cohort="2024",
            departed_at="2026-07-01",
        ),
        User(last_name="Other", first_name="Cohort", primary_cohort="2025"),
    ])
    session.commit()

    report = sync_cohort(
        session,
        "2024",
        _rows(["Active", "Known", "Person", "91%", "", "pass"]),
        MAPPING,
        matching.fold,
    )

    assert report.missing_gradebook_rows == ["Current Student"]
```

Update the matched-row assertion to:

```python
assert report.source_people == 1
assert report.matched_people == 1
assert report.cells == 2
assert report.no_roster_match == []
```

Update ignored-column assertions to compare the list from Task 2.

- [ ] **Step 2: Run the store tests to verify they fail**

Run:

```bash
uv run pytest tests/test_gradebook_store.py -q
```

Expected: FAIL because the new report fields and `CountedName` do not exist.

- [ ] **Step 3: Define the directional report**

In `src/jbcub_bot/features/directory/grades.py`, replace `GradesSyncReport` with:

```python
@dataclass(frozen=True)
class CountedName:
    name: str
    count: int


@dataclass
class GradesSyncReport:
    source_people: int = 0
    matched_people: int = 0
    cells: int = 0
    no_roster_match: list[str] = field(default_factory=list)
    ambiguous_roster_match: list[CountedName] = field(default_factory=list)
    missing_gradebook_rows: list[str] = field(default_factory=list)
    duplicate_rows: list[CountedName] = field(default_factory=list)
    ignored_columns: list[gradebook.IgnoredColumn] = field(default_factory=list)
```

Refactor `sync_cohort()` around one `Counter` and one display-name dictionary:

```python
parsed = gradebook.parse_gradebook(
    rows,
    mapping["last_name"],
    mapping["first_name"],
)
report = GradesSyncReport(
    source_people=len(parsed.rows),
    ignored_columns=parsed.ignored_columns,
)

names = [(fold(row.last_name), fold(row.first_name)) for row in parsed.rows]
counts = Counter(names)
display_names = {
    key: f"{row.last_name} {row.first_name}".strip()
    for row, key in zip(parsed.rows, names)
}
report.duplicate_rows = [
    CountedName(name=display_names[key], count=count)
    for key, count in counts.items()
    if count > 1
]

candidates = session.scalars(
    select(User).where(User.primary_cohort == cohort)
).all()
by_name: dict[tuple[str, str], list[User]] = {}
for user in candidates:
    key = (fold(user.last_name), fold(user.first_name))
    by_name.setdefault(key, []).append(user)

source_keys = set(names)
report.missing_gradebook_rows = sorted(
    user.full_name
    for user in candidates
    if user.departed_at is None
    and user.last_name
    and user.first_name
    and (fold(user.last_name), fold(user.first_name)) not in source_keys
)
```

In the row loop:

```python
if counts[key] > 1:
    continue
matches = by_name.get(key, [])
name = display_names[key]
if not matches:
    report.no_roster_match.append(name)
    continue
if len(matches) > 1:
    report.ambiguous_roster_match.append(
        CountedName(name=name, count=len(matches))
    )
    continue
user = matches[0]
report.matched_people += 1
```

Keep the cohort-scoped delete and cell inserts unchanged. Sort all plain-name
lists and counted-name lists by `.name` before returning so reports are stable.

- [ ] **Step 4: Run parser and store tests**

Run:

```bash
uv run pytest tests/test_gradebook_parse.py tests/test_gradebook_store.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/directory/grades.py tests/test_gradebook_store.py
git commit -m "feat: explain Gradebook matching gaps"
```

---

### Task 4: Pure grouped renderer with a one-message overflow fallback

**Files:**
- Create: `src/jbcub_bot/features/directory/sync_diagnostics.py`
- Create: `tests/test_sync_diagnostics.py`

**Interfaces:**
- Produces: `IssueGroup(title, effect, action, items)`.
- Produces: `CohortOutcome(cohort, roster_students, ignored_roster_rows, gradebook, gradebook_error, issues, source_url)`.
- Produces: `RightsOutcome(staff_records, issues, source_url)`.
- Produces: `RenderedReport(text, caption, document_name, document_bytes)`.
- Produces: `build_issue_groups(roster, grades_report, departed)`.
- Produces: `build_rights_issue_groups(report)`.
- Produces: `render_cohort(outcome)` and
  `render_final(cohorts, rights, completion_note=None)`.
- Produces: `counted(count, singular, plural=None)` for correct English counts.
- `sync_diagnostics.py` imports no aiogram objects; Telegram transport stays in `handlers.py`.

- [ ] **Step 1: Write renderer tests**

Create `tests/test_sync_diagnostics.py` with:

```python
from jbcub_bot.core import gradebook, sheets
from jbcub_bot.features.directory import grades
from jbcub_bot.features.directory.sync_diagnostics import (
    CohortOutcome,
    IssueGroup,
    RightsOutcome,
    build_issue_groups,
    build_rights_issue_groups,
    counted,
    render_cohort,
    render_final,
)


def _grades_report() -> grades.GradesSyncReport:
    return grades.GradesSyncReport(
        source_people=5,
        matched_people=2,
        cells=17,
        no_roster_match=["Aliev Rufat", "Rosa Maria"],
        ambiguous_roster_match=[
            grades.CountedName(name="Lee Alex", count=2)
        ],
        missing_gradebook_rows=["Ivan Petrov"],
        duplicate_rows=[
            grades.CountedName(name="John Smith", count=3)
        ],
        ignored_columns=[
            gradebook.IgnoredColumn(
                index=51,
                label="Credits Failed after make-up",
            )
        ],
    )


def test_problem_copy_is_grouped_and_directional():
    groups = build_issue_groups(
        roster=sheets.ReconcileReport(
            differences=[
                sheets.FieldDifference(
                    key="30000001",
                    field="telegram",
                    sheet_value="ivan_old",
                    profile_value="ivan_new",
                )
            ],
            duplicates=[
                sheets.DuplicateKey(value="30000009", rows=2)
            ],
        ),
        grades_report=_grades_report(),
        departed=[
            sheets.DepartedUser(
                matriculation="30000010",
                full_name="Eve Expelled",
            )
        ],
    )

    outcome = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=33,
        ignored_roster_rows=16,
        gradebook=_grades_report(),
        gradebook_error=None,
        issues=groups,
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    rendered = render_cohort(outcome)
    assert rendered.document_bytes is None
    assert rendered.text is not None
    assert "Gradebook rows without a roster match (2)" in rendered.text
    assert "Aliev Rufat" in rendered.text
    assert "Roster students without a Gradebook row (1)" in rendered.text
    assert "Duplicate Gradebook rows (1)" in rendered.text
    assert "John Smith — 3 rows" in rendered.text
    assert "Ambiguous roster names (1)" in rendered.text
    assert "Lee Alex — 2 roster profiles" in rendered.text
    assert "Columns outside a semester (1)" in rendered.text
    assert "AZ — Credits Failed after make-up" in rendered.text
    assert "Profile values differing from the sheet (1)" in rendered.text
    assert "sheet @ivan_old; profile @ivan_new" in rendered.text
    assert "Newly marked as departed (1)" in rendered.text
    assert "16 historical rows below the roster separator were ignored" in rendered.text
    assert "unmatched=" not in rendered.text
    assert "dup=" not in rendered.text
    assert "drift=" not in rendered.text


def test_rights_duplicates_name_the_handle_source():
    groups = build_rights_issue_groups(sheets.ReconcileReport(
        duplicates=[sheets.DuplicateKey(value="boss", rows=2)]
    ))

    assert groups == (
        IssueGroup(
            title="Duplicate Rights handles",
            effect="These Rights rows resolve to the same profile.",
            action="Correct the duplicate Telegram handles on the Rights tab",
            items=("boss — 2 rows",),
        ),
    )


def test_counted_uses_correct_english_forms():
    assert counted(1, "roster student") == "1 roster student"
    assert counted(2, "roster student") == "2 roster students"
    assert counted(1, "person", "people") == "1 person"
    assert counted(2, "person", "people") == "2 people"


def test_long_report_becomes_one_complete_text_document():
    report = grades.GradesSyncReport(
        source_people=80,
        matched_people=0,
        no_roster_match=[
            f"Student {index:03d} " + ("X" * 80)
            for index in range(80)
        ],
    )
    outcome = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=33,
        ignored_roster_rows=0,
        gradebook=report,
        gradebook_error=None,
        issues=build_issue_groups(
            roster=sheets.ReconcileReport(),
            grades_report=report,
            departed=[],
        ),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )

    rendered = render_cohort(outcome)

    assert rendered.text is None
    assert rendered.document_name == "sync-sdt-2025-2028.txt"
    body = rendered.document_bytes.decode("utf-8")
    assert "Student 000" in body
    assert "Student 079" in body
    assert "and 1 more" not in body
    assert len(rendered.caption) < 1024


def test_final_summary_names_every_cohort_and_the_rights_count():
    healthy = CohortOutcome(
        cohort="sdt-2025-2028",
        roster_students=33,
        ignored_roster_rows=0,
        gradebook=grades.GradesSyncReport(
            source_people=49,
            matched_people=33,
            cells=1247,
        ),
        gradebook_error=None,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )
    kept = CohortOutcome(
        cohort="sdt-2024-2027",
        roster_students=36,
        ignored_roster_rows=0,
        gradebook=None,
        gradebook_error="Gradebook header row not found",
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/BBB",
    )
    rights = RightsOutcome(
        staff_records=6,
        issues=(),
        source_url="https://docs.google.com/spreadsheets/d/RIGHTS",
    )

    text = render_final([healthy, kept], rights)

    assert text.startswith("⚠️ Sync completed with warnings")
    assert "sdt-2025-2028 — 33 roster students; 33 Gradebook rows matched" in text
    assert (
        "sdt-2024-2027 — 36 roster students; "
        "grades not updated, previous data kept"
    ) in text
    assert "Rights: 6 staff records" in text
    assert "1 of 2" not in text
```

- [ ] **Step 2: Run the renderer tests to verify they fail**

Run:

```bash
uv run pytest tests/test_sync_diagnostics.py -q
```

Expected: FAIL because `sync_diagnostics.py` does not exist.

- [ ] **Step 3: Implement the report dataclasses and English group builders**

Create `src/jbcub_bot/features/directory/sync_diagnostics.py` with these public
types:

```python
from dataclasses import dataclass
import re

from jbcub_bot.core import gradebook, sheets
from jbcub_bot.features.directory import grades

MAX_REPORT_TEXT = 3900


@dataclass(frozen=True)
class IssueGroup:
    title: str
    effect: str
    action: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class CohortOutcome:
    cohort: str
    roster_students: int
    ignored_roster_rows: int
    gradebook: grades.GradesSyncReport | None
    gradebook_error: str | None
    issues: tuple[IssueGroup, ...]
    source_url: str


@dataclass(frozen=True)
class RightsOutcome:
    staff_records: int
    issues: tuple[IssueGroup, ...]
    source_url: str


@dataclass(frozen=True)
class RenderedReport:
    text: str | None
    caption: str | None
    document_name: str | None
    document_bytes: bytes | None
```

Implement:

```python
def counted(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or singular + "s")
    return f"{count} {word}"
```

`build_issue_groups()` accepts
`grades_report: grades.GradesSyncReport | None`; when it is `None`, it builds
only roster groups. Implement its groups in this fixed order:

1. Gradebook rows without a roster match.
2. Roster students without a Gradebook row.
3. Duplicate Gradebook rows.
4. Ambiguous roster names.
5. Duplicate matriculation numbers.
6. Profile values differing from the sheet.
7. Newly marked as departed.
8. Columns outside a semester.

Use these exact templates:

```python
IssueGroup(
    title="Gradebook rows without a roster match",
    effect="These Gradebook rows were not imported.",
    action="Make each name match the roster exactly",
    items=tuple(grades_report.no_roster_match),
)

IssueGroup(
    title="Roster students without a Gradebook row",
    effect="No grades were found for these current roster students.",
    action="Add or correct their row on the Gradebook tab",
    items=tuple(grades_report.missing_gradebook_rows),
)

IssueGroup(
    title="Duplicate Gradebook rows",
    effect="These grade rows were skipped.",
    action="Keep or correct one row for each person",
    items=tuple(
        f"{item.name} — {item.count} rows"
        for item in grades_report.duplicate_rows
    ),
)
```

Build the remaining groups with the same constructor and these item formats:

- ambiguous roster: `"{name} — {count} roster profiles"`;
- duplicate key: `"{value} — {rows} rows"`;
- differing field:
  `"{key} — {Field}: sheet @{sheet}; profile @{profile}"` for Telegram and
  without `@` for GitHub/Codeforces;
- departed: `"{full_name} ({matriculation})"`;
- ignored column:
  `"{gradebook.sheet_column_name(index)} — {label}"`.

The remaining actions are, respectively:

- `"Make the roster names unique before re-running /sync"`;
- `"Correct the duplicate matriculation numbers in the roster"`;
- `"If the profile value is current, update the sheet"`;
- `"Restore their roster row if this was unintended"`;
- `"Extend a semester header over these columns"`.

Omit empty groups.

Implement `build_rights_issue_groups()` separately so a duplicated key is
described as a Rights handle, never as a matriculation number:

```python
def build_rights_issue_groups(
    report: sheets.ReconcileReport,
) -> tuple[IssueGroup, ...]:
    groups = []
    if report.duplicates:
        groups.append(IssueGroup(
            title="Duplicate Rights handles",
            effect="These Rights rows resolve to the same profile.",
            action="Correct the duplicate Telegram handles on the Rights tab",
            items=tuple(
                f"{item.value} — {item.rows} rows"
                for item in report.duplicates
            ),
        ))
    if report.differences:
        groups.append(_differences_group(report.differences))
    return tuple(groups)
```

- [ ] **Step 4: Implement bounded cohort and final rendering**

Render a cohort as:

```text
<✅ or ⚠️> <cohort> processed

Roster: <N> students
Gradebook: <matched> of <source> rows matched · <cells> cells imported

<group title> (<item count>)
<effect> <action>:
• <item>
```

For `gradebook_error`, render:

```text
Gradebook: not updated; previous data kept

Gradebook was not updated (1)
The previous successful Gradebook data was kept. Fix the Gradebook error and re-run /sync:
• <error>
```

Append the historical-row note after the facts and before issue groups. Join
groups with two newlines. If the complete body is at most `MAX_REPORT_TEXT`,
return it as `RenderedReport.text`. Otherwise return its UTF-8 bytes and a safe
filename made with:

```python
slug = re.sub(r"[^A-Za-z0-9._-]+", "-", outcome.cohort).strip("-")
filename = f"sync-{slug or 'cohort'}.txt"
```

The document caption retains the status, roster count, Gradebook status, and
`"<N> issue groups; full diagnostics attached."`.

Use `counted()` for every user-facing count. Render the final status as warning
when any cohort has issues/error, Rights has issues, or `completion_note` is
supplied. List each cohort directly, then the Rights count. Append Rights issue
groups after that line, using the same group formatter. If `completion_note` is
supplied, append it after the Rights groups.

- [ ] **Step 5: Run the renderer tests**

Run:

```bash
uv run pytest tests/test_sync_diagnostics.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/features/directory/sync_diagnostics.py tests/test_sync_diagnostics.py
git commit -m "feat: render compact grouped sync reports"
```

---

### Task 5: Send exactly one result per cohort and one final summary

**Files:**
- Modify: `src/jbcub_bot/features/directory/handlers.py`
- Modify: `tests/test_directory_sync.py`

**Interfaces:**
- Consumes: all Task 1–4 report interfaces.
- Produces: `_source_keyboard(label: str, url: str) -> InlineKeyboardMarkup`.
- Produces: `_send_cohort_report(message, outcome) -> None`.
- Keeps: `cmd_sync(message, principal, session)` command contract.

- [ ] **Step 1: Add a healthy three-cohort message-count test**

In `tests/test_directory_sync.py`, add:

```python
class ProgressMessage:
    def __init__(self):
        self.edit_text = AsyncMock()


def _sync_message():
    progress = ProgressMessage()
    message = SimpleNamespace(
        answer=AsyncMock(return_value=progress),
        answer_document=AsyncMock(),
    )
    return message, progress


async def test_healthy_three_cohort_sync_sends_start_cohorts_and_final_only(
    session,
    monkeypatch,
):
    cohort_ids = {"2023": "AAA", "2024": "BBB", "2025": "CCC"}

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [
                COHORTS_HEADER,
                *[
                    _cohorts_row(cohort, sheet_id)
                    for cohort, sheet_id in cohort_ids.items()
                ],
            ]
        if range_ == "Rights!A:Z":
            return [
                RIGHTS_HEADER,
                ["", "Boss", "Alice", "Admin", "boss"],
            ]
        for index, (cohort, cohort_id) in enumerate(cohort_ids.items(), start=1):
            if sheet_id == cohort_id and range_ == "A:Z":
                return [
                    COHORT_HEADER,
                    _cohort_row(
                        f"3000000{index}",
                        f"Student{index}",
                        "Alex",
                        f"alex{index}",
                    ),
                ]
            if sheet_id == cohort_id and range_ == "Gradebook!A:ZZ":
                return _gradebook_rows([
                    f"Student{index}",
                    "Alex",
                    "91%",
                ])
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="Admin", role=Role.ADMIN),
        session=session,
    )

    assert message.answer.await_count == 5
    assert message.answer_document.await_count == 0
    texts = [call.args[0] for call in message.answer.await_args_list]
    assert texts[0].startswith("🔄 Sync started")
    assert [text.splitlines()[0] for text in texts[1:4]] == [
        "✅ 2023 processed",
        "✅ 2024 processed",
        "✅ 2025 processed",
    ]
    assert texts[4].startswith("✅ Sync completed")
    assert "2023 — 1 roster student; 1 Gradebook row matched" in texts[4]
    assert "2024 — 1 roster student; 1 Gradebook row matched" in texts[4]
    assert "2025 — 1 roster student; 1 Gradebook row matched" in texts[4]
    assert "Rights: 1 staff record" in texts[4]
    progress.edit_text.assert_awaited_with(
        "🔄 Sync started. Processing 3 cohorts…"
    )
```

Use Task 4's `counted()` helper through the renderer; do not scatter
pluralization branches in the handler.

- [ ] **Step 2: Run the test to verify the old message flood**

Run:

```bash
uv run pytest tests/test_directory_sync.py::test_healthy_three_cohort_sync_sends_start_cohorts_and_final_only -q
```

Expected: FAIL because the handler sends phase messages and no structured final
summary.

- [ ] **Step 3: Add Telegram transport helpers**

Import `BufferedInputFile` and `sync_diagnostics`. Add:

```python
def _source_keyboard(label: str, url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=label, url=url),
    ]])


async def _send_cohort_report(
    message: Message,
    outcome: sync_diagnostics.CohortOutcome,
) -> None:
    rendered = sync_diagnostics.render_cohort(outcome)
    keyboard = (
        _source_keyboard(
            f"Open {outcome.cohort} spreadsheet",
            outcome.source_url,
        )
        if outcome.issues or outcome.gradebook_error
        else None
    )
    if rendered.document_bytes is not None:
        await message.answer_document(
            BufferedInputFile(
                rendered.document_bytes,
                filename=rendered.document_name,
            ),
            caption=rendered.caption,
            reply_markup=keyboard,
        )
        return
    await message.answer(rendered.text, reply_markup=keyboard)
```

- [ ] **Step 4: Refactor `cmd_sync` around outcomes**

Keep credentials, parse-before-write safety, thread hopping, and deadlines.
Replace all routine `Reading…`, `rows read`, raw reconciliation, and `Sync done`
answers with this flow:

1. `progress = await message.answer("🔄 Sync started. Reading cohort index…")`.
2. Read/parse Cohorts, then
   `await progress.edit_text(f"🔄 Sync started. Processing {len(cohorts)} cohorts…")`.
3. Preserve `ignored_roster_rows` in each `parsed_cohorts` entry.
4. Parse Rights before roster writes as today.
5. For each cohort:
   - upsert roster, collect `departed` and `reconcile`, commit;
   - sync Gradebook and commit, or rollback only that Gradebook transaction and
     retain `gradebook_error = str(exc)`;
   - build issue groups and `CohortOutcome`;
   - call `_send_cohort_report()` exactly once;
   - append the outcome for the final summary.
6. Upsert/reconcile Rights and commit.
7. Build `RightsOutcome` with `build_rights_issue_groups()`; use
   `sheets.sheet_url()` for all source URLs.
8. Send `sync_diagnostics.render_final(outcomes, rights_outcome)` once, with an
   `"Open Rights spreadsheet"` keyboard only when Rights has issues.

For a Gradebook exception, keep `_log.exception(...)` and continue. Build the
outcome with `gradebook=None`, the exception string, and the roster issue
groups; `render_cohort()` adds the single Gradebook-failure block from
`gradebook_error`. Do not send a separate error message.

For roster counts use `len(records)`. For departed counts use `len(departed)`.
For Rights count use `len(rights_records)`.

- [ ] **Step 5: Update old output-shape tests**

Replace assertions for `"Sync done."`, `"N marked departed"`, raw
`drift=/unmatched=/dup=`, and `"Grades for ... skipped"` with assertions against
the cohort report or final summary. Keep every existing database-state and
exception-chain assertion unchanged.

- [ ] **Step 6: Run the complete sync test module**

Run:

```bash
uv run pytest tests/test_directory_sync.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/features/directory/handlers.py tests/test_directory_sync.py
git commit -m "feat: send one sync report per cohort"
```

---

### Task 6: Actionable failure, link, and overflow integration

**Files:**
- Modify: `src/jbcub_bot/features/directory/handlers.py`
- Modify: `src/jbcub_bot/features/directory/sync_diagnostics.py`
- Modify: `tests/test_directory_sync.py`
- Modify: `tests/test_sync_diagnostics.py`

**Interfaces:**
- Produces: friendly abort text that states database effect and repair target.
- Verifies: document fallback still counts as the cohort's one result message.
- Verifies: partial Gradebook success cannot end with a success-only summary.

- [ ] **Step 1: Add grouped-action and source-link integration tests**

Add to `tests/test_directory_sync.py`:

```python
async def test_cohort_problems_share_one_grouped_message_and_source_button(
    session,
    monkeypatch,
):
    unknown = [
        [f"Unknown{index}", "Student", "50%"]
        for index in range(10)
    ]

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Known", "Student", "known"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(*unknown)
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="Admin", role=Role.ADMIN),
        session=session,
    )

    cohort_call = message.answer.await_args_list[1]
    text = cohort_call.args[0]
    assert text.count("Gradebook rows without a roster match (10)") == 1
    assert text.count("These Gradebook rows were not imported.") == 1
    for index in range(10):
        assert f"Unknown{index} Student" in text
    button = cohort_call.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.text == "Open 2024 spreadsheet"
    assert button.url == "https://docs.google.com/spreadsheets/d/AAA"
```

- [ ] **Step 2: Add Gradebook-retention and overflow transport tests**

Update the existing broken-Gradebook test to assert:

```python
cohort_text = message.answer.await_args_list[1].args[0]
final_text = message.answer.await_args_list[-1].args[0]
assert "Gradebook: not updated; previous data kept" in cohort_text
assert "ConnectionResetError" in cohort_text or "boom" in cohort_text
assert final_text.startswith("⚠️ Sync completed with warnings")
assert "grades not updated, previous data kept" in final_text
```

Add:

```python
async def test_oversized_cohort_report_is_one_document_message(
    session,
    monkeypatch,
):
    monkeypatch.setattr(sync_diagnostics, "MAX_REPORT_TEXT", 300)

    def fake_fetch(sheet_id, sa, range_="A:Z"):
        if range_ == "Cohorts!A:Z":
            return [COHORTS_HEADER, _cohorts_row("2024", "AAA")]
        if sheet_id == "AAA" and range_ == "A:Z":
            return [
                COHORT_HEADER,
                _cohort_row("30000001", "Known", "Student", "known"),
            ]
        if sheet_id == "AAA" and range_ == "Gradebook!A:ZZ":
            return _gradebook_rows(*[
                [f"Unknown{index}", "Student", "50%"]
                for index in range(20)
            ])
        if range_ == "Rights!A:Z":
            return [RIGHTS_HEADER]
        return []

    monkeypatch.setattr(handlers, "fetch_rows", fake_fetch)
    monkeypatch.setattr(handlers, "get_settings", _settings)
    monkeypatch.setattr(handlers, "build_credentials", lambda *args: None)
    message, progress = _sync_message()

    await cmd_sync(
        message,
        principal=User(last_name="Admin", role=Role.ADMIN),
        session=session,
    )

    assert message.answer_document.await_count == 1
    document_call = message.answer_document.await_args
    assert document_call.kwargs["caption"].startswith("⚠️ 2024 processed")
    assert document_call.kwargs["reply_markup"] is not None
    assert message.answer.await_count == 2
```

The two text messages in the last assertion are start and final; the document is
the cohort's single result.

- [ ] **Step 3: Add actionable pre-write abort rendering**

For credentials, Cohorts mapping, cohort mapping/empty roster, Rights mapping,
and invalid Rights role, route the known error through one helper:

```python
async def _abort_sync(
    target,
    *,
    source: str,
    error: str,
    action: str,
    url: str | None,
) -> None:
    text = (
        f"❌ Sync aborted while reading {source}.\n\n"
        "No roster changes were made.\n\n"
        f"{error}\n\n"
        f"Fix: {action}"
    )
    keyboard = (
        _source_keyboard(f"Open {source}", url)
        if url
        else None
    )
    await target.edit_text(text, reply_markup=keyboard)
```

When credentials fail before a start message exists, answer the same text
without a button and without claiming a Google Sheet edit will fix credentials.
For sheet errors, edit the existing start message so abort does not create
another phase message.

Use these repair targets:

- Cohorts mapping: `"Correct the Cohorts tab headers or field mapping."`;
- cohort roster: `"Correct the cohort Link, headers, or first roster row."`;
- Rights mapping/role: `"Correct the Rights tab headers and role values."`.

- [ ] **Step 4: Preserve unexpected-exception propagation with partial context**

Keep the existing `raise RuntimeError(...) from exc` branches. If at least one
cohort outcome has already committed, call `render_final()` with
`completion_note` set to this line, send that warning summary, then re-raise:

```text
The processed cohorts above remain updated; the remaining sources were not completed.
```

If nothing committed, edit the start message to say:

```text
❌ Sync failed before any roster changes were committed.
```

Do not include a traceback there; the central error reporter owns it.

- [ ] **Step 5: Run sync, renderer, and error tests**

Run:

```bash
uv run pytest tests/test_directory_sync.py tests/test_sync_diagnostics.py tests/test_errors.py -q
```

Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run:

```bash
uv run pytest -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/features/directory/handlers.py src/jbcub_bot/features/directory/sync_diagnostics.py tests/test_directory_sync.py tests/test_sync_diagnostics.py
git commit -m "test: cover actionable sync failure reports"
```

---

## Final Verification

- [ ] Run `uv run pytest -q` once more from a clean process.
- [ ] Run `git diff --check`.
- [ ] Run `git status --short` and verify only intentional plan-execution files are changed.
- [ ] Review every string added by this plan for English-only user-facing copy.
- [ ] Verify a healthy three-cohort fixture produces exactly five Telegram sends, counting a document as one send.
- [ ] Verify no rendered report silently omits an issue item at the 3900-character boundary.
