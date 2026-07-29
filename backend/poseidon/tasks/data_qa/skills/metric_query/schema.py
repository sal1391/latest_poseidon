"""The router-facing contract of ``data_qa.metric_query``.

``Args`` is the only thing the model is allowed to author. Everything the
answer is made of — the SQL, the metric formulas, the rounding, the proof
block — is deterministic Python downstream of this model, so the JSON Schema
generated here is exactly the surface area of the LLM's influence over the
data layer.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
            "Second window for side-by-side comparison. Cannot be combined with group_by."
        ),
    )
    filters: list[DimFilter] = Field(default_factory=list)
    group_by: str | None = Field(
        default=None,
        description=(
            "Certified dimension column to break down by (e.g. CUST_NM, LOC_NM). "
            "Cannot be combined with compare_period."
        ),
    )
    top_n: int = Field(default=5, ge=1, le=50, description="Row limit for breakdowns, 1-50.")

    @model_validator(mode="after")
    def _reject_breakdown_with_compare(self) -> "Args":
        """A breakdown and a period comparison are two different answer
        shapes (``tools/format_parts.py`` renders a breakdown table OR a
        comparison ``metric_grid``, never both), so ``skill.run`` has no
        rendering for both together. Rejecting the combination here, as a
        structured 422 at argument validation, is what lets the router see
        the mistake and correct itself - the alternative (silently running
        the breakdown and dropping ``compare_period``) would answer a
        different question than the one asked, without saying so.
        """
        if self.group_by is not None and self.compare_period is not None:
            raise ValueError(
                "compare_period is not supported with group_by — run the "
                "breakdown once per period instead"
            )
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
