"""Crash reporting: a failed handler must never look like a hang.

An exception escaping a handler is invisible in Telegram — the person who typed
the command just never gets a reply. So every unhandled exception goes to two
places: the host's log, and the log chat with the full traceback, falling back
to the bootstrap admins' DMs (the ids in BOOTSTRAP_ADMIN_IDS, who are reachable
even on an empty DB). Choosing between those is `core.oplog`'s job.
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


async def report_exception(oplog, exc: BaseException, context: str) -> None:
    """Log `exc` and send its traceback wherever `oplog` points.

    Never raises: this runs on the failure path, and a bad destination must not
    mask the original error. Delivery -- including the fallback to the bootstrap
    admins -- belongs to `core.oplog`; this function only formats.

    Every question and every crash land in the same chat, but a crash also
    pings the admins by name: that is the one entry in this feed that someone
    has to act on, not just skim.
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
