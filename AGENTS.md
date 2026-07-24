# AGENTS.md

Telegram bot for a Constructor University program (students + admins). Core + drop-in feature plugins.

**Design & plan:** `docs/superpowers/specs/` and `docs/superpowers/plans/` — read before non-trivial changes.

## Commands (uv-managed)
- `uv sync` — set up env
- `uv run pytest` — tests
- `uv run python -m jbcub_bot` — run the bot (needs `.env`; see `.env.example`)
- Alembic (`uv run alembic ...`) **requires a populated `.env`** — `alembic/env.py` loads `get_settings()`.

## Conventions that aren't obvious
- **Add a feature** = a package in `src/jbcub_bot/features/<name>/` exporting `router` (aiogram `Router`) + `manifest`. The loader auto-discovers it — no central edits.
- **Field ownership:** Google Sheets are read-only source of truth for roster fields; the bot **never writes to a sheet**. Bot-owned fields (`telegram_id`, `handle_observed`, `status_line`, `visibility`) must survive re-import. `matriculation` is the only stable student key.
- **Profile reads go through `features/directory/visibility.py`** — never bypass it.
- First admins come from `BOOTSTRAP_ADMIN_IDS`.

## UX Rules

The user must confirm destructive actions (e.g., delete, reset).