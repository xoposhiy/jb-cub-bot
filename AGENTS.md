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

- **Add a feature** = a package in `features/<name>/` exporting `register(bot)`; the loader discovers it and calls it. No `router`, no `manifest`: a package the loader cannot read crashes the boot rather than disappearing, which for a project taking student contributions is the failure mode that matters. `bot` gives you `describe`, `command`, `message`, `button`, `dialog`, `note` and nothing else — `core/contract.py`. (`kb` is still on the old `router` + `manifest` shape, hosted by `core/legacy.py`; it is the last one, and nothing may join it.)
- **Features may import each other.** This reverses the older rule, and it is what lets the core stay small: code two features share stays with whichever one owns it instead of being pushed into `core/`.
- **Google Sheets are a read-only source of truth; the bot never writes to one.**
- **Profile reads go through `features/directory/visibility.py`** — read a column off the model and you leak whatever its owner hid.
- **Access is refused in `PrincipalMiddleware`, before any lookup** `core/principal.py`.
- **`/as` is a sticky mode, not a wrapper.** While an admin is in it,
  `principal` *is* the target, so commands run with *their* role — a student
  target refuses admin commands, a staff target doesn't. The real admin is
  `impersonator`. `/unas` is declared `public=True, listed=False`, and both
  halves are load-bearing: any role guard would refuse the one command that
  exits the mode, and being listed would put it in a student's `/help`. It is
  also exempt from the departed refusal in `PrincipalMiddleware`, which runs
  before any handler — without that exemption `/as <departed student>` would
  trap the admin until a restart (the mode lives only in memory,
  `core/impersonation.py`, so a restart is the other way out).
- **The core owns dialog identity.** A feature declares `bot.dialog(name, on_text=..., on_cancel=...)` and writes no `StatesGroup`; the state name is derived from the feature and the dialog, so two features cannot collide. Text goes to the dialog's owner and *only* to them — someone else's open dialog must not silence your chain handler.
- **`/cancel` is the core's, one for every dialog, and commands beat dialogs.** A feature never registers `/cancel` (the loader refuses it) and never filters commands out of its own text handler. `on_cancel` is the hook for whatever the feature wants to say or redraw afterwards.
- **A chain handler returns `bool`.** `False` means "not mine" and obliges it to have answered nothing, because something else is about to answer; anything else, `None` included, counts as taken. Order is the `at=` you declare, not the alphabet of package names. `dispatch` returns the handler that took the message, so a later handler can learn what happened without knowing which feature it was. `core/pipeline.py`.
- **Don't swallow unexpected exceptions in a handler.** Answer only the failures a user can act on; let the rest reach the `dp.errors` handler, adding context by re-raising (`raise RuntimeError("...") from exc`). A bare `except Exception` that answers and returns is how a crash becomes a silent hang.
- **Operational reports go through `core/oplog.py`**, which owns the destination, its fallback, and the judgement of what is worth reporting at all.
- **Blocking I/O in an async handler freezes the whole bot** — one event loop, no threads. Sheets reads go through `read_rows()`; anything else blocking needs `asyncio.to_thread`.

## UX Rules

- All user-facing bot text is in English, including messages, buttons, command
  descriptions, validation errors, and admin diagnostics.
- The user must confirm destructive actions (e.g., delete, reset).
