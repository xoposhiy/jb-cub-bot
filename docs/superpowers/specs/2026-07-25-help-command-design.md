# `/help` command with role-aware listing — design

**Date:** 2026-07-25
**Status:** Approved for planning

## Goal

Add a `/help` command that lists every command and natural-language intent the
bot offers, **filtered to what the current principal is actually allowed to
use**. Admins see admin capabilities; students see only student capabilities;
unlinked users see only public commands.

Each entry carries a human-readable description so `/help` doubles as
lightweight documentation.

## Motivation & the core problem

Permissions are currently enforced at **two** levels:

- **Feature level** — `Manifest.min_role` (e.g. `impersonate` = `ADMIN`).
- **Command level, inside handlers** — e.g. `/sync` lives in the `directory`
  feature (`min_role=STUDENT`) but checks `principal.role is ADMIN` in its own
  body and replies `"Admins only."`.

There is no metadata that captures per-command roles or descriptions. A naive
`/help` that filtered only on `Manifest.min_role` would wrongly show `/sync` to
students — leaking false information about their rights.

**Decision (from brainstorming): establish a single source of truth.** Enrich
the manifest with per-command/per-intent metadata *and* move the role check out
of handler bodies into a shared guard decorator that reads that same metadata.
`/help` and the runtime guard then agree by construction.

Scope decision (from brainstorming): **migrate all existing features**
(`directory`, `impersonate`) onto the new model, not just the admin commands.

## Metadata model (single source of truth)

New module `core/commands.py`:

```python
@dataclass
class CommandSpec:
    name: str             # "sync"
    description: str      # "Re-sync roster from Google Sheets."
    min_role: Role = Role.STUDENT
    public: bool = False  # usable without a linked account (/start, /help)
    usage: str = ""       # optional args hint, e.g. "<ref> <query>"
```

`Intent` (in `core/intents.py`) gains two fields so intents are listable and
filterable on the same footing as commands:

```python
@dataclass
class Intent:
    name: str
    pattern: str
    handler: Callable
    description: str = ""
    min_role: Role = Role.STUDENT
```

`Manifest.commands` changes type from `list[str]` → `list[CommandSpec]`. This is
the single registry of a feature's commands; nothing hand-lists command name
strings anymore.

## Guard = a decorator (replaces in-handler role checks)

A small registrar bound to a feature's router:

```python
# core/commands.py
class CommandRegistrar:
    def __init__(self, router: Router):
        self.router = router
        self.specs: list[CommandSpec] = []

    def command(self, name, description, *, min_role=Role.STUDENT,
                public=False, usage=""):
        spec = CommandSpec(name, description, min_role, public, usage)
        self.specs.append(spec)
        def decorator(fn):
            guarded = _guard(fn, spec)          # enforce before running body
            self.router.message(Command(name))(guarded)
            return guarded                       # module name -> guarded fn
        return decorator
```

Usage in a feature:

```python
cmd = CommandRegistrar(router)

@cmd.command("sync", "Re-sync roster from Google Sheets.", min_role=Role.ADMIN)
async def cmd_sync(message, principal, session): ...
```

`_guard(fn, spec)` returns a wrapper that:

1. Reads `principal` from the injected kwargs.
2. If `principal is None and not spec.public` → reply
   `"You are not linked yet. Contact an admin."` and stop.
3. Else if `principal is not None` and
   `role_rank(principal.role) < role_rank(spec.min_role)` → reply
   `"Admins only."` and stop.
4. Otherwise call the original `fn`, forwarding **only** the kwargs `fn`
   declares (inspected once from its signature), so handlers keep their current
   narrow signatures (`message, principal, session`, plus `command`, `bot`,
   `dispatcher` where used).

Denial strings are kept **identical to today** so existing behavior and
unit tests hold. The `role is ADMIN` checks currently inside `cmd_sync` and
`cmd_as` are **deleted** — the decorator owns them.

`/start` is registered `public=True` (linking happens before a principal
exists); its body keeps handling the `None` case. `/help` is `public=True`.

### Intent guarding

`IntentRouter.dispatch` also consults `min_role`: an intent whose `min_role`
the principal doesn't meet is skipped (treated as no-match), so intents filter
consistently with commands. (Today the only intent, `directory.search`, is
`STUDENT` and its handler already handles `principal is None`; behavior is
unchanged.)

### Why a wrapping decorator (not an aiogram filter)

Alternative considered: attach an aiogram role *filter* to each handler and add
a catch-all fallback to emit denials. Rejected because per-command denial
messaging becomes awkward (one fallback for all commands) and it wouldn't keep
the direct-call unit tests (`cmd_sync(msg, principal=…)`) working. The wrapper
keeps denials per-command and keeps those tests green.

## Feature registry

`/help` must enumerate **all** loaded features, but it is auto-discovered like
any other feature and cannot import its siblings. Solution: `core/registry.py`
holds a module-level list of loaded `Manifest`s.

`build_dispatcher` clears the registry at the start of each call (mirroring the
existing feature-router reset in `conftest.py`) and appends each discovered
feature's manifest as it wires it. `/help` reads
`registry.all_manifests()` at request time.

## The `/help` feature

New package `features/help/`:

- `handlers.py` — registers `/help` (public) via `CommandRegistrar`. The handler
  reads `principal`, pulls manifests from the registry, and calls the pure
  renderer.
- `render.py` — `render_help(manifests, principal) -> str`, pure and
  aiogram-free (mirrors `directory/render.py`), so formatting is unit-testable
  in isolation.
- `__init__.py` — exports `router` + `manifest`.

### Filtering rule

An entry (command or intent) is visible when:

```
spec.public  or  (principal is not None
                  and role_rank(principal.role) >= role_rank(spec.min_role))
```

`role_rank` / `Role` reuse `core/middleware.py` (rank: STUDENT 0, TEACHER 1,
ADMIN 2).

### Layout (grouped by feature, English)

- For each feature (in load order), a header line: `<emoji> <Feature name> —
  <help_text>`, followed by its visible **baseline** entries (`min_role ==
  STUDENT`): commands as `  /name[ usage] — description`, intents as
  `  💬 <description>`.
- All visible **elevated** entries (`min_role > STUDENT`) are collected into a
  single trailing `🔐 Admin` section (so a student never sees it; an admin sees
  `/sync`, `/as` there). This matches the approved preview:

```
📒 Directory — Find classmates and manage your own profile.
  /me — Show your profile
  /cohort — List your cohort
  💬 just type a name — search people

🔐 Admin
  /sync — Re-sync roster from Google Sheets.
  /as <ref> <query> — View the bot as another user
```

- **Unlinked** principal (`None`): render only `public` commands, then the line
  `You're not linked yet — ask a program admin for a one-time link.`

## Files touched

- **New:** `core/commands.py`, `core/registry.py`, `features/help/__init__.py`,
  `features/help/handlers.py`, `features/help/render.py`.
- **Changed:** `core/loader.py` (`Manifest.commands: list[CommandSpec]`),
  `core/intents.py` (`Intent` fields + `dispatch` role check), `main.py`
  (`build_dispatcher` populates/clears registry), `features/directory/*`
  (migrate to `CommandRegistrar`, drop in-handler role check in `cmd_sync`),
  `features/impersonate/*` (migrate `/as`, drop in-handler role check).
- **Tests updated:** `test_loader.py` and `test_directory_handlers.py`
  (`commands` is now `list[CommandSpec]`), `tests/fixtures_features/dummy`
  (use `CommandSpec`). `test_directory_sync.py` / `test_impersonate.py`
  denial-path tests continue to assert `"Admins only."` (now produced by the
  guard).

## Testing strategy

- **`render_help` (pure):** student view has no `🔐 Admin` section and omits
  admin entries; admin view includes the Admin section with `/sync` and `/as`;
  unlinked view shows only public commands plus the not-linked line; descriptions
  and usage hints appear.
- **Guard decorator:** student invoking an admin command → `"Admins only."`,
  body not run; unlinked invoking a non-public command → not-linked message;
  authorized principal → body runs; `fn` receives only its declared kwargs.
- **Intent filtering:** `dispatch` skips an intent whose `min_role` exceeds the
  principal's.
- **Registry:** `build_dispatcher` leaves the registry containing every loaded
  manifest (and is idempotent across repeated calls).
- **End-to-end `/help`** through a real `build_dispatcher` (mirroring
  `test_impersonate_integration.py`): admin `/help` output contains the Admin
  section; student `/help` output does not.

## Out of scope (YAGNI)

- Telegram `setMyCommands` blue-menu integration.
- Per-command `/help <command>` deep help.
- Localization / Russian output (bot is English today).
- Pagination (command set is small).
