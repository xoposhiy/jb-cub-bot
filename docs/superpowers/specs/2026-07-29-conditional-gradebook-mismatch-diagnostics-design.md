# Conditional Gradebook mismatch diagnostics

**Date:** 2026-07-29
**Status:** Approved for planning

## Goal

Make `/sync` report Gradebook name mismatches only when an admin has a current
roster problem to repair. Extra Gradebook rows alone are harmless because they
are skipped, so they must not turn an otherwise healthy cohort sync into a
warning.

## Decisions

- `Roster students without a Gradebook row` remains the trigger: it contains
  only current, non-departed roster students.
- When that list is empty, omit `Gradebook rows without a roster match`
  completely, regardless of how many extra Gradebook rows were skipped.
- When at least one current roster student is missing, show both lists in the
  same report. The complete extra-name list helps an admin spot a spelling
  mismatch; after the missing student is corrected, that extra list becomes
  silent again.
- Do not read, parse, classify, or match names below the roster separator.
  Existing roster normalization continues to stop at the separator.
- A current normalized roster row without a matriculation number or both name
  fields gets its own actionable warning because grades cannot be safely
  assigned to it. This uses only the already-normalized rows above the
  separator.
- Do not add fuzzy matching or suggested pairs. Grades remain exact-match-only,
  and the diagnostic presents evidence without guessing identities.
- Describe Gradebook coverage in terms of current roster students, so ignored
  extra rows do not produce a contradictory healthy report such as “33 of 49
  rows matched.”
- Duplicate Gradebook rows, ambiguous roster names, and other diagnostics keep
  their existing independent behavior.

## Testing

Cover three cases: all current students present with extra Gradebook rows
produces no mismatch warning; a missing current student with extra Gradebook
rows shows both directional lists; and a missing current student without extra
rows shows only the missing-student list. Pin the current-roster coverage text
and verify no historical roster data is introduced into grade matching. Cover
current rows with a missing matriculation number or incomplete name so they
cannot produce a false-success report.

## Rejected

- Reading historical roster names would treat arbitrary data below the
  separator as students and violate the roster boundary.
- Always showing extra Gradebook rows creates a warning with no required fix.
- Fuzzy suggestions add complexity and risk misleading identity guesses.
