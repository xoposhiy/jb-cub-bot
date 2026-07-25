# `/help` Role-Aware Command Listing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/help` command that lists every command and natural-language intent, filtered to what the current principal (admin / student / unlinked) is allowed to use, with human-readable descriptions.

**Architecture:** Establish a single source of truth for command/intent metadata (`CommandSpec`, enriched `Intent`) and a guard **decorator** (`CommandRegistrar`) that registers a handler AND enforces its `min_role`/`public` before the body runs — deleting the ad-hoc `role is ADMIN` checks inside handlers. A module-level registry of loaded manifests lets the auto-discovered `help` feature enumerate everything. A pure `render_help()` formats the role-filtered listing.

**Tech Stack:** Python 3.12, aiogram 3.30, SQLAlchemy, pytest (`uv run pytest`), uv-managed env.

## Global Constraints

- All bot-facing strings are **English** (bot is English-only).
- Denial strings are kept **verbatim**: insufficient role → `"Admins only."`; not linked & not public → `"You are not linked yet. Contact an admin."` (matches current handlers/tests).
- **Add a feature** = a package under `src/jbcub_bot/features/<name>/` exporting `router` (aiogram `Router`) + `manifest`. Loader auto-discovers; no central edits.
- Run tests with `uv run pytest`. Run a single test with `uv run pytest <path>::<name> -v`.
- aiogram handler injection: a guard wrapper must use `functools.wraps(fn)` and signature `async def wrapper(message, **kwargs)`, forwarding `await fn(message, **kwargs)`. aiogram unwraps `__wrapped__` to inject `fn`'s declared params (verified against aiogram 3.30).
- Role ranks live in `core/middleware.py`: `role_rank(role)` → STUDENT 0, TEACHER 1, ADMIN 2. Reuse it; do not redefine.
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass, commit.

---

## File Structure

- **New `src/jbcub_bot/core/commands.py`** — `CommandSpec` dataclass, `CommandRegistrar` (decorator that registers + guards + collects specs), internal `_guard`.
- **New `src/jbcub_bot/core/registry.py`** — module-level list of loaded `Manifest`s with `reset()`, `register(manifest)`, `all_manifests()`.
- **New `src/jbcub_bot/features/help/render.py`** — pure `render_help(manifests, principal) -> str`.
- **New `src/jbcub_bot/features/help/handlers.py`** — `/help` handler via `CommandRegistrar`.
- **New `src/jbcub_bot/features/help/__init__.py`** — exports `router` + `manifest`.
- **Modify `src/jbcub_bot/core/intents.py`** — `Intent` gains `description`, `min_role`; `IntentRouter.dispatch` filters by role.
- **Modify `src/jbcub_bot/core/loader.py`** — `Manifest.commands: list[CommandSpec]`; add `emoji: str = "📒"`.
- **Modify `src/jbcub_bot/main.py`** — `build_dispatcher` resets + populates the registry.
- **Modify `src/jbcub_bot/features/directory/`** — migrate to `CommandRegistrar`/`CommandSpec`, drop in-handler role check in `cmd_sync`, add intent metadata.
- **Modify `src/jbcub_bot/features/impersonate/`** — migrate `/as`, drop in-handler role check.
- **Modify tests/fixtures** — `tests/fixtures_features/dummy/__init__.py`, `tests/test_loader.py`, `tests/test_directory_handlers.py`.

---

## Task 1: `CommandSpec` + `CommandRegistrar` guard decorator

**Files:**
- Create: `src/jbcub_bot/core/commands.py`
- Test: `tests/test_commands.py`

**Interfaces:**
- Consumes: `Role`, `role_rank` from `core/models` / `core/middleware`.
- Produces:
  - `CommandSpec(name: str, description: str, min_role: Role = Role.STUDENT, public: bool = False, usage: str = "")` (dataclass).
  - `CommandRegistrar(router: Router)` with attribute `specs: list[CommandSpec]` and method `command(name, description, *, min_role=Role.STUDENT, public=False, usage="") -> decorator`.
  - The decorator registers the wrapped handler on `router` via `Command(name)`, returns the guarded callable, and appends the `CommandSpec` to `specs`.
  - Guard behavior: `principal is None and not public` → answers `"You are not linked yet. Contact an admin."`; `principal is not None and role_rank(principal.role) < role_rank(min_role)` → answers `"Admins only."`; otherwise runs the body.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_commands.py
import functools
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jbcub_bot.core.commands import CommandSpec, CommandRegistrar
from jbcub_bot.core.models import Role, User


class FakeRouter:
    """Captures what CommandRegistrar registers, mimicking aiogram's
    @router.message(Command(name)) call shape."""
    def __init__(self):
        self.registered = []  # (filters, callback)

    def message(self, *filters):
        def deco(callback):
            self.registered.append((filters, callback))
            return callback
        return deco


def _student():
    return User(last_name="S", role=Role.STUDENT)


def _admin():
    return User(last_name="A", role=Role.ADMIN)


def test_command_appends_spec_with_defaults():
    reg = CommandRegistrar(FakeRouter())

    @reg.command("ping", "Ping the bot.")
    async def _h(message, principal, session):
        pass

    assert len(reg.specs) == 1
    spec = reg.specs[0]
    assert spec == CommandSpec("ping", "Ping the bot.", Role.STUDENT, False, "")


def test_command_registers_on_router():
    router = FakeRouter()
    reg = CommandRegistrar(router)

    @reg.command("ping", "Ping the bot.")
    async def _h(message, principal, session):
        pass

    assert len(router.registered) == 1


async def test_guard_denies_insufficient_role():
    reg = CommandRegistrar(FakeRouter())
    ran = []

    @reg.command("sync", "Sync.", min_role=Role.ADMIN)
    async def handler(message, principal, session):
        ran.append(True)

    msg = SimpleNamespace(answer=AsyncMock())
    await handler(msg, principal=_student(), session="S")
    msg.answer.assert_awaited_once_with("Admins only.")
    assert ran == []


async def test_guard_denies_unlinked_non_public():
    reg = CommandRegistrar(FakeRouter())
    ran = []

    @reg.command("me", "Profile.")
    async def handler(message, principal, session):
        ran.append(True)

    msg = SimpleNamespace(answer=AsyncMock())
    await handler(msg, principal=None, session="S")
    msg.answer.assert_awaited_once_with("You are not linked yet. Contact an admin.")
    assert ran == []


async def test_guard_allows_public_when_unlinked():
    reg = CommandRegistrar(FakeRouter())
    ran = []

    @reg.command("help", "Help.", public=True)
    async def handler(message, principal, session):
        ran.append(True)

    msg = SimpleNamespace(answer=AsyncMock())
    await handler(msg, principal=None, session="S")
    assert ran == [True]
    msg.answer.assert_not_awaited()


async def test_guard_allows_authorized_and_forwards():
    reg = CommandRegistrar(FakeRouter())
    seen = {}

    @reg.command("sync", "Sync.", min_role=Role.ADMIN)
    async def handler(message, principal, session):
        seen["principal"] = principal
        seen["session"] = session

    admin = _admin()
    msg = SimpleNamespace(answer=AsyncMock())
    await handler(msg, principal=admin, session="S")
    assert seen == {"principal": admin, "session": "S"}


def test_guard_preserves_wrapped_for_aiogram_injection():
    reg = CommandRegistrar(FakeRouter())

    @reg.command("me", "Profile.")
    async def handler(message, principal, session):
        pass

    # functools.wraps must set __wrapped__ so aiogram inspects the real signature.
    assert getattr(handler, "__wrapped__", None) is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_commands.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jbcub_bot.core.commands'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/jbcub_bot/core/commands.py
import functools
from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command

from jbcub_bot.core.middleware import role_rank
from jbcub_bot.core.models import Role, User


@dataclass
class CommandSpec:
    name: str
    description: str
    min_role: Role = Role.STUDENT
    public: bool = False
    usage: str = ""


def _guard(fn, spec: "CommandSpec"):
    """Wrap a handler so it enforces spec.public / spec.min_role before running.

    Uses functools.wraps so aiogram unwraps __wrapped__ and injects the
    original handler's declared params (principal, session, command, ...).
    Guarded handlers must declare `principal`.
    """
    @functools.wraps(fn)
    async def wrapper(message, **kwargs):
        principal: User | None = kwargs.get("principal")
        if principal is None and not spec.public:
            await message.answer("You are not linked yet. Contact an admin.")
            return
        if principal is not None and role_rank(principal.role) < role_rank(spec.min_role):
            await message.answer("Admins only.")
            return
        return await fn(message, **kwargs)

    return wrapper


class CommandRegistrar:
    def __init__(self, router: Router):
        self.router = router
        self.specs: list[CommandSpec] = []

    def command(self, name: str, description: str, *,
                min_role: Role = Role.STUDENT, public: bool = False,
                usage: str = ""):
        spec = CommandSpec(name, description, min_role, public, usage)
        self.specs.append(spec)

        def decorator(fn):
            guarded = _guard(fn, spec)
            self.router.message(Command(name))(guarded)
            return guarded

        return decorator
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_commands.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/commands.py tests/test_commands.py
git commit -m "feat: add CommandSpec + CommandRegistrar guard decorator"
```

---

## Task 2: Enrich `Intent` and filter dispatch by role

**Files:**
- Modify: `src/jbcub_bot/core/intents.py`
- Test: `tests/test_intents.py` (add cases)

**Interfaces:**
- Consumes: `Role`, `role_rank`.
- Produces: `Intent(name, pattern, handler, description="", min_role=Role.STUDENT)`. `IntentRouter.dispatch` runs a matched intent only when the principal is allowed: `principal is None` passes only `min_role == Role.STUDENT` intents (so the not-linked handler still runs); otherwise `role_rank(principal.role) >= role_rank(intent.min_role)`. When the top match is disallowed, dispatch returns `False`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_intents.py`)**

```python
from jbcub_bot.core.models import Role, User


def test_intent_has_metadata_defaults():
    i = Intent("x", r".+", handler=None)
    assert i.description == ""
    assert i.min_role is Role.STUDENT


async def test_dispatch_skips_intent_above_principal_role():
    calls = []

    async def h(message, principal, session):
        calls.append(True)

    r = IntentRouter()
    r.register(Intent("admin-only", r".+", handler=h, min_role=Role.ADMIN))
    handled = await r.dispatch(
        "anything", message="M",
        principal=User(last_name="S", role=Role.STUDENT), session="S",
    )
    assert handled is False
    assert calls == []


async def test_dispatch_runs_student_intent_for_unlinked():
    calls = []

    async def h(message, principal, session):
        calls.append(principal)

    r = IntentRouter()
    r.register(Intent("search", r".+", handler=h, min_role=Role.STUDENT))
    handled = await r.dispatch("Ivan", message="M", principal=None, session="S")
    assert handled is True
    assert calls == [None]


async def test_dispatch_runs_admin_intent_for_admin():
    calls = []

    async def h(message, principal, session):
        calls.append(True)

    r = IntentRouter()
    r.register(Intent("admin-only", r".+", handler=h, min_role=Role.ADMIN))
    handled = await r.dispatch(
        "x", message="M",
        principal=User(last_name="A", role=Role.ADMIN), session="S",
    )
    assert handled is True
    assert calls == [True]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_intents.py -v`
Expected: FAIL — `test_intent_has_metadata_defaults` errors (unexpected keyword / missing attr) and the role tests fail (dispatch ignores role).

- [ ] **Step 3: Implement**

Replace the contents of `src/jbcub_bot/core/intents.py` with:

```python
import re
from dataclasses import dataclass
from typing import Callable

from jbcub_bot.core.middleware import role_rank
from jbcub_bot.core.models import Role


@dataclass
class Intent:
    name: str
    pattern: str
    handler: Callable
    description: str = ""
    min_role: Role = Role.STUDENT


def intent_allowed(principal, intent: "Intent") -> bool:
    if principal is None:
        return intent.min_role is Role.STUDENT
    return role_rank(principal.role) >= role_rank(intent.min_role)


class IntentRouter:
    def __init__(self):
        self._intents: list[Intent] = []

    def register(self, intent: Intent) -> None:
        self._intents.append(intent)

    def matches(self, text: str) -> Intent | None:
        for intent in self._intents:
            if re.search(intent.pattern, text, re.IGNORECASE):
                return intent
        return None

    async def dispatch(self, text, message, principal, session) -> bool:
        intent = self.matches(text)
        if intent is None or not intent_allowed(principal, intent):
            return False
        await intent.handler(message, principal, session)
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_intents.py -v`
Expected: PASS (all, including the three pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/intents.py tests/test_intents.py
git commit -m "feat: add description/min_role to Intent and role-filter dispatch"
```

---

## Task 3: `Manifest.commands: list[CommandSpec]` + `emoji`

**Files:**
- Modify: `src/jbcub_bot/core/loader.py`
- Modify: `tests/fixtures_features/dummy/__init__.py`
- Modify: `tests/test_loader.py`

**Interfaces:**
- Consumes: `CommandSpec`.
- Produces: `Manifest(name, commands: list[CommandSpec] = [], intents=[], min_role=Role.STUDENT, help_text="", emoji="📒")`. (Dataclasses don't enforce annotations at runtime, so features still importing string command lists keep working until migrated in Tasks 5–6.)

- [ ] **Step 1: Update the dummy fixture and its test (make them fail first)**

```python
# tests/fixtures_features/dummy/__init__.py
from aiogram import Router

from jbcub_bot.core.commands import CommandSpec
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role

router = Router()
manifest = Manifest(
    name="dummy",
    commands=[CommandSpec("ping", "Ping.", Role.STUDENT)],
    min_role=Role.STUDENT,
    help_text="a dummy feature",
)
```

```python
# tests/test_loader.py  — replace the two assertions that assumed strings
import tests.fixtures_features as fixtures_pkg
from jbcub_bot.core.commands import CommandSpec
from jbcub_bot.core.loader import Manifest, discover_features
from jbcub_bot.core.models import Role


def test_manifest_defaults():
    m = Manifest(name="x")
    assert m.commands == []
    assert m.intents == []
    assert m.min_role is Role.STUDENT
    assert m.emoji == "📒"


def test_discover_reads_router_and_manifest():
    features = discover_features(fixtures_pkg)
    names = {f.manifest.name for f in features}
    assert "dummy" in names
    dummy = next(f for f in features if f.manifest.name == "dummy")
    assert dummy.manifest.commands == [CommandSpec("ping", "Ping.", Role.STUDENT)]
    assert dummy.router is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_loader.py -v`
Expected: FAIL — `test_manifest_defaults` fails on `m.emoji` (no such attribute).

- [ ] **Step 3: Implement the loader change**

In `src/jbcub_bot/core/loader.py`, update the `Manifest` dataclass:

```python
from jbcub_bot.core.commands import CommandSpec  # add near top imports


@dataclass
class Manifest:
    name: str
    commands: list[CommandSpec] = field(default_factory=list)
    intents: list = field(default_factory=list)
    min_role: Role = Role.STUDENT
    help_text: str = ""
    emoji: str = "📒"
```

Leave `LoadedFeature` and `discover_features` unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_loader.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/loader.py tests/fixtures_features/dummy/__init__.py tests/test_loader.py
git commit -m "feat: type Manifest.commands as list[CommandSpec], add emoji"
```

---

## Task 4: Feature registry + `build_dispatcher` wiring

**Files:**
- Create: `src/jbcub_bot/core/registry.py`
- Modify: `src/jbcub_bot/main.py`
- Test: `tests/test_registry.py`

**Interfaces:**
- Consumes: loaded `Manifest`s from `discover_features`.
- Produces: `core/registry.py` with `reset() -> None`, `register(manifest) -> None`, `all_manifests() -> list[Manifest]` (returns a copy). `build_dispatcher` calls `registry.reset()` at the start and `registry.register(feature.manifest)` for each discovered feature.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry.py
from jbcub_bot.core import registry
from jbcub_bot.core.loader import Manifest


def test_reset_clears():
    registry.register(Manifest(name="a"))
    registry.reset()
    assert registry.all_manifests() == []


def test_register_and_all():
    registry.reset()
    m = Manifest(name="a")
    registry.register(m)
    assert registry.all_manifests() == [m]


def test_all_returns_copy():
    registry.reset()
    registry.register(Manifest(name="a"))
    snapshot = registry.all_manifests()
    snapshot.append(Manifest(name="b"))
    assert len(registry.all_manifests()) == 1
```

Also add a dispatcher-level test (append to `tests/test_registry.py`):

```python
import jbcub_bot.features as features_pkg
from jbcub_bot.core.loader import discover_features
from jbcub_bot.main import build_dispatcher


def _reset_routers():
    for feature in discover_features(features_pkg):
        feature.router._parent_router = None


def test_build_dispatcher_populates_registry():
    _reset_routers()
    build_dispatcher(session_factory=lambda: None)
    names = {m.name for m in registry.all_manifests()}
    assert {"directory", "impersonate", "help"} <= names


def test_build_dispatcher_is_idempotent():
    _reset_routers()
    build_dispatcher(session_factory=lambda: None)
    first = len(registry.all_manifests())
    _reset_routers()
    build_dispatcher(session_factory=lambda: None)
    assert len(registry.all_manifests()) == first
```

> Note: `test_build_dispatcher_populates_registry` expects the `help` feature, created in Task 8. Until then it will fail on the `help` membership — that's expected; it goes green after Task 8. The other registry tests pass now.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jbcub_bot.core.registry'`.

- [ ] **Step 3: Implement the registry**

```python
# src/jbcub_bot/core/registry.py
"""Process-wide list of loaded feature manifests.

The `help` feature is auto-discovered like any other and cannot import its
siblings, so build_dispatcher publishes every loaded manifest here for it to
read at request time.
"""
from jbcub_bot.core.loader import Manifest

_MANIFESTS: list[Manifest] = []


def reset() -> None:
    _MANIFESTS.clear()


def register(manifest: Manifest) -> None:
    _MANIFESTS.append(manifest)


def all_manifests() -> list[Manifest]:
    return list(_MANIFESTS)
```

- [ ] **Step 4: Wire `build_dispatcher`**

In `src/jbcub_bot/main.py`, add `from jbcub_bot.core import registry` with the other imports, and update the discovery loop:

```python
def build_dispatcher(session_factory, bootstrap_ids: set | None = None) -> Dispatcher:
    dp = Dispatcher()
    dp.message.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))
    dp.callback_query.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))

    registry.reset()
    for feature in discover_features(features_pkg):
        dp.include_router(feature.router)
        registry.register(feature.manifest)
        for intent in feature.manifest.intents:
            _intent_router.register(intent)

    # NL fallback: any non-command text runs through the intent router.
    @dp.message(F.text & ~F.text.startswith("/"))
    async def nl_fallback(message: Message, principal, session):
        await _intent_router.dispatch(message.text, message, principal, session)

    return dp
```

> `_intent_router` is a module-level singleton that accumulates across `build_dispatcher` calls in tests. This is pre-existing behavior; leave it as-is. The new `registry.reset()` handles registry freshness.

- [ ] **Step 5: Run the registry unit tests (skip the help-dependent one for now)**

Run: `uv run pytest tests/test_registry.py -v -k "not populates"`
Expected: PASS (`test_reset_clears`, `test_register_and_all`, `test_all_returns_copy`, `test_build_dispatcher_is_idempotent`).

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/core/registry.py src/jbcub_bot/main.py tests/test_registry.py
git commit -m "feat: add feature registry populated by build_dispatcher"
```

---

## Task 5: Migrate the `directory` feature to `CommandRegistrar`

**Files:**
- Modify: `src/jbcub_bot/features/directory/handlers.py`
- Modify: `src/jbcub_bot/features/directory/__init__.py`
- Modify: `tests/test_directory_handlers.py`
- Verify: `tests/test_directory_sync.py` still passes (denial now from guard).

**Interfaces:**
- Consumes: `CommandRegistrar`, `CommandSpec`, enriched `Intent`.
- Produces: `directory.handlers.cmd` (a `CommandRegistrar`) whose `.specs` lists `me`, `cohort`, `sync`, `start`; `directory.manifest.commands == cmd.specs`; `name_search_intent` has a description.

- [ ] **Step 1: Update the manifest-contract test (fails first)**

Replace `test_manifest_exposes_contract` in `tests/test_directory_handlers.py`:

```python
import jbcub_bot.features.directory as directory
from jbcub_bot.features.directory.handlers import name_search_intent, set_status
from jbcub_bot.core.models import Role, User


def test_manifest_exposes_contract():
    assert directory.manifest.name == "directory"
    names = {c.name for c in directory.manifest.commands}
    assert {"me", "cohort", "sync", "start"} <= names
    sync = next(c for c in directory.manifest.commands if c.name == "sync")
    assert sync.min_role is Role.ADMIN
    assert directory.manifest.min_role is Role.STUDENT
    assert any(i.name == "directory.search" for i in directory.manifest.intents)
    assert directory.router is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_directory_handlers.py::test_manifest_exposes_contract -v`
Expected: FAIL — command entries are strings without `.name`/`.min_role`.

- [ ] **Step 3: Migrate `handlers.py`**

In `src/jbcub_bot/features/directory/handlers.py`:

1. Add import and the registrar after `router = Router(name="directory")`:

```python
from jbcub_bot.core.commands import CommandRegistrar

router = Router(name="directory")
cmd = CommandRegistrar(router)
```

2. Replace each `@router.message(Command("..."))` command decorator with a `@cmd.command(...)`:

```python
@cmd.command("me", "Show your own profile.")
async def cmd_me(message: Message, principal: User, session):
    kb = admin_keyboard(principal) if principal.role is Role.ADMIN else None
    await message.answer(render_profile(principal, principal), reply_markup=kb)
```

> The guard now handles `principal is None`, so **delete** the `if principal is None:` early-return from `cmd_me`.

```python
@cmd.command("cohort", "List the people in your cohort.")
async def cmd_cohort(message: Message, principal: User, session):
    if not principal.primary_cohort:
        await message.answer("No cohort on file.")
        return
    mates = list_cohort(session, principal.primary_cohort)
    lines = [f"- {m.full_name} (@{m.handle_observed or m.handle_sheet or '?'})"
             for m in mates]
    await message.answer("Your cohort:\n" + "\n".join(lines))
```

> Delete the `principal is None or` part of the `cohort` guard — the decorator covers `None`.

```python
@cmd.command("start", "Start / link your account.", public=True)
async def cmd_start(message: Message, principal: User, session,
                    command: CommandObject):
    ...  # body unchanged (already handles principal is None)
```

```python
@cmd.command("sync", "Re-sync roster from Google Sheets.", min_role=Role.ADMIN)
async def cmd_sync(message: Message, principal: User, session):
    # DELETE the first two lines:
    #   if principal is None or principal.role is not Role.ADMIN:
    #       await message.answer("Admins only.")
    #       return
    settings = get_settings()
    ...  # rest of body unchanged
```

3. Give the search intent a description:

```python
name_search_intent = Intent(
    name="directory.search",
    pattern=r".+",
    handler=name_search,
    description="just type a name — search people",
)
```

- [ ] **Step 4: Update `__init__.py`**

```python
# src/jbcub_bot/features/directory/__init__.py
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role
from jbcub_bot.features.directory.handlers import cmd, name_search_intent, router

manifest = Manifest(
    name="directory",
    commands=cmd.specs,
    intents=[name_search_intent],
    min_role=Role.STUDENT,
    help_text="Find classmates and manage your own profile.",
)

__all__ = ["router", "manifest"]
```

- [ ] **Step 5: Run the directory tests**

Run: `uv run pytest tests/test_directory_handlers.py tests/test_directory_sync.py -v`
Expected: PASS. `test_sync_denied_for_non_admin` still passes because the guard emits `"Admins only."` for a STUDENT principal before the body runs.

> If `test_directory_sync.py` imports `cmd_sync` and calls it directly with `principal=User(role=STUDENT)`, the guarded callable returns the denial — assertion holds. No test edit needed there.

- [ ] **Step 6: Run the full suite to catch regressions**

Run: `uv run pytest -v`
Expected: PASS except the two known-pending help tests (`tests/test_registry.py::test_build_dispatcher_populates_registry`). If any directory admin/handler test referenced the old `None` early-return messages, reconcile per the guard's messages (`"You are not linked yet. Contact an admin."`).

- [ ] **Step 7: Commit**

```bash
git add src/jbcub_bot/features/directory tests/test_directory_handlers.py
git commit -m "refactor: migrate directory feature to CommandRegistrar guard"
```

---

## Task 6: Migrate the `impersonate` feature

**Files:**
- Modify: `src/jbcub_bot/features/impersonate/handlers.py`
- Modify: `src/jbcub_bot/features/impersonate/__init__.py`
- Modify: `tests/test_impersonate.py` (two edits — see Step 3).
- Verify: `tests/test_impersonate_integration.py`.

**Interfaces:**
- Produces: `impersonate.handlers.cmd` (`CommandRegistrar`) with a spec `as` (`min_role=ADMIN`, `usage="<ref> <query>"`); `impersonate.manifest.commands == cmd.specs`.

**Behavior change (spec-approved):** for `/as`, an **unlinked** caller (`principal is None`) now gets the not-linked message, not `"Admins only."` — the guard distinguishes "not linked" from "insufficient role". The existing `test_cmd_as_denied_for_none_principal` is updated to match.

- [ ] **Step 1: Migrate `handlers.py`**

In `src/jbcub_bot/features/impersonate/handlers.py`:

```python
from jbcub_bot.core.commands import CommandRegistrar

router = Router(name="impersonate")
cmd = CommandRegistrar(router)

_USAGE = "Usage: /as <matriculation|telegram_id> <query>"


@cmd.command("as", "View the bot as another user.",
             min_role=Role.ADMIN, usage="<ref> <query>")
async def cmd_as(message: Message, principal: User, session, bot, dispatcher,
                 command: CommandObject):
    # DELETE the first two lines:
    #   if principal is None or principal.role is not Role.ADMIN:
    #       await message.answer("Admins only.")
    #       return
    args = (command.args or "").strip()
    ...  # rest unchanged
```

- [ ] **Step 2: Update `__init__.py`**

```python
# src/jbcub_bot/features/impersonate/__init__.py
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role
from jbcub_bot.features.impersonate.handlers import cmd, router

manifest = Manifest(
    name="impersonate",
    commands=cmd.specs,
    intents=[],
    min_role=Role.ADMIN,
    help_text="Admin: see the bot as a given user (/as <ref> <query>).",
    emoji="🕵️",
)

__all__ = ["router", "manifest"]
```

- [ ] **Step 3: Update `tests/test_impersonate.py` for the new command shape and None behavior**

Two edits:

1. `test_manifest_exposes_as_command` — `commands` is now a `CommandSpec` list:

```python
def test_manifest_exposes_as_command():
    assert impersonate.manifest.name == "impersonate"
    assert any(c.name == "as" for c in impersonate.manifest.commands)
    assert impersonate.router is not None
```

2. `test_cmd_as_denied_for_none_principal` — an unlinked caller now gets the not-linked message (guard behavior), and the body (usage parsing) never runs:

```python
async def test_cmd_as_denied_for_none_principal(session):
    msg = SimpleNamespace(answer=AsyncMock())
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    await cmd_as(msg, principal=None, session=session, bot=object(),
                 dispatcher=dispatcher, command=_cmd("30000001 /me"))
    msg.answer.assert_awaited_once_with("You are not linked yet. Contact an admin.")
    dispatcher.propagate_event.assert_not_awaited()
```

Leave `test_cmd_as_denied_for_non_admin` (STUDENT → `"Admins only."`), `test_cmd_as_usage_on_missing_args`, `test_cmd_as_not_found`, and `test_cmd_as_success_refeeds_query` unchanged — the guard forwards to the body for the ADMIN principal in those cases.

- [ ] **Step 4: Run impersonate tests**

Run: `uv run pytest tests/test_impersonate.py tests/test_impersonate_integration.py -v`
Expected: PASS. STUDENT denial still `"Admins only."` (from the guard); None now gets the not-linked message. The integration test (`/as 30009999 /me` from an admin) still renders the student's `/me` because the admin passes the guard and `cmd_me`'s guard passes for the impersonated STUDENT principal.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/impersonate tests/test_impersonate.py
git commit -m "refactor: migrate impersonate feature to CommandRegistrar guard"
```

---

## Task 7: `render_help` pure formatter

**Files:**
- Create: `src/jbcub_bot/features/help/render.py`
- Create: `src/jbcub_bot/features/help/__init__.py` (minimal, filled in Task 8)
- Test: `tests/test_help_render.py`

**Interfaces:**
- Consumes: `Manifest` (`emoji`, `name`, `help_text`, `commands: list[CommandSpec]`, `intents: list[Intent]`), `User`, `Role`, `role_rank`.
- Produces: `render_help(manifests: list[Manifest], principal: User | None) -> str`.
  - Visibility of an entry: `entry.public` (commands only) OR `principal is not None and role_rank(principal.role) >= role_rank(entry.min_role)`. Intents have no `public`, so they are visible only to a non-`None` principal meeting `min_role`.
  - Baseline entry: `min_role is Role.STUDENT`. Elevated: `min_role` above STUDENT.
  - Layout: for each manifest (in given order) with any visible baseline entries, a header `f"{emoji} {Name} — {help_text}"` (Name = `manifest.name.capitalize()`), then command lines `f"  /{name}{' ' + usage if usage else ''} — {description}"` and intent lines `f"  💬 {description}"`. All visible elevated entries pooled into a trailing section headed `"🔐 Admin"`, command lines only (elevated intents, if any, rendered the same `  /...` way is unnecessary — pool commands; render elevated intents as `  💬 {description}`).
  - `principal is None`: render only visible (public) commands under their feature headers, then a blank line and `"You're not linked yet — ask a program admin for a one-time link."`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_help_render.py
from jbcub_bot.core.commands import CommandSpec
from jbcub_bot.core.intents import Intent
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role, User


def _directory():
    return Manifest(
        name="directory",
        emoji="📒",
        help_text="Find classmates and manage your own profile.",
        commands=[
            CommandSpec("me", "Show your own profile."),
            CommandSpec("cohort", "List the people in your cohort."),
            CommandSpec("sync", "Re-sync roster from Google Sheets.",
                        min_role=Role.ADMIN),
            CommandSpec("start", "Start / link your account.", public=True),
        ],
        intents=[Intent("directory.search", r".+", handler=None,
                        description="just type a name — search people")],
        min_role=Role.STUDENT,
    )


def _impersonate():
    return Manifest(
        name="impersonate", emoji="🕵️",
        help_text="Admin: see the bot as a given user.",
        commands=[CommandSpec("as", "View the bot as another user.",
                              min_role=Role.ADMIN, usage="<ref> <query>")],
        min_role=Role.ADMIN,
    )


def _manifests():
    return [_directory(), _impersonate()]


def test_student_sees_baseline_no_admin_section():
    out = render_help(_manifests(), User(last_name="S", role=Role.STUDENT))
    assert "/me — Show your own profile." in out
    assert "/cohort — List the people in your cohort." in out
    assert "💬 just type a name — search people" in out
    assert "🔐 Admin" not in out
    assert "/sync" not in out
    assert "/as" not in out


def test_admin_sees_admin_section_with_elevated_commands():
    out = render_help(_manifests(), User(last_name="A", role=Role.ADMIN))
    assert "🔐 Admin" in out
    assert "/sync — Re-sync roster from Google Sheets." in out
    assert "/as <ref> <query> — View the bot as another user." in out
    # elevated commands are NOT duplicated under their feature header
    assert out.index("/sync") > out.index("🔐 Admin")


def test_admin_still_sees_baseline():
    out = render_help(_manifests(), User(last_name="A", role=Role.ADMIN))
    assert "/me — Show your own profile." in out


def test_feature_header_rendered():
    out = render_help(_manifests(), User(last_name="S", role=Role.STUDENT))
    assert "📒 Directory — Find classmates and manage your own profile." in out


def test_unlinked_sees_only_public_and_notice():
    out = render_help(_manifests(), None)
    assert "/start — Start / link your account." in out
    assert "/me" not in out
    assert "💬" not in out
    assert "🔐 Admin" not in out
    assert "You're not linked yet — ask a program admin for a one-time link." in out


def test_import():
    # ensure the function is importable at module top
    assert callable(render_help)
```

Add the import at the top of the test file:

```python
from jbcub_bot.features.help.render import render_help
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_help_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jbcub_bot.features.help'`.

- [ ] **Step 3: Create the empty package init and the renderer**

```python
# src/jbcub_bot/features/help/__init__.py
# (Task 8 fills this in with router + manifest.)
```

```python
# src/jbcub_bot/features/help/render.py
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.middleware import role_rank
from jbcub_bot.core.models import Role, User

_UNLINKED_NOTICE = "You're not linked yet — ask a program admin for a one-time link."


def _command_visible(spec, principal: User | None) -> bool:
    if spec.public:
        return True
    return (principal is not None
            and role_rank(principal.role) >= role_rank(spec.min_role))


def _intent_visible(intent, principal: User | None) -> bool:
    return (principal is not None
            and role_rank(principal.role) >= role_rank(intent.min_role))


def _command_line(spec) -> str:
    head = f"/{spec.name}"
    if spec.usage:
        head += f" {spec.usage}"
    return f"  {head} — {spec.description}"


def _intent_line(intent) -> str:
    return f"  💬 {intent.description}"


def render_help(manifests: list[Manifest], principal: User | None) -> str:
    blocks: list[str] = []
    elevated: list[str] = []  # pooled admin-section lines

    for m in manifests:
        body: list[str] = []
        for spec in m.commands:
            if not _command_visible(spec, principal):
                continue
            if spec.min_role is Role.STUDENT:
                body.append(_command_line(spec))
            else:
                elevated.append(_command_line(spec))
        for intent in m.intents:
            if not _intent_visible(intent, principal):
                continue
            if intent.min_role is Role.STUDENT:
                body.append(_intent_line(intent))
            else:
                elevated.append(_intent_line(intent))
        if body:
            header = f"{m.emoji} {m.name.capitalize()} — {m.help_text}"
            blocks.append("\n".join([header, *body]))

    if principal is None:
        joined = "\n\n".join(blocks)
        return f"{joined}\n\n{_UNLINKED_NOTICE}" if blocks else _UNLINKED_NOTICE

    if elevated:
        blocks.append("\n".join(["🔐 Admin", *elevated]))

    return "\n\n".join(blocks)
```

> `principal is None` branch: `blocks` holds only public-command feature blocks (built by the loop above, since `_command_visible` returns `True` only for `public` specs when `principal is None`); append the notice separated by a blank line.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_help_render.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/features/help/__init__.py src/jbcub_bot/features/help/render.py tests/test_help_render.py
git commit -m "feat: add pure render_help formatter for /help"
```

---

## Task 8: `/help` handler + end-to-end wiring

**Files:**
- Create: `src/jbcub_bot/features/help/handlers.py`
- Modify: `src/jbcub_bot/features/help/__init__.py`
- Test: `tests/test_help_integration.py`
- Re-enable: `tests/test_registry.py::test_build_dispatcher_populates_registry`

**Interfaces:**
- Consumes: `CommandRegistrar`, `registry.all_manifests()`, `render_help`.
- Produces: `help.handlers.cmd` with a public `help` spec; `help.router`; `help.manifest` (`commands=cmd.specs`, `emoji="❓"`, `help_text="Commands you can use."`).

- [ ] **Step 1: Write the failing end-to-end tests**

```python
# tests/test_help_integration.py
"""End-to-end /help through a real dispatcher: admin vs student vs unlinked."""
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from aiogram.types import Chat, Message, Update
from aiogram.types import User as TgUser

from jbcub_bot.core.db import Base
from jbcub_bot.core.models import Role, User
from jbcub_bot.main import build_dispatcher


class FakeBot:
    def __init__(self):
        self.id = 1
        self.sent: list = []

    async def __call__(self, method, request_timeout=None):
        self.sent.append(method)
        return None


def _factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _msg(bot, tid, text):
    chat = Chat(id=tid, type="private")
    tg = TgUser(id=tid, is_bot=False, first_name="t")
    return Message(message_id=1, date=datetime.now(timezone.utc),
                   chat=chat, from_user=tg, text=text).as_(bot)


async def _run_help(factory, tid):
    dp = build_dispatcher(session_factory=factory)
    bot = FakeBot()
    upd = Update(update_id=1, message=_msg(bot, tid, "/help")).as_(bot)
    await dp.feed_update(bot, upd, dispatcher=dp)
    return "\n".join(m.text for m in bot.sent)


async def test_admin_help_has_admin_section():
    f = _factory()
    s = f()
    s.add(User(last_name="A", first_name="Anna", telegram_id=777, role=Role.ADMIN))
    s.commit(); s.close()
    out = await _run_help(f, 777)
    assert "🔐 Admin" in out
    assert "/sync" in out
    assert "/as" in out


async def test_student_help_hides_admin_section():
    f = _factory()
    s = f()
    s.add(User(last_name="Z", first_name="Zed", matriculation="30001",
               telegram_id=222, role=Role.STUDENT, primary_cohort="c"))
    s.commit(); s.close()
    out = await _run_help(f, 222)
    assert "/me" in out
    assert "🔐 Admin" not in out
    assert "/sync" not in out


async def test_unlinked_help_shows_notice():
    f = _factory()
    out = await _run_help(f, 999)  # no user row for this telegram id
    assert "You're not linked yet — ask a program admin for a one-time link." in out
    assert "🔐 Admin" not in out
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_help_integration.py -v`
Expected: FAIL — `/help` unhandled (no help feature yet), so `bot.sent` is empty and assertions fail.

- [ ] **Step 3: Implement the handler**

```python
# src/jbcub_bot/features/help/handlers.py
from aiogram import Router
from aiogram.types import Message

from jbcub_bot.core import registry
from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.models import User
from jbcub_bot.features.help.render import render_help

router = Router(name="help")
cmd = CommandRegistrar(router)


@cmd.command("help", "List the commands you can use.", public=True)
async def cmd_help(message: Message, principal: User, session):
    await message.answer(render_help(registry.all_manifests(), principal))
```

> The guard passes `principal` through even when `None` because `help` is `public=True`; `render_help` handles the `None` case.

- [ ] **Step 4: Fill in the package `__init__.py`**

```python
# src/jbcub_bot/features/help/__init__.py
from jbcub_bot.core.loader import Manifest
from jbcub_bot.features.help.handlers import cmd, router

manifest = Manifest(
    name="help",
    commands=cmd.specs,
    intents=[],
    help_text="Commands you can use.",
    emoji="❓",
)

__all__ = ["router", "manifest"]
```

- [ ] **Step 5: Run the help integration tests**

Run: `uv run pytest tests/test_help_integration.py -v`
Expected: PASS.

> If a test's `bot.sent` contains multiple messages, `_run_help` joins them; assertions use substring checks so this is robust. `/help` itself will appear under a `❓ Help` header — expected.

- [ ] **Step 6: Re-run the previously-pending registry test**

Run: `uv run pytest tests/test_registry.py -v`
Expected: PASS including `test_build_dispatcher_populates_registry` (now that `help` exists).

- [ ] **Step 7: Full suite**

Run: `uv run pytest`
Expected: PASS (entire suite green).

- [ ] **Step 8: Commit**

```bash
git add src/jbcub_bot/features/help tests/test_help_integration.py
git commit -m "feat: add /help command listing role-appropriate commands"
```

---

## Task 9: Update `AGENTS.md` feature-authoring note

**Files:**
- Modify: `AGENTS.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update the "Add a feature" convention**

In `AGENTS.md`, under "Conventions that aren't obvious", replace the "Add a feature" bullet with:

```markdown
- **Add a feature** = a package in `src/jbcub_bot/features/<name>/` exporting `router` (aiogram `Router`) + `manifest`. Register commands via `CommandRegistrar(router)`: `@cmd.command("name", "description", min_role=Role.ADMIN, public=False, usage="<args>")` — the decorator enforces `min_role`/`public` (so no in-handler role checks) and collects `CommandSpec`s for `/help`. Build the manifest with `commands=cmd.specs`. Give intents a `description` and `min_role`. The loader auto-discovers the feature and `build_dispatcher` publishes its manifest to `core/registry.py` for `/help`.
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "docs: document CommandRegistrar feature-authoring pattern"
```

---

## Self-Review Notes

- **Spec coverage:** metadata model → Tasks 1–3; guard decorator → Task 1 (+ migrations 5,6); intent filtering → Task 2; registry → Task 4; help feature + render + layout → Tasks 7,8; testing strategy → tests in every task incl. e2e (Task 8); "migrate all features" scope → Tasks 5,6; docs → Task 9.
- **Denial strings** are asserted verbatim in Tasks 1/5/6 and match existing tests.
- **Type consistency:** `CommandSpec` fields, `CommandRegistrar.command(...)` keyword-only options, `registry.all_manifests()`, `render_help(manifests, principal)` used identically across tasks.
- **Ordering caveat:** `tests/test_registry.py::test_build_dispatcher_populates_registry` (Task 4) intentionally depends on the `help` feature and is only asserted green in Task 8 — called out inline.
