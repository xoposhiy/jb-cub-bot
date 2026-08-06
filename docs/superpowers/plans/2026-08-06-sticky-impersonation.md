# Sticky admin impersonation — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `/as` from a one-shot query wrapper into a mode an admin enters
and leaves with `/unas`, so the whole bot can be driven as a student sees it.

**Architecture:** A module-level `{admin telegram id → target ref}` map in
`core/impersonation.py` becomes the single source of truth. `PrincipalMiddleware`
reads it instead of today's three sources (passed data, a `callback_data`
marker, FSM data), so every update from that admin resolves to the target with
no context threaded through buttons. A second, message-only middleware prints a
banner before each answer. Because the real update now takes the normal
dispatch path, `FSMContext` is present and the no-state workarounds go away.

**Tech Stack:** Python 3.12, aiogram 3.30, SQLAlchemy, pytest (`uv run pytest`).

**Spec:** `docs/superpowers/specs/2026-08-06-sticky-impersonation-design.md` —
read it first.

## Global Constraints

- All user-facing bot text is in English.
- Every task ends green: `uv run pytest` passes before each commit.
- Feature packages export `router` + `manifest`; commands are registered
  through `CommandRegistrar` — with one deliberate exception, `/unas` (Task 3).
- Don't swallow unexpected exceptions in a handler; let `dp.errors` see them.
- Comments explain *why*, matching the density of the file being edited. A
  comment that this change makes false must be deleted, not left behind.
- Bash for multi-line commit messages: `git commit -F - <<'EOF'`.

## File Structure

| File | Change | Responsibility after the change |
|---|---|---|
| `src/jbcub_bot/core/impersonation.py` | Rewrite | The mode: who each admin is viewing as, the banner middleware, and the one exemption the exit command needs |
| `src/jbcub_bot/core/middleware.py` | Modify | Unchanged responsibility; reads one ref source instead of three |
| `src/jbcub_bot/features/impersonate/handlers.py` | Rewrite | `/as` enters the mode, `/unas` leaves it |
| `src/jbcub_bot/features/impersonate/__init__.py` | Modify | Manifest help text |
| `src/jbcub_bot/main.py` | Modify | Registers the banner middleware |
| `src/jbcub_bot/features/directory/{render,edit,privacy,handlers}.py` | Modify | Drop `impersonate_ref` threading; plain callback payloads |
| `src/jbcub_bot/features/kb/handlers.py` | Modify | Drop the no-FSM one-shot path |
| `tests/conftest.py` | Modify | Reset the mode between tests |
| `tests/test_impersonation_store.py` | Create | Unit tests for the store |
| `tests/test_impersonate.py`, `tests/test_impersonate_integration.py` | Rewrite | The mode, end to end |
| `tests/test_{middleware,departed_access,edit_handlers,privacy_handlers,directory_render,me_keyboard_integration,kb_handlers}.py` | Modify | Follow the source changes |
| `AGENTS.md` | Modify | One tripwire line |

---

### Task 1: The mode store

**Files:**
- Modify: `src/jbcub_bot/core/impersonation.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_impersonation_store.py` (create)

**Interfaces:**
- Consumes: `User` from `jbcub_bot.core.models`; the existing
  `canonical_ref(user) -> str` in this file stays exactly as it is.
- Produces: `begin(admin_id: int, ref: str) -> None`,
  `end(admin_id: int) -> str | None` (the ref that was active, or None),
  `ref_for(admin_id: int) -> str | None`, `reset() -> None`.
  `callback_data()` / `split_callback()` stay untouched until Task 5.

- [ ] **Step 1: Write the failing test**

Create `tests/test_impersonation_store.py`:

```python
"""Who each admin is currently viewing the bot as."""

from jbcub_bot.core import impersonation


def test_nobody_is_impersonating_by_default():
    assert impersonation.ref_for(777) is None


def test_begin_then_ref_for_returns_the_target():
    impersonation.begin(777, "30000001")
    assert impersonation.ref_for(777) == "30000001"


def test_one_admins_mode_does_not_leak_to_another():
    impersonation.begin(777, "30000001")
    assert impersonation.ref_for(778) is None


def test_end_returns_the_ref_and_clears_it():
    impersonation.begin(777, "30000001")
    assert impersonation.end(777) == "30000001"
    assert impersonation.ref_for(777) is None


def test_end_without_a_mode_is_not_an_error():
    assert impersonation.end(777) is None


def test_reset_clears_every_admin():
    impersonation.begin(777, "30000001")
    impersonation.begin(778, "30000002")
    impersonation.reset()
    assert impersonation.ref_for(777) is None
    assert impersonation.ref_for(778) is None
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_impersonation_store.py -v`
Expected: FAIL — `AttributeError: module 'jbcub_bot.core.impersonation' has no attribute 'ref_for'`.

- [ ] **Step 3: Add the store**

In `src/jbcub_bot/core/impersonation.py`, replace the module docstring and add
the store below the existing imports, keeping `canonical_ref`,
`callback_data` and `split_callback` exactly as they are:

```python
"""Which student an admin is currently viewing the bot as.

The mode is sticky: `/as` enters it and `/unas` leaves it, so every update in
between belongs to the target. Deliberately in memory and not on the admin's
row -- a deploy dropping someone back into their own view is the safe
direction, and the banner going missing says so. One process and one event
loop, so the map needs no locking.
"""

from jbcub_bot.core.models import User

_active: dict[int, str] = {}


def begin(admin_id: int, ref: str) -> None:
    """Start viewing as `ref` until `end`."""
    _active[admin_id] = ref


def end(admin_id: int) -> str | None:
    """Stop; returns the ref that was active, or None if there was none."""
    return _active.pop(admin_id, None)


def ref_for(admin_id: int) -> str | None:
    return _active.get(admin_id)


def reset() -> None:
    """Drop every active session. Tests only."""
    _active.clear()
```

- [ ] **Step 4: Stop the mode leaking between tests**

In `tests/conftest.py`, add an autouse fixture next to `_reset_kb_runtime` —
the map is module-level state, exactly like the kb runtime:

```python
@pytest.fixture(autouse=True)
def _reset_impersonation():
    from jbcub_bot.core import impersonation
    impersonation.reset()
    yield
    impersonation.reset()
```

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest`
Expected: PASS, including the six new tests. Nothing else reads the store yet.

- [ ] **Step 6: Commit**

```bash
git add src/jbcub_bot/core/impersonation.py tests/test_impersonation_store.py tests/conftest.py
git commit -F - <<'EOF'
feat: hold the impersonation target in a per-admin map

The map is what makes /as a mode instead of a one-shot: something has to
remember the target between updates. In memory on purpose -- see the spec.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: The middleware reads the store

**Files:**
- Modify: `src/jbcub_bot/core/middleware.py:136-143`
- Test: `tests/test_middleware.py`

**Interfaces:**
- Consumes: `impersonation.ref_for(admin_id)` from Task 1.
- Produces: nothing new. `data["principal"]`, `data["impersonator"]` and
  `data["impersonate_ref"]` keep their current meanings.

The store becomes the *first* source and the older ones stay for now, so
buttons drawn with a marker keep working until Task 5 removes them.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_middleware.py`:

```python
async def test_middleware_swaps_the_principal_for_an_admin_in_the_mode(session):
    from jbcub_bot.core.models import User
    session.add(User(last_name="Admin", telegram_id=777, role=Role.ADMIN))
    session.add(User(last_name="Stud", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()
    mw = PrincipalMiddleware(lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_tid"] = data["principal"].telegram_id
        captured["impersonator_tid"] = data["impersonator"].telegram_id

    impersonation.begin(777, "30000001")
    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="a"))
    await mw(handler, event, {})

    assert captured == {"principal_tid": 111, "impersonator_tid": 777}


async def test_a_students_own_mode_entry_is_ignored(session):
    # Belt and braces: only /as writes the map and only an admin may run it,
    # but the swap must not depend on that being true.
    from jbcub_bot.core.models import User
    session.add(User(last_name="Stud", telegram_id=777, role=Role.STUDENT))
    session.add(User(last_name="Other", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()
    mw = PrincipalMiddleware(lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_tid"] = data["principal"].telegram_id

    impersonation.begin(777, "30000001")
    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="s"))
    await mw(handler, event, {})

    assert captured["principal_tid"] == 777  # not swapped
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_middleware.py -v`
Expected: FAIL — the principal is still 777, because nothing reads the map.

- [ ] **Step 3: Read the map**

In `src/jbcub_bot/core/middleware.py`, insert the store lookup as the first
source, right after `ref = data.get("impersonate_ref")` (line 136):

```python
            ref = data.get("impersonate_ref")
            if ref is None and principal is not None \
                    and principal.role is Role.ADMIN:
                ref = impersonation.ref_for(user.id)
```

Leave the callback-marker and FSM lookups below it untouched.

- [ ] **Step 4: Run the suite**

Run: `uv run pytest`
Expected: PASS, new tests included.

- [ ] **Step 5: Commit**

```bash
git add src/jbcub_bot/core/middleware.py tests/test_middleware.py
git commit -F - <<'EOF'
feat: resolve the impersonation target from the mode map

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: `/as` enters the mode, `/unas` leaves it

**Files:**
- Rewrite: `src/jbcub_bot/features/impersonate/handlers.py`
- Modify: `src/jbcub_bot/features/impersonate/__init__.py:10`
- Modify: `src/jbcub_bot/core/impersonation.py` (add `is_exit_command`)
- Modify: `src/jbcub_bot/core/middleware.py`
- Rewrite: `tests/test_impersonate.py`, `tests/test_impersonate_integration.py`
- Modify: `tests/test_departed_access.py:145-172`,
  `tests/test_edit_handlers.py:376-464`, `tests/test_kb_handlers.py:283-305`

**Interfaces:**
- Consumes: `impersonation.begin/end/ref_for/canonical_ref`,
  `identity.find_impersonation_target(session, ref) -> User | None`.
- Produces: `impersonation.is_exit_command(event) -> bool`; the messages
  `_USAGE = "Usage: /as <matriculation|telegram_id>"` and
  `_NOT_IMPERSONATING = "You are not viewing as anyone."`; the confirmations
  quoted in Step 3, which later tasks' tests assert on verbatim.

**Write every new assertion against the *set* of texts** (`any(... for t in
texts)`), never `sent[1]` — Task 4 inserts a banner before each answer and
positional assertions would all shift.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_impersonate.py` entirely:

```python
"""/as and /unas: entering and leaving the mode."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import jbcub_bot.features.impersonate as impersonate
from jbcub_bot.core import impersonation
from jbcub_bot.core.models import Role, User
from jbcub_bot.features.impersonate.handlers import cmd_as, cmd_unas


def _cmd(args):
    return SimpleNamespace(args=args)


def _msg(telegram_id=777):
    return SimpleNamespace(answer=AsyncMock(),
                           from_user=SimpleNamespace(id=telegram_id))


def _state():
    return SimpleNamespace(clear=AsyncMock())


def test_manifest_exposes_as_but_not_unas():
    # /unas is deliberately off the registrar: see the docstring on cmd_unas.
    assert impersonate.manifest.name == "impersonate"
    names = {c.name for c in impersonate.manifest.commands}
    assert names == {"as"}


async def test_as_is_denied_for_a_non_admin(session):
    msg = _msg()
    await cmd_as(msg, principal=User(last_name="S", role=Role.STUDENT),
                 session=session, state=_state(), command=_cmd("30000001"))
    msg.answer.assert_awaited_once_with("Admins only.")
    assert impersonation.ref_for(777) is None


async def test_as_is_denied_for_an_unlinked_caller(session):
    msg = _msg()
    await cmd_as(msg, principal=None, session=session, state=_state(),
                 command=_cmd("30000001"))
    msg.answer.assert_awaited_once_with(
        "You are not linked yet. Contact an admin.")
    assert impersonation.ref_for(777) is None


async def test_as_without_a_reference_shows_usage(session):
    admin = User(last_name="A", role=Role.ADMIN)
    for args in (None, "", "   "):
        msg = _msg()
        await cmd_as(msg, principal=admin, session=session, state=_state(),
                     command=_cmd(args))
        msg.answer.assert_awaited_once_with(
            "Usage: /as <matriculation|telegram_id>")
    assert impersonation.ref_for(777) is None


async def test_as_with_an_unknown_reference_starts_nothing(session):
    msg = _msg()
    await cmd_as(msg, principal=User(last_name="A", role=Role.ADMIN),
                 session=session, state=_state(), command=_cmd("nope"))
    msg.answer.assert_awaited_once_with("No user found for nope.")
    assert impersonation.ref_for(777) is None


async def test_as_enters_the_mode_and_clears_the_state(session):
    session.add(User(last_name="Ivanov", first_name="Ivan",
                     matriculation="30000001", telegram_id=111,
                     role=Role.STUDENT))
    session.commit()
    msg, state = _msg(), _state()

    await cmd_as(msg, principal=User(last_name="A", role=Role.ADMIN),
                 session=session, state=state, command=_cmd("30000001"))

    assert impersonation.ref_for(777) == "30000001"
    state.clear.assert_awaited_once()
    said = msg.answer.await_args.args[0]
    assert "Ivan Ivanov" in said
    assert "/unas" in said


async def test_as_stores_the_canonical_ref_not_what_was_typed(session):
    # Typed as a telegram id, stored as the matriculation, so the ref outlives
    # a rebinding of the target's telegram account.
    session.add(User(last_name="Ivanov", first_name="Ivan",
                     matriculation="30000001", telegram_id=111,
                     role=Role.STUDENT))
    session.commit()

    await cmd_as(_msg(), principal=User(last_name="A", role=Role.ADMIN),
                 session=session, state=_state(), command=_cmd("111"))

    assert impersonation.ref_for(777) == "30000001"


async def test_unas_leaves_the_mode(session):
    impersonation.begin(777, "30000001")
    msg, state = _msg(), _state()

    await cmd_unas(msg, state=state)

    assert impersonation.ref_for(777) is None
    state.clear.assert_awaited_once()
    assert "own view" in msg.answer.await_args.args[0]


async def test_unas_outside_the_mode_says_so(session):
    msg = _msg()
    await cmd_unas(msg, state=_state())
    msg.answer.assert_awaited_once_with("You are not viewing as anyone.")
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_impersonate.py -v`
Expected: FAIL — `ImportError: cannot import name 'cmd_unas'`.

- [ ] **Step 3: Rewrite the handlers**

Replace `src/jbcub_bot/features/impersonate/handlers.py` with:

```python
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from jbcub_bot.core import identity, impersonation
from jbcub_bot.core.commands import CommandRegistrar
from jbcub_bot.core.models import Role, User

router = Router(name="impersonate")
cmd = CommandRegistrar(router)

_USAGE = "Usage: /as <matriculation|telegram_id>"
_NOT_IMPERSONATING = "You are not viewing as anyone."


@cmd.command("as", "See the bot as another user, until /unas.",
             min_role=Role.ADMIN, usage="<ref>")
async def cmd_as(message: Message, principal: User, session,
                 state: FSMContext, command: CommandObject):
    """Enter the mode. Every later update from this admin belongs to the target.

    Inside the mode the principal is the target, so this command refuses like
    any other admin command -- switching target is /unas, then /as again.
    """
    ref = (command.args or "").strip()
    if not ref:
        await message.answer(_USAGE)
        return
    target = identity.find_impersonation_target(session, ref)
    if target is None:
        await message.answer(f"No user found for {ref}.")
        return
    # Whatever half-finished dialog the admin was in is theirs, not the
    # target's: it must not carry over into the view they are about to get.
    await state.clear()
    impersonation.begin(message.from_user.id,
                        impersonation.canonical_ref(target))
    await message.answer(
        f"\U0001f464 You are now seeing the bot as {target.full_name}.\n"
        "Send /unas to return to your own view."
    )


@router.message(Command("unas"))
async def cmd_unas(message: Message, state: FSMContext):
    """Leave the mode.

    Registered straight on the router rather than through CommandRegistrar for
    two reasons: inside the mode the principal is a student, so a role-guarded
    command would refuse the one command that gets you out; and listing it in
    /help would put a command in the student's view that no student has. Every
    banner prints it instead.

    It reads the map rather than `impersonator` because the middleware
    deliberately does not impersonate this command -- see `is_exit_command`.
    """
    if impersonation.end(message.from_user.id) is None:
        await message.answer(_NOT_IMPERSONATING)
        return
    await state.clear()
    await message.answer("↩️ Back to your own view.")
```

- [ ] **Step 4: Keep the exit reachable when the target is departed**

A departed target is refused in `PrincipalMiddleware` *before* any handler
runs. Without an exemption that refusal would cover `/unas` too, and the admin
would be stuck in it until a restart.

Add to `src/jbcub_bot/core/impersonation.py`:

```python
_EXIT_COMMAND = "/unas"


def is_exit_command(event) -> bool:
    """True for the message that leaves the mode, which is never impersonated.

    A departed target is refused before any handler runs, so if that refusal
    covered /unas as well, `/as <departed student>` would be a trap with no way
    out short of a restart.
    """
    text = getattr(event, "text", None) or ""
    head = text.split(maxsplit=1)[0] if text.split() else ""
    return head.split("@")[0] == _EXIT_COMMAND
```

And in `src/jbcub_bot/core/middleware.py`, guard the store lookup added in
Task 2:

```python
            ref = data.get("impersonate_ref")
            if ref is None and principal is not None \
                    and principal.role is Role.ADMIN \
                    and not impersonation.is_exit_command(event):
                ref = impersonation.ref_for(user.id)
```

- [ ] **Step 5: Update the manifest help text**

In `src/jbcub_bot/features/impersonate/__init__.py`, line 10:

```python
    help_text="Admin: see the bot as a given user (/as <ref>, /unas to return).",
```

- [ ] **Step 6: Rewrite the end-to-end test**

Replace the docstring and test in `tests/test_impersonate_integration.py`,
keeping `FakeBot`, `_build_session_factory` and `_make_message` as they are.
Give `_make_message` an id so several updates can be fed:

```python
def _make_message(fake_bot, telegram_id: int, text: str,
                  message_id: int = 1) -> Message:
    chat = Chat(id=telegram_id, type="private")
    tg_user = TgUser(id=telegram_id, is_bot=False, first_name="tg")
    return Message(
        message_id=message_id,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=tg_user,
        text=text,
    ).as_(fake_bot)
```

Then, replacing `test_as_command_reaches_real_handler_as_target_student`:

```python
def _feed(dp, fake_bot, telegram_id, text, update_id):
    msg = _make_message(fake_bot, telegram_id, text, message_id=update_id)
    return dp.feed_update(fake_bot, Update(update_id=update_id,
                                           message=msg).as_(fake_bot))


async def test_the_mode_lasts_across_messages_and_then_ends():
    session_factory = _build_session_factory()
    setup = session_factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT, primary_cohort="cohort-x"))
    setup.commit()
    setup.close()

    dp = build_dispatcher(session_factory=session_factory)
    fake_bot = FakeBot()

    await _feed(dp, fake_bot, 777, "/as 30009999", 1)
    await _feed(dp, fake_bot, 777, "/me", 2)
    texts = [m.text for m in fake_bot.sent]
    # The key assertion: cmd_me really ran with the principal swapped, one
    # whole update after the command that swapped it.
    assert any("Zakhar Zhukovsky" in t and "Cohort: cohort-x" in t
               for t in texts)

    # Still in the mode two updates later -- this is what "sticky" means.
    await _feed(dp, fake_bot, 777, "/me", 3)
    assert sum("Cohort: cohort-x" in t for t in
               [m.text for m in fake_bot.sent]) == 2

    await _feed(dp, fake_bot, 777, "/unas", 4)
    await _feed(dp, fake_bot, 777, "/me", 5)
    assert "Anna Adminova" in [m.text for m in fake_bot.sent][-1]


async def test_an_admin_command_is_refused_inside_the_mode():
    session_factory = _build_session_factory()
    setup = session_factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT))
    setup.commit()
    setup.close()

    dp = build_dispatcher(session_factory=session_factory)
    fake_bot = FakeBot()

    await _feed(dp, fake_bot, 777, "/as 30009999", 1)
    await _feed(dp, fake_bot, 777, "/help", 2)
    await _feed(dp, fake_bot, 777, "/as 30009999", 3)

    texts = [m.text for m in fake_bot.sent]
    assert "Admins only." in texts        # /as itself, inside the mode
    help_text = next(t for t in texts if "/me" in t)
    assert "/sync" not in help_text       # the student's /help, not the admin's
```

Note `_feed` drops the `dispatcher=dp` kwarg the old test passed: no handler
needs the dispatcher any more.

- [ ] **Step 7: Run and watch these fail, then pass**

Run: `uv run pytest tests/test_impersonate.py tests/test_impersonate_integration.py -v`
Expected: PASS once Steps 3-6 are in. If `/help` still lists `/sync`, the
`principal` in `data` was not swapped — check Task 2's lookup.

- [ ] **Step 8: Update the three suites that used the one-shot form**

Run: `uv run pytest`
Expected: FAIL in `test_departed_access.py`, `test_edit_handlers.py`,
`test_kb_handlers.py` — each feeds `/as <ref> <query>`, which is now a usage
error. Fix each by feeding the command and the query as two updates:

In `tests/test_departed_access.py`, `_message_update` hardcodes `update_id=1`;
that is fine, `feed_update` does not deduplicate. Replace the two `/as` tests
and add the trap regression:

```python
async def test_impersonating_a_departed_student_shows_their_block():
    # /as exists to see the bot as someone else sees it, and what a departed
    # student sees is the refusal. Rendering their profile instead would tell
    # an admin their access still works.
    factory = _session_factory()
    _seed(factory)
    await _admin(factory)
    bot, dp = FakeBot(), build_dispatcher(factory)
    await dp.feed_update(bot, _message_update(bot, 999, f"/as {DEPARTED_TID}"))
    await dp.feed_update(bot, _message_update(bot, 999, "/me"))
    said = _texts(bot)
    assert DEPARTED_NOTICE in said
    assert not any("Cohort: 2024" in text for text in said)


async def test_impersonating_a_student_still_on_the_roster_works():
    # The guard above must not break /as for everyone else.
    factory = _session_factory()
    _seed(factory)
    await _admin(factory)
    bot, dp = FakeBot(), build_dispatcher(factory)
    await dp.feed_update(bot, _message_update(bot, 999, f"/as {ACTIVE_TID}"))
    await dp.feed_update(bot, _message_update(bot, 999, "/me"))
    said = _texts(bot)
    assert DEPARTED_NOTICE not in said
    assert any("Ivan" in text for text in said)


async def test_the_exit_is_not_swallowed_by_a_departed_targets_refusal():
    # Regression: the refusal runs before any handler, so without an exemption
    # /unas would be refused too and the admin would be stuck until a restart.
    factory = _session_factory()
    _seed(factory)
    await _admin(factory)
    bot, dp = FakeBot(), build_dispatcher(factory)
    await dp.feed_update(bot, _message_update(bot, 999, f"/as {DEPARTED_TID}"))
    await dp.feed_update(bot, _message_update(bot, 999, "/unas"))
    assert "Back to your own view." in _texts(bot)[-1]
```

In `tests/test_kb_handlers.py`, replace both one-shot tests — the mode now has
an FSM, so the point to prove is the opposite one:

```python
async def test_the_knowledge_base_answers_inside_the_mode(monkeypatch):
    dp, bot, _, asked = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, ADMIN_ID, f"/as {STUDENT_ID}"),
                         dispatcher=dp)
    await dp.feed_update(
        bot, _message(bot, ADMIN_ID, "/ask how many retakes?", update_id=2),
        dispatcher=dp)

    assert asked == ["how many retakes?"]
    assert any("Policies for Bachelor Studies" in t for t in _texts(bot))


async def test_a_session_inside_the_mode_survives_the_next_message(monkeypatch):
    # The one-shot /as had no FSMContext, so a second question was impossible.
    dp, bot, _, asked = _setup(monkeypatch)

    await dp.feed_update(bot, _message(bot, ADMIN_ID, f"/as {STUDENT_ID}"),
                         dispatcher=dp)
    await dp.feed_update(bot, _message(bot, ADMIN_ID, "/ask", update_id=2),
                         dispatcher=dp)
    await dp.feed_update(bot, _message(bot, ADMIN_ID, "how many retakes?",
                                       update_id=3), dispatcher=dp)

    assert asked == ["how many retakes?"]
```

In `tests/test_edit_handlers.py`, the three impersonation tests each start with
`/as <ref> <query>`. Split the entry in every one of them, e.g.

```python
async def test_a_search_under_impersonation_still_reaches_the_fallback():
    factory = _session_factory()
    _seed_admin_and_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot, _message_update(fake_bot, 777,
                                                   "/as 30009999"),
                         dispatcher=dp)
    await dp.feed_update(fake_bot, _message_update(fake_bot, 777, "Zhukovsky",
                                                   update_id=2),
                         dispatcher=dp)

    assert any("Zakhar Zhukovsky" in getattr(m, "text", "")
               for m in fake_bot.sent)
```

Delete `test_cancel_under_impersonation_does_not_crash` outright: it guards the
no-state path, which Task 6 removes. Task 6 adds the replacement.

For `test_edit_under_impersonation_updates_the_target`, split the entry the
same way and replace the positional `fake_bot.sent[1]` with a search:

```python
    shown = next(m for m in fake_bot.sent
                 if "target status" in getattr(m, "text", ""))
```

Leave its `impersonation.callback_data(...)` assertions alone — the markers are
still there until Task 5.

- [ ] **Step 9: Run the whole suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -F - <<'EOF'
feat!: /as is a mode you enter, /unas leaves it

The one-shot form faked a Message and re-entered through propagate_event,
which skipped the outer middlewares -- so no screen that needs an FSM
worked under it. The mode carries no update of its own, so the real one
takes the normal path.

/unas is not impersonated: a departed target is refused before any
handler runs, and that refusal would otherwise cover the way out.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 4: The banner

**Files:**
- Modify: `src/jbcub_bot/core/impersonation.py`
- Modify: `src/jbcub_bot/main.py:62-63`
- Test: `tests/test_impersonate_integration.py`

**Interfaces:**
- Consumes: `data["impersonator"]` and `data["principal"]`, both set by
  `PrincipalMiddleware`.
- Produces: `impersonation.BannerMiddleware` (an `aiogram.BaseMiddleware`) and
  the text `👤 Viewing as <full name> · /unas to return`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_impersonate_integration.py` (reusing `_feed` from Task 3):

```python
async def test_every_answer_inside_the_mode_is_announced():
    session_factory = _build_session_factory()
    setup = session_factory()
    setup.add(User(last_name="Adminova", first_name="Anna",
                   telegram_id=777, role=Role.ADMIN))
    setup.add(User(last_name="Zhukovsky", first_name="Zakhar",
                   matriculation="30009999", telegram_id=222,
                   role=Role.STUDENT))
    setup.commit()
    setup.close()

    dp = build_dispatcher(session_factory=session_factory)
    fake_bot = FakeBot()
    banner = "\U0001f464 Viewing as Zakhar Zhukovsky · /unas to return"

    await _feed(dp, fake_bot, 777, "/as 30009999", 1)
    # Entering is not itself impersonated, so it gets no banner -- the
    # confirmation already says who you have become.
    assert banner not in [m.text for m in fake_bot.sent]

    await _feed(dp, fake_bot, 777, "/me", 2)
    await _feed(dp, fake_bot, 777, "/me", 3)
    texts = [m.text for m in fake_bot.sent]
    assert texts.count(banner) == 2
    # It comes first, so the answer below it is already labelled. Match the
    # profile by a line only it has: the banner and the /as confirmation both
    # carry the student's name.
    assert texts.index(banner) < texts.index(
        next(t for t in texts if "Role: Student" in t))

    await _feed(dp, fake_bot, 777, "/unas", 4)
    await _feed(dp, fake_bot, 777, "/me", 5)
    assert [m.text for m in fake_bot.sent].count(banner) == 2  # no more
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_impersonate_integration.py -v`
Expected: FAIL — `assert 0 == 2`, no banner is sent.

- [ ] **Step 3: Write the middleware**

Append to `src/jbcub_bot/core/impersonation.py`:

```python
class BannerMiddleware(BaseMiddleware):
    """Say whose eyes these are, before the answer they belong to.

    Messages only. A button usually edits its own message in place, so a
    banner per tap would push the screen it just redrew off the top.

    It needs no exceptions: /unas arrives unimpersonated (see
    `is_exit_command`) and so announces nothing, and a /as refused inside the
    mode is refused *because* of the mode, which is worth saying.
    """

    async def __call__(self, handler, event, data):
        target = data.get("principal")
        if data.get("impersonator") is not None and target is not None:
            await event.answer(BANNER.format(name=target.full_name))
        return await handler(event, data)
```

with, near the top of the file:

```python
from aiogram import BaseMiddleware
```

and, next to `_EXIT_COMMAND`:

```python
BANNER = "\U0001f464 Viewing as {name} · /unas to return"
```

- [ ] **Step 4: Register it**

In `src/jbcub_bot/main.py`, after the two `PrincipalMiddleware` registrations:

```python
    dp.message.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))
    dp.callback_query.middleware(PrincipalMiddleware(session_factory, bootstrap_ids))
    # After PrincipalMiddleware, which is what puts the impersonator in `data`.
    # Inner middleware, like the one above: aiogram resolves the parent chain's
    # inner middlewares for a sub-router's handler, and runs them once, for the
    # handler that actually matched.
    dp.message.middleware(impersonation.BannerMiddleware())
```

Add `impersonation` to the existing `from jbcub_bot.core import ...` import.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest`
Expected: PASS. If other suites now fail on a message count, they are asserting
positionally — rewrite those assertions as membership checks rather than
adjusting indices.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -F - <<'EOF'
feat: announce whose view this is before each answer

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 5: Delete the callback marker

**Files:**
- Modify: `src/jbcub_bot/core/impersonation.py` (drop `callback_data`,
  `split_callback`, `_CALLBACK_MARKER`)
- Modify: `src/jbcub_bot/core/middleware.py:136-143`
- Modify: `src/jbcub_bot/features/directory/render.py:149-170`,
  `handlers.py:206-214`, `privacy.py`, `edit.py`
- Test: `tests/test_middleware.py`, `tests/test_directory_render.py`,
  `tests/test_me_keyboard_integration.py`, `tests/test_edit_handlers.py`,
  `tests/test_privacy_handlers.py`

**Interfaces:**
- Produces: `me_keyboard(user) -> InlineKeyboardMarkup | None`,
  `edit_keyboard(user)`, `prompt_keyboard(spec)`, `clear_confirm_keyboard(spec)`,
  `privacy_keyboard(user)` — all with the `impersonate_ref` parameter gone.
  Callback payloads are the bare constants again (`dir:edit`, `dir:privacy`,
  `dir:edit:f:<field>`, `dir:vis:<field>`, …).

The mode is server-side now, so a button no longer has to carry its target.

- [ ] **Step 1: Update the tests first**

- `tests/test_middleware.py`: delete
  `test_middleware_reads_impersonation_from_admin_callback` and
  `test_non_admin_cannot_forge_an_impersonated_callback` (both exercise the
  marker), and the two older `{"impersonate_ref": ...}` tests
  `test_middleware_impersonation_swaps_for_admin` and
  `test_middleware_impersonation_ignored_for_non_admin` — Task 2's two tests
  already cover the swap and the non-admin case through the map.
- `tests/test_directory_render.py`: in
  `test_me_keyboard_targets_self_service_during_impersonation`, drop the
  `impersonate_ref=` argument and expect the bare `EDIT_CALLBACK` /
  `PRIVACY_CALLBACK`; rename it to
  `test_me_keyboard_offers_the_self_service_buttons`. Drop the
  `from jbcub_bot.core import impersonation` import.
- `tests/test_me_keyboard_integration.py`, `tests/test_edit_handlers.py`,
  `tests/test_privacy_handlers.py`: replace every
  `impersonation.callback_data("X", "30009999")` with plain `"X"`, and drop the
  now-unused imports.

- [ ] **Step 2: Run and watch them fail**

Run: `uv run pytest tests/test_edit_handlers.py tests/test_privacy_handlers.py -v`
Expected: FAIL — the keyboards still render `dir:edit|as:30009999`, so the
bare payload is not found among the buttons.

- [ ] **Step 3: Strip the threading from the keyboards**

In `src/jbcub_bot/features/directory/render.py`: drop `impersonation` from the
`from jbcub_bot.core import ...` import, and reduce `me_keyboard` to

```python
def me_keyboard(user: User) -> InlineKeyboardMarkup | None:
    """Keyboard for a user's own profile."""
    rows = []
    rows.append([
        InlineKeyboardButton(text="✏️ Edit my profile",
                             callback_data=EDIT_CALLBACK),
        InlineKeyboardButton(text="\U0001f512 Who sees my data",
                             callback_data=PRIVACY_CALLBACK),
    ])
```

leaving the admin rows below it untouched. Its old docstring paragraph about
impersonated buttons carrying their target is now false — delete it.

In `handlers.py`, `cmd_me` loses the parameter:

```python
@cmd.command("me", "Show your own profile.")
async def cmd_me(message: Message, principal: User, session):
    text = render_profile(principal, principal)
    await message.answer(
        text,
        reply_markup=me_keyboard(principal),
        entities=profile_entities(principal, principal, text),
    )
```

In `privacy.py`: drop the `impersonation` import, add `F` to the aiogram
import, drop the parameter from `privacy_keyboard`, `cmd_privacy`,
`_show_privacy`, `cb_open`, `cb_back` and `cb_cycle`, build every
`callback_data=` from the bare constant, and turn the three filters into

```python
@router.callback_query(F.data == PRIVACY_CALLBACK)
@router.callback_query(F.data == PROFILE_CALLBACK)
@router.callback_query(F.data.startswith(FIELD_CALLBACK_PREFIX))
```

with `cb_cycle` reading `name = cb.data[len(FIELD_CALLBACK_PREFIX):]`. The
`me_keyboard(principal, impersonate_ref=...)` call in `cb_back` becomes
`me_keyboard(principal)`.

In `edit.py`: the same treatment for `edit_keyboard`, `prompt_keyboard`,
`clear_confirm_keyboard`, `cmd_edit`, `cmd_cancel`, `_show_screen`, `cb_open`,
`cb_cancel`, `cb_field`, `cb_clear` and `cb_clear_do`; filters become
`F.data == EDIT_CALLBACK`, `F.data == CANCEL_CALLBACK`,
`F.data.startswith(FIELD_CALLBACK_PREFIX)` and so on, with each handler
slicing `cb.data` directly. Also drop `impersonate_ref=impersonate_ref` from
the `state.update_data(...)` call in `cb_field`, and the
`data.get("impersonate_ref")` arguments in `on_value` and `_reprompt`.

- [ ] **Step 4: Delete the marker helpers**

From `src/jbcub_bot/core/impersonation.py`, remove `_CALLBACK_MARKER`,
`callback_data` and `split_callback`. From `middleware.py`, remove the
callback-marker and FSM lookups, leaving one source:

```python
            ref = None
            if principal is not None and principal.role is Role.ADMIN \
                    and not impersonation.is_exit_command(event):
                ref = impersonation.ref_for(user.id)
```

Delete the now-unused `CallbackQuery` import from `middleware.py` only if
nothing else there uses it — `_chat_of` and `_refuse` still do, so it stays.

- [ ] **Step 5: Prove nothing is left behind**

Run (bash): `grep -rn "impersonate_ref\|split_callback\|callback_data(" src/ tests/`
Expected: only `callback_data=` keyword arguments on `InlineKeyboardButton`.
Any surviving `impersonate_ref` is a miss.

- [ ] **Step 6: Run the suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -F - <<'EOF'
refactor: stop carrying the impersonation target in buttons

A sticky mode is server-side, so a button no longer has to name whose
screen it belongs to -- and callback payloads go back to being what the
handler reads.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 6: Delete the no-FSM workarounds

**Files:**
- Modify: `src/jbcub_bot/features/directory/edit.py` (`cmd_edit`, `cmd_cancel`)
- Modify: `src/jbcub_bot/features/kb/handlers.py:406-424, 539-565`
- Modify: `AGENTS.md`
- Test: `tests/test_edit_handlers.py`

**Interfaces:**
- Produces: `cmd_edit(message, principal, session, state: FSMContext)` and
  `cmd_cancel(message, principal, session, state: FSMContext)` — `state` is
  required, not `None`-defaulted; `cmd_ask(message, principal, session, bot,
  command, state: FSMContext)` likewise. `_answer_one_shot` no longer exists.

Every update now reaches these handlers through the normal dispatch path, so
there is always an `FSMContext`.

- [ ] **Step 1: Write the failing test**

This is the capability the whole change buys — a multi-turn edit under
impersonation. Add to `tests/test_edit_handlers.py`, replacing the deleted
`test_cancel_under_impersonation_does_not_crash`:

```python
async def test_cancel_inside_the_mode_cancels_the_targets_edit():
    # The one-shot /as had no FSMContext, so /cancel could only ever answer
    # "Nothing to cancel." Inside the mode there is a real dialog to cancel.
    factory = _session_factory()
    _seed_admin_and_student(factory)
    dp = build_dispatcher(session_factory=factory)
    fake_bot = FakeBot()

    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/as 30009999"),
                         dispatcher=dp)
    await dp.feed_update(fake_bot,
                         _callback_update(fake_bot, 777, "dir:edit",
                                          update_id=2),
                         dispatcher=dp)
    await dp.feed_update(
        fake_bot,
        _callback_update(fake_bot, 777,
                         f"{edit.FIELD_CALLBACK_PREFIX}status_line",
                         update_id=3),
        dispatcher=dp)
    await dp.feed_update(fake_bot,
                         _message_update(fake_bot, 777, "/cancel",
                                         update_id=4),
                         dispatcher=dp)

    assert "Editing cancelled." in _edits(fake_bot)[-1].text
    assert _stored(factory, "status_line") == "target status"  # untouched
```

- [ ] **Step 2: Run it**

Run: `uv run pytest tests/test_edit_handlers.py::test_cancel_inside_the_mode_cancels_the_targets_edit -v`
Expected: PASS. Not a TDD failure and not a mistake — Task 3 already made this
work by routing the update normally. The test is here to pin the capability
down before Step 3 deletes the branches that used to stand in for it, and to
catch it if a later change quietly takes the FSM away again.

- [ ] **Step 3: Require the state**

In `src/jbcub_bot/features/directory/edit.py`:

```python
@cmd.command("edit", "Edit your status, GitHub or Codeforces.")
async def cmd_edit(message: Message, principal: User, session,
                   state: FSMContext):
    await state.clear()
    await message.answer(render_edit(principal),
                         reply_markup=edit_keyboard(principal))


@cmd.command("cancel", "Stop editing a profile field.")
async def cmd_cancel(message: Message, principal: User, session,
                     state: FSMContext):
    data = await state.get_data()
    # Only this feature's own state: another feature may be waiting for text,
    # and clearing that would end its session while showing an edit screen.
    if await state.get_state() != EditProfile.value.state:
        await message.answer(_NOTHING_TO_CANCEL)
        return
    await state.clear()
    await _redraw(message, data, render_edit(principal, _CANCELLED),
                  edit_keyboard(principal))
```

Both comments about `/as` reaching the handler without middlewares go with the
code they described.

In `src/jbcub_bot/features/kb/handlers.py`, `cmd_ask` loses its no-state
branch and its comment:

```python
@cmd.command("ask", "Ask the knowledge base a question.", usage="[question]")
async def cmd_ask(message: Message, principal: User, session, bot: Bot,
                  command: CommandObject, state: FSMContext):
    if runtime() is None:
        await message.answer(_NOT_CONFIGURED)
        return
    question = (command.args or "").strip()
    await _open(state, bot, message.chat.id)
    if question:
        await _answer_question(message, principal, state, bot, question,
                               message.from_user)
    else:
        await _greet(message, state)
```

Delete `_answer_one_shot` (lines 539-565) entirely — `cmd_ask` was its only
caller.

- [ ] **Step 4: Check nothing else called them**

Run (bash): `grep -rn "_answer_one_shot\|state is None" src/ tests/`
Expected: no matches.

- [ ] **Step 5: Run the suite**

Run: `uv run pytest`
Expected: PASS.

- [ ] **Step 6: Record the tripwire**

In `AGENTS.md`, under "Conventions that aren't obvious", after the
`PrincipalMiddleware` bullet:

```markdown
- **`/as` is a sticky mode, not a wrapper.** While an admin is in it,
  `principal` *is* the student and every admin command refuses; the real
  admin is `impersonator`. The map lives in memory in `core/impersonation.py`,
  so `/unas` must never be impersonated or a departed target traps its viewer.
```

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -F - <<'EOF'
refactor: drop the no-FSM paths the one-shot /as needed

Every update now reaches these handlers through the normal dispatch
path, so there is always an FSMContext -- and a multi-turn dialog under
impersonation works for the first time.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
EOF
```

---

## Manual check before calling it done

`uv run python -m jbcub_bot` with a real `.env`, from an admin account:

1. `/as <matriculation>` → confirmation naming the student.
2. `/me` → the student's profile, under a banner. Tap **Edit my profile** →
   **Status** → type a value → it saves onto the *student's* row.
3. `/help` → no admin section. `/sync` → "Admins only."
4. `/ask` → a session; ask two questions in a row; both answer.
5. `/unas` → "Back to your own view." `/me` → your own profile, no banner.
