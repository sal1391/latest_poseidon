"""synthetic schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-28

Creates the local ``synthetic`` Postgres schema and the two certified-entity
tables — ``marine_sales_planning_v`` and ``w_marine_gl_source_ai`` — that
back ``SyntheticDataClient`` (a later task). ``SALES_COLUMNS`` / ``GL_COLUMNS``
below are a **hand transcription** of ``ontology/ontology.yml``'s
``MARINE_SALES_PLANNING_V`` / ``W_MARINE_GL_SOURCE_AI`` column lists —
migrations are frozen artifacts, so this module deliberately does NOT import
``poseidon.core.ontology.loader`` at migration runtime. Instead,
``backend/tests/test_schema_ontology_drift.py`` loads this module offline
and asserts ``SALES_COLUMNS`` / ``GL_COLUMNS`` still match the live ontology
column-for-column, so a future ontology edit (column added/removed/renamed)
fails that test loudly until this migration (or a later one) is extended.

Column-name casing: ``poseidon.core.data.query_builder`` interpolates
dimension/date column names as bare, UNQUOTED identifiers straight into the
SQL text it sends to Postgres — e.g. ``COALESCE(CUST_NM, 'Unknown')`` — and
Postgres folds every unquoted identifier to lowercase before catalog lookup,
regardless of how it was typed in the query. So every ordinary column here
is created with its lowercased name (``cust_nm``, ``class6_calc``, ...),
which is exactly what Postgres would fold the ontology's uppercase/
mixed-case spelling to anyway — see ``_columns()``. The two measure columns
the ontology marks ``quoted: true`` — ``"#_FIXTURES"`` / ``"#_INQUIRIES"`` —
are never referenced bare; they only ever appear pre-quoted inside a
certified metric SQL formula (e.g. ``SUM("#_FIXTURES")``), so they are
created here with their exact literal (quoted) names instead.

Type mapping is VARCHAR -> ``text``, FLOAT/DOUBLE/NUMBER -> ``double
precision``, DATE -> ``date``, with one deliberate exception: the ontology
types GL's ``PERIOD_DATE`` as VARCHAR (Snowflake may store it as a string —
see the ontology's own ``bootstrap_conflicts`` note and its business rule
"always wrap in TO_DATE(PERIOD_DATE) before DATE_TRUNC"), but this synthetic
Postgres table stores it as a real ``date`` column instead. The
``TO_DATE(...)`` wrapping is a Snowflake-dialect-only concern —
``query_builder._date_expr`` never wraps on the ``postgres`` dialect, it
only ever compares the bare column — so the synthetic store has no need to
replicate the VARCHAR-storage quirk and ``PERIOD_DATE`` is typed ``date``
here.

This migration is a no-op on any non-Postgres dialect (guarded on
``op.get_bind().dialect.name``) so that ``backend/tests/test_migrations.py``
— which runs ``alembic upgrade head`` against a throwaway SQLite database —
stays green; SQLite has no schema concept and Alembic batch-mode can't
paper over a bare ``CREATE SCHEMA``.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_SCHEMA = "synthetic"
_SALES_TABLE = "marine_sales_planning_v"
_GL_TABLE = "w_marine_gl_source_ai"

# Hand-derived from ontology/ontology.yml's MARINE_SALES_PLANNING_V.columns:
# ontology column name (exactly as certified) -> Postgres type name. Keep in
# sync by hand — backend/tests/test_schema_ontology_drift.py fails loudly if
# this ever drifts from the ontology.
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

# The ontology's only `quoted: true` columns (both on the sales entity) —
# created with their exact literal name; every other column is lowercased
# (see module docstring).
_QUOTED_COLUMNS = {"#_FIXTURES", "#_INQUIRIES"}

# Hand-derived from ontology/ontology.yml's W_MARINE_GL_SOURCE_AI.columns.
# PERIOD_DATE is `date` here, not the ontology's VARCHAR — see module
# docstring.
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

_PG_TYPES: dict[str, sa.types.TypeEngine] = {
    "text": sa.Text(),
    "double precision": postgresql.DOUBLE_PRECISION(),
    "date": sa.Date(),
}


def _columns(ontology_columns: dict[str, str]) -> list[sa.Column]:
    """Render an ``{ontology name: postgres type}`` mapping into ``sa.Column``s.

    Columns in ``_QUOTED_COLUMNS`` keep their exact literal name (quoted);
    every other column is lowercased — equivalent to what Postgres would
    fold an unquoted identifier to anyway (see module docstring).
    """
    columns = []
    for name, pg_type in ontology_columns.items():
        quoted = name in _QUOTED_COLUMNS
        col_name = name if quoted else name.lower()
        columns.append(
            sa.Column(col_name, _PG_TYPES[pg_type], quote=True if quoted else None)
        )
    return columns


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}")

    op.create_table(_SALES_TABLE, *_columns(SALES_COLUMNS), schema=_SCHEMA)
    op.create_index(
        "ix_marine_sales_planning_v_lift_eta_date",
        _SALES_TABLE,
        ["lift_eta_date"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_marine_sales_planning_v_cust_nm", _SALES_TABLE, ["cust_nm"], schema=_SCHEMA
    )
    op.create_index(
        "ix_marine_sales_planning_v_loc_nm", _SALES_TABLE, ["loc_nm"], schema=_SCHEMA
    )

    op.create_table(_GL_TABLE, *_columns(GL_COLUMNS), schema=_SCHEMA)
    op.create_index(
        "ix_w_marine_gl_source_ai_period_date",
        _GL_TABLE,
        ["period_date"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_w_marine_gl_source_ai_class4", _GL_TABLE, ["class4"], schema=_SCHEMA
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.drop_table(_GL_TABLE, schema=_SCHEMA)
    op.drop_table(_SALES_TABLE, schema=_SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {_SCHEMA}")
