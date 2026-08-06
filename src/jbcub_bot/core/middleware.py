"""Who the caller is, and whether the bot is open to them at all.

Every entry point -- command, intent, callback -- authenticates here, which
makes this the one place that can close all of them together. So both refusals
live in `PrincipalMiddleware` before any lookup: a non-private chat, because the
bot answers where it was addressed and would post one person's profile into a
group, and a `departed_at` row, because the roster no longer lists them.
`BOOTSTRAP_ADMIN_IDS` is exempt from the second one deliberately -- a bad
`/sync` must not lock out the person who can fix it -- and `/as` checks its
target separately, so that exemption covers an admin's own access and never
their view of somebody else.

Hiding a departed person from a *listing* is a different question, and an opt-in
one: see `include_departed` in `features/directory/search.py`.
"""
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery

from jbcub_bot.core import identity, impersonation
from jbcub_bot.core.models import Role, User

_RANK = {Role.STUDENT: 0, Role.TEACHER: 1, Role.ADMIN: 2}

DEPARTED_NOTICE = (
    "The program roster no longer lists you, so the bot is closed to you.\n\n"
    "If that's a mistake, ask a program admin to check the roster."
)

GROUP_NOTICE = "I only work in a private chat — message me directly."


def role_rank(role: Role) -> int:
    return _RANK[role]


class HasRole:
    def __init__(self, min_role: Role):
        self.min_role = min_role

    def __call__(self, principal: User | None) -> bool:
        if principal is None:
            return False
        return role_rank(principal.role) >= role_rank(self.min_role)


async def _refuse(event, notice: str) -> None:
    """Tell the caller why nothing happened.

    An alert for a button press, a message otherwise: a toast under a tapped
    button scrolls away unread, and silence would look like the bot is broken
    rather than closed. The `answer is None` case (an event type with nothing
    to reply to) is unreachable from either call site today -- both guards
    already know they have a message or a callback -- but it costs nothing
    to keep, and a future caller may not.
    """
    answer = getattr(event, "answer", None)
    if answer is None:
        return
    if isinstance(event, CallbackQuery):
        await answer(notice, show_alert=True)
    else:
        await answer(notice)


async def refuse_departed(event) -> None:
    """See `_refuse`: the departed_at wording."""
    await _refuse(event, DEPARTED_NOTICE)


async def refuse_group_chat(event) -> None:
    """See `_refuse`: the group-chat wording."""
    await _refuse(event, GROUP_NOTICE)


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
    def __init__(self, session_factory, bootstrap_ids: set | None = None):
        self.session_factory = session_factory
        self.bootstrap_ids = bootstrap_ids or set()

    async def __call__(self, handler, event, data):
        session = self.session_factory()
        data["session"] = session
        try:
            # The bot answers wherever it was addressed, and a command typed
            # in a group would post one person's private data -- a profile,
            # a cohort CSV row -- into that group. Closing group chats here,
            # before any lookup, keeps the decision in the one place every
            # entry point authenticates, same as the departed_at refusal
            # below. A plain group message gets silence, not a refusal:
            # nobody addressed the bot, and replying to every line in a busy
            # group is spam that Telegram will rate-limit.
            chat = _chat_of(event)
            if chat is not None and chat.type != "private":
                # aiogram's Command filter matches text *or* caption, so a
                # photo posted with "/cohort 2024" as its caption is just as
                # deliberate an address as typing the command -- reading only
                # .text would read it as background chatter and stay silent.
                text = getattr(event, "text", None) or \
                    getattr(event, "caption", None)
                if isinstance(event, CallbackQuery) or \
                        (text is not None and text.startswith("/")):
                    await refuse_group_chat(event)
                return None
            user = getattr(event, "from_user", None)
            principal = None
            if user is not None:
                principal = identity.resolve(session, user.id, user.username)
                principal = identity.apply_bootstrap(
                    principal, user.id, user.username, self.bootstrap_ids
                )
                # Every entry point authenticates here, so this is the one place
                # that can close all of them at once. Bootstrap ids are exempt:
                # they are the way back in when the roster is wrong, and a bad
                # /sync must not be able to lock out the person who can fix it.
                if principal is not None and principal.departed_at \
                        and user.id not in self.bootstrap_ids:
                    await refuse_departed(event)
                    return None
            ref = data.get("impersonate_ref")
            if ref is None and principal is not None \
                    and principal.role is Role.ADMIN:
                ref = impersonation.ref_for(user.id)
            if ref is None and isinstance(event, CallbackQuery):
                _, ref = impersonation.split_callback(event.data)
            if ref is None and principal is not None \
                    and principal.role is Role.ADMIN:
                state = data.get("state")
                if state is not None:
                    ref = (await state.get_data()).get("impersonate_ref")
            if ref is not None and principal is not None \
                    and principal.role is Role.ADMIN:
                target = identity.find_impersonation_target(session, ref)
                # /as shows the bot as its target sees it, and what a departed
                # target sees is the refusal. Kept separate from the caller's
                # own check above so the bootstrap exemption covers only the
                # admin's own access, never their view of somebody else.
                if target is not None and target.departed_at:
                    await refuse_departed(event)
                    return None
                data["principal"] = target
                data["impersonator"] = principal
                if target is not None:
                    data["impersonate_ref"] = impersonation.canonical_ref(target)
            else:
                data["principal"] = principal
            return await handler(event, data)
        finally:
            session.close()
