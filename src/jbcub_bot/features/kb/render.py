"""The admin trace, and the two last resorts for sending an answer.

What this module no longer does is the point of it. The agent's answer used to
be taken apart here — its citation footer parsed, repository paths swept out of
its sentences, punctuation tidied afterwards, a provenance block bolted on from
frontmatter — and every one of those steps was a thing that could go wrong in
the reader's message. One of them did: a citation footer written in Russian was
swept of its paths and left the label standing over a row of commas.

So the answer is now sent word for word. The agent writes its own citations,
asks for its own attachments through a tool, and `validate` tells it once what
looks wrong. Nothing here touches its prose. What is left is the trace an admin
sees, and two fallbacks that fire only when Telegram has already refused: `clip`
for a message over the limit, and `plain` for markup it would not parse.
"""
from __future__ import annotations

import re
from typing import Protocol, Sequence

CLIP_LIMIT = 4096  # Telegram's own limit on a text message
MAX_TRACE_CALLS = 20
TRUNCATION_MARK = "\n[… truncated]"
RULE = "─" * 13

_ANY_TAG = re.compile(r"<[^>]*>")


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


# --- the two last resorts -----------------------------------------------------

def clip(text: str) -> str:
    """Cut to what Telegram accepts.

    `validate` already asks the agent for something shorter, so reaching this
    means the agent stood by a message too long to send. Cutting mid-tag can
    leave markup unbalanced, and Telegram then rejects it and `plain` sends the
    words instead — a worse-looking answer, never a lost one.
    """
    if len(text) <= CLIP_LIMIT:
        return text
    return text[:CLIP_LIMIT - len(TRUNCATION_MARK)] + TRUNCATION_MARK


def plain(text: str) -> str:
    """The same words with no markup, for when Telegram refused the HTML.

    Every tag goes, not just the four that are allowed: the reason this is
    being called is that something in there was not one of them.
    """
    return _ANY_TAG.sub("", text)


# --- the trace, for an admin --------------------------------------------------

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


_ICONS = {"search_notes": "🔎", "list_notes": "📑", "read_note": "📖",
          "choose_sources": "📎"}
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
    elif call.name == "choose_sources":
        numbers = args.get("numbers")
        shown = (", ".join(str(n) for n in numbers) if isinstance(numbers, list)
                 else "") or "none"
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


def trace_message(stats: Stats, complaints: Sequence[str] = (),
                  limit: int = CLIP_LIMIT) -> str:
    """What the agent actually did, for the admin who wants to see the work.

    `complaints` is what the check said about the first answer, if it said
    anything. It belongs here rather than in the reader's message: whether the
    agent then fixed it or stood its ground is exactly the kind of thing worth
    watching while the prompt is still settling.

    `limit` is what the caller has room for. The ops log puts this under a
    question and a sender line, so it gets less than a whole message — but the
    tail-first cut below is the same either way, and belongs in one place.

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
    for complaint in complaints:
        lines.append(f"⚠ {complaint}")
    lines += [RULE, metrics_line(stats)]
    text = "\n".join(lines)
    if len(text) <= limit:
        return text
    # Cut the calls, never the totals: the tail is the part worth keeping.
    keep = f"{TRUNCATION_MARK}\n{RULE}\n{metrics_line(stats)}"
    return text[:max(0, limit - len(keep))] + keep
