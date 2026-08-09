"""One conversation per chat, kept as question/answer pairs.

A conversation is what the reader said and what the bot said back, and nothing
else. Not the run's own input list: that carries tool calls, tool outputs and
reasoning items, and one `read_note` in it is up to 20 000 characters of note
text — carrying it forward is what forced the old twelve-question cap. It also
has no boundaries to cut a window at, because `validate`'s complaint arrives in
the `user` role too and reads exactly like a question.

So a follow-up re-reads the note it needs. That costs a turn and buys three
things: the citation line in the previous answer says where to look, the new
answer rests on freshly read text rather than on a remembered read, and a
`/kb_reload` in the middle of a conversation stops leaving stale notes in view.

It lives in memory, like `core/impersonation.py`'s `_active`, and dies on
redeploy — which the knowledge base already accepts for conversations.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

# Big enough that an ordinary conversation never reaches it, small enough that
# a very long one cannot grow without bound. Characters rather than tokens
# because the pairs are plain text and a character count needs no tokenizer.
WINDOW_CHARS = 40_000
# Chats, not conversations: the least recently used goes when there are more.
# A bound rather than a clock -- two people talking for an hour is not a
# problem to solve.
MAX_CHATS = 200
ELISION = "[Earlier questions in this conversation are no longer shown.]"


@dataclass(frozen=True)
class Pair:
    """One exchange. `question` carries its stamp, exactly as it was sent."""
    question: str
    answer: str

    @property
    def size(self) -> int:
        return len(self.question) + len(self.answer)


@dataclass
class Conversation:
    """What one chat is in the middle of.

    `sent_sources` is here rather than beside it because attaching a document
    twice is the same waste a repeated question is: the reader has it in this
    conversation, and a fresh conversation is when they may need it again.
    """
    pairs: list[Pair] = field(default_factory=list)
    # True once the window dropped something, which is what the elision note
    # tells the agent. A flag rather than a recount: the pairs that fell out
    # are gone, so nothing later can tell that they existed.
    elided: bool = False
    sent_sources: list[str] = field(default_factory=list)


# Process-wide, like the runtime and the rate-limit window: there is one bot.
_CHATS: OrderedDict[int, Conversation] = OrderedDict()


def conversation(chat_id: int) -> Conversation:
    """This chat's conversation, started if there is none.

    Asking counts as use, so the chat goes to the back of the eviction queue.
    """
    if chat_id in _CHATS:
        _CHATS.move_to_end(chat_id)
    else:
        _CHATS[chat_id] = Conversation()
        _evict()
    return _CHATS[chat_id]


def as_input(chat: Conversation) -> list[dict]:
    """The conversation as the messages a run is prefixed with.

    The elision note goes first and in the `user` role, where the agent will
    read it before the oldest question it still has — a conversation that
    looks complete but is not is how a follow-up gets answered as if the thing
    it refers to had never been said.
    """
    messages: list[dict] = []
    if chat.elided:
        messages.append({"role": "user", "content": ELISION})
    for pair in chat.pairs:
        messages.append({"role": "user", "content": pair.question})
        messages.append({"role": "assistant", "content": pair.answer})
    return messages


def remember(chat_id: int, question: str, answer: str) -> None:
    """Add one exchange and trim the window back to size.

    Trimmed on the way in rather than on the way out, so a chat's memory is
    bounded by what is kept rather than by what was ever said.
    """
    chat = conversation(chat_id)
    chat.pairs.append(Pair(question, answer))
    # Whole pairs only, oldest first. Half an exchange is worse than none: an
    # answer with no question above it reads as something the bot volunteered.
    # The newest pair always stays, however long it is -- dropping the exchange
    # that just happened would leave the next question with no context at all.
    while len(chat.pairs) > 1 and _size(chat.pairs) > WINDOW_CHARS:
        chat.pairs.pop(0)
        chat.elided = True


def drop(chat_id: int) -> None:
    """Forget this chat's conversation. The reader changed the subject."""
    _CHATS.pop(chat_id, None)


def reset() -> None:
    """Test seam: forget every chat."""
    _CHATS.clear()


def _size(pairs: list[Pair]) -> int:
    return sum(pair.size for pair in pairs)


def _evict() -> None:
    while len(_CHATS) > MAX_CHATS:
        _CHATS.popitem(last=False)
