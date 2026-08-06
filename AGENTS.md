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

## Shell: bash vs PowerShell

Both PowerShell and bash are available and their quoting is **not** interchangeable:

| Tool | Multi-line string | Never |
|---|---|---|
| Bash | `<<'EOF'` … `EOF` heredoc | `@'` … `'@` |
| PowerShell | `@'` … `'@` (closing `'@` at column 0) | `<<'EOF'` |

**For a multi-line commit message, always `git commit -F - <<'EOF'` in bash.**
One memorized form for the job means there is nothing left to pick wrong.

## Conventions that aren't obvious

- **Add a feature** = a package in `features/<name>/` exporting `router` + `manifest`; the loader discovers it. Register commands through `CommandRegistrar`.
- **Google Sheets are a read-only source of truth; the bot never writes to one.**
- **Profile reads go through `features/directory/visibility.py`** — read a column off the model and you leak whatever its owner hid.
- **Access is refused in `PrincipalMiddleware`, before any lookup** `core/middleware.py`.
- **`/as` is a sticky mode, not a wrapper.** While an admin is in it,
  `principal` *is* the target, so commands run with *their* role — a student
  target refuses admin commands, a staff target doesn't. The real admin is
  `impersonator`. `/unas` is deliberately **not** registered through
  `CommandRegistrar`: that would list it in a student's `/help` and, with any
  `min_role`, refuse the one command that exits the mode. It is also exempt
  from the departed refusal in `PrincipalMiddleware`, which runs before any
  handler — without that exemption `/as <departed student>` would trap the
  admin until a restart (the mode lives only in memory, `core/impersonation.py`,
  so a restart is the other way out).
- **Use FSM for multi stage dialogs**.
- **An intent handler returns `bool`.** `False` means "not mine" and obliges it to have answered nothing, because something else is about to answer. `core/intents.py`.
- **Don't swallow unexpected exceptions in a handler.** Answer only the failures a user can act on; let the rest reach the `dp.errors` handler, adding context by re-raising (`raise RuntimeError("...") from exc`). A bare `except Exception` that answers and returns is how a crash becomes a silent hang.
- **Operational reports go through `core/oplog.py`**, which owns the destination, its fallback, and the judgement of what is worth reporting at all.
- **Blocking I/O in an async handler freezes the whole bot** — one event loop, no threads. Sheets reads go through `read_rows()`; anything else blocking needs `asyncio.to_thread`.

## UX Rules

- All user-facing bot text is in English, including messages, buttons, command
  descriptions, validation errors, and admin diagnostics.
- The user must confirm destructive actions (e.g., delete, reset).
