"""Ground-truth integration tests for :class:`SyntheticDataClient` (Postgres).

These are the only tests in the suite that touch a real database, so they are
all marked ``@pytest.mark.pg`` (registered in ``backend/pyproject.toml``) and
run only when ``DATABASE_URL`` points at a reachable, already-seeded Postgres:

```bash
docker compose -f infra/docker-compose.yml up -d db
cd backend
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \\
  python -m alembic upgrade head
DATABASE_URL=... python -m poseidon.scripts.seed_synthetic
DATABASE_URL=... python -m pytest -m pg
```

The module deliberately does NOT seed anything: it reads whatever
``poseidon.scripts.seed_synthetic`` already loaded (the committed default
``SEED``), exactly as the compose backend container does at start-up. Three
module-level guards keep the offline suite green and the failure modes
legible — no ``DATABASE_URL``, an unreachable/unmigrated database (2-second
connect timeout), or an empty ``synthetic`` schema each SKIP with an
actionable reason rather than erroring.

**The point of this module** is that every expectation below is recomputed in
pure Python from ``generate(seed=SEED)`` — the same deterministic generator
the seeder fed to Postgres — and compared against what the certified SQL
actually returns. Nothing is pinned to a hand-copied number, so the assertions
verify the whole chain end to end: generator -> seed loader -> ``synthetic``
schema -> ``query_builder`` SQL -> ``SyntheticDataClient`` result shaping.
Floats are compared with :func:`pytest.approx` because SQL ``SUM()`` and
Python ``sum()`` accumulate in different orders.
"""

import datetime as dt
import os

import psycopg
import pytest

from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.data.synthetic_client import SyntheticDataClient, normalize_dsn
from poseidon.scripts.generate_synthetic import generate

pytestmark = pytest.mark.pg

# The seed the loader committed to (``profiles.yml``'s ``seed_default``); the
# seeder's ``--seed`` flag exists for experiments, but the seeded database
# these tests read must have been loaded with this one.
SEED = 1391
CONNECT_TIMEOUT_SECONDS = 2

SALES = "MARINE_SALES_PLANNING_V"
GL = "W_MARINE_GL_SOURCE_AI"

APRIL_2026 = PeriodWindow(dt.date(2026, 4, 1), dt.date(2026, 5, 1))
PRIOR_YEAR = PeriodWindow(dt.date(2025, 1, 1), dt.date(2026, 1, 1))
YTD_2026 = PeriodWindow(dt.date(2026, 1, 1), dt.date(2026, 7, 1))

# Skip reasons are ASCII-only on purpose: pytest prints them straight to a
# console that may well be Windows cp1252, where an em dash becomes "?".
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_SEED_HINT = "seed it with `python -m poseidon.scripts.seed_synthetic`"

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        f"DATABASE_URL is not set - pg integration tests need a Postgres: {_UP_HINT}, "
        f"migrate it with `python -m alembic upgrade head`, {_SEED_HINT}",
        allow_module_level=True,
    )

try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute("SELECT COUNT(*) FROM synthetic.marine_sales_planning_v")
            SEEDED_SALES_ROWS = _cur.fetchone()[0]
            _cur.execute("SELECT COUNT(*) FROM synthetic.w_marine_gl_source_ai")
            SEEDED_GL_ROWS = _cur.fetchone()[0]
except Exception as exc:  # noqa: BLE001 - any connect/lookup failure means "not available"
    pytest.skip(
        f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT} and migrate it with "
        "`python -m alembic upgrade head`",
        allow_module_level=True,
    )

if SEEDED_SALES_ROWS == 0:
    pytest.skip(
        f"synthetic.marine_sales_planning_v is empty - {_SEED_HINT}",
        allow_module_level=True,
    )

DATASET = generate(seed=SEED)
CLIENT = SyntheticDataClient(_DSN, connect_timeout=CONNECT_TIMEOUT_SECONDS)


# ---------------------------------------------------------------------------
# pure-Python ground truth (no SQL, no database)
# ---------------------------------------------------------------------------


def sales_in(window: PeriodWindow, **equals: str) -> list[dict]:
    """Generated sales rows inside the half-open ``window``, optionally
    narrowed to exact column values (the pure-Python twin of a spec filter)."""
    return [
        row
        for row in DATASET.sales_rows
        if window.start <= row["LIFT_ETA_DATE"] < window.end
        and all(row[col] == value for col, value in equals.items())
    ]


def six_metrics(rows: list[dict]) -> dict[str, float]:
    """VOLUME/GP/MARGIN/NUM_WON/NUM_INQUIRIES/NUM_LOST over ``rows``, computed
    the way the certified metric SQL defines them (MARGIN is a ratio of the
    aggregates, never an average of per-row margins)."""
    volume = sum(row["FIXED_TONS"] for row in rows)
    gp = sum(row["GROSS_PROFIT"] for row in rows)
    won = sum(row["#_FIXTURES"] for row in rows)
    inquiries = sum(row["#_INQUIRIES"] for row in rows)
    return {
        "VOLUME": volume,
        "GP": gp,
        "MARGIN": gp / volume if volume else None,
        "NUM_WON": won,
        "NUM_INQUIRIES": inquiries,
        "NUM_LOST": inquiries - won,
    }


def gp_by_customer(rows: list[dict]) -> list[tuple[str, float]]:
    """``[(customer, gp), ...]`` sorted by GP descending — the pure-Python twin
    of ``GROUP BY CUST_NM ... ORDER BY "GP" DESC``."""
    totals: dict[str, float] = {}
    for row in rows:
        totals[row["CUST_NM"]] = totals.get(row["CUST_NM"], 0.0) + row["GROSS_PROFIT"]
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


# ---------------------------------------------------------------------------
# the seeded database is the generated dataset
# ---------------------------------------------------------------------------


def test_seeded_row_counts_match_the_generator():
    assert SEEDED_SALES_ROWS == len(DATASET.sales_rows)
    assert SEEDED_GL_ROWS == len(DATASET.gl_rows)


# ---------------------------------------------------------------------------
# six certified metrics — SQL vs pure Python, on all three demo windows
# ---------------------------------------------------------------------------

SIX_METRICS = ("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST")


@pytest.mark.parametrize(
    "window",
    [APRIL_2026, PRIOR_YEAR, YTD_2026],
    ids=["april-2026", "prior-year-2025", "ytd-2026"],
)
def test_six_metrics_match_python_ground_truth(window: PeriodWindow):
    rows = sales_in(window)
    expected = six_metrics(rows)
    assert rows, "the generated window must contain rows for this test to mean anything"

    result = CLIENT.run_metric_query(
        MetricQuerySpec(entity=SALES, metrics=SIX_METRICS, period=window)
    )

    assert result.entity == SALES
    assert result.period == window
    assert set(result.values) == set(SIX_METRICS)
    for metric in SIX_METRICS:
        assert result.values[metric] == pytest.approx(expected[metric]), metric


def test_six_metric_summary_prior_year_vs_ytd_differ():
    """Guards against the window filter silently degrading to "all rows"."""
    prior = CLIENT.run_metric_query(
        MetricQuerySpec(entity=SALES, metrics=SIX_METRICS, period=PRIOR_YEAR)
    )
    ytd = CLIENT.run_metric_query(
        MetricQuerySpec(entity=SALES, metrics=SIX_METRICS, period=YTD_2026)
    )
    assert prior.values["NUM_INQUIRIES"] != ytd.values["NUM_INQUIRIES"]
    assert prior.values["NUM_INQUIRIES"] + ytd.values["NUM_INQUIRIES"] == pytest.approx(
        float(len(DATASET.sales_rows))
    )


def test_metric_query_filtered_to_singapore_matches_python_ground_truth():
    expected = six_metrics(sales_in(APRIL_2026, LOC_NM="Singapore"))

    result = CLIENT.run_metric_query(
        MetricQuerySpec(
            entity=SALES,
            metrics=SIX_METRICS,
            period=APRIL_2026,
            filters={"LOC_NM": ("Singapore",)},
        )
    )

    for metric in SIX_METRICS:
        assert result.values[metric] == pytest.approx(expected[metric]), metric


def test_metric_query_over_an_empty_window_returns_nulls():
    """SUM() over zero rows is SQL NULL — the client surfaces None, not 0.0."""
    empty = PeriodWindow(dt.date(2019, 1, 1), dt.date(2019, 2, 1))
    assert sales_in(empty) == []

    result = CLIENT.run_metric_query(
        MetricQuerySpec(entity=SALES, metrics=SIX_METRICS, period=empty)
    )
    assert result.values == dict.fromkeys(SIX_METRICS)


# ---------------------------------------------------------------------------
# breakdown — top 5 customers by GP, Port of Singapore, April 2026
# ---------------------------------------------------------------------------


def test_singapore_top5_by_gp_matches_python_groupby():
    rows = sales_in(APRIL_2026, LOC_NM="Singapore")
    expected = gp_by_customer(rows)[:5]
    assert len(expected) == 5, "April 2026 Singapore must have >=5 distinct customers"
    gps = [gp for _, gp in expected]
    assert len(set(gps)) == len(gps), "ties would make the SQL ordering ambiguous"

    result = CLIENT.run_breakdown_query(
        BreakdownQuerySpec(
            entity=SALES,
            metrics=("GP", "VOLUME", "MARGIN"),
            period=APRIL_2026,
            group_by="CUST_NM",
            order_by_metric="GP",
            top_n=5,
            filters={"LOC_NM": ("Singapore",)},
        )
    )

    assert result.entity == SALES
    assert result.group_by == "CUST_NM"
    # same top-5 keys, in the same order
    assert [row.key for row in result.rows] == [name for name, _ in expected]
    # same values, per customer, recomputed independently in Python
    for row, (name, gp) in zip(result.rows, expected, strict=True):
        per_customer = [r for r in rows if r["CUST_NM"] == name]
        assert row.values["GP"] == pytest.approx(gp), name
        assert row.values["VOLUME"] == pytest.approx(
            sum(r["FIXED_TONS"] for r in per_customer)
        ), name
        assert row.values["MARGIN"] == pytest.approx(
            gp / sum(r["FIXED_TONS"] for r in per_customer)
        ), name


def test_named_demo_customers_appear_in_singapore_results():
    """Phase-1 mock continuity: the three named customers carry a forced
    Singapore port affinity (see ``profiles.yml``), so a Singapore breakdown
    wide enough to hold them must actually hold them."""
    result = CLIENT.run_breakdown_query(
        BreakdownQuerySpec(
            entity=SALES,
            metrics=("GP",),
            period=YTD_2026,
            group_by="CUST_NM",
            order_by_metric="GP",
            top_n=40,
            filters={"LOC_NM": ("Singapore",)},
        )
    )
    keys = {row.key for row in result.rows}
    assert {"Northstar Lines", "Blue Anchor Marine", "Crestline Freight"} <= keys


# ---------------------------------------------------------------------------
# dimension values
# ---------------------------------------------------------------------------


def test_list_dimension_values_search_is_case_insensitive_substring():
    expected = sorted(
        {row["CUST_NM"] for row in DATASET.sales_rows if "north" in row["CUST_NM"].lower()}
    )

    values = CLIENT.list_dimension_values(SALES, "CUST_NM", search="north")

    assert "Northstar Lines" in values
    assert values == expected


def test_list_dimension_values_returns_every_distinct_value_ordered():
    """Comparing against Python's ``sorted`` is safe for this data even under
    the compose image's ``en_US.utf8`` collation (which ignores spaces and
    punctuation at the primary level, unlike Python): every generated value is
    Title Case and every pair differs at a letter, never at a space-vs-letter
    position. A future profile that broke that would fail here, loudly."""
    expected = {row["CUST_NM"] for row in DATASET.sales_rows}

    values = CLIENT.list_dimension_values(SALES, "CUST_NM")

    assert set(values) == expected
    assert len(values) == len(set(values))
    assert values == sorted(values)


def test_list_dimension_values_caps_at_200():
    """ACCOUNT is the widest synthetic dimension (hundreds of leaf codes)."""
    distinct = {row["ACCOUNT"] for row in DATASET.gl_rows}
    assert len(distinct) > 200, "this test needs a dimension wider than the cap"

    values = CLIENT.list_dimension_values(GL, "ACCOUNT")

    assert len(values) == 200
    assert values == sorted(distinct)[:200]


# ---------------------------------------------------------------------------
# available periods
# ---------------------------------------------------------------------------


def test_available_periods_match_the_generated_profile_window():
    lift_dates = [row["LIFT_ETA_DATE"] for row in DATASET.sales_rows]

    period_range = CLIENT.available_periods(SALES)

    assert period_range.start == min(lift_dates)
    assert period_range.end == max(lift_dates)
    # profiles.yml's rolling window: prior full calendar year + YTD to the anchor
    assert dt.date(2025, 1, 1) <= period_range.start
    assert period_range.end < dt.date(2026, 7, 1)


def test_available_periods_for_gl_are_first_of_month():
    period_dates = [dt.date.fromisoformat(row["PERIOD_DATE"]) for row in DATASET.gl_rows]

    period_range = CLIENT.available_periods(GL)

    assert period_range.start == min(period_dates) == dt.date(2025, 1, 1)
    assert period_range.end == max(period_dates) == dt.date(2026, 6, 1)


# ---------------------------------------------------------------------------
# GL dual-purpose guard, end to end
# ---------------------------------------------------------------------------


def test_gl_monetary_total_excludes_volume_rows():
    """MONETARY_TOTAL must equal the python sum over NON-Volume rows only —
    if the ontology's ``COALESCE(CLASS4,'') <> 'Volume'`` guard were dropped,
    tonnage would leak into the dollar sum and this would fail loudly."""
    april = "2026-04-01"
    month_rows = [row for row in DATASET.gl_rows if row["PERIOD_DATE"] == april]
    assert month_rows
    expected = sum(row["AMOUNT_USD"] for row in month_rows if row["CLASS4"] != "Volume")
    with_volume = sum(row["AMOUNT_USD"] for row in month_rows)
    assert expected != pytest.approx(with_volume), "the month must contain Volume rows"

    result = CLIENT.run_metric_query(
        MetricQuerySpec(entity=GL, metrics=("MONETARY_TOTAL",), period=APRIL_2026)
    )

    assert result.values["MONETARY_TOTAL"] == pytest.approx(expected)


def test_gl_class1_breakdown_buckets_nulls_as_unassigned():
    """The certified NULL placeholder, end to end. CLASS1 is NULL on ~77% of
    generated GL rows (profiles.yml populates it on 5 of 20 non-Volume
    paths, mirroring the certified column's ~76%-null character), and the GL
    entity's ``null_placeholder`` is 'Unassigned' — so the SQL's
    ``COALESCE(CLASS1, 'Unassigned')`` must fold every one of those NULLs
    into a single 'Unassigned' bucket alongside the named leaves. The
    expectation is the pure-Python twin of exactly that COALESCE. Volume
    rows are excluded because MONETARY_TOTAL carries the dual-purpose
    guard. The COMPANY filter is narrowed to one real company so the changed
    filter-side COALESCE executes against Postgres too, not just the
    projection."""
    april = "2026-04-01"
    company = sorted({row["COMPANY"] for row in DATASET.gl_rows})[0]
    month_rows = [
        row
        for row in DATASET.gl_rows
        if row["PERIOD_DATE"] == april
        and row["CLASS4"] != "Volume"
        and row["COMPANY"] == company
    ]
    assert month_rows
    expected: dict[str, float] = {}
    for row in month_rows:
        key = row["CLASS1"] if row["CLASS1"] is not None else "Unassigned"
        expected[key] = expected.get(key, 0.0) + row["AMOUNT_USD"]
    named_leaves = set(expected) - {"Unassigned"}
    assert "Unassigned" in expected, "the month must contain CLASS1-null rows"
    assert named_leaves, "the month must contain at least one named CLASS1 leaf"

    result = CLIENT.run_breakdown_query(
        BreakdownQuerySpec(
            entity=GL,
            metrics=("MONETARY_TOTAL",),
            period=APRIL_2026,
            group_by="CLASS1",
            order_by_metric="MONETARY_TOTAL",
            top_n=50,  # comfortably above the number of distinct CLASS1 values
            filters={"COMPANY": (company,)},
        )
    )

    assert result.group_by == "CLASS1"
    keys = [row.key for row in result.rows]
    assert set(keys) == set(expected)
    assert "Unassigned" in keys
    assert named_leaves <= set(keys)
    for row in result.rows:
        assert row.values["MONETARY_TOTAL"] == pytest.approx(expected[row.key]), row.key
    # ORDER BY "MONETARY_TOTAL" DESC, not incidental table order
    totals = [row.values["MONETARY_TOTAL"] for row in result.rows]
    assert totals == sorted(totals, reverse=True)


def test_gl_volume_mode_breakdown_sums_tonnage_by_class3():
    """The mirror image of the guard: pinning CLASS4='Volume' drops the
    exclusion and the CLASS3 breakdown returns tonnage, not dollars."""
    april = "2026-04-01"
    volume_rows = [
        row
        for row in DATASET.gl_rows
        if row["PERIOD_DATE"] == april and row["CLASS4"] == "Volume"
    ]
    totals: dict[str, float] = {}
    for row in volume_rows:
        totals[row["CLASS3"]] = totals.get(row["CLASS3"], 0.0) + row["AMOUNT_USD"]
    expected = sorted(totals.items(), key=lambda item: item[1], reverse=True)

    result = CLIENT.run_breakdown_query(
        BreakdownQuerySpec(
            entity=GL,
            metrics=("MONETARY_TOTAL",),
            period=APRIL_2026,
            group_by="CLASS3",
            order_by_metric="MONETARY_TOTAL",
            top_n=10,
            filters={"CLASS4": ("Volume",)},
        )
    )

    assert [row.key for row in result.rows] == [name for name, _ in expected]
    for row, (name, total) in zip(result.rows, expected, strict=True):
        assert row.values["MONETARY_TOTAL"] == pytest.approx(total), name
