from aiogram import BaseMiddleware

from jbcub_bot.core import identity
from jbcub_bot.core.models import Role, User

_RANK = {Role.STUDENT: 0, Role.TEACHER: 1, Role.ADMIN: 2}


def role_rank(role: Role) -> int:
    return _RANK[role]


class HasRole:
    def __init__(self, min_role: Role):
        self.min_role = min_role

    def __call__(self, principal: User | None) -> bool:
        if principal is None:
            return False
        return role_rank(principal.role) >= role_rank(self.min_role)


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
            ref = data.get("impersonate_ref")
            if ref is not None and principal is not None \
                    and principal.role is Role.ADMIN:
                data["principal"] = identity.find_impersonation_target(
                    session, ref
                )
                data["impersonator"] = principal
            else:
                data["principal"] = principal
            return await handler(event, data)
        finally:
            session.close()
