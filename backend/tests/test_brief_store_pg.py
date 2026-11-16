"""pg tests for the ``brief_run`` store (D83). Skips with an actionable reason
when no migrated Postgres is reachable. Rows accumulate by design: the app
role has only SELECT and INSERT on ``brief_run``."""

import os
import uuid
from datetime import UTC, date, datetime

import psycopg
import pytest
from sqlalchemy import create_engine, text

from poseidon.briefs.store import BriefStore, NewBrief
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.db import rls_transaction
from poseidon.core.util.uuid7 import uuid7

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `cd web && npx drizzle-kit migrate` (0004)"
_APP_ROLE = "poseidon_app"

pytestmark = pytest.mark.pg


@pytest.fixture
def pg_engine():
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip(
            f"DATABASE_URL is not set - pg brief store tests need a Postgres: "
            f"{_UP_HINT}, {_MIGRATE_HINT}"
        )
    try:
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.brief_run')")
                if cur.fetchone()[0] is None:
                    pytest.skip(f"brief_run does not exist - {_MIGRATE_HINT}")
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
    with pg_engine.connect() as conn:
        is_superuser = conn.execute(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ).scalar_one()
    return _APP_ROLE if is_superuser else None


def _brief(**overrides) -> NewBrief:
    base = dict(
        id=str(uuid7()),
        kind="existing_customer",
        subject="Acme Marine",
        title="Acme Marine - brief",
        anchor_date=date(2026, 11, 16),
        status="ok",
        markdown="# Acme",
        html=b"<p>h</p>",
        pdf=b"%PDF-x",
        pdf_error=None,
        created_by="dev|alice",
        conversation_id=None,
        generated_at=datetime(2026, 11, 16, 8, 0, tzinfo=UTC),
    )
    base.update(overrides)
    return NewBrief(**base)


def test_insert_then_get_bytes_round_trip(pg_engine, effective_app_role):
    store = BriefStore(pg_engine, app_role=effective_app_role)
    brief = _brief()
    store.insert_brief("dev|alice", brief)
    assert store.get_bytes("dev|alice", brief.id, "pdf") == b"%PDF-x"
    assert store.get_bytes("dev|alice", brief.id, "html") == b"<p>h</p>"


def test_a_second_user_reads_the_first_users_brief(pg_engine, effective_app_role):
    store = BriefStore(pg_engine, app_role=effective_app_role)
    brief = _brief()
    store.insert_brief("dev|alice", brief)
    assert store.get_bytes("dev|bob", brief.id, "pdf") == b"%PDF-x"
    assert store.get_bytes("dev|bob", brief.id, "html") == b"<p>h</p>"


def test_pdf_null_reads_none(pg_engine, effective_app_role):
    store = BriefStore(pg_engine, app_role=effective_app_role)
    brief = _brief(pdf=None, pdf_error="OSError")
    store.insert_brief("dev|alice", brief)
    assert store.get_bytes("dev|alice", brief.id, "pdf") is None
    assert store.get_bytes("dev|alice", brief.id, "html") == b"<p>h</p>"


def test_malformed_and_unknown_ids_return_none(pg_engine, effective_app_role):
    store = BriefStore(pg_engine, app_role=effective_app_role)
    assert store.get_bytes("dev|alice", "not-a-uuid", "pdf") is None
    assert store.get_bytes("dev|alice", str(uuid7()), "pdf") is None


def _stored_conversation_id(engine, app_role, brief_id):
    with rls_transaction(engine, "dev|alice", app_role=app_role) as conn:
        return conn.execute(
            text("SELECT conversation_id FROM brief_run WHERE id = :id"),
            {"id": uuid.UUID(brief_id)},
        ).scalar_one()


def test_non_uuid_conversation_id_is_stored_null(pg_engine, effective_app_role):
    store = BriefStore(pg_engine, app_role=effective_app_role)
    bad = _brief(conversation_id="conv-x")
    store.insert_brief("dev|alice", bad)
    assert _stored_conversation_id(pg_engine, effective_app_role, bad.id) is None

    conv = uuid7()
    good = _brief(conversation_id=str(conv))
    store.insert_brief("dev|alice", good)
    assert _stored_conversation_id(pg_engine, effective_app_role, good.id) == conv


def test_unknown_bytes_kind_raises(pg_engine, effective_app_role):
    store = BriefStore(pg_engine, app_role=effective_app_role)
    with pytest.raises(ValueError):
        store.get_bytes("dev|alice", str(uuid7()), "payload")
