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
- **Field ownership:** Google Sheets are read-only source of truth for roster fields; the bot **never writes to a sheet**. Bot-owned fields (`telegram_id`, `handle_observed`, `status_line`, `visibility`) must survive re-import. `matriculation` is the only stable student key.
- **Profile reads go through `features/directory/visibility.py`** — never bypass it.
  A handler that reads a profile column off the model leaks whatever its owner
  hid (`/cohort` did exactly this until telegram became hideable).
- **Adding a profile field = one line in `FIELDS`** (`features/directory/visibility.py`):
  name, label, category (`ALWAYS` / `CONFIGURABLE` / `ADMIN_ONLY`), and a default
  level for configurable ones. The visibility service, the profile renderer, and
  the `/privacy` screen all read that table; nothing else lists profile fields.
  `ADMIN_ONLY` fields are never shown or hinted at to their owner.
- **`user.visibility` must be reassigned, not mutated** — it is a plain `JSON`
  column, so `user.visibility[k] = v` leaves the instance clean and the commit
  writes nothing. Use `visibility.set_level`.
- **Don't swallow unexpected exceptions in a handler.** Answer only the failures a user can act on (a bad mapping, a missing column); let the rest propagate — `build_dispatcher`'s `dp.errors` handler replies, logs, and DMs the full traceback to `BOOTSTRAP_ADMIN_IDS`. Add context by re-raising: `raise RuntimeError("/sync failed reading the Rights tab") from exc`. A bare `except Exception` that answers and returns is how a crash turns into a silent hang.
- **Blocking I/O in an async handler freezes the whole bot** (one event loop, no threads). Google Sheets reads go through `read_rows()`, which adds a thread hop and a deadline.

## UX Rules

The user must confirm destructive actions (e.g., delete, reset).