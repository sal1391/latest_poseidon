"""The router-facing contract of ``data_qa.metric_query``.

``Args`` is the only thing the model is allowed to author. Everything the
answer is made of — the SQL, the metric formulas, the rounding, the proof
block — is deterministic Python downstream of this model, so the JSON Schema
generated here is exactly the surface area of the LLM's influence over the
data layer.
"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from poseidon.core.data.query_builder import TIME_BUCKETS
from poseidon.tasks._shared.fragments import DimFilter, PeriodArg


class Args(BaseModel):
    """Ask a metric question over a certified entity."""

    entity: Literal[
        "MARINE_SALES_PLANNING_V", "W_MARINE_GL_SOURCE_AI"
    ] = "MARINE_SALES_PLANNING_V"
    metrics: list[str] = Field(min_length=1, description="Certified metric names, e.g. GP, VOLUME")
    period: PeriodArg
    compare_period: PeriodArg | None = Field(
        default=None,
        description=(
            "Second window for side-by-side comparison. With a single-column group_by, both "
            "windows run and the result is one comparison table per metric."
        ),
    )
    filters: list[DimFilter] = Field(default_factory=list)
    group_by: str | list[str] | None = Field(
        default=None,
        description=(
            "One to three certified dimension columns to break down by (e.g. CUST_NM, LOC_NM), "
            "and/or ONE time bucket MONTH, QUARTER or YEAR. Examples: [\"MONTH\"] for a monthly "
            "series, [\"LOC_NM\"] by port, [\"LOC_NM\", \"MONTH\"] by port by month."
        ),
    )
    order_by: str | None = Field(
        default=None,
        description="Metric to rank a breakdown by; defaults to the first requested metric.",
    )
    order: Literal["desc", "asc"] = Field(
        default="desc",
        description='"asc" for worst/lowest first, "desc" (default) for best/highest.',
    )
    top_n: int = Field(default=5, ge=1, le=50, description="Row limit for breakdowns, 1-50.")
    scope: Literal["office", "book"] = Field(
        default="office",
        description=(
            'Office scope. "office" (default) applies the conversation\'s carried office filter '
            'when one is set; "book" runs over the whole book: use it for other offices or '
            "locations, the full account, or a share of the total."
        ),
    )
    share_of_total: list[str] = Field(
        default_factory=list,
        description=(
            "Share of total: filter columns to REMOVE for the denominator (the carried office "
            "column counts as a filter). Returns Selection, Total and Share % per metric. "
            "Cannot combine with group_by or compare_period."
        ),
    )

    @field_validator("group_by", mode="after")
    @classmethod
    def _normalise_group_by(cls, value: str | list[str] | None) -> list[str] | None:
        if value is None:
            return None
        columns = [value] if isinstance(value, str) else list(value)
        if not 1 <= len(columns) <= 3:
            raise ValueError("group_by takes one to three columns")
        if len(set(columns)) != len(columns):
            raise ValueError(f"group_by repeats a column: {columns}")
        if sum(col in TIME_BUCKETS for col in columns) > 1:
            raise ValueError("group_by takes at most one time bucket (MONTH, QUARTER or YEAR)")
        return columns

    @model_validator(mode="after")
    def _check_combinations(self) -> "Args":
        if self.order_by is not None and self.order_by not in self.metrics:
            raise ValueError(
                f"order_by {self.order_by!r} must be one of the requested metrics {self.metrics}"
            )
        if self.share_of_total and (self.group_by is not None or self.compare_period is not None):
            raise ValueError("share_of_total cannot combine with group_by or compare_period")
        return self


SKILL_META = {
    "description": (
        "Query certified metrics (GP, VOLUME, MARGIN, NUM_WON, NUM_INQUIRIES, "
        "NUM_LOST, WIN_RATE) over sales or GL data: totals, period comparisons, "
        "or top-N breakdowns by a dimension."
    ),
    "examples": [
        "Top GP customers for Port of Singapore in April 2026",
        "Total volume prior year vs YTD",
    ],
}
