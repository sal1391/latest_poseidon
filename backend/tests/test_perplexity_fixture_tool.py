"""Tests for Phase 7 Task 4 (doc 02 section 7): ``FixtureResearchTool``, the
:class:`~poseidon.mcp.registry.ResearchTool` that answers from a recorded
fixture instead of a real transport -- what ``api/app.py`` installs as the
:class:`~poseidon.mcp.registry.ToolServerRegistry` ``overrides={"research":
...}`` seam's local-dev/demo tool whenever ``settings.llm_mode == "stub"``
(see that module's own docstring, "how local dev runs without a Perplexity
key"), and what this same suite's own ``test_llm_loop.py`` injects to run
``pivot_to_research_with_carry`` end to end through the REAL skill registry.

Reuses Task 2's own recorded ``fixtures/clean.json`` (no new fixture file):
this class differs from ``PerplexityDirectAdapter`` only in HOW it gets an
envelope (read a file, not an HTTP call) -- once it has ``choices[0].
message.content``, it feeds the exact same shared ``load_schema``/``parse_
with_recovery``/``validate_and_normalize`` pipeline every other transport
in this package reuses (see ``mcp_client.py``'s own "REUSE, NOT
DUPLICATION" module docstring section for why that matters).

Non-ASCII: none needed; ``test_perplexity_fixture_tool_module_files_are_
ascii_on_disk`` scans this file and ``fixture_tool.py``, mirroring every
earlier Phase-7 test file's own convention for the module(s) it introduces.
"""

import dataclasses
import json
from pathlib import Path

import pytest

from poseidon.mcp.perplexity import fixture_tool
from poseidon.mcp.perplexity.adapter import PerplexityDirectAdapter
from poseidon.mcp.perplexity.fixture_tool import FixtureResearchTool
from poseidon.mcp.registry import ResearchResult

_FIXTURES_DIR = Path(fixture_tool.__file__).resolve().parent / "fixtures"

_CLEAN_SUMMARY = (
    "Recent coverage highlights growing biofuel bunkering capacity in "
    "Singapore and regulatory pressure from IMO 2030 targets pushing "
    "carriers toward lower-carbon marine fuels."
)

_CLEAN_ITEMS = (
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
        "relevance": "Provides regulatory context for shipping-services fuel transition planning.",
    },
)


# ---------------------------------------------------------------------------
# construction -- must never read the fixture file as a side effect.
# ---------------------------------------------------------------------------


def test_construction_does_not_read_any_file(monkeypatch):
    def _fail_if_called(*args, **kwargs):
        raise AssertionError("FixtureResearchTool() must not read a file at construction time")

    monkeypatch.setattr(Path, "read_text", _fail_if_called)

    FixtureResearchTool()  # must not raise


# ---------------------------------------------------------------------------
# happy path -- the real, shipped "clean" fixture, default construction.
# ---------------------------------------------------------------------------


def test_search_returns_the_clean_fixtures_items_and_summary_by_default():
    tool = FixtureResearchTool()

    result = tool.search(query="marine biofuel Singapore", schema_name="web_research")

    assert result == ResearchResult(
        items=_CLEAN_ITEMS,
        raw_digest="2 results via fixture",
        transport="fixture",
        degraded=False,
        degrade_reason=None,
        summary=_CLEAN_SUMMARY,
    )


def test_transport_is_always_fixture():
    tool = FixtureResearchTool()

    result = tool.search(query="q", schema_name="web_research")

    assert result.transport == "fixture"
    assert result.raw_digest.endswith("via fixture")


@pytest.mark.parametrize(
    "query",
    ["marine biofuel Singapore", "any relevant news on Northstar Lines?", ""],
)
def test_search_answers_identically_regardless_of_query(query):
    """A canned fixture is not a real search -- ``query``/``recency_days``
    are accepted (the ``ResearchTool`` protocol's fixed call shape) but
    never consulted, so this always answers the SAME recorded content no
    matter what a caller asks."""
    tool = FixtureResearchTool()

    result = tool.search(query=query, schema_name="web_research", recency_days=7)

    assert result.items == _CLEAN_ITEMS
    assert result.summary == _CLEAN_SUMMARY


def test_search_never_reflects_the_query_back_into_the_result():
    """The egress-safety concern one layer up (the skill's own D30
    whitelist composer) is about the OUTBOUND query; this is the answering
    side's own complementary guarantee -- a fixture tool that echoed its
    input back would be a much less honest stand-in for a real transport."""
    tool = FixtureResearchTool()
    sentinel = "SENTINEL-QUERY-98214"

    result = tool.search(query=sentinel, schema_name="web_research")

    assert sentinel not in json.dumps(
        {"items": result.items, "summary": result.summary, "raw_digest": result.raw_digest}
    )


# ---------------------------------------------------------------------------
# fixtures_dir override -- proven genuinely wired up (schema_dir precedent,
# test_perplexity_mcp_client.py's own test_schema_dir_override_is_actually_
# used_not_ignored).
# ---------------------------------------------------------------------------


def test_fixture_name_and_fixtures_dir_overrides_are_actually_used(tmp_path):
    envelope = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"items": [{"title": "T", "url": "u", "snippet": "s", "relevance": "r"}]}
                    )
                }
            }
        ]
    }
    (tmp_path / "custom.json").write_text(json.dumps(envelope), encoding="utf-8")
    tool = FixtureResearchTool(fixture_name="custom", fixtures_dir=tmp_path)

    result = tool.search(query="q", schema_name="web_research")

    assert result.items == ({"title": "T", "url": "u", "snippet": "s", "relevance": "r"},)
    assert result.degraded is False


# ---------------------------------------------------------------------------
# degrade rules -- never raises, mirrors adapter.py/mcp_client.py's own
# "shared degrade path" reasons where the failure mode is the same shape.
# ---------------------------------------------------------------------------


def test_search_degrades_when_the_fixture_file_is_missing(tmp_path):
    tool = FixtureResearchTool(fixture_name="does-not-exist", fixtures_dir=tmp_path)

    result = tool.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via fixture",
        transport="fixture",
        degraded=True,
        degrade_reason="fixture file not found",
    )


@pytest.mark.parametrize(
    "envelope",
    [
        pytest.param({"choices": []}, id="empty-choices-list"),
        pytest.param({}, id="missing-choices-key"),
        pytest.param({"choices": [{"message": {}}]}, id="missing-content-key"),
    ],
)
def test_search_degrades_on_a_malformed_envelope(tmp_path, envelope):
    (tmp_path / "bad.json").write_text(json.dumps(envelope), encoding="utf-8")
    tool = FixtureResearchTool(fixture_name="bad", fixtures_dir=tmp_path)

    result = tool.search(query="q", schema_name="web_research")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via fixture",
        transport="fixture",
        degraded=True,
        degrade_reason="malformed fixture envelope",
    )


def test_search_degrades_when_the_content_is_not_valid_json(tmp_path):
    envelope = {"choices": [{"message": {"content": "{not valid json"}}]}
    (tmp_path / "bad.json").write_text(json.dumps(envelope), encoding="utf-8")
    tool = FixtureResearchTool(fixture_name="bad", fixtures_dir=tmp_path)

    result = tool.search(query="q", schema_name="web_research")

    assert result.degraded is True
    # Byte-identical to adapter.py's/mcp_client.py's own private constant of
    # the same meaning -- the shared degrade path (see mcp_client.py's own
    # module docstring for why this is deliberate, not a coincidence).
    assert result.degrade_reason == "could not parse perplexity response"


def test_search_degrades_when_the_parsed_content_is_missing_required_fields(tmp_path):
    envelope = {"choices": [{"message": {"content": json.dumps({"summary": "no items field"})}}]}
    (tmp_path / "bad.json").write_text(json.dumps(envelope), encoding="utf-8")
    tool = FixtureResearchTool(fixture_name="bad", fixtures_dir=tmp_path)

    result = tool.search(query="q", schema_name="web_research")

    assert result.degraded is True
    assert result.degrade_reason == "perplexity response missing required fields"


def test_search_never_raises_for_any_of_the_pinned_failure_modes(tmp_path):
    (tmp_path / "malformed.json").write_text(json.dumps({}), encoding="utf-8")
    (tmp_path / "unparseable.json").write_text(
        json.dumps({"choices": [{"message": {"content": "{not valid"}}]}), encoding="utf-8"
    )
    cases = [
        FixtureResearchTool(fixture_name="missing", fixtures_dir=tmp_path),
        FixtureResearchTool(fixture_name="malformed", fixtures_dir=tmp_path),
        FixtureResearchTool(fixture_name="unparseable", fixtures_dir=tmp_path),
    ]
    for tool in cases:
        result = tool.search(query="q", schema_name="web_research")
        assert result.degraded is True


# ---------------------------------------------------------------------------
# real fixture file, read directly -- the default fixtures_dir actually
# points at the real, shipped fixtures/ directory, not merely a plausible
# guess.
# ---------------------------------------------------------------------------


def test_default_fixtures_dir_is_the_real_shipped_fixtures_directory():
    assert (_FIXTURES_DIR / "clean.json").is_file()
    assert FixtureResearchTool()._fixtures_dir == _FIXTURES_DIR


# ---------------------------------------------------------------------------
# FIXTURE-VS-DIRECT EQUIVALENCE (final-review wave item 5) -- the entire
# offline demo (dev-router pivot, the E2E's turn 5, every screenshot taken
# without a Perplexity key) runs on FixtureResearchTool standing in for
# PerplexityDirectAdapter, on the strength of both classes reusing the
# identical load_schema/parse_with_recovery/validate_and_normalize pipeline
# (see this module's own "REUSE, NOT DUPLICATION" docstring section). That
# substitution held, empirically, but was never PINNED by a test of its
# own before this wave -- only assumed. Mirrors
# test_perplexity_mcp_client.py's own transport-flip contract test (same
# asdict-based _transport_invariant_fields shape, same reasoning for
# excluding transport/raw_digest by name), applied to this second pairing
# of transports; re-declared locally rather than imported cross-test-file,
# the same convention that file's own module docstring establishes for its
# nearly-identical local fakes.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeHttpClient:
    """Minimal stand-in for ``httpx.Client``, used only to exercise
    ``PerplexityDirectAdapter`` as the "direct" side of this equivalence
    test."""

    def __init__(self, response=None) -> None:
        self._response = response

    def post(self, url, **kwargs):
        return self._response


def _fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _fixture_tool_result(fixture_name: str) -> ResearchResult:
    tool = FixtureResearchTool(fixture_name=fixture_name)
    return tool.search(query="marine biofuel Singapore", schema_name="web_research")


def _direct_adapter_result(fixture_name: str) -> ResearchResult:
    fake = _FakeHttpClient(response=_FakeResponse(200, _fixture(fixture_name)))
    instance = PerplexityDirectAdapter(api_key="test-key", client=fake)
    return instance.search(query="marine biofuel Singapore", schema_name="web_research")


def _transport_invariant_fields(result: ResearchResult) -> dict:
    """Every field a caller of ``ToolServerRegistry.research`` must be able
    to treat as transport-agnostic -- excludes ``transport`` and
    ``raw_digest`` BY NAME via ``dataclasses.asdict``, rather than a
    hand-picked tuple of the other fields, the identical future-proofing
    shape test_perplexity_mcp_client.py's own equivalent helper uses (Task 3
    fix round 1, Important I1): a hand-picked allow-list of "the fields that
    must match" only equals "every field except transport and raw_digest" by
    coincidence, for as long as ResearchResult happens to have exactly
    today's fields -- excluding by name instead means a future field is
    covered automatically, with no edit needed here when one is added.
    ``raw_digest`` is excluded deliberately, not by oversight: it embeds the
    transport's name as TEXT by design, so it is mechanically, not
    incidentally, transport-specific -- asserted on separately, exactly, in
    the test below instead of folded in here.
    """
    data = dataclasses.asdict(result)
    del data["transport"]
    del data["raw_digest"]
    return data


@pytest.mark.parametrize(
    "fixture_name",
    [
        "clean",
        "truncated_mid_string",
        "truncated_mid_array",
        "truncated_mid_object",
        "unrecoverable",
    ],
    ids=[
        "clean",
        "truncated-mid-string",
        "truncated-mid-array",
        "truncated-mid-object",
        "degraded",
    ],
)
def test_fixture_tool_and_direct_adapter_agree_except_transport(fixture_name):
    """PINS what the entire offline demo runs on: ``FixtureResearchTool`` (a
    file read) and ``PerplexityDirectAdapter`` (an HTTP POST, faked here) --
    fed the EXACT SAME recorded fixture content -- must produce
    ``ResearchResult`` objects equal in every field except ``transport``
    (and ``raw_digest``, which embeds the transport's name as text by
    design; see ``_transport_invariant_fields``). The five fixtures
    parametrized here are every PAYLOAD-CARRYING shape the two transports
    share: the clean success case, all three truncation-recovery landmarks,
    and the one unrecoverable-parse degrade. ``http_500.json`` is excluded
    deliberately: it is an HTTP-status-code failure mode
    ``FixtureResearchTool`` has no equivalent concept of at all -- a fixture
    read either finds parseable content or it does not; there is no status
    code to fake a non-2xx response with.
    """
    fixture_result = _fixture_tool_result(fixture_name)
    direct_result = _direct_adapter_result(fixture_name)

    assert fixture_result.transport == "fixture"
    assert direct_result.transport == "direct"
    assert _transport_invariant_fields(fixture_result) == _transport_invariant_fields(direct_result)
    assert fixture_result.raw_digest == f"{len(fixture_result.items)} results via fixture"
    assert direct_result.raw_digest == f"{len(direct_result.items)} results via direct"


# ---------------------------------------------------------------------------
# ASCII-only source, matching the Phase 7 Task 2/3 convention.
# ---------------------------------------------------------------------------


def test_perplexity_fixture_tool_module_files_are_ascii_on_disk():
    paths = (Path(fixture_tool.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
