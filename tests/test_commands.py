"""`core/commands.py`, which phase E was supposed to delete and could not.

The plan said "delete `core/registry.py`, `core/commands.py` with their tests".
`registry` went; `commands` could not, because `kb` is still legacy and blocks
it twice over: it declares `/ask` and `/kb_reload` through `CommandRegistrar`,
and `CommandSpec` is the element type of `Manifest.commands`, which
`core/legacy.py`'s bridge reads to put a legacy feature into /help. So `_guard`
below is live production code -- it runs on both of kb's commands -- and this
is its only test. Deleting it on the plan's word would have left live code
untested, which is the same defect as testing a copy nothing calls.

Phase F deletes the module, and this file with it. A *migrated* feature is
guarded once, by `core/guards.py`; `tests/test_guards.py` covers that, and
covers every wording and role combination this file does. What is only here is
what `CommandRegistrar` itself does: collect `.specs` for the manifest, and
keep `__wrapped__` so aiogram still sees the real signature.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
