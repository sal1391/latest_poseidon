"""The ``DataClient`` adapter-seam protocol and its result dataclasses.

A concrete adapter renders a :class:`~poseidon.core.data.specs.MetricQuerySpec`
or :class:`~poseidon.core.data.specs.BreakdownQuerySpec` via ``query_builder``,
executes the parameterized SQL against one of the two dialects, and shapes the
rows into the result dataclasses below — callers (skills) only ever see typed
results, never raw rows or SQL. See ``docs/architecture/04-data-ontology.md``
§3 for the full adapter-seam contract.

This module defines only the interface. ``SyntheticDataClient`` (Postgres,
local/CI — a later task) and ``SnowflakeDataClient`` (production — a later
phase) each satisfy ``DataClient`` structurally (it is a ``Protocol``; no
explicit inheritance required).
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from .specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow


@dataclass(frozen=True)
class MetricResult:
    entity: str
    period: PeriodWindow
    values: dict[str, float | None]  # metric name -> value (None when no rows)


@dataclass(frozen=True)
class BreakdownRow:
    key: str  # dimension value (post-COALESCE)
    values: dict[str, float | None]


@dataclass(frozen=True)
class BreakdownResult:
    entity: str
    group_by: str
    rows: list[BreakdownRow]  # ordered as returned


@dataclass(frozen=True)
class PeriodRange:
    """The span of dates an entity actually holds (MIN/MAX of its date column).

    ``end`` is inclusive — the newest date present, not a half-open bound; do
    NOT feed directly into PeriodWindow.end.
    """

    start: date | None
    end: date | None  # None/None when the entity has no rows


class DataClient(Protocol):
    """The four calls a skill makes against certified data.

    ``scope_value`` on the two lookups below is D16's row scope (see
    ``query_builder.resolve_row_scope_value``, the only sanctioned way to
    produce one): ``None`` for an entity that declares no ``row_scope``,
    which is every certified entity today. The two spec-taking methods carry
    the same value on the spec itself rather than as a parameter, so a spec
    is always self-contained. Adapters pass it straight through to their
    builder; the fail-closed rules live there, once, not in each adapter.
    """

    def list_dimension_values(
        self,
        entity: str,
        column: str,
        search: str | None = None,
        scope_value: str | None = None,
    ) -> list[str]: ...

    def available_periods(self, entity: str, scope_value: str | None = None) -> PeriodRange: ...

    def run_metric_query(self, spec: MetricQuerySpec) -> MetricResult: ...

    def run_breakdown_query(self, spec: BreakdownQuerySpec) -> BreakdownResult: ...
