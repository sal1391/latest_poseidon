"""Tests for Phase 7 Task 4 (doc 02 section 4/7): ``research.web_research``
-- ``Args``/``SKILL_META``, the D30 egress-whitelist query composer, the
success/degraded/absent-tools rendering, and dispatch through the REAL
:class:`~poseidon.core.skills.registry.SkillRegistry`.

No Postgres, no network: this skill never touches ``ctx.data`` at all (see
``skill.py``'s own module docstring), so every test here is plain-offline,
unlike ``data_qa.metric_query``'s own ``tests/test_skill.py`` (``pg``-marked
against the seeded database). ``ctx.tools`` is always a small local fake
exposing ``.research`` -- never a real :class:`~poseidon.mcp.registry
.ToolServerRegistry` -- since ``poseidon.mcp.registry.ToolServerRegistry
.__init__``'s own ``overrides`` seam is what production code uses for
exactly this purpose (see that module's docstring); a unit test one layer
above it only needs the SAME duck-typed shape ``SkillContext.tools`` is
documented to accept (``ctx.tools`` is typed plain ``object`` -- see
``core/skills/context.py``'s own docstring), not the concrete registry
class.

Non-ASCII: none needed; ``test_web_research_module_files_are_ascii_on_disk``
scans this file plus every ``.py`` file Task 4 introduces under
``poseidon/tasks/research/``, the same convention every earlier Phase 7 test
file uses for its own new modules.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import ValidationError

from poseidon.core.config import Settings
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.mcp.registry import ResearchResult
from poseidon.tasks.research.skills.web_research import schema, skill
from poseidon.tasks.research.skills.web_research.schema import SKILL_META, Args
from poseidon.tasks.research.skills.web_research.skill import run
from poseidon.tasks.research.skills.web_research.tools import build_query as build_query_module
from poseidon.tasks.research.skills.web_research.tools import format_parts as format_parts_module
from poseidon.tasks.research.skills.web_research.tools.build_query import build_query
from poseidon.tasks.research.skills.web_research.tools.format_parts import (
    degraded_parts,
    format_parts,
)

_EM_DASH = chr(0x2014)

_SKILL_ID = "research.web_research"

_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

_CLEAN_ITEMS = (
    {
        "title": "Maersk expands biofuel bunkering in Singapore",
        "url": "https://example.com/maersk-biofuel-singapore",
        "snippet": "Maersk announced an expansion of its biofuel bunkering program.",
        "relevance": "Directly relevant to marine biofuel adoption trends.",
    },
    {
        "title": "IMO 2030 targets reshape bunker fuel demand",
        "url": "https://example.com/imo-2030-bunker-demand",
        "snippet": "New IMO greenhouse gas targets shift demand toward low-carbon fuels.",
        "relevance": "Regulatory context for shipping-services fuel transition planning.",
    },
)

_CLEAN_SUMMARY = "Biofuel bunkering capacity is growing in Singapore amid IMO 2030 pressure."


def _clean_result(**overrides) -> ResearchResult:
    fields = {
        "items": _CLEAN_ITEMS,
        "raw_digest": "2 results via fixture",
        "transport": "fixture",
        "degraded": False,
        "degrade_reason": None,
        "summary": _CLEAN_SUMMARY,
    }
    fields.update(overrides)
    return ResearchResult(**fields)


# ---------------------------------------------------------------------------
# test doubles -- ctx.tools is duck-typed (SkillContext.tools is plain
# `object`; see context.py's own docstring), so a small local fake exposing
# `.research` is all a caller needs to provide, the same shape a real
# ToolServerRegistry provides via its own `.research` property.
# ---------------------------------------------------------------------------


@dataclass
class _RecordingResearchTool:
    result: ResearchResult
    calls: list = field(default_factory=list)

    def search(self, *, query: str, schema_name: str, recency_days: int | None = None):
        self.calls.append(
            {"query": query, "schema_name": schema_name, "recency_days": recency_days}
        )
        return self.result


@dataclass
class _Tools:
    research: object


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


# ---------------------------------------------------------------------------
# Args -- question required, the rest optional, recency_days defaults to 30.
# ---------------------------------------------------------------------------


def test_args_requires_question():
    with pytest.raises(ValidationError):
        Args()


def test_args_question_alone_is_sufficient():
    args = Args(question="Any news on marine biofuel?")

    assert args.customer is None
    assert args.port is None
    assert args.region is None
    assert args.topic is None
    assert args.recency_days == 30


def test_args_accepts_every_optional_field():
    args = Args(
        question="q",
        customer="Northstar Lines",
        port="Singapore",
        region="APAC",
        topic="bunker fuel prices",
        recency_days=7,
    )

    assert args.customer == "Northstar Lines"
    assert args.port == "Singapore"
    assert args.region == "APAC"
    assert args.topic == "bunker fuel prices"
    assert args.recency_days == 7


def test_args_recency_days_accepts_an_explicit_none():
    args = Args(question="q", recency_days=None)

    assert args.recency_days is None


# ---------------------------------------------------------------------------
# SKILL_META -- the router-facing contract (registry.py's own discovery
# checks: non-empty description under the 300-char cap, examples a list of
# strings).
# ---------------------------------------------------------------------------


def test_skill_meta_description_names_the_marine_lens_and_the_pivot():
    description = SKILL_META["description"]

    assert 0 < len(description) <= 300
    assert "marine" in description.lower()
    assert any(word in description.lower() for word in ("pivot", "beyond", "outside"))


def test_skill_meta_examples_are_a_non_empty_list_of_strings():
    examples = SKILL_META["examples"]

    assert examples
    assert all(isinstance(example, str) and example for example in examples)


# ---------------------------------------------------------------------------
# build_query -- the D30 whitelist composer: f-strings ONLY over Args'
# fields, a deterministic subject clause, a fixed lens suffix.
# ---------------------------------------------------------------------------


def test_build_query_with_only_a_question_still_carries_the_lens_suffix():
    query = build_query(Args(question="Any recent marine biofuel news?"))

    assert query.startswith("Any recent marine biofuel news?")
    assert "marine fuels" in query
    assert "shipping-services" in query


def test_build_query_subject_clause_is_deterministic_and_field_ordered():
    args = Args(
        question="q",
        customer="Northstar Lines",
        port="Singapore",
        region="APAC",
        topic="bunker fuel prices",
    )

    query = build_query(args)

    # Customer, then port, then region, then topic -- fixed order, every
    # run over the identical Args produces the byte-identical string.
    customer_pos = query.index("Northstar Lines")
    port_pos = query.index("Singapore")
    region_pos = query.index("APAC")
    topic_pos = query.index("bunker fuel prices")
    assert customer_pos < port_pos < region_pos < topic_pos
    assert query == build_query(args)  # deterministic: same Args, same string


def test_build_query_omits_a_subject_clause_for_unset_fields():
    query = build_query(Args(question="q", customer="Northstar Lines"))

    assert "Northstar Lines" in query
    assert " at " not in query  # no port clause
    assert " in " not in query  # no region clause


def test_build_query_never_embeds_recency_days_in_the_text():
    """recency_days is threaded to ResearchTool.search's OWN keyword
    argument (see skill.py), never into the query string itself -- Perplexity's
    search_recency_filter is a structured request field, not prose."""
    query = build_query(Args(question="q", recency_days=7))

    assert "7" not in query


# ---------------------------------------------------------------------------
# THE EGRESS CONTRACT TEST -- D30's proof, this task's headline requirement.
# ---------------------------------------------------------------------------


def test_egress_contract_never_leaks_sentinel_carried_context_into_the_outbound_query():
    """Sentinel values planted in carried conversation state -- a metric
    NUMBER a prior data_qa.metric_query dispatch might have produced, and a
    fake certified TABLE ROW string -- must never reach the outbound query
    a recording ResearchTool actually receives. skill.run's only source of
    query text is build_query(args); ctx.state is never read for it, no
    matter what a careless future edit might be tempted to fold in "for
    extra context."
    """
    metric_sentinel = "3734195"
    row_sentinel = "Northstar Lines,412000.0,GP,SANDBOX.MCA.MARINE_SALES_PLANNING_V"
    poisoned_slots = ConversationSlots(
        customer="SENTINEL-CARRIED-CUSTOMER-" + metric_sentinel,
        topic="SENTINEL-TOPIC-" + row_sentinel,
        pass_through=(("GP", metric_sentinel), ("row", row_sentinel)),
    )
    recording = _RecordingResearchTool(result=_clean_result())
    ctx = _ctx(tools=_Tools(research=recording), state=poisoned_slots)
    args = Args(question="Any relevant news on Northstar Lines?", customer="Northstar Lines")

    result = run(ctx, args)

    assert result.ok is True
    assert len(recording.calls) == 1
    outbound_query = recording.calls[0]["query"]
    assert metric_sentinel not in outbound_query
    assert row_sentinel not in outbound_query
    assert "SENTINEL" not in outbound_query
    # the whitelisted Args field IS present -- this is a leak test, not a
    # test that the query is empty.
    assert "Northstar Lines" in outbound_query


# ---------------------------------------------------------------------------
# format_parts -- success shape (text summary, sources table, proof lines).
# ---------------------------------------------------------------------------


def test_format_parts_success_shape():
    parts, proof = format_parts("the outbound query", _clean_result())

    assert parts == [
        {"kind": "text", "payload": {"markdown": _CLEAN_SUMMARY}},
        {
            "kind": "table",
            "payload": {
                "columns": ["Title", "Source", "Relevance"],
                "rows": [
                    [
                        "Maersk expands biofuel bunkering in Singapore",
                        "https://example.com/maersk-biofuel-singapore",
                        "Directly relevant to marine biofuel adoption trends.",
                    ],
                    [
                        "IMO 2030 targets reshape bunker fuel demand",
                        "https://example.com/imo-2030-bunker-demand",
                        "Regulatory context for shipping-services fuel transition planning.",
                    ],
                ],
            },
        },
    ]
    assert proof == [
        "Query: the outbound query",
        "Transport: fixture",
        "Results: 2",
    ]


def test_format_parts_success_with_zero_items_still_renders_the_summary_and_an_empty_table():
    result = _clean_result(items=(), raw_digest="0 results via fixture")

    parts, proof = format_parts("q", result)

    assert parts[0] == {"kind": "text", "payload": {"markdown": _CLEAN_SUMMARY}}
    assert parts[1]["payload"]["rows"] == []
    assert proof == ["Query: q", "Transport: fixture", "Results: 0"]


# ---------------------------------------------------------------------------
# format_parts / skill.run -- degraded and absent-tools paths: honest parts,
# byte-pinned.
# ---------------------------------------------------------------------------


def test_degraded_parts_are_byte_pinned():
    parts, proof = degraded_parts("perplexity request timed out")

    assert parts == [
        {
            "kind": "text",
            "payload": {
                "markdown": (
                    "External research is unavailable right now "
                    + _EM_DASH
                    + " perplexity request timed out."
                )
            },
        }
    ]
    assert proof == ["Research: degraded (perplexity request timed out)"]


def test_format_parts_routes_a_degraded_result_through_degraded_parts():
    result = _clean_result(
        items=(),
        degraded=True,
        degrade_reason="mcp wire error",
        summary="",
        raw_digest="0 results via mcp",
    )

    parts, proof = format_parts("q", result)

    assert parts == degraded_parts("mcp wire error")[0]
    assert proof == degraded_parts("mcp wire error")[1]


def test_skill_run_success_dispatches_the_search_and_renders_the_result():
    recording = _RecordingResearchTool(result=_clean_result())
    ctx = _ctx(tools=_Tools(research=recording))
    args = Args(question="Any recent marine biofuel news?", customer="Northstar Lines")

    result = run(ctx, args)

    assert result.ok is True
    assert result.parts[0]["kind"] == "text"
    assert result.parts[0]["payload"]["markdown"] == _CLEAN_SUMMARY
    assert result.parts[1]["kind"] == "table"
    assert result.proof[1] == "Transport: fixture"
    assert result.proof[2] == "Results: 2"
    assert len(recording.calls) == 1
    assert recording.calls[0]["schema_name"] == "web_research"
    assert recording.calls[0]["recency_days"] == 30  # Args' own default


def test_skill_run_threads_a_non_default_recency_days_to_the_search_call():
    recording = _RecordingResearchTool(result=_clean_result())
    ctx = _ctx(tools=_Tools(research=recording))
    args = Args(question="q", recency_days=7)

    run(ctx, args)

    assert recording.calls[0]["recency_days"] == 7


def test_skill_run_degraded_result_renders_the_honest_unavailable_message():
    degraded_result = _clean_result(
        items=(),
        degraded=True,
        degrade_reason="perplexity http 500",
        summary="",
        raw_digest="0 results via fixture",
    )
    recording = _RecordingResearchTool(result=degraded_result)
    ctx = _ctx(tools=_Tools(research=recording))
    args = Args(question="q")

    result = run(ctx, args)

    assert result.ok is True
    assert result.parts == [
        {
            "kind": "text",
            "payload": {
                "markdown": (
                    "External research is unavailable right now "
                    + _EM_DASH
                    + " perplexity http 500."
                )
            },
        }
    ]
    assert result.proof == ["Research: degraded (perplexity http 500)"]


def test_skill_run_with_absent_tools_renders_the_honest_unavailable_message():
    """ctx.tools is None -- SkillContext's own default (context.py's own
    docstring: real until Task 4 wires a registry through api/app.py) --
    must render the SAME honest shape a degraded result does, never crash a
    turn just because nothing wired research tools up for this call."""
    ctx = _ctx(tools=None)
    args = Args(question="q")

    result = run(ctx, args)

    assert result.ok is True
    assert len(result.parts) == 1
    assert result.parts[0]["kind"] == "text"
    assert result.parts[0]["payload"]["markdown"].startswith(
        "External research is unavailable right now " + _EM_DASH + " "
    )
    assert len(result.proof) == 1
    assert result.proof[0].startswith("Research: degraded (")


def test_skill_run_never_calls_search_when_tools_are_absent():
    ctx = _ctx(tools=None)

    run(ctx, Args(question="q"))
    # No recording tool was even constructed for this test -- the assertion
    # IS that run() returned successfully above without ever needing one.


# ---------------------------------------------------------------------------
# Dispatch through the REAL SkillRegistry -- the folder-law wiring proof
# (mirrors data_qa.metric_query's own test_skill.py discipline, minus the
# pg marker: this skill never touches ctx.data at all).
# ---------------------------------------------------------------------------


def test_registered_through_the_real_skill_registry():
    registry = SkillRegistry.discover()

    assert _SKILL_ID in registry.skill_ids


def test_dispatch_through_the_real_registry_validates_args_and_renders_parts():
    registry = SkillRegistry.discover()
    recording = _RecordingResearchTool(result=_clean_result())
    ctx = _ctx(tools=_Tools(research=recording))

    result = registry.dispatch(
        _SKILL_ID,
        {"question": "Any relevant news on Northstar Lines?", "customer": "Northstar Lines"},
        ctx,
    )

    assert result.ok is True, result.error
    assert result.parts[1]["kind"] == "table"
    assert recording.calls[0]["query"].startswith("Any relevant news on Northstar Lines?")


def test_dispatch_through_the_real_registry_rejects_missing_question():
    registry = SkillRegistry.discover()
    ctx = _ctx(tools=_Tools(research=_RecordingResearchTool(result=_clean_result())))

    result = registry.dispatch(_SKILL_ID, {}, ctx)

    assert result.ok is False
    assert result.error["status"] == 422


# ---------------------------------------------------------------------------
# ASCII-only source, matching every earlier Phase 7 convention.
# ---------------------------------------------------------------------------


def test_web_research_module_files_are_ascii_on_disk():
    package_dir = Path(schema.__file__).parent
    paths = [
        package_dir / "__init__.py",
        package_dir.parent / "__init__.py",  # skills/__init__.py
        package_dir.parent.parent / "__init__.py",  # research/__init__.py
        Path(schema.__file__),
        Path(skill.__file__),
        Path(build_query_module.__file__),
        Path(format_parts_module.__file__),
        Path(__file__),
    ]
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


def test_web_research_task_yml_is_enabled():
    import yaml

    task_yml_path = Path(schema.__file__).parent.parent.parent / "task.yml"
    manifest = yaml.safe_load(task_yml_path.read_text(encoding="utf-8"))

    assert manifest["id"] == "research"
    assert manifest["enabled"] is True
