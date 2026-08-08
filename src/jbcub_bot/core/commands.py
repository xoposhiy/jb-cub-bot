"""The pre-contract way of declaring a command: register it and guard it here.

**Phase F deletes this module.** It was meant to go with `core/registry.py`,
but `kb` still declares `/ask` and `/kb_reload` through `CommandRegistrar` and
builds its `Manifest` out of `.specs`, and `core/legacy.py`'s bridge reads
`CommandSpec` off that manifest to republish it in /help. A migrated feature
uses `bot.command(...)` in `core/contract.py` and is guarded once, by
`core/guards.py`; nothing new may be added here.
"""
import functools
from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command

from jbcub_bot.core.models import Role, User
from jbcub_bot.core.principal import role_rank


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
