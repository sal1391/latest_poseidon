"""Translation contract for core/llm/messages_api.py: Converse-dialect
requests to Messages API bodies, Messages API responses to LLMResponse.
Every wire field name here was read from platform.claude.com's Messages
and tool-use pages on 2026-10-23 (define-tools: tool name regex
^[a-zA-Z0-9_-]{1,128}$, tool_choice forms; handle-tool-calls: tool_result
with tool_use_id/content/is_error), never from memory."""

import json
from pathlib import Path

import httpx
import pytest

from poseidon.core.llm import messages_api
from poseidon.core.llm.messages_api import (
    FORCED_TOOL_INSTRUCTION,
    MessagesAPIProvider,
    ProviderCredentialsError,
    build_request,
    normalize_response,
)
from poseidon.core.llm.types import LLMResponse, ToolCall

_TOOLS = [
    {
        "name": "data_qa.metric_query",
        "description": "Certified metric query.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    }
]
_USER = [{"role": "user", "content": "gp for 2025"}]


def _body(**overrides):
    base = dict(system="sys", messages=_USER, tools=_TOOLS, model="claude-sonnet-5-5",
                params={"max_tokens": 100}, tool_choice=None)
    base.update(overrides)
    return build_request(**base)


# ---- request building -------------------------------------------------------

def test_build_request_minimal_shape():
    body = _body(tools=[], system="")
    assert body == {"model": "claude-sonnet-5-5", "max_tokens": 100,
                    "messages": [{"role": "user", "content": "gp for 2025"}]}


def test_build_request_defaults_max_tokens_when_params_omit_it():
    assert _body(params={})["max_tokens"] == messages_api.DEFAULT_MAX_TOKENS


def test_build_request_forwards_temperature_and_thinking_and_drops_the_rest():
    body = _body(params={"max_tokens": 5, "temperature": 0.2,
                         "thinking": {"type": "between_tools"}, "enabled": True,
                         "tool_choice_mode": "instruct", "mystery": 1})
    assert body["temperature"] == 0.2
    assert body["thinking"] == {"type": "between_tools"}
    for key in ("enabled", "tool_choice_mode", "mystery"):
        assert key not in body


def test_build_request_translates_tool_names_to_wire_form():
    assert _body()["tools"] == [{"name": "data_qa__metric_query",
                                 "description": "Certified metric query.",
                                 "input_schema": {"type": "object", "properties": {},
                                                  "required": []}}]


def test_build_request_native_forced_tool_choice():
    body = _body(tool_choice="data_qa.metric_query")
    assert body["tool_choice"] == {"type": "tool", "name": "data_qa__metric_query"}
    assert body["system"] == "sys"


def test_build_request_instruct_forced_tool_choice_appends_the_instruction():
    body = _body(tool_choice="data_qa.metric_query",
                 params={"max_tokens": 5, "tool_choice_mode": "instruct"})
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    instruction = FORCED_TOOL_INSTRUCTION.format(name="data_qa__metric_query")
    assert body["system"] == "sys\n\n" + instruction


def test_build_request_instruct_mode_with_empty_system_is_the_instruction_alone():
    body = _body(system="", tool_choice="data_qa.metric_query",
                 params={"max_tokens": 5, "tool_choice_mode": "instruct"})
    assert body["system"] == FORCED_TOOL_INSTRUCTION.format(name="data_qa__metric_query")


def test_build_request_tool_choice_without_tools_is_a_caller_bug():
    with pytest.raises(ValueError, match="tool_choice requires at least one tool"):
        _body(tools=[], tool_choice="data_qa.metric_query")


def test_build_request_unknown_tool_choice_mode_is_a_caller_bug():
    with pytest.raises(ValueError, match="tool_choice_mode"):
        _body(tool_choice="data_qa.metric_query",
              params={"max_tokens": 5, "tool_choice_mode": "shout"})


def test_build_messages_translates_tool_use_and_text_blocks():
    history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [
            {"text": "on it"},
            {"toolUse": {"toolUseId": "t1", "name": "data_qa.metric_query", "input": {"a": 1}}},
        ]},
    ]
    body = _body(messages=history)
    assert body["messages"][1] == {"role": "assistant", "content": [
        {"type": "text", "text": "on it"},
        {"type": "tool_use", "id": "t1", "name": "data_qa__metric_query", "input": {"a": 1}},
    ]}


def test_build_messages_translates_success_tool_result():
    history = [{"role": "user", "content": [
        {"toolResult": {"toolUseId": "t1", "content": [{"text": "Results: 3"}],
                        "status": "success"}}
    ]}]
    assert _body(messages=history)["messages"][0] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1",
         "content": [{"type": "text", "text": "Results: 3"}]}
    ]}


def test_build_messages_translates_json_tool_result_as_text_with_is_error():
    problem = {"status": 422, "detail": "bad args"}
    history = [{"role": "user", "content": [
        {"toolResult": {"toolUseId": "t1", "content": [{"json": problem}], "status": "error"}}
    ]}]
    block = _body(messages=history)["messages"][0]["content"][0]
    assert block["is_error"] is True
    assert block["content"] == [{"type": "text", "text": json.dumps(problem, sort_keys=True)}]


def test_build_messages_translates_reasoning_content_to_thinking_blocks():
    history = [{"role": "assistant", "content": [
        {"reasoningContent": {"reasoningText": {"text": "hmm", "signature": "sig"}}},
        {"reasoningContent": {"redactedContent": "b64=="}},
        {"toolUse": {"toolUseId": "t1", "name": "data_qa.metric_query", "input": {}}},
    ]}]
    content = _body(messages=history)["messages"][0]["content"]
    assert content[0] == {"type": "thinking", "thinking": "hmm", "signature": "sig"}
    assert content[1] == {"type": "redacted_thinking", "data": "b64=="}
    assert content[2]["type"] == "tool_use"


def test_build_messages_rejects_an_unknown_block():
    with pytest.raises(ValueError, match="unsupported content block"):
        _body(messages=[{"role": "user", "content": [{"image": {}}]}])


def test_build_request_never_mutates_its_inputs():
    history = [{"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": "data_qa.metric_query", "input": {"a": 1}}}]}]
    tools = [dict(_TOOLS[0])]
    params = {"max_tokens": 5, "tool_choice_mode": "instruct"}
    snapshot = (json.dumps(history), json.dumps(tools), json.dumps(params))
    _body(messages=history, tools=tools, params=params, tool_choice="data_qa.metric_query")
    assert (json.dumps(history), json.dumps(tools), json.dumps(params)) == snapshot


# ---- response normalization ------------------------------------------------

_END_TURN = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
             "content": [{"type": "text", "text": "the answer is 42"}],
             "stop_reason": "end_turn", "stop_sequence": None,
             "usage": {"input_tokens": 10, "output_tokens": 5}}

_TOOL_USE = {"id": "msg_2", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
             "content": [
                 {"type": "thinking", "thinking": "", "signature": "sig-a"},
                 {"type": "text", "text": "Let me look."},
                 {"type": "tool_use", "id": "toolu_1", "name": "data_qa__metric_query",
                  "input": {"metrics": ["GP"]}},
             ],
             "stop_reason": "tool_use", "stop_sequence": None,
             "usage": {"input_tokens": 20, "output_tokens": 15}}


def test_normalize_end_turn_joins_text_blocks_and_skips_thinking():
    body = dict(_END_TURN, content=[{"type": "thinking", "thinking": "x", "signature": "s"},
                                    {"type": "text", "text": "a"}, {"type": "text", "text": "b"}])
    assert normalize_response(body, error_prefix="anthropic") == LLMResponse(
        text="ab", tool_calls=(), stop_reason="end_turn", input_tokens=10, output_tokens=5)


def test_normalize_tool_use_drops_text_carries_reasoning_and_translates_the_name():
    result = normalize_response(_TOOL_USE, error_prefix="anthropic")
    assert result == LLMResponse(
        text="", stop_reason="tool_use", input_tokens=20, output_tokens=15,
        tool_calls=(ToolCall(id="toolu_1", name="data_qa.metric_query",
                             arguments={"metrics": ["GP"]}),),
        reasoning=({"reasoningContent": {"reasoningText": {"text": "", "signature": "sig-a"}}},),
    )


def test_normalize_redacted_thinking_maps_to_redacted_content():
    body = dict(_TOOL_USE, content=[{"type": "redacted_thinking", "data": "zz=="},
                                    _TOOL_USE["content"][2]])
    assert normalize_response(body, error_prefix="anthropic").reasoning == (
        {"reasoningContent": {"redactedContent": "zz=="}},)


@pytest.mark.parametrize("stop", ["max_tokens", "stop_sequence", "pause_turn", "something_new"])
def test_normalize_other_stop_reasons_are_end_turn(stop):
    result = normalize_response(dict(_END_TURN, stop_reason=stop), error_prefix="p")
    assert result.stop_reason == "end_turn"


def test_normalize_refusal_is_a_pinned_error():
    body = dict(_END_TURN, stop_reason="refusal", content=[],
                stop_details={"type": "refusal", "category": "cyber"})
    assert normalize_response(body, error_prefix="anthropic") == LLMResponse(
        text="anthropic error: refusal", tool_calls=(), stop_reason="error",
        input_tokens=0, output_tokens=0)


def test_normalize_missing_usage_defaults_to_zero():
    body = {k: v for k, v in _END_TURN.items() if k != "usage"}
    result = normalize_response(body, error_prefix="p")
    assert (result.input_tokens, result.output_tokens) == (0, 0)


def test_normalize_usage_with_null_values_defaults_to_zero():
    body = dict(_END_TURN, usage={"input_tokens": None, "output_tokens": None})
    result = normalize_response(body, error_prefix="p")
    assert (result.input_tokens, result.output_tokens) == (0, 0)


@pytest.mark.parametrize("body", [
    {},
    {"stop_reason": "end_turn"},
    dict(_END_TURN, content=None),
    dict(_END_TURN, content=[{"type": "text", "text": None}]),
    dict(_TOOL_USE, content=[{"type": "tool_use", "id": "x", "name": "n"}]),
])
def test_normalize_malformed_bodies_degrade_to_the_pinned_error(body):
    assert normalize_response(body, error_prefix="cortex") == LLMResponse(
        text="cortex error: MalformedResponse", tool_calls=(), stop_reason="error",
        input_tokens=0, output_tokens=0)


def test_normalize_tool_use_with_non_object_input_is_malformed():
    body = dict(_TOOL_USE, content=[{"type": "tool_use", "id": "x", "name": "n", "input": "oops"}])
    assert normalize_response(body, error_prefix="p").stop_reason == "error"
    assert normalize_response(body, error_prefix="p").text.endswith("MalformedResponse")


# ---- the shared HTTP invoke, through a minimal concrete subclass -----------

class _FakeProvider(MessagesAPIProvider):
    error_prefix = "fake"

    def _endpoint(self):
        return "https://example.test/v1/messages"

    def _headers(self):
        return {"x-api-key": "k", "anthropic-version": messages_api.ANTHROPIC_VERSION}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_invoke_posts_the_built_body_and_normalizes():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_END_TURN)

    result = _FakeProvider(client=_client(handler)).invoke(
        system="sys", messages=_USER, tools=[], model="m", params={"max_tokens": 7})
    assert seen["url"] == "https://example.test/v1/messages"
    assert seen["headers"]["x-api-key"] == "k"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"] == {"model": "m", "max_tokens": 7, "system": "sys",
                            "messages": [{"role": "user", "content": "gp for 2025"}]}
    assert result.text == "the answer is 42"


def test_invoke_http_error_body_type_becomes_the_error_code():
    def handler(request):
        return httpx.Response(401, json={"type": "error",
                                         "error": {"type": "authentication_error",
                                                   "message": "bad key k"}})
    result = _FakeProvider(client=_client(handler)).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result == LLMResponse(text="fake error: authentication_error", tool_calls=(),
                                 stop_reason="error", input_tokens=0, output_tokens=0)


def test_invoke_http_error_without_json_reports_the_status():
    def handler(request):
        return httpx.Response(503, text="inference timed out")
    result = _FakeProvider(client=_client(handler)).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "fake error: HTTP 503" and result.stop_reason == "error"


def test_invoke_transport_error_is_a_structured_error_not_a_raise():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)
    result = _FakeProvider(client=_client(handler)).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "fake error: ConnectError" and result.stop_reason == "error"


def test_invoke_non_json_2xx_is_malformed():
    def handler(request):
        return httpx.Response(200, text="<html>")
    result = _FakeProvider(client=_client(handler)).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "fake error: MalformedResponse"


def test_client_is_not_built_until_first_invoke(monkeypatch):
    built = []
    real = httpx.Client

    def spy(*args, **kwargs):
        built.append(kwargs)
        return real(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_END_TURN)))
    monkeypatch.setattr(messages_api.httpx, "Client", spy)
    provider = _FakeProvider()
    assert built == []
    provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert len(built) == 1 and built[0]["timeout"] == httpx.Timeout(120.0)


def test_credentials_error_is_a_runtime_error():
    assert issubclass(ProviderCredentialsError, RuntimeError)


def test_messages_api_module_is_ascii_on_disk():
    for path in (Path(messages_api.__file__), Path(__file__)):
        offending = sorted({b for b in path.read_bytes() if b > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
