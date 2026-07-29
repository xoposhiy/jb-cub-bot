# Conditional Gradebook Mismatch Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show unmatched Gradebook names only while a current roster student is missing a Gradebook row, and report coverage against the current roster.

**Architecture:** Keep Gradebook parsing and exact matching unchanged. Make the pure diagnostics builder conditionally expose the already-computed `no_roster_match` list based on `missing_gradebook_rows`. Pass the already-normalized current roster records into grade resolution so coverage and unmatchable-row diagnostics cannot claim success for a current row without a usable profile; no code reads or interprets roster rows below the separator.

**Tech Stack:** Python 3.13, pytest, existing `GradesSyncReport` and pure `sync_diagnostics` renderer.

## Global Constraints

- All user-facing text is English.
- Do not read, parse, classify, or match names below the roster separator.
- Do not add fuzzy matching or suggested identity pairs.
- Duplicate and ambiguous-name diagnostics retain their independent behavior.
- Current rows without a matriculation number or both name fields must produce
  an actionable warning rather than a false-success report.

---

### Task 1: Conditional mismatch groups and current-roster coverage

**Files:**
- Modify: `tests/test_sync_diagnostics.py`
- Modify: `src/jbcub_bot/features/directory/sync_diagnostics.py:86-107`
- Modify: `src/jbcub_bot/features/directory/sync_diagnostics.py:193-203`
- Modify: `src/jbcub_bot/features/directory/sync_diagnostics.py:279-284`

**Interfaces:**
- Consumes: `GradesSyncReport.missing_gradebook_rows`, `GradesSyncReport.no_roster_match`, and `CohortOutcome.roster_students`.
- Produces: unchanged `build_issue_groups(...) -> tuple[IssueGroup, ...]`, `render_cohort(...) -> RenderedReport`, and `render_final(...) -> str` APIs with corrected conditional output.

- [ ] **Step 1: Write failing behavior tests**

Add focused tests to `tests/test_sync_diagnostics.py`:

```python
def test_extra_gradebook_rows_are_silent_when_all_current_students_have_rows():
    report = grades.GradesSyncReport(
        source_people=5,
        matched_people=2,
        cells=10,
        no_roster_match=["Former Student", "Unrelated Person"],
    )
    groups = build_issue_groups(sheets.ReconcileReport(), report, [])
    outcome = CohortOutcome(
        cohort="2024",
        roster_students=2,
        ignored_roster_rows=17,
        gradebook=report,
        gradebook_error=None,
        issues=groups,
        source_url="https://docs.google.com/spreadsheets/d/AAA",
    )

    rendered = render_cohort(outcome)

    assert groups == ()
    assert rendered.text is not None
    assert rendered.text.startswith("✅")
    assert "Former Student" not in rendered.text
    assert "Unrelated Person" not in rendered.text
    assert "Gradebook: 2 of 2 current roster students found" in rendered.text


def test_missing_current_student_reveals_both_directional_name_lists():
    report = grades.GradesSyncReport(
        source_people=4,
        matched_people=2,
        missing_gradebook_rows=["Ivan Petrov"],
        no_roster_match=["Petrov Ivan", "Former Student"],
    )

    groups = build_issue_groups(sheets.ReconcileReport(), report, [])

    assert [group.title for group in groups] == [
        "Roster students without a Gradebook row",
        "Gradebook rows without a roster match",
    ]
    assert groups[0].items == ("Ivan Petrov",)
    assert groups[1].items == ("Petrov Ivan", "Former Student")
    assert groups[1].action == (
        "Compare these names with the missing current roster students and "
        "correct any misspellings"
    )


def test_missing_current_student_without_extra_names_has_only_missing_group():
    report = grades.GradesSyncReport(
        source_people=2,
        matched_people=1,
        missing_gradebook_rows=["Ivan Petrov"],
    )

    groups = build_issue_groups(sheets.ReconcileReport(), report, [])

    assert [group.title for group in groups] == [
        "Roster students without a Gradebook row"
    ]
```

Update the existing final-summary test with this assertion:

```python
assert "33 of 33 current roster students found in Gradebook" in text
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
uv run pytest tests/test_sync_diagnostics.py -q
```

Expected: FAIL because unmatched Gradebook names are currently unconditional,
the groups are in the opposite order, and coverage uses matched/source
Gradebook rows.

- [ ] **Step 3: Implement the minimal diagnostics change**

In `build_issue_groups`, add the missing-current group first. Add the unmatched
Gradebook group only inside the same `if grades_report.missing_gradebook_rows`
branch and only when `grades_report.no_roster_match` is non-empty. Use:

```python
action=(
    "Compare these names with the missing current roster students and "
    "correct any misspellings"
)
```

Add a private helper:

```python
def _current_roster_found(outcome: CohortOutcome) -> int:
    if outcome.gradebook is None:
        return 0
    return max(
        0,
        outcome.roster_students
        - len(outcome.gradebook.missing_gradebook_rows),
    )
```

Render the cohort fact as:

```python
return (
    f"Gradebook: {_bare_count(_current_roster_found(outcome), 'student')} of "
    f"{counted(outcome.roster_students, 'current roster student')} found · "
    f"{counted(report.cells, 'cell')} imported"
)
```

Render the final cohort status as:

```python
return (
    f"{_bare_count(_current_roster_found(outcome), 'student')} of "
    f"{counted(outcome.roster_students, 'current roster student')} "
    "found in Gradebook"
)
```

Update existing exact renderer fixtures and grammar-spy assertions to the new
current-roster wording. Do not change `sheets.py`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
uv run pytest tests/test_sync_diagnostics.py tests/test_gradebook_store.py -q
```

Expected: PASS.

- [ ] **Step 5: Run the full regression suite**

Run:

```powershell
uv run pytest
```

Expected: PASS with no new warnings.

- [ ] **Step 6: Commit the implementation**

```powershell
git add -- tests/test_sync_diagnostics.py src/jbcub_bot/features/directory/sync_diagnostics.py
git commit -m "fix: condition Gradebook mismatch diagnostics"
```

---

### Task 2: Prevent false success for unmatchable current roster rows

**Files:**
- Modify: `tests/test_gradebook_store.py`
- Modify: `tests/test_directory_sync.py`
- Modify: `src/jbcub_bot/features/directory/grades.py`
- Modify: `src/jbcub_bot/features/directory/handlers.py`
- Modify: `src/jbcub_bot/features/directory/sync_diagnostics.py`

**Interfaces:**
- `grades.sync_cohort(...)` gains
  `current_roster_records: list[dict] | None = None`.
- `GradesSyncReport` gains `current_roster_people`,
  `current_roster_found`, and `unmatchable_roster_rows`.

- [ ] **Step 1: Add failing unit and integration regressions**

Add a unit case passing a current row with full names but no matriculation and
assert:

```python
assert report.current_roster_people == 1
assert report.current_roster_found == 1
assert report.unmatchable_roster_rows == [
    "Awaiting Number — missing matriculation number"
]
```

Add the corresponding `/sync` integration case and assert that the cohort is a
warning, contains `Current roster rows cannot receive grades`, and does not
show `Gradebook rows without a roster match`. Add a second unit case for an
incomplete name.

- [ ] **Step 2: Verify the regressions fail**

Run:

```powershell
uv run pytest tests/test_gradebook_store.py tests/test_directory_sync.py -q
```

Expected: FAIL because `sync_cohort` does not accept current roster records and
the sync report is falsely healthy.

- [ ] **Step 3: Track current-row coverage and unmatchable identities**

Pass `records` from `cmd_sync` to `grades.sync_cohort`. In `sync_cohort`, inspect
only those normalized records, count full-name matches against Gradebook source
keys, and describe missing identity fields without reading raw roster rows.
Expose unmatchable rows as:

```python
IssueGroup(
    title="Current roster rows cannot receive grades",
    effect="Grades could not be assigned for these current roster rows.",
    action="Add the missing roster identity fields and re-run /sync",
    items=tuple(grades_report.unmatchable_roster_rows),
)
```

Use `current_roster_found` for rendered coverage when
`current_roster_people > 0`; preserve the existing fallback for synthetic
renderer fixtures.

- [ ] **Step 4: Verify targeted and full suites**

Run:

```powershell
uv run pytest tests/test_gradebook_store.py tests/test_sync_diagnostics.py tests/test_directory_sync.py -q
uv run pytest
```

Expected: all tests pass.
