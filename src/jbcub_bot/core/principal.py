"""Who the caller is, and whether the bot is open to them.

Two of the middleware stages `main.py` mounts, deliberately not adjacent:
`PrincipalMiddleware` opens the update, `AccessMiddleware` closes it to whoever
it turns out to be about, and stages in between may change that answer. Neither
knows what those are -- `build_dispatcher` owns the order and says why.

Every entry point comes through here, so closing the bot to someone closes all
of them at once. Both stages lean on `identity.closed_out`, so there is one
definition of "the roster no longer lists this person".

Hiding a departed person from a *listing* is a different, opt-in question:
`include_departed` in `features/directory/search.py`.
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
    button scrolls away unread. Public because stage 2 answers too, from
    another module, and the shape is not for two files to each decide.
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
    exactly this with nothing appended -- stage 3's banner is already above it,
    saying whose view this is and how to leave.
    """
    await notify(event, DEPARTED_NOTICE)


async def refuse_group_chat(event) -> None:
    """See `notify`: the group-chat wording."""
    await notify(event, GROUP_NOTICE)


def _chat_of(event):
    """The chat an update belongs to, or None if it carries none at all.

    A callback's chat lives on the message the button was attached to. An
    update with neither isn't this guard's business, so callers let it through
    rather than guess -- which includes an inline-mode callback. That is
    unreachable today, but adding inline mode would bypass the guard silently.
    """
    if isinstance(event, CallbackQuery):
        message = event.message
        return message.chat if message is not None else None
    return getattr(event, "chat", None)


class PrincipalMiddleware(BaseMiddleware):
    """Stage 1: where we are, who is writing, and a session for the rest.

    The only thing it turns away is a chat the bot will not speak in, before
    opening a session or looking anybody up -- that guard is about the *chat*,
    so it needs neither. Beyond recording who sent the update it decides
    nothing: whether they are let in, and whether the update is even about
    them, belong to later stages.

    It owns the session because it is the outermost stage that needs one, so
    closing it here closes it around every stage and the handler alike.
    """

    def __init__(self, session_factory, bootstrap_ids: set | None = None):
        self.session_factory = session_factory
        self.bootstrap_ids = bootstrap_ids or set()

    async def __call__(self, handler, event, data):
        chat = _chat_of(event)
        if chat is not None and chat.type != "private":
            # Only something addressed to the bot is worth a refusal; a group
            # must not be spammed for every message passing by.
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

    Asked of the principal it is handed, with no interest in how it came to be
    that person -- that indifference is the value of running late. An admin in
    `/as` on a departed student meets that student's own refusal, off this same
    line, instead of a copy written to resemble it. `build_dispatcher` owns the
    ordering and explains it.
    """

    def __init__(self, bootstrap_ids: set | None = None):
        self.bootstrap_ids = bootstrap_ids or set()

    async def __call__(self, handler, event, data):
        if identity.closed_out(data.get("principal"), self.bootstrap_ids):
            await refuse_departed(event)
            return None
        return await handler(event, data)
