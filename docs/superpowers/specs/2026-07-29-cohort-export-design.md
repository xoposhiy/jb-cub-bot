# /cohort for staff, with a CSV export — design

**Date:** 2026-07-29
**Status:** Approved for planning

## Goal

`/cohort` works for someone who has no cohort of their own: staff pick which
cohort to list, and get a CSV of it alongside the list — the columns another
system needs to match these people to LMS or external accounts. A student's
`/cohort` is untouched.

## Constraints that drive the decisions

- **Staff have no `primary_cohort`.** A Rights-tab row is keyed on a Telegram
  handle and carries no cohort, so today the command answers "No cohort on
  file." — the whole reason for the picker.
- **A CSV is read by a machine.** The profile renderer's `value (roster: other)`
  and its emoji labels are unusable in a cell, so the exporter needs the raw
  winning value and machine field names.
- **Profile reads go through `visibility.py`.** The export is a profile read of
  a few dozen people at once; a fixed column list in the exporter would be a
  second, silently diverging answer to "who may see what".

## Decisions

- **Staff means the role, not the missing cohort**: `Role.ADMIN` or
  `Role.TEACHER` gets the picker and the CSV even when a `primary_cohort` is on
  their own row. Keying on the empty cohort would make the behaviour depend on
  a field an admin edits in a sheet.
- **`Category.STAFF` joins `FIELDS`**, and `matriculation` and `telegram_id`
  move into it — a teacher may see them, a student and the owner may not. They
  are the two keys an external system matches on, and a teacher knows them
  already. The consequence is deliberate and wider than the CSV: a teacher now
  sees both lines on a profile card too. `birthday`, `citizenship`, `comment`
  and `source_link` stay `ADMIN_ONLY`.
- **Both a keyboard and an argument.** `/cohort` draws a button per cohort;
  `/cohort 2024` skips the tap and matches case-insensitively. An unknown name
  redraws the picker with a note rather than failing — the set of valid names is
  exactly what the buttons show. Buttons come from the cohorts that still have a
  current member, ordered reverse-alphabetically: the names are years, so that
  puts the cohort a staff member is most likely after first without parsing one.
- **A tap replaces the picker's text with the list and keeps its keyboard**, so
  the next cohort is one tap away, and sends the CSV as a separate document. Not
  a caption: a caption is capped at 1024 characters and a cohort list is not.
- **`/cohort` lists only current people, for every role.** It used to show an
  admin the departed with a `⚠️` mark; a roster export is a statement about who
  is here now, and an admin who wants a departed person searches them by name.
  `include_departed` therefore has one caller left — `rank_users` in the search.
  `render_cohort_list` keeps its `departed` mark: `/cohort` no longer feeds it
  such a row, and the mark is what stops one from reading as "still here".
- **Columns are whatever the viewer may see**, in `FIELDS` order, headed by
  field names (`first_name`, `matriculation`, …) rather than labels.
  `visible_fields(viewer, target, merged=False)` supplies the values, so a
  two-source field is one column holding the winning value with no roster note —
  `/sync` is what reports a disagreement. Two fields are skipped explicitly:
  `source_link` identifies the spreadsheet rather than the person and repeats in
  every row, and `departed_at` is empty in every row by the decision above.
- **UTF-8 with a BOM**, named `cohort-<name>.csv` with anything but
  `[A-Za-z0-9._-]` replaced. `comment` and `citizenship` are free text an admin
  typed, and Excel mojibakes a plain UTF-8 CSV; a cohort name comes from a sheet
  cell and may hold a space or a slash.
- **New modules rather than more of `handlers.py`** (already ~450 lines):
  `features/directory/cohort.py` takes the command, the picker and its callback
  on its own router, the way `edit`/`privacy`/`grades` do; `export.py` builds
  the CSV as a pure function of a viewer and a list of users. `search.py` gains
  `list_cohort_names`.
- **The callback checks `is_staff`, not `require_linked`.** Nothing is written,
  and a bootstrap admin's principal has `id is None` — `require_linked` would
  refuse exactly the admin who has no row yet.

## Out of scope

Choosing columns per export, an XLSX or Google Sheet destination, and exporting
anything but one cohort (all people, staff, a single course). Writing back to a
sheet — the bot never does.

## Verification

- `tests/test_cohort_export.py` — a teacher's header carries `matriculation` and
  `telegram_id` but not `comment`; an admin's carries both; `github_self` wins
  over `github_sheet` in one column with no `(roster:` note; the file starts
  with a BOM and quotes a value containing a comma; no `departed_at` column.
- `tests/test_directory_handlers.py` — a student still gets one message and no
  document, argument or not; `test_cohort_list_shows_a_departed_mate_to_an_admin`
  inverts to omitting them.
- `tests/test_directory_cohort.py` — staff with no argument get the picker;
  `/cohort 2024` answers with the list and one document; an unknown name redraws
  the picker with a note; a bootstrap admin (`id is None`) is served; a student
  tapping a stale button is refused.
- `tests/test_visibility.py` — a teacher sees the two `STAFF` fields, a student
  and the owner do not; `test_teacher_never_sees_admin_only` moves to `comment`.
