"""PerplexityDirectAdapter: the direct REST transport for the research tool
(doc 02 section 7, decision D23 -- direct is the default transport).

This is the wfs_core ``PerplexityClient`` pattern (``agents/researcher.py``'s
``_call_perplexity``/``_recover_truncated_json`` in this same repo's earlier
prototype), carried over and hardened for Phase 7: one call shape instead of
four schema-specific prompts, a pure scan-and-close recovery function with
its own unit tests instead of a backwards prefix-search, and byte-pinned
degrade reasons instead of ``print()`` plus ``None``. The shape a caller
gets back is always a :class:`~poseidon.mcp.registry.ResearchResult` --
imported from ``poseidon.mcp.registry``, never redefined here (that module
owns the type; this module only produces instances of it).

Lazy client (mirrors :class:`poseidon.core.llm.bedrock.BedrockProvider`'s
``client=None`` / ``_client_or_build`` pattern exactly): constructing an
``httpx.Client`` has no network side effect by itself, but
``PerplexityDirectAdapter`` is what :meth:`poseidon.mcp.registry
.ToolServerRegistry._build_research` constructs on first ``.research``
access, and this codebase's rule (see that module's docstring) is that
credential/transport objects come into being only when a caller actually
needs them -- constructing the adapter itself must not require ``httpx`` to
do anything. A caller that already has a client (real, or a test double)
passes it via ``client=`` and this class never calls ``httpx.Client()`` at
all, exactly like ``BedrockProvider`` never calls ``boto3.client(...)``
when handed one.

Never raises (:meth:`PerplexityDirectAdapter.search`): the four failure
modes this module is pinned against -- a request timeout, a non-2xx HTTP
response, a 2xx response whose body doesn't have the expected ``choices[0]
.message.content`` shape (fix round 1, Critical C1), and a response body
that is not valid JSON even after truncation recovery -- each produce a
``ResearchResult(degraded=True, ...)`` instead of propagating an exception,
so a skill's tool call always gets a structured answer to render an honest
"unavailable" message from, never a crash mid turn. This mirrors
``BedrockProvider.invoke``'s own scope: that method catches botocore's
``ClientError`` specifically, not every exception a network call could
conceivably raise, and this adapter is scoped the same deliberate way --
``httpx.TimeoutException``, a bad status code, and ``(KeyError, IndexError,
TypeError)`` around the envelope-extraction chain are caught because they
are this module's four pinned cases; a stranger transport failure (DNS
resolution, a connection reset) is NOT wrapped here and propagates,
matching ``BedrockProvider``'s own precedent of catching named exceptions
rather than bare ``Exception``. (For the record: ``BedrockProvider`` has
the identical blind spot C1 fixed here on its own response-extraction
chain -- confirmed during this fix round's review, ledgered for a P5 fix
elsewhere, not touched by this module.)

Truncation recovery (:func:`repair_truncated_json`): Perplexity, like any
LLM-backed API, can hit its own output-token ceiling mid-response, handing
back a ``content`` string that is valid JSON up to the exact byte it was
cut off at and garbage (by omission) after. The fix is mechanical and does
not need to understand the schema being requested: replay the text
character by character tracking whether the cursor is inside a string
literal (so a brace inside a string value is not mistaken for real
structure) and a stack of the brace/bracket characters still open; at end
of text, close an unterminated string first (a stray closing quote fixes
the most common truncation point -- mid string value), then pop the
bracket stack, closing each opener with its matching character, innermost
(most recently opened) first. This can still fail to produce valid JSON --
a cut that lands right after a trailing comma, or right after a key's
colon with no value written yet, has no fix this algorithm attempts (no
placeholder value is ever invented) -- and the caller
(:func:`parse_with_recovery`) re-parses exactly once after repair,
degrading if that also fails. See ``fixtures/unrecoverable.json`` for a
recorded example of exactly that shape.

Schema loading (:func:`load_schema`) and the parse/validate pipeline below
are deliberately free functions, not methods, and are NOT underscore-
prefixed: doc 02 section 7's file map has Task 3's ``mcp_client.py`` (the
MCP-transport client, same package, same directory) reuse this exact
parse/validate/recover path against its own transport's response envelope
("REUSE the adapter's parse/validate/recover helpers by importing them, no
duplication") -- these functions are this package's shared internal API
between the two transports, not merely this module's private
implementation detail, so they are named as such.
"""

import json
from pathlib import Path
from typing import Any

import httpx

from poseidon.mcp.registry import ResearchResult

# The marine-lens system line every request carries, verbatim per the Task 2
# brief -- pinned, not reworded, since it is part of the outbound request
# contract a test asserts against exactly.
SYSTEM_PROMPT = (
    "You research the marine fuels and shipping-services industry. "
    "Answer strictly in the requested JSON schema."
)

# recency_days -> Perplexity's own search_recency_filter values. Pinned
# exactly per the brief; any int not a key here (including None) omits the
# filter entirely rather than guessing -- see _build_payload.
RECENCY_FILTERS: dict[int, str] = {7: "week", 30: "month", 365: "year"}

_TRANSPORT = "direct"
_API_URL = "https://api.perplexity.ai/chat/completions"
_SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

# Byte-pinned degrade reasons (house rule): each is a fixed, deterministic
# string a test asserts literally -- see the module docstring's "never
# raises" paragraph for which failure produces which reason.
_REASON_TIMEOUT = "perplexity request timed out"
_REASON_PARSE_FAILED = "could not parse perplexity response"
_REASON_INVALID_SCHEMA = "perplexity response missing required fields"
_REASON_MALFORMED_ENVELOPE = "malformed response envelope"


def repair_truncated_json(text: str) -> str:
    """Scan-and-close truncation repair -- see the module docstring's
    "Truncation recovery" paragraph for the algorithm and its rationale.

    Pure: takes text, returns text, never raises, never inspects
    anything but its own argument, and does not attempt to PARSE the
    result -- that is the caller's job (:func:`parse_with_recovery`),
    kept separate so this function stays a pure string transform with its
    own unit tests independent of ``json.loads``' error messages.

    Assumes ``text`` is a PREFIX of a value that would have been valid
    JSON if the source had kept writing (true of an LLM response cut off
    by a token ceiling) -- every closing bracket/brace already present in
    ``text`` is assumed to correctly match something on the stack, so
    this never checks bracket TYPE agreement before popping. That
    assumption fails only for genuinely corrupted (not merely truncated)
    input, which this function was never asked to repair.

    Fix round 1 (Minor M1): closing an unterminated string with a bare
    appended ``"`` is only correct if the text isn't ALSO cut off mid
    escape sequence -- two adversarial cases the reviewer found land
    exactly there: truncation landing on a bare trailing backslash (the
    appended quote is then read as THAT backslash's escaped character,
    not a terminator) and truncation mid-``\\uXXXX`` (fewer than 4 hex
    digits present -- the appended quote is read as a stray hex digit,
    or worse, is simply not enough to complete the escape). Both are
    trimmed back to the last KNOWN-good position before the closing quote
    is appended, rather than papered over with one more character.
    """
    stack: list[str] = []
    in_string = False
    escape = False
    # Hex digits still expected to complete an in-progress \uXXXX escape;
    # 0 whenever not currently inside one. Tracked separately from
    # `escape` because a \u escape is 6 characters wide (backslash, "u",
    # 4 hex digits), not the single extra character every other JSON
    # string escape (\", \\, \n, ...) consumes.
    unicode_remaining = 0
    for ch in text:
        if in_string:
            if unicode_remaining > 0:
                unicode_remaining -= 1
                continue
            if escape:
                if ch == "u":
                    unicode_remaining = 4
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()

    repaired = text
    if in_string:
        if unicode_remaining > 0:
            # Trim the whole incomplete escape: "\u" (2 chars) plus
            # whatever hex digits already arrived (4 - unicode_remaining
            # of them) -- there is no way to complete it with information
            # this function does not have, so it is dropped entirely
            # rather than padded with guessed digits.
            repaired = repaired[: -(2 + (4 - unicode_remaining))]
        elif escape:
            # A dangling trailing backslash: drop it so the closing quote
            # below terminates the string instead of being consumed as
            # that backslash's escaped character.
            repaired = repaired[:-1]
        repaired += '"'
    closers = {"{": "}", "[": "]"}
    while stack:
        repaired += closers[stack.pop()]
    return repaired


def parse_with_recovery(content: str) -> Any:
    """``content`` (Perplexity's ``choices[0].message.content`` string) ->
    parsed JSON, trying :func:`repair_truncated_json` exactly once if the
    first attempt fails, per the module docstring's truncation-recovery
    paragraph. Returns ``None`` if both attempts fail -- never raises
    ``json.JSONDecodeError`` out to its caller, since "unparseable" is a
    degrade case, not a bug.
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(repair_truncated_json(content))
    except json.JSONDecodeError:
        return None


def load_schema(schema_name: str) -> dict:
    """Reads ``schemas/{schema_name}.json`` from this package -- e.g.
    ``load_schema("web_research")`` for ``schemas/web_research.json``.
    Schemas are versioned BY FILENAME (a v2 ships as a new file,
    ``web_research_v2.json``), so this never needs to know about any
    particular schema's shape; ``schema_name`` is exactly
    ``ResearchTool.search``'s own argument of the same name.
    """
    path = _SCHEMAS_DIR / f"{schema_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def validate_and_normalize(parsed: Any, schema: dict) -> tuple[tuple[dict, ...], str] | None:
    """Gate + normalize a parsed response against ``schema``, returning
    ``(items, summary)`` -- or ``None`` on an invalid-schema degrade.

    "Validate against the schema's required keys" (the brief) is read
    literally as the schema's TOP-LEVEL ``required`` list (for
    ``web_research.json``, just ``["items"]``): if any required top-level
    key is absent, or ``items`` is not a list, this is an invalid-schema
    degrade -- returns ``None``.

    Per-ITEM fields are NORMALIZED rather than validated the same strict
    way: each element becomes a dict with exactly the keys the schema's
    item-level ``properties`` declares (derived from ``schema`` itself,
    not hardcoded, so this function works for any future schema_name),
    defaulting a missing field to ``""`` rather than rejecting the whole
    response over one incomplete item. Non-dict elements are dropped
    defensively (never raise on a malformed element).

    ``summary`` (Task 4, amendment 9a5ca1b): the schema's OPTIONAL
    top-level ``summary`` key -- not in ``required``, so its absence never
    degrades the response -- extracted the same defaulting-not-validating
    way a missing per-item field already is: ``parsed.get("summary", "")``.
    This corrects this docstring's own earlier claim ("summary is
    descriptive, not load-bearing, since nothing downstream of
    ResearchResult carries it forward today") -- Task 2/3 flagged that gap,
    Task 4 closes it: both transports' ``search()`` now thread this
    function's ``summary`` straight into ``ResearchResult.summary``, which
    a skill's rendered text part carries to the user verbatim.
    """
    if not isinstance(parsed, dict):
        return None
    required = schema.get("required", ())
    if any(key not in parsed for key in required):
        return None
    raw_items = parsed.get("items")
    if not isinstance(raw_items, list):
        return None

    item_properties = (
        schema.get("properties", {}).get("items", {}).get("items", {}).get("properties", {})
    )
    keys = tuple(item_properties)
    items = tuple(
        {key: item.get(key, "") for key in keys} for item in raw_items if isinstance(item, dict)
    )
    summary = parsed.get("summary", "")
    return items, summary


class PerplexityDirectAdapter:
    """Direct REST implementation of :class:`poseidon.mcp.registry
    .ResearchTool`. See the module docstring for the lazy-client and
    never-raises rules.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str = "sonar",
        timeout_s: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._client = client

    def _client_or_build(self) -> httpx.Client:
        """Lazy client construction -- see the module docstring's
        BedrockProvider-mirroring paragraph. Cached after first build, so
        an adapter reused across multiple ``.search()`` calls opens at
        most one ``httpx.Client``."""
        if self._client is None:
            self._client = httpx.Client()
        return self._client

    def search(
        self, *, query: str, schema_name: str, recency_days: int | None = None
    ) -> ResearchResult:
        """One Perplexity chat-completions call: build the request, POST
        it, parse+recover+validate the response. Never raises -- see the
        module docstring's "Never raises" paragraph for exactly which
        four failures degrade instead of propagating.
        """
        schema = load_schema(schema_name)
        payload = _build_payload(
            query=query,
            model=self._model,
            schema_name=schema_name,
            schema=schema,
            recency_days=recency_days,
        )
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        client = self._client_or_build()
        try:
            response = client.post(_API_URL, json=payload, headers=headers, timeout=self._timeout_s)
        except httpx.TimeoutException:
            return _degrade(_REASON_TIMEOUT)

        if response.status_code < 200 or response.status_code >= 300:
            return _degrade(f"perplexity http {response.status_code}")

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            # Fix round 1 (Critical C1): a 2xx response whose body doesn't
            # have the expected shape (empty choices list -> IndexError;
            # a missing choices/message/content key -> KeyError; a
            # non-dict "message" -> TypeError on the next index) must
            # degrade like every other malformed-response case, never
            # crash search() with an uncaught exception from this chain.
            return _degrade(_REASON_MALFORMED_ENVELOPE)
        parsed = parse_with_recovery(content)
        if parsed is None:
            return _degrade(_REASON_PARSE_FAILED)

        normalized = validate_and_normalize(parsed, schema)
        if normalized is None:
            return _degrade(_REASON_INVALID_SCHEMA)
        items, summary = normalized

        return ResearchResult(
            items=items,
            raw_digest=f"{len(items)} results via {_TRANSPORT}",
            transport=_TRANSPORT,
            summary=summary,
        )


def _build_payload(
    *, query: str, model: str, schema_name: str, schema: dict, recency_days: int | None
) -> dict:
    """The outbound request body -- see the module docstring's opening
    paragraph and the Task 2 brief for the pinned shape: a system message
    carrying the marine-lens line, a user message that is exactly the
    query (no template wrapping -- the D30 egress whitelist composer,
    Task 4, is what builds ``query`` FROM parsed slots; this function
    never sees slots, only the finished string), and ``response_format``'s
    ``json_schema.schema`` set to the loaded schema dict verbatim.

    Fix round 1 (Important I1): ``json_schema.name`` is now set to
    ``schema_name`` -- the brief's originally pinned shape omitted it
    (``{"schema": ...}`` alone), but review found Perplexity/OpenAI-
    compatible ``json_schema`` documentation split on whether ``name`` is
    required, with some real-world reports of a 400 when it is absent.
    Adding it is harmless if the field turns out to be optional and fixes
    a permanent silent-degrade if it is not. ``schema_name`` (not a
    hardcoded literal) is used deliberately: this function -- like
    :func:`load_schema` -- stays correct for whatever schema is actually
    being requested rather than assuming ``"web_research"`` specifically,
    matching this module's existing schema-name-agnostic design. See
    ``task-2-report.md``'s "Fix round 1" section for the live-call
    evidence this was checked against.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema},
        },
    }
    recency_filter = RECENCY_FILTERS.get(recency_days) if recency_days is not None else None
    if recency_filter is not None:
        payload["search_recency_filter"] = recency_filter
    return payload


def _degrade(reason: str) -> ResearchResult:
    """Every degrade path funnels through here so the zero-items
    ``raw_digest`` convention (``"0 results via direct"``, matching
    :mod:`poseidon.mcp.registry`'s own docstring example of
    ``"3 results via direct"`` for the success case) is written in
    exactly one place.

    Fix round 1 (Minor M2): ``"0 results via direct"`` is NOT a reliable
    signal of degradation by itself -- a genuinely successful call that
    happened to validate zero items (an empty ``items`` array the model
    legitimately returned) would produce the exact same string via the
    success path in :meth:`PerplexityDirectAdapter.search`. A caller (or
    a proof-line renderer) MUST branch on ``.degraded``, never infer
    failure by parsing or pattern-matching ``raw_digest`` text -- that
    field is provenance for a human reading a transcript, not a machine-
    readable status code.
    """
    return ResearchResult(
        items=(),
        raw_digest=f"0 results via {_TRANSPORT}",
        transport=_TRANSPORT,
        degraded=True,
        degrade_reason=reason,
    )
