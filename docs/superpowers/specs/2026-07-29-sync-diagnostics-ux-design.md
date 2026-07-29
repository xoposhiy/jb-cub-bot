# `/sync` diagnostics UX — design

**Date:** 2026-07-29
**Status:** Approved for planning

## Goal

Make `/sync` easy to follow and easy to repair without flooding the admin chat.
Every user-facing string remains English. A successful three-cohort run should
normally produce five bot messages: one start message, one complete report per
cohort, and one final summary.

## Message flow

The start message acknowledges the command and says how many cohorts will be
processed. Each cohort then produces exactly one result message after both its
roster and Gradebook work finish. `Rights` is summarized in the final message,
not reported as another routine progress message.

The final message states success, success with warnings, partial completion, or
failure. It lists every processed cohort by name with its roster student count
and Gradebook result, rather than reducing them to “N of M.” A failed Gradebook
is explicit that the roster was updated and the grades from the previous
successful sync were kept. The final message also gives the Rights staff count.

## Cohort report

The report begins with the cohort name and these facts:

- students imported from the current roster;
- Gradebook people matched and grade cells imported, or why grades stayed
  unchanged;
- historical rows ignored below the roster separator, as an informational note.

Problems are grouped by meaning. Each group has a count, one explanation of the
effect, one repair instruction, then all affected people, keys, or columns.
Instructions are never repeated per item. User-facing text does not use the
ambiguous internal labels `unmatched`, `dup`, or `drift`.

Supported cohort groups are:

- Gradebook rows without an exact roster match — those grade rows were skipped;
- current roster students without a Gradebook row;
- duplicate Gradebook rows, grouped once per name with its row count;
- ambiguous roster names that prevent safe Gradebook assignment;
- duplicate matriculation numbers, naming the duplicated key;
- profile values that differ from the sheet, showing both values;
- students newly marked departed, with the access consequence;
- suspicious Gradebook columns outside a semester, naming the column and label.

Expected metadata columns outside semester bands are not problems. Rights
problems follow the same grouped pattern in the final message, including
duplicate handles and differing profile values.

For every actionable cohort report, one inline button opens that cohort's source
spreadsheet; the group instruction names the roster or `Gradebook` tab to edit.
Rights problems use the Rights spreadsheet link. The bot never edits a sheet.

## Large reports

The normal text report includes every affected item. The renderer budgets below
Telegram's 4096-character limit; it never drops items behind “and N more.”
When the complete report would not fit, that cohort's single result is instead
sent as a `sync-<cohort>.txt` document containing the same grouped report. Its
English caption retains the cohort counts and issue-group counts, and its inline
button opens the spreadsheet. This preserves one result message per cohort
without pagination, persisted callback state, or hidden diagnostics.

## Boundaries and failure states

Report data is structured first and rendered separately, so progress, database
effects, diagnostics, links, and overflow handling cannot diverge across handler
branches. A parse failure before roster writes says that no roster changes were
made. Any failure after a commit identifies what was updated and what retained
its previous data; a later generic “Sync done” must not overwrite that status.
Unexpected failures still propagate to the central error reporter.

## Testing

Pin the message count and ordering for a healthy three-cohort run; exact English
labels and per-cohort/final counts; every diagnostic group's direction, effect,
and repair instruction; source buttons; healthy, warning, partial, and fatal
outcomes; Gradebook preservation after failure; and the text-document boundary
around Telegram's limit.

## Rejected

- One message per phase remains too noisy.
- A single final message looks hung during slow Sheets reads.
- Per-item repair prose grows rapidly and repeats itself.
- Truncating with “and N more” hides work the admin must complete.
- Interactive pagination adds callback state solely for an exceptional report.
