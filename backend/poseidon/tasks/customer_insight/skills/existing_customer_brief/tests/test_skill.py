"""Tests for ``customer_insight.existing_customer_brief`` co-located with
the skill itself (folder law) -- ``Args``/``SKILL_META`` and ``skill.py``'s
own small helper functions, unit-tested directly.

The BIG cross-cutting scenarios (both skills' full runs, concurrency
determinism, degraded-research continuation, artifacts-None skip, the PDF
path with a fake artifact store, dispatch through the real registry) live
in ``backend/tests/test_brief_skills.py`` instead -- this file mirrors
``data_qa.metric_query``'s/``research.web_research``'s own co-located
``tests/test_skill.py`` proportions: ``Args`` validation plus the skill
module's own private helpers, each in isolation.

No Postgres, no network, no WeasyPrint: every test here is plain-offline.
"""

import datetime as dt

import pytest
from pydantic import ValidationError

from poseidon.core.data.client import BreakdownResult, BreakdownRow, MetricResult
from poseidon.core.data.specs import PeriodWindow
from poseidon.tasks.customer_insight.skills.existing_customer_brief import schema, skill
from poseidon.tasks.customer_insight.skills.existing_customer_brief.schema import (
    SKILL_META,
    Args,
)
from poseidon.tasks.customer_insight.skills.existing_customer_brief.tools.fetch_metrics import (
    SIX_METRICS,
)

# ---------------------------------------------------------------------------
# Args -- customer required, recency_days defaults to 365 and must be >= 1.
# ---------------------------------------------------------------------------


def test_args_requires_customer():
    with pytest.raises(ValidationError):
        Args()


def test_args_customer_alone_is_sufficient():
    args = Args(customer="Northstar Lines")

    assert args.customer == "Northstar Lines"
    assert args.recency_days == 365


def test_args_rejects_blank_customer():
    with pytest.raises(ValidationError):
        Args(customer="")


def test_args_accepts_an_explicit_recency_days():
    args = Args(customer="Northstar Lines", recency_days=30)

    assert args.recency_days == 30


def test_args_rejects_a_non_positive_recency_days():
    """A window ``anchor - timedelta(days=recency_days) .. anchor`` needs
    ``recency_days >= 1`` -- zero would make the window empty (start ==
    end), which ``PeriodWindow`` itself rejects; catching it here, as a
    structured 422 at argument validation, is cheaper than letting that
    surface as a ``ValueError`` from deep inside window construction."""
    with pytest.raises(ValidationError):
        Args(customer="Northstar Lines", recency_days=0)


# ---------------------------------------------------------------------------
# SKILL_META -- description names the flow, stays under the registry's cap.
# ---------------------------------------------------------------------------


def test_skill_meta_description_is_non_blank_and_under_the_cap():
    assert SKILL_META["description"].strip()
    assert len(SKILL_META["description"]) <= 300


def test_skill_meta_description_names_the_existing_customer_flow():
    description = SKILL_META["description"].lower()
    assert "existing" in description
    assert "customer" in description


def test_skill_meta_has_at_least_one_example():
    assert SKILL_META["examples"]


# ---------------------------------------------------------------------------
# skill.py's own private helpers, unit-tested directly.
# ---------------------------------------------------------------------------


def test_metric_display_keys_match_fetch_metrics_six_metrics_exactly():
    """Cross-checks the hand-authored friendly-name/unit constant against
    the P3 tool's own ``SIX_METRICS`` -- the same "two independently
    authored lists must agree" discipline ``contextualize.subskill``'s own
    ``_FIELD_DICTIONARY`` cross-check test uses (see ``skill.py``'s module
    docstring for why this is a fixed constant rather than an
    ``poseidon.core.ontology`` lookup)."""
    assert set(skill._METRIC_DISPLAY) == set(SIX_METRICS)


def test_round_uses_zero_decimals_for_every_metric_except_margin():
    assert skill._round("VOLUME", 18500.6) == 18501
    assert skill._round("GP", 412000.4) == 412000
    assert isinstance(skill._round("VOLUME", 100.0), int)


def test_round_uses_two_decimals_for_margin():
    assert skill._round("MARGIN", 22.2226) == 22.22


def test_round_passes_none_through_unchanged():
    assert skill._round("VOLUME", None) is None
    assert skill._round("MARGIN", None) is None


def test_metric_grid_shape_and_rounding():
    prior = MetricResult(
        entity="MARINE_SALES_PLANNING_V",
        period=PeriodWindow(dt.date(2025, 1, 1), dt.date(2026, 1, 1)),
        values={m: 100.4 for m in SIX_METRICS},
    )
    ytd = MetricResult(
        entity="MARINE_SALES_PLANNING_V",
        period=PeriodWindow(dt.date(2026, 1, 1), dt.date(2026, 7, 1)),
        values={m: 200.6 for m in SIX_METRICS},
    )

    part = skill._metric_grid(prior, ytd)

    assert part["kind"] == "metric_grid"
    assert part["payload"]["periods"] == {
        "a": {"start": "2025-01-01", "end": "2026-01-01"},
        "b": {"start": "2026-01-01", "end": "2026-07-01"},
    }
    names = [m["name"] for m in part["payload"]["metrics"]]
    assert names == list(SIX_METRICS)
    volume = next(m for m in part["payload"]["metrics"] if m["name"] == "VOLUME")
    assert volume == {"name": "VOLUME", "friendly": "Volume", "a": 100, "b": 201, "unit": "tons"}
    margin = next(m for m in part["payload"]["metrics"] if m["name"] == "MARGIN")
    assert margin["a"] == 100.4 and margin["b"] == 200.6


def test_ports_table_shape_and_rounding():
    result = BreakdownResult(
        entity="MARINE_SALES_PLANNING_V",
        group_by="LOC_NM",
        rows=[
            BreakdownRow(key="Singapore", values={"GP": 120000.4}),
            BreakdownRow(key="Rotterdam", values={"GP": 95000.6}),
        ],
    )

    part = skill._ports_table(result)

    assert part == {
        "kind": "table",
        "payload": {
            "columns": ["Port", "Gross Profit"],
            "rows": [["Singapore", 120000], ["Rotterdam", 95001]],
        },
    }


def test_ports_table_empty_rows_renders_an_empty_table_not_a_crash():
    result = BreakdownResult(entity="MARINE_SALES_PLANNING_V", group_by="LOC_NM", rows=[])

    part = skill._ports_table(result)

    assert part["payload"]["rows"] == []


def test_ports_window_spans_recency_days_ending_at_anchor():
    window = skill._ports_window(dt.date(2026, 7, 1), 30)

    assert window == PeriodWindow(dt.date(2026, 6, 1), dt.date(2026, 7, 1))


def test_data_summary_carries_no_services_key():
    """The disclosed judgment call: neither ``fetch_metrics`` nor
    ``fetch_top_ports`` exposes a service-line dimension
    (``MARINE_SALES_PLANNING_V`` has none), so populating "services" would
    be a fabricated business fact. Omitting the key lets
    ``strategize.subskill``'s own documented fallback
    ("[requires live synthesis]") apply honestly."""
    prior = MetricResult(
        entity="MARINE_SALES_PLANNING_V",
        period=PeriodWindow(dt.date(2025, 1, 1), dt.date(2026, 1, 1)),
        values=dict.fromkeys(SIX_METRICS, 1.0),
    )
    ytd = MetricResult(
        entity="MARINE_SALES_PLANNING_V",
        period=PeriodWindow(dt.date(2026, 1, 1), dt.date(2026, 7, 1)),
        values=dict.fromkeys(SIX_METRICS, 2.0),
    )
    ports = BreakdownResult(entity="MARINE_SALES_PLANNING_V", group_by="LOC_NM", rows=[])

    summary = skill._data_summary(prior, ytd, ports)

    assert "services" not in summary


def test_data_summary_counts_six_metrics_times_two_periods_plus_ports_line():
    prior = MetricResult(
        entity="MARINE_SALES_PLANNING_V",
        period=PeriodWindow(dt.date(2025, 1, 1), dt.date(2026, 1, 1)),
        values=dict.fromkeys(SIX_METRICS, 1.0),
    )
    ytd = MetricResult(
        entity="MARINE_SALES_PLANNING_V",
        period=PeriodWindow(dt.date(2026, 1, 1), dt.date(2026, 7, 1)),
        values=dict.fromkeys(SIX_METRICS, 2.0),
    )
    ports = BreakdownResult(
        entity="MARINE_SALES_PLANNING_V",
        group_by="LOC_NM",
        rows=[BreakdownRow(key="Singapore", values={"GP": 1.0})],
    )

    summary = skill._data_summary(prior, ytd, ports)

    assert len(summary) == len(SIX_METRICS) * 2 + 1


def test_phase_proof_lists_completed_and_failed_by_name_in_doc_order():
    proof = skill._phase_proof({"contextualize": False, "research": True, "strategize": False})

    assert proof == [
        "Phases completed: contextualize, strategize",
        "Phases failed: research",
    ]


def test_phase_proof_all_completed_reports_none_failed():
    proof = skill._phase_proof({"contextualize": False, "research": False, "strategize": False})

    assert proof[1] == "Phases failed: none"


def test_transport_name_is_none_when_ctx_tools_is_absent():
    ctx = _bare_ctx(tools=None)

    assert skill._transport_name(ctx) == "none"


def test_transport_name_reads_the_resolved_research_tools_class_name():
    class _FakeResearchTool:
        def search(self, *, query, schema_name, recency_days=None):  # pragma: no cover
            raise AssertionError("must not be called just to name the transport")

    class _Tools:
        research = _FakeResearchTool()

    ctx = _bare_ctx(tools=_Tools())

    assert skill._transport_name(ctx) == "_FakeResearchTool"


def test_today_returns_a_real_date_and_is_monkeypatchable(monkeypatch):
    assert isinstance(skill._today(), dt.date)

    monkeypatch.setattr(skill, "_today", lambda: dt.date(2026, 7, 1))

    assert skill._today() == dt.date(2026, 7, 1)


# ---------------------------------------------------------------------------
# ASCII guard -- this task's new files.
# ---------------------------------------------------------------------------


def test_new_skill_files_are_ascii_on_disk():
    from pathlib import Path

    for path in [Path(schema.__file__), Path(skill.__file__), Path(__file__)]:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bare_ctx(*, tools):
    from poseidon.core.config import Settings
    from poseidon.core.skills.context import SkillContext

    return SkillContext(
        data=object(),
        artifacts=None,
        settings=Settings(
            _env_file=None,
            database_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/void",
            s3_bucket="poseidon-artifacts",
        ),
        tools=tools,
    )
