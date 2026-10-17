"""Arguments of ``reporting.report_lookup``, the router-visible reader of an open report."""

from typing import Literal

from pydantic import BaseModel, Field

SECTIONS = (
    "kpis",
    "entities_by_gp",
    "entities_by_volume",
    "ports_by_gp",
    "ports_by_volume",
    "deal_class",
    "movers",
    "new_lost",
    "narrative",
    "context",
)


class Args(BaseModel):
    section: Literal[
        "kpis",
        "entities_by_gp",
        "entities_by_volume",
        "ports_by_gp",
        "ports_by_volume",
        "deal_class",
        "movers",
        "new_lost",
        "narrative",
        "context",
    ]
    limit: int = Field(default=10, ge=1, le=50)
    offset: int = Field(default=0, ge=0)


SKILL_META = {
    "description": (
        "Read a section of the monthly report open in this conversation: KPIs, the five "
        "ranked tables, movers, new and lost entities, the narrative or the entity "
        "context. Use it for any question about the report's own figures. Runs no query."
    ),
    "examples": ["what were the top ports in the report", "show me the report's movers"],
}
