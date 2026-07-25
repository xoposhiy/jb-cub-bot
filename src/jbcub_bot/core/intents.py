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
