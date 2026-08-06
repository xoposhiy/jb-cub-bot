"""Which student an admin is currently viewing the bot as.

The mode is sticky: `/as` enters it and `/unas` leaves it, so every update in
between belongs to the target. Deliberately in memory and not on the admin's
row -- a deploy dropping someone back into their own view is the safe
direction, and the banner going missing says so. One process and one event
loop, so the map needs no locking.
"""

from aiogram import BaseMiddleware

from jbcub_bot.core.models import User

_active: dict[int, str] = {}


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


_EXIT_COMMAND = "/unas"

BANNER = "\U0001f464 Viewing as {name} · /unas to return"


def is_exit_command(event) -> bool:
    """True for the message that leaves the mode, which is never impersonated.

    A departed target is refused before any handler runs, so if that refusal
    covered /unas as well, `/as <departed student>` would be a trap with no way
    out short of a restart.
    """
    text = getattr(event, "text", None) or ""
    head = text.split(maxsplit=1)[0] if text.split() else ""
    return head.split("@")[0] == _EXIT_COMMAND


class BannerMiddleware(BaseMiddleware):
    """Say whose eyes these are, sent before the handler runs.

    Messages only. A button usually edits its own message in place, so a
    banner per tap would push the screen it just redrew off the top.

    That puts it before most answers, since most handlers reply with a fresh
    message. It is not before `edit.on_value` or `_reprompt`, though: both go
    through `_redraw`, which edits a message sent earlier in the chat -- the
    banner, sent after that message already existed, lands below it instead
    of above.

    It needs no exceptions: /unas arrives unimpersonated (see
    `is_exit_command`) and so announces nothing, and a /as refused inside the
    mode is refused *because* of the mode, which is worth saying.
    """

    async def __call__(self, handler, event, data):
        target = data.get("principal")
        if data.get("impersonator") is not None and target is not None:
            await event.answer(BANNER.format(name=target.full_name))
        return await handler(event, data)
