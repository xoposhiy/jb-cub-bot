"""Which student an admin is currently viewing the bot as.

The mode is sticky: `/as` enters it and `/unas` leaves it, so every update in
between belongs to the target. Deliberately in memory and not on the admin's
row -- a deploy dropping someone back into their own view is the safe
direction, and the banner going missing says so. One process and one event
loop, so the map needs no locking.

The whole mode lives here: the map, the middleware that acts on it (stage 2 of
the stack described in `core/principal.py`), and the banner. It has to be core
rather than a feature, because what it changes is *who the principal is*, which
is the core's own decision and which `PrincipalMiddleware` could not delegate
to a package it is forbidden to import. What is a feature is the pair of
commands that flip the switch, `features/impersonate/` -- in this architecture
every command belongs to some feature, and these two are no exception. So the
split is not a seam left half-finished; it is the seam.
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
    """A stable-enough short reference that the existing resolver accepts."""
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

    The one exemption stage 2 keeps. A departed target is refused before any
    handler runs, so without it `/as <departed student>` would be a trap with
    no way out short of a restart.

    It reads the command through `pipeline.command_of`, the same reading the
    router will use on this very message a moment later, rather than a second
    one written here. The two had already drifted: this one looked only at
    `.text`, so `/unas` sent as a photo caption was routed to `cmd_unas` by the
    pipeline while the middleware went on impersonating -- and under a departed
    target that meant a refusal instead of the exit. A callback carries no
    command at all, which is the one thing `command_of` is not asked.
    """
    if isinstance(event, CallbackQuery):
        return False
    command = command_of(event)
    return command is not None and command[0] == _EXIT_COMMAND


class ImpersonationMiddleware(BaseMiddleware):
    """Stage 2: whose eyes the rest of the update looks through.

    The mode is honoured only for an admin who is themselves let in. Both
    halves matter, and the second is the reason this runs where it does rather
    than folded into the refusal below it: an admin the roster dropped must not
    go on using the bot through somebody else's identity, so `closed_out` --
    the very predicate stage 4 will apply to whatever principal comes out of
    here -- also decides whether there is a swap at all. Declining leaves them
    as themselves, and stage 4 then refuses them, once, as themselves.

    Declining is silent. A stale entry can only belong to someone who *was* an
    admin when they ran `/as` and has since been demoted or dropped, and they
    did not ask for a view of anyone with this update -- they typed `/me`.
    Saying "not allowed" on every message in that state would be noise about a
    request nobody made. The refusal for actually running `/as` without the
    rank is the command's own guard, and it says so there. The entry is left
    in place rather than cleared: a middleware that mutates the mode on the way
    past would turn a transient bad `/sync` into a lost session.
    """

    def __init__(self, bootstrap_ids: set | None = None):
        self.bootstrap_ids = bootstrap_ids or set()

    async def __call__(self, handler, event, data):
        # Stage 1 put the sender here; the swap below is what makes it
        # somebody else.
        caller = data.get("principal")
        user = getattr(event, "from_user", None)
        if user is None or not self._honours_the_mode(caller, event):
            return await handler(event, data)
        ref = ref_for(user.id)
        if ref is None:
            return await handler(event, data)
        target = identity.find_impersonation_target(data["session"], ref)
        if target is None:
            # The row went away under the mode -- a `/sync` that drops a person
            # entirely, rather than marking them departed. There is nobody left
            # to be, so end the mode and say so. The update stops here: it was
            # meant for the target, and answering it as the admin would show
            # them their own screen with no sign that is not what they asked
            # for. Their next message is theirs, and runs normally.
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

    Messages only. A button usually edits its own message in place, so a
    banner per tap would push the screen it just redrew off the top.

    That puts it before most answers, since most handlers reply with a fresh
    message. It is not before `edit.on_value` or `_reprompt`, though: both go
    through `_redraw`, which edits a message sent earlier in the chat -- the
    banner, sent after that message already existed, lands below it instead
    of above.

    It is also, deliberately, before stage 4. That is what carries the way out
    of a departed target's refusal: the refusal itself is the student's own,
    word for word, and the line above it is what tells the admin why they are
    reading it and how to stop. The cost is the one case with no banner to
    stand above it -- a *tap* under a departed target gets the bare notice, and
    the admin recovers on their next typed message.

    It needs no exceptions: /unas arrives unimpersonated (see
    `is_exit_command`) and so announces nothing, and a /as refused inside the
    mode is refused *because* of the mode, which is worth saying.
    """

    async def __call__(self, handler, event, data):
        target = data.get("principal")
        if data.get("impersonator") is not None and target is not None:
            await event.answer(BANNER.format(name=target.full_name))
        return await handler(event, data)
