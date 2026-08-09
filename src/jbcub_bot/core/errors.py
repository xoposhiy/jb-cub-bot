"""Crash reporting: a failed handler must never look like a hang.

An exception escaping a handler is invisible in Telegram -- whoever typed the
command just never gets a reply. So it goes to the host's log and, with its
traceback, wherever `core.oplog` points.
"""
import logging
import traceback

from jbcub_bot.core.oplog import admin_mention

logger = logging.getLogger(__name__)

# Telegram rejects any message over 4096 characters, so a deep traceback has to
# be clipped. Leave room for the context header we prepend to it.
TELEGRAM_LIMIT = 3800
_CUT = "\n\n…(middle cut)…\n\n"
_SUMMARY_LIMIT = 600


def summarize(exc: BaseException, limit: int = _SUMMARY_LIMIT) -> str:
    """The `Type: message` line of every exception in the chain, cause first.

    A handler that re-raises to add context buries the useful exception in
    `__cause__`. Kept out of the traceback because these lines must survive the
    clipping.
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

    Both ends have to survive: a chained traceback opens with the original
    cause and closes with what reached the dispatcher. The frames in between
    are the least interesting part.
    """
    text = "".join(traceback.format_exception(exc)).strip()
    if len(text) <= limit:
        return text
    keep = limit - len(_CUT)
    head = keep // 2
    return text[:head] + _CUT + text[head - keep:]


async def report_exception(oplog, exc: BaseException, context: str) -> None:
    """Log `exc` and send its traceback wherever `oplog` points.

    Never raises: this runs on the failure path, and a bad destination must not
    mask the original error. Delivery belongs to `core.oplog`; this only
    formats. The admin ping is because a crash is one of the few entries in
    that feed somebody has to act on rather than skim.
    """
    logger.error("%s — %s: %s", context, type(exc).__name__, exc, exc_info=exc)
    if oplog is None:  # a handler called directly, with nothing to send through
        return
    ping, entities = admin_mention(getattr(oplog, "admin_ids", ()))
    prefix = f"{ping}\n" if ping else ""
    header = f"⚠️ {context[:200]}\n\n{summarize(exc)}\n\n"
    room = TELEGRAM_LIMIT - len(prefix) - len(header)
    text = prefix + header + format_traceback(exc, limit=room)
    await oplog.send(text, entities=entities)
