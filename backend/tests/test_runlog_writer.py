"""Tests for migration 0003 (doc 06 section 1) and :mod:`poseidon.core.runlog`
(Phase 6 Task 1).

Two halves, split the same way ``test_synthetic_client_pg.py`` and its
purely-offline siblings are split, but inside ONE file (the task brief names
exactly one new test module):

- **Offline** (always run, zero network): SQL TEXT shape, via
  ``_RecordingEngine`` -- a minimal SQLAlchemy-``Engine``-shaped double that
  records every executed statement and its bound parameters and replays a
  scripted queue of canned rows. It never touches sqlite: unlike
  ``test_migrations.py``'s throwaway-sqlite smoke test (which only proves
  ``upgrade()`` no-ops cleanly there), these tests would be fighting sqlite's
  lack of ``jsonb``/``timestamptz``/``ON CONFLICT (...) DO NOTHING
  RETURNING`` for no benefit -- the writer's SQL is Postgres-only by design
  (see the migration's own dialect guard), so a fake that just remembers what
  it was asked to execute is the right-weight double. The never-raises
  contract is proven the same way, with ``_RaisingEngine`` standing in for a
  broken connection (bad DSN, exhausted pool, network down).
- **``pg``** (``@pytest.mark.pg``, skipped without a reachable, migrated
  Postgres): the same guard shape as ``test_synthetic_client_pg.py``
  (``DATABASE_URL`` presence, then a 2-second reachability probe), adapted to
  a ``pg_engine`` FIXTURE rather than a module-level skip, because this file
  -- unlike that one -- mixes pg tests with offline tests that must always
  run. Every identifying value (``user_sub``, ``client_turn_key``) is a fresh
  ``uuid4`` per test, so re-running this suite against a long-lived dev
  Postgres never trips on a previous run's leftover rows (which are
  deliberately never deleted -- ``test_artifact_store.py``'s same choice,
  and for the same reason: harmless, and evidence the write actually
  happened).
"""

import logging
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, text

from poseidon.core import runlog
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.runlog import RunLogWriter, TurnHandle

# ---------------------------------------------------------------------------
# offline fakes -- no real SQLAlchemy dialect, no real database
# ---------------------------------------------------------------------------


class _FakeResult:
    """Just enough of SQLAlchemy's ``CursorResult`` for the writer: one
    ``.first()`` call, returning a scripted row (a plain tuple, indexed
    positionally -- exactly how the writer itself reads a ``RETURNING``/
    ``SELECT id`` row) or ``None``."""

    def __init__(self, row: tuple | None) -> None:
        self._row = row

    def first(self) -> tuple | None:
        return self._row


# Phase 11 Task 1: every RunLogWriter method now opens poseidon.core.db's
# rls_transaction instead of a bare self._engine.begin() -- which itself
# calls engine.begin() (this fake's own .begin() still stands in for that
# unchanged) and then executes ONE bookkeeping statement, `SELECT
# set_config('app.user_sub', ...)`, as the transaction's own first
# statement, BEFORE the writer's real SQL ever runs. Recognized here by a
# simple substring match and routed to `identity_calls` -- never scripted,
# never consuming a queued result -- so every pre-existing assertion in this
# file (`len(engine.calls) == 1`, `engine.calls[0]` == the writer's own SQL)
# keeps meaning exactly what it meant before this cutover, with zero
# test-body changes needed (see this task's own report for the full
# disclosure). `SET LOCAL ROLE` is matched too, defensively, even though no
# test in this file constructs a writer with `app_role` set (all pass a bare
# `RunLogWriter(engine)`) -- so it is never actually emitted today, only
# guarded against for whichever test is first to pass one.
_IDENTITY_SQL_MARKER = "set_config"
_ROLE_SQL_MARKER = "SET LOCAL ROLE"


class _RecordingConnection:
    """Records every ``(sql text, bound params)`` pair it is asked to
    execute and replays a scripted queue of rows, one per call -- enough to
    drive both branches of ``start_turn`` (a row back on insert, or ``None``
    forcing the idempotency fallback) from a single offline test. See the
    module-level comment above ``_IDENTITY_SQL_MARKER`` for why identity/role
    bookkeeping statements are recorded separately, in ``identity_calls``,
    never in ``calls``."""

    def __init__(self, results: list[tuple | None]) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.identity_calls: list[tuple[str, dict]] = []
        self._results = list(results)

    def execute(self, statement, params) -> _FakeResult:
        # str() on a bare sqlalchemy.text() clause is just the literal SQL
        # this module wrote -- no compiler, no dialect, nothing sqlite could
        # possibly object to (see the module docstring).
        text_sql = str(statement)
        if _IDENTITY_SQL_MARKER in text_sql or _ROLE_SQL_MARKER in text_sql:
            self.identity_calls.append((text_sql, dict(params)))
            return _FakeResult(None)
        self.calls.append((text_sql, dict(params)))
        row = self._results.pop(0) if self._results else None
        return _FakeResult(row)

    def __enter__(self) -> "_RecordingConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _RecordingEngine:
    """``engine.begin()`` double: yields a ``_RecordingConnection`` that
    remembers what it was asked to do. ``results`` scripts the RETURNING/
    SELECT rows successive ``execute()`` calls see, in order."""

    def __init__(self, results: list[tuple | None] = ()) -> None:
        self.connection = _RecordingConnection(list(results))

    def begin(self) -> _RecordingConnection:
        return self.connection

    @property
    def calls(self) -> list[tuple[str, dict]]:
        return self.connection.calls

    @property
    def identity_calls(self) -> list[tuple[str, dict]]:
        return self.connection.identity_calls


class _RaisingContext:
    """The context manager ``_RaisingEngine.begin()`` returns: failure
    happens at ``__enter__``, exactly where a real broken engine fails (a
    real ``Engine.begin()`` call is lazy -- it does not touch the network
    until the ``with`` block is entered and a connection is actually checked
    out)."""

    def __enter__(self):
        raise RuntimeError("simulated connection failure")

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _RaisingEngine:
    """Stands in for a broken engine: bad DSN, exhausted pool, network
    down -- anything that fails before a statement is ever sent."""

    def begin(self) -> _RaisingContext:
        return _RaisingContext()


# ---------------------------------------------------------------------------
# TurnHandle
# ---------------------------------------------------------------------------


def test_turn_handle_is_a_frozen_dataclass_with_the_two_named_fields():
    handle = TurnHandle(turn_run_id="abc", created=True)

    assert handle.turn_run_id == "abc"
    assert handle.created is True
    with pytest.raises(AttributeError):
        handle.created = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# offline -- start_turn SQL shape (both branches of the idempotent insert)
# ---------------------------------------------------------------------------


def test_start_turn_fresh_insert_generates_on_conflict_returning_and_reports_created():
    engine = _RecordingEngine(results=[("11111111-1111-1111-1111-111111111111",)])
    writer = RunLogWriter(engine)

    handle = writer.start_turn(
        user_sub="auth0|carlos",
        conversation_id="conv-1",
        client_turn_key="key-1",
        turn_index=1,
        question="top gp for maersk",
        mode="default",
        parsed={"customer": "MAERSK LINE"},
        trace_id="trace-1",
    )

    assert handle == TurnHandle(turn_run_id="11111111-1111-1111-1111-111111111111", created=True)
    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    assert "INSERT INTO turn_run" in sql
    assert "ON CONFLICT (user_sub, client_turn_key) DO NOTHING" in sql
    assert "RETURNING id" in sql
    assert params["user_sub"] == "auth0|carlos"
    assert params["conversation_id"] == "conv-1"
    assert params["client_turn_key"] == "key-1"
    assert params["turn_index"] == 1
    assert params["question"] == "top gp for maersk"
    assert params["mode"] == "default"
    assert params["parsed"] == '{"customer": "MAERSK LINE"}'
    assert params["trace_id"] == "trace-1"
    assert params["kind"] == "chat_turn"
    # A fresh, real uuid4 was minted for the row -- not a canned or blank id.
    assert uuid.UUID(params["id"])


def test_start_turn_defaults_kind_to_chat_turn_and_trace_id_to_none():
    engine = _RecordingEngine(results=[("id-1",)])
    writer = RunLogWriter(engine)

    writer.start_turn(
        user_sub="u",
        conversation_id=None,
        client_turn_key=None,
        turn_index=None,
        question=None,
        mode="default",
        parsed={},
    )

    _, params = engine.calls[0]
    assert params["kind"] == "chat_turn"
    assert params["trace_id"] is None
    assert params["conversation_id"] is None
    assert params["client_turn_key"] is None


def test_start_turn_kind_memory_update_is_passed_through_verbatim():
    engine = _RecordingEngine(results=[("id-1",)])
    writer = RunLogWriter(engine)

    writer.start_turn(
        user_sub="u",
        conversation_id=None,
        client_turn_key=None,
        turn_index=None,
        question=None,
        mode="default",
        parsed={},
        kind="memory_update",
    )

    _, params = engine.calls[0]
    assert params["kind"] == "memory_update"


def test_start_turn_uses_provided_turn_run_id_verbatim_instead_of_minting_one():
    """Phase 6 Task 4's amendment (closing the turn-id seam doc 06 section 1
    comments on): a caller -- the chat orchestrator, passing its SSE sink's
    own turn_id -- may supply the row's id explicitly. The RETURNING clause
    echoes back whatever id was actually inserted, exactly as a real
    Postgres INSERT ... RETURNING would for a row created with an explicit
    id, so scripting the fake's canned row to equal the SAME provided id is
    what makes this test prove the id was used, not merely accepted."""
    provided_id = "caller-supplied-turn-id"
    engine = _RecordingEngine(results=[(provided_id,)])
    writer = RunLogWriter(engine)

    handle = writer.start_turn(
        user_sub="u",
        conversation_id=None,
        client_turn_key=None,
        turn_index=None,
        question=None,
        mode="default",
        parsed={},
        turn_run_id=provided_id,
    )

    assert handle == TurnHandle(turn_run_id=provided_id, created=True)
    _, params = engine.calls[0]
    assert params["id"] == provided_id


def test_start_turn_conflict_falls_back_to_selecting_the_existing_row():
    """The INSERT's ``RETURNING`` comes back empty (a row with this
    ``(user_sub, client_turn_key)`` already exists), so the writer issues a
    SECOND statement to find it -- proven here purely from the two recorded
    SQL calls, no real unique-index conflict involved."""
    engine = _RecordingEngine(results=[None, ("existing-id",)])
    writer = RunLogWriter(engine)

    handle = writer.start_turn(
        user_sub="u",
        conversation_id=None,
        client_turn_key="retry-key",
        turn_index=1,
        question="q",
        mode="default",
        parsed={},
    )

    assert handle == TurnHandle(turn_run_id="existing-id", created=False)
    assert len(engine.calls) == 2
    first_sql, _ = engine.calls[0]
    second_sql, second_params = engine.calls[1]
    assert "INSERT INTO turn_run" in first_sql
    assert "SELECT id FROM turn_run" in second_sql
    assert second_params == {"user_sub": "u", "client_turn_key": "retry-key"}


# ---------------------------------------------------------------------------
# offline -- append_llm_call / append_tool_call / finalize SQL shape
# ---------------------------------------------------------------------------


def test_append_llm_call_generates_insert_with_expected_columns_and_params():
    engine = _RecordingEngine()
    writer = RunLogWriter(engine)

    writer.append_llm_call(
        turn_run_id="turn-1",
        user_sub="u",
        seq=1,
        provider="bedrock",
        model_id="anthropic.claude-x",
        role="router",
        prompt_version="v1",
        prompt_hash="deadbeef",
        input_tokens=100,
        output_tokens=20,
        latency_ms=350,
        status="ok",
    )

    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    assert "INSERT INTO llm_calls" in sql
    assert params["turn_run_id"] == "turn-1"
    assert params["user_sub"] == "u"
    assert params["seq"] == 1
    assert params["provider"] == "bedrock"
    assert params["model_id"] == "anthropic.claude-x"
    assert params["role"] == "router"
    assert params["prompt_version"] == "v1"
    assert params["prompt_hash"] == "deadbeef"
    assert params["input_tokens"] == 100
    assert params["output_tokens"] == 20
    assert params["latency_ms"] == 350
    assert params["status"] == "ok"
    assert params["error"] is None
    assert uuid.UUID(params["id"])


def test_append_llm_call_encodes_error_dict_as_json_when_present():
    engine = _RecordingEngine()
    writer = RunLogWriter(engine)

    writer.append_llm_call(
        turn_run_id="turn-1",
        user_sub="u",
        seq=1,
        provider="bedrock",
        model_id="m",
        role="router",
        prompt_version="v1",
        prompt_hash="h",
        input_tokens=0,
        output_tokens=0,
        latency_ms=None,
        status="error",
        error={"status": 502, "title": "llm provider error", "detail": "boom"},
    )

    _, params = engine.calls[0]
    assert params["error"] == '{"status": 502, "title": "llm provider error", "detail": "boom"}'


def test_append_tool_call_generates_insert_with_expected_columns_and_params():
    engine = _RecordingEngine()
    writer = RunLogWriter(engine)

    writer.append_tool_call(
        turn_run_id="turn-1",
        user_sub="u",
        seq=2,
        tool="data_qa.metric_query",
        server=None,
        args={"metric": "GP"},
        result_digest={"rows": 3},
        status="ok",
        latency_ms=120,
    )

    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    assert "INSERT INTO tool_calls" in sql
    assert params["turn_run_id"] == "turn-1"
    assert params["seq"] == 2
    assert params["tool"] == "data_qa.metric_query"
    assert params["server"] is None
    assert params["args"] == '{"metric": "GP"}'
    assert params["result_digest"] == '{"rows": 3}'
    assert params["status"] == "ok"
    assert params["latency_ms"] == 120
    assert params["error"] is None


def test_finalize_generates_update_with_expected_columns_and_params():
    engine = _RecordingEngine()
    writer = RunLogWriter(engine)

    writer.finalize(
        turn_run_id="turn-1",
        user_sub="u",
        status="ok",
        message_id="msg-1",
        answer_summary="Three customers drove April GP.",
        input_tokens=150,
        output_tokens=40,
        latency_ms=900,
    )

    assert len(engine.calls) == 1
    sql, params = engine.calls[0]
    assert "UPDATE turn_run" in sql
    assert "SET" in sql
    assert "WHERE" in sql
    assert params["turn_run_id"] == "turn-1"
    assert params["status"] == "ok"
    assert params["message_id"] == "msg-1"
    assert params["answer_summary"] == "Three customers drove April GP."
    assert params["input_tokens"] == 150
    assert params["output_tokens"] == 40
    assert params["latency_ms"] == 900
    assert params["error"] is None


# ---------------------------------------------------------------------------
# offline -- never raises (TM1 CSV-writer rule): a broken engine logs ERROR
# and every public method still returns cleanly
# ---------------------------------------------------------------------------


def test_start_turn_never_raises_on_broken_engine_and_logs_error(caplog):
    writer = RunLogWriter(_RaisingEngine())

    with caplog.at_level(logging.ERROR, logger="poseidon.core.runlog"):
        result = writer.start_turn(
            user_sub="u",
            conversation_id=None,
            client_turn_key=None,
            turn_index=None,
            question=None,
            mode="default",
            parsed={},
        )

    assert result is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].name == "poseidon.core.runlog"
    assert "start_turn" in errors[0].message
    assert "RuntimeError" in errors[0].message


def test_append_llm_call_never_raises_on_broken_engine_and_logs_error(caplog):
    writer = RunLogWriter(_RaisingEngine())

    with caplog.at_level(logging.ERROR, logger="poseidon.core.runlog"):
        result = writer.append_llm_call(
            turn_run_id="turn-1",
            user_sub="u",
            seq=1,
            provider="bedrock",
            model_id="m",
            role="router",
            prompt_version="v1",
            prompt_hash="h",
            input_tokens=0,
            output_tokens=0,
            latency_ms=None,
            status="ok",
        )

    assert result is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "append_llm_call" in errors[0].message
    assert "RuntimeError" in errors[0].message


def test_append_tool_call_never_raises_on_broken_engine_and_logs_error(caplog):
    writer = RunLogWriter(_RaisingEngine())

    with caplog.at_level(logging.ERROR, logger="poseidon.core.runlog"):
        result = writer.append_tool_call(
            turn_run_id="turn-1",
            user_sub="u",
            seq=1,
            tool="data_qa.metric_query",
            server=None,
            args={},
            result_digest=None,
            status="ok",
            latency_ms=None,
        )

    assert result is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "append_tool_call" in errors[0].message
    assert "RuntimeError" in errors[0].message


def test_finalize_never_raises_on_broken_engine_and_logs_error(caplog):
    writer = RunLogWriter(_RaisingEngine())

    with caplog.at_level(logging.ERROR, logger="poseidon.core.runlog"):
        result = writer.finalize(
            turn_run_id="turn-1",
            user_sub="u",
            status="ok",
            message_id=None,
            answer_summary=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
        )

    assert result is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "finalize" in errors[0].message
    assert "RuntimeError" in errors[0].message


def test_runlog_module_is_ascii_on_disk():
    """Matches ``test_llm_prompts_module_files_are_ascii_on_disk``'s
    convention -- byte-pinned SQL fragments and log messages stay pinned only
    if no look-alike codepoint can slip into the source. Final-review wave
    item 9: the migration itself (0003, this module's own SQL made real) was
    missing from this guard -- added alongside the two files it already
    covered."""
    paths = (
        Path(runlog.__file__),
        Path(__file__),
        Path(runlog.__file__).resolve().parents[2] / "migrations" / "versions" / "0003_run_log.py",
    )
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ---------------------------------------------------------------------------
# pg -- real Postgres, migrated to head (docker compose db + alembic upgrade)
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"


@pytest.fixture
def pg_engine():
    """Yields a real SQLAlchemy ``Engine`` against ``DATABASE_URL``, or SKIPS
    (not module-level -- this file also holds offline tests that must always
    run) with an actionable reason: unset, unreachable within 2 seconds, or
    reachable but not migrated to 0003 yet.

    Mirrors ``test_synthetic_client_pg.py``'s two-stage guard (bare connect,
    then a real query) with the second stage narrowed to "does ``turn_run``
    exist" rather than "is the table non-empty" -- these tests create their
    own rows, they do not read a pre-seeded fixture.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip(
            f"DATABASE_URL is not set - pg run-log tests need a Postgres: {_UP_HINT}, "
            "migrate it with `python -m alembic upgrade head`"
        )
    try:
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.turn_run')")
                if cur.fetchone()[0] is None:
                    pytest.skip(
                        "turn_run does not exist - migrate with "
                        "`python -m alembic upgrade head` (revision 0003)"
                    )
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


def _fresh_user_sub() -> str:
    """A ``user_sub`` unique to this test invocation, so re-running the pg
    suite against a long-lived dev Postgres never sees a previous run's rows
    (see the module docstring)."""
    return f"pg-test|{uuid.uuid4()}"


@pytest.mark.pg
def test_start_turn_idempotent_same_client_turn_key_no_second_row(pg_engine):
    writer = RunLogWriter(pg_engine)
    user_sub = _fresh_user_sub()
    client_turn_key = str(uuid.uuid4())

    first = writer.start_turn(
        user_sub=user_sub,
        conversation_id=None,
        client_turn_key=client_turn_key,
        turn_index=1,
        question="first send",
        mode="default",
        parsed={},
    )
    second = writer.start_turn(
        user_sub=user_sub,
        conversation_id=None,
        client_turn_key=client_turn_key,
        turn_index=1,
        question="retried send",
        mode="default",
        parsed={},
    )

    assert first is not None
    assert first.created is True
    assert second == TurnHandle(turn_run_id=first.turn_run_id, created=False)

    with pg_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM turn_run WHERE user_sub = :u AND client_turn_key = :k"),
            {"u": user_sub, "k": client_turn_key},
        ).scalar_one()
    assert count == 1


@pytest.mark.pg
def test_start_turn_without_client_turn_key_never_conflicts(pg_engine):
    """``client_turn_key IS NULL`` never equals another ``NULL`` under
    Postgres unique-constraint semantics, so two ``start_turn`` calls with no
    key each create their own row -- the shape every ``memory_update`` run
    (and any chat turn a client sent with no idempotency key) relies on."""
    writer = RunLogWriter(pg_engine)
    user_sub = _fresh_user_sub()

    first = writer.start_turn(
        user_sub=user_sub,
        conversation_id=None,
        client_turn_key=None,
        turn_index=None,
        question=None,
        mode="default",
        parsed={},
        kind="memory_update",
    )
    second = writer.start_turn(
        user_sub=user_sub,
        conversation_id=None,
        client_turn_key=None,
        turn_index=None,
        question=None,
        mode="default",
        parsed={},
        kind="memory_update",
    )

    assert first is not None and second is not None
    assert first.created is True
    assert second.created is True
    assert first.turn_run_id != second.turn_run_id


@pytest.mark.pg
def test_kind_memory_update_accepted_with_null_conversation_and_message(pg_engine):
    writer = RunLogWriter(pg_engine)
    user_sub = _fresh_user_sub()

    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=None,
        client_turn_key=None,
        turn_index=None,
        question=None,
        mode="default",
        parsed={},
        kind="memory_update",
    )

    assert handle is not None
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT kind, conversation_id, message_id, status FROM turn_run WHERE id = :id"),
            {"id": handle.turn_run_id},
        ).first()
    assert row is not None
    assert row[0] == "memory_update"
    assert row[1] is None
    assert row[2] is None
    assert row[3] == "running"


@pytest.mark.pg
def test_finalize_rolls_up_child_llm_call_tokens(pg_engine):
    writer = RunLogWriter(pg_engine)
    user_sub = _fresh_user_sub()
    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=None,
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question="q",
        mode="default",
        parsed={},
    )
    assert handle is not None

    writer.append_llm_call(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        seq=1,
        provider="bedrock",
        model_id="m",
        role="router",
        prompt_version="v1",
        prompt_hash="h1",
        input_tokens=100,
        output_tokens=20,
        latency_ms=300,
        status="ok",
    )
    writer.append_llm_call(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        seq=2,
        provider="bedrock",
        model_id="m",
        role="synthesis",
        prompt_version="v1",
        prompt_hash="h2",
        input_tokens=250,
        output_tokens=80,
        latency_ms=500,
        status="ok",
    )

    writer.finalize(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        status="ok",
        message_id=str(uuid.uuid4()),
        answer_summary="done",
        input_tokens=100 + 250,
        output_tokens=20 + 80,
        latency_ms=900,
    )

    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, input_tokens, output_tokens, latency_ms, finished_at "
                "FROM turn_run WHERE id = :id"
            ),
            {"id": handle.turn_run_id},
        ).first()
    assert row is not None
    assert row[0] == "ok"
    assert row[1] == 350
    assert row[2] == 100
    assert row[3] == 900
    assert row[4] is not None


@pytest.mark.pg
def test_append_llm_call_seq_conflict_is_logged_not_raised_and_not_duplicated(pg_engine, caplog):
    writer = RunLogWriter(pg_engine)
    user_sub = _fresh_user_sub()
    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=None,
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question="q",
        mode="default",
        parsed={},
    )
    assert handle is not None

    writer.append_llm_call(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        seq=1,
        provider="bedrock",
        model_id="m",
        role="router",
        prompt_version="v1",
        prompt_hash="h1",
        input_tokens=10,
        output_tokens=1,
        latency_ms=100,
        status="ok",
    )
    with caplog.at_level(logging.ERROR, logger="poseidon.core.runlog"):
        writer.append_llm_call(
            turn_run_id=handle.turn_run_id,
            user_sub=user_sub,
            seq=1,  # same (turn_run_id, seq) -- unique constraint violation
            provider="bedrock",
            model_id="m",
            role="router",
            prompt_version="v1",
            prompt_hash="h-duplicate",
            input_tokens=999,
            output_tokens=999,
            latency_ms=999,
            status="ok",
        )

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "append_llm_call" in errors[0].message

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT prompt_hash FROM llm_calls WHERE turn_run_id = :id AND seq = 1"),
            {"id": handle.turn_run_id},
        ).all()
    assert len(rows) == 1
    assert rows[0][0] == "h1"  # the duplicate never landed


@pytest.mark.pg
def test_append_tool_call_seq_conflict_is_logged_not_raised_and_not_duplicated(pg_engine, caplog):
    writer = RunLogWriter(pg_engine)
    user_sub = _fresh_user_sub()
    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=None,
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question="q",
        mode="default",
        parsed={},
    )
    assert handle is not None

    writer.append_tool_call(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        seq=1,
        tool="data_qa.metric_query",
        server=None,
        args={"metric": "GP"},
        result_digest={"rows": 1},
        status="ok",
        latency_ms=50,
    )
    with caplog.at_level(logging.ERROR, logger="poseidon.core.runlog"):
        writer.append_tool_call(
            turn_run_id=handle.turn_run_id,
            user_sub=user_sub,
            seq=1,  # same (turn_run_id, seq) -- unique constraint violation
            tool="data_qa.metric_query",
            server=None,
            args={"metric": "VOLUME"},
            result_digest=None,
            status="ok",
            latency_ms=50,
        )

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "append_tool_call" in errors[0].message

    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT args FROM tool_calls WHERE turn_run_id = :id AND seq = 1"),
            {"id": handle.turn_run_id},
        ).all()
    assert len(rows) == 1


@pytest.mark.pg
def test_finalize_invalid_status_check_constraint_is_logged_not_raised(pg_engine, caplog):
    writer = RunLogWriter(pg_engine)
    user_sub = _fresh_user_sub()
    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=None,
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question="q",
        mode="default",
        parsed={},
    )
    assert handle is not None

    with caplog.at_level(logging.ERROR, logger="poseidon.core.runlog"):
        result = writer.finalize(
            turn_run_id=handle.turn_run_id,
            user_sub=user_sub,
            status="bogus",  # not in ('running','ok','clarify','error')
            message_id=None,
            answer_summary=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
        )

    assert result is None
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "finalize" in errors[0].message

    # The whole UPDATE rolled back -- status is still 'running', untouched.
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM turn_run WHERE id = :id"), {"id": handle.turn_run_id}
        ).first()
    assert row[0] == "running"


@pytest.mark.pg
def test_full_turn_round_trip_persists_every_field_including_json(pg_engine):
    """One turn through the whole writer surface -- start, an llm call, a
    tool call, finalize -- with every field read back from Postgres,
    including the ``jsonb`` ones (``parsed``/``args``/``result_digest``/
    ``error``), proving the plain ``json.dumps`` + bound-parameter approach
    actually round-trips against a real ``jsonb`` column (not just something
    a fake engine accepted)."""
    writer = RunLogWriter(pg_engine)
    user_sub = _fresh_user_sub()

    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=str(uuid.uuid4()),
        client_turn_key=str(uuid.uuid4()),
        turn_index=3,
        question="top gp for maersk in singapore",
        mode="existing",
        parsed={"customer": "MAERSK LINE", "confidence": 0.93},
        trace_id="trace-xyz",
    )
    assert handle is not None and handle.created is True

    writer.append_llm_call(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        seq=1,
        provider="bedrock",
        model_id="anthropic.claude-x",
        role="router",
        prompt_version="v1",
        prompt_hash="abc123",
        input_tokens=120,
        output_tokens=30,
        latency_ms=400,
        status="ok",
    )
    writer.append_tool_call(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        seq=1,
        tool="data_qa.metric_query",
        server=None,
        args={"metric": "GP", "entity": "MARINE_SALES_PLANNING_V"},
        result_digest={"rows": 5},
        status="ok",
        latency_ms=80,
    )
    message_id = str(uuid.uuid4())
    writer.finalize(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        status="ok",
        message_id=message_id,
        answer_summary="Maersk's April GP in Singapore was $412K.",
        input_tokens=120,
        output_tokens=30,
        latency_ms=520,
    )

    with pg_engine.connect() as conn:
        turn = conn.execute(
            text(
                "SELECT kind, user_sub, turn_index, question, mode, parsed, status, "
                # message_id::text: SQLAlchemy/psycopg hand back a genuine
                # uuid.UUID object for an untouched UUID column, not the str
                # the writer was given -- cast at the SQL level so the
                # comparison below is against the same plain string either
                # side of the round trip.
                "message_id::text, answer_summary, input_tokens, output_tokens, trace_id "
                "FROM turn_run WHERE id = :id"
            ),
            {"id": handle.turn_run_id},
        ).first()
        llm_call = conn.execute(
            text(
                "SELECT provider, model_id, prompt_version, prompt_hash FROM llm_calls "
                "WHERE turn_run_id = :id"
            ),
            {"id": handle.turn_run_id},
        ).first()
        tool_call = conn.execute(
            text("SELECT tool, args, result_digest FROM tool_calls WHERE turn_run_id = :id"),
            {"id": handle.turn_run_id},
        ).first()

    assert turn == (
        "chat_turn",
        user_sub,
        3,
        "top gp for maersk in singapore",
        "existing",
        {"customer": "MAERSK LINE", "confidence": 0.93},
        "ok",
        message_id,
        "Maersk's April GP in Singapore was $412K.",
        120,
        30,
        "trace-xyz",
    )
    assert llm_call == ("bedrock", "anthropic.claude-x", "v1", "abc123")
    assert tool_call == (
        "data_qa.metric_query",
        {"metric": "GP", "entity": "MARINE_SALES_PLANNING_V"},
        {"rows": 5},
    )
