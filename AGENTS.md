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

- **Add a feature** = a package in `features/<name>/` exporting `register(bot)`; the loader discovers it and calls it. No `router`, no `manifest`: a package the loader cannot read crashes the boot rather than disappearing, which for a project taking student contributions is the failure mode that matters. `bot` gives you six ways to declare — `describe`, `command`, `message`, `button`, `dialog`, `note` — and one way to read back what everyone else declared, `features()`, which exists for `/help` and has no other caller. That is the whole surface, and it is meant to stay that way; `core/contract.py`. (`kb` is still on the old `router` + `manifest` shape, hosted by `core/legacy.py`; it is the last one, and nothing may join it.)
- **Features may import each other.** This reverses the older rule, and it is what lets the core stay small: code two features share stays with whichever one owns it instead of being pushed into `core/`.
- **Google Sheets are a read-only source of truth; the bot never writes to one.**
- **Profile reads go through `features/directory/visibility.py`** — read a column off the model and you leak whatever its owner hid.
- **Authentication is four middleware stages, and the order is the design.**
  `PrincipalMiddleware` (who is writing, plus the session; refuses a non-private
  chat before any lookup) → `ImpersonationMiddleware` (may replace `principal`
  with an `/as` target) → `BannerMiddleware` → `AccessMiddleware` (the one
  `departed_at` refusal). Because the refusal runs *after* the swap, an admin in
  `/as` on a departed student meets that student's own refusal, from that
  student's own line of code — there is no impersonation-flavoured copy of it to
  drift. `identity.closed_out` is the shared predicate, and it carries the
  `BOOTSTRAP_ADMIN_IDS` exemption for target and caller alike. No stage knows
  where it sits — `build_dispatcher` in `main.py` owns the order and is the one
  place that explains it. Don't reorder them without reading that comment.
- **The chain is for people the bot knows, and `public=True` means "written for
  strangers".** An unlinked caller is not refused by a middleware — that would
  close `/start`, the only way to ever get linked. The default `Guard()` refuses
  them per declaration instead, so forgetting a guard is fail-closed and only an
  explicit `public=True` opens anything. On the chain a guard *filters* rather
  than refuses (nothing was addressed to that handler), so the one who tells a
  stranger they are one is `pipeline.last_word`. Don't answer `NOT_LINKED` from
  a feature: declaring a handler public just to say it is how that ends up
  meaning "willing to turn strangers away politely".
- **`/as` is a sticky mode, not a wrapper.** While an admin is in it,
  `principal` *is* the target, so commands run with *their* role — a student
  target refuses admin commands, a staff target doesn't. The real admin is
  `impersonator`. `/unas` is declared with no `role` and `listed=False`, and
  both halves are load-bearing: any rank would refuse the one command that
  exits the mode (inside it, the rank is the target's), and being listed would
  put it in a student's `/help`. Not `public` — that would also open it to
  strangers, which is a different claim and was never the intent. Stage 2
  also never impersonates it (`is_exit_command`, which reads the command through
  `pipeline.command_of` so both sides agree on what `/unas` looks like) —
  without that, `/as <departed student>` would trap the admin until a restart
  (the mode lives only in memory, `core/impersonation.py`, so a restart is the
  other way out). The mode is core because it decides who the principal is; the
  two commands are a feature, like every other command.
- **The core owns dialog identity.** A feature declares `bot.dialog(name, on_text=..., on_cancel=...)` and writes no `StatesGroup`; the state name is derived from the feature and the dialog, so two features cannot collide. Text goes to the dialog's owner and *only* to them — someone else's open dialog must not silence your chain handler.
- **`/cancel` is the core's, one for every dialog, and commands beat dialogs.** A feature never registers `/cancel` (the loader refuses it) and never filters commands out of its own text handler. `on_cancel` is the hook for whatever the feature wants to say or redraw afterwards.
- **A chain handler returns `bool`.** `False` means "not mine" and obliges it to have answered nothing, because something else is about to answer; anything else, `None` included, counts as taken. Order is the `at=` you declare, not the alphabet of package names. `dispatch` returns the `MessageSpec` that took the message, or `None` — but that return is the core's own business; what a feature reads is `bot.registry.last_taker(chat_id)`, which the core records from it per chat. That is how a later turn learns a profile was just shown without knowing that `directory` exists. `core/pipeline.py`, `core/contract.py`.
- **Don't swallow unexpected exceptions in a handler.** Answer only the failures a user can act on; let the rest reach the `dp.errors` handler, adding context by re-raising (`raise RuntimeError("...") from exc`). A bare `except Exception` that answers and returns is how a crash becomes a silent hang.
- **Operational reports go through `core/oplog.py`**, which owns the destination, its fallback, and the judgement of what is worth reporting at all.
- **Blocking I/O in an async handler freezes the whole bot** — one event loop, no threads. Sheets reads go through `read_rows()`; anything else blocking needs `asyncio.to_thread`.

## UX Rules

- All user-facing bot text is in English, including messages, buttons, command
  descriptions, validation errors, and admin diagnostics.
- The user must confirm destructive actions (e.g., delete, reset).
