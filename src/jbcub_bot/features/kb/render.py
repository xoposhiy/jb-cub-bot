"""The model's string in, one Telegram message out.

Telegram rejects an entire message over a single unbalanced tag, and the answers
here quote policy text full of `_`, `*` and `%`. So nothing the model writes is
trusted as markup: everything is escaped, and exactly four tags are restored
afterwards.

The sources block is built from the notes' own frontmatter, never from the
model's text. A model that writes "p. 47" is inventing; a model that names
kb/policies/…/13-grading.md can be checked.

Nothing in a reader's message ever names the knowledge base. The base is an
index the bot keeps into a handful of PDFs and web pages; as far as a teacher is
concerned only those documents exist, so a citation that cannot be resolved to
one of them is dropped rather than printed as a repository path. The paths do
appear in `trace_message`, which only an admin is shown.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Protocol, Sequence

from jbcub_bot.core.kb_snapshot import Snapshot

logger = logging.getLogger(__name__)

ALLOWED = ("b", "i", "code", "blockquote")
CLIP_LIMIT = 4096  # Telegram's own limit on a text message
# A question assembled from twenty notes would otherwise bury its own answer
# under twenty lines of provenance.
MAX_DOCUMENTS = 4
MAX_SECTIONS = 4
MAX_TRACE_CALLS = 20
INDENT = "   "
TRUNCATION_MARK = "\n[… truncated]"
RULE = "─" * 13


class Call(Protocol):
    name: str
    args: dict
    result: str


class Stats(Protocol):
    """What this module needs of a run's statistics.

    A Protocol rather than an import: `agent.py` imports this module, so this
    module cannot import `agent.py`.
    """
    steps: int
    tool_calls: int
    notes_read: int
    input_tokens: int
    output_tokens: int
    calls: Sequence[Call]


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
# A note path is a citation. A bare folder — "assembled from kb/calendars/" —
# is not, but it is still the base showing through, so it is removed from the
# prose without becoming a source.
_NOTE_PATH = re.compile(r"\bkb/[\w./-]*?\.md(?::\d+)?")
_ANY_PATH = re.compile(r"\bkb/[\w./-]*?\.md(?::\d+)?|\bkb/[\w-]+(?:/[\w-]+)*/?")
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
    every note path out of the prose. Either way the body is then swept of
    anything beginning `kb/`, folders included — a repository path in the middle
    of a sentence is exactly what the reader must never see.
    """
    last = None
    for last in _SOURCES.finditer(answer):
        pass
    body, paths = answer, []
    if last is not None:
        paths = _unique(_NOTE_PATH.findall(last.group(1)))
        if paths:
            body = answer[:last.start()] + answer[last.end():]
    if not paths:
        paths = _unique(_NOTE_PATH.findall(answer))
    return _tidy(_ANY_PATH.sub("", body)), paths


# --- the sources block --------------------------------------------------------

def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _pages(raws: Sequence[str]) -> str:
    """`p. 12`, `pp. 18–20`, `pp. 18–20, 31` — from the raw frontmatter strings.

    Singular only for the one case that is genuinely one page: a single entry
    with no range in it.
    """
    parts, plural = [], len(raws) > 1
    for raw in raws:
        raw = raw.strip()
        if "-" in raw:
            first, _, last = raw.partition("-")
            parts.append(f"{first.strip()}–{last.strip()}")
            plural = True
        else:
            parts.append(raw)
    return ("pp. " if plural else "p. ") + ", ".join(parts)


@dataclass
class _Document:
    """One source document and every place in it the answer drew on."""
    icon: str
    head: str                 # "Policies for Bachelor Studies v8"
    url: str = ""
    # section label -> its page strings; both in the order first cited, and the
    # empty label holds notes whose frontmatter names no section.
    sections: dict[str, list[str]] = field(default_factory=dict)


def group_sources(paths: Sequence[str],
                  snapshot: Snapshot) -> tuple[list[_Document], tuple[PdfRef, ...]]:
    """Cited note paths in, one entry per distinct source document out.

    Ten notes cut from one handbook are one document with ten section lines,
    not ten repetitions of the handbook's name. Everything here comes from
    frontmatter, so it is either true or absent — and a note whose frontmatter
    names no document is skipped entirely, because the only thing left to print
    would be its repository path.
    """
    docs: dict[str, _Document] = {}
    pdfs: list[PdfRef] = []
    for path in paths:
        note = snapshot.notes.get(path)
        src = note.source if note is not None else None
        if src is None or not (src.file or src.document):
            logger.warning("kb: cited note %r resolves to no source document",
                           path)
            continue
        key = src.file or src.document
        doc = docs.get(key)
        if doc is None:
            head = src.document or src.file
            if src.version:
                head += f" v{src.version}"
            doc = docs[key] = _Document(icon="📄" if src.is_pdf else "🌐",
                                        head=head, url=src.url)
            if src.is_pdf:
                pdfs.append(PdfRef(file=src.file,
                                   caption=src.document or src.file))
        pages = doc.sections.setdefault("; ".join(src.sections), [])
        if src.pdf_pages and src.pdf_pages not in pages:
            pages.append(src.pdf_pages)
    return list(docs.values()), tuple(pdfs)


def _document_lines(doc: _Document) -> list[str]:
    lines = [f"{doc.icon} {_esc(doc.head)}"]
    labels = list(doc.sections)
    for label in labels[:MAX_SECTIONS]:
        bits = []
        if label:
            bits.append("§" + _esc(label))
        if doc.sections[label]:
            bits.append(_pages(doc.sections[label]))
        if bits:
            lines.append(INDENT + " — ".join(bits))
    hidden = len(labels) - MAX_SECTIONS
    if hidden > 0:
        lines.append(f"{INDENT}… and {_count(hidden, 'more section')}")
    if doc.url:
        lines.append(INDENT + _esc(doc.url))
    return lines


def sources_block(paths: Sequence[str],
                  snapshot: Snapshot) -> tuple[str, tuple[PdfRef, ...]]:
    """The whole provenance block, and the PDFs it asks to be attached."""
    docs, pdfs = group_sources(paths, snapshot)
    lines: list[str] = []
    for doc in docs[:MAX_DOCUMENTS]:
        lines.extend(_document_lines(doc))
    hidden = len(docs) - MAX_DOCUMENTS
    if hidden > 0:
        lines.append(f"… and {_count(hidden, 'more document')}")
    return "\n".join(lines), pdfs


# --- the metrics line ---------------------------------------------------------

def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _tokens(number: int) -> str:
    return f"{number / 1000:.1f}k" if number >= 1000 else str(number)


def metrics_line(stats: Stats) -> str:
    """`3 steps · 4 tool calls · 2 notes read · 1.2k in / 310 out`.

    "notes read" rather than "notes": a bare "0 notes" reads like a fault, when
    it truthfully means the answer came from the map and the search results
    without any note being opened.
    """
    return " · ".join([
        _count(stats.steps, "step"),
        _count(stats.tool_calls, "tool call"),
        _count(stats.notes_read, "note") + " read",
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


def render(answer: str, snapshot: Snapshot) -> Rendered:
    """One HTML message, plus the PDFs the answer asked to be shown.

    Nothing about what the run cost appears here — that is `trace_message`,
    sent separately and only to an admin.
    """
    body, paths = split_sources(answer)
    block, pdfs = sources_block(paths, snapshot)
    parts = [balance(escape_subset(body)), block]
    return Rendered(html=_clip("\n\n".join(p for p in parts if p)), pdfs=pdfs)


# --- the trace, for an admin --------------------------------------------------

_ICONS = {"search_notes": "🔎", "list_notes": "📑", "read_note": "📖"}
MAX_ARG = 48


def _head(text: str, limit: int = MAX_ARG) -> str:
    """Shorten from the right: a search pattern says most in its first words."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _tail(text: str, limit: int = MAX_ARG) -> str:
    """Shorten from the left: a note path says most in its filename."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


def call_line(call: Call) -> str:
    """`🔎 search_notes "supervisor" in kb/handbooks/ — 14 hits`."""
    args = call.args if isinstance(call.args, dict) else {}
    prefix = str(args.get("path_prefix", "") or "")
    if call.name == "read_note":
        shown = _tail(str(args.get("path", "") or ""))
    elif call.name == "search_notes":
        shown = f'"{_head(str(args.get("pattern", "") or ""))}"'
        if prefix:
            shown += f" in {_tail(prefix)}"
    elif call.name == "list_notes":
        shown = _tail(prefix) if prefix else "kb/"
    else:
        shown = _head(", ".join(f"{k}={v}" for k, v in args.items()))
    line = f"{_ICONS.get(call.name, '🔧')} {call.name} {shown}".rstrip()
    return line + (f" — {call.result}" if call.result else "")


def trace_message(stats: Stats) -> str:
    """What the agent actually did, for the admin who wants to see the work.

    Plain text, never HTML: a regular expression the model searched for is as
    likely to hold a `<` as anything else, and this message is diagnostics — it
    must not become the thing that fails to send.
    """
    calls = list(stats.calls)
    lines = [call_line(c) for c in calls[:MAX_TRACE_CALLS]]
    hidden = len(calls) - MAX_TRACE_CALLS
    if hidden > 0:
        lines.append(f"… and {_count(hidden, 'more call')}")
    if not lines:
        lines.append("no tool calls — answered without reading the base")
    lines += [RULE, metrics_line(stats)]
    text = "\n".join(lines)
    if len(text) <= CLIP_LIMIT:
        return text
    # Cut the calls, never the totals: the tail is the part worth keeping.
    keep = f"{TRUNCATION_MARK}\n{RULE}\n{metrics_line(stats)}"
    return text[:CLIP_LIMIT - len(keep)] + keep
