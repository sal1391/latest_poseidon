"""Idempotent DDL for the local ``synthetic`` schema, owned by the seed path.

Phase 16 (decision D58) made Drizzle the migration authority: ``npx
drizzle-kit migrate`` creates the *application* schema, Alembic no longer runs
at boot, and ``backend/migrations/versions/0001-0010`` are frozen history.
That leaves one orphan. Migration ``0002_synthetic_schema`` created the
``synthetic`` schema and its two certified-entity tables, and Drizzle
deliberately does not own them -- ``web/drizzle.config.ts`` excludes
``synthetic`` from ``schemaFilter`` because it is mock analytics data, not app
state, and the query builder reaches it through the certified ontology.

So the seed step creates it instead: :func:`ensure_synthetic_schema` runs from
``poseidon.scripts.seed_synthetic`` before the first row count, and every
statement it issues is ``IF NOT EXISTS``. On a database Alembic 0002 already
migrated it is a no-op; on a Drizzle-only database it creates the schema.

The column dicts below are copied **verbatim** from
``backend/migrations/versions/0002_synthetic_schema.py`` --
``backend/tests/test_synthetic_schema.py::test_column_dicts_match_migration_0002_exactly``
compares them to the migration's and fails on any drift, which keeps the
existing ``test_schema_ontology_drift.py`` guard (migration dicts vs. the live
ontology) transitively covering this module too. Read 0002's docstring for why
column names are lowercased, why ``"#_FIXTURES"`` / ``"#_INQUIRIES"`` keep
their literal quoted spelling, and why GL's ``PERIOD_DATE`` is a real ``date``
here rather than the ontology's VARCHAR.

The one thing that is *translated* rather than copied is ``_PG_TYPES``: 0002
maps each type name to a SQLAlchemy ``TypeEngine`` for ``op.create_table``,
and this module emits raw SQL, so it maps to the type spelling
``information_schema.columns.data_type`` reports for that SQLAlchemy type --
``sa.Text()`` -> ``text``, ``postgresql.DOUBLE_PRECISION()`` -> ``double
precision``, ``sa.Date()`` -> ``date``. The three happen to round-trip to
their own key because 0002's keys were already Postgres type names; the
mapping is kept explicit anyway so a future column typed, say,
``sa.Numeric(18, 4)`` has one obvious place to declare ``numeric(18,4)``.
``backend/tests/fixtures/synthetic_schema_columns.json`` -- captured from a
real Alembic-migrated database before the cutover -- is the arbiter, and the
``pg`` test compares this module's output against it column for column.
"""

SCHEMA = "synthetic"
SALES_TABLE = "marine_sales_planning_v"
GL_TABLE = "w_marine_gl_source_ai"

# Copied verbatim from migration 0002 -- see the module docstring.
SALES_COLUMNS: dict[str, str] = {
    "POI_ID": "text",
    "#_FIXTURES": "double precision",
    "#_INQUIRIES": "double precision",
    "LIFT_ETA_DATE": "date",
    "GROSS_PROFIT": "double precision",
    "FIXED_TONS": "double precision",
    "CUST_NM": "text",
    "SUPPLIER_NM": "text",
    "LOC_NM": "text",
    "SUPPLY_TEAM_NAME": "text",
    "SUPP_BRKR": "text",
    "PRIMARY_SUPPLY_TEAM_OFFICE": "text",
    "PRIMARY_SUPPLY_TEAM_OFFICE_REGION": "text",
    "PRIMARY_BRKR": "text",
    "PRIMARY_BRKR_OFFICE": "text",
    "PRIMARY_BRKR_REGION": "text",
    "CUSTOMER_BRKR": "text",
    "CUSTOMER_TEAM_NAME": "text",
    "CBO_REGION": "text",
    "DEAL_CLASSIFICATION_TRADE_CUT": "text",
    "VESSEL_DASHBOARD_SHIPTYPE_GRP": "text",
    "CUST_DASHBOARD_SHIPTYPE_GRP": "text",
}

# The ontology's only `quoted: true` columns (both on the sales entity) --
# created with their exact literal name; every other column is lowercased.
_QUOTED_COLUMNS = {"#_FIXTURES", "#_INQUIRIES"}

# Copied verbatim from migration 0002 -- see the module docstring.
GL_COLUMNS: dict[str, str] = {
    "CLASS6_Calc": "text",
    "CLASS6": "text",
    "CLASS5": "text",
    "CLASS4": "text",
    "CLASS3": "text",
    "CLASS2": "text",
    "CLASS1": "text",
    "COMPANY": "text",
    "OFFICE": "text",
    "DEPARTMENT": "text",
    "ACCOUNT": "text",
    "BROKER": "text",
    "FUTURE": "text",
    "AMOUNT_USD": "double precision",
    "PERIOD_DATE": "date",
}

# Migration 0002's SQLAlchemy types, rendered as the SQL spelling
# `information_schema.columns.data_type` reports for each.
_PG_TYPES: dict[str, str] = {
    "text": "text",  # sa.Text()
    "double precision": "double precision",  # postgresql.DOUBLE_PRECISION()
    "date": "date",  # sa.Date()
}

# (index name, table, column) -- the five indexes migration 0002 created, with
# the same lowercased column spellings. None of them is a _QUOTED_COLUMNS
# member; if one ever is, quote it the way _column_sql does.
_INDEXES = (
    ("ix_marine_sales_planning_v_lift_eta_date", SALES_TABLE, "lift_eta_date"),
    ("ix_marine_sales_planning_v_cust_nm", SALES_TABLE, "cust_nm"),
    ("ix_marine_sales_planning_v_loc_nm", SALES_TABLE, "loc_nm"),
    ("ix_w_marine_gl_source_ai_period_date", GL_TABLE, "period_date"),
    ("ix_w_marine_gl_source_ai_class4", GL_TABLE, "class4"),
)


def _column_sql(ontology_columns: dict[str, str]) -> str:
    """Render an ``{ontology name: postgres type}`` mapping as a CREATE TABLE
    column list, in dict order -- which fixes ``ordinal_position`` and is why
    the fixture comparison is order-sensitive."""
    parts = []
    for name, pg_type in ontology_columns.items():
        col = f'"{name}"' if name in _QUOTED_COLUMNS else name.lower()
        parts.append(f"{col} {_PG_TYPES[pg_type]}")
    return ", ".join(parts)


def ensure_synthetic_schema(conn) -> None:
    """Idempotent DDL for migration 0002's schema. Every statement is IF NOT
    EXISTS, so running it against a database 0002 already migrated is a no-op
    and running it against a Drizzle-only database creates the schema."""
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{SALES_TABLE} ({_column_sql(SALES_COLUMNS)})"
    )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA}.{GL_TABLE} ({_column_sql(GL_COLUMNS)})"
    )
    for index_name, table, column in _INDEXES:
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {SCHEMA}.{table} ({column})"
        )


__all__ = [
    "GL_COLUMNS",
    "GL_TABLE",
    "SALES_COLUMNS",
    "SALES_TABLE",
    "SCHEMA",
    "ensure_synthetic_schema",
]
