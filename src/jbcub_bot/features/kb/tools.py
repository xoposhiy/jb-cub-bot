"""The agent's whole world: three pure functions over a dict of notes.

"No bash, no writes, no scripts" is a property of this module rather than an
instruction a model could be talked out of — `read_note("../../.env")` is a
missing dict key, not a path traversal, because there is no filesystem here.

Every result is clipped with a visible mark, so one tool call cannot fill the
context window.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from jbcub_bot.core.kb_snapshot import Note, Snapshot

MAX_CHARS = 20000
# A listing gets a looser cap than a note. The prompt now carries folders
# rather than filenames, so a listing is the agent's only route to a note's
# name, and a clipped one hides its tail of notes for good -- while a clipped
# note still shows most of what it says. The biggest folder in the base today
# lists at 19k, close enough to MAX_CHARS that the ordinary growth of one
# handbook would have started swallowing notes.
MAX_LIST_CHARS = 60000
MAX_MATCHES = 40
TRUNCATION_MARK = "\n[… truncated]"

# The three "nothing here" openings, named because `summarize_result` reads
# them back to tell an empty result from a full one.
_NO_NOTE = "There is no note at"
_NO_MATCH = "No line matches"
_NO_NOTES = "No notes under"

_UNKNOWN = (_NO_NOTE + " {path}. Call list_notes to see which notes exist.")


def clip(text: str, limit: int = MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + TRUNCATION_MARK


def current_datetime(now: datetime) -> str:
    """The clock, as a tool result rather than a line in the prompt.

    A tool because the prompt is what the provider caches, and a prompt that
    says what time it is stops matching itself a minute later -- a session's
    second question would then pay to write the whole history again. Asked for
    instead of given, the clock lands in the conversation, where it is written
    once and never changes.

    `now` is the caller's to supply, the way `sheets` takes `today`.
    """
    return f"{now:%A, %d %B %Y}, {now:%H:%M} UTC"


def list_notes(snapshot: Snapshot, path_prefix: str = "") -> str:
    """Paths under `path_prefix`, each with its title and description."""
    lines = []
    for path in sorted(snapshot.notes):
        if not path.startswith(path_prefix):
            continue
        note = snapshot.notes[path]
        label = " — ".join(p for p in (note.title, note.description) if p)
        lines.append(f"{path}{': ' + label if label else ''}")
    if not lines:
        return f"{_NO_NOTES} {path_prefix or 'kb/'}."
    return clip("\n".join(lines), MAX_LIST_CHARS)


def search_notes(snapshot: Snapshot, pattern: str, path_prefix: str = "") -> str:
    """`path:line: text` for every match, capped and clipped."""
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return (f"{pattern!r} is not a valid regular expression ({exc}). "
                "Try plain words instead.")
    hits: list[str] = []
    truncated = False
    for path in sorted(snapshot.notes):
        if not path.startswith(path_prefix):
            continue
        for number, line in enumerate(snapshot.notes[path].text.splitlines(), 1):
            if not regex.search(line):
                continue
            if len(hits) >= MAX_MATCHES:
                truncated = True
                break
            hits.append(f"{path}:{number}: {line.strip()}")
        if truncated:
            break
    if not hits:
        return f"{_NO_MATCH} {pattern!r} under {path_prefix or 'kb/'}."
    body = clip("\n".join(hits))
    if truncated and not body.endswith(TRUNCATION_MARK):
        body += TRUNCATION_MARK
    return body


def source_hint(note: Note) -> str:
    """One line naming the document, section and pages this note reproduces.

    The agent needs it to tell a policy note from a calendar note without
    guessing from the path, and to know a source exists at all.
    """
    src = note.source
    if src is None:
        return ""
    bits = [src.document or src.file]
    if src.sections:
        bits.append("; ".join(src.sections))
    if src.pdf_pages:
        bits.append(f"pp. {src.pdf_pages}")
    return "[source: " + " · ".join(b for b in bits if b) + "]\n\n"


def read_note(snapshot: Snapshot, path: str) -> str:
    """A whole note. Notes are 5–18 KB, so there is nothing to chunk."""
    note = snapshot.notes.get(path)
    if note is None:
        return _UNKNOWN.format(path=path)
    return clip(source_hint(note) + note.text)


@dataclass(frozen=True)
class SourceRef:
    """A source document to put in the chat, resolved from frontmatter.

    `file` is the session's deduplication key, and it is what decides the
    shape: a PDF is uploaded, a web page is linked. Both come from the note's
    own `source:` block, so the agent picks *which* document without ever
    naming a file or an address itself.
    """
    file: str     # repository path under sources/
    caption: str  # the document's name, as its frontmatter gives it
    url: str = ""  # set for a web source, empty for a PDF

    @property
    def is_pdf(self) -> bool:
        return self.file.lower().endswith(".pdf")


def numbered_sources(snapshot: Snapshot, paths: list[str]) -> str:
    """The notes the agent read, numbered, for it to choose among.

    Each line carries what the choice turns on -- which document the note came
    out of, and where in it -- so the agent is picking documents it can
    recognise rather than filenames.
    """
    lines = []
    for number, path in enumerate(paths, 1):
        note = snapshot.notes.get(path)
        src = note.source if note is not None else None
        bits = [(note.title if note is not None else "") or path]
        if src is not None:
            if src.document:
                bits.append(src.document)
            if src.sections:
                bits.append("§" + "; ".join(src.sections))
            if src.pdf_pages:
                bits.append(f"pp. {src.pdf_pages}")
        lines.append(f"{number}. " + " — ".join(bits))
    return "\n".join(lines)


def choose_sources(snapshot: Snapshot, options: list[str],
                   basket: list[SourceRef], numbers) -> str:
    """Resolve the chosen numbers to source documents, for the caller to send.

    The agent hands over numbers and nothing else. Everything the reader ends
    up seeing -- the document's name, whether it is a PDF or a page, the address
    -- is read here out of that note's frontmatter, so it is either true or
    absent. A note whose frontmatter names no source at all is dropped rather
    than shown as a repository path.
    """
    if not options:
        return ("You have not been asked about sources yet. Answer the "
                "question first; you will be given the list to choose from.")
    picked: list[str] = []
    unusable: list[str] = []
    for raw in numbers or ():
        try:
            index = int(raw)
        except (TypeError, ValueError):
            unusable.append(str(raw))
            continue
        if not 1 <= index <= len(options):
            unusable.append(str(raw))
            continue
        path = options[index - 1]
        if path not in picked:
            picked.append(path)

    added: list[str] = []
    for path in picked:
        note = snapshot.notes.get(path)
        src = note.source if note is not None else None
        if src is None or not src.file:
            continue
        if any(ref.file == src.file for ref in basket):
            continue
        name = src.document or src.file
        basket.append(SourceRef(file=src.file, caption=name, url=src.url))
        added.append(name)

    parts = []
    if added:
        parts.append("The reader will be given " + ", ".join(added) + ".")
    elif picked:
        parts.append("Those notes name no source document to show, so nothing "
                     "will be attached.")
    else:
        parts.append("Nothing will be attached.")
    if unusable:
        parts.append(f"There is no source numbered {', '.join(unusable)}.")
    return " ".join(parts)


def _size(chars: int) -> str:
    return f"{chars / 1000:.1f}k" if chars >= 1000 else str(chars)


def summarize_result(name: str, output: str) -> str:
    """What one tool call came back with, in three or four words.

    This reads the "nothing here" openings the functions above write, which is
    why it lives beside them rather than beside the trace that prints it: a
    reworded message and its reader change together.
    """
    text = output.strip()
    clipped = text.endswith(TRUNCATION_MARK.strip())
    if name == "current_datetime":
        return text  # already three words, and the one worth reading back
    if name == "read_note":
        if text.startswith(_NO_NOTE):
            return "no such note"
        return f"{_size(len(output))} chars" + (" (clipped)" if clipped else "")
    if text.startswith(_NO_MATCH) or text.startswith(_NO_NOTES):
        return "0 hits"
    if not text:
        return "empty"
    hits = len(text.splitlines()) - (1 if clipped else 0)
    return f"{hits} hit{'' if hits == 1 else 's'}" + ("+" if clipped else "")
