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
    start: date | None
    end: date | None  # None/None when the entity has no rows


class DataClient(Protocol):
    def list_dimension_values(
        self, entity: str, column: str, search: str | None = None
    ) -> list[str]: ...

    def available_periods(self, entity: str) -> PeriodRange: ...

    def run_metric_query(self, spec: MetricQuerySpec) -> MetricResult: ...

    def run_breakdown_query(self, spec: BreakdownQuerySpec) -> BreakdownResult: ...
