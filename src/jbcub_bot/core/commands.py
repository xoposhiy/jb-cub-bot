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
