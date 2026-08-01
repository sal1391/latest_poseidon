"""Tests for Phase 11 Task 2 (doc 06 section 3): ``core/obs.py``'s JSON
logging/tracing/span seam, ``api/app.py``'s trace middleware, the
``trace_id`` stamp on ``turn_run`` (``core/runlog.py``), and
``api/live_chat.py``'s ``X-Accel-Buffering`` SSE header.

Most of this file is OFFLINE: ``core/obs.py`` itself, the trace middleware,
and span emission are all provable without Postgres -- a captured-stdout
JSON line IS the observable behavior. Two things genuinely need Postgres
(``@pytest.mark.pg``, skipped -- never failed -- without a reachable,
migrated database, mirroring every other pg-marked module's own guard in
this suite): ``turn_run.trace_id`` is a real column only migration 0003
creates, and a real SSE turn needs the Phase 10 persisted-history stack.

``PERPLEXITY_API_KEY`` is never read by anything in this file (nothing here
drives ``ext:perplexity`` for real) -- consistent with the plan's
"withhold on every run" environment rule regardless.
"""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest
from sqlalchemy import create_engine, text

from poseidon.core import obs
from poseidon.core.config import Settings
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.runlog import RunLogWriter

# Present-and-non-blank is all Settings needs from a DSN the offline tests
# below never actually connect with on purpose -- the same "fails fast
# on 127.0.0.1:1, never hangs" placeholder used throughout this suite
# (test_health.py, test_dev_runner.py, test_live_chat_sse.py all reuse this
# exact literal DSN string).
_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0005)"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        _env_file=None, database_url=_PLACEHOLDER_DSN, s3_bucket="poseidon-artifacts"
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _app(**overrides):
    from poseidon.api.app import create_app

    return create_app(_settings(**overrides))


def _json_lines(text_blob: str) -> list[dict]:
    """Every line of ``text_blob`` that parses as JSON, in order -- skips
    any line that does not. Boot/app construction can also emit OTHER,
    still-bare ``print(..., flush=True)`` lines this task's scope leaves
    untouched (the artifact-bucket warning, "skills registered: ...",
    "chat persistence: ..." -- see ``api/app.py``'s own docstring
    comments), so a defensive skip is load-bearing here, not merely
    tidy."""
    records = []
    for line in text_blob.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


@pytest.fixture
def pg_database_url() -> str:
    """``DATABASE_URL``, or a SKIP with an actionable reason -- mirrors
    ``test_runlog_writer.py``'s own ``pg_engine``/``test_live_chat_sse.
    py``'s own ``pg_database_url`` exactly (every pg-marked test module in
    this codebase defines its own; there is no shared ``conftest.py``),
    narrowed to "does ``turn_run`` exist" since that is the one table this
    file's pg tests read from."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip(
            f"DATABASE_URL is not set - pg obs tests need a Postgres: {_UP_HINT}, {_MIGRATE_HINT}"
        )
    try:
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.turn_run')")
                if cur.fetchone()[0] is None:
                    pytest.skip(f"turn_run does not exist - {_MIGRATE_HINT}")
    except psycopg.Error as exc:
        pytest.skip(
            f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
            f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}"
        )
    return dsn


def _fresh_user_sub() -> str:
    """A ``user_sub`` unique to this test invocation -- mirrors
    ``test_runlog_writer.py``'s own ``_fresh_user_sub`` so re-running this
    pg test against a long-lived dev Postgres never collides with a
    previous run's rows."""
    return f"obs-test|{uuid.uuid4()}"


# ===========================================================================
# JSON line shape: exactly the seven pinned keys, no extras (doc 06 section
# 3, verbatim)
# ===========================================================================


def test_json_line_has_exactly_the_seven_pinned_keys_and_no_extras(capsys):
    obs.configure_json_logging()
    obs.get_logger("shape_probe").info("shape_checked", foo="bar", n=1)

    matching = [r for r in _json_lines(capsys.readouterr().out) if r["event"] == "shape_checked"]
    assert len(matching) == 1
    assert set(matching[0].keys()) == {
        "ts",
        "level",
        "trace_id",
        "turn_id",
        "component",
        "event",
        "context",
    }


def test_ts_is_iso8601_utc_and_close_to_the_current_time(capsys):
    obs.configure_json_logging()
    before = datetime.now(UTC)
    obs.get_logger("ts_probe").info("ts_checked")
    after = datetime.now(UTC)

    matching = [r for r in _json_lines(capsys.readouterr().out) if r["event"] == "ts_checked"]
    assert len(matching) == 1
    ts = matching[0]["ts"]
    assert ts.endswith("Z")
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    # Generous 2-second slop: this asserts "the real wall clock", not a
    # tight timing guarantee -- CI/dev-machine scheduling jitter is not
    # this test's concern.
    assert before - timedelta(seconds=2) <= parsed <= after + timedelta(seconds=2)


def test_level_reflects_the_logging_method_used(capsys):
    obs.configure_json_logging()
    logger = obs.get_logger("level_probe")
    logger.info("level_probe_info")
    logger.warning("level_probe_warning")
    logger.error("level_probe_error")

    by_event = {
        r["event"]: r["level"]
        for r in _json_lines(capsys.readouterr().out)
        if r["event"].startswith("level_probe_")
    }
    assert by_event == {
        "level_probe_info": "INFO",
        "level_probe_warning": "WARNING",
        "level_probe_error": "ERROR",
    }


def test_component_and_context_are_stamped_from_the_call(capsys):
    obs.configure_json_logging()
    obs.get_logger("stamped_component").info("stamped_event", key="value", n=7)

    matching = [r for r in _json_lines(capsys.readouterr().out) if r["event"] == "stamped_event"]
    assert len(matching) == 1
    assert matching[0]["component"] == "stamped_component"
    assert matching[0]["context"] == {"key": "value", "n": 7}


def test_context_is_null_when_no_extra_context_is_given(capsys):
    obs.configure_json_logging()
    obs.get_logger("bare_probe").info("bare_event")

    matching = [r for r in _json_lines(capsys.readouterr().out) if r["event"] == "bare_event"]
    assert len(matching) == 1
    assert matching[0]["context"] is None


def test_turn_id_is_always_null_in_this_task(capsys):
    """See ``core/obs.py``'s own module docstring, "turn_id stays null in
    this task" -- nothing in Task 2's scope ever calls ``.set()`` on the
    internal turn-id contextvar, so every line this task emits carries
    ``turn_id: null``, satisfying doc 06 section 3's "absent values null"
    literally rather than fabricating a value nothing has yet."""
    obs.configure_json_logging()
    obs.get_logger("turn_id_probe").info("turn_id_probe_event")

    matching = [
        r for r in _json_lines(capsys.readouterr().out) if r["event"] == "turn_id_probe_event"
    ]
    assert len(matching) == 1
    assert matching[0]["turn_id"] is None


def test_configure_json_logging_is_idempotent(capsys):
    """A second (and third) call must never double-register a handler --
    otherwise every log line downstream would print TWICE, silently
    doubling every ``docker compose logs`` line in a real deploy."""
    obs.configure_json_logging()
    obs.configure_json_logging()
    obs.configure_json_logging()
    obs.get_logger("idempotency_probe").info("probed_once")

    matching = [
        r for r in _json_lines(capsys.readouterr().out) if r["event"] == "probed_once"
    ]
    assert len(matching) == 1


# ===========================================================================
# trace_id_var: the contextvar every log line's trace_id field reads
# ===========================================================================


def test_trace_id_is_null_when_nothing_set_it(capsys):
    obs.configure_json_logging()
    obs.get_logger("trace_probe").info("trace_probe_default")

    matching = [
        r for r in _json_lines(capsys.readouterr().out) if r["event"] == "trace_probe_default"
    ]
    assert len(matching) == 1
    assert matching[0]["trace_id"] is None


def test_trace_id_reflects_the_active_contextvar(capsys):
    obs.configure_json_logging()
    token = obs.trace_id_var.set("probe-trace-abc")
    try:
        obs.get_logger("trace_probe").info("trace_probe_set")
    finally:
        obs.trace_id_var.reset(token)

    matching = [
        r for r in _json_lines(capsys.readouterr().out) if r["event"] == "trace_probe_set"
    ]
    assert len(matching) == 1
    assert matching[0]["trace_id"] == "probe-trace-abc"


def test_new_trace_id_returns_a_32_char_lowercase_hex_uuid7():
    trace_id = obs.new_trace_id()
    assert len(trace_id) == 32
    assert all(ch in "0123456789abcdef" for ch in trace_id)
    assert trace_id != obs.new_trace_id()  # two calls never collide


# ===========================================================================
# span(): event="span", verbatim name + duration_ms inside context,
# component derived from the name's prefix
# ===========================================================================


def test_span_emits_event_span_with_verbatim_name_component_and_nonnegative_duration(capsys):
    obs.configure_json_logging()
    with obs.span("parse"):
        pass
    with obs.span("skill:demo.probe_skill", skill_extra="value"):
        pass

    span_records = [r for r in _json_lines(capsys.readouterr().out) if r["event"] == "span"]
    parse_line = next(r for r in span_records if r["context"]["name"] == "parse")
    skill_line = next(
        r for r in span_records if r["context"]["name"] == "skill:demo.probe_skill"
    )

    assert parse_line["component"] == "parse"
    assert isinstance(parse_line["context"]["duration_ms"], int)
    assert parse_line["context"]["duration_ms"] >= 0

    assert skill_line["component"] == "skill"
    assert skill_line["context"]["skill_extra"] == "value"
    assert skill_line["context"]["duration_ms"] >= 0


def test_span_still_emits_and_reraises_on_exception(capsys):
    """Spans ADD log emission; they never change control flow -- an
    exception raised inside the wrapped block must still propagate to the
    caller, unchanged, AFTER the span line is emitted."""
    obs.configure_json_logging()
    with pytest.raises(ValueError, match="boom"):
        with obs.span("db:query"):
            raise ValueError("boom")

    span_records = [
        r
        for r in _json_lines(capsys.readouterr().out)
        if r["event"] == "span" and r["context"]["name"] == "db:query"
    ]
    assert len(span_records) == 1
    assert span_records[0]["context"]["duration_ms"] >= 0


# ===========================================================================
# Trace middleware: contextvar set per request, X-Trace-Id on every
# response, distinct ids for concurrent requests
# ===========================================================================


@pytest.mark.anyio
async def test_trace_middleware_adds_a_well_formed_x_trace_id_header():
    app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/health/live")

    trace_id = response.headers["x-trace-id"]
    assert len(trace_id) == 32
    assert all(ch in "0123456789abcdef" for ch in trace_id)


@pytest.mark.anyio
async def test_two_concurrent_requests_get_distinct_trace_ids():
    app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r1, r2 = await asyncio.gather(client.get("/health/live"), client.get("/health/live"))

    assert r1.headers["x-trace-id"] != r2.headers["x-trace-id"]


@pytest.mark.anyio
async def test_trace_id_propagates_through_a_sync_route_into_nested_span_lines(capsys):
    """End-to-end, OFFLINE proof that the middleware's contextvar value
    survives the "sync route function runs in a worker thread" boundary
    (FastAPI's own ``run_in_threadpool`` for ``def`` routes, the identical
    mechanism ``anyio.to_thread.run_sync`` uses for ``api/live_chat.py``'s
    turn execution): ``data_qa.metric_query`` dispatched through the dev
    runner (``deploy_mode="local"``, the default) reaches BOTH the
    ``skill:data_qa.metric_query`` span (``core/skills/registry.py``) and
    the nested ``db:query`` span (``core/data/synthetic_client.py``,
    called from inside the skill) -- against the placeholder DSN, so the
    query itself fails fast (a connection refused), but both spans still
    fire (spans log on ANY exit, per their own contract) before dispatch's
    own ``except Exception`` turns the failure into dev_runner's pinned
    "always 200, failure is structured content" response. No Postgres
    needed: this proves PROPAGATION, not the query's own success.
    """
    app = _app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post(
            "/api/dev/skills/data_qa.metric_query/run",
            json={"metrics": ["GP"], "period": {"start": "2026-04-01", "end": "2026-05-01"}},
        )

    assert response.status_code == 200
    trace_id = response.headers["x-trace-id"]
    span_records = [r for r in _json_lines(capsys.readouterr().out) if r["event"] == "span"]
    skill_lines = [r for r in span_records if r["context"]["name"] == "skill:data_qa.metric_query"]
    db_lines = [r for r in span_records if r["context"]["name"] == "db:query"]

    assert skill_lines, "expected a skill:data_qa.metric_query span line"
    assert db_lines, "expected a db:query span line (SyntheticDataClient._fetch)"
    assert skill_lines[0]["trace_id"] == trace_id
    assert db_lines[0]["trace_id"] == trace_id


# ===========================================================================
# Boot-line conversion: create_app's own construction emits the two
# converted lines as JSON (the pinned CONTENT is asserted, per test, in the
# adapted tests this task disclosed in test_identity_providers.py/
# test_live_chat_sse.py -- this is the consolidated "it actually happens
# during a real create_app() boot" integration check the brief also asks
# this file to carry)
# ===========================================================================


def test_create_app_boot_emits_json_lines_for_identity_and_research_transport(capsys):
    _app(chat_mode="live")

    events = {r["event"] for r in _json_lines(capsys.readouterr().out)}
    assert "identity_mode_resolved" in events
    assert "research_transport_resolved" in events


# ===========================================================================
# turn_run.trace_id stamping (pg) -- via the writer's own contextvar
# fallback, and via a real HTTP-driven turn
# ===========================================================================


@pytest.mark.pg
def test_start_turn_falls_back_to_the_ambient_trace_id_var_when_not_given(pg_database_url):
    """Surgical proof of ``core/runlog.py``'s own fallback (see that
    module's ``start_turn`` docstring, "Phase 11 Task 2: trace stamping,
    via THIS writer, not the caller"): a bare ``RunLogWriter`` call that
    passes ``trace_id=None`` -- exactly what ``core/chat/orchestrator.py``'s
    own ``_begin_turn`` does today -- still lands the AMBIENT contextvar's
    value in the row, with no HTTP layer involved at all.
    """
    engine = create_engine(pg_database_url)
    try:
        writer = RunLogWriter(engine)
        user_sub = _fresh_user_sub()
        trace_id = obs.new_trace_id()
        token = obs.trace_id_var.set(trace_id)
        try:
            handle = writer.start_turn(
                user_sub=user_sub,
                conversation_id=None,
                client_turn_key=None,
                turn_index=1,
                question="probe",
                mode="default",
                parsed={},
                trace_id=None,
            )
        finally:
            obs.trace_id_var.reset(token)

        assert handle is not None
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT trace_id FROM turn_run WHERE id = :id"),
                {"id": handle.turn_run_id},
            ).first()
        assert row is not None
        assert row[0] == trace_id
    finally:
        engine.dispose()


@pytest.mark.pg
@pytest.mark.anyio
async def test_turn_run_trace_id_is_stamped_and_sse_response_headers_are_correct(pg_database_url):
    """doc 06 section 3, end to end: "send a turn, read the row". The D19
    entry-branch phrase ("start an existing-customer brief") is used
    deliberately -- it drives ``_begin_turn``/``writer.start_turn`` without
    ever touching ``ctx.data`` (see ``core/chat/orchestrator.py``'s own
    module docstring, "D19 entry orchestration"), so this test needs no
    seeded synthetic data, only a real, migrated Postgres for the history/
    run-log tables. The ``X-Accel-Buffering`` header assertion is
    piggybacked onto this SAME SSE response (Task 2's other pg-shaped
    deliverable) rather than standing up a second live app/conversation
    just to re-check one more header.
    """
    app = _app(chat_mode="live", database_url=pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        async with client.stream(
            "POST",
            f"/api/conversations/{cid}/messages",
            json={"text": "start an existing-customer brief"},
        ) as response:
            assert response.status_code == 200
            trace_id = response.headers["x-trace-id"]
            assert response.headers["x-accel-buffering"] == "no"
            async for _line in response.aiter_lines():
                pass  # drain fully so the turn (and its run-log write) completes

    engine = create_engine(pg_database_url)
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT trace_id FROM turn_run WHERE conversation_id = :cid"),
                {"cid": cid},
            ).first()
    finally:
        engine.dispose()
    assert row is not None
    assert row[0] == trace_id
