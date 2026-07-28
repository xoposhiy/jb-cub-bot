from jbcub_bot.core import identity
from jbcub_bot.core.models import User


def _add(session, **kw):
    u = User(last_name=kw.pop("name", "X"), **kw)
    session.add(u)
    session.commit()
    return u


def test_resolve_by_telegram_id(session):
    u = _add(session, telegram_id=777, handle_observed="old")
    got = identity.resolve(session, 777, "newhandle")
    assert got.id == u.id
    assert got.handle_observed == "newhandle"  # observed handle refreshed


def test_claim_unclaimed_by_handle(session):
    u = _add(session, handle_sheet="ivanov")
    got = identity.resolve(session, 555, "ivanov")
    assert got.id == u.id
    assert got.telegram_id == 555
    assert got.handle_observed == "ivanov"


def test_claimed_record_not_reclaimed_by_handle(session):
    _add(session, handle_sheet="ivanov", telegram_id=111)
    got = identity.resolve(session, 999, "ivanov")
    assert got is None  # already claimed; handle no longer a valid path


def test_a_departed_row_is_not_claimed_by_a_matching_handle(session):
    # Claiming binds a telegram_id. Doing that to someone the roster dropped
    # would write to their row on every message, only to refuse them after.
    u = _add(session, handle_sheet="ivanov", departed_at="2026-07-28")
    assert identity.resolve(session, 555, "ivanov") is None
    assert u.telegram_id is None  # nothing written


def test_unknown_user_returns_none(session):
    assert identity.resolve(session, 42, "nobody") is None


def test_reset_binding(session):
    u = _add(session, matriculation="30000001", telegram_id=777)
    assert identity.reset_binding(session, "30000001") is True
    session.refresh(u)
    assert u.telegram_id is None


def test_bootstrap_creates_transient_admin_when_unknown():
    from jbcub_bot.core.models import Role
    p = identity.apply_bootstrap(None, 999, "adm", {999})
    assert p is not None
    assert p.role is Role.ADMIN
    assert p.telegram_id == 999


def test_bootstrap_elevates_existing_principal():
    from jbcub_bot.core.models import Role
    u = User(last_name="X", role=Role.STUDENT)
    p = identity.apply_bootstrap(u, 999, "adm", {999})
    assert p.role is Role.ADMIN


def test_bootstrap_noop_for_non_admin_id():
    assert identity.apply_bootstrap(None, 1, "x", {999}) is None


def test_find_impersonation_target_by_matriculation(session):
    u = _add(session, matriculation="30000001", telegram_id=777)
    got = identity.find_impersonation_target(session, "30000001")
    assert got.id == u.id


def test_find_impersonation_target_by_telegram_id(session):
    u = _add(session, matriculation="ABC", telegram_id=777)
    got = identity.find_impersonation_target(session, "777")
    assert got.id == u.id


def test_find_impersonation_target_prefers_matriculation_when_numeric(session):
    by_matr = _add(session, matriculation="777", telegram_id=111)
    _add(session, matriculation="OTHER", telegram_id=777)
    got = identity.find_impersonation_target(session, "777")
    assert got.id == by_matr.id  # matriculation wins even though numeric


def test_find_impersonation_target_not_found(session):
    _add(session, matriculation="30000001", telegram_id=777)
    assert identity.find_impersonation_target(session, "nope") is None
    assert identity.find_impersonation_target(session, "999") is None
