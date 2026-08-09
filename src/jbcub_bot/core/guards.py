"""Whether a declaration is open to the caller -- one decision for all of them.

A command, a chain handler, a button and a dialog all carry the same `Guard`,
so they all refuse the same way and with the same wording. `/help` filters on
exactly this and nothing else, which is why a guard here is also a visibility
rule: anything that should stay listed while it refuses (the bootstrap-admin
check, a profile with no saved row) is ordinary code inside its feature, not a
guard.

One kind refuses quietly: on the chain the wording is thrown away and the
handler is simply skipped, because nothing was addressed to it. So `NOT_LINKED`
for a stranger who just types something comes from `pipeline.last_word`
instead, and no feature needs to be `public` in order to say it.

Note what the default does. `Guard()` is `public=False`, and `refusal` turns
away a `None` principal for it -- so a declaration with no guard at all is
closed to strangers, and only an explicit `public=True` opens one. That is why
there is no second, earlier check for "not linked": an unlinked caller has to
reach `/start` somehow, and the declaration is the only thing that knows
whether this is it.
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
