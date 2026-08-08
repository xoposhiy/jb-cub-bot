"""Whether a declaration is open to the caller -- one decision for all of them.

A command, a chain handler, a button and a dialog all carry the same `Guard`,
so they all refuse the same way and with the same wording. `/help` filters on
exactly this and nothing else, which is why a guard here is also a visibility
rule: anything that should stay listed while it refuses (the bootstrap-admin
check, a profile with no saved row) is ordinary code inside its feature, not a
guard.
"""
from jbcub_bot.core.contract import Guard
from jbcub_bot.core.middleware import role_rank
from jbcub_bot.core.models import Role, User

ADMIN_REFUSAL = "Admins only."
STAFF_REFUSAL = "Staff only."
NOT_LINKED = "You are not linked yet. Contact an admin."

# Only these two ranks can refuse anyone: everybody outranks STUDENT, so a
# guard asking for it never reaches this table.
_ROLE_REFUSAL = {Role.ADMIN: ADMIN_REFUSAL, Role.TEACHER: STAFF_REFUSAL}


def refusal(guard: Guard, principal: User | None) -> str | None:
    """None when allowed; the wording to answer with when refused.

    The core answers it -- a message with the text, a callback with an alert --
    so a handler never sees a caller it should not have.
    """
    if principal is None:
        return None if guard.public else NOT_LINKED
    if guard.role is not None and role_rank(principal.role) < role_rank(guard.role):
        return _ROLE_REFUSAL[guard.role]
    return None


def allowed(guard: Guard, principal: User | None) -> bool:
    """The same decision without the wording -- this is what /help filters on."""
    return refusal(guard, principal) is None
