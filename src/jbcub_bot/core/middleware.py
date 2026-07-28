from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from jbcub_bot.core import identity
from jbcub_bot.core.models import Role, User

_RANK = {Role.STUDENT: 0, Role.TEACHER: 1, Role.ADMIN: 2}

DEPARTED_NOTICE = (
    "The program roster no longer lists you, so the bot is closed to you.\n\n"
    "If that's a mistake, ask a program admin to check the roster."
)


def role_rank(role: Role) -> int:
    return _RANK[role]


class HasRole:
    def __init__(self, min_role: Role):
        self.min_role = min_role

    def __call__(self, principal: User | None) -> bool:
        if principal is None:
            return False
        return role_rank(principal.role) >= role_rank(self.min_role)


async def refuse_departed(event) -> None:
    """Tell the caller why nothing happened.

    An alert for a button press, a message otherwise: a toast under a tapped
    button scrolls away unread, and silence would look like the bot is broken
    rather than closed.
    """
    answer = getattr(event, "answer", None)
    if answer is None:
        return  # an event type with nothing to reply to
    if isinstance(event, CallbackQuery):
        await answer(DEPARTED_NOTICE, show_alert=True)
    else:
        await answer(DEPARTED_NOTICE)


class PrincipalMiddleware(BaseMiddleware):
    def __init__(self, session_factory, bootstrap_ids: set | None = None):
        self.session_factory = session_factory
        self.bootstrap_ids = bootstrap_ids or set()

    async def __call__(self, handler, event, data):
        session = self.session_factory()
        data["session"] = session
        try:
            user = getattr(event, "from_user", None)
            principal = None
            if user is not None:
                principal = identity.resolve(session, user.id, user.username)
                principal = identity.apply_bootstrap(
                    principal, user.id, user.username, self.bootstrap_ids
                )
                # Every entry point authenticates here, so this is the one place
                # that can close all of them at once. Bootstrap ids are exempt:
                # they are the way back in when the roster is wrong, and a bad
                # /sync must not be able to lock out the person who can fix it.
                if principal is not None and principal.departed_at \
                        and user.id not in self.bootstrap_ids:
                    await refuse_departed(event)
                    return None
            ref = data.get("impersonate_ref")
            if ref is not None and principal is not None \
                    and principal.role is Role.ADMIN:
                target = identity.find_impersonation_target(session, ref)
                # /as shows the bot as its target sees it, and what a departed
                # target sees is the refusal. Kept separate from the caller's
                # own check above so the bootstrap exemption covers only the
                # admin's own access, never their view of somebody else.
                if target is not None and target.departed_at:
                    await refuse_departed(event)
                    return None
                data["principal"] = target
                data["impersonator"] = principal
            else:
                data["principal"] = principal
            return await handler(event, data)
        finally:
            session.close()
