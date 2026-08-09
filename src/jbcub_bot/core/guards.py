"""Whether a declaration is open to the caller -- one decision for all of them.

Commands, chain handlers, buttons and dialogs all carry the same `Guard`, so
they refuse alike, and `/help` filters on exactly this. A guard is therefore
also a visibility rule: anything that should stay listed while it refuses is
ordinary code inside its feature, not a guard.

`Guard()` is closed to strangers and only `public=True` opens one, so a
forgotten guard is fail-closed. That per-declaration check is also why no
middleware refuses an unlinked caller -- one would close `/start`, the only way
to get linked. On the chain the wording is thrown away and the handler is just
skipped, so `pipeline.last_word` is what tells a stranger they are one. See
AGENTS.md, "The chain is for people the bot knows".
"""
from jbcub_bot.core.contract import Guard
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.principal import role_rank

ADMIN_REFUSAL = "Admins only."
STAFF_REFUSAL = "Staff only."
NOT_LINKED = "You are not linked yet. Contact an admin."

# Only these two ranks can refuse anyone: everybody outranks STUDENT, so a
# guard asking for it never reaches this table.
_ROLE_REFUSAL = {Role.ADMIN: ADMIN_REFUSAL, Role.TEACHER: STAFF_REFUSAL}


def refusal(guard: Guard, principal: User | None) -> str | None:
    """None when allowed; the wording to answer with when refused.

    The core is what answers it, so a handler never sees a caller it should
    not have.
    """
    if principal is None:
        return None if guard.public else NOT_LINKED
    if guard.role is not None and role_rank(principal.role) < role_rank(guard.role):
        return _ROLE_REFUSAL[guard.role]
    return None


def allowed(guard: Guard, principal: User | None) -> bool:
    """The same decision without the wording -- this is what /help filters on."""
    return refusal(guard, principal) is None
