"""What a legacy feature still declares as "plain text I might want".

**Phase F deletes this module**, together with `core/legacy.py`. It survives
this branch only because `kb` has not migrated: its manifest carries an
`Intent`, and the shim reads `pattern`, `min_role` and `handler` off it to
offer text around.

What used to live here as well -- `IntentRouter`, with `matches` and a
`dispatch` whose registration order was precedence -- is gone. Order is a
declaration now (`at=` in `core/contract.py`, resolved by `core/pipeline.py`),
and the walk that once belonged to `dispatch` is `LegacyIntents.offer` in
`core/legacy.py`, so there is one copy of it and it is the one production runs.
"""
from dataclasses import dataclass
from typing import Callable

from jbcub_bot.core.models import Role
from jbcub_bot.core.principal import role_rank


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
