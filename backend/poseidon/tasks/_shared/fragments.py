"""Argument fragments every skill reuses instead of re-describing.

These are the pieces of a skill's ``Args`` that more than one skill needs: a
period, a dimension filter. Sharing the classes (rather than copying two
fields) keeps the generated JSON Schema - field names, types, and above all
the description text - byte-identical across skills. Two skills that
described "a period" in two ways would teach the model two different habits,
and one of them would be wrong.

Which is why the class docstrings below are written for the router, not for
us: pydantic copies them verbatim into ``model_json_schema()``, so they are
prompt tokens on every turn. Notes for maintainers go in comments.

Cross-references, kept here rather than in the schema text:

- :class:`PeriodArg` matches :class:`~poseidon.core.data.specs.PeriodWindow`
  (half-open, and the SQL it renders is ``>= start AND < end``), so the two
  never have to be reconciled. It does NOT match
  :class:`~poseidon.core.data.client.PeriodRange`, whose ``end`` is the
  inclusive newest date present.
- :class:`DimFilter`'s OR-within/AND-across semantics are the spec layer's;
  this fragment only carries the values.
"""

from datetime import date

from pydantic import BaseModel, Field, model_validator

from poseidon.core.data.specs import PeriodWindow


class PeriodArg(BaseModel):
    """A date window. Start is included, end is excluded: April 2026 is
    start 2026-04-01, end 2026-05-01."""

    start: date
    end: date

    @model_validator(mode="after")
    def _reject_empty_window(self) -> "PeriodArg":
        """Reject ``start >= end`` here rather than downstream.

        The rule and its wording live in ``PeriodWindow``; constructing one
        is how this fragment borrows both instead of duplicating them. Doing
        it during argument validation is what turns an inverted window into a
        structured 422 from the dispatcher, rather than an exception raised
        halfway through a skill.
        """
        PeriodWindow(self.start, self.end)
        return self


class DimFilter(BaseModel):
    """Restrict one dimension column to a set of values. Values are OR-ed
    within a column and AND-ed across columns; at least one is required."""

    column: str
    values: list[str] = Field(min_length=1)
