"""Tests for Phase 8 Task 2 (doc 02 section 4.3): the four new structured-
research schemas and their clean fixtures, ``fixture_tool.py``'s schema-name
routing and soft-degrade rule, and the ``research`` subskill itself
(``poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills
.research.subskill``).

Tested STANDALONE here, per the Task 2 brief's own instruction ("this task
tests it standalone in test_brief_subskills.py") -- skill-tree-level tests
(``existing_customer_brief/tests/test_skill.py``) arrive with Task 4, which
wires this subskill into a real ``SkillContext``/``SkillResult`` dispatch.

No Postgres, no network: this subskill never touches ``ctx.data`` (only
``ctx.tools.research``, exactly like ``research.web_research``'s own
``skill.py``), and every ``ResearchTool`` here is either the real, offline
``FixtureResearchTool`` (a file read) or a small local fake -- never a live
Perplexity call. ``PERPLEXITY_API_KEY`` is withheld from every suite run
regardless (this task's own house rule), though nothing in this file could
reach it either way.

Section map:
  A. The four new schemas' fixtures, round-tripped through the REAL
     ``FixtureResearchTool`` (the shared adapter pipeline, unmodified).
  B. ``fixture_tool.py``'s Task 2 changes in isolation: schema-name routing,
     the "no fixture for schema" soft degrade, the explicit-``fixture_name``
     override staying exactly as it was, and the SCHEMA-vs-FIXTURE contrast
     (a missing fixture degrades; a missing schema file still raises).
  C. ``format_phase_section.py`` unit-tested directly (the markdown shape).
  D. ``SubskillResult`` -- the frozen subskill-layer contract.
  E. ``run()`` against real fixtures -- both modes' call order and part
     shapes.
  F. Per-call degrade honesty -- ``failed`` true only when EVERY call in
     the mode degraded.
  G. ``ctx.tools is None`` -- all-degraded parts, ``failed=True``, no crash.
  H. D30 egress discipline -- fixed lens phrases only, the sentinel test.
  I. ASCII guards, extended to every file this task introduces or touches.

Non-ASCII characters in expected strings are written as literal ``--`` for
an em dash (never a typed Unicode character), matching the convention every
earlier Phase 4-8 suite in this codebase uses; ``_EM_DASH`` below is used
only where the production code under test itself emits a real em-dash
byte (``chr(0x2014)``, the same construction ``poseidon.mcp.registry``'s own
``_EM_DASH`` uses) that a test must match exactly.
"""

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from poseidon.core.config import Settings
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.mcp.perplexity import fixture_tool
from poseidon.mcp.perplexity.fixture_tool import FixtureResearchTool
from poseidon.mcp.registry import ResearchResult

# Import paths below are long because doc 02 section 1's folder law is
# deep by design (task/skill/subskills/subskill/tools); several "from ...
# import (" header lines exceed the 100-column limit on the dotted path
# alone, with nothing left to wrap -- each such line carries an inline
# line-length linter suppression rather than shortening the path (which
# would mean renaming a real package) or flattening the import (which
# would mean fewer, less-specific names in scope).
from poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills.research import (  # noqa: E501
    subskill,
)
from poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills.research.subskill import (  # noqa: E501
    MODE_EXISTING,
    MODE_PROSPECT,
    SubskillResult,
    run,
)
from poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills.research.tools import (  # noqa: E501
    build_query as build_query_module,
)
from poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills.research.tools import (  # noqa: E501
    format_phase_section as format_phase_section_module,
)
from poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills.research.tools.build_query import (  # noqa: E501
    build_query,
)
from poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills.research.tools.format_phase_section import (  # noqa: E501
    format_degraded,
    format_success,
    phase_section_part,
)

_EM_DASH = chr(0x2014)

_SCHEMAS_DIR = Path(fixture_tool.__file__).resolve().parent / "schemas"
_FIXTURES_DIR = Path(fixture_tool.__file__).resolve().parent / "fixtures"

_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

# The four schemas Task 2 authors, and all five schema_names the research
# subskill ever calls with (the fourth plus web_research, Phase 7's
# existing schema, reused verbatim in new-prospect mode) -- named once so
# the parametrized tests below stay one line each.
_NEW_SCHEMA_NAMES = (
    "sustainability",
    "market_position",
    "strategic_profile",
    "operational_profile",
)
_ALL_CALLABLE_SCHEMA_NAMES = (*_NEW_SCHEMA_NAMES, "web_research")


def _settings() -> Settings:
    return Settings(_env_file=None, database_url=_PLACEHOLDER_DSN, s3_bucket="poseidon-artifacts")


def _ctx(*, tools: object | None = None, state: ConversationSlots | None = None) -> SkillContext:
    return SkillContext(
        data=object(),
        artifacts=None,
        settings=_settings(),
        state=ConversationSlots() if state is None else state,
        tools=tools,
    )


@dataclass
class _Tools:
    research: object


@dataclass
class _RecordingResearchTool:
    """Always answers with the SAME ``ResearchResult`` regardless of
    ``schema_name``/``query`` -- mirrors ``research.web_research``'s own
    test file's identically-named fake. Used wherever a test only needs to
    capture the outbound calls (order, determinism, the egress contract),
    not script per-schema outcomes -- see ``_ScriptedResearchTool`` below
    for that."""

    result: ResearchResult
    calls: list = field(default_factory=list)

    def search(self, *, query: str, schema_name: str, recency_days: int | None = None):
        self.calls.append(
            {"query": query, "schema_name": schema_name, "recency_days": recency_days}
        )
        return self.result


@dataclass
class _ScriptedResearchTool:
    """Answers per ``schema_name`` from a caller-supplied mapping -- lets a
    test script exactly which of a mode's several calls succeed and which
    degrade, which ``_RecordingResearchTool``'s one fixed result cannot do.
    A ``schema_name`` missing from ``results`` is a test-authoring bug
    (every call this subskill ever makes is scripted deliberately in the
    tests that use this double), so this raises rather than silently
    degrading -- a wrong test double answer should fail loudly, not
    masquerade as a legitimate transport degrade.
    """

    results: dict
    calls: list = field(default_factory=list)

    def search(self, *, query: str, schema_name: str, recency_days: int | None = None):
        self.calls.append(
            {"query": query, "schema_name": schema_name, "recency_days": recency_days}
        )
        if schema_name not in self.results:
            raise AssertionError(f"_ScriptedResearchTool: no result scripted for {schema_name!r}")
        return self.results[schema_name]


def _ok(**overrides) -> ResearchResult:
    fields = {
        "items": ({"label": "L", "detail": "D", "relevance": "R"},),
        "raw_digest": "1 results via fixture",
        "transport": "fixture",
        "degraded": False,
        "degrade_reason": None,
        "summary": "S",
    }
    fields.update(overrides)
    return ResearchResult(**fields)


def _degraded_result(reason: str, **overrides) -> ResearchResult:
    fields = {
        "items": (),
        "raw_digest": "0 results via fixture",
        "transport": "fixture",
        "degraded": True,
        "degrade_reason": reason,
        "summary": "",
    }
    fields.update(overrides)
    return ResearchResult(**fields)


# ===========================================================================
# Section A -- the four new schemas' fixtures, through the REAL
# FixtureResearchTool (unmodified shared adapter pipeline).
# ===========================================================================


def test_sustainability_fixture_round_trips_through_fixture_research_tool():
    tool = FixtureResearchTool()

    result = tool.search(query="Northstar Lines", schema_name="sustainability")

    assert result.degraded is False
    assert result.transport == "fixture"
    assert result.raw_digest == "3 results via fixture"
    labels = [item["label"] for item in result.items]
    assert labels == [
        "Alternative marine fuel pilot",
        "Corporate net-zero pledge",
        "EU ETS compliance program",
    ]
    assert set(result.items[0]) == {"label", "detail", "relevance"}
    assert "net-zero" in result.summary


def test_market_position_fixture_round_trips_through_fixture_research_tool():
    tool = FixtureResearchTool()

    result = tool.search(query="Northstar Lines", schema_name="market_position")

    assert result.degraded is False
    labels = [item["label"] for item in result.items]
    assert labels == ["Industry classification", "Competitive landscape", "Market share trend"]
    assert "challenger" in result.summary


def test_strategic_profile_fixture_round_trips_through_fixture_research_tool():
    tool = FixtureResearchTool()

    result = tool.search(query="Northstar Lines", schema_name="strategic_profile")

    assert result.degraded is False
    labels = [item["label"] for item in result.items]
    assert labels == ["Business model", "Financial trajectory", "Market presence"]
    assert "charter-focused" in result.summary


def test_operational_profile_fixture_round_trips_and_covers_all_three_categories():
    tool = FixtureResearchTool()

    result = tool.search(query="Meridian Global Shipping", schema_name="operational_profile")

    assert result.degraded is False
    assert len(result.items) == 5
    assert set(result.items[0]) == {"category", "label", "detail", "relevance"}
    categories = [item["category"] for item in result.items]
    assert categories.count("vessel_type") == 2
    assert categories.count("preferred_port") == 2
    assert categories.count("note") == 1
    assert "Singapore" in result.summary


def test_web_research_schema_still_auto_routes_to_the_original_clean_fixture():
    """Regression pin: schema_name="web_research" must keep answering from
    the Phase 7 fixture (fixtures/clean.json) under Task 2's new
    auto-routing default, not a fixtures/web_research_clean.json this task
    never authors -- see the Task 2 brief's own "web_research keeps its
    existing file" instruction."""
    tool = FixtureResearchTool()

    result = tool.search(query="q", schema_name="web_research")

    assert result.degraded is False
    assert len(result.items) == 2
    assert result.items[0]["title"] == "Maersk expands biofuel bunkering in Singapore"


# ===========================================================================
# Section B -- fixture_tool.py's Task 2 changes: schema-name routing, the
# soft "no fixture for schema" degrade, and the explicit-fixture_name
# override + schema-vs-fixture contrast.
# ===========================================================================


@pytest.mark.parametrize(
    "schema_name,expected_stem",
    [
        ("sustainability", "sustainability_clean"),
        ("market_position", "market_position_clean"),
        ("strategic_profile", "strategic_profile_clean"),
        ("operational_profile", "operational_profile_clean"),
        ("web_research", "clean"),
    ],
)
def test_schema_name_routes_to_exactly_one_documented_fixture_stem(
    tmp_path, schema_name, expected_stem
):
    """Isolates the naming rule: an otherwise-EMPTY directory holding only
    a file named ``{expected_stem}.json`` must resolve for ``schema_name``
    under auto-routing -- proves the mapping precisely (not merely that
    SOME file in the real, fully-populated fixtures/ directory happened to
    answer)."""
    envelope = {"choices": [{"message": {"content": json.dumps({"items": [], "summary": "ok"})}}]}
    (tmp_path / f"{expected_stem}.json").write_text(json.dumps(envelope), encoding="utf-8")
    tool = FixtureResearchTool(fixtures_dir=tmp_path)

    result = tool.search(query="q", schema_name=schema_name)

    assert result.degraded is False
    assert result.summary == "ok"


def test_unknown_schema_name_degrades_softly_never_raises():
    tool = FixtureResearchTool()

    result = tool.search(query="q", schema_name="totally_unknown_schema_xyz")

    assert result == ResearchResult(
        items=(),
        raw_digest="0 results via fixture",
        transport="fixture",
        degraded=True,
        degrade_reason="no fixture for schema",
    )


def test_auto_routed_missing_fixture_degrades_in_an_empty_directory(tmp_path):
    tool = FixtureResearchTool(fixtures_dir=tmp_path)

    result = tool.search(query="q", schema_name="no_such_schema_or_fixture")

    assert result.degraded is True
    assert result.degrade_reason == "no fixture for schema"


def test_explicit_fixture_name_bypasses_schema_routing_and_reads_its_own_file():
    """fixture_name="clean" pinned explicitly, schema_name="sustainability"
    -- must read clean.json (web_research's own fixture), NOT
    sustainability_clean.json, proven by relevance text unique to
    clean.json's own first item surviving normalization while label/detail
    (keys clean.json's items do not carry) come through empty."""
    tool = FixtureResearchTool(fixture_name="clean")

    result = tool.search(query="q", schema_name="sustainability")

    assert result.degraded is False
    assert (
        result.items[0]["relevance"]
        == "Directly relevant to marine biofuel adoption trends in the region."
    )
    assert result.items[0]["label"] == ""


def test_explicit_missing_fixture_name_keeps_the_original_reason_text():
    """A caller who names a SPECIFIC missing file gets the pre-Task-2
    reason ("fixture file not found"), never the schema-routing one ("no
    fixture for schema") -- schema_name was never even consulted on this
    path."""
    tool = FixtureResearchTool(fixture_name="does-not-exist-at-all")

    result = tool.search(query="q", schema_name="sustainability")

    assert result.degraded is True
    assert result.degrade_reason == "fixture file not found"


def test_a_fixture_with_no_matching_schema_file_still_raises_uncaught(tmp_path):
    """Contrast case (the reason this task's docstrings draw a line
    between the two lookups): the FIXTURE exists (so the new soft degrade
    never fires) but the SCHEMA file does not -- load_schema's own
    pre-existing, documented behavior (uncaught FileNotFoundError, a
    deployment bug, not a degrade) must still apply, completely unchanged
    by Task 2's routing."""
    envelope = {"choices": [{"message": {"content": json.dumps({"items": [], "summary": "s"})}}]}
    (tmp_path / "ghost_schema_clean.json").write_text(json.dumps(envelope), encoding="utf-8")
    tool = FixtureResearchTool(fixtures_dir=tmp_path)

    with pytest.raises(FileNotFoundError):
        tool.search(query="q", schema_name="ghost_schema")


def test_construction_with_no_fixture_name_still_does_not_read_any_file(monkeypatch):
    """Regression: the Task 2 default change (``fixture_name=None`` instead
    of the literal ``"clean"``) must not make construction itself start
    reading anything -- resolution stays fully deferred to ``search()``."""

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("FixtureResearchTool() must not read a file at construction time")

    monkeypatch.setattr(Path, "read_text", _fail_if_called)

    FixtureResearchTool()  # must not raise


# ===========================================================================
# Section C -- format_phase_section.py, unit-tested directly.
# ===========================================================================


def test_phase_section_part_shape():
    part = phase_section_part("My Title", "some markdown")

    assert part == {
        "kind": "phase_section",
        "payload": {"title": "My Title", "markdown": "some markdown"},
    }


def test_format_degraded_is_byte_pinned():
    part = format_degraded("Sustainability & ESG", "perplexity http 500")

    assert part == {
        "kind": "phase_section",
        "payload": {
            "title": "Sustainability & ESG",
            "markdown": (
                "Research for this section is unavailable right now "
                + _EM_DASH
                + " perplexity http 500."
            ),
        },
    }


def test_format_success_renders_summary_then_item_bullets_for_the_new_schemas():
    result = _ok(
        summary="Sum.",
        items=({"label": "Alpha", "detail": "Detail text", "relevance": "Why it matters"},),
    )

    part = format_success("sustainability", "Sustainability & ESG", result)

    assert part["kind"] == "phase_section"
    assert part["payload"]["title"] == "Sustainability & ESG"
    markdown = part["payload"]["markdown"]
    assert markdown.startswith("Sum.")
    assert "**Alpha**" in markdown
    assert "Detail text" in markdown
    assert "Why it matters" in markdown


def test_format_success_prefixes_operational_profile_items_with_their_category():
    result = _ok(
        summary="",
        items=({"category": "vessel_type", "label": "Supramax", "detail": "D", "relevance": "R"},),
    )

    part = format_success("operational_profile", "Operational Profile", result)

    assert "[vessel_type]" in part["payload"]["markdown"]


def test_format_success_renders_web_researchs_own_item_shape():
    result = _ok(
        summary="",
        items=({"title": "T", "url": "https://x", "snippet": "Sn", "relevance": "Rel"},),
    )

    part = format_success("web_research", "Web Research", result)

    markdown = part["payload"]["markdown"]
    assert "**T**" in markdown
    assert "Sn" in markdown
    assert "Rel" in markdown


def test_format_success_with_no_summary_and_no_items_still_renders_one_honest_line():
    result = _ok(summary="", items=())

    part = format_success("sustainability", "Sustainability & ESG", result)

    assert part["payload"]["markdown"] == "No results returned."


# ===========================================================================
# Section D -- build_query.py, unit-tested directly.
# ===========================================================================


def test_build_query_starts_with_subject_and_carries_the_marine_lens():
    query = build_query("Meridian Global Shipping", "operational_profile")

    assert query.startswith("Meridian Global Shipping")
    assert "marine fuels" in query


def test_build_query_is_deterministic_for_the_same_inputs():
    assert build_query("X", "sustainability") == build_query("X", "sustainability")


@pytest.mark.parametrize("schema_name", _ALL_CALLABLE_SCHEMA_NAMES)
def test_build_query_has_a_lens_phrase_for_every_schema_this_subskill_calls(schema_name):
    query = build_query("X", schema_name)

    assert query != "X"  # a lens phrase was actually appended


def test_build_query_lens_phrases_are_distinct_per_schema():
    queries = {schema_name: build_query("X", schema_name) for schema_name in _NEW_SCHEMA_NAMES}

    assert len(set(queries.values())) == 4


# ===========================================================================
# Section E -- SubskillResult: the frozen subskill-layer contract.
# ===========================================================================


def test_subskill_result_is_frozen():
    result = SubskillResult(parts=(), synthesis_inputs=(), failed=False)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.failed = True


def test_subskill_result_carries_exactly_parts_synthesis_inputs_failed():
    part = {"kind": "phase_section", "payload": {"title": "T", "markdown": "M"}}
    inputs = {"schema_name": "sustainability"}

    result = SubskillResult(parts=(part,), synthesis_inputs=(inputs,), failed=True)

    assert result.parts == (part,)
    assert result.synthesis_inputs == (inputs,)
    assert result.failed is True


# ===========================================================================
# Section F -- run() against real fixtures: both modes' call order and part
# shapes.
# ===========================================================================


def test_run_existing_mode_makes_three_sequential_calls_in_order():
    recording = _RecordingResearchTool(result=_ok())
    ctx = _ctx(tools=_Tools(research=recording))

    run(ctx, MODE_EXISTING, "Northstar Lines")

    assert [c["schema_name"] for c in recording.calls] == [
        "sustainability",
        "market_position",
        "strategic_profile",
    ]


def test_run_prospect_mode_makes_two_calls_operational_profile_then_web_research():
    recording = _RecordingResearchTool(result=_ok())
    ctx = _ctx(tools=_Tools(research=recording))

    run(ctx, MODE_PROSPECT, "Meridian Global Shipping")

    assert [c["schema_name"] for c in recording.calls] == ["operational_profile", "web_research"]


def test_run_existing_mode_against_real_fixtures_returns_three_phase_sections():
    tool = FixtureResearchTool()
    ctx = _ctx(tools=_Tools(research=tool))

    result = run(ctx, MODE_EXISTING, "Northstar Lines")

    assert result.failed is False
    assert len(result.parts) == 3
    assert [part["kind"] for part in result.parts] == ["phase_section"] * 3
    titles = [part["payload"]["title"] for part in result.parts]
    assert titles == ["Sustainability & ESG", "Market Position", "Strategic Profile"]
    assert "Alternative marine fuel pilot" in result.parts[0]["payload"]["markdown"]
    assert "net-zero" in result.parts[0]["payload"]["markdown"]
    assert "Industry classification" in result.parts[1]["payload"]["markdown"]
    assert "Business model" in result.parts[2]["payload"]["markdown"]


def test_run_prospect_mode_against_real_fixtures_returns_two_phase_sections():
    tool = FixtureResearchTool()
    ctx = _ctx(tools=_Tools(research=tool))

    result = run(ctx, MODE_PROSPECT, "Meridian Global Shipping")

    assert result.failed is False
    assert len(result.parts) == 2
    titles = [part["payload"]["title"] for part in result.parts]
    assert titles == ["Operational Profile", "Web Research"]
    assert "Supramax bulk carrier" in result.parts[0]["payload"]["markdown"]
    assert "[vessel_type]" in result.parts[0]["payload"]["markdown"]
    assert "Maersk expands biofuel bunkering in Singapore" in result.parts[1]["payload"]["markdown"]


def test_synthesis_inputs_mirror_parts_count_and_order_existing_mode():
    tool = FixtureResearchTool()
    ctx = _ctx(tools=_Tools(research=tool))

    result = run(ctx, MODE_EXISTING, "Northstar Lines")

    assert len(result.synthesis_inputs) == 3
    assert [r["schema_name"] for r in result.synthesis_inputs] == [
        "sustainability",
        "market_position",
        "strategic_profile",
    ]
    assert [r["title"] for r in result.synthesis_inputs] == [
        part["payload"]["title"] for part in result.parts
    ]
    assert all(r["degraded"] is False for r in result.synthesis_inputs)
    assert all(r["items"] for r in result.synthesis_inputs)
    assert all(r["summary"] for r in result.synthesis_inputs)


def test_unknown_mode_raises_value_error():
    ctx = _ctx(tools=_Tools(research=_RecordingResearchTool(result=_ok())))

    with pytest.raises(ValueError):
        run(ctx, "not_a_real_mode", "subject")


# ===========================================================================
# Section G -- per-call degrade honesty: failed only when EVERY call
# degraded.
# ===========================================================================


def test_run_existing_mode_with_one_degraded_call_keeps_failed_false_and_pins_its_text():
    scripted = _ScriptedResearchTool(
        results={
            "sustainability": _ok(
                summary="sustain-summary",
                items=({"label": "L1", "detail": "D1", "relevance": "R1"},),
            ),
            "market_position": _degraded_result("perplexity http 500"),
            "strategic_profile": _ok(
                summary="strategy-summary",
                items=({"label": "L3", "detail": "D3", "relevance": "R3"},),
            ),
        }
    )
    ctx = _ctx(tools=_Tools(research=scripted))

    result = run(ctx, MODE_EXISTING, "Northstar Lines")

    assert result.failed is False
    assert result.parts[0]["payload"]["markdown"].startswith("sustain-summary")
    assert result.parts[1]["payload"]["markdown"] == (
        "Research for this section is unavailable right now " + _EM_DASH + " perplexity http 500."
    )
    assert result.parts[2]["payload"]["markdown"].startswith("strategy-summary")
    assert result.synthesis_inputs[0]["degraded"] is False
    assert result.synthesis_inputs[1]["degraded"] is True
    assert result.synthesis_inputs[1]["degrade_reason"] == "perplexity http 500"
    assert result.synthesis_inputs[2]["degraded"] is False


def test_run_failed_true_only_when_every_call_in_the_mode_degraded():
    scripted = _ScriptedResearchTool(
        results={
            "operational_profile": _degraded_result("no fixture for schema"),
            "web_research": _degraded_result("perplexity request timed out"),
        }
    )
    ctx = _ctx(tools=_Tools(research=scripted))

    result = run(ctx, MODE_PROSPECT, "Meridian Global Shipping")

    assert result.failed is True
    assert all(r["degraded"] for r in result.synthesis_inputs)
    assert result.parts[0]["payload"]["markdown"] == (
        "Research for this section is unavailable right now " + _EM_DASH + " no fixture for schema."
    )
    assert result.parts[1]["payload"]["markdown"] == (
        "Research for this section is unavailable right now "
        + _EM_DASH
        + " perplexity request timed out."
    )


def test_a_single_success_among_three_keeps_failed_false():
    scripted = _ScriptedResearchTool(
        results={
            "sustainability": _degraded_result("r1"),
            "market_position": _degraded_result("r2"),
            "strategic_profile": _ok(),
        }
    )
    ctx = _ctx(tools=_Tools(research=scripted))

    result = run(ctx, MODE_EXISTING, "subject")

    assert result.failed is False


def test_all_three_degraded_makes_failed_true():
    scripted = _ScriptedResearchTool(
        results={
            "sustainability": _degraded_result("r1"),
            "market_position": _degraded_result("r2"),
            "strategic_profile": _degraded_result("r3"),
        }
    )
    ctx = _ctx(tools=_Tools(research=scripted))

    result = run(ctx, MODE_EXISTING, "subject")

    assert result.failed is True


# ===========================================================================
# Section H -- ctx.tools is None: all-degraded parts, failed=True, no crash.
# ===========================================================================


def test_run_with_ctx_tools_none_existing_mode_returns_three_degraded_parts_failed_true():
    ctx = _ctx(tools=None)

    result = run(ctx, MODE_EXISTING, "Northstar Lines")

    assert result.failed is True
    assert len(result.parts) == 3
    for part in result.parts:
        assert part["kind"] == "phase_section"
        assert part["payload"]["markdown"] == (
            "Research for this section is unavailable right now "
            + _EM_DASH
            + " no research tools configured."
        )
    assert all(r["degraded"] is True for r in result.synthesis_inputs)


def test_run_with_ctx_tools_none_prospect_mode_returns_two_degraded_parts_failed_true():
    ctx = _ctx(tools=None)

    result = run(ctx, MODE_PROSPECT, "Meridian Global Shipping")

    assert result.failed is True
    assert len(result.parts) == 2
    titles = [part["payload"]["title"] for part in result.parts]
    assert titles == ["Operational Profile", "Web Research"]


def test_run_with_ctx_tools_none_never_crashes_and_never_needs_a_research_tool():
    ctx = _ctx(tools=None)

    run(ctx, MODE_EXISTING, "subject")  # must not raise
    run(ctx, MODE_PROSPECT, "subject")  # must not raise


# ===========================================================================
# Section I -- D30 egress discipline: fixed lens phrases only, sentinel
# test.
# ===========================================================================


def test_egress_contract_never_leaks_sentinel_carried_state_into_outbound_queries():
    """Sentinel values planted in carried conversation state -- a metric
    NUMBER a prior data_qa.metric_query dispatch might have produced, and a
    fake certified TABLE ROW string -- must never reach any of the three
    outbound queries a recording ResearchTool actually receives. run()'s
    only source of query text is build_query(subject, schema_name); ctx.state
    is never read for it."""
    metric_sentinel = "3734195"
    row_sentinel = "Northstar Lines,412000.0,GP,SANDBOX.MCA.MARINE_SALES_PLANNING_V"
    poisoned_slots = ConversationSlots(
        customer="SENTINEL-CARRIED-CUSTOMER-" + metric_sentinel,
        topic="SENTINEL-TOPIC-" + row_sentinel,
        pass_through=(("GP", metric_sentinel), ("row", row_sentinel)),
    )
    recording = _RecordingResearchTool(result=_ok())
    ctx = _ctx(tools=_Tools(research=recording), state=poisoned_slots)

    result = run(ctx, MODE_EXISTING, "Northstar Lines")

    assert result.failed is False
    assert len(recording.calls) == 3
    for call in recording.calls:
        assert metric_sentinel not in call["query"]
        assert row_sentinel not in call["query"]
        assert "SENTINEL" not in call["query"]
        # the whitelisted subject IS present -- this is a leak test, not a
        # test that the query is empty.
        assert "Northstar Lines" in call["query"]


def test_queries_are_subject_plus_one_fixed_lens_phrase_and_are_deterministic():
    recording1 = _RecordingResearchTool(result=_ok())
    run(_ctx(tools=_Tools(research=recording1)), MODE_EXISTING, "Northstar Lines")
    first_run_queries = [c["query"] for c in recording1.calls]

    recording2 = _RecordingResearchTool(result=_ok())
    run(_ctx(tools=_Tools(research=recording2)), MODE_EXISTING, "Northstar Lines")
    second_run_queries = [c["query"] for c in recording2.calls]

    assert first_run_queries == second_run_queries
    assert len(set(first_run_queries)) == 3  # three distinct lens phrases
    for query in first_run_queries:
        assert query.startswith("Northstar Lines")


def test_prospect_mode_queries_also_carry_the_subject_and_recur_deterministically():
    recording = _RecordingResearchTool(result=_ok())
    run(_ctx(tools=_Tools(research=recording)), MODE_PROSPECT, "Meridian Global Shipping")

    queries = [c["query"] for c in recording.calls]
    assert len(queries) == 2
    assert queries[0] != queries[1]
    for query in queries:
        assert query.startswith("Meridian Global Shipping")


# ===========================================================================
# Section J -- ASCII guards, extended to every file this task introduces or
# touches.
# ===========================================================================


def test_new_backend_modules_are_ascii_on_disk():
    package_dir = Path(subskill.__file__).parent  # .../subskills/research/
    subskills_dir = package_dir.parent  # .../subskills/
    paths = [
        Path(subskill.__file__),
        package_dir / "__init__.py",
        subskills_dir / "__init__.py",
        package_dir / "tools" / "__init__.py",
        Path(build_query_module.__file__),
        Path(format_phase_section_module.__file__),
        Path(__file__),
    ]
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


def test_new_schema_files_are_ascii_on_disk():
    for name in ["sustainability", "market_position", "strategic_profile", "operational_profile"]:
        path = _SCHEMAS_DIR / f"{name}.json"
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


def test_new_fixture_files_are_ascii_on_disk():
    for name in [
        "sustainability_clean",
        "market_position_clean",
        "strategic_profile_clean",
        "operational_profile_clean",
    ]:
        path = _FIXTURES_DIR / f"{name}.json"
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


def test_modified_fixture_tool_module_is_still_ascii_on_disk():
    """fixture_tool.py was MODIFIED (not just newly authored) this task,
    gaining a large amount of new module-docstring prose -- pinned here
    explicitly too, alongside test_perplexity_fixture_tool.py's own
    pre-existing identical guard over the same file."""
    offending = sorted({byte for byte in Path(fixture_tool.__file__).read_bytes() if byte > 0x7F})
    assert not offending
