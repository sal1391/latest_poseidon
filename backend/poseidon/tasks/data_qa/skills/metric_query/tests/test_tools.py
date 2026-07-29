"""Offline, hand-authored contract tests for the skill's two pure tools.

``build_spec`` and ``format_parts`` never touch a database - every fixture
below is authored directly (no generator, no Postgres), so these tests pin
the mapping/rendering CONTRACT byte-for-byte: exact spec fields, exact part
dicts, exact proof lists. The pg-marked ``test_skill.py`` alongside this
module is what proves the same contract holds end to end against real
seeded data, through the registry.
"""

import datetime as dt

from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult, BreakdownRow, MetricResult, PeriodRange
from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.tasks._shared.fragments import DimFilter, PeriodArg
from poseidon.tasks.data_qa.skills.metric_query.schema import Args
from poseidon.tasks.data_qa.skills.metric_query.tools.build_spec import build_spec
from poseidon.tasks.data_qa.skills.metric_query.tools.format_parts import format_parts

SALES = "MARINE_SALES_PLANNING_V"
APRIL = PeriodWindow(dt.date(2026, 4, 1), dt.date(2026, 5, 1))
PRIOR_APRIL = PeriodWindow(dt.date(2025, 4, 1), dt.date(2025, 5, 1))

# Present-and-non-blank is all Settings asks of these two fields; nothing in
# this module connects anywhere.
SETTINGS = Settings(
    _env_file=None,
    database_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/void",
    s3_bucket="poseidon-artifacts",
)


def _args(**overrides: object) -> Args:
    defaults: dict[str, object] = {
        "entity": SALES,
        "metrics": ["GP", "VOLUME"],
        "period": PeriodArg(start=dt.date(2026, 4, 1), end=dt.date(2026, 5, 1)),
    }
    defaults.update(overrides)
    return Args(**defaults)


# ---------------------------------------------------------------------------
# build_spec
# ---------------------------------------------------------------------------


def test_build_spec_plain_metric_query_maps_field_for_field():
    args = _args(filters=[DimFilter(column="LOC_NM", values=["Singapore"])])

    spec = build_spec(args)

    assert spec == MetricQuerySpec(
        entity=SALES,
        metrics=("GP", "VOLUME"),
        period=APRIL,
        filters={"LOC_NM": ("Singapore",)},
    )


def test_build_spec_breakdown_orders_by_first_metric():
    args = _args(metrics=["GP", "VOLUME", "MARGIN"], group_by="CUST_NM", top_n=5)

    spec = build_spec(args)

    assert spec == BreakdownQuerySpec(
        entity=SALES,
        metrics=("GP", "VOLUME", "MARGIN"),
        period=APRIL,
        group_by="CUST_NM",
        order_by_metric="GP",
        top_n=5,
        filters={},
    )


def test_build_spec_multiple_filters_become_a_tuple_mapping():
    args = _args(
        filters=[
            DimFilter(column="LOC_NM", values=["Singapore", "Rotterdam"]),
            DimFilter(column="CUST_NM", values=["Acme Shipping"]),
        ]
    )

    spec = build_spec(args)

    assert spec.filters == {
        "LOC_NM": ("Singapore", "Rotterdam"),
        "CUST_NM": ("Acme Shipping",),
    }


def test_build_spec_no_filters_is_an_empty_mapping():
    assert build_spec(_args()).filters == {}


def test_build_spec_ignores_compare_period_and_maps_the_primary_window():
    """``build_spec`` only ever reads ``args.period`` - ``compare_period`` is
    ``skill.run``'s signal to call it again on a period-swapped copy, not
    something this function branches on itself."""
    args = _args(compare_period=PeriodArg(start=dt.date(2025, 4, 1), end=dt.date(2025, 5, 1)))

    spec = build_spec(args)

    assert spec == MetricQuerySpec(entity=SALES, metrics=("GP", "VOLUME"), period=APRIL, filters={})


def test_build_spec_over_a_swapped_compare_period_maps_it_as_the_window():
    """The mechanism ``skill.run`` actually uses for a comparison: swap
    ``period`` for ``compare_period`` and call ``build_spec`` again."""
    args = _args(compare_period=PeriodArg(start=dt.date(2025, 4, 1), end=dt.date(2025, 5, 1)))
    compare_args = args.model_copy(update={"period": args.compare_period})

    spec = build_spec(compare_args)

    assert spec == MetricQuerySpec(
        entity=SALES, metrics=("GP", "VOLUME"), period=PRIOR_APRIL, filters={}
    )


# ---------------------------------------------------------------------------
# Args validation: breakdown + compare_period is rejected, through dispatch
# ---------------------------------------------------------------------------


class _UnreachableDataClient:
    """A ``DataClient`` stand-in whose every method fails loudly.

    ``Args`` validation must reject the group_by/compare_period combination
    before ``dispatch`` ever calls the skill, so none of these should run -
    if one does, that is a bug in the validator (or in this test), not a
    reason to require a real data client.
    """

    _MESSAGE = "compare_period + group_by must 422 at argument validation, before the data layer"

    def list_dimension_values(self, *args: object, **kwargs: object) -> list[str]:
        raise AssertionError(self._MESSAGE)

    def available_periods(self, *args: object, **kwargs: object) -> PeriodRange:
        raise AssertionError(self._MESSAGE)

    def run_metric_query(self, *args: object, **kwargs: object) -> MetricResult:
        raise AssertionError(self._MESSAGE)

    def run_breakdown_query(self, *args: object, **kwargs: object) -> BreakdownResult:
        raise AssertionError(self._MESSAGE)


def test_dispatch_rejects_breakdown_combined_with_compare_period():
    """``format_parts`` has no "breakdown + comparison" shape (see its module
    docstring), so ``Args`` itself rejects the combination - a structured 422
    the router can read and correct itself from, rather than a breakdown that
    silently answers a different question than the one asked."""
    reg = SkillRegistry.discover()
    ctx = SkillContext(data=_UnreachableDataClient(), artifacts=None, settings=SETTINGS)

    res = reg.dispatch(
        "data_qa.metric_query",
        {
            "entity": SALES,
            "metrics": ["GP"],
            "period": {"start": "2026-04-01", "end": "2026-05-01"},
            "compare_period": {"start": "2025-04-01", "end": "2025-05-01"},
            "group_by": "CUST_NM",
        },
        ctx,
    )

    assert res.ok is False
    assert res.error["status"] == 422
    assert (
        "compare_period is not supported with group_by — run the breakdown "
        "once per period instead"
    ) in res.error["detail"]


# ---------------------------------------------------------------------------
# format_parts - breakdown
# ---------------------------------------------------------------------------

_BREAKDOWN_SPEC = BreakdownQuerySpec(
    entity=SALES,
    metrics=("GP", "VOLUME", "MARGIN"),
    period=APRIL,
    group_by="CUST_NM",
    order_by_metric="GP",
    top_n=5,
    filters={"LOC_NM": ("Singapore",)},
)

# The three phase-1 demo customers (profiles.yml's named, forced-Singapore
# customers) - hand-authored values, not pulled from any generator or DB.
# Crestline Freight is an all-lost group (no won fixtures): GP and VOLUME are
# both legitimately 0, and MARGIN is NULL (NULLIF guards the 0/0 divide) -
# exercising the "a real row can still carry a None cell" case.
_BREAKDOWN_RESULT = BreakdownResult(
    entity=SALES,
    group_by="CUST_NM",
    rows=[
        BreakdownRow(
            key="Northstar Lines", values={"GP": 125350.6, "VOLUME": 2504.3, "MARGIN": 50.037}
        ),
        BreakdownRow(
            key="Blue Anchor Marine", values={"GP": 98200.2, "VOLUME": 1980.9, "MARGIN": 49.566}
        ),
        BreakdownRow(key="Crestline Freight", values={"GP": 0.0, "VOLUME": 0.0, "MARGIN": None}),
    ],
)


def test_format_parts_breakdown_table_and_proof():
    parts, proof = format_parts(_BREAKDOWN_SPEC, _BREAKDOWN_RESULT, SETTINGS)

    assert parts == [
        {
            "kind": "table",
            "payload": {
                "columns": ["Customer", "Gross Profit", "Volume", "Margin"],
                "rows": [
                    ["Northstar Lines", 125351, 2504, 50.04],
                    ["Blue Anchor Marine", 98200, 1981, 49.57],
                    ["Crestline Freight", 0, 0, None],
                ],
            },
        }
    ]
    assert proof == [
        "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
        "Backend: synthetic",
        "Period: 2026-04-01..2026-05-01",
        "Filters: LOC_NM IN (Singapore)",
        "Group by: CUST_NM (top 5)",
        "Rows: 3",
    ]


def test_format_parts_breakdown_multi_column_filters_are_joined_with_and():
    spec = BreakdownQuerySpec(
        entity=SALES,
        metrics=("GP",),
        period=APRIL,
        group_by="CUST_NM",
        order_by_metric="GP",
        filters={"LOC_NM": ("Singapore", "Rotterdam"), "CUST_NM": ("Acme Shipping",)},
    )
    result = BreakdownResult(
        entity=SALES,
        group_by="CUST_NM",
        rows=[BreakdownRow(key="Acme Shipping", values={"GP": 100.0})],
    )

    _, proof = format_parts(spec, result, SETTINGS)

    assert "Filters: LOC_NM IN (Singapore, Rotterdam) AND CUST_NM IN (Acme Shipping)" in proof


def test_format_parts_breakdown_zero_rows_is_no_data():
    empty_result = BreakdownResult(entity=SALES, group_by="CUST_NM", rows=[])

    parts, proof = format_parts(_BREAKDOWN_SPEC, empty_result, SETTINGS)

    assert parts == [{"kind": "text", "payload": {"markdown": "No data for this selection."}}]
    assert proof == [
        "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
        "Backend: synthetic",
        "Period: 2026-04-01..2026-05-01",
        "Filters: LOC_NM IN (Singapore)",
        "Group by: CUST_NM (top 5)",
        "Result: empty",
    ]


# ---------------------------------------------------------------------------
# format_parts - metric, no compare
# ---------------------------------------------------------------------------

_METRIC_SPEC = MetricQuerySpec(entity=SALES, metrics=("GP", "VOLUME"), period=APRIL, filters={})


def test_format_parts_metric_without_compare():
    result = MetricResult(entity=SALES, period=APRIL, values={"GP": 204000.4, "VOLUME": 4102.6})

    parts, proof = format_parts(_METRIC_SPEC, result, SETTINGS)

    assert parts == [
        {
            "kind": "table",
            "payload": {
                "columns": ["Metric", "Value"],
                "rows": [["Gross Profit", 204000], ["Volume", 4103]],
            },
        }
    ]
    assert proof == [
        "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
        "Backend: synthetic",
        "Period: 2026-04-01..2026-05-01",
        "Filters: none",
        "Metrics: 2 values",
    ]


def test_format_parts_metric_all_none_is_no_data():
    result = MetricResult(entity=SALES, period=APRIL, values={"GP": None, "VOLUME": None})

    parts, proof = format_parts(_METRIC_SPEC, result, SETTINGS)

    assert parts == [{"kind": "text", "payload": {"markdown": "No data for this selection."}}]
    assert proof == [
        "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
        "Backend: synthetic",
        "Period: 2026-04-01..2026-05-01",
        "Filters: none",
        "Result: empty",
    ]


# ---------------------------------------------------------------------------
# format_parts - metric, WITH compare
# ---------------------------------------------------------------------------


def test_format_parts_metric_with_compare_builds_metric_grid():
    result = MetricResult(entity=SALES, period=APRIL, values={"GP": 204000.4, "VOLUME": 4102.6})
    compare_result = MetricResult(
        entity=SALES, period=PRIOR_APRIL, values={"GP": 180500.9, "VOLUME": 3900.2}
    )

    parts, proof = format_parts(
        _METRIC_SPEC, result, SETTINGS, compare_period=PRIOR_APRIL, compare_result=compare_result
    )

    assert parts == [
        {
            "kind": "metric_grid",
            "payload": {
                "periods": {
                    "a": {"start": "2026-04-01", "end": "2026-05-01"},
                    "b": {"start": "2025-04-01", "end": "2025-05-01"},
                },
                "metrics": [
                    {
                        "name": "GP",
                        "friendly": "Gross Profit",
                        "a": 204000,
                        "b": 180501,
                        "unit": "USD",
                    },
                    {
                        "name": "VOLUME",
                        "friendly": "Volume",
                        "a": 4103,
                        "b": 3900,
                        "unit": "tons",
                    },
                ],
            },
        }
    ]
    assert proof == [
        "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
        "Backend: synthetic",
        "Period: 2026-04-01..2026-05-01",
        "Compare: 2025-04-01..2025-05-01",
        "Filters: none",
        "Metrics: 2 values",
    ]


def test_format_parts_compare_both_sides_empty_is_no_data():
    result = MetricResult(entity=SALES, period=APRIL, values={"GP": None, "VOLUME": None})
    compare_result = MetricResult(
        entity=SALES, period=PRIOR_APRIL, values={"GP": None, "VOLUME": None}
    )

    parts, proof = format_parts(
        _METRIC_SPEC, result, SETTINGS, compare_period=PRIOR_APRIL, compare_result=compare_result
    )

    assert parts == [{"kind": "text", "payload": {"markdown": "No data for this selection."}}]
    assert proof[-1] == "Result: empty"


def test_format_parts_compare_one_side_empty_is_still_a_real_answer():
    """Only one period being empty is a partial answer, not "no data" - the
    other period is real and must not be hidden behind a no-data message."""
    result = MetricResult(entity=SALES, period=APRIL, values={"GP": 204000.4, "VOLUME": 4102.6})
    compare_result = MetricResult(
        entity=SALES, period=PRIOR_APRIL, values={"GP": None, "VOLUME": None}
    )

    parts, proof = format_parts(
        _METRIC_SPEC, result, SETTINGS, compare_period=PRIOR_APRIL, compare_result=compare_result
    )

    assert parts[0]["kind"] == "metric_grid"
    gp_entry = next(m for m in parts[0]["payload"]["metrics"] if m["name"] == "GP")
    assert gp_entry["a"] == 204000
    assert gp_entry["b"] is None
    assert proof[-1] == "Metrics: 2 values"


# ---------------------------------------------------------------------------
# friendly-name display overrides (MARGIN/WIN_RATE/NUM_LOST)
# ---------------------------------------------------------------------------


def test_format_parts_margin_and_win_rate_use_the_display_override():
    """MARGIN's first depends_on column (GROSS_PROFIT) describes an INPUT to
    the ratio, not the ratio itself - using its friendly ("Gross Profit")
    would misname a USD/ton figure. Both are `kind: ratio`, so the general
    rule (kind == "sum" only) would already skip the column lookup and
    title-case their own name instead - _DISPLAY_OVERRIDES pins that same
    text explicitly rather than leaving it to coincide with the generic
    fallback. Values still round to 2dp."""
    spec = MetricQuerySpec(entity=SALES, metrics=("MARGIN", "WIN_RATE"), period=APRIL, filters={})
    result = MetricResult(entity=SALES, period=APRIL, values={"MARGIN": 50.037, "WIN_RATE": 0.612})

    parts, _ = format_parts(spec, result, SETTINGS)

    assert parts[0]["payload"]["rows"] == [["Margin", 50.04], ["Win Rate", 0.61]]


def test_format_parts_num_lost_and_num_inquiries_have_distinct_friendlies():
    """Regression: NUM_LOST (`kind: derived`) and NUM_INQUIRIES (`kind: sum`)
    both name "#_INQUIRIES" as their FIRST depends_on column. Before
    _DISPLAY_OVERRIDES existed, the plain "first depends_on column" rule gave
    both metrics the identical friendly "# Inquiries" - a real collision that
    would print two different numbers under one header in the same table.
    NUM_LOST is display-overridden to "# Lost"; NUM_INQUIRIES is unaffected
    (kind == "sum", so it still legitimately borrows the column's friendly)."""
    spec = MetricQuerySpec(
        entity=SALES, metrics=("NUM_INQUIRIES", "NUM_LOST"), period=APRIL, filters={}
    )
    result = MetricResult(
        entity=SALES, period=APRIL, values={"NUM_INQUIRIES": 120.0, "NUM_LOST": 45.0}
    )

    parts, _ = format_parts(spec, result, SETTINGS)

    rows = parts[0]["payload"]["rows"]
    assert rows == [["# Inquiries", 120], ["# Lost", 45]]
    labels = [row[0] for row in rows]
    assert len(set(labels)) == len(labels), "the two metrics must not share a label"
