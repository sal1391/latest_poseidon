"""Tests for Phase 7 Task 3 (doc 02 section 7, decision D23):
``PerplexityMcpClient``, the MCP-transport implementation of
``poseidon.mcp.registry.ResearchTool``, plus the transport-flip contract
test that is this task's headline requirement -- see
``test_transport_flip_contract_direct_and_mcp_agree_except_transport``
near the bottom of this file.

Every test here exercises ``PerplexityMcpClient`` against a scripted FAKE
wire (``_FakeWire`` below -- a plain callable, no real stdio/websocket
transport exists to test against; see ``mcp_client.py``'s own module
docstring for why that boundary is honest, not a shortcut). The transport-
flip contract test additionally exercises ``PerplexityDirectAdapter``
(Task 2) against a small local fake HTTP client (``_FakeHttpClient``/
``_FakeResponse`` -- the same tiny shape ``test_perplexity_adapter.py``'s
own ``_FakeClient``/``_FakeResponse`` already use, re-declared locally
rather than imported cross-test-file, since that is not an established
pattern in this codebase and the classes are ten lines each).

Fixture reuse: the MCP-side inputs are built by reading Task 2's own
recorded fixture files (``poseidon/mcp/perplexity/fixtures/*.json``) and
wrapping their ``choices[0].message.content`` string in the MCP content-
block envelope shape (see ``_mcp_envelope_from_fixture`` below) -- the
fixture files on disk are the single source of truth for the CONTENT;
nothing here retypes fixture text, only the outer envelope differs between
the two transports' inputs, which is exactly what the contract test is
proving matters not at all to the shape that comes out the other end.

Non-ASCII: none needed (every pinned string here is plain ASCII already);
``test_perplexity_mcp_client_module_files_are_ascii_on_disk`` scans this
file and ``mcp_client.py`` -- the two files this task introduces -- and
does not re-scan ``adapter.py``/``__init__.py``/fixtures/schemas, which
``test_perplexity_package_files_are_ascii_on_disk`` (Task 2) already
covers.
"""

import json
from pathlib import Path

import pytest

from poseidon.mcp.perplexity import adapter, mcp_client
from poseidon.mcp.perplexity.adapter import PerplexityDirectAdapter
from poseidon.mcp.perplexity.mcp_client import PerplexityMcpClient
from poseidon.mcp.registry import ResearchResult

_FIXTURES_DIR = Path(mcp_client.__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _mcp_envelope_from_fixture(name: str) -> dict:
    """The MCP-side input for a given fixture: the SAME content string
    Task 2's fixture carries at ``choices[0].message.content``, wrapped in
    the MCP tool-call response envelope shape instead of the direct
    adapter's HTTP chat-completion shape. Single source of truth for the
    content -- read from the exact fixture file on disk, never retyped;
    only the outer envelope differs, which is precisely the point the
    transport-flip contract test proves does not matter to the result.
    """
    content = _fixture(name)["choices"][0]["message"]["content"]
    return {"content": [{"type": "text", "text": content}]}


# ---------------------------------------------------------------------------
# offline test doubles -- _FakeWire stands in for a real stdio/websocket MCP
# wire via PerplexityMcpClient(wire=...); _FakeHttpClient/_FakeResponse
# stand in for a real httpx.Client via PerplexityDirectAdapter(client=...),
# used only by the transport-flip contract test below.
# ---------------------------------------------------------------------------


class _FakeWire:
    """Records every ``(method, params)`` call; replays one canned
    response or raises one canned exception -- the callable shape ``wire``
    is documented as (see ``mcp_client.py``'s module docstring), mirroring
    ``test_perplexity_adapter.py``'s ``_FakeClient`` exactly, just called
    directly rather than via a ``.post(...)`` method.
    """

    def __init__(self, response=None, raises=None):
        self.calls: list[tuple[str, dict]] = []
        self._response = response
        self._raises = raises

    def __call__(self, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if self._raises is not None:
            raise self._raises
        return self._response


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    """Minimal stand-in for ``httpx.Client``, used only to exercise
    ``PerplexityDirectAdapter`` as the "direct" side of the transport-flip
    contract test -- see the module docstring for why this is re-declared
    locally rather than imported from ``test_perplexity_adapter.py``.
    """

    def __init__(self, response=None, raises=None):
        self.calls: list[dict] = []
        self._response = response
        self._raises = raises

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._response


def _client_for(fixture_name: str) -> tuple[PerplexityMcpClient, _FakeWire]:
    wire = _FakeWire(response=_mcp_envelope_from_fixture(fixture_name))
    return PerplexityMcpClient(wire=wire), wire


# ---------------------------------------------------------------------------
# construction -- must never call the wire as a side effect.
# ---------------------------------------------------------------------------


def test_construction_does_not_call_the_wire():
    calls = []

    def _fail_if_called(method, params):
        calls.append((method, params))
        raise AssertionError("wire must not be called at construction time")

    PerplexityMcpClient(wire=_fail_if_called)  # must not raise

    assert calls == []


# ---------------------------------------------------------------------------
# request shape -- "tools/call" with {"name": "perplexity_search",
# "arguments": {...}}, per the Task 3 brief.
# ---------------------------------------------------------------------------


def test_search_calls_tools_call_with_the_pinned_envelope():
    instance, wire = _client_for("clean")

    instance.search(query="marine biofuel Singapore", schema_name="web_research")

    assert len(wire.calls) == 1
    method, params = wire.calls[0]
    assert method == "tools/call"
    assert params == {
        "name": "perplexity_search",
        "arguments": {"query": "marine biofuel Singapore", "schema_name": "web_research"},
    }


def test_search_includes_recency_days_when_given():
    instance, wire = _client_for("clean")

    instance.search(query="q", schema_name="web_research", recency_days=30)

    assert wire.calls[0][1]["arguments"]["recency_days"] == 30


def test_search_omits_recency_days_when_none():
    instance, wire = _client_for("clean")

    instance.search(query="q", schema_name="web_research", recency_days=None)

    assert "recency_days" not in wire.calls[0][1]["arguments"]


# ---------------------------------------------------------------------------
# fixtures, end to end through the client.
# ---------------------------------------------------------------------------


def test_search_returns_items_from_the_clean_fixture():
    instance, _ = _client_for("clean")

    result = instance.search(query="marine biofuel Singapore", schema_name="web_research")

    assert result == ResearchResult(
        items=(
            {
                "title": "Maersk expands biofuel bunkering in Singapore",
                "url": "https://example.com/maersk-biofuel-singapore",
                "snippet": (
                    "Maersk announced an expansion of its biofuel bunkering "
                    "program at the Port of Singapore."
                ),
                "relevance": "Directly relevant to marine biofuel adoption trends in the region.",
            },
            {
                "title": "IMO 2030 targets reshape bunker fuel demand",
                "url": "https://example.com/imo-2030-bunker-demand",
                "snippet": (
                    "New IMO greenhouse gas targets for 2030 are expected to shift "
                    "demand toward low-carbon marine fuels."
                ),
                "relevance": (
                    "Provides regulatory context for shipping-services fuel transition planning."
                ),
            },
        ),
        raw_digest="2 results via mcp",
        transport="mcp",
        degraded=False,
        degrade_reason=None,
    )


def test_search_recovers_the_truncated_mid_string_fixture():
    # Only one of Task 2's three truncation-landmark variants is
    # re-exercised here (disclosed judgment call): repair_truncated_json's
    # exhaustive correctness across all three landmarks is already proven,
    # transport-independently, in test_perplexity_adapter.py -- reused,
    # never reimplemented, by this client (see mcp_client.py's module
    # docstring). Re-parametrizing all three here would only reconfirm
    # "the client correctly delegates to the shared function," already
    # proven by this one case.
    instance, _ = _client_for("truncated_mid_string")

    result = instance.search(query="marine biofuel Singapore", schema_name="web_research")

    assert result.degraded is False
    assert result.degrade_reason is None
    assert result.transport == "mcp"
    assert len(result.items) >= 1
    assert result.raw_digest == f"{len(result.items)} results via mcp"
    for item in result.items:
        assert set(item) == {"title", "url", "snippet", "relevance"}


def test_search_degrades_on_the_unrecoverable_fixture():
    instance, _ = _client_for("unrecoverable")

    result = instance.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via mcp",
        transport="mcp",
        degraded=True,
        degrade_reason="could not parse perplexity response",
    )


# ---------------------------------------------------------------------------
# degrade rules -- wire raises / malformed envelope / schema-invalid.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("boom"), ConnectionError("pipe closed"), ValueError("bad frame")],
    ids=["RuntimeError", "ConnectionError", "ValueError"],
)
def test_search_degrades_when_the_wire_raises(exc):
    # Deliberately broad: unlike the direct adapter's narrow
    # httpx.TimeoutException catch, "wire" is an arbitrary, pluggable
    # callable (a stdio pipe, a websocket, an in-memory fake) with no
    # fixed exception taxonomy to name specific types against -- see
    # mcp_client.py's module docstring. Any exception it raises is a wire
    # failure, full stop.
    wire = _FakeWire(raises=exc)
    instance = PerplexityMcpClient(wire=wire)

    result = instance.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via mcp",
        transport="mcp",
        degraded=True,
        degrade_reason="mcp wire error",
    )


@pytest.mark.parametrize(
    "envelope",
    [
        pytest.param("not-a-dict", id="envelope-not-a-dict"),
        pytest.param({}, id="missing-content-key"),
        pytest.param({"content": "not-a-list"}, id="content-not-a-list"),
        pytest.param({"content": []}, id="empty-content-list"),
        pytest.param({"content": [{"type": "image"}]}, id="no-text-typed-block"),
        pytest.param({"content": [{"type": "text"}]}, id="text-block-missing-text-key"),
        pytest.param({"content": [{"type": "text", "text": 123}]}, id="text-value-not-a-string"),
        pytest.param({"content": ["not-a-dict-block"]}, id="content-item-not-a-dict"),
    ],
)
def test_search_degrades_on_a_malformed_envelope(envelope):
    wire = _FakeWire(response=envelope)
    instance = PerplexityMcpClient(wire=wire)

    result = instance.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via mcp",
        transport="mcp",
        degraded=True,
        degrade_reason="malformed mcp envelope",
    )


def test_search_degrades_when_response_is_missing_the_items_key():
    # Not one of the brief's named fixtures -- a hand-built envelope
    # exercising validate_and_normalize's top-level gate end to end
    # through search(), mirroring test_perplexity_adapter.py's own
    # equivalent addition beyond the literal fixture list.
    envelope = {"content": [{"type": "text", "text": json.dumps({"summary": "no items field"})}]}
    wire = _FakeWire(response=envelope)
    instance = PerplexityMcpClient(wire=wire)

    result = instance.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via mcp",
        transport="mcp",
        degraded=True,
        degrade_reason="perplexity response missing required fields",
    )


def test_search_never_raises_for_any_of_the_four_pinned_failure_modes():
    # A single cross-cutting proof, in addition to the per-case tests
    # above: none of the four pinned degrade paths ever lets an exception
    # escape search() itself. Mirrors test_perplexity_adapter.py's own
    # equivalent proof for the direct transport's three modes.
    cases = [
        PerplexityMcpClient(wire=_FakeWire(raises=RuntimeError("boom"))),
        PerplexityMcpClient(wire=_FakeWire(response={"content": []})),
        PerplexityMcpClient(wire=_FakeWire(response=_mcp_envelope_from_fixture("unrecoverable"))),
        PerplexityMcpClient(
            wire=_FakeWire(
                response={
                    "content": [{"type": "text", "text": json.dumps({"summary": "no items"})}]
                }
            )
        ),
    ]
    for instance in cases:
        result = instance.search(query="q", schema_name="web_research")
        assert result.degraded is True


def test_shared_degrade_reasons_match_the_adapters_own_private_constants():
    """The "shared degrade path" cases (Task 3 brief) must report the
    EXACT same reason text as the direct adapter's own (private, not
    imported) constants of the same meaning -- see mcp_client.py's module
    docstring. Reaching into adapter._REASON_* here, from a test, is a
    verification step, not a production reuse path -- production code
    never imports them (only the four named public helper functions are
    this package's sanctioned cross-module reuse surface).
    """
    assert mcp_client._REASON_PARSE_FAILED == adapter._REASON_PARSE_FAILED
    assert mcp_client._REASON_INVALID_SCHEMA == adapter._REASON_INVALID_SCHEMA


# ---------------------------------------------------------------------------
# schema_dir -- constructor override, proven genuinely wired up.
# ---------------------------------------------------------------------------


def test_schema_dir_override_is_actually_used_not_ignored(tmp_path):
    """schema_dir defaults to delegating to the adapter's own load_schema
    (see mcp_client.py's docstring) -- this proves the override path is
    genuinely wired up rather than silently ignored: a custom schema
    directory with a schema shaped nothing like web_research.json's
    (different item property names entirely) must be what actually
    governs validate_and_normalize's output.
    """
    (tmp_path / "custom.json").write_text(
        json.dumps(
            {
                "required": ["items"],
                "properties": {
                    "items": {"items": {"properties": {"headline": {"type": "string"}}}}
                },
            }
        ),
        encoding="utf-8",
    )
    envelope = {
        "content": [{"type": "text", "text": json.dumps({"items": [{"headline": "Hello"}]})}]
    }
    instance = PerplexityMcpClient(wire=_FakeWire(response=envelope), schema_dir=tmp_path)

    result = instance.search(query="q", schema_name="custom")

    assert result.items == ({"headline": "Hello"},)
    assert result.degraded is False


# ---------------------------------------------------------------------------
# THE TRANSPORT-FLIP CONTRACT TEST -- D23's proof, this task's headline
# requirement.
# ---------------------------------------------------------------------------


def _direct_result(fixture_name: str) -> ResearchResult:
    fake = _FakeHttpClient(response=_FakeResponse(200, _fixture(fixture_name)))
    instance = PerplexityDirectAdapter(api_key="test-key", client=fake)
    return instance.search(query="marine biofuel Singapore", schema_name="web_research")


def _mcp_result(fixture_name: str) -> ResearchResult:
    instance, _ = _client_for(fixture_name)
    return instance.search(query="marine biofuel Singapore", schema_name="web_research")


def _transport_invariant_fields(result: ResearchResult) -> tuple:
    """Every field a caller of ``ToolServerRegistry.research`` must be
    able to treat as transport-agnostic -- excludes ``transport`` itself
    (the field the contract explicitly allows to differ) AND
    ``raw_digest``, which is NOT excluded by accident: it embeds the
    transport's name as TEXT by design (``ResearchResult``'s own
    docstring: "a short count/transport summary"), so it is mechanically,
    not incidentally, transport-specific. Asserted on separately, exactly,
    in the test below instead of folded into this tuple.
    """
    return (result.items, result.degraded, result.degrade_reason)


@pytest.mark.parametrize(
    "fixture_name",
    ["clean", "truncated_mid_string", "unrecoverable"],
    ids=["clean", "truncated-recoverable", "degraded"],
)
def test_transport_flip_contract_direct_and_mcp_agree_except_transport(fixture_name):
    """D23's proof: equivalent recorded inputs -- the SAME fixture content
    string Task 2 shipped, carried through each transport's own envelope
    shape -- must produce ``ResearchResult`` objects equal in every field
    except ``transport`` (and ``raw_digest``, which embeds transport's
    name as text by design; see ``_transport_invariant_fields``). This is
    the proof that ``ToolServerRegistry.research``'s callers (a skill's
    ``ctx.tools.research.search(...)``) genuinely do not need to know or
    care which transport answered -- the whole reason ``ResearchTool`` is
    one Protocol, not two.
    """
    direct = _direct_result(fixture_name)
    mcp = _mcp_result(fixture_name)

    assert direct.transport == "direct"
    assert mcp.transport == "mcp"
    assert _transport_invariant_fields(direct) == _transport_invariant_fields(mcp)
    assert direct.raw_digest == f"{len(direct.items)} results via direct"
    assert mcp.raw_digest == f"{len(mcp.items)} results via mcp"


# ---------------------------------------------------------------------------
# ASCII-only source, matching the Phase 5 / Task 1 / Task 2 convention.
# ---------------------------------------------------------------------------


def test_perplexity_mcp_client_module_files_are_ascii_on_disk():
    """Byte-pinned degrade reasons stay pinned only if no look-alike
    codepoint can slip into this task's own new files -- see
    ``test_mcp_module_files_are_ascii_on_disk`` (Task 1) and
    ``test_perplexity_package_files_are_ascii_on_disk`` (Task 2), which
    already cover every file those tasks introduced. Scoped to exactly the
    two files Task 3 introduces, not a re-scan of files this task did not
    touch.
    """
    paths = (Path(mcp_client.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
