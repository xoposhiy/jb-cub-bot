# Self-service profile fields — design

**Date:** 2026-07-27
**Status:** Approved for planning

## Goal

Let a user edit their own `status_line`, GitHub and Codeforces accounts from an
inline screen reachable from `/me`. `status_line` has been bot-owned and
unwritable since the visibility work; `github`/`codeforces` are columns no sheet
mapping fills, so today they are empty for everyone.

## Two columns per account field

The roster may also carry a GitHub or Codeforces handle, so each account field
gets the `handle_sheet` / `handle_observed` treatment:

| column | owner |
|---|---|
| `github_sheet`, `codeforces_sheet` | the sheet (`SHEET_OWNED`) |
| `github_self`, `codeforces_self` | the bot — what the user typed |

The existing `github`/`codeforces` columns are **renamed** to `*_sheet` rather
than reused as-is: an unsuffixed name next to a `_self` one stops saying whose
value it is. The rename is free — no mapping supplies those columns, so every
cell is NULL. One migration: two `alter_column`, two `add_column`.

`status_line` needs no second column; no sheet has such a field.

Bot-owned fields that must survive re-import become `telegram_id`,
`handle_observed`, `status_line`, `github_self`, `codeforces_self`, `visibility`.

### Reading a two-source field

`FieldSpec` gains `sources=("github_self", "github_sheet")` — first non-empty
wins, self-reported first. Rendering:

- one value set, or both equal → `GitHub: alice`
- both set and different → `GitHub: alice-dev (roster: alice)`

Everyone who may see the field sees the parenthetical. It is one field with one
visibility level; a second level for "the roster's version of it" would be a
setting nobody asked for. Rejected: showing only the self-reported value, which
hides from the owner the fact that two systems disagree.

`telegram`'s `@` prefix stays a special case in `field_value`, but it stops
being the only two-source field in the code.

### Drift

`reconcile()` compares one pair today (`handle_sheet` vs `handle_observed`).
It generalizes over a table of pairs, adding the two account fields, and drift
entries become `<key>:<field>` so the `/sync` report says what disagreed. An
admin fixes the sheet by hand — the bot still never writes to a sheet.

## Editing

`/edit`, plus an `✏️ Edit my profile` button on `/me`. Any linked user, staff
included — same reach as `/privacy`, whose screen and callback shape this
mirrors: one button per editable field, two per row, `← Back to profile` alone
on the last row, everything edited in place in one message.

Which fields are editable is one more flag in `FIELDS`, so the screen keeps
listing itself and nothing else enumerates profile fields.

Tapping a field turns that message into a prompt (`Send your new GitHub
username.` plus the current value) with `[🗑 Clear] [Cancel]`. The next text
message is the value; the screen is redrawn with a `✅ GitHub updated` header.

**Clearing asks for confirmation** (`[Yes, clear GitHub] [Cancel]`), reusing the
two-step shape of the existing `dir:reset` flow, per the destructive-action rule.

`/edit` under `/as` renders without a keyboard, for the reason `/privacy`
already does: the callback arrives without `impersonate_ref`, so the admin would
edit their own row while looking at someone else's profile. `me_keyboard`'s
`allow_privacy` flag becomes `interactive` and gates both buttons.

### Waiting for text costs a core change

State lives in `FSMContext` (the `Dispatcher`'s default `MemoryStorage`, no new
dependency), and `nl_fallback` in `main.py` gains `StateFilter(None)`. Without
it the feature cannot work at all: a `Dispatcher`'s own handlers run before its
sub-routers, so the `.+` name-search intent would swallow every value before
`directory` saw it.

A restart drops in-flight state and the next message becomes a name search.
Acceptable — persisting a half-typed field buys nothing.

`Cancel` and a `/cancel` command clear the state; outside a state `/cancel`
answers `Nothing to cancel.` The command is global by necessity — a state can
only be escaped from outside the screen that created it.

## Validation

A new `accounts.py` keeps normalization pure and separate from the network:

- URLs unwrap to a handle (`github.com/alice`, `codeforces.com/profile/alice`),
  a leading `@` is dropped.
- GitHub `^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$`, Codeforces
  `^[A-Za-z0-9_.-]{3,24}$`.
- `status_line`: newlines collapse to spaces, 120 characters max, longer is
  refused. One line keeps the profile, `/cohort` and the `/privacy` screen from
  being reflowed by one person's essay.
- `verify(kind, handle, fetch=...) -> EXISTS | MISSING | UNKNOWN` over `aiohttp`
  (already an aiogram dependency) with a 5s timeout. Only an explicit "no such
  user" (GitHub 404, Codeforces `status: FAILED`) is `MISSING`; a timeout, 5xx,
  403 rate limit or unparseable body is `UNKNOWN`.

`MISSING` keeps the state open: `GitHub has no user alice-dev. Send another
value or /cancel.` **`UNKNOWN` saves** with `⚠️ Saved. GitHub didn't answer, so
I couldn't verify alice-dev.` — GitHub allows 60 anonymous requests an hour per
IP, and a shared IP running out of them must not lock everyone out of editing.

`fetch` is a parameter so tests inject a fake; no test touches the network.

## Files

- **New:** `features/directory/edit.py` (own `Router("directory.edit")`,
  `CommandRegistrar`, states, callbacks, pure screen renderers),
  `features/directory/accounts.py`, one alembic revision.
- **Changed:** `visibility.py` (`sources`/`editable` in `FieldSpec`,
  two-source `field_value`), `render.py`, `models.py`, `sheets.py`
  (`SHEET_OWNED`, generalized `reconcile`), `main.py` (`StateFilter(None)`),
  `directory/__init__.py`, `AGENTS.md`.

## Testing strategy

- **`test_accounts.py`** — normalization (URL, `@`, bad characters, length), and
  all three `verify` outcomes against a fake `fetch`.
- **`test_edit.py`** — screen text, refusal by format and by length, a save, a
  confirmed clear.
- **Updated** — `test_directory_render.py` (both values differ → parenthetical),
  `test_directory_sync.py` (drift on the new pairs), `test_visibility.py`.
- **Integration** — `/edit` under `/as` has no keyboard; a message sent while
  editing reaches the field handler instead of the name search.

## Out of scope (YAGNI)

- Editing `gmail` or the Telegram handle; any bot write to a sheet.
- Admin editing of someone else's self-reported value.
- Rate-limit budgeting, caching or re-verification of stored handles.
- Deleting the user's own value message to keep the chat tidy.
