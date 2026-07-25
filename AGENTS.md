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

## Conventions that aren't obvious
- **Add a feature** = a package in `src/jbcub_bot/features/<name>/` exporting `router` (aiogram `Router`) + `manifest`. Register commands via `CommandRegistrar(router)`: `@cmd.command("name", "description", min_role=Role.ADMIN, public=False, usage="<args>")` — the decorator enforces `min_role`/`public` (so no in-handler role checks) and collects `CommandSpec`s for `/help`. Build the manifest with `commands=cmd.specs`. Give intents a `description` and `min_role`. The loader auto-discovers the feature and `build_dispatcher` publishes its manifest to `core/registry.py` for `/help`.
- **Field ownership:** Google Sheets are read-only source of truth for roster fields; the bot **never writes to a sheet**. Bot-owned fields (`telegram_id`, `handle_observed`, `status_line`, `visibility`) must survive re-import. `matriculation` is the only stable student key.
- **Profile reads go through `features/directory/visibility.py`** — never bypass it.

## UX Rules

The user must confirm destructive actions (e.g., delete, reset).