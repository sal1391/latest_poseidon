"""The shared Anthropic Messages API translation layer (Phase 17 PR C, D79).

Two providers speak the same wire format: the direct Anthropic API and
Snowflake Cortex, whose LLM REST API exposes an Anthropic-compatible Messages
endpoint. Both therefore share this one module: it translates the loop's
request dialect into a Messages API body, normalizes a Messages API response
into :class:`LLMResponse`, and supplies :class:`MessagesAPIProvider`, the base
class with the shared ``httpx`` ``invoke``. A concrete provider adds only
``_endpoint()``, ``_headers()`` and ``error_prefix``.

Sources, read on 2026-10-23 and never written from memory: platform.claude.com's
define-tools page (the tool-name rule ``^[a-zA-Z0-9_-]{1,128}$`` and the
``tool_choice`` forms ``{"type": "tool", "name"}`` / ``{"type": "auto",
"disable_parallel_tool_use"}``); its Messages API reference and handle-tool-calls
page (``tool_result`` with ``tool_use_id``/``content``/``is_error``, whose
content accepts only text and image blocks; ``thinking``/``redacted_thinking``
blocks; the ``stop_reason`` values); and Snowflake's Cortex LLM REST API page
(the Anthropic-compatible Messages endpoint).

The dialect above the seam
--------------------------
``messages`` are Converse-native content blocks with dotted skill ids (see
``roles.py``'s ``LLMProvider`` docstring): the loop speaks that grammar, and
each provider translates FROM it at its own boundary, as ``bedrock.py`` does.

Request translation (``build_request``):

- ``model``, ``max_tokens`` (``params["max_tokens"]``, else
  ``DEFAULT_MAX_TOKENS``: the Messages API requires the field), ``messages``.
- ``system`` (a string) only when non-empty after any instruct-mode append.
- ``temperature`` only when present in params; ``thinking`` only when present
  (a dict, forwarded verbatim). Every other param key (``enabled``,
  ``tool_choice_mode``, anything unknown) is dropped, exactly as
  ``bedrock.py``'s ``_build_inference_config`` drops it, so an unrecognized
  knob never becomes a 400.
- ``tools`` (``{"name": wire, "description", "input_schema"}``) only when the
  tool list is non-empty.
- ``tool_choice`` only when the caller passed one. The mode is
  ``params.get("tool_choice_mode", "native")``. ``native`` forces the tool
  (``{"type": "tool", "name": wire}``). ``instruct`` sends ``{"type": "auto",
  "disable_parallel_tool_use": True}`` and appends
  :data:`FORCED_TOOL_INSTRUCTION` to the system prompt after a blank line (or
  makes it the whole system prompt when that was empty), for a model or
  endpoint that rejects a forced tool choice.

Block translation (``_build_block``), Converse -> Messages API:

==========================================  ==========================================
``{"text": t}``                             ``{"type": "text", "text": t}``
``{"toolUse": {toolUseId, name, input}}``   ``{"type": "tool_use", id, name: wire, input}``
``{"toolResult": {toolUseId, content,       ``{"type": "tool_result", tool_use_id,
status}}``                                  content: [...]}`` + ``is_error: True`` only
                                            when status is ``"error"``
  inside: ``{"text": t}``                   ``{"type": "text", "text": t}``
  inside: ``{"json": obj}``                 ``{"type": "text", "text": json.dumps(obj,
                                            sort_keys=True)}`` (a tool result takes only
                                            text/image; the loop's error block is JSON)
``{"reasoningContent": {"reasoningText":    ``{"type": "thinking", "thinking": text,
{text, signature}}}``                       "signature": signature}``
``{"reasoningContent":                      ``{"type": "redacted_thinking", "data": data}``
{"redactedContent": data}}``
==========================================  ==========================================

String message content stays a string. Tool names cross the same ``"." <->
"__"`` boundary ``bedrock.py`` uses (dots fail the name regex above), with the
same pure, unconditional functions: no registry lookup, no validation.

Response normalization (``normalize_response``):

- ``usage.input_tokens``/``output_tokens``, 0 when ``usage`` or a key is missing.
- ``stop_reason == "tool_use"``: ``tool_calls`` from every ``tool_use`` block
  (name reverse-translated; ``input`` must be a dict), ``reasoning`` from every
  ``thinking``/``redacted_thinking`` block in order, as Converse
  ``reasoningContent`` blocks (D80: the 5.5 generation requires them echoed
  back ahead of the tool_use blocks), and ``text=""`` (the Bedrock rule:
  commentary beside a tool call is not carried).
- ``stop_reason == "refusal"``: the pinned ``"<prefix> error: refusal"``. The
  loop turns an error into a 502 problem part; an empty end_turn would render
  as a blank reply.
- Every other ``stop_reason`` (``end_turn``, ``max_tokens``, ``stop_sequence``,
  ``pause_turn``, anything new): ``end_turn`` with the text blocks joined,
  thinking blocks skipped, no tool calls.
- Any malformed shape (``KeyError``/``IndexError``/``TypeError``/``ValueError``
  while reading it) becomes the pinned ``"<prefix> error: MalformedResponse"``.

Contracts:

- ``invoke`` never raises for a provider failure: a transport error, a 4xx/5xx,
  a non-JSON 2xx and a malformed body all return ``stop_reason="error"`` with
  ``text="<prefix> error: <code>"``. The response body is never echoed into
  ``text``; the surface stays fixed and small.
- It does raise ``ValueError`` for two caller bugs: ``tool_choice`` with no
  tools, and an unknown ``tool_choice_mode`` (plus an unsupported content
  block). And ``_endpoint()``/``_headers()`` may raise
  :class:`ProviderCredentialsError` when the provider is not configured: a
  deployment error, not a model turn.
- Nothing here mutates ``messages``, ``tools`` or ``params``; every helper
  builds new dicts, so a caller's growing history list can be passed again
  on the next loop iteration untouched.
- The ``httpx.Client`` is built lazily on the first ``invoke`` (the same
  reason ``bedrock.py`` defers ``boto3.client``: registries build providers
  unconditionally in offline tests and stub-mode boots).
"""

import json

import httpx

from poseidon.core.llm.types import LLMResponse, ToolCall

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096
TOOL_CHOICE_MODES = ("native", "instruct")
MALFORMED_RESPONSE_CODE = "MalformedResponse"
REFUSAL_CODE = "refusal"

FORCED_TOOL_INSTRUCTION = (
    "Respond only by calling the tool named `{name}`, with every required field filled. "
    "Do not reply with text."
)


class ProviderCredentialsError(RuntimeError):
    """The provider has no usable endpoint or credentials (a missing key or
    account). Raised by ``_endpoint()``/``_headers()`` before any request is
    sent: a configuration problem for the operator, not a model failure to
    fold into an error turn."""


def to_wire_tool_name(skill_id: str) -> str:
    """Skill id -> Messages API tool name (``"data_qa.metric_query"`` ->
    ``"data_qa__metric_query"``). Pure and unconditional, like
    ``bedrock.py``'s; the registry already guarantees ids stay distinct."""
    return skill_id.replace(".", "__")


def from_wire_tool_name(wire: str) -> str:
    """The mechanical inverse of :func:`to_wire_tool_name`. A hallucinated
    name still maps to some string; dispatch's 404 catches it, not this."""
    return wire.replace("__", ".")


def error_response(error_prefix: str, code: str) -> LLMResponse:
    """The one structured error shape every failure path returns."""
    return LLMResponse(
        text=f"{error_prefix} error: {code}",
        tool_calls=(),
        stop_reason="error",
        input_tokens=0,
        output_tokens=0,
    )


# ---------------------------------------------------------------------------
# request building -- Converse dialect -> Messages API body. Every helper
# returns a NEW structure; none mutates what it is given.
# ---------------------------------------------------------------------------


def build_request(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    model: str,
    params: dict,
    tool_choice: str | None,
) -> dict:
    """The JSON body for one Messages API call (rules: module docstring)."""
    body: dict = {
        "model": model,
        "max_tokens": params.get("max_tokens", DEFAULT_MAX_TOKENS),
        "messages": _build_messages(messages),
    }
    if "temperature" in params:
        body["temperature"] = params["temperature"]
    if "thinking" in params:
        body["thinking"] = params["thinking"]
    if tools:
        body["tools"] = _build_tools(tools)
    if tool_choice is not None:
        if not tools:
            raise ValueError("tool_choice requires at least one tool")
        system = _apply_tool_choice(body, system, params, tool_choice)
    if system:
        body["system"] = system
    return body


def _apply_tool_choice(body: dict, system: str, params: dict, tool_choice: str) -> str:
    """Sets ``body["tool_choice"]`` for the configured mode and returns the
    system prompt to send (unchanged for ``native``; with the forced-tool
    instruction appended for ``instruct``)."""
    mode = params.get("tool_choice_mode", "native")
    if mode not in TOOL_CHOICE_MODES:
        raise ValueError(f"unknown tool_choice_mode {mode!r}; expected one of {TOOL_CHOICE_MODES}")
    wire = to_wire_tool_name(tool_choice)
    if mode == "native":
        body["tool_choice"] = {"type": "tool", "name": wire}
        return system
    body["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    instruction = FORCED_TOOL_INSTRUCTION.format(name=wire)
    return f"{system}\n\n{instruction}" if system else instruction


def _build_tools(tools: list[dict]) -> list[dict]:
    """``SkillRegistry.tool_schema`` entries already carry the Messages API
    field names; only the name needs its wire spelling."""
    return [
        {
            "name": to_wire_tool_name(schema["name"]),
            "description": schema["description"],
            "input_schema": schema["input_schema"],
        }
        for schema in tools
    ]


def _build_messages(messages: list[dict]) -> list[dict]:
    """String content stays a string (the Messages API accepts it); list
    content is translated block by block."""
    return [
        {
            "role": message["role"],
            "content": message["content"]
            if isinstance(message["content"], str)
            else [_build_block(block) for block in message["content"]],
        }
        for message in messages
    ]


def _build_block(block: dict) -> dict:
    """One Converse content block -> one Messages API block (the table in the
    module docstring). Anything else is a caller bug, raised loudly."""
    if "text" in block:
        return {"type": "text", "text": block["text"]}
    if "toolUse" in block:
        tool_use = block["toolUse"]
        return {
            "type": "tool_use",
            "id": tool_use["toolUseId"],
            "name": to_wire_tool_name(tool_use["name"]),
            "input": tool_use["input"],
        }
    if "toolResult" in block:
        result = block["toolResult"]
        built = {
            "type": "tool_result",
            "tool_use_id": result["toolUseId"],
            "content": [_build_tool_result_content(item) for item in result["content"]],
        }
        if result.get("status") == "error":
            built["is_error"] = True
        return built
    reasoning = block.get("reasoningContent")
    if reasoning is not None and "reasoningText" in reasoning:
        text = reasoning["reasoningText"]
        return {"type": "thinking", "thinking": text["text"], "signature": text["signature"]}
    if reasoning is not None and "redactedContent" in reasoning:
        return {"type": "redacted_thinking", "data": reasoning["redactedContent"]}
    raise ValueError(f"unsupported content block: {sorted(block)}")


def _build_tool_result_content(item: dict) -> dict:
    """A tool result's inner block. The Messages API takes only text and
    image here, so the loop's JSON error block is sent as sorted JSON text."""
    if "text" in item:
        return {"type": "text", "text": item["text"]}
    if "json" in item:
        return {"type": "text", "text": json.dumps(item["json"], sort_keys=True)}
    raise ValueError(f"unsupported content block: {sorted(item)}")


# ---------------------------------------------------------------------------
# response normalization -- Messages API response -> LLMResponse
# ---------------------------------------------------------------------------


def normalize_response(body: dict, *, error_prefix: str) -> LLMResponse:
    """A Messages API response body -> :class:`LLMResponse` (rules: module
    docstring). One ``try`` around every read, as in ``bedrock.py``'s
    ``_normalize_response``: a 2xx body is not guaranteed well formed, and
    ``invoke`` promises never to raise for a provider failure. ``ValueError``
    joins ``bedrock.py``'s three caught types so that any value-shaped
    surprise lands in the same pinned error; a non-object body or ``usage``
    (a JSON list, say) is raised as ``TypeError`` below for the same reason,
    rather than escaping as an ``AttributeError`` from ``.get``."""
    try:
        if not isinstance(body, dict):
            raise TypeError("response body is not an object")
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            raise TypeError("usage is not an object")
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        stop_reason = body["stop_reason"]
        content = body["content"]
        if stop_reason == "refusal":
            return error_response(error_prefix, REFUSAL_CODE)
        if stop_reason == "tool_use":
            return _normalize_tool_use(content, input_tokens, output_tokens)
        text = "".join(block["text"] for block in content if block["type"] == "text")
        return LLMResponse(
            text=text,
            tool_calls=(),
            stop_reason="end_turn",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return error_response(error_prefix, MALFORMED_RESPONSE_CODE)


def _normalize_tool_use(content: list[dict], input_tokens: int, output_tokens: int) -> LLMResponse:
    """The tool_use branch: calls and reasoning carried, text dropped."""
    tool_calls = []
    reasoning = []
    for block in content:
        kind = block["type"]
        if kind == "tool_use":
            arguments = block["input"]
            if not isinstance(arguments, dict):
                raise TypeError("tool_use input is not an object")
            name = from_wire_tool_name(block["name"])
            tool_calls.append(ToolCall(id=block["id"], name=name, arguments=arguments))
        elif kind in ("thinking", "redacted_thinking"):
            reasoning.append(_reasoning_block(block))
    return LLMResponse(
        text="",
        tool_calls=tuple(tool_calls),
        stop_reason="tool_use",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning=tuple(reasoning),
    )


def _reasoning_block(block: dict) -> dict:
    """A ``thinking``/``redacted_thinking`` block -> the Converse
    ``reasoningContent`` block ``LLMResponse.reasoning`` carries (D80); the
    exact inverse of the two reasoning rows in ``_build_block``."""
    if block["type"] == "thinking":
        return {
            "reasoningContent": {
                "reasoningText": {"text": block["thinking"], "signature": block["signature"]}
            }
        }
    return {"reasoningContent": {"redactedContent": block["data"]}}


def _error_code(response: httpx.Response) -> str:
    """``error.type`` from a Messages API error body (``authentication_error``,
    ``rate_limit_error``, ...), else ``"HTTP <status>"``. Only the type is
    read; the message is never surfaced."""
    try:
        code = response.json()["error"]["type"]
    except (ValueError, KeyError, IndexError, TypeError):
        return f"HTTP {response.status_code}"
    return code if isinstance(code, str) else f"HTTP {response.status_code}"


# ---------------------------------------------------------------------------
# the shared provider
# ---------------------------------------------------------------------------


class MessagesAPIProvider:
    """Base class for a provider that speaks the Messages API over HTTP.

    Subclasses set ``error_prefix`` (the first word of every error ``text``)
    and implement ``_endpoint()`` and ``_headers()``; both may raise
    :class:`ProviderCredentialsError`. Everything else is shared.
    """

    error_prefix: str

    def __init__(
        self, *, client: httpx.Client | None = None, timeout_seconds: float = 120.0
    ) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds

    def _endpoint(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _client_or_build(self) -> httpx.Client:
        """Lazy client: built on first use unless one was injected."""
        if self._client is None:
            self._client = httpx.Client(timeout=httpx.Timeout(self._timeout_seconds))
        return self._client

    def invoke(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        model: str,
        params: dict,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        """One Messages API call: build, post, normalize. Never raises for a
        provider failure (see the module docstring's contracts)."""
        body = build_request(
            system=system,
            messages=messages,
            tools=tools,
            model=model,
            params=params,
            tool_choice=tool_choice,
        )
        url = self._endpoint()
        headers = self._headers()
        client = self._client_or_build()
        try:
            response = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            return error_response(self.error_prefix, type(exc).__name__)
        if response.status_code >= 400:
            return error_response(self.error_prefix, _error_code(response))
        try:
            parsed = response.json()
        except ValueError:
            return error_response(self.error_prefix, MALFORMED_RESPONSE_CODE)
        return normalize_response(parsed, error_prefix=self.error_prefix)
