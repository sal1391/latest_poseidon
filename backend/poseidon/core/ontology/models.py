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

from pydantic import BaseModel, ConfigDict


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

    def dimensions(self) -> list[str]:
        """Column names with role == "dimension", in file order."""
        return [c.name for c in self.columns.values() if c.role == "dimension"]

    def measures(self) -> list[str]:
        """Column names with role == "measure", in file order."""
        return [c.name for c in self.columns.values() if c.role == "measure"]


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
