"""The agent, driven by a stub model.

The framework owns the tool loop, so the seam is the model: a stub that returns
scripted responses proves the wiring without a network call or an API key.
"""
import pytest
from agents import ModelResponse
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


def _call(name: str, arguments: str) -> TResponseOutputItem:
    return ResponseFunctionToolCall(type="function_call", name=name,
                                    arguments=arguments, call_id="call-1")


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
        return ModelResponse(output=list(items), usage=Usage(),
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
        [_text("Retakes are allowed once (kb/policies/exams.md).")],
    ])

    answer, history = await kb_agent.ask(_agent(model), _snapshot(),
                                         "How many retakes?", [])

    assert "Retakes are allowed once" in answer
    assert model.calls == 2
    assert history, "the run's input list carries the session forward"


async def test_a_model_that_never_stops_is_cut_and_says_so():
    model = StubModel([[_call("list_notes", '{"path_prefix": "kb/"}')]])

    answer, history = await kb_agent.ask(_agent(model), _snapshot(), "hi", [])

    assert answer == kb_agent.CUT_SHORT
    assert model.calls == kb_agent.MAX_TURNS
    assert history == [], "an abandoned run must not pollute the session"


async def test_a_raising_tool_comes_back_as_an_error_not_a_crash(monkeypatch):
    def boom(snapshot, path):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(kb_agent.tools, "read_note", boom)
    model = StubModel([
        [_call("read_note", '{"path": "kb/policies/exams.md"}')],
        [_text("I could not read that note.")],
    ])

    answer, _ = await kb_agent.ask(_agent(model), _snapshot(), "retakes?", [])

    assert "could not read" in answer


async def test_an_endpoint_failure_propagates():
    with pytest.raises(RuntimeError, match="endpoint is down"):
        await kb_agent.ask(_agent(ExplodingModel()), _snapshot(), "hi", [])


def test_citations_render_against_the_snapshot_sha():
    rendered = kb_agent.render_answer(
        "Retakes are allowed once, see kb/policies/exams.md:5.",
        repo="xoposhiy/cub-kb", sha="abc123")

    assert ("https://github.com/xoposhiy/cub-kb/blob/abc123/"
            "kb/policies/exams.md#L5") in rendered


def test_an_answer_without_a_note_reference_gets_no_sources_block():
    rendered = kb_agent.render_answer("I could not find that.",
                                      repo="r", sha="abc123")

    assert rendered == "I could not find that."


def test_each_note_is_linked_once():
    rendered = kb_agent.render_answer(
        "kb/a.md:1 says one thing and kb/a.md:1 says it again.",
        repo="r", sha="s")

    assert rendered.count("https://github.com/r/blob/s/kb/a.md#L1") == 1


def test_no_runtime_without_all_three_settings():
    class Unconfigured:
        kb_configured = False

    assert kb_agent.build_runtime(Unconfigured()) is None
