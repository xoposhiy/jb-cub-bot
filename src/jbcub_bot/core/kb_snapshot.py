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
import logging
import re
import tarfile
import time
import urllib.request
from dataclasses import dataclass

import yaml

logger = logging.getLogger(__name__)

_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)

_TIMEOUT = 30

# How long a failed freshness check is left alone. Below the TTL, because the
# usual cause is a rate limit or a blip that is over within the hour; above
# zero, because retrying per question turns one 403 into an outage.
_RETRY_AFTER_FAILURE = 300


@dataclass(frozen=True)
class Source:
    """Where a note's text came from, as the note's own frontmatter records it.

    The repository's tooling writes this block, so the bot never parses a PDF
    and never computes a page number.
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
        return render_folder_map(self.notes)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """`(mapping, body)`. An absent or unparseable block yields `({}, ...)`.

    A person edits these notes by hand, so one bad note costs that note's
    metadata and never the whole snapshot.
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


def render_folder_map(notes: dict[str, Note]) -> str:
    """One line per folder: its path, how many notes it holds, what it covers.

    This goes in the system prompt, and per folder rather than per note is the
    point: a folder is one source document, so the map grows with the shelf
    instead of with the page count, and an unanswerable question is decided
    against a few hundred characters rather than every filename in the repo.

    The description comes from the folder's own `_index.md`, so nobody
    maintains a second copy. A folder without one still gets its line -- a
    folder the agent cannot see is one it will never search.
    """
    folders: dict[str, int] = {}
    for path in notes:
        folder, _, _ = path.rpartition("/")
        folders[folder] = folders.get(folder, 0) + 1
    lines = []
    for folder in sorted(folders):
        index = notes.get(f"{folder}/_index.md")
        label = " — ".join(p for p in (index.title, index.description)
                           if p) if index else ""
        count = folders[folder]
        head = f"- {folder}/ ({count} note{'' if count == 1 else 's'})"
        lines.append(f"{head}{': ' + label if label else ''}")
    return "\n".join(lines)


def fetch_head_sha(repo: str, opener=urllib.request.urlopen,
                   token: str = "") -> str:
    """The commit the default branch points at -- one cheap call, no download.

    Cheap in bytes, not in quota: GitHub's REST API allows 60 calls an hour per
    IP unauthenticated, and a host NATs its outbound traffic, so those 60 are
    shared with strangers. A token buys a bucket of our own. Conditional
    requests would not help -- a 304 counts the same as a 200.

    The tarball and the PDFs come from codeload and raw.githubusercontent,
    which are outside that budget; the megabytes were never what was rationed.
    """
    url = f"https://api.github.com/repos/{repo}/commits/HEAD"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with opener(request, timeout=_TIMEOUT) as response:
        return json.loads(response.read())["sha"]


def load_snapshot(repo: str, sha: str, opener=urllib.request.urlopen) -> Snapshot:
    url = f"https://codeload.github.com/{repo}/tar.gz/{sha}"
    with opener(url, timeout=_TIMEOUT) as response:
        blob = response.read()
    return Snapshot(sha=sha, repo=repo, notes=notes_from_tarball(blob))


class SnapshotStore:
    """Holds one snapshot and decides when it is stale.

    Past the TTL it asks GitHub for the head `sha` and downloads only when that
    moved, so an hour of questions costs one cheap call rather than a tarball.
    Call and unpack run in a worker thread -- blocking I/O, one event loop.

    The lock is not decoration: two people asking at once would otherwise both
    download the repository.

    A failed freshness check costs freshness and nothing else -- the snapshot
    in memory answers, and the next check waits out `_RETRY_AFTER_FAILURE`.
    Serving a note a few minutes old beats an outage.
    """

    def __init__(self, repo: str, ttl_seconds: int, *,
                 opener=urllib.request.urlopen, clock=time.monotonic,
                 token: str = ""):
        self._repo = repo
        self._ttl = ttl_seconds
        self._opener = opener
        self._clock = clock
        self._token = token
        self._snapshot: Snapshot | None = None
        self._recheck_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, *, force: bool = False) -> Snapshot:
        async with self._lock:
            # Neither of these swallows a failure: a cold start has nothing to
            # serve, and whoever asked for a reload is owed the error rather
            # than a silent no-op.
            if self._snapshot is None:
                self._snapshot = await self._fetch()
                self._recheck_at = self._clock() + self._ttl
                return self._snapshot
            if force:
                self._snapshot = await self._fetch()
                self._recheck_at = self._clock() + self._ttl
                return self._snapshot
            if self._clock() < self._recheck_at:
                return self._snapshot
            try:
                self._snapshot = await self._fetch()
                self._recheck_at = self._clock() + self._ttl
            except Exception:  # noqa: BLE001 - any failure keeps the old notes
                self._recheck_at = self._clock() + _RETRY_AFTER_FAILURE
                logger.exception(
                    "kb: could not check %s for a newer commit; answering from "
                    "the snapshot at %s for the next %d seconds",
                    self._repo, self._snapshot.sha[:7], _RETRY_AFTER_FAILURE)
            return self._snapshot

    async def _fetch(self) -> Snapshot:
        """The head snapshot, reusing the one in hand when the sha has not
        moved -- which is the point of asking for the sha first."""
        sha = await asyncio.to_thread(fetch_head_sha, self._repo, self._opener,
                                      self._token)
        if self._snapshot is not None and sha == self._snapshot.sha:
            return self._snapshot
        return await asyncio.to_thread(load_snapshot, self._repo, sha,
                                       self._opener)
