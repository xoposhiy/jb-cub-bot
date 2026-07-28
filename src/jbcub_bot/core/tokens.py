"""One-time invite links.

The token travels as a Telegram deep-link parameter — `?start=<token>` — and
Telegram only accepts A-Z, a-z, 0-9, `_` and `-`, up to 64 characters. A signed
itsdangerous token breaks both rules: it is ~79 characters and its `.`
separators are outside the allowed set, so the client refuses to treat the URL
as a deep link and silently sends nothing at all. Hence a short opaque token
instead: random bytes in the link, their HMAC in the row, so a leaked database
still hands out no working invites.
"""
import hashlib
import hmac
import secrets
import time

from sqlalchemy import select

from jbcub_bot.core.models import User

# 16 bytes -> 22 base64url characters, comfortably inside Telegram's 64.
_TOKEN_BYTES = 16
TELEGRAM_PAYLOAD_LIMIT = 64


def _digest(secret: str, token: str) -> str:
    return hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def issue_link_token(session, matriculation: str, secret: str) -> str:
    user = session.scalar(select(User).where(User.matriculation == matriculation))
    if user is None:
        raise ValueError(f"no user with matriculation {matriculation}")
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    # Storing the HMAC rather than the token keeps it single-use (the row is
    # cleared on binding) without keeping a usable invite in the database.
    user.link_nonce = _digest(secret, token)
    user.link_issued_at = int(time.time())
    session.commit()
    return token


def verify_link_token(session, token: str, secret: str, ttl: int) -> User | None:
    if not token or len(token) > TELEGRAM_PAYLOAD_LIMIT:
        return None
    user = session.scalar(
        select(User).where(User.link_nonce == _digest(secret, token))
    )
    if user is None or user.link_issued_at is None:
        return None
    if int(time.time()) - user.link_issued_at > ttl:
        return None
    if user.departed_at:
        # The roster dropped them between the invite and the tap. Binding here
        # would hand out a login the middleware refuses on the very next
        # message; putting them back in the sheet is what restores access.
        return None
    return user
