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
        """Offer `text` to each matching intent until one takes it.

        A handler returning False declines -- it must not have answered -- and
        the turn goes to the next intent. Anything else (including None, so a
        handler that forgets to return cannot go silently unhandled) ends the
        walk.
        """
        for intent in self._intents:
            if not re.search(intent.pattern, text, re.IGNORECASE):
                continue
            if not intent_allowed(principal, intent):
                continue
            if await intent.handler(message, principal, session) is not False:
                return True
        return False
