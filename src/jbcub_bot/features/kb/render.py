"""The model's string in, one Telegram message out.

Telegram rejects an entire message over a single unbalanced tag, and the answers
here quote policy text full of `_`, `*` and `%`. So nothing the model writes is
trusted as markup: everything is escaped, and exactly four tags are restored
afterwards.

The sources block is built from the notes' own frontmatter, never from the
model's text. A model that writes "p. 47" is inventing; a model that names
kb/policies/…/13-grading.md can be checked.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Protocol

from jbcub_bot.core.kb_snapshot import Note, Snapshot

ALLOWED = ("b", "i", "code", "blockquote")
CLIP_LIMIT = 4096  # Telegram's own limit on a text message
TRUNCATION_MARK = "\n[… truncated]"
RULE = "─" * 13


class Stats(Protocol):
    """What `render` needs of a run's statistics.

    A Protocol rather than an import: `agent.py` imports this module, so this
    module cannot import `agent.py`.
    """
    steps: int
    tool_calls: int
    notes_read: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class PdfRef:
    file: str     # repository path under sources/
    caption: str  # what the attachment is called in the chat


@dataclass(frozen=True)
class Rendered:
    html: str
    pdfs: tuple[PdfRef, ...]


# --- escaping -----------------------------------------------------------------

_ESCAPED_TAG = re.compile(r"&lt;(/?)(" + "|".join(ALLOWED) + r")&gt;",
                          re.IGNORECASE)
_TAG = re.compile(r"<(/?)(" + "|".join(ALLOWED) + r")>")


def escape_subset(text: str) -> str:
    """Escape everything, then put back the four tags we allow.

    Only the bare forms come back, so `<blockquote expandable>` and
    `<a href=…>` stay escaped and show up as visible text rather than as
    markup Telegram has to parse.
    """
    escaped = html.escape(text, quote=False)
    return _ESCAPED_TAG.sub(
        lambda m: f"<{m.group(1)}{m.group(2).lower()}>", escaped)


def balance(text: str) -> str:
    """Drop unmatched closing tags, close unclosed opening ones."""
    stack: list[str] = []
    out: list[str] = []
    pos = 0
    for match in _TAG.finditer(text):
        out.append(text[pos:match.start()])
        pos = match.end()
        name = match.group(2)
        if not match.group(1):
            stack.append(name)
            out.append(match.group(0))
        elif name in stack:
            while stack:  # a crossed tag closes what it encloses first
                top = stack.pop()
                out.append(f"</{top}>")
                if top == name:
                    break
        # an unmatched closer is dropped
    out.append(text[pos:])
    while stack:
        out.append(f"</{stack.pop()}>")
    return "".join(out)


def plain(text: str) -> str:
    """The same words with no markup, for the fallback send."""
    return html.unescape(_TAG.sub("", text))


# --- what the model cited -----------------------------------------------------

_SOURCES = re.compile(r"^[ \t]*sources?[ \t]*:[ \t]*(.+?)[ \t]*$",
                      re.IGNORECASE | re.MULTILINE)
_PATH = re.compile(r"kb/[\w./-]+\.md(?::\d+)?")
_EMPTY_WRAP = re.compile(r"[(\[][ \t]*[)\]]")


def _tidy(text: str) -> str:
    text = _EMPTY_WRAP.sub("", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([.,;:])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _unique(paths) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        path = path.split(":")[0]
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def split_sources(answer: str) -> tuple[str, list[str]]:
    """`(body, paths)`. The prompt asks for one final `Sources:` line.

    A model that writes paths inline instead is still cited: the fallback pulls
    every kb/ path out of the prose and takes it out of the body, because a raw
    repository path in the middle of a sentence is what this change exists to
    remove.
    """
    last = None
    for last in _SOURCES.finditer(answer):
        pass
    if last is not None:
        paths = _unique(_PATH.findall(last.group(1)))
        if paths:
            return _tidy(answer[:last.start()] + answer[last.end():]), paths
    paths = _unique(_PATH.findall(answer))
    if not paths:
        return _tidy(answer), []
    return _tidy(_PATH.sub("", answer)), paths


# --- the sources block --------------------------------------------------------

def _pages(raw: str) -> str:
    raw = raw.strip()
    if "-" in raw:
        first, _, last = raw.partition("-")
        return f"pp. {first.strip()}–{last.strip()}"
    return f"p. {raw}"


def source_line(path: str, note: Note | None) -> tuple[str, PdfRef | None]:
    """One line of the sources block, and the PDF it asks to be shown.

    Everything here comes from frontmatter, so it is either true or absent.
    """
    src = note.source if note is not None else None
    if src is None:
        return "📄 " + html.escape(path, quote=False), None
    head = html.escape(src.document or src.file or path, quote=False)
    if src.version:
        head += f" v{html.escape(src.version, quote=False)}"
    bits = [head]
    if src.sections:
        bits.append("§" + html.escape("; ".join(src.sections), quote=False))
    if src.is_pdf:
        if src.pdf_pages:
            bits.append(_pages(src.pdf_pages))
        return "📄 " + " · ".join(bits), PdfRef(file=src.file,
                                                caption=src.document or src.file)
    line = "🌐 " + " · ".join(bits)
    if src.url:
        line += "\n" + html.escape(src.url, quote=False)
    return line, None


# --- the metrics line ---------------------------------------------------------

def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _tokens(number: int) -> str:
    return f"{number / 1000:.1f}k" if number >= 1000 else str(number)


def metrics_line(stats: Stats) -> str:
    return " · ".join([
        _count(stats.steps, "step"),
        _count(stats.tool_calls, "tool call"),
        _count(stats.notes_read, "note"),
        f"{_tokens(stats.input_tokens)} in / {_tokens(stats.output_tokens)} out",
    ])


# --- the whole message --------------------------------------------------------

def _clip(text: str) -> str:
    """Cut to what Telegram accepts, leaving room for the tags balance() adds.

    Cutting mid-message can orphan an opening tag; closing it costs characters
    the limit has not budgeted for, hence the margin.
    """
    if len(text) <= CLIP_LIMIT:
        return text
    budget = CLIP_LIMIT - len(TRUNCATION_MARK) - 64
    return balance(text[:budget]) + TRUNCATION_MARK


def render(answer: str, snapshot: Snapshot, stats: Stats) -> Rendered:
    """One HTML message, plus the PDFs the answer asked to be shown."""
    body, paths = split_sources(answer)
    lines: list[str] = []
    pdfs: list[PdfRef] = []
    for path in paths:
        line, pdf = source_line(path, snapshot.notes.get(path))
        lines.append(line)
        if pdf is not None and all(p.file != pdf.file for p in pdfs):
            pdfs.append(pdf)
    parts = [balance(escape_subset(body))]
    if lines:
        parts.append("\n".join(lines))
    parts.append(f"{RULE}\n{metrics_line(stats)}")
    return Rendered(html=_clip("\n\n".join(p for p in parts if p)),
                    pdfs=tuple(pdfs))
