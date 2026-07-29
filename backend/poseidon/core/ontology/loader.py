"""Loads the vendored certified ontology (``ontology/ontology.yml``) into the
typed models in ``models.py``.

Default path resolution is anchored to this module's own file location, not
the process cwd, so ``load()`` finds the same file whether pytest runs from
the repo root or from ``backend/``:

    backend/poseidon/core/ontology/loader.py
    parents[0] = .../backend/poseidon/core/ontology
    parents[1] = .../backend/poseidon/core
    parents[2] = .../backend/poseidon
    parents[3] = .../backend
    parents[4] = .../  <- repo root

We use the fixed ``parents[4]`` offset (rather than walking up looking for
an ``ontology/`` directory) since the package depth relative to the repo
root is a stable, already-committed layout choice.

Parsing notes (see task brief / docs/architecture/04-data-ontology.md §2):

- Each entity's ``columns`` / ``metrics`` mapping keys are YAML identifiers
  (e.g. ``"#_FIXTURES"``, ``MARGIN``); those keys are not present *inside*
  the value mapping, so the loader injects them as the ``name`` field
  explicitly rather than relying on generic model validation (which would
  otherwise either reject the missing required field or silently drop the
  name-from-key association).
- ``hierarchies.level_columns`` -> ``Entity.hierarchy_levels``.
- ``hierarchies.dual_purpose_measures[0].exclusion_clause`` ->
  ``Entity.dual_purpose_exclusion``.
- Top-level keys the loader does not model (``apps``, provenance blocks,
  ``bootstrap_conflicts``, ``disambiguations``, ``data_snapshots``, ...)
  are simply never read, so they can't crash parsing.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .models import Column, Entity, Metric, NegativeConstraint, Ontology

_REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_ONTOLOGY_PATH = _REPO_ROOT / "ontology" / "ontology.yml"


def _parse_columns(raw: dict[str, Any] | None) -> dict[str, Column]:
    return {name: Column(name=name, **body) for name, body in (raw or {}).items()}


def _parse_metrics(raw: dict[str, Any] | None) -> dict[str, Metric]:
    return {name: Metric(name=name, **body) for name, body in (raw or {}).items()}


def _parse_negative_constraints(raw: list[dict[str, Any]] | None) -> list[NegativeConstraint]:
    return [NegativeConstraint(**item) for item in (raw or [])]


def _parse_hierarchies(raw: dict[str, Any] | None) -> tuple[list[str], str | None]:
    hierarchies = raw or {}
    hierarchy_levels = list(hierarchies.get("level_columns") or [])
    dual_purpose_measures = hierarchies.get("dual_purpose_measures") or []
    dual_purpose_exclusion = (
        dual_purpose_measures[0].get("exclusion_clause") if dual_purpose_measures else None
    )
    return hierarchy_levels, dual_purpose_exclusion


def _parse_entity(name: str, raw: dict[str, Any]) -> Entity:
    hierarchy_levels, dual_purpose_exclusion = _parse_hierarchies(raw.get("hierarchies"))
    return Entity(
        name=name,
        fqn=raw.get("fqn", ""),
        status=raw.get("status", "certified"),
        grain=raw.get("grain", ""),
        date_column=raw.get("date_column"),
        columns=_parse_columns(raw.get("columns")),
        metrics=_parse_metrics(raw.get("metrics")),
        negative_constraints=_parse_negative_constraints(raw.get("negative_constraints")),
        business_rules=list(raw.get("business_rules") or []),
        hierarchy_levels=hierarchy_levels,
        dual_purpose_exclusion=dual_purpose_exclusion,
    )


def load(path: Path | None = None) -> Ontology:
    """Parse the certified ontology YAML into an :class:`Ontology`.

    ``path`` defaults to the vendored ``ontology/ontology.yml`` at the repo
    root (see module docstring for how that default is resolved).
    """
    target = path if path is not None else DEFAULT_ONTOLOGY_PATH
    with open(target, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    entities = {
        entity_name: _parse_entity(entity_name, entity_body or {})
        for entity_name, entity_body in raw["entities"].items()
    }
    return Ontology(version=raw["version"], entities=entities)


@lru_cache
def get_ontology() -> Ontology:
    """Process-wide cached load of the default ontology."""
    return load()
