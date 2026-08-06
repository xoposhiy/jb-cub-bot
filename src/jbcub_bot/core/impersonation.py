"""Which student an admin is currently viewing the bot as.

The mode is sticky: `/as` enters it and `/unas` leaves it, so every update in
between belongs to the target. Deliberately in memory and not on the admin's
row -- a deploy dropping someone back into their own view is the safe
direction, and the banner going missing says so. One process and one event
loop, so the map needs no locking.
"""

from jbcub_bot.core.models import User

_active: dict[int, str] = {}

_CALLBACK_MARKER = "|as:"


def begin(admin_id: int, ref: str) -> None:
    """Start viewing as `ref` until `end`."""
    _active[admin_id] = ref


def end(admin_id: int) -> str | None:
    """Stop; returns the ref that was active, or None if there was none."""
    return _active.pop(admin_id, None)


def ref_for(admin_id: int) -> str | None:
    return _active.get(admin_id)


def reset() -> None:
    """Drop every active session. Tests only."""
    _active.clear()


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
