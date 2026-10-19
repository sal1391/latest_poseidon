"""Share of total in the C04 order (reports codex review C04; spec 8.2).

``skill.run`` builds the SELECTION spec through its normal path, which is
where the carried office is stamped. THIS module then derives the TOTAL
spec by REMOVING the named columns from that already-effective filter set
and never restamps -- so the office can only ever sit on the numerator.
D16 row scope rides on ``scope_value`` of the selection spec and is copied
unchanged onto the total.
"""

from dataclasses import replace

from poseidon.core.analytics import mom
from poseidon.core.config import Settings
from poseidon.core.data.client import MetricResult
from poseidon.core.data.query_builder import SpecValidationError
from poseidon.core.data.specs import MetricQuerySpec
from poseidon.core.skills.result import table_part, text_part

from .format_parts import (
    EMPTY_RESULT_PROOF,
    NO_DATA_TEXT,
    _filters_proof,
    _friendly_and_unit,
    _header_proof,
    _is_empty,
    _round,
)

_FILTERS_PREFIX = "Filters: "


def denominator_spec(selection: MetricQuerySpec, removed: list[str]) -> MetricQuerySpec:
    """The TOTAL spec: ``selection`` minus the ``removed`` filter columns.

    Every removed column must be among the selection's EFFECTIVE filters
    (the model's filters plus any carried-office stamp); otherwise the share
    would silently equal 100%, so it is a ``SpecValidationError`` (422).
    """
    present = sorted(selection.filters)
    for column in removed:
        if column not in selection.filters:
            raise SpecValidationError(
                f"share_of_total column {column!r} is not among the effective filters {present}"
            )
    kept = {col: values for col, values in selection.filters.items() if col not in removed}
    return replace(selection, filters=kept)


def share_parts(
    selection: MetricQuerySpec,
    sel_result: MetricResult,
    total: MetricQuerySpec,
    tot_result: MetricResult,
    settings: Settings,
    removed: list[str],
) -> tuple[list[dict], list[str]]:
    """One ``Metric | Selection | Total | Share %`` table plus proof."""
    proof = _header_proof(selection, settings, None)
    # The header's own last line is "Filters: ..."; name both filter sets instead.
    proof[-1] = "Selection filters: " + _filters_proof(selection.filters)[len(_FILTERS_PREFIX) :]
    proof.append("Total filters: " + _filters_proof(total.filters)[len(_FILTERS_PREFIX) :])
    proof.append(f"Share of total: removed {', '.join(removed)}")
    if _is_empty(sel_result) and _is_empty(tot_result):
        return [text_part(NO_DATA_TEXT)], [*proof, EMPTY_RESULT_PROOF]

    rows = []
    for m in selection.metrics:
        sel = sel_result.values.get(m)
        tot = tot_result.values.get(m)
        share = mom.share(sel or 0.0, tot or 0.0)
        rows.append(
            [
                _friendly_and_unit(selection.entity, m)[0],
                _round(m, sel),
                _round(m, tot),
                "n/a" if share is None else round(share, 1),
            ]
        )
    proof.append(f"Metrics: {len(rows)} values")
    return [table_part(columns=["Metric", "Selection", "Total", "Share %"], rows=rows)], proof
