# Self-service profile visibility — design

**Date:** 2026-07-27
**Status:** Approved for planning

## Goal

Let a user decide who sees each of their own profile fields — in particular hide
their Telegram handle or Gmail — from an inline screen reachable from `/me`.

Along the way, make the three field categories explicit. They exist today but
are implied by three tuples in `visibility.py`, and `telegram` sits in the wrong
one (unhideable).

## Field categories

- **Unhideable** — visible to every linked bot user; no setting: `first_name`,
  `last_name`, `role`, `primary_cohort`.
- **Configurable** — the owner and program staff always see them; students see
  them per the owner's setting: `telegram`, `status_line`, `gmail`, `github`,
  `codeforces`.
- **Hidden** — admins only. Not shown to peers, teachers, **or the owner**, who
  is not told such fields exist: `matriculation`, `telegram_id`, `birthday`,
  `citizenship`, `comment`.

`status_line` moves from unhideable to configurable (self-authored, so
self-hideable); `role` stays unhideable (not private data).

## Single field table

Metadata is spread across four places today — categories in `visibility.py`,
labels and display order in `render.py`, one shared default in `visibility.py`.
Adding a field means four edits and nothing catches a mismatch. This feature
makes the field set grow, so consolidate first:

```python
@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    category: Category          # ALWAYS | CONFIGURABLE | ADMIN_ONLY
    default: str | None = None  # CONFIGURABLE only
```

`FIELDS` in `visibility.py` is the single source of truth, ordered as the
profile renders (identical to today's `_ORDER`). `visible_fields`, the profile
renderer, and the settings screen all read it; `render.py` drops its own label
and order tables, keeping only the synthetic `Name` line that joins
`first_name` + `last_name`.

Rejected: registering profile fields per feature plugin. Only `directory` owns
profile fields today.

## Levels and defaults

`staff_only` ⊂ `cohort` ⊂ `everyone`, shown as 🔒 Staff only · 👥 My cohort ·
🌐 Everyone, and cycled in that order. **Renamed from `nobody`** because staff
see the field regardless — naming the level honestly beats a disclaimer line
under the keyboard.

Per-field defaults, not one global default: `telegram` and `status_line` default
to `everyone`, the rest to `cohort`. A single `cohort` default would silently
drop contacts of other cohorts out of every profile on deploy day; a single
`everyone` default would publish everyone's Gmail. With per-field defaults the
rendered output is byte-identical until someone taps a button.

Stored in the existing bot-owned `users.visibility` JSON, so no migration and
`/sync` can't clobber it. Legacy values are read tolerantly (`nobody` →
`staff_only`, `all_students` → `everyone`); only new names are written.

**Every tap is stored, even when it equals the default** — otherwise changing a
default in code later retroactively overrides a deliberate choice.

## Visibility rules

`visible_fields(viewer, target)`:

1. `ALWAYS` → always.
2. `ADMIN_ONLY` → only when `viewer.role is ADMIN`.
3. `CONFIGURABLE` → always when the viewer *is* the target, or the viewer is
   staff (ADMIN/TEACHER); otherwise by level: `everyone` yes, `cohort` if
   cohort-mates, `staff_only` no.

The self rule is new and fixes a live bug: a student who sets `gmail` to hidden
currently loses it from their own `/me` too.

The staff override stays — the levels govern student-to-student visibility only.

### `/cohort` must stop bypassing the service

`/cohort` prints `@handle` straight off the model. Harmless while `telegram` was
unhideable; the moment it becomes configurable, that line leaks a handle its
owner set to `staff_only`. It has to read `visible_fields`, and drop the handle
from the line when the viewer isn't allowed to see it.

## UI

Entry points: a `🔒 Who sees my data` button on `/me` (above the existing admin
buttons) and a `/privacy` command, so the feature is discoverable from `/help`
too. Any linked user configures their own fields — including staff, whose rows
carry the same configurable fields.

The screen lists every configurable field with its value and one cycling button
per field. Empty fields are listed too, as `—`: their level is still worth
setting ahead of time, and `github`/`codeforces` are in no sheet mapping yet, so
they are empty for everyone. Buttons go **two per row** — the level is an emoji,
so labels stay short and 10+ fields still fit. Odd counts leave the last field a
full-width row; `← Back to
profile` is always its own row so it never moves. No pagination (Telegram
allows 8 buttons per row and 100 per keyboard; the current field set is five).

Buttons **edit the message in place** — `/me` → settings → back to `/me` all
happen in one message.

`callback_data` is `dir:vis:<field>` and carries **no level**: the next level is
computed from the database at tap time, so a stale keyboard in another chat can
only advance a step, never write an outdated value. A tap writes, commits, and
redraws — the redrawn screen is the acknowledgement, no alert. An unknown field
(keyboard from an older deploy) answers with an alert.

Callbacks always write the caller's own row, so ownership is structural and
needs no check.

**Impersonation:** `/as <ref> /me` renders the target's profile but omits the
Privacy button — the follow-up callback arrives without `impersonate_ref`, so
the admin would edit their own settings while looking at a student's profile.

## Files

- **New:** `features/directory/privacy.py` — its own
  `Router("directory.privacy")` + `CommandRegistrar`, the `/privacy` command,
  the callbacks, and the pure screen/keyboard renderers. `handlers.py` is
  already 264 lines and owns all of `/sync`.
- **Changed:** `visibility.py` (field table, levels, rewritten
  `visible_fields`), `render.py` (reads `FIELDS`; new `me_keyboard`),
  `handlers.py` (`/me` uses `me_keyboard`; `/cohort` reads `visible_fields`),
  `__init__.py`
  (`include_router(privacy.router)`, `commands=cmd.specs + privacy.cmd.specs` —
  explicit composition rather than an import for its side effect).

## Testing strategy

- **`test_visibility.py`** — updated for the renamed levels, plus: the owner
  sees their own `staff_only` field; per-field defaults (a stranger sees
  `telegram` but not `gmail`); legacy values still read; staff override and
  admin-only rules unchanged.
- **`test_directory_render.py`** — regression anchor: `render_profile` output is
  unchanged after `telegram`/`status_line` become configurable.
- **`test_privacy.py`** (new) — pure: screen text, two-per-row chunking with
  `Back` alone, level cycle wraps.
- **`/cohort`** — a mate who hid their Telegram is still listed, without the
  handle.
- **Integration** — a callback advances the level, commits, and edits the
  message; `/me` under `/as` has no Privacy button.

## Out of scope (YAGNI)

- Editing `status_line` content (`set_status` exists but no handler wires it).
- A separate "staff can't see this either" level, or per-teacher visibility.
- "Reset to defaults", pagination, localization.
