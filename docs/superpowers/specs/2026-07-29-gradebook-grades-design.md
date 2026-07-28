# Gradebook grades — design

**Date:** 2026-07-29
**Status:** Approved for planning

## Goal

Every cohort spreadsheet has a `Gradebook` tab beside the roster: one row per
student, one column per course, the cell holding whatever that course came to.
`/sync` must import it, and an admin or teacher must be able to open a student's
grades from their profile, grouped by semester. Students never see grades —
their own or anyone's.

## What the tab actually looks like

Measured across all three cohorts (`sdt-2025-2028`, `sdt-2024-2027`,
`sdt-2023-2026`); the numbers below drive every decision that follows.

- **Three header rows, data from row 3.** Row 0 is the semester, in merged
  cells. Row 1 is the category. Row 2 names the column.
- **Semester labels are not a format.** `Fall 2025`, `Spring 2026` — but the
  oldest cohort opens with `1st Semester` / `2d Semester`. Nothing can be parsed
  out of them; they are labels.
- **Categories:** `Mandatory`, `Methods`, `Own Choice`, `Language/Humanities`,
  `CSC Seminars`, `Extra`, `Mandatory Elective`, `MINOR`, `New Skills`,
  `Specialization`, `Thesis`, `Summer Internship`, `Comment`.
- **No matriculation column.** Identity is `Last name` / `First name`, at
  columns 0–1 in two cohorts and 1–2 in the third (`Status` takes column 0).
- **Some columns are unnamed in row 2** — `CSC Seminars` is a merged band whose
  own label is the column name.
- **Column names mix modules and their components**: `CH-230 Programming in
  C/C++ // ACS-102` next to `Programming in C/C++ Tutorial`. Only the code
  prefix distinguishes them, and `German`, `Analysis` and `Programming in
  Python & C++ Practice` have none.
- **Semester bands do not cover every column.** The per-semester
  `Credits EARNED / FAILED / Modules Failed` blocks carry their semester in
  row 1, with row 0 blank above them. The 2023 cohort's `2d Semester` merge
  stops 13 columns short of that semester's own courses.
- **Values are free text and stay that way**: `91%`, `4.33`,
  `incomplete / incomplete`, `excused / fail`, `pass (A1.2)`, `TC`, `r`, `-`,
  `IS, CL`, `все плохо`, `сама просит беседу`, and multi-line blobs listing
  extra modules with their grades. There is nothing to parse into a number.
- **Widest tab is 115 columns** — a `A:CZ` read silently drops the last
  semester's tail, so the range must be `A:ZZ`.

Two facts that shape the design more than the rest:

**The roster-end rule does not apply here.** `sheets._ends_the_roster` stops the
roster at the first row naming nobody. The 2025 cohort's Gradebook has no such
row at all, and in the other two the rows below it are departed students who
exist in the database and whose grades are wanted. So every row is read and only
nameless ones are skipped.

**Name matching works.** Folded `(last_name, first_name)` against every user of
the cohort, departed included: **49/49, 36/37, 23/24**. The two misses are
disagreements between the two tabs — `Rosa` vs `Roza`, `Aliev` vs `Alieva EM` —
which is what the unmatched report is for.

## Parsing — `core/gradebook.py`

Pure rows-and-lists, like `matching.py`: no aiogram, no sqlalchemy.

**Finding the header row:** the first of the top five rows holding both name
columns from the cohort's *existing* `Cohorts` mapping (`last_name` →
`Last name`). That mapping already fits the Gradebook in all three cohorts, so
the feature adds no configuration, and locating the row by content absorbs the
0–1 / 1–2 offset difference. Not found → `MappingError` naming what it looked
for.

A column is described by `term`, `category`, `label` and its column index:

- **`term`** — row 0, filled rightwards from the last non-empty cell; when
  blank, row 1. The fallback exists for the credit blocks, and has the welcome
  side effect of grouping them with the courses of the same semester.
- **`category`** — row 1, filled the same way, reset at each `term` boundary,
  and dropped when it equals the `term` (the credit blocks again) so nothing
  renders as `Fall 2025 → Fall 2025`.
- **`label`** — row 2 with newlines flattened; when blank, the `category`, which
  is how the unnamed `CSC Seminars` column gets its name; blank in both, skip.

**A column with no `term` is not imported, and is counted.** That discards
`Status`, the name columns and `Location /Arr.Date` — and also
`Credits Failed 1st Year (after make-up)`, which sits left of the 2024 cohort's
first band. So `/sync` reports `N columns outside a semester band ignored`: the
fix is widening a merged cell in the sheet, and the existing rule that ignoring
rows is fine but ignoring them quietly is not applies here too.

Rejected: reading true band widths from the merge ranges via
`spreadsheets().get()`. It is another blocking call per cohort plus a new
function in `sheets_client`, and buys only a better category label on about five
summary columns. Filling rightwards is wrong exactly there and harmlessly so —
the 2023 cohort's `Basic modules failed` lands under category `Comment`, but in
the right semester.

## Storage

`grades`: `user_id` (FK, indexed), `cohort` (indexed), `term`, `category`,
`label`, `value`, `position`. One row per non-empty cell — roughly 4–5k rows
across the three cohorts. Empty cells are not stored.

`position` is the sheet column index and is the only ordering: semesters sort by
the lowest `position` among their cells, courses by `position` within a
semester. Column order is chronological in all three cohorts, so no date is ever
parsed.

`cohort` lives on the row rather than being reached through
`users.primary_cohort`, because the sync replaces a whole cohort at once
(`DELETE WHERE cohort = ?`, then insert) and that delete must be bounded by the
sheet just read; a student who changes cohort then strands nothing.

Rejected: a JSON column on `users`. Less code, but the table stays queryable for
the obvious next questions ("who failed this module"), and the ask was a table.

Resolution lives in `features/directory/grades.py`, not in core: it needs
`matching.fold`, which `core` must not import. `fold` is passed in as a
parameter, the way `mark_departed` takes `today`, so `core/gradebook.py` stays
dependency-free and testable with `str.lower`.

**Candidates are the users whose `primary_cohort` is this sheet's cohort**,
departed included — the same scoping `mark_departed` uses, and for the same
reason: a name is only unambiguous inside one cohort. Two consequences worth
expecting rather than debugging later:

- A student listed in two cohorts' Gradebooks (`Hamze Al Masri Hadi` is in both
  2025 and 2024) matches only where their `primary_cohort` points and is
  reported unmatched in the other. `upsert_users` already gives such a person a
  single `primary_cohort`, so this reflects an ambiguity in the sheets rather
  than adding one.
- The unmatched list will name people the roster never mentioned while the bot
  was running: the 2025 Gradebook has 49 named rows against 33 current roster
  rows, and anyone dropped before the bot's first sync has no user row to match.
  Expected, not a fault.

Fuzzy matching is deliberately absent. Hanging a student's grades on the wrong
profile is worse than not hanging them at all, and this project reports a
disagreement rather than resolving it. Duplicate names within one Gradebook are
reported and skipped for the same reason.

## `/sync`

`gradebook_tab: str = "Gradebook"` in `Settings` (`GRADEBOOK_TAB`), the pattern
`cohorts_tab` and `rights_tab` already use.

The grades pass runs **after the roster commit**, one commit per cohort. Three
consequences, all wanted: users created by this very sync are already matchable,
so grades land on the first pass; a failure cannot roll back the roster write,
leaving its atomicity exactly as it is; and grades are read last, so a network
failure leaves them at their previous state.

`cmd_sync` does the reading — it already owns `read_rows`, with the thread hop
and the deadline — and hands rows to `grades.sync_cohort(session, cohort, rows,
mapping)`. So `grades.py` never touches the network, is testable with lists, and
the existing `handlers.fetch_rows` patches in `test_directory_sync.py` cover the
new read unchanged.

Each cohort is wrapped in `try/except Exception`: log the traceback, tell the
admin `Grades for <cohort> skipped: <error>`, continue. This is a **deliberate
exception** to the rule against swallowing unexpected exceptions in a handler,
and the reason belongs in the code comment: the roster governs access, since
`departed_at` closes it, and a typo in a grades header must not delay a
departure taking effect. Rejected: aborting the whole `/sync` as every other tab
does — it would keep a departed student's access alive until someone fixed a
header. Also rejected: a separate `/sync_grades` command, which just lets grades
drift behind the roster.

Per-cohort report: `23 rows matched, 1180 cells, unmatched=['Aliev Rufat'],
dup=-, 3 columns outside a semester band ignored`.

## Screen and access

`📊 Grades` appears on a profile when the viewer is admin or teacher **and** the
target has at least one grade row. The second condition is what keeps the button
off staff profiles and off students the sheet never matched, instead of opening
an empty screen.

`name_search` currently builds a keyboard only for admins, so a teacher gets
none at all. `render.py` gains `profile_keyboard(viewer, target, *,
show_grades)`, assembling the grades row for staff and the `🛠 Admin` row for
admins; `name_search` and `cb_admin_back` both use it. That closes the teacher
gap as a side effect.

`me_keyboard` does **not** get the button — a student must not see their own
grades, and that also settles `/as`, where `cmd_me` already hands back a
non-interactive keyboard.

The role check is written out rather than reusing `screens.require_linked`,
which refuses `principal.id is None` and would lock out a bootstrap admin. That
guard is right for privacy and edit because they write the caller's own row; this
screen only reads someone else's. `is_staff` goes into `visibility.py`, where the
same test is already inlined in `visible_fields` — putting it beside `is_admin`
in `handlers.py` would make `grades.py` import the module that imports it.

The screen replaces the profile message (`edit_text`) and opens on the **latest
semester**, that being the last one by column order. Under it: the other
semesters, three buttons per row, and `⬅️ Back`, which re-renders the profile
through `render_profile` and the same `profile_keyboard`.

Plain text, no `parse_mode` — the bot sets none anywhere, which is what keeps
`Matrix Algebra & Advanced Calculus I` from breaking the send. Categories are
sub-headings, courses are `• label: value` lines.

Callback: `dir:grades:<matriculation>:<term_index>`, 21 bytes of the 64
available. The index is the semester's position in that student's ordered list,
because the name will not fit beside a matriculation; an index that no longer
resolves answers `screens.EXPIRED` and a second tap fixes it.

Modules and their components render as a **flat list in sheet order**. Nesting
needs the "name starts with a course code" heuristic, which is wrong for
`German`, `Analysis` and `Programming in Python & C++ Practice`; three plain
consecutive lines read fine and cannot lie.

A rendered semester is truncated at Telegram's 4096 characters with a visible
`… (truncated)`. It does not fire on today's data — the largest semester,
`2d Semester` in the 2023 cohort at 26 columns, is about 2000 characters — but
without it one future multi-line blob would fail the send outright.

Departed students keep their grades: they are historical, and the profile
already carries `⚠️ Departed` for the admin reading it.

## Files

- **New:** `core/gradebook.py`, `features/directory/grades.py`, an Alembic
  revision creating `grades`, `tests/test_gradebook_parse.py`,
  `tests/test_gradebook_store.py`, `tests/test_grades_screen.py`.
- **Changed:** `core/models.py` (`Grade`), `core/config.py` (`gradebook_tab`),
  `.env.example`, `features/directory/render.py` (`profile_keyboard`),
  `features/directory/visibility.py` (`is_staff`),
  `features/directory/handlers.py` (grades pass in `cmd_sync`, `name_search` and
  `cb_admin_back` use `profile_keyboard`), `features/directory/__init__.py`
  (include `grades.router`), `tests/test_directory_sync.py`, `AGENTS.md`.

## Testing strategy

- **`test_gradebook_parse.py`** — header row found at either offset; `term`
  filled rightwards and falling back to row 1; `category` reset at a term
  boundary and dropped when it equals the term; a blank row-2 cell taking the
  band's name; columns outside every band skipped and counted; newlines
  flattened; empty cells skipped; **rows below a nameless row still imported**,
  which is the deliberate difference from the roster.
- **`test_gradebook_store.py`** — resolution by folded name including departed
  users; a name belonging to another cohort not matched; an unmatched name
  reported, not guessed; duplicate names reported and skipped; the per-cohort
  replace dropping stale rows and leaving another cohort's rows alone.
- **`test_grades_screen.py`** — grouping and order from `position`; the latest
  semester opening first; the button absent for a student viewer, present for
  teacher and admin, absent when the target has no rows; a student tapping a
  stale button refused; a bootstrap admin allowed through.
- **`test_directory_sync.py`** — a broken Gradebook reports and the roster still
  syncs and commits; a cohort whose Gradebook fails does not stop the next one.

## Out of scope (YAGNI)

- Interpreting a value: no percentages, GPAs, pass/fail, credit totals or
  averages. Cells are text in and text out.
- Distinguishing a module from its components, and anything built on that.
- Letting students see their own grades, and any per-field visibility for
  grades — the whole screen is staff-only.
- Editing grades from the bot. Sheets stay the source of truth and the bot never
  writes to one.
- Searching or filtering by grade ("who failed X"); the table shape leaves it
  open, this change does not do it.
