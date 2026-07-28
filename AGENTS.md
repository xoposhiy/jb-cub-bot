# AGENTS.md

Telegram bot for a Constructor University program (students + admins). Core + drop-in feature plugins.

**Design & plan:** `docs/superpowers/specs/` and `docs/superpowers/plans/` — read before non-trivial changes.

## Writing specs

Keep them short enough to read end to end — aim for one screen. A spec records
only what the author would regret not knowing later: the goal, the constraints
that forced the design, and the decisions with a one-line why (including the
rejected option, when the choice was close). Leave everything else out —
decisions that can just as well be made while implementing, code snippets,
restatements of how a library works, and explanations aimed at teaching the
reader. Explain those in conversation instead, where they can be skipped.

## Commands (uv-managed)
- `uv sync` — set up env
- `uv run pytest` — tests
- `uv run python -m jbcub_bot` — run the bot (needs `.env`; see `.env.example`)
- Alembic (`uv run alembic ...`) **requires a populated `.env`** — `alembic/env.py` loads `get_settings()`.
- `init_db()` runs `alembic upgrade head` on every start, so a new migration needs no deploy change — but a bad migration takes the bot down at boot.

## Conventions that aren't obvious
- **Add a feature** = a package in `src/jbcub_bot/features/<name>/` exporting `router` (aiogram `Router`) + `manifest`. Register commands via `CommandRegistrar(router)`: `@cmd.command("name", "description", min_role=Role.ADMIN, public=False, usage="<args>")` — the decorator enforces `min_role`/`public` (so no in-handler role checks) and collects `CommandSpec`s for `/help`. Build the manifest with `commands=cmd.specs`. Give intents a `description` and `min_role`. The loader auto-discovers the feature and `build_dispatcher` publishes its manifest to `core/registry.py` for `/help`.
- **Field ownership:** Google Sheets are read-only source of truth for roster fields; the bot **never writes to a sheet**. Bot-owned fields (`telegram_id`, `handle_observed`, `status_line`, `github_self`, `codeforces_self`, `visibility`) must survive re-import. `matriculation` is the only stable student key. An account field a user can set has **two columns** — `*_sheet` (the roster's, listed in `sheets.SHEET_OWNED`) and `*_self` (theirs). `visibility.field_value` prefers the user's and shows the roster's beside it when the two disagree; `sheets.DRIFT_PAIRS` makes `/sync` report the disagreement. Nothing resolves it automatically — an admin edits the sheet.
- **Column mapping lives in the sheets, not in the repo.** On the `Cohorts` tab, `Cohort` and `Link` describe the cohort and **every other column is a `User` field name**; the cell under it is what that field is called in that cohort's own sheet (so a roster GitHub column is a `github_sheet` column holding `GitHub`). A blank cell means that cohort lacks the field. The `Rights` tab is ours to shape, so its columns *are* our field names and it maps to itself via `sheets.identity_mapping`. Both headers are checked against `sheets.KNOWN_FIELDS` and an unrecognized name aborts `/sync` — a typo in a hand-edited header would otherwise silently drop a whole field. Adding a syncable field means adding it to `SHEET_OWNED`, not editing a config file.
- **A roster ends at the first row naming nobody** (`sheets._ends_the_roster`): both cohort sheets keep departed students below a blank separator row, so `normalize_rows` stops there and `/sync` reports how many rows it ignored. A row still counts as a person if it has *either* a name or a `matriculation`, so a student awaiting a number doesn't truncate the roster. `sheets.mark_departed` then stamps `departed_at` on that cohort's members the roster no longer names — scoped to `primary_cohort` so Rights-only staff and other cohorts are never touched, and a cohort yielding zero rows aborts the sync instead of marking everyone. `upsert_users` clears `departed_at`, so putting a row back restores the person.
- **`departed_at` revokes access, not just visibility.** The refusal lives in `PrincipalMiddleware` because that is where every entry point authenticates — one check closes commands, intents and callbacks together. `identity.try_claim_by_handle` and `tokens.verify_link_token` also refuse a marked row, so neither the handle nor an invite is a way back in; `BOOTSTRAP_ADMIN_IDS` is the deliberate exemption. `/as` checks its **target** separately from the caller, so an admin impersonating a departed student sees that student's refusal instead of their profile — the exemption covers an admin's own access, never their view of someone else. Row-level hiding is separate and opt-in: `search.rank_users`/`list_cohort` take `include_departed`, which callers pass from `handlers.is_admin`.
- **Profile reads go through `features/directory/visibility.py`** — never bypass it.
  A handler that reads a profile column off the model leaks whatever its owner
  hid (`/cohort` did exactly this until telegram became hideable).
- **Adding a profile field = one line in `FIELDS`** (`features/directory/visibility.py`):
  name, label, category (`ALWAYS` / `CONFIGURABLE` / `ADMIN_ONLY`), and a default
  level for configurable ones. The visibility service, the profile renderer, and
  the `/privacy` screen all read that table; nothing else lists profile fields.
  `ADMIN_ONLY` fields are never shown or hinted at to their owner.
  `editable=True` plus an `edit_hint` puts the field on the `/edit` screen, and
  `accounts.NORMALIZERS` must gain an entry for it — a test in `test_edit.py`
  enforces the pairing.
- **`user.visibility` must be reassigned, not mutated** — it is a plain `JSON`
  column, so `user.visibility[k] = v` leaves the instance clean and the commit
  writes nothing. Use `visibility.set_level`.
- **A feature that waits for free text must own an FSM state.** `nl_fallback` in
  `main.py` is registered on the `Dispatcher`, whose own handlers run before
  every sub-router, so plain text reaches a feature only while
  `StateFilter(None)` fails — that is, only while the sender is in a state.
  Exclude commands from a state handler (`~F.text.startswith("/")`) so
  `/cancel` still works.
- **An intent handler returns `bool`.** `False` means "not mine" — the router
  offers the message to the next intent, so a declining handler must not have
  answered. Anything else (including `None`) ends the walk. Below its
  threshold the name search declines; `nl_fallback` in `main.py` owns the
  reply when nothing took the message.
- **Name matching lives in `features/directory/matching.py`** and is pure
  string work — no aiogram, no sqlalchemy. Every roster name is Latin while
  queries arrive in Cyrillic, so comparison happens on `fold` (no diacritics,
  no case, no punctuation) and `skeleton` (one code per name, whatever the
  transliteration). Thresholds are the constants at the top of that module;
  the rule tuples `GLIDES` and `RULES` are order-dependent, and
  `tests/test_matching.py` is the table that keeps a new rule from fixing one
  name and breaking three.
- **Don't swallow unexpected exceptions in a handler.** Answer only the failures a user can act on (a bad mapping, a missing column); let the rest propagate — `build_dispatcher`'s `dp.errors` handler replies, logs, and DMs the full traceback to `BOOTSTRAP_ADMIN_IDS`. Add context by re-raising: `raise RuntimeError("/sync failed reading the Rights tab") from exc`. A bare `except Exception` that answers and returns is how a crash turns into a silent hang.
- **Blocking I/O in an async handler freezes the whole bot** (one event loop, no threads). Google Sheets reads go through `read_rows()`, which adds a thread hop and a deadline.

## UX Rules

The user must confirm destructive actions (e.g., delete, reset).