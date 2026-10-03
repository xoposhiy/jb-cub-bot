# One person, many rows — design

**Date:** 2026-10-03
**Status:** Approved for planning

## Goal

A name search must always lead to a profile, even when the roster holds the
same person twice. Two real cases broke this:

- A student who moved from a bachelor cohort to a master cohort is listed in
  both, under one matriculation number. Each `/sync` wrote the row twice and
  the cohort processed last won `primary_cohort`, so the bachelor cohort lost
  the person: no grades from its Gradebook, no line in its `/cohort` list.
- An admin typed `not in CN` into the matriculation column. The bot took it
  for a real number, so when the real number arrived the old row became
  departed and stayed forever next to the new one. An admin's search showed
  two equal names as plain text, with no way to open either.

## Constraints

- `matriculation` stays the only student key, and the bot still never writes
  to a sheet.
- A departed row is the bot's own record and must survive; a provisional row
  holds nothing and may be deleted.
- `settle_absentees` must not mark a person departed while another cohort
  still names them.

## Decisions

- **A shortlist is a list of buttons.** Each button opens that row's profile
  by `id`, because staff rows have no matriculation. The label carries the
  cohort, and for an admin the departure date, so two equal names differ.
  This also covers real namesakes.
- **An active row beats a departed one.** If exactly one active row is in the
  leading group, `classify` picks it; the departed rows of that group come
  back as buttons under the profile. Rejected: hiding departed rows from
  admins — the departure history is why they are kept.
- **Only digits are a real matriculation number.** Anything else is a
  placeholder: on import it gets a `TMP-` key like an empty cell, and
  `is_provisional` is true for any stored non-numeric key. So the existing
  `not in CN` row is deleted by the next `/sync`, with no manual repair. This
  replaces "only the `TMP-` prefix" from the provisional rows design; the new
  rule restricts more people, never fewer.
- **A person named by several cohorts is written once, from the newest
  cohort.** Newest = the highest first four-digit year in the cohort name. The
  other cohorts go to `past_cohorts`, which visibility already reads. The
  other cohorts still count the person as present when they settle
  absentees. Rejected: a cohort membership table — `past_cohorts` already
  exists and the roster is a few hundred rows.
- **Cohort membership means primary or past** for Gradebook matching and for
  `/cohort` lists. Settling absentees stays on `primary_cohort` only.
- **Every sheet cell is stripped on import**, like the matriculation number
  already was: a trailing space made the two cohorts' copies of one name
  differ.
