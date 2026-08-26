"""Where an operational report goes, and what one looks like.

A crash or a dead end is invisible in Telegram: whoever typed it just gets
nothing useful. Reports go to one private staff chat -- or, if it is unset or
the bot was thrown out of it, to the bootstrap admins' DMs, which work even on
an empty database.
"""
import logging
from collections.abc import Iterable

logger = logging.getLogger(__name__)

# A query is user text, and someone will paste an essay into the search box.
MISS_LIMIT = 500


def clip(text: str, limit: int = MISS_LIMIT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


class OpsLog:
    """Delivers a report, and never lets the delivery become the failure."""

    def __init__(self, bot, chat_id: str = "",
                 admin_ids: Iterable[int] | None = None):
        self.bot = bot
        self.chat_id = str(chat_id or "").strip()
        self.admin_ids = sorted(admin_ids or ())

    async def send(self, text: str, entities=None) -> None:
        if self.bot is None:  # a handler called directly, with no bot to send through
            return
        if self.chat_id and await self._try(self.chat_id, text, entities):
            return
        for admin_id in self.admin_ids:
            await self._try(admin_id, text, entities)

    async def _try(self, chat_id, text: str, entities=None) -> bool:
        """True if it landed. Plain text: an entry quotes whatever a user typed."""
        try:
            await self.bot.send_message(chat_id=chat_id, text=text,
                                        entities=entities)
            return True
        except Exception:  # noqa: BLE001 - a bad destination must not hide the report
            logger.exception("Could not deliver an ops report to %s", chat_id)
            return False


def describe_sender(principal, tg_user) -> str:
    """Who asked, from both sides: the roster row and Telegram itself.

    The handle goes in bare. With an `@` Telegram reads it as a mention and
    notifies that person, so an admin watching this chat is pinged by their own
    every question. Pinging is `admin_mention`'s job, for the entries somebody
    has to act on.
    """
    parts: list[str] = []
    if principal is not None:
        parts.append(principal.full_name or "(no name)")
    if tg_user is not None:
        if tg_user.username:
            parts.append(tg_user.username)
        parts.append(str(tg_user.id))
    if principal is not None:
        parts.append(principal.role.value)
    return " · ".join(parts) or "unknown"


def format_miss(query: str, answer: str, principal=None, tg_user=None,
                impersonator=None) -> str:
    """One entry for a request the bot could not serve.

    Under `/as` the human who typed it is the impersonator, not the principal,
    so the credit goes to them and the target gets its own line.
    """
    actor = impersonator if impersonator is not None else principal
    lines = ["🔍 Nothing matched", f"from: {describe_sender(actor, tg_user)}"]
    if impersonator is not None:
        target = principal.full_name if principal is not None else "(nobody)"
        lines.append(f"as: {target}")
    lines.append(f"query: «{clip(query)}»")
    lines.append(f"answer: «{clip(answer)}»")
    return "\n".join(lines)


def admin_mention(admin_ids: Iterable[int]) -> tuple[str, list]:
    """A line that pings every admin, without needing any of their usernames.

    `text_mention` entities alongside plain text rather than parsed markup, so
    a report can never be the message that fails to send.
    """
    from aiogram.types import MessageEntity
    from aiogram.types import User as TgUser

    ids = sorted(set(admin_ids))
    if not ids:
        return "", []
    words = [f"@admin{admin_id}" for admin_id in ids]
    entities = []
    offset = 0
    for admin_id, word in zip(ids, words):
        entities.append(MessageEntity(
            type="text_mention", offset=offset, length=len(word),
            user=TgUser(id=admin_id, is_bot=False, first_name="Admin"),
        ))
        offset += len(word) + 1  # the joining space
    return " ".join(words), entities


def format_kb_feedback(good: bool, principal=None, tg_user=None) -> str:
    """One entry per rating a reader leaves on their way out of a session.

    A thumb reads at a glance, which is what keeps this feed skimmable.
    """
    icon = "👍" if good else "👎"
    return "\n".join([
        f"{icon} Knowledge base feedback",
        f"from: {describe_sender(principal, tg_user)}",
    ])


def format_kb_rate_limited(limit: int, principal=None, tg_user=None) -> str:
    """The shared AI budget ran out for the hour.

    Nobody should reach this in normal use, so it gets the admin ping a crash
    gets: something to go and look at, not skim past.
    """
    return "\n".join([
        f"🚨 Knowledge base hit its hourly limit ({limit} questions)",
        f"from: {describe_sender(principal, tg_user)}",
    ])


def format_kb_person_found(question: str, target_name: str, principal=None,
                           tg_user=None) -> str:
    """The head of one entry for a question the agent answered by showing a
    profile instead of writing words -- `search_people` and `show_profile`.

    The caller appends the trace, same as `format_kb_question`, so the cost of
    finding them sits under the question that caused it.
    """
    return "\n".join([
        "🔎 Knowledge base found a person",
        f"from: {describe_sender(principal, tg_user)}",
        f"question: «{clip(question)}»",
        f"shown: {target_name}",
    ])


def format_kb_question(question: str, principal=None, tg_user=None) -> str:
    """The head of one entry per question put to the knowledge base.

    The caller appends the trace, so the cost of an answer sits under the
    question that caused it.
    """
    return "\n".join([
        "📚 Knowledge base question",
        f"from: {describe_sender(principal, tg_user)}",
        f"question: «{clip(question)}»",
    ])
