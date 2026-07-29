"""BedrockProvider: normalizes Amazon Bedrock's Converse/ConverseStream APIs
to the LLMProvider seam (doc 03 section 1, decision D11).

Every field name this module reads or writes -- ``modelId``, ``system``,
``messages[].content[]``, ``toolConfig.tools[].toolSpec``, ``inferenceConfig``,
``output.message.content[].toolUse``, ``usage.inputTokens``/``outputTokens``,
the ``StopReason`` enum, and the ConverseStream event names
``contentBlockStart``/``contentBlockDelta``/``contentBlockStop``/
``messageStop``/``metadata`` -- was read from the installed botocore
package's own bedrock-runtime service model
(``botocore/data/bedrock-runtime/2023-09-30/service-2.json.gz``, shapes
``ConverseRequest``/``ConverseResponse``/``ConverseStreamOutput`` and their
members), never from memory. That file is the ground truth boto3 itself
validates requests against and deserializes responses from, so a
hand-authored test dict that matches it exercises the same shape a real call
would produce or accept.

Two normalization rules worth stating up front, because nothing else in this
file states them locally:

- ``LLMResponse.stop_reason``'s documented domain is only ``tool_use``/
  ``end_turn``/``error`` (``types.py``). Bedrock's ``StopReason`` enum has
  nine values; every one of them BESIDES ``tool_use`` normalizes into the
  ``end_turn`` branch (joined text, no tool calls) here -- ``error`` is
  reserved for transport failures (a caught ``ClientError``), never a value
  the model itself reports.
- A response mixing a text block and a ``toolUse`` block in the same turn
  (``stopReason: "tool_use"``) normalizes with ``text=""``, matching
  ``LLMResponse.text``'s own documented contract ("'' when pure tool_use").
  Any commentary text emitted alongside a tool call is not carried into the
  structured response -- a streaming caller already saw it live via
  ``on_text`` (see :meth:`BedrockProvider.invoke_stream`).

Tool names cross a translation boundary at every ``toolConfig``/``toolUse``
touch point (plan amendment aa33a2f, fix round 1): Bedrock's ``ToolName``
pattern (``[a-zA-Z0-9_-]+``, verified in service-2.json) excludes ``.``
entirely, but this codebase's skill ids are dotted (``"data_qa.metric_
query"``). :func:`_to_bedrock_tool_name`/:func:`_from_bedrock_tool_name`
translate ``"." <-> "__"`` at exactly four sites -- forward in
``_build_tool_config`` (the tool definitions) and in
:func:`_build_content_block` (the assistant history's own echoed
``toolUse``, Task 4's discovery), reverse in both tool_use normalization
paths (``_normalize_response``, ``_consume_stream``) -- and are deliberately
pure, unconditional, and mechanical: no registry lookup and no validation
happen here. A hallucinated wire name the model invented (never produced by
the forward function for any real skill id) still reverse-maps to SOME
string without raising; an unknown skill id is dispatch's existing 404 to
catch, never this provider's job. The reverse map is likewise a NO-OP on a
name that is already dotted (there is no ``"__"`` in it to replace), which
matters more than it looks: the router prompt shows skill ids dotted while
``toolConfig`` carries the wire spelling -- a deliberate split -- so a model
that copies the prompt's spelling into its ``toolUse`` instead of the tool
definition's still dispatches to the right skill. Whether that split costs
routing accuracy is a live-key question, not one an offline test can
answer. The invariant this translation relies on -- that every registered
skill id survives the round trip, and so that no two DIFFERENT ids ever map
to the same wire name -- is guaranteed one layer up, at discovery time, by
``core/skills/registry.py``'s own ``SkillDefinitionError`` checks (the
registry is where ids are minted; this module only ever consumes them).

The client is built lazily (:meth:`BedrockProvider._client_or_build`, first
call only) rather than in ``__init__``: constructing a ``boto3``
bedrock-runtime client touches AWS config/credential resolution, and
``BedrockProvider`` gets instantiated by provider registries that offline
tests and stub-mode boots build unconditionally (see ``roles.py``'s
``RoleClient``) -- those must never depend on AWS being configured just to
exist. Importing the ``boto3`` package itself has no such side effect (only
``boto3.client(...)`` does), so the import stays at module level like every
other third-party import in this codebase; only the client CONSTRUCTION is
deferred. A caller that already has a client (real, or a test double) passes
it to ``client=`` and this class never calls ``boto3.client`` at all.

Every request-building helper below constructs a new dict/list from what it
reads; none ever mutate ``messages``/``tools``/``params`` in place, so the
SAME list a caller builds once (a role's tool schema list, a growing message
window) can be handed to ``invoke``/``invoke_stream`` repeatedly with no
defensive copy needed at the call site.
"""

import json
from collections.abc import Callable

import boto3
from botocore.exceptions import ClientError

from poseidon.core.llm.types import LLMResponse, ToolCall

# The one StopReason value (of nine -- see module docstring) that normalizes
# to tool_calls instead of text. Named so every branch below reads as "is
# this the tool-use case" rather than repeating a bare string literal.
_TOOL_USE_STOP_REASON = "tool_use"


def _to_bedrock_tool_name(skill_id: str) -> str:
    """Skill id -> Bedrock-safe wire ``ToolName`` (``"data_qa.metric_query"``
    -> ``"data_qa__metric_query"``). Pure and unconditional -- see the module
    docstring's tool-name-translation paragraph for why this needs no
    validation of its own (the registry already guarantees injectivity
    across every id this is ever called with)."""
    return skill_id.replace(".", "__")


def _from_bedrock_tool_name(wire_name: str) -> str:
    """The mechanical inverse of :func:`_to_bedrock_tool_name`. Unconditional
    on purpose: no registry lookup, no validation. A wire name the model
    hallucinated (never produced by the forward function for any real skill
    id) still reverse-maps to SOME string here -- it fails later, at
    dispatch's existing 404, never in this provider."""
    return wire_name.replace("__", ".")


class BedrockProvider:
    """Bedrock Converse/ConverseStream, normalized to the LLMProvider seam.

    ``client`` stays ``None`` until the first :meth:`invoke`/
    :meth:`invoke_stream` call if not supplied at construction -- see the
    module docstring's lazy-client rule.
    """

    def __init__(self, region: str = "us-east-1", client=None) -> None:
        self._region = region
        self._client = client

    def _client_or_build(self):
        if self._client is None:
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def invoke(
        self, *, system: str, messages: list[dict], tools: list[dict], model: str, params: dict
    ) -> LLMResponse:
        """One Converse call: build the request, call, normalize the
        response. Never raises -- a ``ClientError`` (bad request, throttled,
        model unavailable, ...) becomes ``LLMResponse(stop_reason="error",
        ...)`` so a caller (the Task 4 agent loop) always gets a structured
        turn result to act on, not an exception to catch."""
        request = _build_request(
            system=system, messages=messages, tools=tools, model=model, params=params
        )
        client = self._client_or_build()
        try:
            response = client.converse(**request)
        except ClientError as exc:
            return _error_response(exc)
        return _normalize_response(response)

    def invoke_stream(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        model: str,
        params: dict,
        on_text: Callable[[str], None],
    ) -> LLMResponse:
        """ConverseStream: same request as :meth:`invoke`, ``on_text`` fired
        once per text delta as the response streams in, final return value
        normalized exactly like :meth:`invoke`'s equivalent whole response
        would be.

        One ``try`` covers both the initial call and iterating the stream:
        botocore raises ``EventStreamError`` -- a ``ClientError`` subclass
        with no override of its own (``class EventStreamError(ClientError):
        pass``) -- from INSIDE the iteration, once the response has already
        started, not from the initial ``converse_stream()`` call returning.
        Catching the base ``ClientError`` around the whole block handles
        both "the request was rejected up front" and "the stream failed
        partway through" with the identical pinned error response, without a
        second except clause naming ``EventStreamError`` explicitly.
        """
        request = _build_request(
            system=system, messages=messages, tools=tools, model=model, params=params
        )
        client = self._client_or_build()
        try:
            response = client.converse_stream(**request)
            return _consume_stream(response["stream"], on_text)
        except ClientError as exc:
            return _error_response(exc)


def _error_response(exc: ClientError) -> LLMResponse:
    """Bedrock/botocore failures never raise out of the provider (see the
    class docstrings). ``exc.response["Error"]["Code"]`` is the field a
    genuine ``ClientError`` always carries; the ``.get`` fallback to
    "Unknown" mirrors ``ClientError.__init__``'s own defensive handling of
    the same field (it builds its exception message the identical way), so
    an incomplete error dict here still yields a structured response instead
    of a second exception while already handling the first one."""
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    return LLMResponse(
        text=f"bedrock error: {code}",
        tool_calls=(),
        stop_reason="error",
        input_tokens=0,
        output_tokens=0,
    )


# ---------------------------------------------------------------------------
# request building -- system/messages/tools/params -> a dict shaped exactly
# like ConverseRequest's members (verified against service-2.json; see
# module docstring). Every function here reads its arguments and returns a
# NEW structure; none mutate what they are given.
# ---------------------------------------------------------------------------


def _build_request(
    *, system: str, messages: list[dict], tools: list[dict], model: str, params: dict
) -> dict:
    request: dict = {"modelId": model, "messages": _build_messages(messages)}
    if system:
        request["system"] = [{"text": system}]
    inference_config = _build_inference_config(params)
    if inference_config:
        request["inferenceConfig"] = inference_config
    if tools:
        request["toolConfig"] = _build_tool_config(tools)
    return request


def _build_messages(messages: list[dict]) -> list[dict]:
    return [_build_message(message) for message in messages]


def _build_message(message: dict) -> dict:
    """``content`` may already be a list of Converse content blocks (passed
    through as a NEW list -- never the caller's own list object, e.g. the
    toolResult message Task 4's agent loop appends -- though the block DICTS
    inside it are shared, shallow-copy only EXCEPT for a ``toolUse`` block,
    which is rebuilt rather than shared: see :func:`_build_content_block`)
    or plain text (the shape ``RoleClient``'s own tests pass), wrapped as
    the one-block ``[{"text": content}]`` Converse requires."""
    content = message["content"]
    if isinstance(content, str):
        return {"role": message["role"], "content": [{"text": content}]}
    return {"role": message["role"], "content": [_build_content_block(b) for b in content]}


def _build_content_block(block: dict) -> dict:
    """The fourth tool-name translation site (see the module docstring's
    translation paragraph for the other three).

    Once the agent loop runs a second iteration it sends the model's OWN
    previous ``toolUse`` block back as assistant history, and
    ``ToolUseBlock.name`` is the same ``ToolName`` shape as
    ``toolSpec.name`` (service-2.json) -- a dotted skill id is rejected
    there exactly as it would be in a tool definition. The loop above this
    seam writes dotted ids ONLY; translating them is this module's job, and
    doing it here rather than at the loop keeps that true for every provider
    that will ever consume the same message list.

    A rebuilt dict, never an in-place edit: the caller's history list is the
    same object across every iteration of a turn, so writing the wire name
    back into it would make the NEXT iteration translate an already-
    translated name.
    """
    tool_use = block.get("toolUse")
    if tool_use is None:  # text/image/toolResult/... carry no ToolName
        return block
    return {**block, "toolUse": {**tool_use, "name": _to_bedrock_tool_name(tool_use["name"])}}


def _build_inference_config(params: dict) -> dict:
    """``max_tokens``/``temperature`` map onto ``InferenceConfiguration``'s
    ``maxTokens``/``temperature`` members; every other key -- ``enabled``
    (``RoleClient``'s own on/off gate; a role invoked at all is already
    "on") and any future tuning knob this provider does not yet understand
    -- is dropped rather than forwarded, so an unrecognized param never
    becomes a Bedrock ``ValidationException``."""
    config: dict = {}
    if "max_tokens" in params:
        config["maxTokens"] = params["max_tokens"]
    if "temperature" in params:
        config["temperature"] = params["temperature"]
    return config


def _build_tool_config(tools: list[dict]) -> dict:
    """Wraps each tool-schema entry (``{"name", "description",
    "input_schema"}`` -- ``SkillRegistry.tool_schema``'s shape) into
    Converse's ``Tool``/``ToolSpecification`` shape. Only called when
    ``tools`` is non-empty (see ``_build_request``): Converse's
    ``ToolConfigurationToolsList`` has a ``min: 1`` constraint, so an empty
    ``toolConfig.tools`` would be a guaranteed ``ValidationException``, not
    a harmless no-op -- omitting the whole key is the only correct
    representation of "no tools this call". ``name`` is translated to its
    Bedrock-safe wire form (see the module docstring) -- the dotted skill id
    itself is never sent."""
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": _to_bedrock_tool_name(schema["name"]),
                    "description": schema["description"],
                    "inputSchema": {"json": schema["input_schema"]},
                }
            }
            for schema in tools
        ]
    }


# ---------------------------------------------------------------------------
# response normalization -- ConverseResponse -> LLMResponse
# ---------------------------------------------------------------------------


def _normalize_response(response: dict) -> LLMResponse:
    stop_reason = response["stopReason"]
    usage = response.get("usage") or {}
    input_tokens = usage.get("inputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)
    content = response["output"]["message"]["content"]

    if stop_reason == _TOOL_USE_STOP_REASON:
        tool_calls = tuple(
            ToolCall(
                id=block["toolUse"]["toolUseId"],
                name=_from_bedrock_tool_name(block["toolUse"]["name"]),
                arguments=block["toolUse"]["input"],
            )
            for block in content
            if "toolUse" in block
        )
        return LLMResponse(
            text="",
            tool_calls=tool_calls,
            stop_reason="tool_use",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    text = "".join(block["text"] for block in content if "text" in block)
    return LLMResponse(
        text=text,
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# ---------------------------------------------------------------------------
# stream consumption -- ConverseStreamOutput events -> LLMResponse
# ---------------------------------------------------------------------------


def _consume_stream(stream, on_text: Callable[[str], None]) -> LLMResponse:
    """Replays a ConverseStream event iterator into the same LLMResponse
    shape :func:`_normalize_response` would produce from the equivalent
    whole response, firing ``on_text`` once per text delta as it arrives.

    ``toolUse`` blocks stream their ``input`` as successive raw-JSON-text
    FRAGMENTS (``ContentBlockDelta.toolUse.input`` is a plain ``String``),
    unlike a non-streamed response's ``toolUse.input`` (a ``Document`` --
    already-parsed JSON). Fragments are tracked per ``contentBlockIndex`` (a
    stream can interleave more than one tool call's deltas -- see the
    interleaved-index regression test) and joined then ``json.loads``-ed
    only once the stream ends.
    """
    text_parts: list[str] = []
    pending_tool_use: dict[int, dict] = {}
    stop_reason = "end_turn"
    input_tokens = 0
    output_tokens = 0

    for event in stream:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"]["start"]
            if "toolUse" in start:
                index = event["contentBlockStart"]["contentBlockIndex"]
                pending_tool_use[index] = {
                    "id": start["toolUse"]["toolUseId"],
                    "name": start["toolUse"]["name"],
                    "fragments": [],
                }
        elif "contentBlockDelta" in event:
            delta_event = event["contentBlockDelta"]
            delta = delta_event["delta"]
            if "text" in delta:
                text_parts.append(delta["text"])
                on_text(delta["text"])
            elif "toolUse" in delta:
                index = delta_event["contentBlockIndex"]
                # Relies on AWS's documented event ordering -- a
                # contentBlockStart always precedes any contentBlockDelta for
                # the same contentBlockIndex, so pending_tool_use[index] is
                # guaranteed to exist here; no defensive .get()/KeyError guard.
                pending_tool_use[index]["fragments"].append(delta["toolUse"]["input"])
        elif "messageStop" in event:
            stop_reason = event["messageStop"]["stopReason"]
        elif "metadata" in event:
            usage = event["metadata"].get("usage") or {}
            input_tokens = usage.get("inputTokens", 0)
            output_tokens = usage.get("outputTokens", 0)
        # contentBlockStop/messageStart carry nothing this normalization
        # needs and are intentionally not special-cased.

    if stop_reason == _TOOL_USE_STOP_REASON:
        tool_calls = tuple(
            ToolCall(
                id=entry["id"],
                name=_from_bedrock_tool_name(entry["name"]),
                arguments=_parse_tool_input(entry["fragments"]),
            )
            for _, entry in sorted(pending_tool_use.items())
        )
        return LLMResponse(
            text="",
            tool_calls=tool_calls,
            stop_reason="tool_use",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    return LLMResponse(
        text="".join(text_parts),
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _parse_tool_input(fragments: list[str]) -> dict:
    raw = "".join(fragments)
    return json.loads(raw) if raw else {}
