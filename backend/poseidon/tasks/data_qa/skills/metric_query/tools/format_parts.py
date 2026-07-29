"""Query result -> typed parts + a certified proof block.

``format_parts`` is the only place this skill decides HOW an answer looks:
which of the three ``data_qa.metric_query`` shapes to render (breakdown
table, metric/value table, or side-by-side comparison grid), the certified
display name and unit for each metric (from
``poseidon.core.ontology.loader.get_ontology()``), the rounding rule, and the
proof block that lets a user check the answer without trusting prose (doc 02
section 6a). Nothing here queries anything - the caller (``skill.run``) has
already fetched ``result``/``compare_result`` through ``ctx.data``; this
module only shapes what it is handed.

Shape selection
----------------
- ``spec`` a :class:`~poseidon.core.data.specs.BreakdownQuerySpec` -> a
  ``table`` part: the group-by dimension's friendly name plus one column per
  requested metric, one row per breakdown key.
- ``spec`` a :class:`~poseidon.core.data.specs.MetricQuerySpec`, no
  ``compare_result`` -> a ``table`` part with exactly two columns,
  ``Metric``/``Value``.
- ``spec`` a ``MetricQuerySpec`` WITH ``compare_result`` -> one
  ``metric_grid`` part: two period descriptors (``"a"``/``"b"``) and one
  entry per metric carrying both periods' values side by side.

No data ever gets a hallucinated narrative (doc 02 section 6a): a
``BreakdownQuerySpec`` with zero rows, a ``MetricQuerySpec`` whose every
value is ``None``, or a comparison whose BOTH periods are entirely ``None``
all render as a single ``text`` part ("No data for this selection.") with a
proof block ending in ``"Result: empty"`` - the literal docs 02 section 6a
and 06 section 4 both name for this case, never the ``"Metrics: N values"``
line a real answer ends with, never the ``"Rows: N"`` line a non-empty
breakdown ends with, and never a table of blanks that could be mistaken for
real zeros. A comparison where only ONE side is empty is NOT "no data": it
renders as a normal metric_grid with ``None`` on the empty side, because the
other side is a real answer that must not be hidden.

Rounding
--------
Every certified metric renders to 0 decimal places EXCEPT ``MARGIN`` and
``WIN_RATE`` (2dp) - see :func:`_round`. ``round(x)`` with no second argument
returns a Python ``int``, which is what keeps a whole-number cell from
rendering as "1234.0" in a table row; ``None`` (SQL ``NULL`` - "no rows", not
"zero") always passes through unchanged.

Friendly names and units
-------------------------
For a ``kind == "sum"`` metric, the display name and unit come from its own
FIRST ``depends_on`` column's ``friendly``/``unit`` where that column exists
on the entity (``VOLUME`` -> ``FIXED_TONS`` -> "Volume"/"tons", ``GP`` ->
``GROSS_PROFIT`` -> "Gross Profit"/"USD") - "sum" is the one ``kind`` where
the summed column really IS the metric, so borrowing its name is correct
rather than incidental. Every other metric - ``ratio``, ``derived``, any
future ``kind`` this module has never been taught about, or a "sum" metric
with no usable ``depends_on`` column - falls back to its OWN name,
title-cased, with no unit: an unknown future metric must never silently wear
a column's name that does not actually describe it. This is the CORRECTNESS
rule - see :func:`_round`'s sibling, :func:`_friendly_and_unit`.

The example has to be HYPOTHETICAL - a metric certified tomorrow, say
``SOME_FUTURE_RATIO`` -> "Some Future Ratio" - because no metric in today's
ontology actually reaches this branch: every certified metric is either
``kind == "sum"`` with a usable column (so the rule above names it) or
listed in ``_DISPLAY_OVERRIDES`` below. Naming a real metric here would
describe a path that metric does not take.

``_DISPLAY_OVERRIDES`` is a small, presentation-only exception list checked
BEFORE the rule above, for metrics whose title-cased own name would still be
wrong or ambiguous. ``NUM_LOST`` is the reason this map exists: it is
``kind: derived``, and its first ``depends_on`` column is ``"#_INQUIRIES"``
- the SAME column ``NUM_INQUIRIES`` depends on first - so without an
override, a table requesting both metrics would print two different numbers
under the identical header "# Inquiries" (a real collision, caught in
review, not a hypothetical one). ``MARGIN``/``WIN_RATE`` are listed for the
same reason - to pin their display text explicitly rather than let it rest
on coincidentally matching the generic title-cased fallback, even though
today it does.
"""

from collections.abc import Mapping

from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult, MetricResult
from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.ontology.loader import get_ontology
from poseidon.core.skills.result import metric_grid_part, table_part, text_part

# 2dp metrics: hardcoded because the rounding rule is about MAGNITUDE (a
# ratio's natural range is sub-100, where whole-number rounding would flatten
# distinct values together), which has nothing to do with `kind` or
# `depends_on` - unlike the friendly-name rule below, there is no more
# general signal in the ontology to derive this from.
_RATIO_METRICS = frozenset({"MARGIN", "WIN_RATE"})

# A metric's own name, title-cased, is a perfectly good display label for
# anything the `kind == "sum"` rule in _friendly_and_unit does not cover.
# This map exists only to override that generic fallback for a handful of
# metrics where it would be wrong or ambiguous - see the module docstring's
# "Friendly names and units" section for why each entry is here. It is
# PRESENTATION-ONLY: the correctness rule that keeps an unlisted future
# ratio/derived metric from ever borrowing a misleading column name is the
# `kind == "sum"` gate in _friendly_and_unit, not this map.
_DISPLAY_OVERRIDES = {"MARGIN": "Margin", "WIN_RATE": "Win Rate", "NUM_LOST": "# Lost"}

NO_DATA_TEXT = "No data for this selection."

# The certified empty-result proof literal (docs 02 section 6a, 06 section 4).
# Deliberately NOT "Rows: 0": a count of zero reads as one possible outcome of
# a successful query, while "Result: empty" is the docs' own name for the case
# the whole no-narrative rule hangs off. Non-empty results still end in
# "Rows: N" (breakdowns) or "Metrics: N values" (metric shapes).
EMPTY_RESULT_PROOF = "Result: empty"


def _round(metric: str, value: float | None) -> float | int | None:
    """0dp for every metric except MARGIN/WIN_RATE (2dp); None passes
    through (SQL NULL means "no rows", never "zero")."""
    if value is None:
        return None
    if metric in _RATIO_METRICS:
        return round(value, 2)
    return round(value)


def _friendly_and_unit(entity_name: str, metric: str) -> tuple[str, str | None]:
    """The metric's display name and unit - see the module docstring."""
    if metric in _DISPLAY_OVERRIDES:
        return _DISPLAY_OVERRIDES[metric], None
    entity = get_ontology().entity(entity_name)
    metric_spec = entity.metrics[metric]
    if metric_spec.kind == "sum" and metric_spec.depends_on:
        first = metric_spec.depends_on[0]
        if first in entity.columns:
            column = entity.columns[first]
            return column.friendly, column.unit
    return metric.replace("_", " ").title(), None


def _dimension_friendly(entity_name: str, column: str) -> str:
    return get_ontology().entity(entity_name).columns[column].friendly


def _filters_proof(filters: Mapping[str, tuple[str, ...]]) -> str:
    if not filters:
        return "Filters: none"
    clauses = (f"{column} IN ({', '.join(values)})" for column, values in filters.items())
    return "Filters: " + " AND ".join(clauses)


def _header_proof(
    spec: MetricQuerySpec | BreakdownQuerySpec,
    settings: Settings,
    compare_period: PeriodWindow | None,
) -> list[str]:
    """The proof lines every shape shares: entity, backend, period(s) and
    filters, plus (breakdown only) the group-by line."""
    entity = get_ontology().entity(spec.entity)
    proof = [
        f"Entity: {entity.fqn}",
        f"Backend: {settings.data_backend}",
        f"Period: {spec.period.start}..{spec.period.end}",
    ]
    if compare_period is not None:
        proof.append(f"Compare: {compare_period.start}..{compare_period.end}")
    proof.append(_filters_proof(spec.filters))
    if isinstance(spec, BreakdownQuerySpec):
        proof.append(f"Group by: {spec.group_by} (top {spec.top_n})")
    return proof


def _is_empty(result: MetricResult) -> bool:
    return all(value is None for value in result.values.values())


def _breakdown_parts(
    spec: BreakdownQuerySpec, result: BreakdownResult, proof: list[str]
) -> tuple[list[dict], list[str]]:
    if not result.rows:
        return [text_part(NO_DATA_TEXT)], [*proof, EMPTY_RESULT_PROOF]

    columns = [_dimension_friendly(spec.entity, spec.group_by)]
    columns += [_friendly_and_unit(spec.entity, m)[0] for m in spec.metrics]
    rows = [[row.key, *(_round(m, row.values[m]) for m in spec.metrics)] for row in result.rows]
    proof.append(f"Rows: {len(result.rows)}")
    return [table_part(columns=columns, rows=rows)], proof


def _metric_grid_parts(
    spec: MetricQuerySpec,
    result: MetricResult,
    compare_period: PeriodWindow,
    compare_result: MetricResult,
    proof: list[str],
) -> tuple[list[dict], list[str]]:
    proof.append(f"Metrics: {len(spec.metrics)} values")
    periods = {
        "a": {"start": spec.period.start.isoformat(), "end": spec.period.end.isoformat()},
        "b": {"start": compare_period.start.isoformat(), "end": compare_period.end.isoformat()},
    }
    metrics = []
    for m in spec.metrics:
        friendly, unit = _friendly_and_unit(spec.entity, m)
        metrics.append(
            {
                "name": m,
                "friendly": friendly,
                "a": _round(m, result.values[m]),
                "b": _round(m, compare_result.values[m]),
                "unit": unit,
            }
        )
    return [metric_grid_part(periods=periods, metrics=metrics)], proof


def _metric_table_parts(
    spec: MetricQuerySpec, result: MetricResult, proof: list[str]
) -> tuple[list[dict], list[str]]:
    proof.append(f"Metrics: {len(spec.metrics)} values")
    rows = [
        [_friendly_and_unit(spec.entity, m)[0], _round(m, result.values[m])] for m in spec.metrics
    ]
    return [table_part(columns=["Metric", "Value"], rows=rows)], proof


def format_parts(
    spec: MetricQuerySpec | BreakdownQuerySpec,
    result: MetricResult | BreakdownResult,
    settings: Settings,
    *,
    compare_period: PeriodWindow | None = None,
    compare_result: MetricResult | None = None,
) -> tuple[list[dict], list[str]]:
    """Shape one already-fetched result (plus its optional compare twin)
    into ``(parts, proof)`` - see the module docstring for the shape rules.
    """
    proof = _header_proof(spec, settings, compare_period)

    if isinstance(spec, BreakdownQuerySpec):
        return _breakdown_parts(spec, result, proof)

    if _is_empty(result) and (compare_result is None or _is_empty(compare_result)):
        return [text_part(NO_DATA_TEXT)], [*proof, EMPTY_RESULT_PROOF]

    if compare_result is not None:
        if compare_period is None:
            # skill.run always passes the two together; a caller that breaks
            # that pairing gets a loud, python -O-proof failure, not a
            # stripped-away assert followed by a confusing crash below.
            raise ValueError("compare_period must be given whenever compare_result is given")
        return _metric_grid_parts(spec, result, compare_period, compare_result, proof)

    return _metric_table_parts(spec, result, proof)
