"""The knowledge base as bytes: what survives the unpack, and when we re-fetch.

Every test builds its own tar.gz in memory and hands the module a fake opener,
so nothing here touches GitHub or the disk.
"""
import io
import json
import tarfile

from jbcub_bot.core import kb_snapshot

NOTE = """---
title: Exam rules
description: When exams happen and how retakes work.
---

Retakes are allowed once.
"""

BARE = "Just a heading\n\nand a paragraph.\n"


def _tarball(files: dict[str, str], prefix: str = "cub-kb-abc123") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            data = body.encode()
            info = tarfile.TarInfo(f"{prefix}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeOpener:
    """Answers the commits call with `sha` and the tarball call with `files`."""

    def __init__(self, sha: str, files: dict[str, str]):
        self.sha = sha
        self.files = files
        self.urls: list[str] = []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        if "/commits/" in url:
            return FakeResponse(json.dumps({"sha": self.sha}).encode())
        return FakeResponse(_tarball(self.files, prefix=f"cub-kb-{self.sha}"))

    @property
    def downloads(self) -> int:
        return len([u for u in self.urls if "/commits/" not in u])


def test_only_kb_markdown_survives_the_unpack():
    notes = kb_snapshot.notes_from_tarball(_tarball({
        "kb/policies/exams.md": NOTE,
        "kb/README.md": BARE,
        "sources/handbook.pdf": "%PDF-1.7 binary-ish",
        "kb/diagram.png": "not markdown",
        "AGENTS.md": "repo rules, not a note",
    }))

    assert sorted(notes) == ["kb/README.md", "kb/policies/exams.md"]


def test_frontmatter_becomes_title_and_description():
    notes = kb_snapshot.notes_from_tarball(_tarball({"kb/policies/exams.md": NOTE}))
    note = notes["kb/policies/exams.md"]

    assert note.title == "Exam rules"
    assert note.description == "When exams happen and how retakes work."
    assert note.text.endswith("Retakes are allowed once.\n")


def test_a_note_without_frontmatter_is_still_listed():
    notes = kb_snapshot.notes_from_tarball(_tarball({"kb/loose.md": BARE}))

    assert notes["kb/loose.md"].title == ""
    assert kb_snapshot.render_map(notes).count("kb/loose.md") == 1


def test_the_map_carries_one_line_per_note():
    notes = kb_snapshot.notes_from_tarball(_tarball({
        "kb/policies/exams.md": NOTE,
        "kb/loose.md": BARE,
    }))

    lines = [ln for ln in kb_snapshot.render_map(notes).splitlines() if ln.strip()]

    assert len(lines) == 2
    assert any("kb/policies/exams.md" in ln and "Exam rules" in ln
               and "how retakes work" in ln for ln in lines)


async def test_first_get_downloads_and_caches():
    opener = FakeOpener("sha-one", {"kb/a.md": NOTE})
    store = kb_snapshot.SnapshotStore("xoposhiy/cub-kb", 3600, opener=opener)

    first = await store.get()
    second = await store.get()

    assert first.sha == "sha-one"
    assert second is first
    assert opener.downloads == 1


# One tick is consumed per get(): the first stamps `checked_at`, the second
# reads the clock for the TTL comparison and then stamps it again.
def _ticks(*values):
    it = iter(values)
    return lambda: next(it)


async def test_an_unchanged_sha_reuses_the_snapshot_without_downloading():
    opener = FakeOpener("sha-one", {"kb/a.md": NOTE})
    store = kb_snapshot.SnapshotStore("xoposhiy/cub-kb", 3600, opener=opener,
                                      clock=_ticks(0.0, 9999.0, 9999.0))

    first = await store.get()
    again = await store.get()  # past the TTL: one more commits call, no tarball

    assert again is first
    assert opener.downloads == 1
    assert sum("/commits/" in u for u in opener.urls) == 2


async def test_a_moved_sha_refetches():
    opener = FakeOpener("sha-one", {"kb/a.md": NOTE})
    store = kb_snapshot.SnapshotStore("xoposhiy/cub-kb", 3600, opener=opener,
                                      clock=_ticks(0.0, 9999.0, 9999.0))
    await store.get()
    opener.sha = "sha-two"

    moved = await store.get()

    assert moved.sha == "sha-two"
    assert opener.downloads == 2


async def test_force_skips_the_ttl():
    opener = FakeOpener("sha-one", {"kb/a.md": NOTE})
    store = kb_snapshot.SnapshotStore("xoposhiy/cub-kb", 3600, opener=opener,
                                      clock=lambda: 0.0)
    await store.get()

    await store.get(force=True)

    # The clock never moves, so an unforced second get would have asked nothing.
    # The second commits call is the whole evidence that force bypassed the TTL.
    assert sum("/commits/" in u for u in opener.urls) == 2
    assert opener.downloads == 1, "the sha did not move, so nothing to download"
