"""Typed models for the certified ontology (see ``ontology/ontology.yml``).

These mirror the vendored YAML's schema v2 shape. Presence of an entity in
the file **is** certification; only ``planned``/``retired`` entities are
stamped with an explicit ``status`` (see ``ontology/SOURCE.md`` and
``docs/architecture/04-data-ontology.md``). Every model ignores unknown
keys (`extra="ignore"`) so descriptive/provenance fields the loader doesn't
care about (``description_source``, ``verified``, ``app``, ``first_seen``,
...) don't break parsing.

Every model is also ``frozen=True``: `poseidon.core.ontology.loader.get_ontology`
hands out one cached, process-wide singleton, so top-level attribute
reassignment (e.g. ``column.friendly = "..."``) raises `pydantic.ValidationError`
instead of silently corrupting it for every caller. Freezing does not deep-freeze
the interior `list`/`dict` fields (e.g. ``Entity.business_rules.append(...)``
would still mutate the shared object) — see the caller contract documented on
`get_ontology`.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

# The COALESCE() literal for any entity whose certified rules don't name a
# different one — MARINE_SALES_PLANNING_V's own rule ("COALESCE(col,
# 'Unknown') on dimension columns before grouping.") happens to be exactly
# this, so that entity needs no override. See the loader's
# `_NULL_PLACEHOLDERS` for the entities that do.
DEFAULT_NULL_PLACEHOLDER = "Unknown"


class Column(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str  # e.g. "#_FIXTURES" (unquoted form; YAML key)
    type: str
    role: Literal["identifier", "measure", "dimension", "date"]
    friendly: str
    quoted: bool = False  # True -> render as "..." in SQL
    agg: str | None = None
    unit: str | None = None
    description: str = ""


class Metric(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str  # e.g. "MARGIN"
    sql: str  # certified expression, verbatim
    kind: str  # sum | ratio | derived
    rule: str | None = None
    depends_on: list[str] = []


class NegativeConstraint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    wrong: str
    right: str
    observed: bool = False


class RowScope(BaseModel):
    """Decision D16 (doc 05 section 4): an entity MAY declare that every
    query over it is restricted to the rows one caller is allowed to see --
    ``column`` (a dimension column of the entity) compared against one claim
    off the caller's ``poseidon.core.identity.UserContext``.

    MECHANISM WITHOUT POLICY. No entity in the certified ``ontology/
    ontology.yml`` declares this today, and this task deliberately does not
    add one: the hook ships fully wired and fail-closed so the Snowflake-side
    effort that eventually needs it flips CONFIG, not code. The pin that
    keeps that a noticed event rather than a silent drift is
    ``backend/tests/test_ontology_loader.py``'s
    ``test_no_certified_entity_declares_row_scope`` -- removing it IS the
    flip. What the builder does with a declaration lives in
    ``poseidon.core.data.query_builder`` (``resolve_row_scope_value`` and the
    symmetric fail-closed checks in all four builders).

    ``claim`` is restricted to the two ``UserContext`` fields that can
    plausibly key a per-person scope today: ``sub`` (always present,
    provider-prefixed, globally unambiguous) and ``email`` (optional, so a
    caller carrying none is refused rather than silently unscoped). Widening
    this Literal later is a one-line change; leaving it open would let an
    ontology name a field that is not a string, or not an identity at all
    (``roles`` is a tuple), and only find out at render time.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    column: str
    claim: Literal["sub", "email"]


class Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str
    fqn: str
    status: str = "certified"  # "planned" for AR_INVOICES (presence == certified otherwise)
    grain: str = ""
    date_column: str | None = None
    columns: dict[str, Column] = {}
    metrics: dict[str, Metric] = {}
    negative_constraints: list[NegativeConstraint] = []
    business_rules: list[str] = []
    hierarchy_levels: list[str] = []  # W_MARINE_GL: the 7 CLASS level_columns
    dual_purpose_exclusion: str | None = None  # "COALESCE(CLASS4,'') <> 'Volume'"
    dual_purpose_pivot_column: str | None = None  # "CLASS4" — the unit-pivot column
    dual_purpose_pivot_value: str | None = None  # "Volume" — the unit-pivot value
    # The literal every COALESCE() over this entity's dimensions must use —
    # see the loader's `_NULL_PLACEHOLDERS` for the certified rules that fix
    # it per entity. "Unknown" is the default (MARINE_SALES_PLANNING_V's own
    # certified rule); W_MARINE_GL_SOURCE_AI overrides it to "Unassigned".
    null_placeholder: str = DEFAULT_NULL_PLACEHOLDER
    # D16, dormant by design: None on every certified entity today. See
    # `RowScope` above for the mechanism-without-policy contract.
    row_scope: RowScope | None = None

    def dimensions(self) -> list[str]:
        """Column names with role == "dimension", in file order."""
        return [c.name for c in self.columns.values() if c.role == "dimension"]

    def measures(self) -> list[str]:
        """Column names with role == "measure", in file order."""
        return [c.name for c in self.columns.values() if c.role == "measure"]

    @model_validator(mode="after")
    def _row_scope_must_name_a_dimension(self) -> "Entity":
        """A ``row_scope`` column that is not a dimension of this entity is a
        certification error, refused at LOAD -- the same fail-fast posture the
        loader already takes for a malformed entity, rather than surfacing as
        a ``SpecValidationError`` on the first query months later.

        Covers both "column has the wrong role" (a measure can never key a
        per-person scope) and "column does not exist at all", with one
        message, exactly as ``query_builder._require_dimension`` does for
        filter/group-by columns: what matters is that it was never certified
        as a dimension.
        """
        if self.row_scope is not None and self.row_scope.column not in self.dimensions():
            raise ValueError(
                f"row_scope column {self.row_scope.column!r} is not a dimension "
                f"of {self.name} -- certified dimensions: {self.dimensions()}"
            )
        return self


class Ontology(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    version: int
    entities: dict[str, Entity]

    def active(self) -> dict[str, Entity]:
        """All entities except those with status == "planned"."""
        return {name: e for name, e in self.entities.items() if e.status != "planned"}

    def entity(self, name: str) -> Entity:
        """Look up a certified entity by name.

        Raises KeyError for an unknown name, or for a known-but-planned
        entity (not yet certified for querying) — either way the caller
        gets a clear, entity-named message rather than a bare KeyError.
        """
        try:
            found = self.entities[name]
        except KeyError:
            raise KeyError(f"unknown entity {name!r}") from None
        if found.status == "planned":
            raise KeyError(f"{name} is a planned entity — not yet certified for querying")
        return found
