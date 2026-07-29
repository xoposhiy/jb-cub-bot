"""Small, transport-safe pieces of interactive admin impersonation."""

from jbcub_bot.core.models import User

_CALLBACK_MARKER = "|as:"


def canonical_ref(user: User) -> str:
    """A stable-enough short reference that the existing resolver accepts."""
    if user.matriculation:
        return user.matriculation
    if user.telegram_id is not None:
        return str(user.telegram_id)
    raise ValueError("An impersonation target needs matriculation or telegram_id")


def callback_data(value: str, ref: str | None = None) -> str:
    """Carry an impersonation target through a Telegram button press."""
    return value if ref is None else f"{value}{_CALLBACK_MARKER}{ref}"


def split_callback(value: str | None) -> tuple[str, str | None]:
    """Return the handler payload and optional impersonation reference."""
    if value is None:
        return "", None
    payload, marker, ref = value.rpartition(_CALLBACK_MARKER)
    if not marker or not payload or not ref:
        return value, None
    return payload, ref
