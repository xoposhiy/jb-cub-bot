# Feature contract — implementation plan

Spec: `docs/superpowers/specs/2026-08-08-feature-contract-design.md`.
One branch; merge only after phase F. Each phase leaves the suite green.

## Decided while planning

- **Core routes messages and callbacks itself.** No feature keeps an aiogram
  `Router`, so there is no include order and no `StateFilter` left to collide.
  Commands are a dict, buttons a longest-prefix match, the chain a sorted list.
- **`kb` migrates in phase F, after the 2026-08-07 spec lands** — its handlers are
  being rewritten by that spec, so migrating them now would be planning against
  code that will not exist. Until then it keeps its `Router` + `Intent`, hosted by
  a shim at a position after every landmark. The shim never reaches main.
- **`bot.command(..., listed=False)`** for `/unas`: `public=True` fixes the role
  problem, but it must still stay out of `/help`. A description is required either
  way — unlisted is not undocumented.
- **No `dialogs.end_for`.** `/as` clears the admin's *own* dialog, so it just calls
  `dialog.end()`.

## A. Core machinery — additive, nothing wired

| File | Goal |
|---|---|
| `core/contract.py` | new | `Registry` + per-feature `BotApi` with `describe`/`command`/`message`/`button`/`dialog`/`note`; the spec dataclasses; `TEXT`/`PHOTO`/`DOCUMENT`/`ANY` predicates; `validate()` raising on duplicate command name, duplicate button key, duplicate `at=`, missing `describe`, empty description |
| `core/guards.py` | new | one `public`/`role` check for all four kinds; keeps both existing refusals — "Admins only." for `role=ADMIN`, "Staff only." for `role=TEACHER` — and one "not linked" wording |
| `core/dialogs.py` | new | `Dialog` façade over `FSMContext` (`owner`, `start`, `data`, `update`, `end`), state name derived from feature + dialog name; middleware injecting `dialog` |
| `core/pipeline.py` | new | landmarks `LOOKUP`/`AGENT`; ordered walk; `False` declines; `dispatch` returns the taker; last taker per chat; the last word and the ops-log miss |
| `core/buttons.py` | new | longest-matching key wins; the payload after the key reaches the handler as `arg` |
| `core/help.py` | new | assembly grouped by feature, role as a badge on the line, notes (string or callable of the principal) |
| `core/loader.py` | rewrite | discover packages, call `register(bot)`, validate, log the resolved chain at startup; crash on a package with no `register`; detect the legacy `router`+`manifest` shape |

## B. Wire it up — every feature still legacy

| File | Goal |
|---|---|
| `main.py` | `build_dispatcher` builds the registry, mounts the message and callback entry points and the final fallback router, still includes legacy routers and hosts legacy intents through the shim. Prove the plumbing before migrating anything: the whole existing suite stays green |

## C. The two small features

| File | Goal |
|---|---|
| `features/help/__init__.py` | `register(bot)`; the handler closes over `bot` and asks it for every feature — which is what kills `core/registry.py` |
| `features/help/handlers.py`, `render.py` | delete; rendering lives in `core/help.py` |
| `features/impersonate/__init__.py`, `handlers.py` | `register(bot)`; `/as` with `role=ADMIN`, `/unas` with `public=True, listed=False`; `state.clear()` → `dialog.end()` |

## D. Directory

| File | Goal |
|---|---|
| `features/directory/__init__.py` | the whole `register(bot)`: 6 commands, 17 buttons, 1 dialog, 1 chain handler |
| `handlers.py` | drop `CommandRegistrar` and the 5 hand-written `role is not ADMIN` checks; `cb.data` slicing → `arg` |
| `privacy.py`, `edit.py` | drop `@router.callback_query` and `@require_linked`; `EditProfile(StatesGroup)` deleted, edit becomes `bot.dialog("edit", on_text=..., on_cancel=...)`; `/cancel` deleted (core owns it); `~F.text.startswith("/")` deleted |
| `grades.py`, `cohort.py` | `is_staff` checks → `role=Role.TEACHER` in registration |
| `screens.py` | `require_linked` deleted; the bootstrap-admin "no saved row" check stays only where a write happens (`on_value`, `cb_cycle`, `cb_clear_do`) |
| `handlers.py` (`name_search`) | `bot.message(at=LOOKUP)`; `NOTHING_MATCHED` moves to `core/pipeline.py` |
| `core/sheets.py`, `core/gradebook.py` | move into `features/directory/` |

## E. Delete the old machinery

| File | Goal |
|---|---|
| `core/registry.py`, `core/commands.py` | delete with their tests |
| `core/intents.py` | reduced to the `Intent` dataclass the shim still reads; deleted in F |
| `core/middleware.py` | `git mv` → `core/principal.py`; `HasRole` deleted |
| `tests/conftest.py` | `_reset_feature_routers` deleted — nothing is parented any more |
| `AGENTS.md` | feature = `register(bot)`; features may import each other; `/cancel` and "commands beat dialogs" are core rules |

## F. Agent split — separate plan, written after the 2026-08-07 spec lands

`features/ai/` takes the runtime, the conversation window, the budget, the trace and
the Telegram face; `features/kb/` keeps `snapshot.py` (from `core/kb_snapshot.py`),
the tools and `/kb_reload`, and extends `ai` with tools, a prompt fragment and a
source resolver. `oplog`'s `format_kb_*` move with it. The legacy shim and the last
of `core/intents.py` go here.

## Verification per phase

A: unit tests for each new module. B: the existing suite, unchanged. C–D: the
feature's own tests rewired, assertions unchanged, plus loader-validation tests
(duplicate command, duplicate key, duplicate position, missing `register`,
missing `describe`) and chain-order, dialog-routing, and `/help`-assembly tests.
E: nothing new — the suite must pass with the modules gone.
