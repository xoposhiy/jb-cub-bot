import re

from jbcub_bot.core import identity, tokens
from jbcub_bot.core.models import User

SECRET = "unit-secret"


def _student(session):
    u = User(last_name="Ivan", matriculation="30000001")
    session.add(u)
    session.commit()
    return u


def test_issue_and_verify_roundtrip(session):
    u = _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    got = tokens.verify_link_token(session, tok, SECRET, ttl=1000)
    assert got.id == u.id


def test_token_is_a_usable_telegram_deep_link_parameter(session):
    """Telegram sends nothing at all for a malformed ?start= payload.

    Only A-Z a-z 0-9 _ - are allowed, up to 64 characters. A signed token with
    its `.` separators makes the link a no-op in the client — which looks
    exactly like a bot that ignores you.
    """
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", tok), tok
    assert len(tok) <= tokens.TELEGRAM_PAYLOAD_LIMIT


def test_invite_for_a_departed_person_is_refused(session):
    # An invite is the other way in. Honouring one for someone the roster
    # dropped would bind them a telegram_id the middleware then refuses --
    # putting them back in the sheet is what restores access.
    u = _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    u.departed_at = "2026-07-28"
    session.commit()
    assert tokens.verify_link_token(session, tok, SECRET, ttl=1000) is None


def test_expired_token_rejected(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    assert tokens.verify_link_token(session, tok, SECRET, ttl=-1) is None


def test_tampered_token_rejected(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    assert tokens.verify_link_token(session, tok, SECRET, ttl=1000) is not None
    assert tokens.verify_link_token(session, tok + "x", SECRET, ttl=1000) is None


def test_token_rejected_under_another_secret(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    assert tokens.verify_link_token(session, tok, "other-secret", 1000) is None


def test_database_never_holds_a_working_invite(session):
    """The row keeps the HMAC, not the token an admin sent out."""
    user = _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    assert user.link_nonce != tok
    assert tokens.verify_link_token(session, user.link_nonce, SECRET, 1000) is None


def test_garbage_payload_rejected(session):
    _student(session)
    assert tokens.verify_link_token(session, "", SECRET, 1000) is None
    assert tokens.verify_link_token(session, "x" * 200, SECRET, 1000) is None
    assert tokens.verify_link_token(session, "not-a-token", SECRET, 1000) is None


def test_reissuing_invalidates_the_previous_invite(session):
    _student(session)
    first = tokens.issue_link_token(session, "30000001", SECRET)
    second = tokens.issue_link_token(session, "30000001", SECRET)
    assert tokens.verify_link_token(session, first, SECRET, 1000) is None
    assert tokens.verify_link_token(session, second, SECRET, 1000) is not None


def test_single_use_via_nonce(session):
    _student(session)
    tok = tokens.issue_link_token(session, "30000001", SECRET)
    user = tokens.verify_link_token(session, tok, SECRET, ttl=1000)
    identity.bind_by_token(session, 12345, "ivan_new", user)
    # nonce cleared -> the same token no longer verifies
    assert tokens.verify_link_token(session, tok, SECRET, ttl=1000) is None
    assert user.telegram_id == 12345
    assert user.handle_observed == "ivan_new"
