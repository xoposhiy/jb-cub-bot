# Provisional roster rows — design

**Date:** 2026-08-20
**Status:** Approved for planning

## Goal

First-years join the program a month before the university issues matriculation
numbers. Their cohort sheet already carries names and Telegram handles, with the
`matriculation` column empty. They must appear in the bot now — findable by
name, visible to staff, visible to each other — and turn into ordinary students
in a single `/sync` once the numbers arrive, with no manual repair of anything.

## Constraints that drive the decisions

- **`matriculation` is the only stable student key.** It is the roster upsert
  key, the departure key, the duplicate key, and the payload of every profile
  button. Nothing else on a student row can stand in for it.
- **An empty `matriculation` degrades silently, in six different ways.**
  `upsert_users` skips the record and reports success, `mark_departed` spares
  the row, `issue_link_token` raises, `admin_keyboard` and the grades button are
  not drawn, `canonical_ref` raises, and the CSV cell is blank. Each would need
  its own decision.
- **The bot never writes to a sheet.** Anything the bot learns about a
  provisional person before the numbers arrive cannot be pushed back to the
  source of truth, so it cannot be preserved by the sheet either.
- **A handle is the only way in for these people.** An invite link needs a
  matriculation, and the sheet carries handles, so `try_claim_by_handle` is the
  whole binding story for a provisional row.

## The invariant everything follows from

**A provisional row is a pure projection of the sheet.** It holds no byte that
Google Sheets does not hold. So it can be deleted and rebuilt at will, and a
mistake in the sheet costs nothing — the next `/sync` restores it. Read-only,
deletion instead of `departed_at`, and the absence of a safety catch are all the
same decision seen from three sides.

## Decisions

- **The bot invents the provisional key; the sheet leaves the column empty.**
  A roster row with no `matriculation` gets `TMP-` plus random characters,
  injected in `cmd_sync` beside `primary_cohort` and `source_link`. Asking
  admins to type placeholder keys into the sheet would make them maintain
  identifiers that mean nothing to them, and a typo there would split one person
  into two rows. Rejected: deriving the key from the person's name, which is
  stable across syncs and therefore keeps the Telegram binding — but two
  students sharing a name would silently merge into one row, and this project
  reports a name collision rather than resolving it.
- **The generator is a parameter, not a call inside the parser.** `normalize_rows`
  stays a pure function of rows and mapping, testable with lists, the way
  `mark_departed` takes `today` rather than reading the clock.
- **Provisional rows are rebuilt every sync, not matched.** This needs no code
  of its own: a fresh key matches no existing row, so `upsert_users` inserts,
  and last sync's key is absent from this sync's records, so the pass below
  deletes it. The churn is the invariant made visible rather than a side effect
  to work around.
- **`matriculation` stays a non-empty unique string, always.** One predicate,
  `identity.is_provisional(user)`, tests the prefix. Rejected: allowing an empty
  key, which trades one predicate for six silent degradations.
- **The prefix is a constant in code, matched case-insensitively.** A setting
  would fail open: an env value that drifts from what the sheet holds turns
  every provisional person into an ordinary student and lifts every restriction
  below at once. Case-insensitive because a hand-typed `Tmp-` must leave a
  person restricted, not promote them.
- **`matriculation` is stripped on import.** Without it a trailing space in a
  real number is a different key and gives one person two rows. No stored value
  currently has surrounding whitespace, so nothing changes for today's data.
- **One pass settles absentees, with two branches.** `mark_departed` becomes
  `settle_absentees` and returns what it marked *and* what it removed, because
  it no longer only marks. It selects every row of the cohort — the
  `departed_at IS NULL` filter is dropped — and branches: provisional → delete
  the row; not provisional and unmarked → set `departed_at`; not provisional and
  already marked → leave alone. Dropping the filter matters: a provisional row
  that somehow carries a date would otherwise never be selected again and would
  linger forever.
- **Deleting a row deletes its grades in the same statement.** Not defensive
  polish: `users.id` is an `INTEGER PRIMARY KEY`, so it is a rowid alias and
  SQLite reuses it, and an orphan `grades.user_id` would eventually attach to a
  different person — a teacher shown someone else's grades. The grades pass
  cannot be relied on to clean up, because it is skipped whenever a Gradebook is
  broken.
- **Binding is untouched, and it self-heals.** `try_claim_by_handle` claims a
  provisional row like any other. The binding dies with the row, and the
  replacement row arrives from the sheet with `handle_sheet` set and
  `telegram_id` empty, so the next message re-claims it. Nothing an admin has to
  do, and first-claim stays the only way to occupy a row.
- **Every write to a provisional person's own row is refused:** `/edit`
  (`status_line`, `github_self`, `codeforces_self`) and `/privacy`. That is what
  makes the row a pure projection, and it is why a lost row loses nothing.
- **The refusal answers and explains; it does not hide the screen.** One early
  return in the handler, the same choice already made for a bootstrap admin's
  unsaved principal — hiding would need the provisional test at every point a
  keyboard is built, and would leave a first-year reading a `/help` whose
  commands do nothing.
- **`is_provisional` is a free function, and the fail-open risk is accepted.** A
  future feature that writes and forgets the test is allowed by default. The
  guard is one test that enumerates every write screen, not a change to the
  feature contract.
- **Invites are refused for a provisional target.** The token is keyed on a
  value that this sync invented and the next will discard.
- **The number is hidden in exactly one place.** `visibility.field_value`
  returns `None` for a provisional `matriculation`. `render_profile` already
  skips an empty value, and `cohort_csv` already builds its header from the keys
  `visible_fields` returned rather than from non-empty values — so the profile
  line disappears and the CSV keeps the column with an empty cell, with no
  change to the renderer or the exporter. This is also the only place the rule
  belongs: a profile read that goes around `visibility.py` leaks whatever its
  owner hid.
- **The grades import is not blocked.** Gradebook rows match on folded first and
  last name inside the cohort, and a cohort's grades are deleted and reinserted
  on every sync, so grades cross the replacement on their own and break the
  invariant nowhere. Blocking them would be code that buys nothing.
- **The admin keyboard is not drawn for a provisional target.** Every action in
  it either refuses by the decision above or resets a binding the next message
  recreates. A stale payload is already safe — `cb_admin_open` and `cb_grades`
  answer "Not found." on a row that went away — so this is about not offering
  what cannot work, not about avoiding a crash.
- **`/as` on a first-year goes through `telegram_id`, not the key.**
  `find_impersonation_target` already accepts one, and a staff viewer already
  sees `Telegram ID` on the profile. `canonical_ref` prefers `telegram_id` for a
  provisional target so that a `/sync` during the mode does not end it;
  `ImpersonationMiddleware` already handles the rest, and its comment already
  names this exact case — "a `/sync` dropping a person entirely rather than
  marking them departed".
- **`/sync` reports five new counts:** provisional profiles that exist,
  provisional profiles replaced by a real number, provisional profiles removed,
  provisional rows sharing a handle, provisional rows with no handle. The first
  is reported on every sync so a forgotten placeholder cannot live quietly for
  months; the last two are the only signal a first-year who cannot get in will
  ever produce, because the bot has no way to tell them their sheet handle is
  wrong. `settle_absentees` never names a provisional row under "Newly marked as
  departed".
- **No safety catch on deletion.** The existing catch — a cohort returning zero
  rows aborts rather than marking everyone departed — protects `departed_at`,
  which the sheet does not hold. A provisional row holds nothing to protect.

## Out of scope

Visibility rules are unchanged: a first-year sees of an upperclassman what any
student with no shared cohort sees. Their sheet carries no email columns, so the
question of consent for a person who cannot use `/privacy` does not arise — if
such a column is ever added, it has to be answered before the sync runs. A
first-year who leaves during the provisional month leaves no trace, because the
row was never anything but a projection. No merge, rename, or dedupe tooling. An
empty Gradebook tab may keep reporting itself. The bot still never writes to a
sheet.

## Verification

- **`tests/test_sheets_upsert.py`** — a record with an empty `matriculation` and
  an injected provisional key is inserted; a real key with surrounding
  whitespace matches the stripped row rather than creating a second one; a
  provisional row is inserted fresh on a second pass with a new key, and the
  first is gone.
- **`tests/test_settle_absentees.py`** (renamed from the `mark_departed` tests) —
  a provisional row absent from the records is deleted, not marked; a
  provisional row that already carries `departed_at` is still deleted; a
  non-provisional row absent from the records is marked and keeps an earlier
  date; a row of another cohort is untouched; the returned removals and markings
  are separate lists; `grades` rows of a deleted user are gone and another
  user's are not.
- **`tests/test_provisional_writes.py`** — one test enumerating every write
  screen (`/edit` for each editable field, `/privacy` cycling a level) and
  asserting a provisional principal writes nothing and is told why; the same
  screens still work for an ordinary student.
- **`tests/test_visibility.py`** — `field_value` returns `None` for a
  provisional `matriculation` and the real value otherwise; a staff viewer's
  `visible_fields` still carries the key.
- **`tests/test_directory_render.py`** — a provisional profile renders no
  `Matriculation` line for an admin and gets no admin keyboard; an ordinary
  profile is unchanged, and the exact-output regression anchor still holds.
- **`tests/test_cohort_export.py`** — a cohort mixing provisional and real
  people keeps the `matriculation` column, with the provisional cell empty.
- **`tests/test_identity.py`** — `try_claim_by_handle` claims a provisional row;
  `is_provisional` is case-insensitive and false for a digits-only number;
  `issue_link_token` refuses a provisional target.
- **`tests/test_directory_sync.py`** — the five new report counts, including a
  handle shared by two provisional rows and a provisional row with no handle;
  the replacement sync reports the removals and the new real profiles and
  reports no provisional row as newly departed.
