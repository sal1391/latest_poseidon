"""Deterministic query-building layer over the certified ontology.

The LLM never authors SQL: callers build one of the frozen specs in
``specs.py`` naming certified entities/metrics/dimensions, and
``query_builder.py`` renders the parameterized SQL for the Postgres
(``synthetic`` schema) or Snowflake dialect. See
``docs/architecture/04-data-ontology.md`` §3.
"""

from .client import FrameResult, FrameRow
from .query_builder import (
    SpecValidationError,
    build_breakdown_query,
    build_dimension_values_query,
    build_frame_query,
    build_metric_query,
    build_months_query,
    build_period_range_query,
)
from .specs import BreakdownQuerySpec, FrameQuerySpec, MetricQuerySpec, PeriodWindow

__all__ = [
    "BreakdownQuerySpec",
    "FrameQuerySpec",
    "FrameResult",
    "FrameRow",
    "MetricQuerySpec",
    "PeriodWindow",
    "SpecValidationError",
    "build_breakdown_query",
    "build_dimension_values_query",
    "build_frame_query",
    "build_metric_query",
    "build_months_query",
    "build_period_range_query",
]
