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

# How long a failed freshness check is left alone. Far below the TTL, because
# the usual cause is a rate limit that resets within the hour or a blip that is
# already over, and far above zero, because retrying per question is what turned
# one 403 into an outage.
_RETRY_AFTER_FAILURE = 300


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
        return render_folder_map(self.notes)


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


def render_folder_map(notes: dict[str, Note]) -> str:
    """One line per folder: its path, how many notes it holds, what it covers.

    This goes in the system prompt, and a line per folder rather than a line per
    note is the whole point: one folder here is one source document, so this map
    is the list of documents and it grows with the shelf rather than with the
    page count. A question the base cannot answer is then decided against a few
    hundred characters instead of every filename in the repository.

    The description is the folder's own `_index.md` frontmatter, so the map
    stays honest without anybody maintaining a second copy of it. A folder
    without one still gets its line -- a folder the agent cannot see is a folder
    it will never search.
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
    """The commit the default branch points at — one cheap call, no download.

    Cheap in bytes, not in quota: this is the only call the bot makes against
    GitHub's REST API, and that API allows 60 an hour per IP unauthenticated.
    A host NATs its outbound traffic, so those 60 are shared with strangers.
    One header moves us to a 5000-an-hour budget of our own; conditional
    requests would not help, since a 304 is counted the same as a 200.

    The tarball and the PDFs come from codeload and raw.githubusercontent, which
    are not part of that budget — the megabytes were never what was rationed.
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

    Past the TTL it asks GitHub for the head `sha` and only downloads again when
    that differs, so an hour of questions costs one cheap call rather than a
    tarball. Both the call and the unpack run in a worker thread: they are
    blocking I/O, and this bot has one event loop.

    The lock is not decoration — two staff asking at the same moment would
    otherwise both download the repository.

    A freshness check that fails costs freshness and nothing else: the snapshot
    already in memory answers the question, and the next check waits out
    `_RETRY_AFTER_FAILURE`. Serving a note a few minutes old is not a
    compromise worth an outage — and asking again per question is how a single
    403 used to keep the whole feature down for an hour.
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
            # A cold start has nothing to serve, and an admin who typed the
            # reload command is owed the error rather than a silent no-op, so
            # both of these let the failure through to the crash report.
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
        moved — that is the whole point of asking for the sha first."""
        sha = await asyncio.to_thread(fetch_head_sha, self._repo, self._opener,
                                      self._token)
        if self._snapshot is not None and sha == self._snapshot.sha:
            return self._snapshot
        return await asyncio.to_thread(load_snapshot, self._repo, sha,
                                       self._opener)
