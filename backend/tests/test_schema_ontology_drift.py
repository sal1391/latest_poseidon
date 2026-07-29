"""Schema <-> ontology drift test for migration 0002 (see task 4 brief).

OFFLINE — never opens a database connection. Migration 0002 hand-transcribes
the certified ontology's column lists into module-level ``SALES_COLUMNS`` /
``GL_COLUMNS`` dicts (it must NOT import the loader at migration runtime —
migrations are frozen artifacts, see that module's docstring); this test is
what ties those hand-derived dicts back to ``get_ontology()`` so that a
future ontology change (a column added/removed/renamed, or its declared
type changed) fails loudly here until migration 0002 — or a later
migration — is extended to match. Type coverage matters on its own: the
ontology's own ``bootstrap_conflicts`` notes flag ``AMOUNT_USD`` and
``PERIOD_DATE`` as pending reconciliation against the live Snowflake
schema, so their certified ``type`` is exactly the kind of field that can
change without any column being added/removed/renamed.

``migrations/versions/0002_synthetic_schema.py`` can't be reached with a
normal ``import`` statement: alembic revision filenames start with a digit
(``0002_...``, not a legal Python identifier) and ``migrations``/
``migrations/versions`` are not regular packages (no ``__init__.py`` —
alembic discovers revisions via its own filename scan, not the import
system). ``importlib.util.spec_from_file_location`` loads the file directly
by path instead, as a standalone module, without running any migration.
"""

import importlib.util
from pathlib import Path
from types import ModuleType

from poseidon.core.ontology.loader import load
from poseidon.core.ontology.models import Entity

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0002_synthetic_schema.py"
)

# The migration's own stated type-mapping rule (see its docstring): ontology
# column ``type`` -> Postgres type name. Kept test-local and independent of
# the migration module's ``_PG_TYPES`` (which maps postgres-type-name ->
# SQLAlchemy type object, a different mapping) so this test derives its
# expectation from the rule, not from importing the migration's own answer.
_PG_TYPES = {
    "VARCHAR": "text",
    "FLOAT": "double precision",
    "DOUBLE": "double precision",
    "NUMBER": "double precision",
    "DATE": "date",
}


def expected_pg_columns(entity: Entity, overrides: dict[str, str] | None = None) -> dict[str, str]:
    """The ``{column name: postgres type}`` mapping ``entity``'s certified
    columns should produce under the migration's type-mapping rule, with any
    documented, deliberate deviations (e.g. GL's ``PERIOD_DATE``, stored as
    ``date`` rather than the ontology's own VARCHAR — see the migration's
    docstring) layered on top via ``overrides``.
    """
    cols = {name: _PG_TYPES[col.type] for name, col in entity.columns.items()}
    cols.update(overrides or {})
    return cols


def _load_migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "_migration_0002_synthetic_schema", _MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_columns_match_ontology():
    mig = _load_migration_module()
    ont = load()

    sales = ont.entity("MARINE_SALES_PLANNING_V")
    gl = ont.entity("W_MARINE_GL_SOURCE_AI")

    # Names AND types: an ontology column added/removed/renamed, or an
    # existing column's declared type changed, must break this. PERIOD_DATE
    # is the one documented, deliberate deviation (VARCHAR in the ontology,
    # `date` in the synthetic store), so it's supplied as an override rather
    # than derived from the ontology's own type.
    assert mig.SALES_COLUMNS == expected_pg_columns(sales)
    assert mig.GL_COLUMNS == expected_pg_columns(gl, overrides={"PERIOD_DATE": "date"})

    # Count: belt-and-suspenders against a same-size substitution slipping
    # past the dict comparison (can't actually happen with dict equality,
    # but the brief calls out "names and count" explicitly).
    assert len(mig.SALES_COLUMNS) == len(sales.columns) == 22
    assert len(mig.GL_COLUMNS) == len(gl.columns) == 15
