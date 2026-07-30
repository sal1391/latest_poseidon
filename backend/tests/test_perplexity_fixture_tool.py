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

import json
from pathlib import Path

import pytest

from poseidon.mcp.perplexity import fixture_tool
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
# ASCII-only source, matching the Phase 7 Task 2/3 convention.
# ---------------------------------------------------------------------------


def test_perplexity_fixture_tool_module_files_are_ascii_on_disk():
    paths = (Path(fixture_tool.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
