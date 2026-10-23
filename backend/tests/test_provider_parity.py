"""Provider-parity contract (spec 9.3, D33's parity requirement, D68): every
provider -- stub, bedrock, anthropic, cortex -- must normalize the same
recorded scenario to the same LLMResponse, and must send the same intent on
the wire for a forced tool call and a tool-result history. Fixtures live in
tests/fixtures/providers/<provider>/<case>.json and are the recorded truth;
a change to one is a reviewed diff. Bedrock runs through a fake client:
nothing here touches the network.

Provenance: Bedrock bodies follow the ConverseResponse shape that
test_llm_bedrock.py's recorded dicts use (botocore's bundled service model);
Anthropic bodies follow the Messages API response schema. Cortex bodies
follow the Messages-compatible shape documented on Snowflake's Cortex LLM
REST API page, 2026-10-23; the page's example omits `id` and `usage`, which
are included here per the Anthropic schema the page defers to. The handoff
asks the Snowflake owner to capture one real body and replace these."""

import json
from pathlib import Path

import httpx
import pytest

from poseidon.core.llm.anthropic import AnthropicProvider
from poseidon.core.llm.bedrock import BedrockProvider
from poseidon.core.llm.cortex import CortexProvider
from poseidon.core.llm.messages_api import error_response
from poseidon.core.llm.stub import StubProvider
from poseidon.core.llm.types import LLMResponse, ToolCall

FIXTURES = Path(__file__).parent / "fixtures" / "providers"
PROVIDERS = ("stub", "bedrock", "anthropic", "cortex")

_METRIC_TOOL = {"name": "data_qa.metric_query", "description": "Certified metric query.",
                "input_schema": {"type": "object", "properties": {}}}
_NARRATE_TOOL = {"name": "reporting.narrate", "description": "Narrative.",
                 "input_schema": {"type": "object", "properties": {}}}
_METRIC_ARGS = {"metrics": ["GP"], "period": {"start": "2025-01-01", "end": "2025-12-31"}}
_NARRATE_ARGS = {"narrative_md": "x", "insights": ["a", "b", "c"], "context_notes": [],
                 "suggested_questions": ["q1", "q2", "q3"]}

EXPECTED = {
    "plain_completion": LLMResponse("the answer is 42", (), "end_turn", 10, 5),
    "tool_call": LLMResponse("", (ToolCall("call-1", "data_qa.metric_query", _METRIC_ARGS),),
                             "tool_use", 20, 15),
    "forced_tool_call": LLMResponse(
        "", (ToolCall("call-2", "reporting.narrate", _NARRATE_ARGS),), "tool_use", 30, 40
    ),
    "token_accounting": LLMResponse("ok", (), "end_turn", 1234, 567),
}
REQUESTS = {
    "plain_completion": dict(system="s", messages=[{"role": "user", "content": "q"}], tools=[],
                             tool_choice=None),
    "tool_call": dict(system="s", messages=[{"role": "user", "content": "gp 2025"}],
                      tools=[_METRIC_TOOL], tool_choice=None),
    "forced_tool_call": dict(system="s", messages=[{"role": "user", "content": "write"}],
                             tools=[_NARRATE_TOOL], tool_choice="reporting.narrate"),
    "malformed_tool_output": dict(system="s", messages=[{"role": "user", "content": "gp"}],
                                  tools=[_METRIC_TOOL], tool_choice=None),
    "token_accounting": dict(system="s", messages=[{"role": "user", "content": "q"}], tools=[],
                             tool_choice=None),
}


class _FakeConverseClient:
    def __init__(self, response):
        self.calls = []
        self._response = response

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class Harness:
    """One provider wired to one recorded fixture, plus what it sent."""

    def __init__(self, name, case):
        self.name = name
        self.sent = {}
        if name == "stub":
            scripted = EXPECTED.get(case) or error_response("stub", "MalformedResponse")
            self.provider = StubProvider([scripted])
            return
        body = json.loads((FIXTURES / name / f"{case}.json").read_text(encoding="utf-8"))
        if name == "bedrock":
            self.client = _FakeConverseClient(body)
            self.provider = BedrockProvider(client=self.client)
            return

        def handler(request):
            self.sent = json.loads(request.content)
            return httpx.Response(200, json=body)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        if name == "anthropic":
            self.provider = AnthropicProvider(api_key="k", client=client)
        else:
            self.provider = CortexProvider(account_url="https://a.snowflakecomputing.com",
                                           token="t", client=client)

    def invoke(self, case, **overrides):
        request = dict(REQUESTS[case], **overrides)
        result = self.provider.invoke(model="m", params={"max_tokens": 100}, **request)
        if self.name == "bedrock":
            self.sent = self.client.calls[-1]
        elif self.name == "stub":
            self.sent = self.provider.calls[-1]
        return result


@pytest.mark.parametrize("name", PROVIDERS)
@pytest.mark.parametrize("case", sorted(EXPECTED))
def test_every_provider_normalizes_the_recorded_case_identically(name, case):
    assert Harness(name, case).invoke(case) == EXPECTED[case]


@pytest.mark.parametrize("name", PROVIDERS)
def test_every_provider_degrades_malformed_tool_output_to_an_error_turn(name):
    result = Harness(name, "malformed_tool_output").invoke("malformed_tool_output")
    assert result.stop_reason == "error"
    assert result.tool_calls == ()
    assert result.text.endswith("MalformedResponse")
    assert (result.input_tokens, result.output_tokens) == (0, 0)


@pytest.mark.parametrize("name", ("bedrock", "anthropic", "cortex"))
def test_forced_tool_call_names_the_wire_tool_natively(name):
    harness = Harness(name, "forced_tool_call")
    harness.invoke("forced_tool_call")
    if name == "bedrock":
        assert harness.sent["toolConfig"]["toolChoice"] == {"tool": {"name": "reporting__narrate"}}
    else:
        assert harness.sent["tool_choice"] == {"type": "tool", "name": "reporting__narrate"}


def test_anthropic_instruct_mode_forces_through_the_system_prompt():
    harness = Harness("anthropic", "forced_tool_call")
    harness.provider.invoke(model="claude-sonnet-5-5",
                            params={"max_tokens": 100, "tool_choice_mode": "instruct"},
                            **REQUESTS["forced_tool_call"])
    assert harness.sent["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert "reporting__narrate" in harness.sent["system"]


_HISTORY = [
    {"role": "user", "content": "gp 2025"},
    {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "call-1", "name": "data_qa.metric_query",
                     "input": _METRIC_ARGS}}]},
    {"role": "user", "content": [
        {"toolResult": {"toolUseId": "call-1",
                        "content": [{"json": {"status": 422, "detail": "bad"}}],
                        "status": "error"}}]},
]


@pytest.mark.parametrize("name", ("bedrock", "anthropic", "cortex"))
def test_tool_result_history_reaches_the_wire_as_an_error_result(name):
    harness = Harness(name, "plain_completion")
    harness.invoke("plain_completion", messages=_HISTORY, tools=[_METRIC_TOOL])
    if name == "bedrock":
        assistant, user = harness.sent["messages"][1], harness.sent["messages"][2]
        assert assistant["content"][0]["toolUse"]["name"] == "data_qa__metric_query"
        assert user["content"][0]["toolResult"]["status"] == "error"
    else:
        assistant, user = harness.sent["messages"][1], harness.sent["messages"][2]
        assert assistant["content"][0] == {"type": "tool_use", "id": "call-1",
                                           "name": "data_qa__metric_query", "input": _METRIC_ARGS}
        assert user["content"][0]["type"] == "tool_result"
        assert user["content"][0]["tool_use_id"] == "call-1"
        assert user["content"][0]["is_error"] is True


@pytest.mark.parametrize("name", PROVIDERS)
def test_every_provider_leaves_the_caller_lists_untouched(name):
    messages = [{"role": "user", "content": [{"text": "q"}]}]
    tools = [dict(_METRIC_TOOL)]
    before = (json.dumps(messages), json.dumps(tools))
    Harness(name, "plain_completion").invoke("plain_completion", messages=messages, tools=tools)
    assert (json.dumps(messages), json.dumps(tools)) == before


def test_fixture_set_is_complete():
    for name in ("bedrock", "anthropic", "cortex"):
        present = {p.stem for p in (FIXTURES / name).glob("*.json")}
        assert present == set(EXPECTED) | {"malformed_tool_output"}, name
