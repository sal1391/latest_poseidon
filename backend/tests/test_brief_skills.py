"""Tests for Phase 8 Task 4: the two brief skills assembled from Tasks 1-3's
plumbing/subskills/tools, and the registry discovering all four skills.

``customer_insight.existing_customer_brief``: fetch_metrics + fetch_top_ports
(metric_grid + table parts emitted immediately) -> contextualize || research
(concurrent, ThreadPoolExecutor(2), FIXED consumption order) -> strategize ->
build_brief_pdf (or a pinned skip line when no artifact store is wired).

``customer_insight.new_prospect_brief``: research -> contextualize (consuming
research, sequential -- D10) -> strategize -> build_brief_pdf, no internal
data tools (nothing exists for a prospect).

No Postgres, no network, no live LLM: every ``ResearchTool`` here is either
the real, offline ``FixtureResearchTool`` (a file read) or a small local
fake; every ``Settings`` is ``llm_mode="stub"``, so ``contextualize``/
``strategize`` never touch ``ctx.llm`` at all (proven directly by Task 3's
own ``_ExplodingLLM`` tests -- not re-proven here). ``PERPLEXITY_API_KEY``
is withheld from every suite run regardless (this phase's own house rule),
though nothing in this file could reach it either way.

PDF-path tests are ``@pytest.mark.pdf``, skipped unless WeasyPrint's native
libraries import cleanly (same ``_HAS_WEASYPRINT`` probe as
``existing_customer_brief/tests/test_tools.py``) -- they use a FAKE
``ArtifactStore`` (P3's test-double pattern, adapted: no real MinIO needed,
since only the upload step is doubled, not the markdown -> HTML -> PDF
rendering, which needs the real library to prove anything).

Section map:
  A. Test doubles shared by every section below.
  B. existing_customer_brief: full run, part order pinned end to end
     (including the early-emitted metric_grid/table), proof pinned,
     artifacts-None skip.
  C. new_prospect_brief: full run, part order pinned, proof pinned,
     artifacts-None skip.
  D. Concurrency determinism: slow-research/fast-contextualize and
     inverted -- byte-identical part order both ways.
  E. Degraded research (ctx.tools is None): both briefs continue, ok=True.
  F. Exception-escape guard (P8 whole-branch final-review wave, 2026-07-30,
     item 2 / I-4): a raising fake subskill in EACH of the three phases,
     in BOTH flows -- both briefs continue, ok=True, the raising phase's
     own pinned failure text stands in for it.
  G. PDF path with a fake artifact store.
  H. Dispatch through the real SkillRegistry, with validated Args.
  I. ASCII guard.
"""

import datetime as dt
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult, BreakdownRow, MetricResult
from poseidon.core.skills.context import ArtifactRef, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.core.skills.result import phase_section_part
from poseidon.mcp.perplexity.fixture_tool import FixtureResearchTool
from poseidon.tasks.customer_insight.skills.existing_customer_brief import schema as existing_schema
from poseidon.tasks.customer_insight.skills.existing_customer_brief import skill as existing_skill
from poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills.research.subskill import (  # noqa: E501
    SubskillResult,
)
from poseidon.tasks.customer_insight.skills.new_prospect_brief import schema as prospect_schema
from poseidon.tasks.customer_insight.skills.new_prospect_brief import skill as prospect_skill

# WeasyPrint links Pango/Cairo/GDK-Pixbuf at import time and raises OSError
# (not ImportError) when they are missing -- same probe as
# existing_customer_brief/tests/test_tools.py's own _HAS_WEASYPRINT.
try:
    import weasyprint  # noqa: F401 - existence probe only, never used directly
except Exception:  # noqa: BLE001 - missing native libs raise OSError, not ImportError
    _HAS_WEASYPRINT = False
else:
    _HAS_WEASYPRINT = True

_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

_ANCHOR = dt.date(2026, 7, 1)

_PRIOR_VALUES = {
    "VOLUME": 1000.0,
    "GP": 50000.0,
    "MARGIN": 50.0,
    "NUM_WON": 10.0,
    "NUM_INQUIRIES": 15.0,
    "NUM_LOST": 5.0,
}
_YTD_VALUES = {
    "VOLUME": 600.0,
    "GP": 30000.0,
    "MARGIN": 50.0,
    "NUM_WON": 6.0,
    "NUM_INQUIRIES": 9.0,
    "NUM_LOST": 3.0,
}
_PORTS = [("Singapore", 20000.0), ("Rotterdam", 10000.0)]

# ===========================================================================
# Section A -- test doubles shared by every section below.
# ===========================================================================


@dataclass
class _FakeDataClient:
    """A ``DataClient`` (structurally) that answers ``fetch_metrics``'s two
    calls with fixed prior-year/YTD values (by CALL COUNT -- fetch_metrics
    always runs prior then YTD, in that order, see its own module
    docstring) and ``fetch_top_ports``'s one call with a fixed two-port
    breakdown -- never touches a database. Mirrors
    ``existing_customer_brief/tests/test_tools.py``'s own
    ``_RecordingDataClient``, extended to return USEFUL values (that file's
    twin returns all-None, which is fine for asserting specs but gives
    nothing concrete for a full-run part-order/content test to check)."""

    metric_specs: list = field(default_factory=list)
    breakdown_specs: list = field(default_factory=list)

    def list_dimension_values(self, *args: object, **kwargs: object) -> list[str]:
        raise AssertionError("a brief skill must never list dimension values")

    def available_periods(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("a brief skill must never query available periods")

    def run_metric_query(self, spec):
        self.metric_specs.append(spec)
        values = _PRIOR_VALUES if len(self.metric_specs) == 1 else _YTD_VALUES
        return MetricResult(entity=spec.entity, period=spec.period, values=dict(values))

    def run_breakdown_query(self, spec):
        self.breakdown_specs.append(spec)
        rows = [BreakdownRow(key=port, values={"GP": gp}) for port, gp in _PORTS]
        return BreakdownResult(entity=spec.entity, group_by=spec.group_by, rows=rows)


@dataclass
class _Tools:
    research: object


def _raising_subskill(*_args: object, **_kwargs: object):
    """A subskill ``.run`` double that always raises -- Section F's own
    exception-escape-guard pin (final-review wave item 2 / I-4): skill.py's
    own try/except must catch this regardless of which subskill or which
    flow it stands in for, never let it propagate and 500 the whole
    brief. ``*_args, **_kwargs`` accepts any subskill's own signature
    unchanged (contextualize/strategize/research each take a different
    number of positional arguments)."""
    raise RuntimeError("boom -- simulated subskill failure")


@dataclass
class _RecordingEmit:
    """A ``ctx.emit_part`` double: records every part it is called with, in
    call order -- lets a test prove the STREAMED order matches the final
    ``SkillResult.parts`` order exactly (doc 02 section 5: a skill's result
    always carries every part it produced, streamed early or not; see
    ``test_emit_seam_loop_events.py``'s own ``_skill_returning`` fake for
    the identical contract one layer down)."""

    parts: list = field(default_factory=list)

    def __call__(self, part: dict) -> None:
        self.parts.append(part)


@dataclass
class _FakeArtifactStore:
    """An ``ArtifactStore`` double (structurally -- ``build_brief_pdf``
    only ever calls ``.put_pdf``): records every upload and hands back a
    fake presigned reference, no real S3/MinIO client involved. P3's own
    ``test_tools.py`` exercises ``build_brief_pdf`` against a REAL MinIO;
    this task's own brief asks for a double instead so the PDF-path tests
    below need no running container -- only WeasyPrint's native libraries,
    which the ``@pytest.mark.pdf`` marker gates."""

    puts: list = field(default_factory=list)

    def put_pdf(self, key: str, content: bytes) -> ArtifactRef:
        self.puts.append((key, content))
        return ArtifactRef(name=key, url=f"https://fake.example.test/{key}", mime="application/pdf")


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_url=_PLACEHOLDER_DSN,
        s3_bucket="poseidon-artifacts",
        llm_mode="stub",
    )


def _ctx(
    *,
    data=None,
    tools: object | None = None,
    artifacts: object | None = None,
    emit_part: object | None = None,
) -> SkillContext:
    return SkillContext(
        data=data if data is not None else _FakeDataClient(),
        artifacts=artifacts,
        settings=_settings(),
        tools=tools,
        emit_part=emit_part,
    )


# ===========================================================================
# Section B -- existing_customer_brief: full run, part order pinned end to
# end, proof pinned, artifacts-None skip.
# ===========================================================================


def test_existing_customer_brief_full_run_part_order_and_proof_pinned(monkeypatch):
    monkeypatch.setattr(existing_skill, "_today", lambda: _ANCHOR)
    data = _FakeDataClient()
    recorder = _RecordingEmit()
    ctx = _ctx(data=data, tools=_Tools(research=FixtureResearchTool()), emit_part=recorder)
    args = existing_schema.Args(customer="Northstar Lines")

    result = existing_skill.run(ctx, args)

    assert result.ok is True
    assert result.error is None

    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["metric_grid", "table"] + ["phase_section"] * 5

    titles = [part["payload"]["title"] for part in result.parts[2:]]
    assert titles == [
        "Context",
        "Sustainability & ESG",
        "Market Position",
        "Strategic Profile",
        "Strategy",
    ]

    # every part was ALSO streamed early, in the identical order -- proves
    # the emit_part call order and the final parts list never diverge.
    assert recorder.parts == result.parts

    assert result.proof == [
        "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
        "Backend: synthetic",
        "Customer: Northstar Lines",
        "Prior year: 2025-01-01..2026-01-01",
        "YTD: 2026-01-01..2026-07-01",
        "Metrics: 6 requested",
        "Customer: Northstar Lines",
        "Window: 2025-07-01..2026-07-01",
        "Top ports: 2 of requested 5",
        "Phases completed: contextualize, research, strategize",
        "Phases failed: none",
        "Transport: FixtureResearchTool",
        "Artifact: skipped (no artifact store configured)",
    ]

    # artifacts-None skip: no artifact store was wired, so no PDF is built
    # and no artifact rides the result -- never a crash.
    assert result.artifacts == []


def test_existing_customer_brief_metric_grid_and_table_content():
    ctx = _ctx(data=_FakeDataClient(), tools=_Tools(research=FixtureResearchTool()))
    args = existing_schema.Args(customer="Northstar Lines")

    result = existing_skill.run(ctx, args)

    grid = result.parts[0]
    assert grid["kind"] == "metric_grid"
    volume = next(m for m in grid["payload"]["metrics"] if m["name"] == "VOLUME")
    assert volume == {"name": "VOLUME", "friendly": "Volume", "a": 1000, "b": 600, "unit": "tons"}

    table = result.parts[1]
    assert table == {
        "kind": "table",
        "payload": {
            "columns": ["Port", "Gross Profit"],
            "rows": [["Singapore", 20000], ["Rotterdam", 10000]],
        },
    }


def test_existing_customer_brief_rejects_a_january_1_anchor_as_a_structured_422(monkeypatch):
    """``fetch_metrics`` raises ``ValueError`` for a January 1 anchor (its
    own documented guard); the skill catches it and reports a structured
    422 rather than letting the registry's generic 500 wrapper swallow a
    perfectly well-typed, well-understood failure mode."""
    monkeypatch.setattr(existing_skill, "_today", lambda: dt.date(2026, 1, 1))
    ctx = _ctx(data=_FakeDataClient(), tools=_Tools(research=FixtureResearchTool()))
    args = existing_schema.Args(customer="Northstar Lines")

    result = existing_skill.run(ctx, args)

    assert result.ok is False
    assert result.error["status"] == 422
    assert "year-to-date" in result.error["detail"]


# ===========================================================================
# Section C -- new_prospect_brief: full run, part order pinned, proof
# pinned, artifacts-None skip.
# ===========================================================================


def test_new_prospect_brief_full_run_part_order_and_proof_pinned():
    recorder = _RecordingEmit()
    ctx = _ctx(tools=_Tools(research=FixtureResearchTool()), emit_part=recorder)
    args = prospect_schema.Args(prospect_name="Meridian Global Shipping")

    result = prospect_skill.run(ctx, args)

    assert result.ok is True
    assert result.error is None

    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["phase_section"] * 4

    titles = [part["payload"]["title"] for part in result.parts]
    assert titles == ["Operational Profile", "Web Research", "Context", "Strategy"]

    assert recorder.parts == result.parts

    assert result.proof == [
        "Subject: Meridian Global Shipping",
        "Phases completed: research, contextualize, strategize",
        "Phases failed: none",
        "Transport: FixtureResearchTool",
        "Artifact: skipped (no artifact store configured)",
    ]

    assert result.artifacts == []


def test_new_prospect_brief_strategize_renders_the_no_current_services_rule():
    """D10's own pinned rule: a prospect's CRM "Current Services" field is
    the fixed "Prospect -- no current services" text, never
    "[requires live synthesis]" -- a prospect having no services is a known
    fact, not a synthesis gap (see strategize.subskill's own "CURRENT
    SERVICES" docstring section)."""
    ctx = _ctx(tools=_Tools(research=FixtureResearchTool()))
    args = prospect_schema.Args(prospect_name="Meridian Global Shipping")

    result = prospect_skill.run(ctx, args)

    strategy_markdown = result.parts[-1]["payload"]["markdown"]
    assert "Prospect" in strategy_markdown and "no current services" in strategy_markdown


# ===========================================================================
# Section D -- concurrency determinism: slow-research/fast-contextualize
# and inverted, byte-identical part order both ways.
# ===========================================================================


def _gated_contextualize(gate: threading.Event, *, wait_first: bool, text: str):
    def _run(ctx, mode, subject, data_block, research_inputs):
        if wait_first:
            assert gate.wait(timeout=5), "the paired fake never released the gate"
        else:
            gate.set()
        return SubskillResult(
            parts=(phase_section_part("Context", text),),
            synthesis_inputs=({"text": text},),
            failed=False,
        )

    return _run


def _gated_research(gate: threading.Event, *, wait_first: bool, text: str):
    def _run(ctx, mode, subject):
        if wait_first:
            assert gate.wait(timeout=5), "the paired fake never released the gate"
        else:
            gate.set()
        return SubskillResult(
            parts=(phase_section_part("Research", text),),
            synthesis_inputs=(
                {
                    "schema_name": "sustainability",
                    "title": "Research",
                    "summary": text,
                    "items": (),
                    "degraded": False,
                    "degrade_reason": None,
                },
            ),
            failed=False,
        )

    return _run


def test_concurrency_determinism_byte_identical_regardless_of_which_finishes_first(monkeypatch):
    """Two scenarios, deliberately inverted -- ``gate.wait()``/``gate.set()``
    force a REAL completion-order guarantee (not a sleep-based approximation):
    in scenario A research is held back until contextualize has already
    returned; in scenario B contextualize is held back until research has
    already returned. Both must still produce the SAME final part order
    (contextualize's part, then research's part -- doc 02 section 4.1's own
    numbering: 2. contextualize, 3. research), proving the skill's own
    ``.result()`` consumption order -- not wall-clock completion order --
    decides the output.
    """
    monkeypatch.setattr(existing_skill, "_today", lambda: _ANCHOR)
    context_part = phase_section_part("Context", "C-text")
    research_part = phase_section_part("Research", "R-text")

    # Scenario A: contextualize returns immediately and releases the gate;
    # research is blocked until it does (research finishes LAST).
    gate_a = threading.Event()
    monkeypatch.setattr(
        existing_skill.contextualize_subskill,
        "run",
        _gated_contextualize(gate_a, wait_first=False, text="C-text"),
    )
    monkeypatch.setattr(
        existing_skill.research_subskill,
        "run",
        _gated_research(gate_a, wait_first=True, text="R-text"),
    )
    ctx_a = _ctx(data=_FakeDataClient())
    result_a = existing_skill.run(ctx_a, existing_schema.Args(customer="Acme"))

    # Scenario B (inverted): research returns immediately and releases the
    # gate; contextualize is blocked until it does (contextualize finishes
    # LAST -- the opposite of scenario A).
    gate_b = threading.Event()
    monkeypatch.setattr(
        existing_skill.contextualize_subskill,
        "run",
        _gated_contextualize(gate_b, wait_first=True, text="C-text"),
    )
    monkeypatch.setattr(
        existing_skill.research_subskill,
        "run",
        _gated_research(gate_b, wait_first=False, text="R-text"),
    )
    ctx_b = _ctx(data=_FakeDataClient())
    result_b = existing_skill.run(ctx_b, existing_schema.Args(customer="Acme"))

    assert result_a.parts[2] == context_part
    assert result_a.parts[3] == research_part
    assert result_b.parts[2] == context_part
    assert result_b.parts[3] == research_part
    assert result_a.parts == result_b.parts


# ===========================================================================
# Section E -- degraded research (ctx.tools is None): both briefs continue,
# ok=True, previously streamed parts stand (doc 02 section 6).
# ===========================================================================


def test_existing_customer_brief_continues_when_research_is_degraded():
    ctx = _ctx(data=_FakeDataClient(), tools=None)
    args = existing_schema.Args(customer="Northstar Lines")

    result = existing_skill.run(ctx, args)

    assert result.ok is True
    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["metric_grid", "table"] + ["phase_section"] * 5
    research_parts = result.parts[3:6]
    for part in research_parts:
        assert "unavailable" in part["payload"]["markdown"]
    # the metrics/context/strategy phases are unaffected by research's degrade
    assert "Phases completed: contextualize, strategize" in result.proof
    assert "Phases failed: research" in result.proof
    assert "Transport: none" in result.proof


def test_new_prospect_brief_continues_when_research_is_degraded():
    ctx = _ctx(tools=None)
    args = prospect_schema.Args(prospect_name="Meridian Global Shipping")

    result = prospect_skill.run(ctx, args)

    assert result.ok is True
    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["phase_section"] * 4
    for part in result.parts[:2]:
        assert "unavailable" in part["payload"]["markdown"]
    assert "Phases completed: contextualize, strategize" in result.proof
    assert "Phases failed: research" in result.proof
    assert "Transport: none" in result.proof


# ===========================================================================
# Section F -- exception-escape guard (P8 whole-branch final-review wave,
# 2026-07-30, item 2 / I-4): a raw exception escaping a subskill's own
# dispatch (e.g. a BotoCoreError past bedrock's ClientError-only catch, or
# any failure a subskill's own stub/live branches do not already turn into
# failed=True) must not 500 the whole brief -- skill.py's own try/except
# (`_run_subskill_or_failed`, independently declared in both skill.py
# modules) catches it and synthesizes that phase's own failed-phase
# result, the SAME shape (and, for contextualize/strategize, the SAME
# pinned text) the subskill's own internal error branches already use --
# doc 02 section 6's anti-happy-path rule read at this outer grain too.
# One test per subskill, per flow: six in total, each proving the OTHER
# two phases complete normally and only the raising one is marked failed.
# ===========================================================================


def test_existing_customer_brief_continues_when_contextualize_raises(monkeypatch):
    monkeypatch.setattr(existing_skill, "_today", lambda: _ANCHOR)
    monkeypatch.setattr(existing_skill.contextualize_subskill, "run", _raising_subskill)
    ctx = _ctx(data=_FakeDataClient(), tools=_Tools(research=FixtureResearchTool()))
    args = existing_schema.Args(customer="Northstar Lines")

    result = existing_skill.run(ctx, args)

    assert result.ok is True
    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["metric_grid", "table"] + ["phase_section"] * 5
    titles = [part["payload"]["title"] for part in result.parts[2:]]
    assert titles == [
        "Context",
        "Sustainability & ESG",
        "Market Position",
        "Strategic Profile",
        "Strategy",
    ]
    context_markdown = result.parts[2]["payload"]["markdown"]
    assert "unavailable" in context_markdown
    assert "Phases completed: research, strategize" in result.proof
    assert "Phases failed: contextualize" in result.proof


def test_existing_customer_brief_continues_when_research_raises(monkeypatch, caplog):
    monkeypatch.setattr(existing_skill, "_today", lambda: _ANCHOR)
    monkeypatch.setattr(existing_skill.research_subskill, "run", _raising_subskill)
    ctx = _ctx(data=_FakeDataClient(), tools=_Tools(research=FixtureResearchTool()))
    args = existing_schema.Args(customer="Northstar Lines")

    with caplog.at_level("ERROR"):
        result = existing_skill.run(ctx, args)

    assert result.ok is True
    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["metric_grid", "table"] + ["phase_section"] * 5
    titles = [part["payload"]["title"] for part in result.parts[2:]]
    assert titles == [
        "Context",
        "Sustainability & ESG",
        "Market Position",
        "Strategic Profile",
        "Strategy",
    ]
    research_parts = result.parts[3:6]
    for part in research_parts:
        assert "unavailable" in part["payload"]["markdown"]
    assert "Phases completed: contextualize, strategize" in result.proof
    assert "Phases failed: research" in result.proof
    # the guard logs the escape rather than letting it vanish silently --
    # proven, not merely documented (the module docstring's own contract).
    assert any("research" in r.message and "RuntimeError" in r.message for r in caplog.records)


def test_existing_customer_brief_continues_when_strategize_raises(monkeypatch):
    monkeypatch.setattr(existing_skill, "_today", lambda: _ANCHOR)
    monkeypatch.setattr(existing_skill.strategize_subskill, "run", _raising_subskill)
    ctx = _ctx(data=_FakeDataClient(), tools=_Tools(research=FixtureResearchTool()))
    args = existing_schema.Args(customer="Northstar Lines")

    result = existing_skill.run(ctx, args)

    assert result.ok is True
    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["metric_grid", "table"] + ["phase_section"] * 5
    titles = [part["payload"]["title"] for part in result.parts[2:]]
    assert titles == [
        "Context",
        "Sustainability & ESG",
        "Market Position",
        "Strategic Profile",
        "Strategy",
    ]
    strategy_markdown = result.parts[-1]["payload"]["markdown"]
    assert "unavailable" in strategy_markdown
    assert "Phases completed: contextualize, research" in result.proof
    assert "Phases failed: strategize" in result.proof


def test_new_prospect_brief_continues_when_research_raises(monkeypatch):
    monkeypatch.setattr(prospect_skill.research_subskill, "run", _raising_subskill)
    ctx = _ctx(tools=_Tools(research=FixtureResearchTool()))
    args = prospect_schema.Args(prospect_name="Meridian Global Shipping")

    result = prospect_skill.run(ctx, args)

    assert result.ok is True
    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["phase_section"] * 4
    titles = [part["payload"]["title"] for part in result.parts]
    assert titles == ["Operational Profile", "Web Research", "Context", "Strategy"]
    for part in result.parts[:2]:
        assert "unavailable" in part["payload"]["markdown"]
    assert "Phases completed: contextualize, strategize" in result.proof
    assert "Phases failed: research" in result.proof


def test_new_prospect_brief_continues_when_contextualize_raises(monkeypatch):
    monkeypatch.setattr(prospect_skill.contextualize_subskill, "run", _raising_subskill)
    ctx = _ctx(tools=_Tools(research=FixtureResearchTool()))
    args = prospect_schema.Args(prospect_name="Meridian Global Shipping")

    result = prospect_skill.run(ctx, args)

    assert result.ok is True
    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["phase_section"] * 4
    titles = [part["payload"]["title"] for part in result.parts]
    assert titles == ["Operational Profile", "Web Research", "Context", "Strategy"]
    context_markdown = result.parts[2]["payload"]["markdown"]
    assert "unavailable" in context_markdown
    assert "Phases completed: research, strategize" in result.proof
    assert "Phases failed: contextualize" in result.proof


def test_new_prospect_brief_continues_when_strategize_raises(monkeypatch):
    monkeypatch.setattr(prospect_skill.strategize_subskill, "run", _raising_subskill)
    ctx = _ctx(tools=_Tools(research=FixtureResearchTool()))
    args = prospect_schema.Args(prospect_name="Meridian Global Shipping")

    result = prospect_skill.run(ctx, args)

    assert result.ok is True
    kinds = [part["kind"] for part in result.parts]
    assert kinds == ["phase_section"] * 4
    titles = [part["payload"]["title"] for part in result.parts]
    assert titles == ["Operational Profile", "Web Research", "Context", "Strategy"]
    strategy_markdown = result.parts[-1]["payload"]["markdown"]
    assert "unavailable" in strategy_markdown
    assert "Phases completed: research, contextualize" in result.proof
    assert "Phases failed: strategize" in result.proof


# ===========================================================================
# Section G -- PDF path with a fake artifact store.
# ===========================================================================


@pytest.mark.pdf
@pytest.mark.skipif(
    not _HAS_WEASYPRINT, reason="weasyprint needs Pango/Cairo - run in the container"
)
def test_existing_customer_brief_pdf_path_with_a_fake_artifact_store(monkeypatch):
    monkeypatch.setattr(existing_skill, "_today", lambda: _ANCHOR)
    store = _FakeArtifactStore()
    ctx = _ctx(
        data=_FakeDataClient(), tools=_Tools(research=FixtureResearchTool()), artifacts=store
    )
    args = existing_schema.Args(customer="Northstar Lines")

    result = existing_skill.run(ctx, args)

    assert result.ok is True
    assert len(result.artifacts) == 1
    ref = result.artifacts[0]
    assert ref.name == "existing-customer-brief/2026-07-01/northstar-lines-brief.pdf"
    assert ref.mime == "application/pdf"
    assert len(store.puts) == 1
    uploaded_key, uploaded_content = store.puts[0]
    assert uploaded_key == ref.name
    assert uploaded_content.startswith(b"%PDF")
    assert result.proof[-3] == f"Artifact: {ref.name}"
    assert result.proof[-2].startswith("Pages: ")
    assert result.proof[-1].startswith("Bytes: ")


@pytest.mark.pdf
@pytest.mark.skipif(
    not _HAS_WEASYPRINT, reason="weasyprint needs Pango/Cairo - run in the container"
)
def test_new_prospect_brief_pdf_path_with_a_fake_artifact_store():
    store = _FakeArtifactStore()
    ctx = _ctx(tools=_Tools(research=FixtureResearchTool()), artifacts=store)
    args = prospect_schema.Args(prospect_name="Meridian Global Shipping")

    result = prospect_skill.run(ctx, args)

    assert result.ok is True
    assert len(result.artifacts) == 1
    ref = result.artifacts[0]
    assert ref.name.startswith("new-prospect-brief/")
    assert ref.name.endswith("meridian-global-shipping-prospect-brief.pdf")
    assert len(store.puts) == 1
    assert store.puts[0][1].startswith(b"%PDF")


# ===========================================================================
# Section H -- dispatch through the real SkillRegistry, with validated Args.
# ===========================================================================


def test_existing_customer_brief_dispatches_through_the_real_registry(monkeypatch):
    monkeypatch.setattr(existing_skill, "_today", lambda: _ANCHOR)
    reg = SkillRegistry.discover()
    ctx = _ctx(data=_FakeDataClient(), tools=_Tools(research=FixtureResearchTool()))

    result = reg.dispatch(
        "customer_insight.existing_customer_brief", {"customer": "Northstar Lines"}, ctx
    )

    assert result.ok is True
    assert result.error is None
    assert any(part["kind"] == "metric_grid" for part in result.parts)


def test_existing_customer_brief_dispatch_rejects_missing_customer_as_a_422():
    reg = SkillRegistry.discover()
    ctx = _ctx(data=_FakeDataClient())

    result = reg.dispatch("customer_insight.existing_customer_brief", {}, ctx)

    assert result.ok is False
    assert result.error["status"] == 422
    assert "customer" in result.error["detail"]


def test_new_prospect_brief_dispatches_through_the_real_registry():
    reg = SkillRegistry.discover()
    ctx = _ctx(tools=_Tools(research=FixtureResearchTool()))

    result = reg.dispatch(
        "customer_insight.new_prospect_brief",
        {"prospect_name": "Meridian Global Shipping"},
        ctx,
    )

    assert result.ok is True
    assert result.error is None
    assert any(part["kind"] == "phase_section" for part in result.parts)


def test_new_prospect_brief_dispatch_rejects_missing_prospect_name_as_a_422():
    reg = SkillRegistry.discover()
    ctx = _ctx()

    result = reg.dispatch("customer_insight.new_prospect_brief", {}, ctx)

    assert result.ok is False
    assert result.error["status"] == 422
    assert "prospect_name" in result.error["detail"]


# ===========================================================================
# Section I -- ASCII guard.
# ===========================================================================


def test_this_test_module_is_ascii_on_disk():
    offending = sorted({byte for byte in Path(__file__).read_bytes() if byte > 0x7F})
    assert not offending, f"{Path(__file__).name} holds non-ASCII bytes: {offending}"
