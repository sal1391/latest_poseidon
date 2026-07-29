"""Tests for Phase 5 Task 3 (doc 03 section 1, decision D11): BedrockProvider,
which normalizes Amazon Bedrock's Converse/ConverseStream APIs to the
LLMProvider seam (Task 1's ``roles.py``).

Every hand-authored response/event dict below is shaped to match botocore's
own bundled bedrock-runtime service model (``ConverseRequest``/
``ConverseResponse``/``ConverseStreamOutput`` and their nested shapes --
``bedrock.py``'s module docstring names the exact file and shapes verified),
not guessed from memory or from the (differently-shaped) Anthropic Messages
API. All calls go through an injected fake client (``_FakeClient`` below) --
no network, no AWS config, no real ``boto3`` client construction anywhere in
this file except inside the credential-gated live smoke at the bottom.

Non-ASCII characters: none needed in this task (the pinned error string
``f"bedrock error: {code}"`` and every other pinned value are already plain
ASCII), but ``test_llm_bedrock_module_files_are_ascii_on_disk`` still scans
this file and ``bedrock.py`` byte-for-byte, matching the convention Task 1's
``test_llm_module_files_are_ascii_on_disk`` and Task 2's
``test_llm_prompts_module_files_are_ascii_on_disk`` each established for
their own new files.
"""

import copy
import os
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EventStreamError

from poseidon.core.llm import bedrock
from poseidon.core.llm.bedrock import BedrockProvider
from poseidon.core.llm.types import LLMResponse, ToolCall

# ---------------------------------------------------------------------------
# offline test double -- records every converse/converse_stream call,
# replays one canned response (or raises one canned exception). Stands in
# for a real boto3 bedrock-runtime client via BedrockProvider(client=...).
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, converse_response=None, converse_stream_response=None, raises=None):
        self.converse_calls: list[dict] = []
        self.converse_stream_calls: list[dict] = []
        self._converse_response = converse_response
        self._converse_stream_response = converse_stream_response
        self._raises = raises

    def converse(self, **kwargs):
        self.converse_calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._converse_response

    def converse_stream(self, **kwargs):
        self.converse_stream_calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._converse_stream_response


# ---------------------------------------------------------------------------
# recorded Converse response dicts (ConverseResponse shape: output.message.
# content[] / stopReason / usage.inputTokens+outputTokens / metrics.latencyMs)
# ---------------------------------------------------------------------------

_END_TURN_RESPONSE = {
    "output": {"message": {"role": "assistant", "content": [{"text": "the answer is 42"}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
    "metrics": {"latencyMs": 245},
}

_MULTI_TEXT_RESPONSE = {
    "output": {
        "message": {"role": "assistant", "content": [{"text": "Hello, "}, {"text": "world."}]}
    },
    "stopReason": "end_turn",
    "usage": {"inputTokens": 3, "outputTokens": 4, "totalTokens": 7},
    "metrics": {"latencyMs": 88},
}

_MAX_TOKENS_RESPONSE = {
    "output": {"message": {"role": "assistant", "content": [{"text": "truncated mid-sen"}]}},
    "stopReason": "max_tokens",
    "usage": {"inputTokens": 100, "outputTokens": 200, "totalTokens": 300},
    "metrics": {"latencyMs": 900},
}

_GUARDRAIL_RESPONSE = {
    "output": {"message": {"role": "assistant", "content": [{"text": "blocked"}]}},
    "stopReason": "guardrail_intervened",
    "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
    "metrics": {"latencyMs": 50},
}

_MISSING_USAGE_RESPONSE = {
    "output": {"message": {"role": "assistant", "content": [{"text": "no usage reported"}]}},
    "stopReason": "end_turn",
    "metrics": {"latencyMs": 10},
}

_TOOL_USE_RESPONSE = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "tooluse_abc123",
                        "name": "data_qa.metric_query",
                        "input": {"metric": "GP", "port": "SINGAPORE"},
                    }
                }
            ],
        }
    },
    "stopReason": "tool_use",
    "usage": {"inputTokens": 20, "outputTokens": 12, "totalTokens": 32},
    "metrics": {"latencyMs": 512},
}

_MULTI_TOOL_USE_RESPONSE = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": "t1", "name": "skill_a", "input": {"x": 1}}},
                {"toolUse": {"toolUseId": "t2", "name": "skill_b", "input": {"y": 2}}},
            ],
        }
    },
    "stopReason": "tool_use",
    "usage": {"inputTokens": 8, "outputTokens": 9, "totalTokens": 17},
    "metrics": {"latencyMs": 300},
}

_MIXED_TEXT_AND_TOOL_USE_RESPONSE = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [
                {"text": "Let me check that for you."},
                {"toolUse": {"toolUseId": "t1", "name": "skill_a", "input": {"x": 1}}},
            ],
        }
    },
    "stopReason": "tool_use",
    "usage": {"inputTokens": 14, "outputTokens": 6, "totalTokens": 20},
    "metrics": {"latencyMs": 210},
}

_TOOL_SCHEMA = {
    "name": "data_qa.metric_query",
    "description": "Answer a metric question over certified sales/GL data.",
    "input_schema": {
        "type": "object",
        "properties": {"metric": {"type": "string"}},
        "required": ["metric"],
    },
}


def _client(**kwargs) -> BedrockProvider:
    return BedrockProvider(client=_FakeClient(**kwargs))


# ---------------------------------------------------------------------------
# invoke() -- request building (modelId / messages / system / inferenceConfig
# / toolConfig), each read off client.converse_calls[0]
# ---------------------------------------------------------------------------


def test_invoke_passes_model_as_model_id():
    fake = _FakeClient(converse_response=_END_TURN_RESPONSE)
    provider = BedrockProvider(client=fake)

    provider.invoke(system="s", messages=[], tools=[], model="us.amazon.nova-lite-v1:0", params={})

    assert fake.converse_calls[0]["modelId"] == "us.amazon.nova-lite-v1:0"


def test_invoke_wraps_string_message_content_as_text_block():
    fake = _FakeClient(converse_response=_END_TURN_RESPONSE)
    provider = BedrockProvider(client=fake)

    provider.invoke(
        system="s",
        messages=[{"role": "user", "content": "hi there"}],
        tools=[],
        model="m",
        params={},
    )

    assert fake.converse_calls[0]["messages"] == [
        {"role": "user", "content": [{"text": "hi there"}]}
    ]


def test_invoke_passes_through_already_structured_message_content():
    """A message whose ``content`` is already a list of Converse content
    blocks (e.g. a future toolResult message from Task 4's agent loop)
    passes through as a NEW list holding the same blocks, not the caller's
    own list object -- see the mutation-discipline tests below."""
    fake = _FakeClient(converse_response=_END_TURN_RESPONSE)
    provider = BedrockProvider(client=fake)
    blocks = [{"text": "already"}, {"text": "structured"}]

    provider.invoke(
        system="s",
        messages=[{"role": "assistant", "content": blocks}],
        tools=[],
        model="m",
        params={},
    )

    sent = fake.converse_calls[0]["messages"]
    assert sent == [{"role": "assistant", "content": blocks}]
    assert sent[0]["content"] is not blocks


def test_invoke_wraps_system_text_as_one_block():
    fake = _FakeClient(converse_response=_END_TURN_RESPONSE)
    provider = BedrockProvider(client=fake)

    provider.invoke(
        system="be helpful",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        model="m",
        params={},
    )

    assert fake.converse_calls[0]["system"] == [{"text": "be helpful"}]


def test_invoke_omits_system_key_when_system_is_empty():
    """Converse's SystemContentBlock.text is a NonEmptyString (min: 1) --
    sending [{"text": ""}] would be a guaranteed ValidationException, so an
    empty system prompt omits the key entirely rather than sending it
    empty."""
    fake = _FakeClient(converse_response=_END_TURN_RESPONSE)
    provider = BedrockProvider(client=fake)

    provider.invoke(
        system="", messages=[{"role": "user", "content": "hi"}], tools=[], model="m", params={}
    )

    assert "system" not in fake.converse_calls[0]


def test_invoke_maps_max_tokens_and_temperature_to_inference_config():
    fake = _FakeClient(converse_response=_END_TURN_RESPONSE)
    provider = BedrockProvider(client=fake)

    provider.invoke(
        system="s",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        model="m",
        params={"max_tokens": 512, "temperature": 0.2},
    )

    assert fake.converse_calls[0]["inferenceConfig"] == {"maxTokens": 512, "temperature": 0.2}


def test_invoke_drops_enabled_and_unknown_params_and_omits_empty_inference_config():
    """`enabled` (RoleClient's own on/off gate -- a role invoked at all is
    already "on") and any not-yet-mapped future param are dropped, not
    forwarded; once nothing maps to inferenceConfig, the key is omitted
    entirely rather than sent as {}."""
    fake = _FakeClient(converse_response=_END_TURN_RESPONSE)
    provider = BedrockProvider(client=fake)

    provider.invoke(
        system="s",
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        model="m",
        params={"enabled": False, "some_future_knob": "x"},
    )

    assert "inferenceConfig" not in fake.converse_calls[0]


def test_invoke_omits_tool_config_when_tools_empty():
    """Converse's ToolConfigurationToolsList has min: 1 -- toolConfig: {tools:
    []} is a guaranteed ValidationException, so no tools means no toolConfig
    key at all, matching RoleClient.invoke's own tools=[] default."""
    fake = _FakeClient(converse_response=_END_TURN_RESPONSE)
    provider = BedrockProvider(client=fake)

    provider.invoke(
        system="s", messages=[{"role": "user", "content": "hi"}], tools=[], model="m", params={}
    )

    assert "toolConfig" not in fake.converse_calls[0]


def test_invoke_builds_tool_config_from_tool_schema_entries():
    fake = _FakeClient(converse_response=_TOOL_USE_RESPONSE)
    provider = BedrockProvider(client=fake)

    provider.invoke(
        system="s",
        messages=[{"role": "user", "content": "hi"}],
        tools=[_TOOL_SCHEMA],
        model="m",
        params={},
    )

    assert fake.converse_calls[0]["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "data_qa.metric_query",
                    "description": "Answer a metric question over certified sales/GL data.",
                    "inputSchema": {"json": _TOOL_SCHEMA["input_schema"]},
                }
            }
        ]
    }


# ---------------------------------------------------------------------------
# invoke() -- response normalization branches (stopReason -> tool_calls/text,
# usage -> tokens)
# ---------------------------------------------------------------------------


def test_invoke_normalizes_tool_use_response_to_one_tool_call():
    provider = _client(converse_response=_TOOL_USE_RESPONSE)

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result == LLMResponse(
        text="",
        tool_calls=(
            ToolCall(
                id="tooluse_abc123",
                name="data_qa.metric_query",
                arguments={"metric": "GP", "port": "SINGAPORE"},
            ),
        ),
        stop_reason="tool_use",
        input_tokens=20,
        output_tokens=12,
    )


def test_invoke_normalizes_multiple_tool_use_blocks_in_order():
    provider = _client(converse_response=_MULTI_TOOL_USE_RESPONSE)

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result.tool_calls == (
        ToolCall(id="t1", name="skill_a", arguments={"x": 1}),
        ToolCall(id="t2", name="skill_b", arguments={"y": 2}),
    )
    assert result.stop_reason == "tool_use"
    assert result.text == ""


def test_invoke_normalizes_mixed_text_and_tool_use_response_drops_the_text():
    """A response with BOTH a text block and a toolUse block (stopReason
    tool_use) still normalizes with text="" -- matches LLMResponse.text's
    own documented contract ("'' when pure tool_use", types.py) read as "the
    response IS the tool_use case", not "there happen to be zero text
    blocks". Any commentary text is not silently lost information in the
    stream path -- a streaming caller sees it live via on_text (see the
    stream-side equivalent of this test below) -- but the structured,
    non-stream LLMResponse only ever carries text for the end_turn case."""
    provider = _client(converse_response=_MIXED_TEXT_AND_TOOL_USE_RESPONSE)

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result.text == ""
    assert result.tool_calls == (ToolCall(id="t1", name="skill_a", arguments={"x": 1}),)
    assert result.stop_reason == "tool_use"


def test_invoke_normalizes_end_turn_response_to_text():
    provider = _client(converse_response=_END_TURN_RESPONSE)

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result == LLMResponse(
        text="the answer is 42",
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=10,
        output_tokens=5,
    )


def test_invoke_joins_multiple_text_blocks():
    provider = _client(converse_response=_MULTI_TEXT_RESPONSE)

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result.text == "Hello, world."
    assert result.stop_reason == "end_turn"


def test_invoke_normalizes_max_tokens_stop_reason_as_end_turn():
    provider = _client(converse_response=_MAX_TOKENS_RESPONSE)

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result.stop_reason == "end_turn"
    assert result.text == "truncated mid-sen"
    assert result.tool_calls == ()


def test_invoke_normalizes_unlisted_stop_reason_as_end_turn():
    """LLMResponse.stop_reason's documented domain is only tool_use/end_turn/
    error (types.py); Bedrock's StopReason enum has 9 values (verified in
    service-2.json). Every one besides "tool_use" folds into the end_turn
    branch here -- "error" is reserved for transport failures (ClientError),
    never a value the model itself reports. guardrail_intervened is used as
    a representative example of the other 7 values."""
    provider = _client(converse_response=_GUARDRAIL_RESPONSE)

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result.stop_reason == "end_turn"
    assert result.text == "blocked"


def test_invoke_defaults_missing_usage_to_zero_tokens():
    """usage is technically required by ConverseResponse, but this stays
    defensive against its absence rather than raising KeyError."""
    provider = _client(converse_response=_MISSING_USAGE_RESPONSE)

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result.input_tokens == 0
    assert result.output_tokens == 0


# ---------------------------------------------------------------------------
# invoke() -- ClientError never raises out of the provider
# ---------------------------------------------------------------------------


def test_invoke_client_error_returns_pinned_error_response():
    error_response = {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}
    provider = _client(raises=ClientError(error_response, "Converse"))

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result == LLMResponse(
        text="bedrock error: ThrottlingException",
        tool_calls=(),
        stop_reason="error",
        input_tokens=0,
        output_tokens=0,
    )


def test_invoke_client_error_with_no_code_reports_unknown():
    """Mirrors ClientError's OWN defensive fallback for its message template
    (error_response.get('Error', {}).get('Code', 'Unknown')) -- a malformed
    error dict still yields a structured response, never a second
    exception."""
    provider = _client(raises=ClientError({}, "Converse"))

    result = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result.text == "bedrock error: Unknown"
    assert result.stop_reason == "error"


# ---------------------------------------------------------------------------
# mutation discipline -- tools/messages arguments are never mutated
# ---------------------------------------------------------------------------


def test_invoke_never_mutates_tools_argument():
    tools = [dict(_TOOL_SCHEMA)]
    snapshot = copy.deepcopy(tools)
    provider = _client(converse_response=_TOOL_USE_RESPONSE)

    provider.invoke(
        system="s", messages=[{"role": "user", "content": "hi"}], tools=tools, model="m", params={}
    )

    assert tools == snapshot


def test_invoke_never_mutates_messages_argument():
    messages = [{"role": "user", "content": "hi"}]
    snapshot = copy.deepcopy(messages)
    provider = _client(converse_response=_END_TURN_RESPONSE)

    provider.invoke(system="s", messages=messages, tools=[], model="m", params={})

    assert messages == snapshot


# ---------------------------------------------------------------------------
# lazy client construction (region/client __init__ contract)
# ---------------------------------------------------------------------------


def test_client_is_not_constructed_until_first_invoke(monkeypatch):
    calls = []

    def fake_boto3_client(service_name, **kwargs):
        calls.append((service_name, kwargs))
        return _FakeClient(converse_response=_END_TURN_RESPONSE)

    monkeypatch.setattr(bedrock.boto3, "client", fake_boto3_client)
    provider = BedrockProvider(region="us-west-2")
    assert calls == []  # construction alone touches nothing

    provider.invoke(system="s", messages=[], tools=[], model="m", params={})
    assert calls == [("bedrock-runtime", {"region_name": "us-west-2"})]

    provider.invoke(system="s", messages=[], tools=[], model="m", params={})
    assert calls == [("bedrock-runtime", {"region_name": "us-west-2"})]  # built once, reused


def test_default_region_is_us_east_1(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bedrock.boto3,
        "client",
        lambda service_name, **kwargs: (
            calls.append((service_name, kwargs))
            or _FakeClient(converse_response=_END_TURN_RESPONSE)
        ),
    )
    provider = BedrockProvider()

    provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert calls == [("bedrock-runtime", {"region_name": "us-east-1"})]


def test_injected_client_is_used_without_ever_calling_boto3_client(monkeypatch):
    monkeypatch.setattr(
        bedrock.boto3, "client", lambda *a, **k: pytest.fail("boto3.client should not be called")
    )
    fake = _FakeClient(converse_response=_END_TURN_RESPONSE)
    provider = BedrockProvider(client=fake)

    provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert len(fake.converse_calls) == 1


# ---------------------------------------------------------------------------
# invoke_stream() -- on_text per delta, final LLMResponse matches the
# non-stream equivalent (ContentBlockStartEvent/ContentBlockDeltaEvent/
# MessageStopEvent/ConverseStreamMetadataEvent shapes)
# ---------------------------------------------------------------------------

_TEXT_STREAM_EVENTS = [
    {"messageStart": {"role": "assistant"}},
    {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hello, "}}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "world."}}},
    {"contentBlockStop": {"contentBlockIndex": 0}},
    {"messageStop": {"stopReason": "end_turn"}},
    {
        "metadata": {
            "usage": {"inputTokens": 6, "outputTokens": 4, "totalTokens": 10},
            "metrics": {"latencyMs": 77},
        }
    },
]

_TOOL_USE_STREAM_EVENTS = [
    {"messageStart": {"role": "assistant"}},
    {
        "contentBlockStart": {
            "contentBlockIndex": 0,
            "start": {"toolUse": {"toolUseId": "tooluse_abc123", "name": "data_qa.metric_query"}},
        }
    },
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"met'}}}},
    {
        "contentBlockDelta": {
            "contentBlockIndex": 0,
            "delta": {"toolUse": {"input": 'ric": "GP", "por'}},
        }
    },
    {
        "contentBlockDelta": {
            "contentBlockIndex": 0,
            "delta": {"toolUse": {"input": 't": "SINGAPORE"}'}},
        }
    },
    {"contentBlockStop": {"contentBlockIndex": 0}},
    {"messageStop": {"stopReason": "tool_use"}},
    {
        "metadata": {
            "usage": {"inputTokens": 20, "outputTokens": 12, "totalTokens": 32},
            "metrics": {"latencyMs": 512},
        }
    },
]

_INTERLEAVED_TWO_TOOL_STREAM_EVENTS = [
    {
        "contentBlockStart": {
            "contentBlockIndex": 0,
            "start": {"toolUse": {"toolUseId": "t1", "name": "skill_a"}},
        }
    },
    {
        "contentBlockStart": {
            "contentBlockIndex": 1,
            "start": {"toolUse": {"toolUseId": "t2", "name": "skill_b"}},
        }
    },
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"x"'}}}},
    {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"y"'}}}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": ": 1}"}}}},
    {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": ": 2}"}}}},
    {"contentBlockStop": {"contentBlockIndex": 0}},
    {"contentBlockStop": {"contentBlockIndex": 1}},
    {"messageStop": {"stopReason": "tool_use"}},
    {
        "metadata": {
            "usage": {"inputTokens": 9, "outputTokens": 11, "totalTokens": 20},
            "metrics": {},
        }
    },
]


def test_invoke_stream_calls_on_text_per_delta_and_returns_joined_text():
    fake = _FakeClient(converse_stream_response={"stream": list(_TEXT_STREAM_EVENTS)})
    provider = BedrockProvider(client=fake)
    received = []

    result = provider.invoke_stream(
        system="s", messages=[], tools=[], model="m", params={}, on_text=received.append
    )

    assert received == ["Hello, ", "world."]
    assert result == LLMResponse(
        text="Hello, world.", tool_calls=(), stop_reason="end_turn", input_tokens=6, output_tokens=4
    )


def test_invoke_stream_accumulates_tool_use_input_json_fragments():
    fake = _FakeClient(converse_stream_response={"stream": list(_TOOL_USE_STREAM_EVENTS)})
    provider = BedrockProvider(client=fake)
    received = []

    result = provider.invoke_stream(
        system="s", messages=[], tools=[], model="m", params={}, on_text=received.append
    )

    assert received == []  # no text deltas in this scenario
    assert result == LLMResponse(
        text="",
        tool_calls=(
            ToolCall(
                id="tooluse_abc123",
                name="data_qa.metric_query",
                arguments={"metric": "GP", "port": "SINGAPORE"},
            ),
        ),
        stop_reason="tool_use",
        input_tokens=20,
        output_tokens=12,
    )


_MIXED_TEXT_AND_TOOL_USE_STREAM_EVENTS = [
    {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}},
    {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Let me check that."}}},
    {"contentBlockStop": {"contentBlockIndex": 0}},
    {
        "contentBlockStart": {
            "contentBlockIndex": 1,
            "start": {"toolUse": {"toolUseId": "t1", "name": "skill_a"}},
        }
    },
    {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"x": 1}'}}}},
    {"contentBlockStop": {"contentBlockIndex": 1}},
    {"messageStop": {"stopReason": "tool_use"}},
    {
        "metadata": {
            "usage": {"inputTokens": 14, "outputTokens": 6, "totalTokens": 20},
            "metrics": {},
        }
    },
]


def test_invoke_stream_mixed_text_and_tool_use_fires_on_text_but_drops_text_from_result():
    """Stream-side equivalent of the mixed-content non-stream test above:
    on_text still fires live for the text delta that arrived before the
    tool_use decision (a streaming caller already saw it), but the final
    normalized LLMResponse matches the non-stream mixed-content case exactly
    -- text="", only the tool call populated."""
    fake = _FakeClient(
        converse_stream_response={"stream": list(_MIXED_TEXT_AND_TOOL_USE_STREAM_EVENTS)}
    )
    provider = BedrockProvider(client=fake)
    received = []

    result = provider.invoke_stream(
        system="s", messages=[], tools=[], model="m", params={}, on_text=received.append
    )

    assert received == ["Let me check that."]
    assert result.text == ""
    assert result.tool_calls == (ToolCall(id="t1", name="skill_a", arguments={"x": 1}),)
    assert result.stop_reason == "tool_use"


def test_invoke_stream_matches_non_stream_equivalent_for_tool_use():
    """Direct proof of the brief's "same LLMResponse as non-stream
    equivalent" contract: the non-stream ConverseResponse and the streamed
    event sequence above describe the SAME logical turn (same ids/name/
    args/usage) and must normalize identically."""
    stream_provider = BedrockProvider(
        client=_FakeClient(converse_stream_response={"stream": list(_TOOL_USE_STREAM_EVENTS)})
    )
    non_stream_provider = _client(converse_response=_TOOL_USE_RESPONSE)

    stream_result = stream_provider.invoke_stream(
        system="s", messages=[], tools=[], model="m", params={}, on_text=lambda _: None
    )
    non_stream_result = non_stream_provider.invoke(
        system="s", messages=[], tools=[], model="m", params={}
    )

    assert stream_result == non_stream_result


def test_invoke_stream_keeps_interleaved_tool_use_fragments_separate_by_index():
    """Two tool calls streaming concurrently (deltas for index 0 and index 1
    alternating) must not cross-contaminate each other's accumulated JSON --
    this is the scenario a single shared buffer (instead of per-index
    tracking) would silently corrupt."""
    fake = _FakeClient(
        converse_stream_response={"stream": list(_INTERLEAVED_TWO_TOOL_STREAM_EVENTS)}
    )
    provider = BedrockProvider(client=fake)

    result = provider.invoke_stream(
        system="s", messages=[], tools=[], model="m", params={}, on_text=lambda _: None
    )

    assert result.tool_calls == (
        ToolCall(id="t1", name="skill_a", arguments={"x": 1}),
        ToolCall(id="t2", name="skill_b", arguments={"y": 2}),
    )


def test_invoke_stream_client_error_returns_pinned_error_response():
    error_response = {"Error": {"Code": "ModelTimeoutException", "Message": "timed out"}}
    fake = _FakeClient(raises=ClientError(error_response, "ConverseStream"))
    provider = BedrockProvider(client=fake)

    result = provider.invoke_stream(
        system="s", messages=[], tools=[], model="m", params={}, on_text=lambda _: None
    )

    assert result == LLMResponse(
        text="bedrock error: ModelTimeoutException",
        tool_calls=(),
        stop_reason="error",
        input_tokens=0,
        output_tokens=0,
    )


def test_invoke_stream_event_stream_error_mid_iteration_returns_pinned_error_response():
    """EventStreamError -- a ClientError subclass botocore raises FROM INSIDE
    stream iteration, once the response has already started -- is caught by
    the same except ClientError that guards the initial converse_stream()
    call (see bedrock.py's invoke_stream docstring). on_text calls that
    already fired before the break cannot be un-fired; the RETURNED
    LLMResponse still normalizes to the standard pinned error shape,
    discarding whatever partial text/tool_calls had accumulated."""
    error_response = {"Error": {"Code": "ModelStreamErrorException", "Message": "stream broke"}}

    def _raising_stream():
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "partial"}}}
        raise EventStreamError(error_response, "ConverseStream")

    fake = _FakeClient(converse_stream_response={"stream": _raising_stream()})
    provider = BedrockProvider(client=fake)
    received = []

    result = provider.invoke_stream(
        system="s", messages=[], tools=[], model="m", params={}, on_text=received.append
    )

    assert received == ["partial"]
    assert result == LLMResponse(
        text="bedrock error: ModelStreamErrorException",
        tool_calls=(),
        stop_reason="error",
        input_tokens=0,
        output_tokens=0,
    )


# ---------------------------------------------------------------------------
# router_live -- credential-gated smoke (skips on this machine: no
# AWS_ACCESS_KEY_ID / AWS_PROFILE in the ambient environment)
# ---------------------------------------------------------------------------

_NO_CREDENTIALS_REASON = "no AWS credentials"
_HAS_AWS_CREDENTIALS = bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"))


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_AWS_CREDENTIALS, reason=_NO_CREDENTIALS_REASON)
def test_bedrock_utility_role_live_smoke(monkeypatch):
    """Real Converse call through RoleClient's "utility" role -- the model id
    comes from models.yml (via RoleClient.resolve), never hardcoded here, so
    a config change never needs a matching change in this test. Only shape
    is asserted (non-empty text, positive token counts), never exact text --
    a real model's wording is not this test's business."""
    from poseidon.core.config import Settings
    from poseidon.core.llm.roles import RoleClient

    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@localhost:5432/poseidon")
    monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
    monkeypatch.setenv("LLM_PROFILE", "bedrock")
    monkeypatch.setenv("LLM_MODE", "live")
    settings = Settings(_env_file=None)

    role_client = RoleClient(settings, providers={"bedrock": BedrockProvider()})
    result = role_client.invoke(
        "utility",
        system="Reply with a short greeting, nothing else.",
        messages=[{"role": "user", "content": "Say hello."}],
    )

    assert result.stop_reason != "error"
    assert result.text.strip() != ""
    assert result.input_tokens > 0
    assert result.output_tokens > 0


# ---------------------------------------------------------------------------
# ASCII-only source, matching the Task 1/Task 2 convention
# ---------------------------------------------------------------------------


def test_llm_bedrock_module_files_are_ascii_on_disk():
    paths = (Path(bedrock.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
