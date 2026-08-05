"""Where an operational report goes, and what an unanswered request looks like.

A crash or a dead end is invisible in Telegram: the person who typed it just
gets nothing useful. Both go to one private staff chat -- and if that chat is
unset, or the bot was removed from it, to the bootstrap admins' DMs, which work
even on an empty database.
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
    """Who asked, from both sides: the roster row and Telegram itself."""
    parts: list[str] = []
    if principal is not None:
        parts.append(principal.full_name or "(no name)")
    if tg_user is not None:
        if tg_user.username:
            parts.append(f"@{tg_user.username}")
        parts.append(str(tg_user.id))
    if principal is not None:
        parts.append(principal.role.value)
    return " · ".join(parts) or "unknown"


def format_miss(query: str, answer: str, principal=None, tg_user=None,
                impersonator=None) -> str:
    """One entry for a request the bot could not serve.

    While /as is on, `principal` is the target and the human who typed this is
    the impersonator, so the credit goes to them and the target gets its own
    line.
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

    Built as `text_mention` entities passed alongside plain text rather than
    parsed from markup, so nothing here can turn into the thing that fails to
    send -- the same reason `render.trace_message` stays plain text.
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

    The icon is what makes this skimmable in a chat that also carries every
    question and its cost: a thumb reads at a glance, no need to open the
    entry to know which way it went.
    """
    icon = "👍" if good else "👎"
    return "\n".join([
        f"{icon} Knowledge base feedback",
        f"from: {describe_sender(principal, tg_user)}",
    ])


def format_kb_rate_limited(limit: int, principal=None, tg_user=None) -> str:
    """The one entry that says the shared AI budget ran out for the hour.

    Nobody is meant to hit this in normal use, so unlike a plain question it
    carries the caller's admin mention -- the same treatment a crash gets --
    because this is something to go and look at, not just skim past.
    """
    return "\n".join([
        f"🚨 Knowledge base hit its hourly limit ({limit} questions)",
        f"from: {describe_sender(principal, tg_user)}",
    ])


def format_kb_question(question: str, principal=None, tg_user=None) -> str:
    """The head of one entry per question put to the knowledge base.

    Who asked and what they asked, which is the whole point of watching this
    chat while the bot is out with the team. The caller appends the same trace
    an admin sees, so the cost of the answer sits directly under the question.
    """
    return "\n".join([
        "📚 Knowledge base question",
        f"from: {describe_sender(principal, tg_user)}",
        f"question: «{clip(question)}»",
    ])
