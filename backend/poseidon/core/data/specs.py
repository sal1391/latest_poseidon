"""Frozen specs for the deterministic query builder (see ``query_builder.py``).

The LLM never authors SQL: skills parse a user question into one of these
dataclasses — naming only certified entities/metrics/dimensions from
``poseidon.core.ontology.loader.get_ontology()`` — and ``query_builder.py``
renders the spec into a parameterized SQL string. See
``docs/architecture/04-data-ontology.md`` §3 for the end-to-end contract.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class PeriodWindow:
    start: date  # inclusive
    end: date  # exclusive (half-open — renders as >= start AND < end)

    def __post_init__(self) -> None:
        """Reject an inverted or empty window at construction time.

        ``start >= end`` can only ever select zero rows, so it is a caller
        mistake, not a query: rendering it would silently return "no data"
        for what is really a bug. ``start == end`` is included — a half-open
        window whose bounds coincide is empty by definition. The message
        text is pinned in ``test_query_builder_snapshots.py``.

        Note the asymmetry with :class:`~poseidon.core.data.client.
        PeriodRange`, whose ``end`` is INCLUSIVE (the newest date present):
        feeding a ``PeriodRange`` straight into a ``PeriodWindow`` silently
        drops the last day, and a single-day range would raise here.
        """
        if self.start >= self.end:
            raise ValueError(
                f"period window start {self.start.isoformat()} must be "
                f"before end {self.end.isoformat()}"
            )


@dataclass(frozen=True)
class MetricQuerySpec:
    entity: str
    metrics: tuple[str, ...]  # certified metric names
    period: PeriodWindow
    filters: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )  # dim col -> values (OR within col, AND across)


@dataclass(frozen=True)
class BreakdownQuerySpec:
    entity: str
    metrics: tuple[str, ...]
    period: PeriodWindow
    group_by: str  # dimension column
    order_by_metric: str  # must be in metrics
    top_n: int = 5
    filters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
