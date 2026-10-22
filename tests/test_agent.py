from contextlib import contextmanager
from types import SimpleNamespace as NS

from replica.agent import Agent
from replica.tools import Toolbox


class FakeMessages:
    """Replays scripted responses and records each request's kwargs."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self.response


def fake_client(responses):
    messages = FakeMessages(responses)

    @contextmanager
    def stream(**kwargs):
        messages.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        yield FakeStream(messages.responses.pop(0))

    return NS(beta=NS(messages=NS(stream=stream))), messages


def test_tool_loop_runs_tool_and_returns_final_text(tmp_path):
    (tmp_path / "hello.txt").write_text("hi there\n")
    client, recorded = fake_client([
        NS(stop_reason="tool_use", content=[
            NS(type="tool_use", id="t1", name="read_file", input={"path": "hello.txt"}),
        ]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="It says hi there.")]),
    ])
    agent = Agent(toolbox=Toolbox(tmp_path), client=client)

    assert agent.run("What does hello.txt say?") == "It says hi there."
    second = recorded.requests[1]
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result == {"type": "tool_result", "tool_use_id": "t1", "content": "     1\thi there"}
    assert second["model"] == "claude-opus-5-5"
    assert second["thinking"]["type"] == "adaptive"
    assert second["fallbacks"] == "default"


def test_invalid_tool_input_is_returned_as_error(tmp_path):
    client, recorded = fake_client([
        NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="bash", input={})]),
        NS(stop_reason="end_turn", content=[NS(type="text", text="ok")]),
    ])
    Agent(toolbox=Toolbox(tmp_path), client=client).run("go")
    result = recorded.requests[1]["messages"][-1]["content"][0]
    assert result["is_error"] is True and "INVALID_JSON" in result["content"]


def test_refusal_stops_without_running_tools(tmp_path):
    client, recorded = fake_client([
        NS(stop_reason="refusal", content=[NS(type="tool_use", id="t1", name="bash", input={"command": "touch x"})]),
    ])
    assert Agent(toolbox=Toolbox(tmp_path), client=client).run("go") == "[the request was declined]"
    assert not (tmp_path / "x").exists()
