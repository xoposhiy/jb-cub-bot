from sdt_bot.core import identity, tokens
from sdt_bot.core.models import User

SECRET = "unit-secret"


def _student(session):
    u = User(name="Ivan", matriculation="30000001")
    session.add(u)
    session.commit()
    return u


def test_issue_and_verify_roundtrip(session):
    u = _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    got = tokens.verify_link_token(session, tok, SECRET, ttl=1000)
    assert got.id == u.id


def test_expired_token_rejected(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    assert tokens.verify_link_token(session, tok, SECRET, ttl=-1) is None


def test_tampered_token_rejected(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    assert tokens.verify_link_token(session, tok, SECRET, ttl=1000) is not None
    assert tokens.verify_link_token(session, tok + "x", SECRET, ttl=1000) is None


def test_single_use_via_nonce(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    user = tokens.verify_link_token(session, tok, SECRET, ttl=1000)
    identity.bind_by_token(session, 12345, "ivan_new", user)
    # nonce cleared -> the same token no longer verifies
    assert tokens.verify_link_token(session, tok, SECRET, ttl=1000) is None
    assert user.telegram_id == 12345
    assert user.handle_observed == "ivan_new"
