"""Tests for the dev-only skill runner (``poseidon.api.dev_runner``) and its
wiring into ``create_app``.

Local-mode-only surface: ``create_app`` includes ``dev_runner.router`` — and
builds ``app.state.skill_registry`` via ``SkillRegistry.discover()`` — only
when ``settings.deploy_mode == "local"`` (see ``poseidon/api/app.py`` and
``dev_runner``'s own module docstring for the "every response is HTTP 200,
failure is structured content" contract this endpoint mirrors from
``SkillRegistry.dispatch``).

Most tests here build a placeholder-DSN app that never touches a real
database — dispatch's own validation/unknown-skill/backend-guard paths all
return before ``ctx.data`` is ever called. The one ``@pytest.mark.pg`` test
at the bottom re-drives ``data_qa.metric_query``'s Singapore golden
(``poseidon/tasks/data_qa/skills/metric_query/tests/test_skill.py``) through
the real HTTP surface with the real ``DATABASE_URL``, and skips — never fails
— exactly the way that module does when Postgres is unset/unreachable/
unseeded. This file mixes offline and pg-marked tests, so (per the pattern in
``existing_customer_brief/tests/test_tools.py``) the pg guard cannot
module-skip; ``_require_pg()`` is called only from inside the one test that
needs it.
"""

import datetime as dt
import os

import httpx
import psycopg
import pytest

from poseidon.core.config import Settings
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.scripts.generate_synthetic import generate

SEED = 1391

# Present-and-non-blank is all Settings needs from a DSN it never actually
# connects with — the offline tests below never reach ctx.data.
_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

DEV_RUN_PATH_TEMPLATE = "/api/dev/skills/{skill_id}/run"


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        _env_file=None, database_url=_PLACEHOLDER_DSN, s3_bucket="poseidon-artifacts"
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _app(**overrides):
    from poseidon.api.app import create_app

    return create_app(_settings(**overrides))


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# habitat gating: local mounts the router, spcs/ec2 never do
# ---------------------------------------------------------------------------


def test_local_mode_app_exposes_the_dev_runner_route():
    """``create_app`` mounts ``dev_runner.router`` when ``deploy_mode`` is the
    default, ``"local"`` — checked via the public ``app.openapi()`` schema
    (stable across FastAPI's internal route-table representation) so this
    test stays independent of whatever the dispatch-behavior tests below
    assert about response bodies."""
    app = _app()
    assert DEV_RUN_PATH_TEMPLATE in app.openapi()["paths"]
    assert hasattr(app.state, "skill_registry")
    assert "data_qa.metric_query" in app.state.skill_registry.skill_ids


@pytest.mark.anyio
async def test_spcs_mode_app_returns_404_for_the_dev_runner_route():
    """The dev surface never exists outside local — an ``spcs`` app has no
    route mounted at all, so this is a genuine HTTP-level 404 (unlike every
    structured-error case below, which is a 200)."""
    app = _app(deploy_mode="spcs")
    assert not hasattr(app.state, "skill_registry")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post(
            DEV_RUN_PATH_TEMPLATE.format(skill_id="data_qa.metric_query"), json={}
        )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# dispatch surfaces: every failure is structured content inside a 200
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_invalid_args_return_200_with_a_structured_422():
    app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post(
            DEV_RUN_PATH_TEMPLATE.format(skill_id="data_qa.metric_query"),
            json={"nonsense": True},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["status"] == 422
    assert "metrics" in body["error"]["detail"] and "period" in body["error"]["detail"]
    assert body["parts"] == [] and body["proof"] == [] and body["artifacts"] == []


@pytest.mark.anyio
async def test_unknown_skill_returns_200_with_a_structured_404():
    app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post(DEV_RUN_PATH_TEMPLATE.format(skill_id="no.such_skill"), json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["status"] == 404
    assert "no.such_skill" in body["error"]["detail"]


@pytest.mark.anyio
async def test_non_synthetic_data_backend_returns_200_with_a_structured_501():
    """``data_backend="snowflake"`` has no adapter yet (Phase 15) — the
    runner must refuse with a structured 501 rather than crash building a
    client that doesn't exist, and — same "always 200" contract as every
    other dispatch failure — never as an HTTP-level error."""
    app = _app(data_backend="snowflake")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post(
            DEV_RUN_PATH_TEMPLATE.format(skill_id="data_qa.metric_query"),
            json={
                "metrics": ["GP"],
                "period": {"start": "2026-04-01", "end": "2026-05-01"},
            },
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["status"] == 501


# ---------------------------------------------------------------------------
# pg-marked ground truth: the Singapore breakdown through the real HTTP
# surface — same golden as test_skill.py, reused rather than re-derived
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT_SECONDS = 2

# Skip reasons are ASCII-only on purpose: pytest prints them straight to a
# console that may well be Windows cp1252, where an em dash becomes "?".
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_SEED_HINT = "seed it with `python -m poseidon.scripts.seed_synthetic`"


def _pg_skip_reason() -> str | None:
    """``None`` when a reachable, seeded Postgres is available for the
    ``@pytest.mark.pg`` test below; otherwise the reason it must skip.

    This module mixes offline and pg-marked tests, so — unlike
    ``test_skill.py``/``test_synthetic_client_pg.py`` (pg-only top to
    bottom) — this cannot call ``pytest.skip(allow_module_level=True)``:
    that would skip the offline tests above too. See
    ``existing_customer_brief/tests/test_tools.py`` for the same pattern.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return (
            "DATABASE_URL is not set - pg integration tests need a Postgres: "
            f"{_UP_HINT}, migrate it with `python -m alembic upgrade head`, {_SEED_HINT}"
        )
    try:
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM synthetic.marine_sales_planning_v")
                seeded_rows = cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001 - any connect/lookup failure means "not available"
        return (
            f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
            f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT} and migrate it with "
            "`python -m alembic upgrade head`"
        )
    if seeded_rows == 0:
        return f"synthetic.marine_sales_planning_v is empty - {_SEED_HINT}"
    return None


_DSN = os.environ.get("DATABASE_URL", "")
_PG_SKIP_REASON = _pg_skip_reason()
# Expensive-ish (generate() draws 24k+ rows) so built once, at import time,
# but only when the pg test could actually use it — never on a bare offline
# run.
DATASET = generate(seed=SEED) if _PG_SKIP_REASON is None else None


def _require_pg() -> None:
    if _PG_SKIP_REASON is not None:
        pytest.skip(_PG_SKIP_REASON)


def sales_in(start: dt.date, end: dt.date, **equals: str) -> list[dict]:
    """Generated sales rows inside the half-open window, optionally narrowed
    to exact column values — the pure-Python twin of a spec filter."""
    return [
        row
        for row in DATASET.sales_rows
        if start <= row["LIFT_ETA_DATE"] < end
        and all(row[col] == value for col, value in equals.items())
    ]


def gp_by_customer(rows: list[dict]) -> list[tuple[str, float]]:
    """``[(customer, gp), ...]`` sorted by GP descending — the pure-Python
    twin of ``GROUP BY CUST_NM ... ORDER BY "GP" DESC``."""
    totals: dict[str, float] = {}
    for row in rows:
        totals[row["CUST_NM"]] = totals.get(row["CUST_NM"], 0.0) + row["GROSS_PROFIT"]
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


@pytest.mark.pg
@pytest.mark.anyio
async def test_singapore_top5_gp_breakdown_through_the_http_endpoint():
    """Same ground truth as
    ``data_qa/skills/metric_query/tests/test_skill.py::
    test_singapore_top5_gp_breakdown_through_the_registry``, driven through
    the actual HTTP endpoint (real ``DATABASE_URL``, ASGI transport) instead
    of calling ``SkillRegistry.dispatch`` in-process — this is the same code
    path the phase-gate curl hits."""
    _require_pg()
    rows = sales_in(dt.date(2026, 4, 1), dt.date(2026, 5, 1), LOC_NM="Singapore")
    expected = gp_by_customer(rows)[:5]
    assert len(expected) == 5, "April 2026 Singapore must have >=5 distinct customers"
    gps = [gp for _, gp in expected]
    assert len(set(gps)) == len(gps), "ties would make the SQL ordering ambiguous"
    top_name, top_gp = expected[0]

    app = _app(database_url=_DSN)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post(
            DEV_RUN_PATH_TEMPLATE.format(skill_id="data_qa.metric_query"),
            json={
                "metrics": ["GP"],
                "period": {"start": "2026-04-01", "end": "2026-05-01"},
                "filters": [{"column": "LOC_NM", "values": ["Singapore"]}],
                "group_by": "CUST_NM",
            },
        )

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, body["error"]
    assert len(body["parts"]) == 1
    table = body["parts"][0]
    assert table["kind"] == "table"
    assert table["payload"]["columns"] == ["Customer", "Gross Profit"]
    assert len(table["payload"]["rows"]) == 5

    first_row = table["payload"]["rows"][0]
    assert first_row[0] == top_name  # the seed's true #1 by GP
    assert first_row[1] == pytest.approx(top_gp, abs=0.51)  # 0dp-rounded GP

    assert "Backend: synthetic" in body["proof"]
    assert body["artifacts"] == []
