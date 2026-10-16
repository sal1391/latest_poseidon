"""Month-over-month math, ported from mom-comparison into one home.

Vocabulary: the spec says ``prior`` and ``current``; mom-comparison says
``a`` and ``b``. Here ``a = prior`` and ``b = current`` throughout, so
``vol_a`` is the prior month's volume and ``m_b`` the current month's margin.

Zero-base rule: a percent change is measured against ``abs(prior)``. When the
prior value is zero there is no base to measure against, so ``pct_change`` is
``None`` (rendered "new") rather than an infinite or invented percentage. A
zero-to-zero delta has ``change == 0`` and renders "flat", never "new".

Every total (the KPI tiles, a ranked table's "Other" row and its footer) goes
through the same ``_sum_rows`` and ``_derived`` helpers. ``_sum_rows`` uses
``math.fsum`` so the sum is correctly rounded and therefore independent of row
order: the footer sums merged rows in key order while the KPI sums the input
rows in input order, and both must agree exactly. MARGIN and WIN_RATE are
always recomputed from summed components, never averaged.

No I/O, no pandas, no imports from ``poseidon.tasks``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

SUM_METRICS = ("GP", "VOLUME", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST")
KPI_METRICS = ("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST", "WIN_RATE")
ROW_METRICS = ("GP", "VOLUME", "MARGIN")

_COUNT_METRICS = ("NUM_WON", "NUM_INQUIRIES", "NUM_LOST")

Driver = Literal["volume-driven", "margin-driven", "both", "unchanged", "new", "lost"]
Status = Literal["both", "new", "lost"]
Kind = Literal["money", "tons", "margin", "count", "rate"]

KIND_BY_METRIC: dict[str, Kind] = {
    "GP": "money",
    "VOLUME": "tons",
    "MARGIN": "margin",
    "NUM_WON": "count",
    "NUM_INQUIRIES": "count",
    "NUM_LOST": "count",
    "WIN_RATE": "rate",
}

_VOLUME_DRIVEN_ABOVE = 0.65
_MARGIN_DRIVEN_BELOW = 0.35


@dataclass(frozen=True)
class Delta:
    prior: float
    current: float
    change: float  # current - prior
    pct_change: float | None  # percent units, against abs(prior); None when prior == 0


def delta(prior: float, current: float) -> Delta:
    change = current - prior
    pct = change / abs(prior) * 100 if prior != 0 else None
    return Delta(prior, current, change, pct)


def _num(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _sum_rows(rows: Sequence[Mapping[str, float | None]], keys: Iterable[str]) -> dict[str, float]:
    """Correctly rounded (order-independent) sum per key; missing or None counts as 0."""
    return {k: math.fsum(_num(row.get(k)) for row in rows) for k in keys}


def _derived(sums: Mapping[str, float]) -> dict[str, float]:
    """MARGIN and WIN_RATE recomputed from summed components."""
    out: dict[str, float] = {}
    if "GP" in sums and "VOLUME" in sums:
        out["MARGIN"] = sums["GP"] / sums["VOLUME"] if sums["VOLUME"] != 0 else 0.0
    if "NUM_WON" in sums and "NUM_INQUIRIES" in sums:
        out["WIN_RATE"] = (
            sums["NUM_WON"] / sums["NUM_INQUIRIES"] if sums["NUM_INQUIRIES"] != 0 else 0.0
        )
    return out


def totals(rows: Iterable[Mapping[str, float | None]]) -> dict[str, float]:
    sums = _sum_rows(list(rows), SUM_METRICS)
    return {**sums, **_derived(sums)}


def compute_kpis(
    prior_rows: Iterable[Mapping[str, float | None]],
    current_rows: Iterable[Mapping[str, float | None]],
) -> dict[str, Delta]:
    a = totals(prior_rows)
    b = totals(current_rows)
    return {m: delta(a[m], b[m]) for m in KPI_METRICS}


@dataclass(frozen=True)
class MergedRow:
    key: str
    status: Status
    metrics: dict[
        str, Delta
    ]  # ROW_METRICS always, plus NUM_WON/NUM_INQUIRIES/NUM_LOST when present


def _side_values(sums: Mapping[str, float]) -> dict[str, float]:
    """GP, VOLUME, MARGIN, then any count metrics, in a stable order."""
    out = {"GP": sums["GP"], "VOLUME": sums["VOLUME"], "MARGIN": _derived(sums)["MARGIN"]}
    for k in _COUNT_METRICS:
        if k in sums:
            out[k] = sums[k]
    return out


def _metrics(a: Mapping[str, float], b: Mapping[str, float]) -> dict[str, Delta]:
    return {m: delta(a[m], b[m]) for m in a}


def merge_breakdowns(
    prior: Mapping[str, Mapping[str, float]], current: Mapping[str, Mapping[str, float]]
) -> list[MergedRow]:
    counts = [
        k
        for k in _COUNT_METRICS
        if any(k in row for side in (prior, current) for row in side.values())
    ]
    keys = ("GP", "VOLUME", *counts)
    merged: list[MergedRow] = []
    for key in sorted(set(prior) | set(current)):
        status: Status = (
            "both" if key in prior and key in current else ("new" if key in current else "lost")
        )
        a = _side_values(_sum_rows([prior[key]] if key in prior else [], keys))
        b = _side_values(_sum_rows([current[key]] if key in current else [], keys))
        merged.append(MergedRow(key, status, _metrics(a, b)))
    return merged


def driver(row: MergedRow) -> Driver:
    if row.status == "new":
        return "new"
    if row.status == "lost":
        return "lost"
    vol_a = row.metrics["VOLUME"].prior
    vol_b = row.metrics["VOLUME"].current
    m_a = row.metrics["MARGIN"].prior
    m_b = row.metrics["MARGIN"].current
    vol_effect = (vol_b - vol_a) * m_a if m_a != 0 else 0.0
    margin_effect = (m_b - m_a) * vol_b if vol_b != 0 else 0.0
    if vol_effect == 0 and margin_effect == 0:
        return "unchanged"
    ratio = abs(vol_effect) / (abs(vol_effect) + abs(margin_effect))
    if ratio > _VOLUME_DRIVEN_ABOVE:
        return "volume-driven"
    if ratio < _MARGIN_DRIVEN_BELOW:
        return "margin-driven"
    return "both"


@dataclass(frozen=True)
class Mover:
    row: MergedRow
    driver: Driver


@dataclass(frozen=True)
class Movers:
    increases: list[Mover]
    decreases: list[Mover]


def top_movers(merged: Sequence[MergedRow], n: int = 5) -> Movers:
    up = sorted(
        (r for r in merged if r.metrics["GP"].change > 0),
        key=lambda r: (-r.metrics["GP"].change, r.key),
    )
    down = sorted(
        (r for r in merged if r.metrics["GP"].change < 0),
        key=lambda r: (r.metrics["GP"].change, r.key),
    )
    return Movers(
        increases=[Mover(r, driver(r)) for r in up[:n]],
        decreases=[Mover(r, driver(r)) for r in down[:n]],
    )


@dataclass(frozen=True)
class NewLost:
    new: list[MergedRow]
    lost: list[MergedRow]


def new_lost(merged: Sequence[MergedRow]) -> NewLost:
    new = sorted(
        (r for r in merged if r.status == "new"),
        key=lambda r: (-abs(r.metrics["GP"].current), r.key),
    )
    lost = sorted(
        (r for r in merged if r.status == "lost"),
        key=lambda r: (-abs(r.metrics["GP"].prior), r.key),
    )
    return NewLost(new=new, lost=lost)


@dataclass(frozen=True)
class Ranked:
    rows: list[MergedRow]
    other: MergedRow | None
    footer: MergedRow


def _aggregate(key: str, rows: Sequence[MergedRow]) -> MergedRow:
    """Sum rows into one ``both`` row with the same helpers ``totals`` uses."""
    keys = ("GP", "VOLUME", *(k for k in _COUNT_METRICS if any(k in r.metrics for r in rows)))
    a = _side_values(
        _sum_rows([{k: r.metrics[k].prior for k in keys if k in r.metrics} for r in rows], keys)
    )
    b = _side_values(
        _sum_rows([{k: r.metrics[k].current for k in keys if k in r.metrics} for r in rows], keys)
    )
    return MergedRow(key, "both", _metrics(a, b))


def singular(noun: str) -> str:
    if noun.endswith("classes"):
        return noun[:-2]
    if noun.endswith("s"):
        return noun[:-1]
    return noun


def ranked(merged: Sequence[MergedRow], by: str, n: int = 10, noun: str = "customers") -> Ranked:
    if by not in ROW_METRICS:
        raise ValueError(f"by must be one of {ROW_METRICS}, got {by!r}")
    ordered = sorted(merged, key=lambda r: (-r.metrics[by].current, r.key))
    shown = ordered[:n]
    rest = ordered[n:]
    label = noun if len(rest) != 1 else singular(noun)
    other = _aggregate(f"Other ({len(rest)} {label})", rest) if rest else None
    return Ranked(rows=shown, other=other, footer=_aggregate("Total", ordered))


def share(numerator: float, denominator: float) -> float | None:
    return numerator / denominator * 100 if denominator != 0 else None


def reconciles(a: float, b: float) -> bool:
    return abs(a - b) < 0.005


_ZERO_DISPLAY = re.compile(r"[+-]?0(?:[.,]0+)?(?:%| pts)?")


def _is_zero_display(s: str) -> bool:
    return _ZERO_DISPLAY.fullmatch(s) is not None


def fmt_value(x: float, kind: Kind) -> str:
    if kind == "margin":
        s = f"{x:.2f}"
    elif kind == "rate":
        s = f"{x * 100:.1f}%"
    else:
        s = f"{x:,.0f}"
    return s.lstrip("-") if _is_zero_display(s) else s


def fmt_change(x: float, kind: Kind) -> str:
    if x == 0:
        return "flat"
    if kind == "margin":
        s = f"{x:+.2f}"
    elif kind == "rate":
        s = f"{x * 100:+.1f} pts"
    else:
        s = f"{x:+,.0f}"
    return "flat" if _is_zero_display(s) else s


def fmt_pct(pct: float | None, change: float) -> str:
    if change == 0:
        return "flat"
    if pct is None:
        return "new"
    s = f"{pct:+.1f}%"
    return "flat" if _is_zero_display(s) else s


def display(d: Delta, kind: Kind) -> dict[str, str]:
    return {
        "prior": fmt_value(d.prior, kind),
        "current": fmt_value(d.current, kind),
        "change": fmt_change(d.change, kind),
        "pct": fmt_pct(d.pct_change, d.change),
    }
