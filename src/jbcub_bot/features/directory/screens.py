"""Pieces every self-service screen needs: the value shortener, and the
refusals that are this feature's own rather than the contract's.

Two screens (`privacy.py`, `edit.py`) write only the caller's own row, so why
that row cannot be written is the thing they both have to say the same way --
and there are two reasons. `NOT_LINKED` used to live here beside them; refusing
a caller with no principal at all is the contract's default guard now
(`core/guards.py`). These two must not become guards: a guard is a visibility
filter, so it would hide the screen from exactly the person who has to be told
why nothing can be saved.
"""

NO_ROW = "Your account has no saved profile yet. Ask an admin to link you."
# Short enough for a callback alert, which Telegram cuts off past 200 characters.
PROVISIONAL = (
    "Your profile is a placeholder until your matriculation number arrives. "
    "Every /sync rebuilds it from the cohort sheet, so nothing saved here "
    "would last. Ask an admin to edit the sheet."
)
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
