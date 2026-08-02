"""The knowledge base as a dict in memory, fetched from GitHub.

No aiogram and no model client import: this module is about bytes and text, so
it can be tested with a tarball built in a BytesIO and a fake opener.

Only `kb/**.md` survives the unpack. `sources/` is megabytes of PDFs and, by
that repository's own rule, a match there is not evidence.
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import tarfile
import time
import urllib.request
from dataclasses import dataclass

import yaml

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

_TIMEOUT = 30


@dataclass(frozen=True)
class Source:
    """Where a note's text came from, as the note itself records it.

    The knowledge base is generated with this block in every note, so the bot
    never parses a PDF and never computes a page number -- it reads one the
    repository's own tooling wrote.
    """
    file: str = ""       # sources/policies/bachelor_policies_v8.pdf
    document: str = ""   # "Policies for Bachelor Studies"
    version: str = ""
    sections: tuple[str, ...] = ()
    pdf_pages: str = ""  # "18" or "18-20"; empty for a web source
    url: str = ""        # set for a web source, empty for a PDF

    @property
    def is_pdf(self) -> bool:
        return self.file.lower().endswith(".pdf")

    @classmethod
    def from_mapping(cls, raw) -> "Source | None":
        """None unless `raw` is a mapping. Every value is coerced to str:
        YAML reads `version: 8` as an int and `valid_from:` as a date."""
        if not isinstance(raw, dict):
            return None
        sections = raw.get("sections") or ()
        if isinstance(sections, str):
            sections = (sections,)
        return cls(
            file=str(raw.get("file") or ""),
            document=str(raw.get("document") or ""),
            version=str(raw.get("version") or ""),
            sections=tuple(str(s) for s in sections),
            pdf_pages=str(raw.get("pdf_pages") or ""),
            url=str(raw.get("url") or ""),
        )


@dataclass(frozen=True)
class Note:
    path: str  # repository path, e.g. "kb/policies/exams.md"
    text: str
    title: str = ""
    description: str = ""
    source: "Source | None" = None


@dataclass(frozen=True)
class Snapshot:
    sha: str
    repo: str
    notes: dict[str, Note]

    @property
    def map_text(self) -> str:
        return render_map(self.notes)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """`(mapping, body)`. An absent or unparseable block yields `({}, ...)`.

    A person edits these notes, so one bad note must cost that note's metadata
    and nothing else -- never the whole snapshot.
    """
    match = _FRONTMATTER.match(text)
    if match is None:
        return {}, text
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, text[match.end():]
    return (meta if isinstance(meta, dict) else {}), text[match.end():]


def notes_from_tarball(blob: bytes) -> dict[str, Note]:
    """Every `kb/**.md` in a GitHub tarball, keyed by its repository path.

    GitHub prefixes every member with `<repo>-<sha>/`, so the first path
    component is dropped.
    """
    notes: dict[str, Note] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            _, _, path = member.name.partition("/")
            if not path.startswith("kb/") or not path.endswith(".md"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            text = handle.read().decode("utf-8", errors="replace")
            meta, _body = parse_frontmatter(text)
            notes[path] = Note(
                path=path,
                text=text,
                title=str(meta.get("title") or ""),
                description=str(meta.get("description") or ""),
                source=Source.from_mapping(meta.get("source")),
            )
    return notes


def render_map(notes: dict[str, Note]) -> str:
    """One line per note: the path, its title and its description.

    This goes in the system prompt so the agent usually reaches the right note
    in one read instead of three steps of reconnaissance.
    """
    lines = []
    for path in sorted(notes):
        note = notes[path]
        label = " — ".join(p for p in (note.title, note.description) if p)
        lines.append(f"- {path}{': ' + label if label else ''}")
    return "\n".join(lines)


def fetch_head_sha(repo: str, opener=urllib.request.urlopen) -> str:
    """The commit the default branch points at — one cheap call, no download."""
    url = f"https://api.github.com/repos/{repo}/commits/HEAD"
    with opener(url, timeout=_TIMEOUT) as response:
        return json.loads(response.read())["sha"]


def load_snapshot(repo: str, sha: str, opener=urllib.request.urlopen) -> Snapshot:
    url = f"https://codeload.github.com/{repo}/tar.gz/{sha}"
    with opener(url, timeout=_TIMEOUT) as response:
        blob = response.read()
    return Snapshot(sha=sha, repo=repo, notes=notes_from_tarball(blob))


class SnapshotStore:
    """Holds one snapshot and decides when it is stale.

    Past the TTL it asks GitHub for the head `sha` and only downloads again when
    that differs, so an hour of questions costs one cheap call rather than a
    tarball. Both the call and the unpack run in a worker thread: they are
    blocking I/O, and this bot has one event loop.

    The lock is not decoration — two staff asking at the same moment would
    otherwise both download the repository.
    """

    def __init__(self, repo: str, ttl_seconds: int, *,
                 opener=urllib.request.urlopen, clock=time.monotonic):
        self._repo = repo
        self._ttl = ttl_seconds
        self._opener = opener
        self._clock = clock
        self._snapshot: Snapshot | None = None
        self._checked_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, *, force: bool = False) -> Snapshot:
        async with self._lock:
            if self._snapshot is None:
                sha = await asyncio.to_thread(fetch_head_sha, self._repo,
                                              self._opener)
                self._snapshot = await asyncio.to_thread(
                    load_snapshot, self._repo, sha, self._opener)
                self._checked_at = self._clock()
                return self._snapshot
            if not force and self._clock() - self._checked_at < self._ttl:
                return self._snapshot
            sha = await asyncio.to_thread(fetch_head_sha, self._repo,
                                          self._opener)
            self._checked_at = self._clock()
            if sha != self._snapshot.sha:
                self._snapshot = await asyncio.to_thread(
                    load_snapshot, self._repo, sha, self._opener)
            return self._snapshot
