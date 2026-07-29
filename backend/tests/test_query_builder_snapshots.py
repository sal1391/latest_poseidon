"""Byte-pinned snapshot tests for the deterministic query builder.

Every case below asserts the EXACT rendered SQL string and the EXACT params
list (in %s-placeholder order) for one dialect/spec-shape combination, per
the rendering-rules contract in the Task 2 brief. These strings ARE the
contract — the builder was written to reproduce them, not the other way
around.

Error-message tests pin the exact `SpecValidationError` text (and, for an
unknown entity, the loader's own `KeyError` text) since those strings are
part of the contract too.

ADJUDICATED CONTRACT CHANGE (final-review wave) — the COALESCE() NULL
placeholder is now per-entity, read from `Entity.null_placeholder` instead
of a hardcoded 'Unknown'. Every W_MARINE_GL_SOURCE_AI snapshot below
therefore expects `'Unassigned'`, in both group-by projections and filter
predicates: that is what the GL table's own certified rules require
(ontology.yml business_rules "COALESCE(<col>,'Unassigned') on every GROUP
BY", and the CLASS1 column description). MARINE_SALES_PLANNING_V snapshots
are UNCHANGED at 'Unknown' — that entity's certified rule says 'Unknown',
which is `null_placeholder`'s default. The previous GL strings were wrong
against the certified rules, not merely different; they are not preserved.
"""

import datetime as dt

import pytest

from poseidon.core.data import query_builder as qb
from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.ontology.models import Entity

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


def test_gl_metric_query_mixed_class4_filter_keeps_guard_snowflake():
    """CLASS4 IN ('Volume', 'Trade GP') is a mixed IN-list, not volume mode
    (the filter isn't pinned to *exactly* the pivot value) — this is still a
    monetary query, so the dual-purpose guard still applies, silently
    narrowing the IN-list to the monetary-compatible category."""
    spec = MetricQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
        filters={"CLASS4": ("Volume", "Trade GP")},
    )
    sql, params = qb.build_metric_query(spec, "snowflake")
    assert sql == (
        'SELECT SUM(AMOUNT_USD) AS "MONETARY_TOTAL"\n'
        "FROM SANDBOX.MCA.W_MARINE_GL_SOURCE_AI\n"
        "WHERE TO_DATE(PERIOD_DATE) >= %s AND TO_DATE(PERIOD_DATE) < %s "
        "AND COALESCE(CLASS4, 'Unassigned') IN (%s, %s) "
        "AND COALESCE(CLASS4,'') <> 'Volume'")
    assert params == [
        dt.date(2026, 4, 1), dt.date(2026, 5, 1), "Volume", "Trade GP",
    ]


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
        "SELECT COALESCE(COMPANY, 'Unassigned') AS COMPANY, "
        'SUM(AMOUNT_USD) AS "MONETARY_TOTAL"\n'
        "FROM synthetic.w_marine_gl_source_ai\n"
        "WHERE PERIOD_DATE >= %s AND PERIOD_DATE < %s "
        "AND COALESCE(CLASS4,'') <> 'Volume'\n"
        "GROUP BY 1\n"
        'ORDER BY "MONETARY_TOTAL" DESC NULLS LAST\n'
        "LIMIT %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), 5]


def test_gl_breakdown_by_class1_postgres_uses_unassigned_placeholder():
    """The certified-placeholder case, on the column the rule was written
    for: CLASS1 is NULL on ~76% of GL rows, and ontology.yml requires
    `COALESCE(<col>,'Unassigned')` on every GL GROUP BY. Both COALESCE sites
    — the group-by projection AND the filter predicate — must render
    'Unassigned', not the default 'Unknown' the sales snapshots still use.
    COMPANY is filtered here purely so the filter site appears in the same
    snapshot as the projection site."""
    spec = BreakdownQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
        group_by="CLASS1",
        order_by_metric="MONETARY_TOTAL",
        top_n=10,
        filters={"COMPANY": ("Poseidon Marine Fuels Ltd",)},
    )
    sql, params = qb.build_breakdown_query(spec, "postgres")
    assert sql == (
        "SELECT COALESCE(CLASS1, 'Unassigned') AS CLASS1, "
        'SUM(AMOUNT_USD) AS "MONETARY_TOTAL"\n'
        "FROM synthetic.w_marine_gl_source_ai\n"
        "WHERE PERIOD_DATE >= %s AND PERIOD_DATE < %s "
        "AND COALESCE(COMPANY, 'Unassigned') IN (%s) "
        "AND COALESCE(CLASS4,'') <> 'Volume'\n"
        "GROUP BY 1\n"
        'ORDER BY "MONETARY_TOTAL" DESC NULLS LAST\n'
        "LIMIT %s")
    assert params == [
        dt.date(2026, 4, 1), dt.date(2026, 5, 1), "Poseidon Marine Fuels Ltd", 10,
    ]


def test_volume_mode_class3_breakdown_postgres_drops_guard():
    """Volume mode (CLASS4 pinned to exactly ('Volume',)) drops the
    dual-purpose guard entirely — the filter itself already scopes
    AMOUNT_USD to one unit — under postgres; the guard drop isn't
    snowflake-specific, and postgres's usual NULLS LAST/plain-date rules
    still apply on top of it."""
    spec = BreakdownQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
        group_by="CLASS3",
        order_by_metric="MONETARY_TOTAL",
        top_n=5,
        filters={"CLASS4": ("Volume",)},
    )
    sql, params = qb.build_breakdown_query(spec, "postgres")
    assert sql == (
        "SELECT COALESCE(CLASS3, 'Unassigned') AS CLASS3, "
        'SUM(AMOUNT_USD) AS "MONETARY_TOTAL"\n'
        "FROM synthetic.w_marine_gl_source_ai\n"
        "WHERE PERIOD_DATE >= %s AND PERIOD_DATE < %s "
        "AND COALESCE(CLASS4, 'Unassigned') IN (%s)\n"
        "GROUP BY 1\n"
        'ORDER BY "MONETARY_TOTAL" DESC NULLS LAST\n'
        "LIMIT %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), "Volume", 5]


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


def test_volume_mode_class3_breakdown_snowflake_drops_guard():
    """Volume mode drops the dual-purpose guard under snowflake too, and
    CLASS3 is the certified required group_by — the hierarchy level
    immediately below the CLASS4 pivot (`hierarchy_levels`), computed from
    the loader's pivot fields, not a hardcoded name. This is the case that
    used to be a self-contradicting WHERE clause before volume mode:
    `CLASS4 = 'Volume'` and the guard `<> 'Volume'` can no longer both
    appear at once."""
    spec = BreakdownQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
        group_by="CLASS3",
        order_by_metric="MONETARY_TOTAL",
        top_n=5,
        filters={"CLASS4": ("Volume",)},
    )
    sql, params = qb.build_breakdown_query(spec, "snowflake")
    assert sql == (
        "SELECT COALESCE(CLASS3, 'Unassigned') AS CLASS3, "
        'SUM(AMOUNT_USD) AS "MONETARY_TOTAL"\n'
        "FROM SANDBOX.MCA.W_MARINE_GL_SOURCE_AI\n"
        "WHERE TO_DATE(PERIOD_DATE) >= %s AND TO_DATE(PERIOD_DATE) < %s "
        "AND COALESCE(CLASS4, 'Unassigned') IN (%s)\n"
        "GROUP BY 1\n"
        'ORDER BY "MONETARY_TOTAL" DESC\n'
        "LIMIT %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), "Volume", 5]


def test_volume_mode_breakdown_duplicate_pivot_values_snowflake():
    """`_is_volume_mode` compares DISTINCT filter values, so a duplicate-
    laden tuple like `("Volume", "Volume")` is still volume mode — the
    guard stays dropped — even though it isn't the single-element tuple the
    simplest reading of the rule would expect. Rendering itself doesn't
    dedupe: the IN-list carries both %s params exactly as given."""
    spec = BreakdownQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
        group_by="CLASS3",
        order_by_metric="MONETARY_TOTAL",
        top_n=5,
        filters={"CLASS4": ("Volume", "Volume")},
    )
    sql, params = qb.build_breakdown_query(spec, "snowflake")
    assert sql == (
        "SELECT COALESCE(CLASS3, 'Unassigned') AS CLASS3, "
        'SUM(AMOUNT_USD) AS "MONETARY_TOTAL"\n'
        "FROM SANDBOX.MCA.W_MARINE_GL_SOURCE_AI\n"
        "WHERE TO_DATE(PERIOD_DATE) >= %s AND TO_DATE(PERIOD_DATE) < %s "
        "AND COALESCE(CLASS4, 'Unassigned') IN (%s, %s)\n"
        "GROUP BY 1\n"
        'ORDER BY "MONETARY_TOTAL" DESC\n'
        "LIMIT %s")
    assert params == [
        dt.date(2026, 4, 1), dt.date(2026, 5, 1), "Volume", "Volume", 5,
    ]


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


def test_gl_dimension_values_with_search_snowflake():
    """Dimension-values queries never touch the dual-purpose guard (that
    clause is only ever appended inside `_where_clause`, which this builder
    doesn't call) — this case exercises the one snowflake/entity combination
    the existing snapshots don't: W_MARINE_GL_SOURCE_AI's FQN table name."""
    sql, params = qb.build_dimension_values_query(
        "W_MARINE_GL_SOURCE_AI", "COMPANY", "Acme", "snowflake")
    assert sql == (
        "SELECT DISTINCT COMPANY\n"
        "FROM SANDBOX.MCA.W_MARINE_GL_SOURCE_AI\n"
        "WHERE COMPANY IS NOT NULL AND COMPANY ILIKE %s\n"
        "ORDER BY COMPANY")
    assert params == ["%Acme%"]


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


def test_empty_filter_values_raises():
    """A filter column whose value collection is EMPTY used to render an
    invalid `IN ()` (a syntax error on both dialects) instead of failing —
    it is now refused before rendering, in both spec shapes. The message is
    asserted by exact equality, snapshot-style: it is part of the contract,
    not an informal hint."""
    metric_spec = MetricQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        filters={"LOC_NM": ()},
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_metric_query(metric_spec, "postgres")
    assert str(exc_info.value) == (
        "filter on 'LOC_NM' has no values — omit the column or provide "
        "at least one value")

    breakdown_spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        group_by="CUST_NM",
        order_by_metric="GP",
        filters={"LOC_NM": ()},
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_breakdown_query(breakdown_spec, "postgres")
    assert str(exc_info.value) == (
        "filter on 'LOC_NM' has no values — omit the column or provide "
        "at least one value")


def test_inverted_period_window_raises():
    """`PeriodWindow` rejects `start >= end` at construction — before any
    builder call — so an inverted window can never reach SQL and quietly
    return "no data". Both the strictly-inverted case and the degenerate
    `start == end` case (a half-open window with coincident bounds is empty
    by definition) raise, with the same message shape."""
    with pytest.raises(ValueError) as exc_info:
        PeriodWindow(dt.date(2026, 5, 1), dt.date(2026, 4, 1))
    assert str(exc_info.value) == (
        "period window start 2026-05-01 must be before end 2026-04-01")

    with pytest.raises(ValueError) as exc_info:
        PeriodWindow(dt.date(2026, 4, 1), dt.date(2026, 4, 1))
    assert str(exc_info.value) == (
        "period window start 2026-04-01 must be before end 2026-04-01")


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


def test_volume_mode_metric_query_rejects_single_aggregate():
    """CLASS4 pinned to exactly ('Volume',) on a plain MetricQuerySpec is
    volume mode — refused outright, since a single aggregate mixes
    incompatible units (tons, gallons, ...) across CLASS3 categories. The
    message is built from the entity's own pivot fields (never a hardcoded
    'CLASS4'/'Volume' literal) plus the computed required breakdown level."""
    spec = MetricQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
        filters={"CLASS4": ("Volume",)},
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_metric_query(spec, "postgres")
    assert str(exc_info.value) == (
        "CLASS4 = 'Volume' is unit-mixed — a single aggregate is not "
        "allowed; use a breakdown grouped by CLASS3")


def test_volume_mode_breakdown_wrong_group_by_rejects():
    """Volume mode further restricts group_by to CLASS3 (the hierarchy
    level immediately below the CLASS4 pivot) — COMPANY is a perfectly
    certified dimension of this entity outside volume mode (see
    `test_gl_breakdown_by_company_postgres_dual_purpose_and_plain_date`),
    but not once CLASS4 is pinned to 'Volume'."""
    spec = BreakdownQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
        group_by="COMPANY",
        order_by_metric="MONETARY_TOTAL",
        top_n=5,
        filters={"CLASS4": ("Volume",)},
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb.build_breakdown_query(spec, "postgres")
    assert str(exc_info.value) == (
        "volume-mode breakdowns must group by CLASS3 "
        "(each CLASS3 is one unit); got 'COMPANY'")


def test_volume_mode_required_group_by_defensive_errors():
    """`_volume_mode_required_group_by`'s two defensive branches are
    unreachable through `build_metric_query`/`build_breakdown_query` against
    the vendored ontology — W_MARINE_GL_SOURCE_AI's CLASS4 pivot is always
    present in `hierarchy_levels` and is never the last (narrowest) level —
    so both are exercised directly here, against synthetic `Entity` objects
    constructed inline rather than the loaded ontology."""
    pivot_not_a_level = Entity(
        name="SYNTHETIC_MISSING_PIVOT",
        fqn="X.Y.SYNTHETIC_MISSING_PIVOT",
        hierarchy_levels=["L1", "L2"],
        dual_purpose_pivot_column="NOT_A_LEVEL",
        dual_purpose_pivot_value="Volume",
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb._volume_mode_required_group_by(pivot_not_a_level)
    assert str(exc_info.value) == (
        "pivot column 'NOT_A_LEVEL' is not a hierarchy level of SYNTHETIC_MISSING_PIVOT")

    pivot_is_last_level = Entity(
        name="SYNTHETIC_LAST_LEVEL",
        fqn="X.Y.SYNTHETIC_LAST_LEVEL",
        hierarchy_levels=["L1", "L2", "PIVOT"],
        dual_purpose_pivot_column="PIVOT",
        dual_purpose_pivot_value="Volume",
    )
    with pytest.raises(qb.SpecValidationError) as exc_info:
        qb._volume_mode_required_group_by(pivot_is_last_level)
    assert str(exc_info.value) == (
        "pivot column 'PIVOT' has no level below it in SYNTHETIC_LAST_LEVEL")
