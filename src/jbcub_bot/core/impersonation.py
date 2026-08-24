"""Which student an admin is currently viewing the bot as.

The mode is sticky: `/as` enters it and `/unas` leaves it, so every update in
between belongs to the target. In memory rather than on the admin's row -- a
deploy dropping someone back into their own view is the safe direction, and the
banner going missing says so. One process and one event loop, so no locking.

The whole mode is here: the map, the middleware acting on it (stage 2 of the
stack in `core/principal.py`), and the banner. Core rather than a feature
because it changes *who the principal is*, which the core cannot delegate to a
package it may not import. The two commands that flip it are a feature like any
other: `features/impersonate/`.
"""

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from jbcub_bot.core import identity
from jbcub_bot.core.models import Role, User
from jbcub_bot.core.pipeline import command_of
from jbcub_bot.core.principal import notify

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
    """A stable-enough short reference that the existing resolver accepts.

    A provisional key lasts one `/sync`, so a linked first-year is referred to
    by `telegram_id` instead -- otherwise a sync in the middle of the mode
    would end it, which is the case the middleware below already names.
    """
    if identity.is_provisional(user) and user.telegram_id is not None:
        return str(user.telegram_id)
    if user.matriculation:
        return user.matriculation
    if user.telegram_id is not None:
        return str(user.telegram_id)
    raise ValueError("An impersonation target needs matriculation or telegram_id")


_EXIT_COMMAND = "unas"

BANNER = "\U0001f464 Viewing as {name} · /unas to return"

TARGET_GONE = (
    "The person you were viewing as is no longer on the roster, so you are "
    "back to your own view."
)


def is_exit_command(event) -> bool:
    """True for the message that leaves the mode, which is never impersonated.

    The one exemption stage 2 keeps: a departed target is refused before any
    handler runs, so without it `/as <departed student>` is a trap with no way
    out short of a restart.

    It reads the command through `pipeline.command_of` -- the same reading the
    router will use on this message a moment later -- rather than a second one
    of its own, which would let the two disagree about what `/unas` looks like.
    """
    if isinstance(event, CallbackQuery):
        return False
    command = command_of(event)
    return command is not None and command[0] == _EXIT_COMMAND


class ImpersonationMiddleware(BaseMiddleware):
    """Stage 2: whose eyes the rest of the update looks through.

    Honoured only for an admin who is themselves let in: `closed_out` -- the
    same predicate stage 4 applies to whatever principal comes out of here --
    also decides whether there is a swap at all, so an admin the roster dropped
    is refused as themselves rather than let on through someone else.

    Declining is silent, and leaves the entry in place. A stale entry belongs
    to someone demoted since they ran `/as`, and this update asked for nobody's
    view -- refusing it every message would be noise about a request nobody
    made, and clearing it would turn a transient bad `/sync` into a lost
    session. Running `/as` without the rank is refused by the command's guard.
    """

    def __init__(self, bootstrap_ids: set | None = None):
        self.bootstrap_ids = bootstrap_ids or set()

    async def __call__(self, handler, event, data):
        # Stage 1 put the sender here; the swap below makes it somebody else.
        caller = data.get("principal")
        user = getattr(event, "from_user", None)
        if user is None or not self._honours_the_mode(caller, event):
            return await handler(event, data)
        ref = ref_for(user.id)
        if ref is None:
            return await handler(event, data)
        target = identity.find_impersonation_target(data["session"], ref)
        if target is None:
            # The row went away under the mode -- a `/sync` dropping a person
            # entirely rather than marking them departed. Nobody left to be, so
            # end the mode and stop here: the update was meant for the target,
            # and answering it as the admin would show them their own screen
            # with no sign that is not what they asked for.
            end(user.id)
            await notify(event, TARGET_GONE)
            return None
        data["principal"] = target
        data["impersonator"] = caller
        return await handler(event, data)

    def _honours_the_mode(self, caller, event) -> bool:
        return (caller is not None
                and caller.role is Role.ADMIN
                and not identity.closed_out(caller, self.bootstrap_ids)
                and not is_exit_command(event))


class BannerMiddleware(BaseMiddleware):
    """Stage 3: say whose eyes these are, sent before the handler runs.

    Messages only -- a button usually edits its own message in place, so a
    banner per tap would push the screen it just redrew off the top. Most
    handlers reply with a fresh message, so the banner lands above the answer;
    a handler that instead edits an older message gets it below, since that
    message already existed.

    Before stage 4 on purpose: a departed target's refusal is the student's
    own, word for word, and this line is what tells the admin why they are
    reading it and how to stop. The cost is a *tap* under a departed target,
    which gets the bare notice until the admin's next typed message.

    No exceptions needed: `/unas` arrives unimpersonated (`is_exit_command`),
    and a `/as` refused inside the mode is refused *because* of the mode.
    """

    async def __call__(self, handler, event, data):
        target = data.get("principal")
        if data.get("impersonator") is not None and target is not None:
            await event.answer(BANNER.format(name=target.full_name))
        return await handler(event, data)
