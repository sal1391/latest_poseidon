"""Byte-pinned snapshot tests for the deterministic query builder.

Every case below asserts the EXACT rendered SQL string and the EXACT params
list (in %s-placeholder order) for one dialect/spec-shape combination, per
the rendering-rules contract in the Task 2 brief. These strings ARE the
contract — the builder was written to reproduce them, not the other way
around.

Error-message tests pin the exact `SpecValidationError` text (and, for an
unknown entity, the loader's own `KeyError` text) since those strings are
part of the contract too.
"""

import datetime as dt

import pytest

from poseidon.core.data import query_builder as qb
from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow

APRIL = PeriodWindow(dt.date(2026, 4, 1), dt.date(2026, 5, 1))


# ---------------------------------------------------------------------------
# build_metric_query — postgres
# ---------------------------------------------------------------------------


def test_six_metric_summary_postgres():
    spec = MetricQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST"),
        period=APRIL)
    sql, params = qb.build_metric_query(spec, "postgres")
    assert sql == (
        'SELECT SUM(FIXED_TONS) AS "VOLUME", SUM(GROSS_PROFIT) AS "GP", '
        'SUM(GROSS_PROFIT) / NULLIF(SUM(FIXED_TONS), 0) AS "MARGIN", '
        'SUM("#_FIXTURES") AS "NUM_WON", SUM("#_INQUIRIES") AS "NUM_INQUIRIES", '
        'SUM("#_INQUIRIES") - SUM("#_FIXTURES") AS "NUM_LOST"\n'
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1)]


def test_metric_query_multi_value_and_multi_column_filters_postgres():
    """OR within a filter column's IN-list, AND across filter columns."""
    spec = MetricQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        filters={
            "LOC_NM": ("Singapore", "Rotterdam"),
            "CUST_NM": ("Acme Shipping",),
        },
    )
    sql, params = qb.build_metric_query(spec, "postgres")
    assert sql == (
        'SELECT SUM(GROSS_PROFIT) AS "GP"\n'
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s "
        "AND COALESCE(LOC_NM, 'Unknown') IN (%s, %s) "
        "AND COALESCE(CUST_NM, 'Unknown') IN (%s)")
    assert params == [
        dt.date(2026, 4, 1), dt.date(2026, 5, 1),
        "Singapore", "Rotterdam", "Acme Shipping",
    ]


def test_win_rate_appends_having_guard_postgres():
    spec = MetricQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("WIN_RATE",),
        period=APRIL,
    )
    sql, params = qb.build_metric_query(spec, "postgres")
    assert sql == (
        'SELECT SUM("#_FIXTURES") / NULLIF(SUM("#_INQUIRIES"), 0) AS "WIN_RATE"\n'
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s\n"
        'HAVING SUM("#_INQUIRIES") >= 5')
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1)]


# ---------------------------------------------------------------------------
# build_metric_query — snowflake
# ---------------------------------------------------------------------------


def test_six_metric_summary_snowflake():
    """FQN table name; LIFT_ETA_DATE is DATE-typed, so even on snowflake the
    date filter is a plain comparison (no TO_DATE)."""
    spec = MetricQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST"),
        period=APRIL)
    sql, params = qb.build_metric_query(spec, "snowflake")
    assert sql == (
        'SELECT SUM(FIXED_TONS) AS "VOLUME", SUM(GROSS_PROFIT) AS "GP", '
        'SUM(GROSS_PROFIT) / NULLIF(SUM(FIXED_TONS), 0) AS "MARGIN", '
        'SUM("#_FIXTURES") AS "NUM_WON", SUM("#_INQUIRIES") AS "NUM_INQUIRIES", '
        'SUM("#_INQUIRIES") - SUM("#_FIXTURES") AS "NUM_LOST"\n'
        "FROM SANDBOX.MCA.MARINE_SALES_PLANNING_V\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1)]


def test_gl_monetary_total_snowflake_volume_exclusion_and_to_date():
    """PERIOD_DATE is VARCHAR-typed, so snowflake wraps it in TO_DATE(...);
    MONETARY_TOTAL always appends the dual-purpose Volume-exclusion guard,
    rendered from the ontology's dual_purpose_exclusion field."""
    spec = MetricQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
    )
    sql, params = qb.build_metric_query(spec, "snowflake")
    assert sql == (
        'SELECT SUM(AMOUNT_USD) AS "MONETARY_TOTAL"\n'
        "FROM SANDBOX.MCA.W_MARINE_GL_SOURCE_AI\n"
        "WHERE TO_DATE(PERIOD_DATE) >= %s AND TO_DATE(PERIOD_DATE) < %s "
        "AND COALESCE(CLASS4,'') <> 'Volume'")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1)]


# ---------------------------------------------------------------------------
# build_breakdown_query — postgres
# ---------------------------------------------------------------------------


def test_breakdown_top_5_ports_by_gp_for_customer_postgres():
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        group_by="LOC_NM",
        order_by_metric="GP",
        top_n=5,
        filters={"CUST_NM": ("Acme Shipping",)},
    )
    sql, params = qb.build_breakdown_query(spec, "postgres")
    assert sql == (
        "SELECT COALESCE(LOC_NM, 'Unknown') AS LOC_NM, SUM(GROSS_PROFIT) AS \"GP\"\n"
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s "
        "AND COALESCE(CUST_NM, 'Unknown') IN (%s)\n"
        "GROUP BY 1\n"
        'ORDER BY "GP" DESC NULLS LAST\n'
        "LIMIT %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), "Acme Shipping", 5]


def test_breakdown_top_customers_by_gp_in_singapore_postgres():
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        group_by="CUST_NM",
        order_by_metric="GP",
        top_n=5,
        filters={"LOC_NM": ("Singapore",)},
    )
    sql, params = qb.build_breakdown_query(spec, "postgres")
    assert sql == (
        "SELECT COALESCE(CUST_NM, 'Unknown') AS CUST_NM, SUM(GROSS_PROFIT) AS \"GP\"\n"
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s "
        "AND COALESCE(LOC_NM, 'Unknown') IN (%s)\n"
        "GROUP BY 1\n"
        'ORDER BY "GP" DESC NULLS LAST\n'
        "LIMIT %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), "Singapore", 5]


def test_breakdown_win_rate_appends_having_guard_postgres():
    """Exercises the full clause order in one query: SELECT / FROM / WHERE /
    GROUP BY / HAVING / ORDER BY / LIMIT."""
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("WIN_RATE",),
        period=APRIL,
        group_by="LOC_NM",
        order_by_metric="WIN_RATE",
        top_n=5,
    )
    sql, params = qb.build_breakdown_query(spec, "postgres")
    assert sql == (
        "SELECT COALESCE(LOC_NM, 'Unknown') AS LOC_NM, "
        'SUM("#_FIXTURES") / NULLIF(SUM("#_INQUIRIES"), 0) AS "WIN_RATE"\n'
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s\n"
        "GROUP BY 1\n"
        'HAVING SUM("#_INQUIRIES") >= 5\n'
        'ORDER BY "WIN_RATE" DESC NULLS LAST\n'
        "LIMIT %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), 5]


def test_gl_breakdown_by_company_postgres_dual_purpose_and_plain_date():
    """The dual-purpose guard applies in the breakdown shape too, and under
    postgres — not just snowflake. Postgres never wraps TO_DATE even though
    PERIOD_DATE is VARCHAR-typed (that rule is snowflake-only)."""
    spec = BreakdownQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
        group_by="COMPANY",
        order_by_metric="MONETARY_TOTAL",
        top_n=5,
    )
    sql, params = qb.build_breakdown_query(spec, "postgres")
    assert sql == (
        "SELECT COALESCE(COMPANY, 'Unknown') AS COMPANY, "
        'SUM(AMOUNT_USD) AS "MONETARY_TOTAL"\n'
        "FROM synthetic.w_marine_gl_source_ai\n"
        "WHERE PERIOD_DATE >= %s AND PERIOD_DATE < %s "
        "AND COALESCE(CLASS4,'') <> 'Volume'\n"
        "GROUP BY 1\n"
        'ORDER BY "MONETARY_TOTAL" DESC NULLS LAST\n'
        "LIMIT %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), 5]


# ---------------------------------------------------------------------------
# build_breakdown_query — snowflake
# ---------------------------------------------------------------------------


def test_breakdown_top_suppliers_by_volume_snowflake():
    """Snowflake ORDER BY has no NULLS LAST suffix; FQN table name; plain
    date comparison (LIFT_ETA_DATE is DATE-typed, not VARCHAR)."""
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("VOLUME",),
        period=APRIL,
        group_by="SUPPLIER_NM",
        order_by_metric="VOLUME",
        top_n=5,
    )
    sql, params = qb.build_breakdown_query(spec, "snowflake")
    assert sql == (
        "SELECT COALESCE(SUPPLIER_NM, 'Unknown') AS SUPPLIER_NM, "
        'SUM(FIXED_TONS) AS "VOLUME"\n'
        "FROM SANDBOX.MCA.MARINE_SALES_PLANNING_V\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s\n"
        "GROUP BY 1\n"
        'ORDER BY "VOLUME" DESC\n'
        "LIMIT %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), 5]


# ---------------------------------------------------------------------------
# build_dimension_values_query
# ---------------------------------------------------------------------------


def test_dimension_values_with_search_postgres():
    sql, params = qb.build_dimension_values_query(
        "MARINE_SALES_PLANNING_V", "LOC_NM", "Sing", "postgres")
    assert sql == (
        "SELECT DISTINCT LOC_NM\n"
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LOC_NM IS NOT NULL AND LOC_NM ILIKE %s\n"
        "ORDER BY LOC_NM")
    assert params == ["%Sing%"]


def test_dimension_values_without_search_postgres():
    sql, params = qb.build_dimension_values_query(
        "MARINE_SALES_PLANNING_V", "LOC_NM", None, "postgres")
    assert sql == (
        "SELECT DISTINCT LOC_NM\n"
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LOC_NM IS NOT NULL\n"
        "ORDER BY LOC_NM")
    assert params == []


def test_dimension_values_with_search_snowflake():
    """ILIKE is used on snowflake too — dialect only changes the table name."""
    sql, params = qb.build_dimension_values_query(
        "MARINE_SALES_PLANNING_V", "LOC_NM", "Sing", "snowflake")
    assert sql == (
        "SELECT DISTINCT LOC_NM\n"
        "FROM SANDBOX.MCA.MARINE_SALES_PLANNING_V\n"
        "WHERE LOC_NM IS NOT NULL AND LOC_NM ILIKE %s\n"
        "ORDER BY LOC_NM")
    assert params == ["%Sing%"]


def test_dimension_values_query_rejects_non_dimension_column():
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_dimension_values_query(
            "MARINE_SALES_PLANNING_V", "GROSS_PROFIT", None, "postgres")
    assert str(exc_info.value) == "'GROSS_PROFIT' is not a dimension of MARINE_SALES_PLANNING_V"


# ---------------------------------------------------------------------------
# build_period_range_query
# ---------------------------------------------------------------------------


def test_period_range_marine_sales_postgres():
    sql, params = qb.build_period_range_query("MARINE_SALES_PLANNING_V", "postgres")
    assert sql == (
        'SELECT MIN(LIFT_ETA_DATE) AS "MIN_DATE", MAX(LIFT_ETA_DATE) AS "MAX_DATE"\n'
        "FROM synthetic.marine_sales_planning_v")
    assert params == []


def test_period_range_gl_snowflake_wraps_to_date():
    """PERIOD_DATE is VARCHAR-typed: both MIN and MAX wrap it in TO_DATE(...)."""
    sql, params = qb.build_period_range_query("W_MARINE_GL_SOURCE_AI", "snowflake")
    assert sql == (
        'SELECT MIN(TO_DATE(PERIOD_DATE)) AS "MIN_DATE", '
        'MAX(TO_DATE(PERIOD_DATE)) AS "MAX_DATE"\n'
        "FROM SANDBOX.MCA.W_MARINE_GL_SOURCE_AI")
    assert params == []


# ---------------------------------------------------------------------------
# Validation errors — exact messages are part of the contract
# ---------------------------------------------------------------------------


def test_unknown_metric_raises():
    spec = MetricQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("REVENUE",),
        period=APRIL,
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_metric_query(spec, "postgres")
    assert str(exc_info.value) == (
        "unknown metric 'REVENUE' for entity MARINE_SALES_PLANNING_V — "
        "certified: ['GP', 'MARGIN', 'NUM_INQUIRIES', 'NUM_LOST', 'NUM_WON', "
        "'VOLUME', 'WIN_RATE']")


def test_non_dimension_group_by_raises():
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        group_by="GROSS_PROFIT",
        order_by_metric="GP",
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_breakdown_query(spec, "postgres")
    assert str(exc_info.value) == "'GROSS_PROFIT' is not a dimension of MARINE_SALES_PLANNING_V"


def test_filter_on_unknown_column_raises():
    """PORT_NM is the hallucinated column from the shipped apps' negative
    constraints — it isn't a certified column at all (the real one is LOC_NM)."""
    spec = MetricQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        filters={"PORT_NM": ("Singapore",)},
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_metric_query(spec, "postgres")
    assert str(exc_info.value) == "'PORT_NM' is not a dimension of MARINE_SALES_PLANNING_V"


def test_order_by_metric_not_in_metrics_raises():
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        group_by="LOC_NM",
        order_by_metric="VOLUME",
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_breakdown_query(spec, "postgres")
    assert str(exc_info.value) == (
        "order_by_metric 'VOLUME' must be one of the requested metrics ('GP',)")


def test_top_n_less_than_one_raises():
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        group_by="LOC_NM",
        order_by_metric="GP",
        top_n=0,
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_breakdown_query(spec, "postgres")
    assert str(exc_info.value) == "top_n must be >= 1, got 0"


def test_unknown_entity_raises_loader_keyerror():
    """Entity resolution delegates to the loader — its KeyError passes
    through unwrapped, not a SpecValidationError."""
    spec = MetricQuerySpec(
        entity="NOT_AN_ENTITY",
        metrics=("GP",),
        period=APRIL,
    )
    with pytest.raises(KeyError, match="unknown entity 'NOT_AN_ENTITY'"):
        qb.build_metric_query(spec, "postgres")
