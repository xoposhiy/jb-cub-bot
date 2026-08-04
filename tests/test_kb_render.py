"""The admin's trace, and the two fallbacks for sending an answer.

The answer itself is not rendered any more — it goes out as the agent wrote it —
so what is left to test here is the diagnostics an admin reads and the two
things that fire only after Telegram has already said no.
"""
from dataclasses import dataclass, field

from jbcub_bot.features.kb import render


@dataclass(frozen=True)
class FakeCall:
    name: str = "read_note"
    args: dict = field(default_factory=dict)
    result: str = ""


@dataclass(frozen=True)
class FakeStats:
    steps: int = 3
    tool_calls: int = 4
    notes_read: int = 2
    input_tokens: int = 1200
    output_tokens: int = 310
    calls: tuple = ()


# --- the two last resorts -----------------------------------------------------

def test_an_answer_within_the_limit_is_passed_through_untouched():
    """The whole point of the rewrite: the agent's words are not edited."""
    answer = "Retakes are allowed once.\n📄 Policies — §III.4, pp. 18–20"

    assert render.clip(answer) == answer


def test_an_over_long_answer_is_cut_to_what_telegram_accepts():
    out = render.clip("x" * 5000)

    assert len(out) <= render.CLIP_LIMIT
    assert out.endswith(render.TRUNCATION_MARK)


def test_plain_strips_every_tag_not_just_the_allowed_ones():
    """It is called because Telegram refused the markup, so the offending tag
    is by definition one the allow-list does not know."""
    out = render.plain("<b>Bold</b> and <span class=x>odd</span>")

    assert out == "Bold and odd"


# --- the metrics line ---------------------------------------------------------

def test_the_metrics_line_reports_all_five_numbers():
    line = render.metrics_line(FakeStats())

    assert "3 steps" in line
    assert "4 tool calls" in line
    assert "2 notes read" in line
    assert "1.2k in" in line
    assert "310 out" in line


def test_one_of_something_is_singular():
    line = render.metrics_line(FakeStats(steps=1, tool_calls=1, notes_read=1))

    assert "1 step ·" in line
    assert "1 tool call ·" in line
    assert "1 note read ·" in line


def test_reading_nothing_says_so_rather_than_looking_broken():
    """A bare "0 notes" reads as a fault; it is a legitimate outcome."""
    line = render.metrics_line(FakeStats(notes_read=0))

    assert "0 notes read" in line



# --- the trace ----------------------------------------------------------------

def _stats(*calls) -> FakeStats:
    return FakeStats(calls=tuple(calls))


def test_the_trace_names_every_tool_and_what_it_returned():
    text = render.trace_message(_stats(
        FakeCall("search_notes", {"pattern": "supervisor"}, "0 hits"),
        FakeCall("read_note", {"path": "kb/p.md"}, "8.1k chars"),
    ))

    assert '🔎 search_notes "supervisor" — 0 hits' in text
    assert "📖 read_note kb/p.md — 8.1k chars" in text


def test_the_trace_shows_a_search_prefix_and_defaults_a_listing_to_the_root():
    text = render.trace_message(_stats(
        FakeCall("search_notes", {"pattern": "thesis",
                                  "path_prefix": "kb/handbooks/"}, "14 hits"),
        FakeCall("list_notes", {}, "31 hits"),
    ))

    assert '"thesis" in kb/handbooks/' in text
    assert "📑 list_notes kb/ — 31 hits" in text


def test_the_trace_ends_with_the_totals():
    text = render.trace_message(_stats(FakeCall("list_notes", {}, "3 hits")))

    assert text.splitlines()[-1] == render.metrics_line(FakeStats())
    assert render.RULE in text


def test_a_run_that_called_nothing_says_so_rather_than_showing_a_bare_rule():
    text = render.trace_message(FakeStats())

    assert "no tool calls" in text


def test_a_long_search_pattern_is_cut_from_the_right():
    text = render.trace_message(_stats(
        FakeCall("search_notes", {"pattern": "supervisor " * 20}, "0 hits")))

    assert "…" in text
    assert text.startswith('🔎 search_notes "supervisor supervisor')


def test_a_long_note_path_keeps_its_filename():
    path = "kb/handbooks/" + "nested/" * 8 + "07-thesis.md"

    text = render.trace_message(_stats(
        FakeCall("read_note", {"path": path}, "2.0k chars")))

    assert "07-thesis.md" in text
    assert "…" in text


def test_a_call_whose_output_never_arrived_still_appears():
    """A cut-short run leaves its last call unpaired; the call itself is the
    thing worth seeing."""
    text = render.trace_message(_stats(FakeCall("read_note", {"path": "kb/p.md"})))

    assert "📖 read_note kb/p.md" in text
    assert "—" not in text.splitlines()[0]


def test_the_trace_survives_a_pattern_full_of_angle_brackets():
    text = render.trace_message(_stats(
        FakeCall("search_notes", {"pattern": "<b>&x"}, "0 hits")))

    assert "<b>&x" in text, "plain text, so nothing needs escaping"


def test_the_trace_names_the_sources_the_agent_chose():
    """The follow-up turn is part of what the question cost, so the admin sees
    it alongside the reads it chose among."""
    text = render.trace_message(
        _stats(FakeCall("choose_sources", {"numbers": [1, 3]}, "2 hits")))

    assert "📎 choose_sources 1, 3" in text


def test_the_trace_says_so_when_the_agent_chose_no_source():
    text = render.trace_message(
        _stats(FakeCall("choose_sources", {"numbers": []}, "0 hits")))

    assert "📎 choose_sources none" in text


def test_the_trace_shows_what_the_check_complained_about():
    """Whether the agent then fixed it or stood its ground is the thing worth
    watching while the prompt is still settling — and it belongs to the admin,
    not the reader."""
    text = render.trace_message(
        _stats(FakeCall("read_note", {"path": "kb/p.md"}, "2.0k chars")),
        ["The answer names kb/p.md."])

    assert "⚠ The answer names kb/p.md." in text


def test_a_runaway_trace_keeps_its_totals():
    calls = [FakeCall("read_note", {"path": f"kb/n{i}.md"}, "1.0k chars")
             for i in range(40)]

    text = render.trace_message(_stats(*calls))

    assert len(text.splitlines()) == render.MAX_TRACE_CALLS + 3
    assert "… and 20 more calls" in text
    assert text.endswith(render.metrics_line(FakeStats()))


def test_a_smaller_limit_still_keeps_the_totals():
    """The ops log puts this under a question, so it gets less than a whole
    message — and the totals are the part it is there for."""
    calls = [FakeCall("read_note", {"path": f"kb/n{i}.md"}, "1.0k chars")
             for i in range(20)]

    text = render.trace_message(_stats(*calls), limit=300)

    assert len(text) <= 300
    assert text.endswith(render.metrics_line(FakeStats()))
