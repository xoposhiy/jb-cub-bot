"""Crash reporting: a failed handler must never look like a hang.

An exception escaping a handler is invisible in Telegram — the person who typed
the command just never gets a reply. So every unhandled exception goes to two
places: the host's log, and a DM with the full traceback to each bootstrap
admin (the ids in BOOTSTRAP_ADMIN_IDS, who are reachable even on an empty DB).
"""
import logging
import traceback
from collections.abc import Iterable

logger = logging.getLogger(__name__)

# Telegram rejects any message over 4096 characters, so a deep traceback has to
# be clipped. Leave room for the context header we prepend to it.
TELEGRAM_LIMIT = 3800
_CUT = "\n\n…(middle cut)…\n\n"
_SUMMARY_LIMIT = 600


def summarize(exc: BaseException, limit: int = _SUMMARY_LIMIT) -> str:
    """The `Type: message` line of every exception in the chain, cause first.

    /sync wraps failures to name the phase, so the exception that reaches the
    dispatcher is a RuntimeError and the useful one is its `__cause__`. Kept
    separate from the traceback because these lines must never be clipped away.
    """
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append("".join(traceback.format_exception_only(current)).strip())
        current = current.__cause__ or current.__context__
    text = "\n↳ raised: ".join(reversed(chain))
    return text if len(text) <= limit else text[:limit] + "…"


def format_traceback(exc: BaseException, limit: int = TELEGRAM_LIMIT) -> str:
    """The traceback, with the middle dropped if it won't fit in one message.

    Both ends have to survive: a chained traceback opens with the original cause
    ("ConnectionResetError: ...") and closes with the exception that reached the
    dispatcher. Cutting from either end alone loses one of them — and the frames
    in between are the least interesting part.
    """
    text = "".join(traceback.format_exception(exc)).strip()
    if len(text) <= limit:
        return text
    keep = limit - len(_CUT)
    head = keep // 2
    return text[:head] + _CUT + text[head - keep:]


async def report_exception(
    bot, admin_ids: Iterable[int] | None, exc: BaseException, context: str
) -> None:
    """Log `exc` and DM its traceback to every bootstrap admin.

    Never raises: this runs on the failure path, and an admin who has never
    opened a chat with the bot (or has blocked it) must not break reporting for
    the others — or mask the original error.
    """
    logger.error("%s — %s: %s", context, type(exc).__name__, exc, exc_info=exc)
    if bot is None:  # nothing to send through (e.g. a handler called directly)
        return
    header = f"⚠️ {context[:200]}\n\n{summarize(exc)}\n\n"
    text = header + format_traceback(exc, limit=TELEGRAM_LIMIT - len(header))
    for admin_id in sorted(admin_ids or ()):
        try:
            await bot.send_message(chat_id=admin_id, text=text)
        except Exception:  # noqa: BLE001 - a blocked bot must not hide the crash
            logger.exception("Could not deliver the crash report to %s", admin_id)
