import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class Intent:
    name: str
    pattern: str
    handler: Callable


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
        if intent is None:
            return False
        await intent.handler(message, principal, session)
        return True
