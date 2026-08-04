"""The agent, driven by a stub model.

The framework owns the tool loop, so the seam is the model: a stub that returns
scripted responses proves the wiring without a network call or an API key.
"""
import pytest
from agents import ModelResponse, RunContextWrapper
from agents.items import TResponseOutputItem
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from jbcub_bot.core.kb_snapshot import Note, Snapshot, Source
from jbcub_bot.features.kb import agent as kb_agent
from jbcub_bot.features.kb import tools, validate


def _snapshot() -> Snapshot:
    return Snapshot(sha="abc123", repo="xoposhiy/cub-kb", notes={
        "kb/policies/exams.md": Note(
            path="kb/policies/exams.md",
            text="Retakes are allowed once.\n",
            title="Exam rules", description="How retakes work."),
    })


def _text(body: str) -> TResponseOutputItem:
    return ResponseOutputMessage(
        id="msg-1", type="message", role="assistant", status="completed",
        content=[ResponseOutputText(type="output_text", text=body,
                                    annotations=[])],
    )


# Distinct ids: the framework pairs a tool output back to its call, so two
# calls sharing an id in one response are matched wrongly.
_CALL_IDS = iter(range(1, 10_000))


def _call(name: str, arguments: str) -> TResponseOutputItem:
    return ResponseFunctionToolCall(type="function_call", name=name,
                                    arguments=arguments,
                                    call_id=f"call-{next(_CALL_IDS)}")


# Both stubs subclass the framework's Model: Agent type-checks its `model`
# against that interface, so a bare duck-type is rejected before any run starts.
class StubModel(Model):
    """Returns the scripted responses in order; repeats the last one forever."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        self.calls += 1
        items = self.script[min(self.calls - 1, len(self.script) - 1)]
        # A real model reports one request and its tokens per response, and the
        # framework sums them; a bare Usage() would make every count read zero.
        return ModelResponse(
            output=list(items),
            usage=Usage(requests=1, input_tokens=600, output_tokens=155),
            response_id=f"resp-{self.calls}")

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError("the bot never streams")


class ExplodingModel(Model):
    async def get_response(self, *args, **kwargs):
        raise RuntimeError("endpoint is down")

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


def _agent(model):
    return kb_agent.build_agent("stub-model", client=None, model=model)


_CLEAN = "Retakes are allowed once.\n📄 Exam rules — §III.4, pp. 18–20"


async def test_a_tool_call_sequence_reaches_an_answer():
    model = StubModel([
        [_call("read_note", '{"path": "kb/policies/exams.md"}')],
        [_text(_CLEAN)],
        # It read a note, so the question about sources follows.
        [_call("choose_sources", '{"numbers": [1]}')],
        [_text("done")],
    ])

    out = await kb_agent.ask(_agent(model), _snapshot(),
                             "How many retakes?", [])

    assert out.text == _CLEAN, "the agent's words, unedited"
    assert model.calls == 4, "two turns to answer, two to name the source"
    assert out.history, "the run's input list carries the session forward"
    assert out.complaints == (), "no complaint, so no reconsidering"
    assert out.stats.steps == 4
    assert out.stats.tool_calls == 2
    assert out.stats.notes_read == 1
    assert out.stats.input_tokens == 2400, "tokens sum across every turn"
    assert out.stats.output_tokens == 620


async def test_reading_one_note_twice_counts_one_note():
    model = StubModel([
        [_call("read_note", '{"path": "kb/policies/exams.md"}'),
         _call("read_note", '{"path": "kb/policies/exams.md"}'),
         _call("list_notes", '{"path_prefix": "kb/"}')],
        [_text("Retakes are allowed once.")],
    ])

    stats = (await kb_agent.ask(_agent(model), _snapshot(), "q", [])).stats

    assert stats.tool_calls == 3
    assert stats.notes_read == 1


async def test_every_call_is_recorded_with_its_arguments_and_its_result():
    """The admin trace is built from these, so the pairing is what matters:
    the framework emits a turn's calls and then that turn's outputs."""
    model = StubModel([
        [_call("search_notes", '{"pattern": "retake"}'),
         _call("read_note", '{"path": "kb/policies/exams.md"}')],
        [_text("Retakes are allowed once.")],
    ])

    stats = (await kb_agent.ask(_agent(model), _snapshot(), "q", [])).stats

    assert [(c.name, c.args, c.result) for c in stats.calls] == [
        ("search_notes", {"pattern": "retake"}, "1 hit"),
        ("read_note", {"path": "kb/policies/exams.md"}, "26 chars"),
    ]


async def test_a_call_the_model_mangled_is_recorded_with_no_arguments():
    model = StubModel([
        [_call("list_notes", "not json at all")],
        [_text("done")],
    ])

    stats = (await kb_agent.ask(_agent(model), _snapshot(), "q", [])).stats

    assert stats.calls[0].args == {}
    assert stats.tool_calls == 1


async def test_a_model_that_never_stops_is_cut_and_says_so():
    model = StubModel([[_call("list_notes", '{"path_prefix": "kb/"}')]])

    out = await kb_agent.ask(_agent(model), _snapshot(), "hi", [])

    assert out.text == kb_agent.CUT_SHORT
    assert model.calls == kb_agent.MAX_TURNS
    assert out.history == [], "an abandoned run must not pollute the session"
    assert out.stats.tool_calls > 0, "a cut-short run still reports its burn"


async def test_a_raising_tool_comes_back_as_an_error_not_a_crash(monkeypatch):
    def boom(snapshot, path):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(kb_agent.tools, "read_note", boom)
    model = StubModel([
        [_call("read_note", '{"path": "kb/policies/exams.md"}')],
        [_text("I could not read that note.")],
    ])

    out = await kb_agent.ask(_agent(model), _snapshot(), "retakes?", [])

    assert "could not read" in out.text


async def test_an_endpoint_failure_propagates():
    with pytest.raises(RuntimeError, match="endpoint is down"):
        await kb_agent.ask(_agent(ExplodingModel()), _snapshot(), "hi", [])


def test_the_prompt_asks_for_brevity_and_grounding():
    rules = kb_agent.SYSTEM_RULES

    assert "three sentences" in rules
    assert "<blockquote>" in rules
    assert "Answer only from notes you actually read" in rules


def test_the_prompt_names_exactly_the_tags_the_check_accepts():
    """A coupling, not a style rule: the prompt promises the agent a set of
    tags and `validate` rejects everything outside it. The two lists drifting
    apart costs the reader a whole message."""
    rules = kb_agent.SYSTEM_RULES

    for tag in validate.ALLOWED:
        assert f"<{tag}>" in rules
    assert "<u>" not in rules, "promising a tag the check will reject"


def test_the_prompt_points_at_the_marker_read_note_really_emits():
    """The agent is told to cite from the [source: …] line, so the prompt is
    wrong the moment `source_hint` stops writing one."""
    note = Note(path="kb/p.md", text="body",
                source=Source(document="Policies", pdf_pages="18-20"))

    assert tools.source_hint(note).startswith("[source:")
    assert "[source: …]" in kb_agent.SYSTEM_RULES


def test_the_prompt_says_the_sources_question_comes_separately():
    """Otherwise the agent tries to manage attachments while answering, which
    is the arrangement that skipped them three times out of four."""
    rules = " ".join(kb_agent.SYSTEM_RULES.split())

    assert "you will be asked, separately, which of the notes" in rules


def test_the_prompt_keeps_citations_in_the_sources_own_language():
    """A translated document title matches nothing the reader can open."""
    rules = " ".join(kb_agent.SYSTEM_RULES.split())

    assert "Leave the citation in the original language" in rules


def test_the_prompt_says_how_to_escape_a_literal_angle_bracket():
    """Nothing escapes the answer for it now, and one stray < costs the reader
    the whole message."""
    assert "&lt;" in kb_agent.SYSTEM_RULES


def test_the_prompt_asks_for_the_key_words_to_be_bolded_inside_the_quote():
    rules = kb_agent.SYSTEM_RULES

    assert "<b>" in rules
    assert "bold" in rules.lower()


def test_the_prompt_releases_a_broad_question_from_the_quote_rule():
    """Forcing a quote out of an answer assembled from twenty notes produces
    an unrepresentative one, which is worse than none."""
    rules = kb_agent.SYSTEM_RULES.lower()

    assert "prerequisite" in rules, "the prompt names the shape of such a question"
    assert "skip the quotation" in rules


def test_the_prompt_forbids_naming_the_knowledge_base_to_the_reader():
    """Paired with the check in `validate`: one asks, the other complains when
    the asking did not take."""
    rules = " ".join(kb_agent.SYSTEM_RULES.split()).lower()

    assert "never name a note, a folder, a file or a path from it" in rules
    assert validate.complaints("See kb/policies/exams.md."), "and it is checked"


def test_the_prompt_shows_an_answer_that_cites_nothing():
    """An unanswerable question that still drags a citation behind it is the
    thing this shape exists to prevent."""
    rules = kb_agent.SYSTEM_RULES

    assert "No document answers it" in rules
    assert "no citation line whatsoever" in rules


async def test_who_is_asking_reaches_the_prompt():
    ctx = RunContextWrapper(kb_agent.Ask(snapshot=_snapshot(),
                                         about="role: Student · cohort: 2024"))

    text = kb_agent.instructions(ctx, None)

    assert "cohort: 2024" in text
    assert "kb/policies/" in text, "the map of the base is still there"


async def test_the_prompt_maps_folders_and_never_filenames():
    """The map is what an idle chat pays for before it has asked anything, so
    it stays a line per document however many notes the base grows."""
    ctx = RunContextWrapper(kb_agent.Ask(snapshot=_snapshot()))

    text = kb_agent.instructions(ctx, None)

    assert "exams.md" not in text
    assert "kb/policies/ (1 note)" in text
    assert "list_notes" in text, "and it says how to get the filenames"


async def test_an_anonymous_run_says_nothing_about_the_asker():
    ctx = RunContextWrapper(kb_agent.Ask(snapshot=_snapshot()))

    assert "The person asking" not in kb_agent.instructions(ctx, None)


# --- the one round of feedback ------------------------------------------------

async def test_an_answer_that_leaks_a_path_is_handed_back_once():
    model = StubModel([
        [_text("The rule is in kb/policies/exams.md.")],
        [_text(_CLEAN)],
    ])

    out = await kb_agent.ask(_agent(model), _snapshot(), "q", [])

    assert out.text == _CLEAN, "the second answer is the one that goes out"
    assert model.calls == 2
    assert any("kb/policies/exams.md" in c for c in out.complaints)


async def test_the_agent_may_stand_by_its_answer_and_it_goes_out():
    """The check is advice. An agent that repeats itself has made a decision,
    and nothing here overrules it -- that is the whole point of dropping the
    post-processing."""
    stubborn = "The rule is in kb/policies/exams.md."
    model = StubModel([[_text(stubborn)]])

    out = await kb_agent.ask(_agent(model), _snapshot(), "q", [])

    assert out.text == stubborn, "sent as written, path and all"
    assert model.calls == 2, "asked once, never twice"


async def test_a_second_pass_is_never_itself_re_checked():
    """Otherwise a stubborn agent and a stubborn check would ping-pong for the
    whole turn budget."""
    model = StubModel([[_text("still kb/policies/exams.md")]])

    out = await kb_agent.ask(_agent(model), _snapshot(), "q", [])

    assert model.calls == 2
    assert len(out.complaints) == 1


async def test_every_pass_is_counted_in_one_line_of_statistics():
    model = StubModel([
        [_call("read_note", '{"path": "kb/policies/exams.md"}')],
        [_text("in kb/policies/exams.md")],  # trips the check
        [_text(_CLEAN)],                     # the reconsidered answer
        [_call("choose_sources", '{"numbers": [1]}')],
        [_text("done")],
    ])

    out = await kb_agent.ask(_agent(model), _snapshot(), "q", [])

    assert out.stats.steps == 5, "answer, reconsider, then sources"
    assert out.stats.notes_read == 1, "the same note read twice is one note"
    assert out.stats.input_tokens == 3000


async def test_a_cut_short_second_pass_keeps_the_first_whole_answer():
    """An answer with a stray path still beats the "I ran out of steps" line."""
    leaky = "The rule is in kb/policies/exams.md."
    model = StubModel([
        [_text(leaky)],
        [_call("list_notes", '{"path_prefix": "kb/"}')],  # loops until cut off
    ])

    out = await kb_agent.ask(_agent(model), _snapshot(), "q", [])

    assert out.text == leaky
    assert out.text != kb_agent.CUT_SHORT


# --- the separate question about sources ---------------------------------------

def _sourced() -> Snapshot:
    return Snapshot(sha="abc123", repo="r", notes={
        "kb/p.md": Note(path="kb/p.md", text="body", title="Grading",
                        source=Source(file="sources/policies/v8.pdf",
                                      document="Policies for Bachelor Studies",
                                      sections=("III.4 Grading",),
                                      pdf_pages="18-20")),
        "kb/c.md": Note(path="kb/c.md", text="body", title="Fall 2026",
                        source=Source(file="sources/calendars/2026.html",
                                      document="Academic Calendar 2026/2027",
                                      url="https://example.org/cal")),
        "kb/bare.md": Note(path="kb/bare.md", text="body", title="Loose"),
    })


def _reads_then_picks(read: str, numbers: str) -> StubModel:
    """Answer off one note, then answer the question about sources."""
    return StubModel([
        [_call("read_note", '{"path": "%s"}' % read)],
        [_text(_CLEAN)],
        [_call("choose_sources", '{"numbers": %s}' % numbers)],
        [_text("done")],
    ])


async def test_the_picked_note_becomes_the_source_the_reader_is_given():
    out = await kb_agent.ask(_agent(_reads_then_picks("kb/p.md", "[1]")),
                             _sourced(), "q", [])

    assert [(s.caption, s.is_pdf) for s in out.sources] == [
        ("Policies for Bachelor Studies", True)]


async def test_a_web_source_carries_its_address_out_of_the_frontmatter():
    """The whole reason the code resolves the choice rather than the agent: the
    [source: …] line it reads has no address in it at all."""
    out = await kb_agent.ask(_agent(_reads_then_picks("kb/c.md", "[1]")),
                             _sourced(), "q", [])

    assert [(s.url, s.is_pdf) for s in out.sources] == [
        ("https://example.org/cal", False)]


async def test_only_the_notes_it_picked_are_shown_not_everything_it_read():
    model = StubModel([
        [_call("read_note", '{"path": "kb/p.md"}'),
         _call("read_note", '{"path": "kb/c.md"}')],
        [_text(_CLEAN)],
        [_call("choose_sources", '{"numbers": [2]}')],
        [_text("done")],
    ])

    out = await kb_agent.ask(_agent(model), _sourced(), "q", [])

    assert [s.caption for s in out.sources] == ["Academic Calendar 2026/2027"]


async def test_picking_nothing_shows_nothing():
    out = await kb_agent.ask(_agent(_reads_then_picks("kb/p.md", "[]")),
                             _sourced(), "q", [])

    assert out.sources == ()


async def test_an_answer_that_read_nothing_is_never_asked_about_sources():
    """A chat that opened no note has nothing to choose among, and the question
    would be a round trip spent on an empty list."""
    model = StubModel([[_text("Hello!")]])

    out = await kb_agent.ask(_agent(model), _sourced(), "hi", [])

    assert out.sources == ()
    assert model.calls == 1, "answered and stopped"


async def test_a_note_read_but_missing_is_not_offered_as_a_source():
    model = StubModel([
        [_call("read_note", '{"path": "kb/nope.md"}')],
        [_text(_CLEAN)],
    ])

    out = await kb_agent.ask(_agent(model), _sourced(), "q", [])

    assert out.sources == ()
    assert model.calls == 2, "a failed read leaves nothing to choose among"


async def test_choosing_before_being_asked_is_refused():
    """The tool is on the list from the first turn so the cached prefix never
    changes, which means the agent can reach it early. Nothing is attached if
    it does."""
    model = StubModel([
        [_call("choose_sources", '{"numbers": [1]}')],
        [_text(_CLEAN)],
    ])

    out = await kb_agent.ask(_agent(model), _sourced(), "q", [])

    assert out.sources == ()
    basket = []
    assert "not been asked about sources yet" in tools.choose_sources(
        _sourced(), [], basket, [1]), "and it is told why"
    assert basket == []


async def test_a_broken_sources_turn_costs_the_attachments_and_not_the_answer():
    class DiesOnTheThirdCall(StubModel):
        async def get_response(self, *args, **kwargs):
            if self.calls >= 2:
                raise RuntimeError("endpoint is down")
            return await super().get_response(*args, **kwargs)

    model = DiesOnTheThirdCall([
        [_call("read_note", '{"path": "kb/p.md"}')],
        [_text(_CLEAN)],
    ])

    out = await kb_agent.ask(_agent(model), _sourced(), "q", [])

    assert out.text == _CLEAN, "the answer was already settled"
    assert out.sources == ()


async def test_the_sources_question_stays_out_of_the_session_history():
    """The next question inherits the answer, not the bookkeeping that followed
    it."""
    out = await kb_agent.ask(_agent(_reads_then_picks("kb/p.md", "[1]")),
                             _sourced(), "q", [])

    assert not any("choose_sources" in str(item) for item in out.history)


def test_the_notes_offered_are_the_ones_read_in_order_without_repeats():
    calls = (kb_agent.ToolCall("read_note", {"path": "kb/b.md"}, "1k chars"),
             kb_agent.ToolCall("list_notes", {"path_prefix": "kb/"}, "3 hits"),
             kb_agent.ToolCall("read_note", {"path": "kb/a.md"}, "1k chars"),
             kb_agent.ToolCall("read_note", {"path": "kb/b.md"}, "1k chars"),
             kb_agent.ToolCall("read_note", {"path": "kb/x.md"}, "no such note"))

    assert kb_agent.notes_read(calls) == ["kb/b.md", "kb/a.md"]


def test_the_numbered_list_names_the_document_and_where_in_it():
    listing = tools.numbered_sources(_sourced(), ["kb/p.md", "kb/bare.md"])

    assert listing.splitlines() == [
        "1. Grading — Policies for Bachelor Studies — §III.4 Grading — pp. 18-20",
        "2. Loose",
    ]


def test_reasoning_is_off_by_default():
    """Function tools plus reasoning is a 400 from OpenAI's small models on
    chat completions, and this agent is nothing but function tools."""
    built = kb_agent.build_agent("m", client=None, model=StubModel([]))

    assert built.model_settings.reasoning.effort == "none"


def test_an_empty_reasoning_effort_omits_the_parameter():
    """A gateway fronting a model with no notion of reasoning must not be sent
    the field at all."""
    built = kb_agent.build_agent("m", client=None, model=StubModel([]),
                                 reasoning_effort="")

    assert built.model_settings.reasoning is None


def test_no_runtime_without_the_llm_api_key():
    class Unconfigured:
        kb_configured = False

    assert kb_agent.build_runtime(Unconfigured()) is None
