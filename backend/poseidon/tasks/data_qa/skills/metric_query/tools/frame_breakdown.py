"""A frame (two or more group-by columns, or a time bucket) -> one ordered,
capped ``table`` part plus proof. Plan rulings 1, 4, 5, 6 of the PR B plan.

Cap rule (owner ruling 5): ``top_n`` ranks the NON-time keys by ``order_by``
summed over the whole period; every bucket for those keys is shown,
chronologically (ISO keys sort chronologically). No zero-filling: a key
with no rows in a bucket has no row for it. Without a bucket the rows are
the ranked keys themselves.
"""

from datetime import date

from poseidon.core.config import Settings
from poseidon.core.data.client import FrameResult, FrameRow
from poseidon.core.data.query_builder import TIME_BUCKETS
from poseidon.core.data.specs import FrameQuerySpec
from poseidon.core.skills.result import table_part, text_part

from .format_parts import (
    EMPTY_RESULT_PROOF,
    NO_DATA_TEXT,
    _dimension_friendly,
    _friendly_and_unit,
    _header_proof,
    _order_proof,
    _round,
)

BUCKET_HEADERS = {"MONTH": "Month", "QUARTER": "Quarter", "YEAR": "Year"}


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
) -> tuple[list[dict], list[str]]:
    """Shape a fetched frame into ``(parts, proof)`` - see the module docstring."""
    proof = _header_proof(spec, settings, None)
    columns_in = list(spec.group_by)
    bucket = next((c for c in columns_in if c in TIME_BUCKETS), None)
    key_cols = [c for c in columns_in if c not in TIME_BUCKETS]
    key_idx = [columns_in.index(c) for c in key_cols]
    bucket_idx = columns_in.index(bucket) if bucket is not None else None

    cap_text = f"top {top_n} keys; every bucket" if bucket is not None else f"top {top_n}"
    proof.append(f"Group by: {', '.join(columns_in)} ({cap_text})")
    proof.extend(_order_proof(order_by, order, spec.metrics[0], force=True))

    if not result.rows:
        return [text_part(NO_DATA_TEXT)], [*proof, EMPTY_RESULT_PROOF]

    def key_of(row: FrameRow) -> tuple[str, ...]:
        return tuple(row.keys[i] for i in key_idx)

    totals: dict[tuple[str, ...], float] = {}
    for row in result.rows:
        key = key_of(row)
        totals[key] = totals.get(key, 0.0) + _value(row.values, order_by)
    sign = -1.0 if order == "desc" else 1.0
    ranked = sorted(totals, key=lambda k: (sign * totals[k], k))[:top_n]
    rank = {k: i for i, k in enumerate(ranked)}

    def sort_key(row: FrameRow) -> tuple[str, int]:
        bucket_key = row.keys[bucket_idx] if bucket_idx is not None else ""
        return (bucket_key, rank[key_of(row)])

    kept = sorted((r for r in result.rows if key_of(r) in rank), key=sort_key)

    headers: list[str] = []
    if bucket is not None:
        headers.append(BUCKET_HEADERS[bucket])
    headers += [_dimension_friendly(spec.entity, c) for c in key_cols]
    headers += [_friendly_and_unit(spec.entity, m)[0] for m in spec.metrics]

    rows = []
    for r in kept:
        cells: list = []
        if bucket is not None and bucket_idx is not None:
            cells.append(bucket_label(bucket, r.keys[bucket_idx]))
        cells += [r.keys[i] for i in key_idx]
        cells += [_round(m, r.values.get(m)) for m in spec.metrics]
        rows.append(cells)

    proof.append(f"Rows: {len(rows)}")
    return [table_part(columns=headers, rows=rows)], proof
