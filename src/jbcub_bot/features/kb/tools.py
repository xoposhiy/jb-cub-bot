"""The agent's whole world: three pure functions over a dict of notes.

"No bash, no writes, no scripts" is a property of this module rather than an
instruction a model could be talked out of — `read_note("../../.env")` is a
missing dict key, not a path traversal, because there is no filesystem here.

Every result is clipped with a visible mark, so one tool call cannot fill the
context window.
"""
from __future__ import annotations

import re

from jbcub_bot.core.kb_snapshot import Note, Snapshot

MAX_CHARS = 20000
MAX_MATCHES = 40
TRUNCATION_MARK = "\n[… truncated]"

_UNKNOWN = ("There is no note at {path}. Call list_notes to see which notes "
            "exist.")


def clip(text: str, limit: int = MAX_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + TRUNCATION_MARK


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
        return f"No notes under {path_prefix or 'kb/'}."
    return clip("\n".join(lines))


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
        return f"No line matches {pattern!r} under {path_prefix or 'kb/'}."
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
