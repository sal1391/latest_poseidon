"""pg tests for the Phase 17 report_run store (D65, D69). Skips with an
actionable reason when no migrated Postgres is reachable."""

import os
import uuid
from datetime import UTC, date, datetime

import psycopg
import pytest
from sqlalchemy import create_engine, text

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.util.uuid7 import uuid7
from poseidon.reports.store import NewRun, ReportStore

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `cd web && npx drizzle-kit migrate` (0003)"
_APP_ROLE = "poseidon_app"


@pytest.fixture
def pg_engine():
    """A real ``Engine`` against ``DATABASE_URL``, or a SKIP with an
    actionable reason: unset, unreachable within 2 seconds, or reachable but
    not yet migrated to 0003."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip(
            f"DATABASE_URL is not set - pg report store tests need a Postgres: "
            f"{_UP_HINT}, {_MIGRATE_HINT}"
        )
    try:
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.report_run')")
                if cur.fetchone()[0] is None:
                    pytest.skip(f"report_run does not exist - {_MIGRATE_HINT}")
    except psycopg.Error as exc:
        pytest.skip(
            f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
            f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}"
        )
    engine = create_engine(dsn)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def effective_app_role(pg_engine):
    """``poseidon_app`` when ``DATABASE_URL``'s role is a cluster superuser
    (this dev compose database's bootstrap quirk), ``None`` otherwise."""
    with pg_engine.connect() as conn:
        is_superuser = conn.execute(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ).scalar_one()
    return _APP_ROLE if is_superuser else None


def _run(**overrides) -> NewRun:
    base = dict(
        id=str(uuid7()),
        perspective="customer",
        office_column="CUSTOMER_TEAM_NAME",
        office_value="Rotterdam",
        period_current=PeriodWindow(date(2026, 6, 1), date(2026, 7, 1)),
        period_prior=PeriodWindow(date(2026, 5, 1), date(2026, 6, 1)),
        window_kind="complete",
        basis="prior_month",
        status="ok",
        payload_version=1,
        payload={"payload_version": 1, "kpis": {"GP": {"current": 1.5}}},
        html=b"<html>x</html>",
        pdf=b"%PDF-1.7 fake",
        narrative_grounded=True,
        source="synthetic",
        created_by="test|alice",
        conversation_id=None,
        generated_at=datetime(2026, 10, 12, 8, 14, tzinfo=UTC),
    )
    base.update(overrides)
    return NewRun(**base)


def test_get_bytes_rejects_an_unknown_kind_before_touching_the_uuid():
    store = ReportStore(engine=object())  # never used: the kind check comes first
    with pytest.raises(ValueError, match="kind must be 'html' or 'pdf'"):
        store.get_bytes("test|alice", "not-a-uuid", "txt")


@pytest.mark.pg
def test_insert_then_get_round_trips_every_column(pg_engine, effective_app_role):
    store = ReportStore(pg_engine, app_role=effective_app_role)
    run = _run()
    store.insert_run("test|alice", run)

    got = store.get_run("test|alice", run.id)

    assert got is not None
    assert got.id == run.id and got.perspective == "customer"
    assert got.office_column == "CUSTOMER_TEAM_NAME" and got.office_value == "Rotterdam"
    assert got.period_current == run.period_current and got.period_prior == run.period_prior
    assert got.window_kind == "complete" and got.basis == "prior_month" and got.status == "ok"
    assert got.payload == run.payload and got.payload_version == 1
    assert got.narrative_grounded is True and got.source == "synthetic"
    assert got.created_by == "test|alice" and got.conversation_id is None
    assert got.superseded_by is None and got.definition_id is None
    assert got.generated_at == run.generated_at
    assert got.created_at is not None


@pytest.mark.pg
def test_bytes_come_back_by_kind_and_are_absent_from_get_run(pg_engine, effective_app_role):
    store = ReportStore(pg_engine, app_role=effective_app_role)
    run = _run()
    store.insert_run("test|alice", run)

    assert store.get_bytes("test|alice", run.id, "html") == b"<html>x</html>"
    assert store.get_bytes("test|alice", run.id, "pdf") == b"%PDF-1.7 fake"
    assert not hasattr(store.get_run("test|alice", run.id), "pdf")


@pytest.mark.pg
def test_a_run_is_readable_by_another_sales_user(pg_engine, effective_app_role):
    """D69: shared org document, no row policy."""
    store = ReportStore(pg_engine, app_role=effective_app_role)
    run = _run()
    store.insert_run("test|alice", run)

    assert store.get_run(f"test|{uuid.uuid4().hex}", run.id) is not None


@pytest.mark.pg
def test_unknown_or_malformed_ids_return_none(pg_engine, effective_app_role):
    store = ReportStore(pg_engine, app_role=effective_app_role)
    assert store.get_run("test|alice", str(uuid.uuid4())) is None
    assert store.get_run("test|alice", "not-a-uuid") is None
    assert store.get_bytes("test|alice", str(uuid.uuid4()), "pdf") is None


@pytest.mark.pg
def test_a_run_with_no_bytes_stores_nulls(pg_engine, effective_app_role):
    store = ReportStore(pg_engine, app_role=effective_app_role)
    run = _run(html=None, pdf=None, status="error", narrative_grounded=False)
    store.insert_run("test|alice", run)
    assert store.get_bytes("test|alice", run.id, "pdf") is None
    assert store.get_run("test|alice", run.id).status == "error"
