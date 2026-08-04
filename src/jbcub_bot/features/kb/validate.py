"""What is wrong with an answer, said in words the agent can act on.

This module never changes an answer. It only describes what it finds, and the
agent is free to disagree: an answer the check dislikes still goes out if the
agent stands by it on the second pass. That is the whole point of the split.
The bot used to rewrite the model's prose instead -- sweeping repository paths
out of sentences, patching what the sweep left behind -- and each patch was a
new thing to break, in the reader's message, where a break is worst.

So every check here earns its place by naming a consequence the reader would
otherwise see: a path that means nothing to them, or a message Telegram refuses
outright.
"""
from __future__ import annotations

import re

# Telegram's own ceiling on a text message.
LIMIT = 4096
# The four tags Telegram accepts and this bot asks the agent to keep to.
ALLOWED = ("b", "i", "code", "blockquote")

_TAG = re.compile(r"<\s*(/?)\s*([a-zA-Z][\w-]*)[^>]*>")
_KB_PATH = re.compile(r"\bkb/[\w./-]+")


def _markup(answer: str) -> list[str]:
    """Tag trouble Telegram would reject the whole message over.

    A rejected message costs the reader the answer entirely, so this is the
    check most worth a second pass. The stack is the point: `<b>` opened and
    never closed reads fine to a person and is fatal to Telegram.
    """
    found: list[str] = []
    stack: list[str] = []
    # Tags a crossing already had to force shut. Their own closer turns up
    # later and closes nothing, which is a consequence of the complaint
    # already made rather than a second thing wrong.
    forced: list[str] = []
    for match in _TAG.finditer(answer):
        closing, name = match.group(1) == "/", match.group(2).lower()
        if name not in ALLOWED:
            note = (f"<{name}> is not a tag Telegram accepts here. Only "
                    f"{', '.join(f'<{t}>' for t in ALLOWED)} are allowed, and "
                    "a literal < in quoted text has to be written &lt;.")
            if note not in found:
                found.append(note)
            continue
        if not closing:
            stack.append(name)
        elif stack and stack[-1] == name:
            stack.pop()
        elif name in stack:
            inner = stack[-1]
            found.append(f"</{name}> closes <{name}> while <{inner}> is still "
                         f"open inside it. Close <{inner}> first.")
            while stack:
                top = stack.pop()
                if top == name:
                    break
                forced.append(top)
        elif name in forced:
            forced.remove(name)
        else:
            found.append(f"</{name}> closes a tag that was never opened.")
    for name in stack:
        found.append(f"<{name}> is opened and never closed.")
    return found


def complaints(answer: str) -> list[str]:
    """Everything wrong with this answer, or an empty list."""
    found: list[str] = []
    paths = sorted(set(_KB_PATH.findall(answer)))
    if paths:
        found.append(
            f"The answer names {', '.join(paths)}. The reader has never seen "
            "the knowledge base and those paths mean nothing to them — name "
            "the document the note reproduces instead."
        )
    found.extend(_markup(answer))
    if len(answer) > LIMIT:
        found.append(f"The answer is {len(answer)} characters and Telegram "
                     f"takes {LIMIT}. It has to be shorter.")
    return found


def feedback(found: list[str]) -> str:
    """The one message that carries the complaints back to the agent.

    It says outright that the check can be wrong, because sometimes it is, and
    an agent bullied into rewriting a good answer is a worse outcome than the
    thing being complained about.
    """
    listed = "\n".join(f"- {c}" for c in found)
    return ("Before this answer goes to the reader, an automatic check "
            f"flagged it:\n{listed}\n\n"
            "Send a corrected answer if the check is right. If you judge it "
            "wrong, send your answer again unchanged and it will go out as it "
            "is — the check is advice, not a rule, and this is the only time "
            "you will be asked.")
