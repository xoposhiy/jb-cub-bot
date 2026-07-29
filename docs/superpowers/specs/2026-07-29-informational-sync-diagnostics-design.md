# Informational `/sync` diagnostics

**Date:** 2026-07-29
**Status:** Approved for planning

## Goal

Keep useful Gradebook import information visible without presenting harmless
ignored columns as a failed or warning sync.

## Decisions

- Every diagnostic group carries whether it affects sync status; existing
  groups default to warning behavior.
- `Columns outside a semester` is informational. It remains fully rendered
  with its count, column names, explanation, and repair instruction.
- A cohort containing only this informational group starts with `✅`.
- A complete sync containing only informational groups ends with
  `✅ Sync completed`.
- Any real cohort issue, Rights issue, unavailable/failed Gradebook, or partial
  completion retains the existing `⚠️` behavior.
- Rendering, overflow documents, and source-sheet links continue to include
  informational groups; only status calculation changes.

## Testing

Cover an ignored-column-only cohort and final report as successful while the
column section remains visible. Also cover informational columns combined with
a real issue to ensure both reports remain warnings.

## Rejected

- Matching the group title in status code couples behavior to English copy.
- Rendering ignored columns outside the group model duplicates report logic.
