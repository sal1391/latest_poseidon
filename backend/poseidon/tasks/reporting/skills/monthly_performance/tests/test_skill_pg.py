"""Ground truth for reporting.monthly_performance through the real registry
against the seeded Postgres. Expectations are recomputed from
generate(seed=SEED), never hand-copied. The generator gives every customer
exactly one office today, so share_of_book is 100% and offsets are empty;
PR A2's multi-office overlay re-pins those two assertions."""

import datetime as dt
import os
from math import fsum

import psycopg
import pytest
from sqlalchemy import create_engine, text

from poseidon.core.analytics import mom
from poseidon.core.config import Settings
from poseidon.core.data.synthetic_client import SyntheticDataClient, normalize_dsn
from poseidon.core.identity import UserContext
from poseidon.core.llm.prompt_tree import PromptTree
from poseidon.core.parsing.period_labels import month_window
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.reports.store import ReportStore
from poseidon.scripts.generate_synthetic import generate

pytestmark = pytest.mark.pg

SEED = 1391
SALES = "MARINE_SALES_PLANNING_V"
SKILL = "reporting.monthly_performance"
_SETTINGS_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"
_DSN = os.environ.get("DATABASE_URL", "")

CONNECT_TIMEOUT_SECONDS = 2

# Skip reasons are ASCII-only on purpose: pytest prints them straight to a
# console that may well be Windows cp1252, where an em dash becomes "?".
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_SEED_HINT = "seed it with `python -m poseidon.scripts.seed_synthetic`"
_MIGRATE_HINT = "create it with `cd web && npx drizzle-kit migrate`"

if not _DSN:
    pytest.skip(
        f"DATABASE_URL is not set - pg integration tests need a Postgres: {_UP_HINT}, {_SEED_HINT}",
        allow_module_level=True,
    )

try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute("SELECT COUNT(*) FROM synthetic.marine_sales_planning_v")
            SEEDED_SALES_ROWS = _cur.fetchone()[0]
            _cur.execute("SELECT to_regclass('public.report_run')")
            REPORT_RUN_TABLE = _cur.fetchone()[0]
except Exception as exc:  # noqa: BLE001 - any connect/lookup failure means "not available"
    pytest.skip(
        f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}",
        allow_module_level=True,
    )

if SEEDED_SALES_ROWS == 0:
    pytest.skip(
        f"synthetic.marine_sales_planning_v is empty - {_SEED_HINT}", allow_module_level=True
    )

if REPORT_RUN_TABLE is None:
    pytest.skip(f"public.report_run does not exist - {_MIGRATE_HINT}", allow_module_level=True)

DATASET = generate(seed=SEED)
CLIENT = SyntheticDataClient(_DSN)
USER = UserContext(sub="dev|report-pg", email=None, name="Report PG", roles=("Poseidon:Sales",))


def _engine_and_role():
    engine = create_engine(_DSN)
    with engine.connect() as conn:
        is_superuser = conn.execute(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ).scalar_one()
    return engine, ("poseidon_app" if is_superuser else None)


@pytest.fixture(scope="module")
def registry():
    return SkillRegistry.discover()


@pytest.fixture(scope="module")
def store():
    engine, role = _engine_and_role()
    yield ReportStore(engine, app_role=role)
    engine.dispose()


def _ctx(store):
    settings = Settings(
        _env_file=None, database_url=_SETTINGS_DSN, s3_bucket="poseidon-artifacts", llm_mode="stub"
    )
    return SkillContext(
        data=CLIENT,
        artifacts=None,
        settings=settings,
        state=ConversationSlots(),
        user=USER,
        report_store=store,
        prompt_tree=PromptTree(),
    )


def _latest_complete_month() -> dt.date:
    today = dt.date.today()
    return max(
        m for m in CLIENT.list_months(SALES, {}) if month_window(m.year, m.month).end <= today
    )


def _rows(window, column=None, value=None):
    return [
        r
        for r in DATASET.sales_rows
        if window.start <= r["LIFT_ETA_DATE"] < window.end
        and (column is None or r[column] == value)
    ]


def _by(rows, column):
    out = {}
    for r in rows:
        acc = out.setdefault(r[column], {"GP": 0.0, "VOLUME": 0.0})
        acc["GP"] += r["GROSS_PROFIT"]
        acc["VOLUME"] += r["FIXED_TONS"]
    return out


def _run(registry, store, **args):
    result = registry.dispatch(SKILL, args, _ctx(store))
    assert result.ok, result.error
    return result, result.parts[0]["payload"]


def _assert_footers_equal_tiles(payload):
    for name, table in payload["tables"].items():
        for metric in ("GP", "VOLUME", "MARGIN"):
            assert table["footer"]["display"][metric] == payload["kpis"][metric]["display"], (
                name,
                metric,
            )
            for side in ("prior", "current"):
                assert mom.reconciles(
                    table["footer"][side][metric] if metric != "MARGIN" else 0.0,
                    payload["kpis"][metric][side] if metric != "MARGIN" else 0.0,
                ), (name, metric, side)


def test_customer_office_golden(registry, store):
    month = _latest_complete_month()
    office = sorted({r["CUSTOMER_TEAM_NAME"] for r in DATASET.sales_rows})[0]
    result, payload = _run(
        registry, store, perspective="customer", office_value=office, month=month.isoformat()
    )

    current = month_window(month.year, month.month)
    prior = month_window(
        *((month.year, month.month - 1) if month.month > 1 else (month.year - 1, 12))
    )
    cur_rows, pri_rows = (
        _rows(current, "CUSTOMER_TEAM_NAME", office),
        _rows(prior, "CUSTOMER_TEAM_NAME", office),
    )
    assert payload["kpis"]["GP"]["display"]["current"] == mom.fmt_value(
        fsum(r["GROSS_PROFIT"] for r in cur_rows), "money"
    )
    assert payload["kpis"]["GP"]["display"]["prior"] == mom.fmt_value(
        fsum(r["GROSS_PROFIT"] for r in pri_rows), "money"
    )
    assert payload["kpis"]["VOLUME"]["display"]["current"] == mom.fmt_value(
        fsum(r["FIXED_TONS"] for r in cur_rows), "tons"
    )
    top = max(_by(cur_rows, "CUST_NM").items(), key=lambda kv: kv[1]["GP"])[0]
    assert payload["tables"]["entities_by_gp"]["rows"][0]["key"] == top
    assert len(payload["tables"]["entities_by_gp"]["rows"]) <= 10
    _assert_footers_equal_tiles(payload)
    for name, context in payload["entity_context"].items():
        assert context["offsets"] == [], (
            name
        )  # one office per customer today (A2 overlay changes this)
        for side in ("prior", "current"):
            assert context["share_of_book"]["display"][side] in ("100.0%", "n/a"), (name, side)
    assert payload["narrative"]["flags"][0] == "model narrative skipped: stub mode"
    assert len(payload["suggested_questions"]) == 3

    run = store.get_run(USER.sub, payload["run_id"])
    assert run is not None and run.status == "ok" and run.payload["run_id"] == payload["run_id"]
    assert store.get_bytes(USER.sub, payload["run_id"], "html") is not None
    pdf = store.get_bytes(USER.sub, payload["run_id"], "pdf")
    if pdf is None:
        assert any(f.startswith("PDF not rendered: ") for f in payload["narrative"]["flags"])
        assert result.artifacts == []
    else:
        assert (
            pdf.startswith(b"%PDF")
            and result.artifacts[0].url == f"/api/reports/{payload['run_id']}/pdf"
        )


def test_supplier_office_golden(registry, store):
    month = _latest_complete_month()
    office = sorted({r["PRIMARY_SUPPLY_TEAM_OFFICE"] for r in DATASET.sales_rows})[0]
    _, payload = _run(
        registry, store, perspective="supplier", office_value=office, month=month.isoformat()
    )
    current = month_window(month.year, month.month)
    cur_rows = _rows(current, "PRIMARY_SUPPLY_TEAM_OFFICE", office)
    assert payload["entity"]["label"] == "Supplier"
    top = max(_by(cur_rows, "SUPPLIER_NM").items(), key=lambda kv: kv[1]["GP"])[0]
    assert payload["tables"]["entities_by_gp"]["rows"][0]["key"] == top
    assert payload["scope"]["label"] == f"{office} (Supply Team Office)"
    _assert_footers_equal_tiles(payload)


def test_whole_book_golden(registry, store):
    month = _latest_complete_month()
    _, payload = _run(registry, store, perspective="book", month=month.isoformat())
    current = month_window(month.year, month.month)
    assert payload["entity_context"] is None
    assert payload["kpis"]["GP"]["display"]["current"] == mom.fmt_value(
        fsum(r["GROSS_PROFIT"] for r in _rows(current)), "money"
    )
    assert len(payload["queries"]) == 4
    _assert_footers_equal_tiles(payload)


def test_month_outside_the_data_is_a_422(registry, store):
    result = registry.dispatch(SKILL, {"perspective": "book", "month": "2019-01-01"}, _ctx(store))
    assert result.ok is False and result.error["status"] == 422
