"""Arguments of ``reporting.monthly_performance``, an internal skill.

The router never chooses this skill (``SKILL_META["internal"]``); the report
flow dispatches it with arguments it built from chips. That is why
``office_value`` is only checked for presence here and validated against the
certified office values inside ``run``: the model never authors it, the flow
does, and the certified list is a data lookup that belongs inside the
snapshot with every other query of the run.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


class Args(BaseModel):
    perspective: Literal["customer", "supplier", "book"]
    office_value: str | None = None
    month: date
    window_kind: Literal["complete", "mtd"] = "complete"
    basis: Literal["prior_month"] = "prior_month"

    @field_validator("month")
    @classmethod
    def _first_of_month(cls, value: date) -> date:
        if value.day != 1:
            raise ValueError("month must be the first day of a month")
        return value

    @model_validator(mode="after")
    def _office_matches_perspective(self) -> "Args":
        if self.perspective == "book":
            if self.office_value is not None:
                raise ValueError("office_value must be omitted for a whole-book report")
        elif not self.office_value:
            raise ValueError(f"office_value is required for a {self.perspective} report")
        return self


SKILL_META = {
    "description": (
        "Internal: builds the monthly performance report for an office or the whole book. "
        "Dispatched by the report flow, never chosen by the router."
    ),
    "examples": [],
    "internal": True,
}
