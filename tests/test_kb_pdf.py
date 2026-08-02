"""Uploading a 3 MB handbook once, then never again.

Telegram hands back a file_id for anything it has stored; sending that string
instead of a URL is the difference between a re-upload and a pointer.
"""
from types import SimpleNamespace

import pytest

from jbcub_bot.features.kb import pdf


@pytest.fixture(autouse=True)
def _clean():
    pdf.reset_cache()
    yield
    pdf.reset_cache()


class FakeBot:
    def __init__(self, file_id="FILE-1", explode=False):
        self.file_id = file_id
        self.explode = explode
        self.documents: list = []

    async def send_document(self, chat_id, document, caption=None):
        if self.explode:
            raise RuntimeError("telegram said no")
        self.documents.append(document)
        return SimpleNamespace(
            document=SimpleNamespace(file_id=self.file_id))


URL = "https://raw.githubusercontent.com/xoposhiy/cub-kb/abc123/sources/p.pdf"


def test_the_raw_url_is_pinned_to_the_snapshot_sha():
    assert pdf.raw_url("xoposhiy/cub-kb", "abc123", "sources/p.pdf") == URL


async def test_the_first_send_uses_the_url():
    bot = FakeBot()

    assert await pdf.send(bot, 5, URL, "Policies") is not None
    assert bot.documents == [URL]


async def test_the_second_send_reuses_the_file_id():
    bot = FakeBot()
    await pdf.send(bot, 5, URL, "Policies")

    await pdf.send(bot, 5, URL, "Policies")

    assert bot.documents == [URL, "FILE-1"]


async def test_a_moved_sha_is_a_different_file():
    bot = FakeBot()
    await pdf.send(bot, 5, URL, "Policies")

    moved = pdf.raw_url("xoposhiy/cub-kb", "def456", "sources/p.pdf")
    await pdf.send(bot, 5, moved, "Policies")

    assert bot.documents == [URL, moved], "a new sha must not serve a stale file"


async def test_a_failed_send_is_reported_not_raised():
    assert await pdf.send(FakeBot(explode=True), 5, URL, "Policies") is None
    assert pdf.cached() == {}, "a failure must not poison the cache"
