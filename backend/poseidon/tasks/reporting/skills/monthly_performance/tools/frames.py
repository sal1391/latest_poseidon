"""The report's queries: two six-way frames, two certified totals, and the
reconciliation between them.

Only ``poseidon.core.data`` is imported here -- never a client or a driver.
Every call goes through the ``DataClient`` the skill hands in (the snapshot
of one REPEATABLE READ connection), and every spec is stamped with the D16
scope value the skill resolved once.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import fsum
from typing import TYPE_CHECKING, TypeVar

from poseidon.core.analytics import mom
from poseidon.core.data import (
    FrameQuerySpec,
    FrameResult,
    MetricQuerySpec,
    PeriodWindow,
    SpecValidationError,
    build_frame_query,
    build_metric_query,
)

if TYPE_CHECKING:
    from poseidon.core.data.client import DataClient

    from .periods import ResolvedPeriods

ENTITY = "MARINE_SALES_PLANNING_V"
GROUP_BY = (
    "CUST_NM",
    "SUPPLIER_NM",
    "LOC_NM",
    "CUSTOMER_TEAM_NAME",
    "PRIMARY_SUPPLY_TEAM_OFFICE",
    "DEAL_CLASSIFICATION_TRADE_CUT",
)
TOTAL_METRICS = ("GP", "VOLUME")
UNBUILDABLE = "unbuildable"

_T = TypeVar("_T")


@dataclass(frozen=True)
class Timed:
    """One query's proof line."""

    label: str
    sql_hash: str
    rows: int
    ms: int


@dataclass(frozen=True)
class Slices:
    prior: FrameResult
    current: FrameResult
    prior_total: dict[str, float]
    current_total: dict[str, float]
    queries: list[Timed]


class ReconciliationError(Exception):
    """The frame rows do not add up to the certified total for a window."""


def frame_spec(
    window: PeriodWindow,
    filters: Mapping[str, tuple[str, ...]],
    scope_value: str | None,
) -> FrameQuerySpec:
    return FrameQuerySpec(
        entity=ENTITY,
        metrics=mom.SUM_METRICS,
        period=window,
        group_by=GROUP_BY,
        filters=dict(filters),
        scope_value=scope_value,
    )


def sql_hash(spec: FrameQuerySpec | MetricQuerySpec, dialect: str) -> str:
    """sha256 of the builder's SQL text, first 12 hex characters."""
    if isinstance(spec, FrameQuerySpec):
        sql, _ = build_frame_query(spec, dialect)
    else:
        sql, _ = build_metric_query(spec, dialect)
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()[:12]


def timed(
    label: str,
    spec: FrameQuerySpec | MetricQuerySpec,
    dialect: str,
    call: Callable[[], _T],
    rows: Callable[[_T], int],
) -> tuple[_T, Timed]:
    """Run one query and record its proof line.

    The client runs first: it builds the same SQL through the same certified
    builder, so a spec the builder refuses fails there with the builder's own
    error. The hash is taken afterwards; the builder refusing a spec the
    client already ran is only possible for a client that does not build SQL
    (the offline test double), and that proof line says ``unbuildable``
    rather than failing a run whose data came back.
    """
    start = time.perf_counter()
    result = call()
    ms = round((time.perf_counter() - start) * 1000)
    try:
        digest = sql_hash(spec, dialect)
    except SpecValidationError:
        # Reachable only through a fake client: every real client runs this
        # same certified builder before the query, so it would have raised.
        digest = UNBUILDABLE
    return result, Timed(label=label, sql_hash=digest, rows=rows(result), ms=ms)


def _frame_total(frame: FrameResult) -> dict[str, float]:
    return mom.totals(row.values for row in frame.rows)


def _num(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def run_slices(
    data: DataClient,
    periods: ResolvedPeriods,
    filters: Mapping[str, tuple[str, ...]],
    scope_value: str | None,
    dialect: str,
) -> Slices:
    """Two frames and two certified totals; each window must reconcile on GP
    and VOLUME to the half cent, or the run stops with ``ReconciliationError``."""
    queries: list[Timed] = []
    frames: dict[str, FrameResult] = {}
    windows = {"prior": periods.prior, "current": periods.current}
    for side, window in windows.items():
        spec = frame_spec(window, filters, scope_value)
        frames[side], proof = timed(
            f"frame {side}", spec, dialect, lambda s=spec: data.run_frame_query(s), _rows
        )
        queries.append(proof)
    totals: dict[str, dict[str, float]] = {}
    for side, window in windows.items():
        spec = MetricQuerySpec(
            entity=ENTITY,
            metrics=TOTAL_METRICS,
            period=window,
            filters=dict(filters),
            scope_value=scope_value,
        )
        result, proof = timed(
            f"total {side}", spec, dialect, lambda s=spec: data.run_metric_query(s), _one
        )
        totals[side] = {m: _num(result.values.get(m)) for m in TOTAL_METRICS}
        queries.append(proof)

    labels = {"prior": periods.label_prior, "current": periods.label_current}
    frame_totals = {side: _frame_total(frames[side]) for side in windows}
    for side in windows:
        for metric in TOTAL_METRICS:
            x, y = frame_totals[side][metric], totals[side][metric]
            if not mom.reconciles(x, y):
                raise ReconciliationError(
                    f"reconciliation failed: {labels[side]} {metric} frame {x:,.2f} "
                    f"vs total {y:,.2f}"
                )
    return Slices(
        prior=frames["prior"],
        current=frames["current"],
        prior_total=frame_totals["prior"],
        current_total=frame_totals["current"],
        queries=queries,
    )


def _rows(frame: FrameResult) -> int:
    return len(frame.rows)


def _one(_result: object) -> int:
    return 1


def rollup(frame: FrameResult, column: str) -> dict[str, dict[str, float]]:
    """Group the frame rows by ``column``; fsum each SUM_METRIC (None -> 0)."""
    position = frame.group_by.index(column)
    groups: dict[str, list[dict[str, float | None]]] = {}
    for row in frame.rows:
        groups.setdefault(row.keys[position], []).append(row.values)
    return {
        key: {m: fsum(_num(values.get(m)) for values in rows) for m in mom.SUM_METRICS}
        for key, rows in groups.items()
    }
