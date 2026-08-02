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

from jbcub_bot.core.kb_snapshot import Note, Snapshot
from jbcub_bot.features.kb import agent as kb_agent


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


async def test_a_tool_call_sequence_reaches_an_answer():
    model = StubModel([
        [_call("read_note", '{"path": "kb/policies/exams.md"}')],
        [_text("Retakes are allowed once.\nSources: kb/policies/exams.md")],
    ])

    answer, history, stats = await kb_agent.ask(_agent(model), _snapshot(),
                                                "How many retakes?", [])

    assert "Retakes are allowed once" in answer
    assert model.calls == 2
    assert history, "the run's input list carries the session forward"
    assert stats.steps == 2
    assert stats.tool_calls == 1
    assert stats.notes_read == 1
    assert stats.input_tokens == 1200, "tokens sum across the run's two turns"
    assert stats.output_tokens == 310


async def test_reading_one_note_twice_counts_one_note():
    model = StubModel([
        [_call("read_note", '{"path": "kb/policies/exams.md"}'),
         _call("read_note", '{"path": "kb/policies/exams.md"}'),
         _call("list_notes", '{"path_prefix": "kb/"}')],
        [_text("Retakes are allowed once.")],
    ])

    _, _, stats = await kb_agent.ask(_agent(model), _snapshot(), "q", [])

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

    _, _, stats = await kb_agent.ask(_agent(model), _snapshot(), "q", [])

    assert [(c.name, c.args, c.result) for c in stats.calls] == [
        ("search_notes", {"pattern": "retake"}, "1 hit"),
        ("read_note", {"path": "kb/policies/exams.md"}, "26 chars"),
    ]


async def test_a_call_the_model_mangled_is_recorded_with_no_arguments():
    model = StubModel([
        [_call("list_notes", "not json at all")],
        [_text("done")],
    ])

    _, _, stats = await kb_agent.ask(_agent(model), _snapshot(), "q", [])

    assert stats.calls[0].args == {}
    assert stats.tool_calls == 1


async def test_a_model_that_never_stops_is_cut_and_says_so():
    model = StubModel([[_call("list_notes", '{"path_prefix": "kb/"}')]])

    answer, history, stats = await kb_agent.ask(_agent(model), _snapshot(),
                                                "hi", [])

    assert answer == kb_agent.CUT_SHORT
    assert model.calls == kb_agent.MAX_TURNS
    assert history == [], "an abandoned run must not pollute the session"
    assert stats.tool_calls > 0, "a cut-short run still reports what it burned"


async def test_a_raising_tool_comes_back_as_an_error_not_a_crash(monkeypatch):
    def boom(snapshot, path):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(kb_agent.tools, "read_note", boom)
    model = StubModel([
        [_call("read_note", '{"path": "kb/policies/exams.md"}')],
        [_text("I could not read that note.")],
    ])

    answer, _, _ = await kb_agent.ask(_agent(model), _snapshot(), "retakes?", [])

    assert "could not read" in answer


async def test_an_endpoint_failure_propagates():
    with pytest.raises(RuntimeError, match="endpoint is down"):
        await kb_agent.ask(_agent(ExplodingModel()), _snapshot(), "hi", [])


def test_the_prompt_asks_for_brevity_and_forbids_invented_pages():
    rules = kb_agent.SYSTEM_RULES

    assert "three sentences" in rules
    assert "<blockquote>" in rules
    assert "Sources:" in rules
    assert "never write a page number" in rules.lower()


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
    rules = kb_agent.SYSTEM_RULES

    assert "Never name a note, a path, a folder or a file in your answer" in rules
    assert "only the source documents exist" in rules


def test_the_prompt_shows_an_answer_that_cites_nothing():
    """An unanswerable question that still drags a citation behind it is the
    thing this example exists to prevent."""
    rules = kb_agent.SYSTEM_RULES

    assert "no Sources: line whatsoever" in rules
    assert "parking permits" in rules, "the example, not just the rule"


async def test_who_is_asking_reaches_the_prompt():
    ctx = RunContextWrapper(kb_agent.Ask(snapshot=_snapshot(),
                                         about="role: Student · cohort: 2024"))

    text = kb_agent.instructions(ctx, None)

    assert "cohort: 2024" in text
    assert "kb/policies/exams.md" in text, "the map of the base is still there"


async def test_an_anonymous_run_says_nothing_about_the_asker():
    ctx = RunContextWrapper(kb_agent.Ask(snapshot=_snapshot()))

    assert "The person asking" not in kb_agent.instructions(ctx, None)


def test_no_runtime_without_all_three_settings():
    class Unconfigured:
        kb_configured = False

    assert kb_agent.build_runtime(Unconfigured()) is None
