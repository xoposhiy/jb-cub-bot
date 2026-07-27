"""Pieces every self-service screen needs: refusals, the value shortener, and
the "is this caller usable" guard.

Two screens (`privacy.py`, `edit.py`) write only the caller's own row, so they
share one guard and one vocabulary of refusals rather than each inventing its
own wording.
"""

import functools

from aiogram.types import CallbackQuery

from jbcub_bot.core.models import User

NOT_LINKED = "You are not linked yet. Contact an admin."
NO_ROW = "Your account has no saved profile yet. Ask an admin to link you."
EXPIRED = "This screen expired — send the command again."
UNKNOWN_FIELD = "Unknown field."

EMPTY = "—"
_MAX_VALUE_LEN = 40


def short_value(value) -> str:
    """A field value that fits on one line of a screen."""
    if value in (None, ""):
        return EMPTY
    text = str(value)
    if len(text) <= _MAX_VALUE_LEN:
        return text
    return text[:_MAX_VALUE_LEN - 1] + "…"


def require_linked(fn):
    """Wrap a callback handler so it refuses an unusable caller before running.

    Mirrors CommandRegistrar._guard in core/commands.py. Uses functools.wraps
    so aiogram unwraps __wrapped__ and injects the original handler's declared
    params (principal, session, ...); guarded handlers must declare
    `principal`.

    Two distinct "not usable yet" cases: no principal at all (unlinked), and a
    bootstrap admin whose principal is a transient row never written to the
    database (`id is None` -- see identity.apply_bootstrap). The latter must
    not be silently materialized into a real row just because a button was
    tapped, so it gets refused here rather than persisted.
    """
    @functools.wraps(fn)
    async def wrapper(cb: CallbackQuery, **kwargs):
        principal: User | None = kwargs.get("principal")
        if principal is None:
            await cb.answer(NOT_LINKED, show_alert=True)
            return
        if principal.id is None:
            await cb.answer(NO_ROW, show_alert=True)
            return
        return await fn(cb, **kwargs)

    return wrapper
