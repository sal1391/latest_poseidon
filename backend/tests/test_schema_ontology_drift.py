"""Schema <-> ontology drift test for migration 0002 (see task 4 brief).

OFFLINE — never opens a database connection. Migration 0002 hand-transcribes
the certified ontology's column lists into module-level ``SALES_COLUMNS`` /
``GL_COLUMNS`` dicts (it must NOT import the loader at migration runtime —
migrations are frozen artifacts, see that module's docstring); this test is
what ties those hand-derived dicts back to ``get_ontology()`` so that a
future ontology change (a column added/removed/renamed) fails loudly here
until migration 0002 — or a later migration — is extended to match.

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

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0002_synthetic_schema.py"
)


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

    sales_columns = ont.entity("MARINE_SALES_PLANNING_V").columns
    gl_columns = ont.entity("W_MARINE_GL_SOURCE_AI").columns

    # Names: an ontology column added/removed/renamed must break this.
    assert set(mig.SALES_COLUMNS) == set(sales_columns)
    assert set(mig.GL_COLUMNS) == set(gl_columns)

    # Count: belt-and-suspenders against a same-size substitution slipping
    # past the set comparison (can't actually happen with dict keys, but the
    # brief calls out "names and count" explicitly).
    assert len(mig.SALES_COLUMNS) == len(sales_columns) == 22
    assert len(mig.GL_COLUMNS) == len(gl_columns) == 15
