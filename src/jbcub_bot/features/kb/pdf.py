"""Sending a source document, and only uploading it once.

Telegram fetches a document from a URL itself and answers with a file_id that
identifies the stored copy forever. Keeping that id turns a 3 MB upload into a
short string, so the cost is paid once per deploy rather than once per session.

The cache is keyed by the whole pinned URL, so a knowledge base that moves to a
new sha uploads afresh instead of showing an old file under a new page number.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_FILE_IDS: dict[str, str] = {}


def raw_url(repo: str, sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{path}"


def cached() -> dict[str, str]:
    """Test seam: what Telegram has already stored for us."""
    return dict(_FILE_IDS)


def reset_cache() -> None:
    _FILE_IDS.clear()


async def send(bot, chat_id, url: str, caption: str):
    """The sent message if the document landed, None if it did not.

    The caller needs the message itself, not just the fact of it: the Exit
    button is moved under whatever the bot said last, and an attachment is
    often that.

    A source document is supporting evidence, not the answer: the answer has
    already been sent and names the document and pages regardless. So a failure
    here is logged and reported, never raised.
    """
    try:
        message = await bot.send_document(chat_id,
                                          document=_FILE_IDS.get(url, url),
                                          caption=caption)
    except Exception:  # noqa: BLE001 - a missing attachment must not lose the answer
        logger.exception("Could not send the source document %s", url)
        return None
    document = getattr(message, "document", None)
    file_id = getattr(document, "file_id", "")
    if file_id:
        _FILE_IDS[url] = file_id
    return message
