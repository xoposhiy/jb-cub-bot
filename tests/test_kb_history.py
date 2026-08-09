"""The conversation behind a chat: pairs in, a windowed message list out."""
import pytest

from jbcub_bot.features.kb import history

CHAT = 777


@pytest.fixture(autouse=True)
def _clean():
    history.reset()
    yield
    history.reset()


def _remember(chat_id: int, *questions: str, answer: str = "a") -> None:
    for question in questions:
        history.remember(chat_id, question, answer)


def _input(chat_id: int) -> list[dict]:
    return history.as_input(history.conversation(chat_id))


# --- what accumulates ----------------------------------------------------------

def test_a_chat_starts_with_nothing_to_carry():
    assert _input(CHAT) == []


def test_pairs_accumulate_in_the_order_they_were_asked():
    history.remember(CHAT, "first?", "one.")
    history.remember(CHAT, "second?", "two.")

    assert _input(CHAT) == [
        {"role": "user", "content": "first?"},
        {"role": "assistant", "content": "one."},
        {"role": "user", "content": "second?"},
        {"role": "assistant", "content": "two."},
    ]


def test_a_conversation_is_per_chat():
    history.remember(CHAT, "mine?", "yours.")

    assert _input(888) == []


def test_dropping_forgets_the_whole_thing():
    history.remember(CHAT, "first?", "one.")

    history.drop(CHAT)

    assert _input(CHAT) == []


def test_dropping_a_chat_that_never_spoke_is_not_an_error():
    history.drop(CHAT)  # no exception is the assertion


# --- the window ------------------------------------------------------------------

def _big(marker: str) -> str:
    """A pair's worth of text, sized so three of them overflow the window."""
    return marker + "x" * (history.WINDOW_CHARS // 2 - 1)


def test_the_window_drops_the_oldest_pairs_first():
    history.remember(CHAT, _big("one"), "a")
    history.remember(CHAT, _big("two"), "a")
    history.remember(CHAT, _big("three"), "a")

    kept = "".join(item["content"] for item in _input(CHAT))
    assert "one" not in kept
    assert kept.count("three") == 1


def test_the_window_drops_whole_pairs_and_never_half_of_one():
    """An answer with no question above it reads as something the bot
    volunteered."""
    history.remember(CHAT, _big("one"), "first answer")
    history.remember(CHAT, _big("two"), "second answer")
    history.remember(CHAT, _big("three"), "third answer")

    roles = [item["role"] for item in _input(CHAT) if item["content"] !=
             history.ELISION]
    assert roles == ["user", "assistant"] * (len(roles) // 2)
    assert "first answer" not in str(_input(CHAT))


def test_the_pair_that_just_happened_is_kept_however_long_it_is():
    """Dropping it would leave the next question with no context at all, which
    is worse than one oversized prefix."""
    history.remember(CHAT, "x" * (history.WINDOW_CHARS * 2), "a")

    assert len(_input(CHAT)) == 2


def test_a_conversation_inside_the_window_is_carried_whole():
    _remember(CHAT, "first?", "second?", "third?")

    assert len(_input(CHAT)) == 6


# --- the elision note --------------------------------------------------------------

def test_nothing_dropped_means_no_elision_note():
    _remember(CHAT, "first?", "second?")

    assert history.ELISION not in str(_input(CHAT))


def test_the_note_appears_first_once_a_pair_has_fallen_out():
    history.remember(CHAT, _big("one"), "a")
    history.remember(CHAT, _big("two"), "a")
    history.remember(CHAT, _big("three"), "a")

    assert _input(CHAT)[0] == {"role": "user", "content": history.ELISION}


def test_the_note_is_written_once_however_many_pairs_fell_out():
    for marker in ("one", "two", "three", "four", "five"):
        history.remember(CHAT, _big(marker), "a")

    assert str(_input(CHAT)).count(history.ELISION) == 1


def test_a_dropped_conversation_forgets_that_anything_was_elided():
    history.remember(CHAT, _big("one"), "a")
    history.remember(CHAT, _big("two"), "a")
    history.remember(CHAT, _big("three"), "a")

    history.drop(CHAT)
    history.remember(CHAT, "fresh?", "a")

    assert history.ELISION not in str(_input(CHAT))


# --- what is never stored ------------------------------------------------------------

def test_only_the_question_and_the_answer_are_ever_carried():
    """The run's own input list is what this replaces: one `read_note` in it is
    up to 20 000 characters of note text, and none of it is worth re-sending."""
    history.remember(CHAT, "retakes?", "Once.")

    carried = _input(CHAT)
    assert {item["role"] for item in carried} == {"user", "assistant"}
    assert [item["content"] for item in carried] == ["retakes?", "Once."]


# --- the bound on chats ----------------------------------------------------------------

def test_the_least_recently_used_chat_is_the_one_that_goes():
    for chat_id in range(history.MAX_CHATS + 1):
        history.remember(chat_id, "q", "a")

    assert _input(0) == [], "the oldest conversation was dropped"
    assert len(_input(history.MAX_CHATS)) == 2


def test_asking_after_a_conversation_counts_as_using_it():
    for chat_id in range(history.MAX_CHATS):
        history.remember(chat_id, "q", "a")

    history.conversation(0)  # chat 0 is now the most recent, not the oldest
    history.remember(history.MAX_CHATS, "q", "a")

    assert len(_input(0)) == 2
    assert _input(1) == []


# --- what a conversation carries besides its pairs ----------------------------------------

def test_the_documents_already_sent_are_remembered_with_the_conversation():
    """A source is attached once per conversation, so a fresh one is when the
    reader may need it again."""
    history.conversation(CHAT).sent_sources.append("sources/policies/v8.pdf")

    assert history.conversation(CHAT).sent_sources == \
        ["sources/policies/v8.pdf"]

    history.drop(CHAT)

    assert history.conversation(CHAT).sent_sources == []
