"""Phase 16 Task 8: the `synthetic` schema is created by the seed path, not by
Alembic, now that Drizzle owns migrations. Two pins: the column dicts stay
byte-equal to migration 0002's (offline), and the DDL reproduces what 0002
produced on a real database (pg)."""

import importlib.util
import json
import os
from pathlib import Path

import pytest

from poseidon.core.data.synthetic_schema import (
    GL_COLUMNS,
    SALES_COLUMNS,
    ensure_synthetic_schema,
)

_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "versions"
    / "0002_synthetic_schema.py"
)
_FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_schema_columns.json"


def _load_0002():
    spec = importlib.util.spec_from_file_location("m0002", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_column_dicts_match_migration_0002_exactly():
    m = _load_0002()
    assert SALES_COLUMNS == m.SALES_COLUMNS
    assert GL_COLUMNS == m.GL_COLUMNS


@pytest.mark.pg
def test_ddl_reproduces_the_alembic_schema_on_a_scratch_database():
    import psycopg

    from poseidon.core.data.synthetic_client import normalize_dsn

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip("DATABASE_URL unset")
    admin = psycopg.connect(normalize_dsn(dsn), autocommit=True)
    admin.execute("DROP DATABASE IF EXISTS poseidon_synth_probe")
    admin.execute("CREATE DATABASE poseidon_synth_probe")
    probe_dsn = normalize_dsn(dsn).rsplit("/", 1)[0] + "/poseidon_synth_probe"
    with psycopg.connect(probe_dsn) as conn:
        ensure_synthetic_schema(conn)
        ensure_synthetic_schema(conn)  # idempotent
        conn.commit()
        rows = conn.execute(
            "SELECT table_name, column_name, data_type, numeric_precision, numeric_scale, "
            "ordinal_position FROM information_schema.columns WHERE table_schema='synthetic' "
            "ORDER BY table_name, ordinal_position"
        ).fetchall()
        idx = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='synthetic' "
            "ORDER BY indexname"
        ).fetchall()
    expected = json.loads(_FIXTURE.read_text())
    assert [list(r) for r in rows] == expected["columns"]
    assert [list(r) for r in idx] == expected["indexes"]
    admin.execute("DROP DATABASE poseidon_synth_probe")
