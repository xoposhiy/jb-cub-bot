"""Who the caller is, and whether the bot is open to them.

Two of the middleware stages `main.py` mounts, and they are deliberately not
adjacent: `PrincipalMiddleware` opens the update, `AccessMiddleware` closes it
to whoever it turns out to be about, and other stages run in between and may
change that answer. Neither knows what those stages do, and neither needs to --
`build_dispatcher` owns the order and says why it is that order.

What both of them lean on is `identity.closed_out`: one predicate for "the
roster no longer lists this person", `BOOTSTRAP_ADMIN_IDS` exemption included,
so that the refusal below is the only place that decides it and every caller
meets the same one.

Every entry point -- command, intent, callback -- comes through here, which is
what makes this able to close all of them together.

Hiding a departed person from a *listing* is a different question, and an opt-in
one: see `include_departed` in `features/directory/search.py`.
"""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from jbcub_bot.core import identity
from jbcub_bot.core.models import Role

_RANK = {Role.STUDENT: 0, Role.TEACHER: 1, Role.ADMIN: 2}

DEPARTED_NOTICE = (
    "The program roster no longer lists you, so the bot is closed to you.\n\n"
    "If that's a mistake, ask a program admin to check the roster."
)

GROUP_NOTICE = "I only work in a private chat — message me directly."


def role_rank(role: Role) -> int:
    return _RANK[role]


async def notify(event, notice: str) -> None:
    """Say something back in the shape the event can carry.

    An alert for a button press, a message otherwise: a toast under a tapped
    button scrolls away unread, and silence would look like the bot is broken
    rather than closed. The `answer is None` case (an event type with nothing
    to reply to) is unreachable from any call site today -- every one of them
    already knows it has a message or a callback -- but it costs nothing to
    keep, and a future caller may not.

    Public because stage 2 answers too, from another module, and the choice of
    shape is not something two files should each decide for themselves.
    """
    answer = getattr(event, "answer", None)
    if answer is None:
        return
    if isinstance(event, CallbackQuery):
        await answer(notice, show_alert=True)
    else:
        await answer(notice)


async def refuse_departed(event) -> None:
    """See `notify`: the departed_at wording.

    One wording, whoever the principal turned out to be. An `/as` target gets
    exactly this, with no hint appended -- the banner from stage 3 is already
    on screen above it saying whose view this is and how to leave.
    """
    await notify(event, DEPARTED_NOTICE)


async def refuse_group_chat(event) -> None:
    """See `notify`: the group-chat wording."""
    await notify(event, GROUP_NOTICE)


def _chat_of(event):
    """The chat an update belongs to, or None if it carries none at all.

    A callback's chat lives on the message the button was attached to, not
    on the callback itself; an update with neither isn't this guard's
    business, so callers let it through rather than guess. That includes an
    inline-mode callback (`inline_message_id` instead of `message`), which is
    unreachable today because no inline handler exists and `allowed_updates`
    excludes inline queries -- revisit this branch if inline mode is ever
    added, since it would then bypass the guard silently.
    """
    if isinstance(event, CallbackQuery):
        message = event.message
        return message.chat if message is not None else None
    return getattr(event, "chat", None)


class PrincipalMiddleware(BaseMiddleware):
    """Stage 1: where we are, who is writing, and a session for the rest.

    The only thing it turns away is a chat the bot will not speak in, and it
    does that before opening a session or looking anybody up -- the guard is
    about the *chat*, so it needs neither. Beyond that it decides nothing. It
    records who sent the update and leaves the rest to the stages after it:
    whether that sender is let in, and whether they are even who the update
    ends up being about, are both somebody else's question. Which is why
    nothing here mentions the answer to the second one.

    It owns the session because it is the outermost stage that needs one, so
    closing it here closes it around every stage and the handler alike.
    """

    def __init__(self, session_factory, bootstrap_ids: set | None = None):
        self.session_factory = session_factory
        self.bootstrap_ids = bootstrap_ids or set()

    async def __call__(self, handler, event, data):
        chat = _chat_of(event)
        if chat is not None and chat.type != "private":
            # We dont want to spam a group with a refusal, but we do want to refuse any command or callback that comes from a group.
            text = getattr(event, "text", None) or \
                getattr(event, "caption", None)
            if isinstance(event, CallbackQuery) or \
                    (text is not None and text.startswith("/")):
                await refuse_group_chat(event)
            return None
        session = self.session_factory()
        data["session"] = session
        try:
            user = getattr(event, "from_user", None)
            principal = None
            if user is not None:
                principal = identity.resolve(session, user.id, user.username)
                principal = identity.apply_bootstrap(
                    principal, user.id, user.username, self.bootstrap_ids
                )
            # Unconditionally: `route_message` declares `principal` without a
            # default, so an absent key is a TypeError rather than a None.
            data["principal"] = principal
            return await handler(event, data)
        finally:
            session.close()


class AccessMiddleware(BaseMiddleware):
    """The one refusal: the roster no longer lists whoever this update is about.

    Asked of the principal it is handed, and with no interest in how it came to
    be that person. That indifference is the whole value of running late: a
    stage in between may have decided the update is about somebody else, and
    then what comes out of here is the refusal *they* would meet, off this same
    line, rather than a second one written to resemble it. `build_dispatcher`
    owns that ordering and explains it.
    """

    def __init__(self, bootstrap_ids: set | None = None):
        self.bootstrap_ids = bootstrap_ids or set()

    async def __call__(self, handler, event, data):
        if identity.closed_out(data.get("principal"), self.bootstrap_ids):
            await refuse_departed(event)
            return None
        return await handler(event, data)
