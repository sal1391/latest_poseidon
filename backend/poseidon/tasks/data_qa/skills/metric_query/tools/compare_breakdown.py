"""Two breakdown windows -> one comparison table per metric.

``skill.run`` calls this when the router asks for a breakdown (``group_by``)
together with a second window (``compare_period``). Both windows have
already been fetched through ``ctx.data`` at ``WINDOW_TOP_N`` rows each; this
module only merges and shapes them. Nothing here queries anything.

Merge rule: the union of both windows' keys, a key missing from one side
counting as 0.0 on that side. Every table lists the same keys in the same
order - by the absolute change of the FIRST requested metric, descending,
then by key - capped at the caller's ``top_n``, so the first table's first
column is the row identity ``_repopulate_pass_through`` reads.

Percent change is measured against ``abs(prior)``; a zero prior has no base,
so the cell reads ``"new"`` (the same zero-base rule as ``mom.delta``).

Driver: when both GP and VOLUME are requested, every table gains a last
``Driver`` column computed by ``mom.driver`` over ``mom.merge_breakdowns`` -
the A1 rule, reused rather than reimplemented. ``merge_breakdowns`` derives
MARGIN from the summed GP and VOLUME itself.

The proof reuses ``format_parts``' header (entity, backend, both windows,
filters, group-by) but rewords its last line to ``Group by: X (top N
shown; merged over the top 50 per window)`` so the cap the table was cut to
is named, and ends with ``Rows: N (merged over the top 50 per window)``, so a
reader knows the union was taken over capped windows.
"""

from poseidon.core.analytics import mom
from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult
from poseidon.core.data.specs import BreakdownQuerySpec
from poseidon.core.skills.result import table_part, text_part

from .format_parts import (
    EMPTY_RESULT_PROOF,
    NO_DATA_TEXT,
    _dimension_friendly,
    _friendly_and_unit,
    _header_proof,
    _round,
)

# Rows fetched per window before the merge. Wider than any display cap so a
# key that moved in or out of the top rows still has both sides.
WINDOW_TOP_N = 50

_DRIVER_METRICS = frozenset({"GP", "VOLUME"})


def _by_key(result: BreakdownResult) -> dict[str, dict[str, float | None]]:
    return {row.key: row.values for row in result.rows}


def _value(side: dict[str, dict[str, float | None]], key: str, metric: str) -> float:
    value = side.get(key, {}).get(metric)
    return 0.0 if value is None else float(value)


def _pct(prior: float, change: float) -> float | str:
    if prior == 0:
        return "new"
    return round(change / abs(prior) * 100, 1)


def _drivers(
    prior: dict[str, dict[str, float | None]], current: dict[str, dict[str, float | None]]
) -> dict[str, str]:
    def side(values: dict[str, dict[str, float | None]]) -> dict[str, dict[str, float]]:
        return {key: {m: _value(values, key, m) for m in ("GP", "VOLUME")} for key in values}

    merged = mom.merge_breakdowns(side(prior), side(current))
    return {row.key: mom.driver(row) for row in merged}


def compare_breakdown_parts(
    spec_current: BreakdownQuerySpec,
    result_current: BreakdownResult,
    spec_prior: BreakdownQuerySpec,
    result_prior: BreakdownResult,
    settings: Settings,
    top_n: int,
) -> tuple[list[dict], list[str]]:
    """One table part per metric in ``spec_current.metrics`` plus the proof
    block - see the module docstring for the merge, order and Driver rules.
    """
    proof = _header_proof(spec_current, settings, spec_prior.period)
    proof[-1] = (
        f"Group by: {spec_current.group_by} (top {top_n} shown; "
        f"merged over the top {spec_current.top_n} per window)"
    )
    if not result_current.rows and not result_prior.rows:
        return [text_part(NO_DATA_TEXT)], [*proof, EMPTY_RESULT_PROOF]

    metrics = spec_current.metrics
    prior = _by_key(result_prior)
    current = _by_key(result_current)
    first = metrics[0]

    def order(key: str) -> tuple[float, str]:
        change = _value(current, key, first) - _value(prior, key, first)
        return (-abs(change), key)

    keys = sorted(set(prior) | set(current), key=order)[:top_n]
    drivers = _drivers(prior, current) if _DRIVER_METRICS <= set(metrics) else None
    dimension = _dimension_friendly(spec_current.entity, spec_current.group_by)

    parts = []
    for m in metrics:
        friendly = _friendly_and_unit(spec_current.entity, m)[0]
        columns = [dimension, f"{friendly} prior", f"{friendly} current", "Change", "%"]
        if drivers is not None:
            columns.append("Driver")
        rows = []
        for key in keys:
            a = _value(prior, key, m)
            b = _value(current, key, m)
            change = b - a
            row = [key, _round(m, a), _round(m, b), _round(m, change), _pct(a, change)]
            if drivers is not None:
                row.append(drivers[key])
            rows.append(row)
        parts.append(table_part(columns=columns, rows=rows))

    proof.append(f"Rows: {len(keys)} (merged over the top {spec_current.top_n} per window)")
    return parts, proof
