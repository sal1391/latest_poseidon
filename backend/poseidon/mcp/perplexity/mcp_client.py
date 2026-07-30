"""PerplexityMcpClient: the MCP-transport implementation of the research
tool (doc 02 section 7, decision D23 -- "mcp" is the standing pattern for
tool servers where MCP is the native surface; "direct", shipped by Task 2,
stays the default transport). Selected the same way "direct" is --
``Settings.tool_transport_perplexity``, resolved lazily by
:meth:`poseidon.mcp.registry.ToolServerRegistry._build_research`.

THE WIRE SEAM, STATED HONESTLY: ``wire`` is a callable of the shape
``wire(method: str, params: dict) -> dict`` -- the JSON-RPC request/
response shape any real MCP transport (a stdio subprocess, a websocket,
...) would speak underneath. This module owns MCP REQUEST SHAPING
(building the ``"tools/call"`` envelope) and RESPONSE NORMALIZATION
(unwrapping the MCP content-block envelope, then handing off to the same
shared parse/validate/recover pipeline the direct adapter uses) -- it does
NOT own, and does not attempt, an actual stdio/websocket wire
implementation. That is deploy-phase work: it needs a real MCP server on
the other end to talk to, which does not exist for this project yet (see
``task-4-brief.md``'s own self-review note: "MCP wire transport
implementation deferred to the phase that has a real MCP server to talk
to"). Every test in ``test_perplexity_mcp_client.py`` exercises this class
against a scripted FAKE wire (a plain Python callable that returns canned
envelopes or raises canned exceptions) -- proving the request-shaping and
normalization logic exhaustively without pretending a network transport
exists that does not.

``ToolServerRegistry._build_research``'s "mcp" branch mirrors this
honesty: it constructs a real, working ``PerplexityMcpClient`` (so
resolving "mcp" today never crashes), but wires it to a placeholder
``wire`` that unconditionally raises the moment anything actually calls
it -- caught by :meth:`PerplexityMcpClient.search` below like any other
wire failure, degrading with reason "mcp wire error" rather than
propagating. A future task that ships a real wire (most likely a stdio
subprocess speaking the MCP protocol) replaces only that one placeholder
call site, not anything in this module.

REUSE, NOT DUPLICATION (Task 3 brief, plan mandate): this module imports
and calls three of the direct adapter's four public parse/validate/recover
helpers -- :func:`~poseidon.mcp.perplexity.adapter.load_schema`,
:func:`~poseidon.mcp.perplexity.adapter.parse_with_recovery`,
:func:`~poseidon.mcp.perplexity.adapter.validate_and_normalize` (see that
module for their exact signatures and the algorithms behind them,
especially :func:`~poseidon.mcp.perplexity.adapter.repair_truncated_json`'s
scan-and-close truncation repair, which this module never calls directly:
it already runs FROM INSIDE ``parse_with_recovery``, so importing it a
second time here with no direct call site of its own would be a dead
import, not reuse). No JSON parsing, schema validation, or truncation-
recovery logic is reimplemented anywhere in this file -- every byte of
that pipeline is the exact same code the direct adapter runs, which is the
whole point of the transport-flip contract test at the bottom of
``test_perplexity_mcp_client.py``: the same recorded content, delivered
through each transport's own envelope shape, must come out the other end
identically shaped.

THE MCP ENVELOPE (the standard MCP content-block response shape, per the
Task 3 brief): ``{"content": [{"type": "text", "text": <JSON string>}]}``
-- this module unwraps the FIRST block of type ``"text"`` (a real MCP
response can mix content types; this does not assume ``content[0]``
specifically is the text block, only that some block in the list is one),
then feeds that block's ``"text"`` string into the SAME
``parse_with_recovery`` -> ``validate_and_normalize`` pipeline the direct
adapter feeds its own ``choices[0].message.content`` string into. The two
outer envelopes look nothing alike (HTTP chat-completion JSON vs. an MCP
content-block array) -- what they both carry, once unwrapped, is the
identical JSON-encoded research payload string, which is why sharing
everything downstream of "get me that string" is correct by design, not
merely convenient: there is exactly one payload shape to parse, validate,
and recover, regardless of which transport delivered it.

DEGRADE RULES (never raises -- mirrors
:meth:`PerplexityDirectAdapter.search`'s own "Never raises" paragraph in
spirit; byte-pinned reasons):

- the wire call itself raises (ANY exception -- unlike the direct
  adapter's narrowly-scoped ``httpx.TimeoutException`` catch, ``wire``
  here is an arbitrary, pluggable callable with no fixed exception
  taxonomy to name specific types against, so every exception it raises
  is treated as a wire failure) -> degrade ``"mcp wire error"``;
- the envelope ``wire()`` returns does not have the expected
  ``{"content": [{"type": "text", "text": <str>}]}`` shape -> degrade
  ``"malformed mcp envelope"``;
- ``parse_with_recovery`` cannot produce valid JSON even after
  ``repair_truncated_json``'s one recovery attempt -> degrade ``"could
  not parse perplexity response"`` (byte-identical to ``adapter.py``'s
  own private ``_REASON_PARSE_FAILED`` string -- "the shared degrade
  path" per the brief: both transports reach this exact failure through
  the exact same reused function on the exact same string, so both must
  report it with the exact same words);
- ``validate_and_normalize`` rejects the parsed JSON (missing the
  schema's required top-level keys) -> degrade ``"perplexity response
  missing required fields"`` (byte-identical to ``adapter.py``'s own
  private ``_REASON_INVALID_SCHEMA``, same "shared degrade path"
  reasoning).

Both "shared" reasons above are kept as this module's OWN local constants
rather than imported from ``adapter.py`` (only the four named functions
are this package's sanctioned cross-module reuse surface; the reason
strings are ``adapter.py``-private, leading-underscore, module-local
constants). Kept in sync by the transport-flip contract test's full-
equality assertion (a divergence would fail that test immediately, not
just this module's own suite) and by
``test_shared_degrade_reasons_match_the_adapters_own_private_constants``,
which is a stronger, machine-checked guarantee against silent drift than
object identity alone would have been.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from poseidon.mcp.perplexity.adapter import (
    load_schema,
    parse_with_recovery,
    validate_and_normalize,
)
from poseidon.mcp.registry import ResearchResult

_TRANSPORT = "mcp"
_TOOL_NAME = "perplexity_search"

# Byte-pinned degrade reasons (house rule). The first two are genuinely
# MCP-specific -- no analog exists on the direct transport. The last two
# are deliberately byte-identical to adapter.py's own private constants of
# the same name/meaning -- see the module docstring's "DEGRADE RULES"
# paragraph for why that is "the shared degrade path," not a coincidence.
_REASON_WIRE_ERROR = "mcp wire error"
_REASON_MALFORMED_ENVELOPE = "malformed mcp envelope"
_REASON_PARSE_FAILED = "could not parse perplexity response"
_REASON_INVALID_SCHEMA = "perplexity response missing required fields"


def _unwrap_text_block(envelope: Any) -> str | None:
    """MCP tool-call response envelope -> the first text block's string,
    or ``None`` if the shape does not match
    ``{"content": [{"type": "text", "text": ...}, ...]}`` closely enough
    to trust -- see the module docstring's "THE MCP ENVELOPE" paragraph.

    Deliberately permissive about EXTRA content blocks (a real MCP
    response can mix text/image/resource blocks; this looks for the first
    ``"text"``-typed one rather than assuming ``content[0]`` specifically
    is it), but strict once that first text block is found: a ``"text"``
    block whose ``"text"`` value is not a string is malformed, not merely
    empty, and this does not keep scanning past it for some other,
    later-positioned text block as a fallback.
    """
    if not isinstance(envelope, dict):
        return None
    content = envelope.get("content")
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            return text if isinstance(text, str) else None
    return None


def _degrade(reason: str) -> ResearchResult:
    """Every degrade path funnels through here -- see
    :func:`poseidon.mcp.perplexity.adapter._degrade`'s own docstring for
    the identical zero-items ``raw_digest`` convention and the reason a
    caller must branch on ``.degraded`` rather than ever inferring
    failure from ``raw_digest`` text.
    """
    return ResearchResult(
        items=(),
        raw_digest=f"0 results via {_TRANSPORT}",
        transport=_TRANSPORT,
        degraded=True,
        degrade_reason=reason,
    )


class PerplexityMcpClient:
    """MCP-transport implementation of :class:`poseidon.mcp.registry
    .ResearchTool`. See the module docstring for the wire seam and the
    reuse/degrade rules.
    """

    def __init__(
        self,
        wire: Callable[[str, dict], dict],
        schema_dir: Path | None = None,
    ) -> None:
        self._wire = wire
        self._schema_dir = schema_dir

    def _load_schema(self, schema_name: str) -> dict:
        """``schema_name`` -> parsed JSON Schema dict. Delegates to the
        adapter's own ``load_schema`` (reuse mandate -- see the module
        docstring) when no explicit ``schema_dir`` override was given at
        construction; reads directly from the override directory
        otherwise. The override exists for test isolation (a scratch
        schema directory), not because this client and the direct adapter
        are expected to diverge on where schemas normally live -- both
        packages sit in the same directory on disk today.
        """
        if self._schema_dir is None:
            return load_schema(schema_name)
        path = self._schema_dir / f"{schema_name}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def search(
        self, *, query: str, schema_name: str, recency_days: int | None = None
    ) -> ResearchResult:
        """One MCP ``tools/call`` round trip: shape the request, call the
        wire, unwrap the response envelope, parse+recover+validate via the
        shared pipeline. Never raises -- see the module docstring's
        "DEGRADE RULES" paragraph for exactly which four failures degrade
        instead of propagating.
        """
        schema = self._load_schema(schema_name)
        arguments: dict[str, Any] = {"query": query, "schema_name": schema_name}
        if recency_days is not None:
            arguments["recency_days"] = recency_days
        params = {"name": _TOOL_NAME, "arguments": arguments}

        try:
            envelope = self._wire("tools/call", params)
        except Exception:
            # Deliberately broad -- see the module docstring's "DEGRADE
            # RULES" paragraph: wire is an arbitrary pluggable callable
            # with no fixed exception taxonomy to name narrowly, unlike
            # the direct adapter's httpx-specific catches.
            return _degrade(_REASON_WIRE_ERROR)

        text = _unwrap_text_block(envelope)
        if text is None:
            return _degrade(_REASON_MALFORMED_ENVELOPE)

        parsed = parse_with_recovery(text)
        if parsed is None:
            return _degrade(_REASON_PARSE_FAILED)

        items = validate_and_normalize(parsed, schema)
        if items is None:
            return _degrade(_REASON_INVALID_SCHEMA)

        return ResearchResult(
            items=items,
            raw_digest=f"{len(items)} results via {_TRANSPORT}",
            transport=_TRANSPORT,
        )
