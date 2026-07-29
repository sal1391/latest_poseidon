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
proof block ending in ``"Rows: 0"`` - never the ``"Metrics: N values"`` line
a real answer ends with, and never a table of blanks that could be mistaken
for real zeros. A comparison where only ONE side is empty is NOT "no data":
it renders as a normal metric_grid with ``None`` on the empty side, because
the other side is a real answer that must not be hidden.

Rounding
--------
Every certified metric renders to 0 decimal places EXCEPT ``MARGIN`` and
``WIN_RATE`` (2dp) - see :func:`_round`. ``round(x)`` with no second argument
returns a Python ``int``, which is what keeps a whole-number cell from
rendering as "1234.0" in a table row; ``None`` (SQL ``NULL`` - "no rows", not
"zero") always passes through unchanged.

Friendly names and units
-------------------------
A metric's display name and unit come from its own FIRST ``depends_on``
column's ``friendly``/``unit`` where that column exists on the entity
(``VOLUME`` -> ``FIXED_TONS`` -> "Volume"/"tons", ``GP`` -> ``GROSS_PROFIT``
-> "Gross Profit"/"USD"), falling back to the bare metric name (no unit)
otherwise - the same fallback a metric with no ``depends_on`` at all would
get. ``MARGIN`` and ``WIN_RATE`` are ratios: their first ``depends_on``
column names one INPUT to the ratio, not the ratio itself (``MARGIN``
depends on ``GROSS_PROFIT`` first, and "Gross Profit" would misname a
USD/ton figure), so both are hardcoded to fall back to their own metric name
- a deliberate, documented exception to the general rule, applied the same
way every time so the choice stays deterministic.
"""

from collections.abc import Mapping

from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult, MetricResult
from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.ontology.loader import get_ontology
from poseidon.core.skills.result import metric_grid_part, table_part, text_part

# Ratios: see the module docstring's "Friendly names and units" section for
# why these two fall back to their own metric name instead of their first
# depends_on column, both here and in _friendly_and_unit.
_RATIO_METRICS = frozenset({"MARGIN", "WIN_RATE"})

NO_DATA_TEXT = "No data for this selection."


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
    if metric in _RATIO_METRICS:
        return metric, None
    entity = get_ontology().entity(entity_name)
    depends_on = entity.metrics[metric].depends_on
    if depends_on and depends_on[0] in entity.columns:
        column = entity.columns[depends_on[0]]
        return column.friendly, column.unit
    return metric, None


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
        return [text_part(NO_DATA_TEXT)], [*proof, "Rows: 0"]

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
        return [text_part(NO_DATA_TEXT)], [*proof, "Rows: 0"]

    if compare_result is not None:
        assert compare_period is not None  # skill.run always pairs the two
        return _metric_grid_parts(spec, result, compare_period, compare_result, proof)

    return _metric_table_parts(spec, result, proof)
