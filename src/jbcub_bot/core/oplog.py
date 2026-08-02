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

    async def send(self, text: str) -> None:
        if self.bot is None:  # a handler called directly, with no bot to send through
            return
        if self.chat_id and await self._try(self.chat_id, text):
            return
        for admin_id in self.admin_ids:
            await self._try(admin_id, text)

    async def _try(self, chat_id, text: str) -> bool:
        """True if it landed. Plain text: an entry quotes whatever a user typed."""
        try:
            await self.bot.send_message(chat_id=chat_id, text=text)
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


def format_kb_session(asked: int, principal=None, tg_user=None) -> str:
    """One entry per closed knowledge base session.

    Staff text that used to land in format_miss now gets an offer to search
    instead, so this is what replaces that entry: how much the feature was
    actually used, which is what a daily quota would eventually be chosen from.
    """
    return "\n".join([
        "📚 Knowledge base session",
        f"from: {describe_sender(principal, tg_user)}",
        f"questions: {asked}",
    ])
