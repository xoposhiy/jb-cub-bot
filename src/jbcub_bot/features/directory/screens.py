"""Pieces every self-service screen needs: the value shortener, and the two
refusals that are this feature's own rather than the contract's.

Two screens (`privacy.py`, `edit.py`) write only the caller's own row, so
whether the caller *has* a row is the one thing they both have to say the same
way. `NOT_LINKED` used to live here beside `NO_ROW`; refusing a caller with no
principal at all is the contract's default guard now (`core/guards.py`), and
`NO_ROW` stays because it must not be one: a contract guard is a visibility
filter, and hiding the screen from a bootstrap admin whose principal was never
saved would take it from exactly the person who needs to be told why.
"""

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
