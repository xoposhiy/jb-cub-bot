# Admin Impersonation (`/as`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let admins run `/as <ref> <query>` to see the bot exactly as a referenced student sees it.

**Architecture:** The `/as` handler re-feeds a copy of the message (with the query text) back through the aiogram dispatcher, passing an `impersonate_ref` in the event data. `PrincipalMiddleware` becomes impersonation-aware: when the real caller is an admin and `impersonate_ref` is present, it swaps `data["principal"]` to the resolved target. Every real handler runs untouched.

**Tech Stack:** Python 3, aiogram 3.x, SQLAlchemy, pytest (async), uv.

## Global Constraints

- Features are self-contained packages in `src/jbcub_bot/features/<name>/` exporting `router` (aiogram `Router`) + `manifest`; auto-discovered by the loader — no central edits to register them.
- Handlers enforce their own role checks (matching `cmd_sync`); `manifest.min_role` is descriptive only.
- Profile reads go through `features/directory/visibility.py` — never bypass it (impersonation reuses it automatically via the swapped principal).
- `matriculation` is the only stable student key; it wins over `telegram_id` in reference resolution even when numeric.
- Run tests with `uv run pytest`.
- Reference resolution: match `User.matriculation == ref` first; only if no match and `ref.isdigit()`, match `User.telegram_id == int(ref)`.

---

### Task 1: `find_impersonation_target` resolver

**Files:**
- Modify: `src/jbcub_bot/core/identity.py`
- Test: `tests/test_identity.py`

**Interfaces:**
- Consumes: `User` model, `sqlalchemy.select` (already imported in `identity.py`).
- Produces: `identity.find_impersonation_target(session, ref: str) -> User | None` — returns the user whose `matriculation == ref`, else (when `ref.isdigit()`) the user whose `telegram_id == int(ref)`, else `None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_identity.py`:

```python
def test_find_impersonation_target_by_matriculation(session):
    u = _add(session, matriculation="30000001", telegram_id=777)
    got = identity.find_impersonation_target(session, "30000001")
    assert got.id == u.id


def test_find_impersonation_target_by_telegram_id(session):
    u = _add(session, matriculation="ABC", telegram_id=777)
    got = identity.find_impersonation_target(session, "777")
    assert got.id == u.id


def test_find_impersonation_target_prefers_matriculation_when_numeric(session):
    by_matr = _add(session, matriculation="777", telegram_id=111)
    _add(session, matriculation="OTHER", telegram_id=777)
    got = identity.find_impersonation_target(session, "777")
    assert got.id == by_matr.id  # matriculation wins even though numeric


def test_find_impersonation_target_not_found(session):
    _add(session, matriculation="30000001", telegram_id=777)
    assert identity.find_impersonation_target(session, "nope") is None
    assert identity.find_impersonation_target(session, "999") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_identity.py -k impersonation -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'find_impersonation_target'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/jbcub_bot/core/identity.py` (after `find_by_telegram_id`):

```python
def find_impersonation_target(session, ref: str) -> User | None:
    user = session.scalar(select(User).where(User.matriculation == ref))
    if user is not None:
        return user
    if ref.isdigit():
        return session.scalar(select(User).where(User.telegram_id == int(ref)))
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_identity.py -k impersonation -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/identity.py tests/test_identity.py
git commit -m "feat: add find_impersonation_target resolver"
```

---

### Task 2: Impersonation-aware `PrincipalMiddleware`

**Files:**
- Modify: `src/jbcub_bot/core/middleware.py`
- Test: `tests/test_middleware.py`

**Interfaces:**
- Consumes: `identity.find_impersonation_target` (Task 1), `Role`, `User`.
- Produces: `PrincipalMiddleware` honors `data["impersonate_ref"]`: when set AND the real principal is an admin, `data["principal"]` becomes `find_impersonation_target(session, ref)` (may be `None`) and `data["impersonator"]` holds the real admin principal. Otherwise unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_middleware.py` (note: `identity` import needed):

```python
async def test_middleware_impersonation_swaps_for_admin(session):
    from jbcub_bot.core.models import User
    session.add(User(last_name="Admin", telegram_id=777, role=Role.ADMIN))
    session.add(User(last_name="Stud", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal"] = data["principal"]
        captured["impersonator"] = data.get("impersonator")

    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="a"))
    await mw(handler, event, {"impersonate_ref": "30000001"})
    assert captured["principal"].matriculation == "30000001"
    assert captured["impersonator"].telegram_id == 777


async def test_middleware_impersonation_ignored_for_non_admin(session):
    from jbcub_bot.core.models import User
    session.add(User(last_name="Stud", telegram_id=777, role=Role.STUDENT))
    session.add(User(last_name="Other", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal"] = data["principal"]

    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="s"))
    await mw(handler, event, {"impersonate_ref": "30000001"})
    assert captured["principal"].telegram_id == 777  # not swapped
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_middleware.py -k impersonation -v`
Expected: FAIL (`test_middleware_impersonation_swaps_for_admin` — principal is the admin, not the student)

- [ ] **Step 3: Write minimal implementation**

Rewrite the body of `PrincipalMiddleware.__call__` in `src/jbcub_bot/core/middleware.py`. Add `from jbcub_bot.core.models import Role, User` (already imported) and use `identity` (already imported):

```python
    async def __call__(self, handler, event, data):
        session = self.session_factory()
        data["session"] = session
        try:
            user = getattr(event, "from_user", None)
            principal = None
            if user is not None:
                principal = identity.resolve(session, user.id, user.username)
                principal = identity.apply_bootstrap(
                    principal, user.id, user.username, self.bootstrap_ids
                )
            ref = data.get("impersonate_ref")
            if ref is not None and principal is not None \
                    and principal.role is Role.ADMIN:
                data["principal"] = identity.find_impersonation_target(
                    session, ref
                )
                data["impersonator"] = principal
            else:
                data["principal"] = principal
            return await handler(event, data)
        finally:
            session.close()
```

- [ ] **Step 4: Run the full middleware suite to verify pass + no regressions**

Run: `uv run pytest tests/test_middleware.py -v`
Expected: PASS (all — existing `test_middleware_injects_principal`, `test_middleware_bootstrap_admin`, plus 2 new)

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/middleware.py tests/test_middleware.py
git commit -m "feat: make PrincipalMiddleware impersonation-aware"
```

---

### Task 3: `impersonate` feature package + `/as` handler

**Files:**
- Create: `src/jbcub_bot/features/impersonate/__init__.py`
- Create: `src/jbcub_bot/features/impersonate/handlers.py`
- Test: `tests/test_impersonate.py`

**Interfaces:**
- Consumes: `identity.find_impersonation_target` (Task 1); impersonation-aware middleware (Task 2); aiogram-injected `bot` and `dispatcher` in handler data.
- Produces: `handlers.cmd_as(message, principal, session, bot, dispatcher, command)`; `handlers.router` (aiogram `Router` named `"impersonate"`); package exports `router` + `manifest` with `commands=["as"]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_impersonate.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jbcub_bot.features.impersonate as impersonate
from jbcub_bot.features.impersonate.handlers import cmd_as
from jbcub_bot.core.models import Role, User


def _cmd(args):
    return SimpleNamespace(args=args)


def test_manifest_exposes_as_command():
    assert impersonate.manifest.name == "impersonate"
    assert "as" in impersonate.manifest.commands
    assert impersonate.router is not None


async def test_cmd_as_denied_for_non_admin(session):
    msg = SimpleNamespace(answer=AsyncMock())
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    await cmd_as(msg, principal=User(last_name="S", role=Role.STUDENT),
                 session=session, bot=object(), dispatcher=dispatcher,
                 command=_cmd("30000001 /me"))
    msg.answer.assert_awaited_once_with("Admins only.")
    dispatcher.propagate_event.assert_not_awaited()


async def test_cmd_as_denied_for_none_principal(session):
    msg = SimpleNamespace(answer=AsyncMock())
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    await cmd_as(msg, principal=None, session=session, bot=object(),
                 dispatcher=dispatcher, command=_cmd("30000001 /me"))
    msg.answer.assert_awaited_once_with("Admins only.")
    dispatcher.propagate_event.assert_not_awaited()


async def test_cmd_as_usage_on_missing_args(session):
    admin = User(last_name="A", role=Role.ADMIN)
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    for args in (None, "", "30000001", "30000001   "):
        msg = SimpleNamespace(answer=AsyncMock())
        await cmd_as(msg, principal=admin, session=session, bot=object(),
                     dispatcher=dispatcher, command=_cmd(args))
        msg.answer.assert_awaited_once_with(
            "Usage: /as <matriculation|telegram_id> <query>")
    dispatcher.propagate_event.assert_not_awaited()


async def test_cmd_as_not_found(session):
    admin = User(last_name="A", role=Role.ADMIN)
    msg = SimpleNamespace(answer=AsyncMock())
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    await cmd_as(msg, principal=admin, session=session, bot=object(),
                 dispatcher=dispatcher, command=_cmd("nope /me"))
    msg.answer.assert_awaited_once_with("No user found for nope.")
    dispatcher.propagate_event.assert_not_awaited()


async def test_cmd_as_success_refeeds_query(session):
    admin = User(last_name="A", role=Role.ADMIN)
    session.add(User(last_name="Ivanov", first_name="Ivan",
                     matriculation="30000001", telegram_id=111,
                     role=Role.STUDENT))
    session.commit()

    new_msg = SimpleNamespace()
    new_msg.as_ = lambda bot: new_msg
    msg = SimpleNamespace(
        answer=AsyncMock(),
        model_copy=lambda update: (setattr(new_msg, "update", update) or new_msg),
    )
    dispatcher = SimpleNamespace(propagate_event=AsyncMock())
    bot = object()

    await cmd_as(msg, principal=admin, session=session, bot=bot,
                 dispatcher=dispatcher, command=_cmd("30000001 /me"))

    msg.answer.assert_awaited_once_with("\U0001f464 Showing as Ivan Ivanov:")
    assert new_msg.update == {"text": "/me", "entities": None}
    dispatcher.propagate_event.assert_awaited_once()
    call = dispatcher.propagate_event.await_args
    assert call.args[0] == "message"
    assert call.kwargs["impersonate_ref"] == "30000001"
    assert call.kwargs["bot"] is bot
    assert call.kwargs["dispatcher"] is dispatcher
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_impersonate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'jbcub_bot.features.impersonate'`

- [ ] **Step 3: Write the handler**

Create `src/jbcub_bot/features/impersonate/handlers.py`:

```python
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from jbcub_bot.core import identity
from jbcub_bot.core.models import Role, User

router = Router(name="impersonate")

_USAGE = "Usage: /as <matriculation|telegram_id> <query>"


@router.message(Command("as"))
async def cmd_as(message: Message, principal: User, session, bot, dispatcher,
                 command: CommandObject):
    if principal is None or principal.role is not Role.ADMIN:
        await message.answer("Admins only.")
        return

    args = (command.args or "").strip()
    parts = args.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(_USAGE)
        return
    ref, query = parts[0], parts[1].strip()

    target = identity.find_impersonation_target(session, ref)
    if target is None:
        await message.answer(f"No user found for {ref}.")
        return

    await message.answer(f"\U0001f464 Showing as {target.full_name}:")
    new_msg = message.model_copy(
        update={"text": query, "entities": None}
    ).as_(bot)
    await dispatcher.propagate_event(
        "message", new_msg,
        bot=bot, dispatcher=dispatcher, impersonate_ref=ref,
    )
```

- [ ] **Step 4: Write the package exports**

Create `src/jbcub_bot/features/impersonate/__init__.py`:

```python
from jbcub_bot.core.loader import Manifest
from jbcub_bot.core.models import Role
from jbcub_bot.features.impersonate.handlers import router

manifest = Manifest(
    name="impersonate",
    commands=["as"],
    intents=[],
    min_role=Role.ADMIN,
    help_text="Admin: see the bot as a given user (/as <ref> <query>).",
)

__all__ = ["router", "manifest"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_impersonate.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/features/impersonate/ tests/test_impersonate.py
git commit -m "feat: add /as admin impersonation command"
```

---

### Task 4: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Confirm the feature is auto-discovered**

Run: `uv run pytest tests/test_bootstrap.py -v`
Expected: PASS (existing test still green; `build_dispatcher` discovers all feature routers including `impersonate`).

- [ ] **Step 2: Run the whole suite**

Run: `uv run pytest`
Expected: PASS — all tests green, no regressions.

- [ ] **Step 3: Commit (only if any doc/fixup changes were needed)**

```bash
git add -A
git commit -m "test: verify admin impersonation end-to-end"
```

(If there is nothing to commit, skip this step.)
