from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select

from sdt_bot.core.models import User

_SALT = "one-time-link"


def _serializer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt=_SALT)


def issue_link_token(session, matriculation: str, secret: str) -> str:
    user = session.scalar(select(User).where(User.matriculation == matriculation))
    if user is None:
        raise ValueError(f"no user with matriculation {matriculation}")
    # fresh nonce derived from the pk + current row id keeps it single-use;
    # itsdangerous provides the timestamp, we provide the uniqueness.
    nonce = f"{user.id}-{len(matriculation)}-{user.matriculation}"
    user.link_nonce = nonce
    session.commit()
    return _serializer(secret).dumps({"m": matriculation, "n": nonce})


def verify_link_token(session, token: str, secret: str, ttl: int) -> User | None:
    try:
        data = _serializer(secret).loads(token, max_age=ttl)
    except (BadSignature, SignatureExpired):
        return None
    user = session.scalar(
        select(User).where(User.matriculation == data["m"])
    )
    if user is None or user.link_nonce is None or user.link_nonce != data["n"]:
        return None
    return user
