"""A frame (two or more group-by columns, or a time bucket) -> one ordered,
capped ``table`` part plus proof. Plan rulings 1, 4, 5, 6 of the PR B plan.

Cap rule (owner ruling 5): ``top_n`` ranks the NON-time keys by ``order_by``
summed over the whole period; every bucket for those keys is shown,
chronologically (ISO keys sort chronologically). No zero-filling: a key
with no rows in a bucket has no row for it. Without a bucket the rows are
the ranked keys themselves.

Ranking value. For an additive metric a key's value is the plain sum of
``order_by`` over its rows; a NULL metric value ranks as 0. A RATIO metric
(MARGIN) does not sum: on a bucketed frame a key's value is the period
ratio recomputed from its rows, ``sum(GP) / sum(VOLUME)`` (the ontology's
MARGIN rule), so a port with more active months cannot outrank a port with
the better margin. ``skill.py`` adds GP and VOLUME to the query for this and
passes the requested metrics as ``display_metrics``, so they render only
when asked for. A key whose VOLUME total is 0 or NULL has no ratio and
ranks LAST under either order, after every ranked key; ties break by key.
The proof line reads ``Order: MARGIN desc (period ratio)``. An unbucketed
frame has one row per key, so its own MARGIN is already the period ratio.
WIN_RATE never reaches this module (``skill.run`` refuses it on the frame
path).
"""

from collections.abc import Callable
from datetime import date

from poseidon.core.config import Settings
from poseidon.core.data.client import FrameResult, FrameRow
from poseidon.core.data.query_builder import TIME_BUCKETS
from poseidon.core.data.specs import FrameQuerySpec
from poseidon.core.skills.result import table_part, text_part

from .format_parts import (
    _RATIO_METRICS,
    EMPTY_RESULT_PROOF,
    NO_DATA_TEXT,
    _dimension_friendly,
    _friendly_and_unit,
    _header_proof,
    _order_proof,
    _round,
)

BUCKET_HEADERS = {"MONTH": "Month", "QUARTER": "Quarter", "YEAR": "Year"}

# A ratio metric's (numerator, denominator) for the period-ratio ranking.
# MARGIN is the only ratio the frame path admits (WIN_RATE is refused).
RATIO_COMPONENTS = {"MARGIN": ("GP", "VOLUME")}


def ranks_by_period_ratio(group_by: tuple[str, ...], order_by: str) -> bool:
    """True when a frame ranks its keys by the recomputed period ratio: a
    time bucket is present and ``order_by`` is a ratio metric."""
    return any(c in TIME_BUCKETS for c in group_by) and order_by in _RATIO_METRICS


def bucket_label(bucket: str, iso: str) -> str:
    """An ISO bucket-start date as the table shows it: ``Jan 2025``,
    ``Q2 2025``, ``2025``."""
    d = date.fromisoformat(iso)
    if bucket == "MONTH":
        return d.strftime("%b %Y")
    if bucket == "QUARTER":
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    return str(d.year)


def _value(values: dict[str, float | None], metric: str) -> float:
    """A metric value for ranking: SQL NULL ranks as zero."""
    v = values.get(metric)
    return 0.0 if v is None else float(v)


def frame_breakdown_parts(
    spec: FrameQuerySpec,
    result: FrameResult,
    settings: Settings,
    *,
    order_by: str,
    order: str,
    top_n: int,
    display_metrics: tuple[str, ...] | None = None,
) -> tuple[list[dict], list[str]]:
    """Shape a fetched frame into ``(parts, proof)`` - see the module docstring.

    ``display_metrics`` (default ``spec.metrics``) are the columns the table
    shows; ``spec.metrics`` may carry more, fetched only for ranking."""
    shown = spec.metrics if display_metrics is None else display_metrics
    proof = _header_proof(spec, settings, None)
    columns_in = list(spec.group_by)
    bucket = next((c for c in columns_in if c in TIME_BUCKETS), None)
    key_cols = [c for c in columns_in if c not in TIME_BUCKETS]
    key_idx = [columns_in.index(c) for c in key_cols]
    bucket_idx = columns_in.index(bucket) if bucket is not None else None

    cap_text = f"top {top_n} keys; every bucket" if bucket is not None else f"top {top_n}"
    proof.append(f"Group by: {', '.join(columns_in)} ({cap_text})")
    ratio = ranks_by_period_ratio(tuple(columns_in), order_by)
    if ratio:
        proof.append(f"Order: {order_by} {order} (period ratio)")
    else:
        proof.extend(_order_proof(order_by, order, spec.metrics[0], force=True))

    if not result.rows:
        return [text_part(NO_DATA_TEXT)], [*proof, EMPTY_RESULT_PROOF]

    def key_of(row: FrameRow) -> tuple[str, ...]:
        return tuple(row.keys[i] for i in key_idx)

    totals: dict[tuple[str, ...], float | None]
    if ratio:
        totals = _period_ratios(result.rows, key_of, *RATIO_COMPONENTS[order_by])
    else:
        totals = {}
        for row in result.rows:
            key = key_of(row)
            totals[key] = (totals.get(key) or 0.0) + _value(row.values, order_by)
    sign = -1.0 if order == "desc" else 1.0

    def rank_key(k: tuple[str, ...]) -> tuple[int, float, tuple[str, ...]]:
        value = totals[k]
        # An unrankable key (no ratio) sorts after every ranked key.
        return (1, 0.0, k) if value is None else (0, sign * value, k)

    ranked = sorted(totals, key=rank_key)[:top_n]
    rank = {k: i for i, k in enumerate(ranked)}

    def sort_key(row: FrameRow) -> tuple[str, int]:
        bucket_key = row.keys[bucket_idx] if bucket_idx is not None else ""
        return (bucket_key, rank[key_of(row)])

    kept = sorted((r for r in result.rows if key_of(r) in rank), key=sort_key)

    headers: list[str] = []
    if bucket is not None:
        headers.append(BUCKET_HEADERS[bucket])
    headers += [_dimension_friendly(spec.entity, c) for c in key_cols]
    headers += [_friendly_and_unit(spec.entity, m)[0] for m in shown]

    rows = []
    for r in kept:
        cells: list = []
        if bucket is not None and bucket_idx is not None:
            cells.append(bucket_label(bucket, r.keys[bucket_idx]))
        cells += [r.keys[i] for i in key_idx]
        cells += [_round(m, r.values.get(m)) for m in shown]
        rows.append(cells)

    proof.append(f"Rows: {len(rows)}")
    return [table_part(columns=headers, rows=rows)], proof


def _period_ratios(
    rows: list[FrameRow],
    key_of: Callable[[FrameRow], tuple[str, ...]],
    numerator: str,
    denominator: str,
) -> dict[tuple[str, ...], float | None]:
    """Each key's ``sum(numerator) / sum(denominator)`` over its rows; None
    when the denominator total is 0 (a NULL counts as 0, as under the
    ontology's ``NULLIF`` guard)."""
    num: dict[tuple[str, ...], float] = {}
    den: dict[tuple[str, ...], float] = {}
    for row in rows:
        key = key_of(row)
        num[key] = num.get(key, 0.0) + _value(row.values, numerator)
        den[key] = den.get(key, 0.0) + _value(row.values, denominator)
    return {k: (num[k] / den[k] if den[k] else None) for k in num}
