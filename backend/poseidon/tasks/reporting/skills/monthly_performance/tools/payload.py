"""Assemble the report payload, version 1 (the Task 6 contract, pinned in
code by ``tests/payload_fixture.py``).

This is the one place payload keys are spelled. ``row_from_merged`` (ROW)
and ``kpi_from_delta`` (DELTA) are its two row shapes; ``tools/context.py``
reuses ``kpi_from_delta`` for the whole-book deltas. Every display string
comes from ``mom.display`` / ``mom.fmt_change``, so the template narrative,
the grounding index and the rendered HTML all read the same strings.

``periods.<side>.end`` is EXCLUSIVE, like every ``PeriodWindow`` in Poseidon:
it is exactly ``PeriodWindow.end``, the same half-open window the
``report_run`` row stores, so PR A2 reads one convention from both.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from poseidon.core.analytics import mom
from poseidon.core.data import PeriodWindow

if TYPE_CHECKING:
    from .frames import Timed
    from .periods import ResolvedPeriods

PAYLOAD_VERSION = 1


def kpi_from_delta(d: mom.Delta, metric: str) -> dict[str, Any]:
    """DELTA: raw sides, change, pct_change and their display strings."""
    return {
        "prior": d.prior,
        "current": d.current,
        "change": d.change,
        "pct_change": d.pct_change,
        "display": mom.display(d, mom.KIND_BY_METRIC[metric]),
    }


def row_from_merged(row: mom.MergedRow) -> dict[str, Any]:
    """ROW: one entity, port or deal class with GP/VOLUME/MARGIN per side."""
    metrics = {m: row.metrics[m] for m in mom.ROW_METRICS}
    return {
        "key": row.key,
        "status": row.status,
        "prior": {m: d.prior for m, d in metrics.items()},
        "current": {m: d.current for m, d in metrics.items()},
        "change": {m: d.change for m, d in metrics.items()},
        "pct": {m: d.pct_change for m, d in metrics.items()},
        "display": {m: mom.display(d, mom.KIND_BY_METRIC[m]) for m, d in metrics.items()},
    }


def mover_from(mover: mom.Mover) -> dict[str, Any]:
    """MOVER: a ROW plus its driver word."""
    return {**row_from_merged(mover.row), "driver": mover.driver}


def table_from_ranked(table: mom.Ranked) -> dict[str, Any]:
    return {
        "rows": [row_from_merged(r) for r in table.rows],
        "other": None if table.other is None else row_from_merged(table.other),
        "footer": row_from_merged(table.footer),
    }


def _period(window: PeriodWindow, label: str) -> dict[str, str]:
    return {
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "label": label,
    }


def narrative_section(
    template_md: str, llm_md: str | None, grounded: bool, flags: Sequence[str]
) -> dict[str, Any]:
    return {
        "template_md": template_md,
        "llm_md": llm_md,
        "grounded": grounded,
        "flags": list(flags),
    }


def query_lines(queries: Sequence[Timed]) -> list[dict[str, Any]]:
    return [{"label": q.label, "sql_hash": q.sql_hash, "rows": q.rows, "ms": q.ms} for q in queries]


def build_payload(
    *,
    run_id: str,
    perspective: str,
    entity_column: str,
    entity_label: str,
    office_column: str | None,
    office_value: str | None,
    scope_label: str,
    periods: ResolvedPeriods,
    kpis: Mapping[str, mom.Delta],
    tables: Mapping[str, mom.Ranked],
    movers: mom.Movers,
    new_lost: mom.NewLost,
    entity_context: dict[str, dict] | None,
    narrative: Mapping[str, Any] | None,
    suggested_questions: Sequence[str],
    proof: Sequence[str],
    queries: Sequence[Timed],
    generated_at: datetime,
    source: str,
    generated_by: str,
) -> dict[str, Any]:
    """The v1 payload. ``narrative=None`` gives the empty section a partial
    payload carries before the template narrative is rendered from it."""
    return {
        "payload_version": PAYLOAD_VERSION,
        "run_id": run_id,
        "perspective": perspective,
        "entity": {"column": entity_column, "label": entity_label},
        "scope": {
            "office_column": office_column,
            "office_value": office_value,
            "label": scope_label,
        },
        "periods": {
            "current": _period(periods.current, periods.label_current),
            "prior": _period(periods.prior, periods.label_prior),
            "window_kind": periods.window_kind,
        },
        "kpis": {m: kpi_from_delta(kpis[m], m) for m in mom.KPI_METRICS},
        "tables": {name: table_from_ranked(t) for name, t in tables.items()},
        "movers": {
            "increases": [mover_from(m) for m in movers.increases],
            "decreases": [mover_from(m) for m in movers.decreases],
        },
        "new_lost": {
            "new": [row_from_merged(r) for r in new_lost.new],
            "lost": [row_from_merged(r) for r in new_lost.lost],
        },
        "entity_context": entity_context,
        "narrative": dict(narrative)
        if narrative is not None
        else narrative_section("", None, False, []),
        "suggested_questions": list(suggested_questions),
        "proof": list(proof),
        "queries": query_lines(queries),
        "generated_at": generated_at.isoformat(),
        "source": source,
        "generated_by": generated_by,
    }


def with_flag(payload: Mapping[str, Any], flag: str) -> dict[str, Any]:
    """A copy of ``payload`` with one more narrative flag (the PDF guard)."""
    narrative = dict(payload["narrative"])
    narrative["flags"] = [*narrative["flags"], flag]
    return {**payload, "narrative": narrative}
