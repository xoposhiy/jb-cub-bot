from types import SimpleNamespace

from jbcub_bot.core.middleware import HasRole, PrincipalMiddleware, role_rank
from jbcub_bot.core.models import Role, User


def test_role_rank_ordering():
    assert role_rank(Role.STUDENT) < role_rank(Role.TEACHER) < role_rank(Role.ADMIN)


def test_has_role_allows_equal_or_higher():
    guard = HasRole(Role.ADMIN)
    assert guard(User(role=Role.ADMIN)) is True
    assert guard(User(role=Role.STUDENT)) is False


def test_has_role_none_principal_denied():
    assert HasRole(Role.STUDENT)(None) is False


async def test_middleware_injects_principal(session):
    session.add(User(last_name="Ivan", telegram_id=777, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["tid"] = data["principal"].telegram_id

    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="ivan"))
    await mw(handler, event, {})
    assert captured["tid"] == 777


async def test_middleware_bootstrap_admin(session):
    mw = PrincipalMiddleware(session_factory=lambda: session,
                             bootstrap_ids={4242})
    captured = {}

    async def handler(event, data):
        captured["principal"] = data["principal"]

    event = SimpleNamespace(from_user=SimpleNamespace(id=4242, username="boss"))
    await mw(handler, event, {})
    assert captured["principal"].role is Role.ADMIN


async def test_middleware_impersonation_swaps_for_admin(session):
    from jbcub_bot.core.models import User
    session.add(User(last_name="Admin", telegram_id=777, role=Role.ADMIN))
    session.add(User(last_name="Stud", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_matriculation"] = data["principal"].matriculation
        captured["impersonator_tid"] = data.get("impersonator").telegram_id

    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="a"))
    await mw(handler, event, {"impersonate_ref": "30000001"})
    assert captured["principal_matriculation"] == "30000001"
    assert captured["impersonator_tid"] == 777


async def test_middleware_impersonation_ignored_for_non_admin(session):
    from jbcub_bot.core.models import User
    session.add(User(last_name="Stud", telegram_id=777, role=Role.STUDENT))
    session.add(User(last_name="Other", matriculation="30000001",
                     telegram_id=111, role=Role.STUDENT))
    session.commit()

    mw = PrincipalMiddleware(session_factory=lambda: session)
    captured = {}

    async def handler(event, data):
        captured["principal_tid"] = data["principal"].telegram_id

    event = SimpleNamespace(from_user=SimpleNamespace(id=777, username="s"))
    await mw(handler, event, {"impersonate_ref": "30000001"})
    assert captured["principal_tid"] == 777  # not swapped
