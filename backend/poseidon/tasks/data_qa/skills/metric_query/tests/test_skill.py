"""Ground-truth tests for ``data_qa.metric_query``, dispatched through the
real :class:`~poseidon.core.skills.registry.SkillRegistry` against the
seeded Postgres.

Same discipline as ``backend/tests/test_synthetic_client_pg.py``: marked
``@pytest.mark.pg``, skipped (never failed) when ``DATABASE_URL`` is unset,
the database is unreachable, or the ``synthetic`` schema is unseeded, and
every expected number is recomputed in pure Python from
``generate(seed=SEED)`` - the same deterministic generator the seeder fed to
Postgres. Nothing here is pinned to a hand-copied figure; the hand-authored
fixtures live in ``test_tools.py`` instead.

```bash
docker compose -f infra/docker-compose.yml up -d db
cd backend
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \\
  python -m alembic upgrade head
DATABASE_URL=... python -m poseidon.scripts.seed_synthetic
DATABASE_URL=... python -m pytest -m pg
```
"""

import datetime as dt
import os

import psycopg
import pytest

from poseidon.core.config import Settings
from poseidon.core.data.synthetic_client import SyntheticDataClient, normalize_dsn
from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.scripts.generate_synthetic import generate

pytestmark = pytest.mark.pg

# The seed the loader committed to (``profiles.yml``'s ``seed_default``); see
# ``test_synthetic_client_pg.py`` for why this must match whatever seeded the
# database these tests read.
SEED = 1391
CONNECT_TIMEOUT_SECONDS = 2

SALES = "MARINE_SALES_PLANNING_V"
APRIL_START, APRIL_END = dt.date(2026, 4, 1), dt.date(2026, 5, 1)

# Present-and-non-blank is all Settings needs; the skill talks to Postgres
# only through ``ctx.data``, never through this DSN.
_SETTINGS_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

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
except Exception as exc:  # noqa: BLE001 - any connect/lookup failure means "not available"
    pytest.skip(
        f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT} and migrate it with "
        "`python -m alembic upgrade head`",
        allow_module_level=True,
    )

if SEEDED_SALES_ROWS == 0:
    pytest.skip(
        f"synthetic.marine_sales_planning_v is empty - {_SEED_HINT}", allow_module_level=True
    )

DATASET = generate(seed=SEED)
REGISTRY = SkillRegistry.discover()


def _ctx() -> SkillContext:
    return SkillContext(
        data=SyntheticDataClient(_DSN, connect_timeout=CONNECT_TIMEOUT_SECONDS),
        artifacts=None,
        settings=Settings(
            _env_file=None, database_url=_SETTINGS_DSN, s3_bucket="poseidon-artifacts"
        ),
    )


def sales_in(start: dt.date, end: dt.date, **equals: str) -> list[dict]:
    """Generated sales rows inside the half-open window, optionally narrowed
    to exact column values - the pure-Python twin of a spec filter."""
    return [
        row
        for row in DATASET.sales_rows
        if start <= row["LIFT_ETA_DATE"] < end
        and all(row[col] == value for col, value in equals.items())
    ]


def gp_by_customer(rows: list[dict]) -> list[tuple[str, float]]:
    """``[(customer, gp), ...]`` sorted by GP descending - the pure-Python
    twin of ``GROUP BY CUST_NM ... ORDER BY "GP" DESC``."""
    totals: dict[str, float] = {}
    for row in rows:
        totals[row["CUST_NM"]] = totals.get(row["CUST_NM"], 0.0) + row["GROSS_PROFIT"]
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


# ---------------------------------------------------------------------------
# breakdown - top 5 GP customers, Port of Singapore, April 2026
# ---------------------------------------------------------------------------


def test_singapore_top5_gp_breakdown_through_the_registry():
    rows = sales_in(APRIL_START, APRIL_END, LOC_NM="Singapore")
    expected = gp_by_customer(rows)[:5]
    assert len(expected) == 5, "April 2026 Singapore must have >=5 distinct customers"
    top_name, top_gp = expected[0]

    res = REGISTRY.dispatch(
        "data_qa.metric_query",
        {
            "entity": SALES,
            "metrics": ["GP", "VOLUME", "MARGIN"],
            "period": {"start": "2026-04-01", "end": "2026-05-01"},
            "filters": [{"column": "LOC_NM", "values": ["Singapore"]}],
            "group_by": "CUST_NM",
            "top_n": 5,
        },
        _ctx(),
    )

    assert res.ok is True, res.error
    assert len(res.parts) == 1
    table = res.parts[0]
    assert table["kind"] == "table"
    assert table["payload"]["columns"] == ["Customer", "Gross Profit", "Volume", "MARGIN"]
    assert len(table["payload"]["rows"]) == 5

    first_row = table["payload"]["rows"][0]
    assert first_row[0] == top_name  # the seed's true #1 by GP
    assert first_row[1] == pytest.approx(top_gp, abs=0.51)  # 0dp-rounded GP

    assert res.proof == [
        "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
        "Backend: synthetic",
        "Period: 2026-04-01..2026-05-01",
        "Filters: LOC_NM IN (Singapore)",
        "Group by: CUST_NM (top 5)",
        "Rows: 5",
    ]


# ---------------------------------------------------------------------------
# empty window - valid query, zero data
# ---------------------------------------------------------------------------


def test_empty_window_metric_query_returns_no_data_text():
    empty_start, empty_end = dt.date(2019, 1, 1), dt.date(2019, 2, 1)
    assert sales_in(empty_start, empty_end) == [], "this window must be outside the generated data"

    res = REGISTRY.dispatch(
        "data_qa.metric_query",
        {
            "entity": SALES,
            "metrics": ["GP", "VOLUME"],
            "period": {"start": "2019-01-01", "end": "2019-02-01"},
        },
        _ctx(),
    )

    assert res.ok is True, res.error
    assert res.parts == [{"kind": "text", "payload": {"markdown": "No data for this selection."}}]
    assert res.proof == [
        "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
        "Backend: synthetic",
        "Period: 2019-01-01..2019-02-01",
        "Filters: none",
        "Rows: 0",
    ]


# ---------------------------------------------------------------------------
# hallucinated dimension - certified error surfaces verbatim as a 422
# ---------------------------------------------------------------------------


def test_hallucinated_port_nm_filter_surfaces_the_certified_422():
    """PORT_NM is a documented hallucination (ontology.yml's
    negative_constraints: wrong=PORT_NM, right=LOC_NM) - not a certified
    dimension, so the query builder's own message must come back verbatim."""
    res = REGISTRY.dispatch(
        "data_qa.metric_query",
        {
            "entity": SALES,
            "metrics": ["GP"],
            "period": {"start": "2026-04-01", "end": "2026-05-01"},
            "filters": [{"column": "PORT_NM", "values": ["Singapore"]}],
        },
        _ctx(),
    )

    assert res.ok is False
    assert res.error["status"] == 422
    assert res.error["title"] == "invalid query"
    assert res.error["detail"] == "'PORT_NM' is not a dimension of MARINE_SALES_PLANNING_V"
