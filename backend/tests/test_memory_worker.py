"""Tests for Phase 13 Task 4 (doc 05 section 5, decision D31): the
idle-triggered outbox worker (``poseidon.scripts.memory_worker``).

**What this suite drives, and what it deliberately does not.** The worker's
shipped entry point is an infinite ``while True`` polling loop -- untestable
by construction and untested by design (it is a four-line shell around the
one function that does the work, mirroring every other script in
``poseidon/scripts/``). Everything below drives that ONE-cycle unit,
:func:`~poseidon.scripts.memory_worker.run_once`, and the claim query
underneath it, directly.

**No live LLM call, ever.** Every test registers its own scripted
:class:`~poseidon.core.llm.roles.LLMProvider` double under the ``"stub"``
key with ``LLM_MODE=stub``, exactly the seam ``core/chat/dev_router.py``'s
``DevDeterministicRouter`` already established for the router role --
``RoleClient.invoke`` routes every role to the ``"stub"`` provider in that
mode regardless of what ``models.yml`` configures, so the worker's own
production call path (``role_client.invoke("memory", ...)``) is what runs,
with a deterministic answer.

**Privilege model (Global Constraints, verified empirically before this
file was written).** The claim query is the ONE operation in this phase
that reads ``memory_outbox`` outside a per-user ``rls_transaction``: it
runs on the raw, privileged ``DATABASE_URL`` connection, which this
project's compose Postgres bootstraps as the cluster superuser
(``rolsuper = true``, ``rolbypassrls = true`` -- see this module's own
``test_the_claim_connection_actually_bypasses_row_level_security``, which
re-proves that empirically rather than trusting the deployment note). Every
OTHER operation the worker performs -- reading the conversation, writing
the memory version, moving the outbox row to ``done``/``failed`` -- goes
through the ordinary ``Store.for_user(sub)``/``rls_transaction`` path.

Mirrors ``test_personalization_stores.py``'s pg conventions exactly: a
module-level probe skips the whole file if ``DATABASE_URL`` is unset,
unreachable, or migration 0008 has not been applied; every fixture row is
built through a real store (``HistoryStore`` for conversations/messages),
never a bare INSERT for content rows.
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text

from poseidon.core.chat.history import HistoryStore
from poseidon.core.config import Settings, get_settings
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.db import build_engine, rls_transaction
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.types import LLMResponse
from poseidon.core.util.uuid7 import uuid7
from poseidon.scripts import memory_worker

pytestmark = pytest.mark.pg

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0008)"

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        f"DATABASE_URL is not set - pg memory-worker tests need a Postgres: "
        f"{_UP_HINT}, {_MIGRATE_HINT}",
        allow_module_level=True,
    )

_DSN_ROLE_IS_SUPERUSER = False
try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'memory_outbox'"
            )
            if _cur.fetchone() is None:
                pytest.skip(
                    f"memory_outbox does not exist - {_MIGRATE_HINT}",
                    allow_module_level=True,
                )
            _cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            _DSN_ROLE_IS_SUPERUSER = _cur.fetchone()[0]
except psycopg.Error as exc:
    pytest.skip(
        f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}",
        allow_module_level=True,
    )

_APP_ROLE = "poseidon_app"
_EFFECTIVE_APP_ROLE = _APP_ROLE if _DSN_ROLE_IS_SUPERUSER else None

_IDLE_MINUTES = 30


# ---------------------------------------------------------------------------
# fixtures and small helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_engine():
    engine = build_engine(_DSN)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def quiet_queue(pg_engine):
    """Park every pre-existing claimable outbox row for the duration of one
    test, and put it back afterwards.

    This suite is the first in this codebase to drive a query with NO
    ``user_sub`` predicate at all (the worker's claim is cross-user by
    construction -- see ``memory_worker``'s own module docstring), so the
    per-test ``_fresh_user_sub()`` isolation every other pg suite here
    relies on does not reach it: a long-lived dev database accumulates
    ``memory_outbox`` rows from every suite that ever ran against it (246
    of them when this file was written), and without this fixture
    ``run_once`` would claim, distill and mark ``done`` two dozen unrelated
    abandoned conversations on every single test, while the row the test
    actually cares about sat outside the batch limit.

    Parking is a ``last_turn_at`` bump to ``now()`` (which makes a row
    non-idle, hence unclaimable) applied only to rows that are ``pending``
    when the test starts, with every original value restored on the way
    out -- fully reversible, and it never touches a row's ``status``,
    ``attempts`` or ``last_error``. Runs on the raw privileged connection
    for the same reason the claim query does: no single identity could see
    these rows.
    """
    with pg_engine.begin() as conn:
        parked = conn.execute(
            text(
                "SELECT conversation_id, last_turn_at FROM memory_outbox "
                "WHERE status = 'pending'"
            )
        ).all()
        conn.execute(text("UPDATE memory_outbox SET last_turn_at = now() WHERE status = 'pending'"))
    try:
        yield
    finally:
        if parked:
            with pg_engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE memory_outbox SET last_turn_at = :t WHERE conversation_id = :c"
                    ),
                    [{"t": row.last_turn_at, "c": row.conversation_id} for row in parked],
                )


@pytest.fixture
def worker_settings(monkeypatch):
    """A hermetic ``Settings`` for the worker under test -- the identical
    env-clearing/``get_settings.cache_clear()`` discipline
    ``test_personalization_stores.py``'s own ``settings_override`` fixture
    uses (see that fixture's docstring), returning the constructed
    ``Settings`` because ``run_once`` takes one as an explicit argument
    rather than reading the process-wide singleton itself."""
    applied = {"done": False}

    def _apply(**overrides) -> Settings:
        for key in (name.upper() for name in Settings.model_fields):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("DATABASE_URL", _DSN)
        monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
        monkeypatch.setenv("LLM_MODE", "stub")
        monkeypatch.setenv(
            "DATABASE_APP_ROLE", _EFFECTIVE_APP_ROLE if _EFFECTIVE_APP_ROLE else ""
        )
        for key, value in overrides.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()
        applied["done"] = True
        return get_settings()

    yield _apply
    if applied["done"]:
        get_settings.cache_clear()


class _ScriptedDistiller:
    """A deterministic stand-in for the ``memory`` role, registered under
    the ``"stub"`` provider key -- the same in-repo-fake precedent
    ``DevDeterministicRouter`` set for the router role (see this module's
    docstring). ``script`` is a list of either a ``str`` (returned verbatim
    as the response text) or an ``Exception`` INSTANCE (raised instead of
    answering, simulating a provider failure); the last element repeats
    forever once the script is exhausted, so a test that runs several
    cycles against the same behavior needs only one element."""

    def __init__(self, script) -> None:
        self._script = list(script)
        self.calls: list[dict] = []

    def invoke(self, *, system, messages, tools, model, params) -> LLMResponse:
        self.calls.append({"system": system, "messages": messages, "model": model})
        step = self._script.pop(0) if len(self._script) > 1 else self._script[0]
        if isinstance(step, BaseException):
            raise step
        return LLMResponse(
            text=step, tool_calls=(), stop_reason="end_turn", input_tokens=11, output_tokens=7
        )


def _role_client(settings: Settings, provider) -> RoleClient:
    return RoleClient(settings, providers={"stub": provider})


def _fresh_user_sub() -> str:
    return f"test|{uuid.uuid4().hex}"


def _entries_json(*statements: str) -> str:
    """A distiller answer: the JSON array shape ``distill.md`` pins."""
    return json.dumps([{"type": "preference", "statement": s} for s in statements])


def _create_conversation(pg_engine, user_sub: str, *, user_turns: int = 2) -> str:
    """A real conversation with ``user_turns`` user messages (each answered
    by an assistant message), built through ``HistoryStore`` -- never a bare
    INSERT, matching ``test_personalization_stores.py``'s own convention.
    ``create_conversation`` itself persists one assistant opener, so the
    conversation always holds ``2 * user_turns + 1`` messages."""
    history = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    conversation, _opener = history.create_conversation()
    cid = conversation["id"]
    for index in range(user_turns):
        history.append_user_message(cid, str(uuid7()), f"user message {index}", None)
        history.write_assistant_message(
            cid,
            {
                "id": str(uuid7()),
                "role": "assistant",
                "parts": [
                    {"kind": "text", "payload": {"markdown": f"assistant reply {index}"}}
                ],
            },
            None,
        )
    return cid


def _seed_outbox(
    pg_engine,
    user_sub: str,
    conversation_id: str,
    *,
    minutes_ago: int,
    status: str = "pending",
    attempts: int = 0,
) -> datetime:
    """Insert this conversation's outbox row with an explicit
    ``last_turn_at`` (``touch()`` always stamps ``now()``, which no test of
    an IDLE row could ever use) -- through ``rls_transaction`` with the
    owning user's identity, so even this fixture write obeys the same RLS
    path everything else in this phase does."""
    last_turn_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    with rls_transaction(pg_engine, user_sub, app_role=_EFFECTIVE_APP_ROLE) as conn:
        conn.execute(
            text(
                "INSERT INTO memory_outbox "
                "(conversation_id, user_sub, last_turn_at, status, attempts) "
                "VALUES (:c, :u, :t, :s, :a)"
            ),
            {
                "c": conversation_id,
                "u": user_sub,
                "t": last_turn_at,
                "s": status,
                "a": attempts,
            },
        )
    return last_turn_at


def _outbox_row(pg_engine, conversation_id: str):
    with pg_engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT status, attempts, last_error, last_turn_at FROM memory_outbox "
                "WHERE conversation_id = :c"
            ),
            {"c": conversation_id},
        ).first()


def _memory_versions(pg_engine, user_sub: str):
    with pg_engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT version, entries, created_by FROM user_memory "
                "WHERE user_sub = :u ORDER BY version"
            ),
            {"u": user_sub},
        ).all()


def _turn_runs(pg_engine, user_sub: str):
    with pg_engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT id, kind, status, conversation_id, question, answer_summary, error "
                "FROM turn_run WHERE user_sub = :u ORDER BY created_at"
            ),
            {"u": user_sub},
        ).all()


def _llm_calls(pg_engine, user_sub: str):
    with pg_engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT role, provider, model_id, status, input_tokens, output_tokens, "
                "prompt_version, prompt_hash FROM llm_calls WHERE user_sub = :u ORDER BY seq"
            ),
            {"u": user_sub},
        ).all()


# ===========================================================================
# offline: ASCII-on-disk (house rule; matches test_personalization_stores.py)
# ===========================================================================


def test_worker_module_prompt_and_this_test_module_are_ascii_on_disk():
    paths = [
        Path(memory_worker.__file__),
        Path(memory_worker.__file__).resolve().parents[1]
        / "config"
        / "prompts"
        / "memory"
        / "distill.md",
        Path(__file__),
    ]
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ===========================================================================
# Settings: the two new fields (Global Constraints)
# ===========================================================================


def test_settings_carries_the_two_new_memory_worker_fields_with_their_pinned_defaults(
    worker_settings,
):
    settings = worker_settings()

    assert settings.memory_idle_minutes == 30
    assert settings.memory_max_attempts == 5


# ===========================================================================
# the privilege model and the claim query
# ===========================================================================


def test_the_claim_connection_actually_bypasses_row_level_security(pg_engine):
    """Global Constraints' load-bearing precondition, re-proved here rather
    than trusted: the worker's cross-user claim query runs on the RAW
    DATABASE_URL connection with no ``rls_transaction`` wrapper and no
    ``app.user_sub`` set at all -- which returns rows only because this
    role bypasses row-level security. If this ever stops holding, the claim
    query silently returns zero rows forever (an unset ``app.user_sub``
    fails closed) and the worker quietly stops working."""
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).first()

    assert row.rolsuper or row.rolbypassrls, (
        "the DATABASE_URL role must bypass RLS for the cross-user claim query to see "
        "any row at all -- see this module's docstring and the task report"
    )


def test_claim_returns_only_the_idle_pending_row(pg_engine):
    """Seeds all three shapes the brief names -- a fresh pending row, an
    old-enough pending row, an old-enough but already-``done`` row -- and
    asserts exactly the middle one claims."""
    user_sub = _fresh_user_sub()
    fresh = _create_conversation(pg_engine, user_sub)
    idle = _create_conversation(pg_engine, user_sub)
    already_done = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, fresh, minutes_ago=1)
    _seed_outbox(pg_engine, user_sub, idle, minutes_ago=_IDLE_MINUTES + 5)
    _seed_outbox(pg_engine, user_sub, already_done, minutes_ago=_IDLE_MINUTES + 5, status="done")

    with pg_engine.begin() as conn:
        claimed = memory_worker.claim_idle_conversations(
            conn, idle_minutes=_IDLE_MINUTES, limit=100
        )

    claimed_here = [row for row in claimed if row.user_sub == user_sub]
    assert [row.conversation_id for row in claimed_here] == [idle]
    assert claimed_here[0].attempts == 0


def test_claim_skips_a_row_another_transaction_already_locked(pg_engine):
    """``FOR UPDATE SKIP LOCKED``, proved against real concurrent
    transactions rather than asserted from the SQL text: a second claim
    running while the first transaction still holds its row lock must
    return that row NOT AT ALL -- neither a duplicate (which would let two
    workers distill the same conversation) nor a block (which would stall
    the whole worker behind one slow cycle)."""
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)

    with pg_engine.begin() as first_conn:
        first = memory_worker.claim_idle_conversations(
            first_conn, idle_minutes=_IDLE_MINUTES, limit=100
        )
        assert cid in [row.conversation_id for row in first]

        # A genuinely separate connection/transaction from the engine's own
        # pool, opened while the first still holds its FOR UPDATE lock.
        with pg_engine.begin() as second_conn:
            second = memory_worker.claim_idle_conversations(
                second_conn, idle_minutes=_IDLE_MINUTES, limit=100
            )

    assert cid not in [row.conversation_id for row in second]


def test_claim_respects_the_configured_idle_threshold(pg_engine):
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=10)

    with pg_engine.begin() as conn:
        at_thirty = memory_worker.claim_idle_conversations(conn, idle_minutes=30, limit=100)
        at_five = memory_worker.claim_idle_conversations(conn, idle_minutes=5, limit=100)

    assert cid not in [row.conversation_id for row in at_thirty]
    assert cid in [row.conversation_id for row in at_five]


# ===========================================================================
# run_once: the happy path
# ===========================================================================


def test_successful_distillation_writes_a_version_a_turn_run_and_marks_the_row_done(
    pg_engine, worker_settings
):
    settings = worker_settings()
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub, user_turns=3)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)
    distiller = _ScriptedDistiller([_entries_json("prefers GP in USD thousands")])

    processed = memory_worker.run_once(pg_engine, settings, _role_client(settings, distiller))

    assert processed >= 1
    versions = _memory_versions(pg_engine, user_sub)
    assert len(versions) == 1
    assert versions[0].version == 1
    assert versions[0].created_by == "distiller"
    assert [e["statement"] for e in versions[0].entries] == ["prefers GP in USD thousands"]
    runs = _turn_runs(pg_engine, user_sub)
    assert len(runs) == 1
    assert runs[0].kind == "memory_update"
    assert runs[0].status == "ok"
    assert str(runs[0].conversation_id) == cid
    row = _outbox_row(pg_engine, cid)
    assert row.status == "done"
    assert row.attempts == 0
    assert row.last_error is None


def test_the_worker_stamps_provenance_itself_and_never_takes_it_from_the_model(
    pg_engine, worker_settings
):
    """``source_conversation_id``/``at`` are authored by the worker, from
    the row it actually claimed and the clock -- never read off the model's
    own answer, even when the model supplies them (here, deliberately
    wrong ones)."""
    settings = worker_settings()
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)
    lying_answer = json.dumps(
        [
            {
                "type": "fact",
                "statement": "a real statement",
                "source_conversation_id": str(uuid7()),
                "at": "1999-01-01T00:00:00+00:00",
            }
        ]
    )

    memory_worker.run_once(
        pg_engine, settings, _role_client(settings, _ScriptedDistiller([lying_answer]))
    )

    entry = _memory_versions(pg_engine, user_sub)[0].entries[0]
    assert entry["source_conversation_id"] == cid
    assert entry["at"].startswith(str(datetime.now(UTC).year))


def test_an_existing_entry_the_model_echoes_keeps_its_original_provenance(
    pg_engine, worker_settings
):
    """Doc 05 section 5: "the distiller treats user-authored entries as
    ground truth it must preserve verbatim". An entry the model echoes back
    unchanged must keep the conversation/timestamp it was FIRST attributed
    to, not be re-stamped with this run's conversation."""
    from poseidon.core.personalization.memory import MemoryStore

    settings = worker_settings()
    user_sub = _fresh_user_sub()
    old_cid = str(uuid7())
    memory = MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    memory.write_version(
        [
            {
                "type": "preference",
                "statement": "always show GP in USD thousands",
                "source_conversation_id": old_cid,
                "at": "2026-01-02T03:04:05+00:00",
            }
        ],
        created_by="user",
    )
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)
    answer = json.dumps(
        [
            {"type": "preference", "statement": "always show GP in USD thousands"},
            {"type": "scope", "statement": "works the Rotterdam book"},
        ]
    )

    memory_worker.run_once(
        pg_engine, settings, _role_client(settings, _ScriptedDistiller([answer]))
    )

    current = memory.get_current()
    assert current["version"] == 2
    assert current["created_by"] == "distiller"
    preserved = next(e for e in current["entries"] if e["type"] == "preference")
    fresh = next(e for e in current["entries"] if e["type"] == "scope")
    assert preserved["source_conversation_id"] == old_cid
    assert preserved["at"] == "2026-01-02T03:04:05+00:00"
    assert fresh["source_conversation_id"] == cid


def test_an_empty_distillation_marks_the_row_done_without_writing_a_version(
    pg_engine, worker_settings
):
    """"Nothing here is worth remembering" is a real, successful answer --
    it must not churn an identical new memory version, and it must not
    leave the row pending to be re-distilled on the next poll forever."""
    settings = worker_settings()
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)

    memory_worker.run_once(pg_engine, settings, _role_client(settings, _ScriptedDistiller(["[]"])))

    assert _memory_versions(pg_engine, user_sub) == []
    assert _outbox_row(pg_engine, cid).status == "done"
    runs = _turn_runs(pg_engine, user_sub)
    assert [run.status for run in runs] == ["ok"]


def test_the_distillation_call_logs_an_llm_calls_row_for_the_memory_role(
    pg_engine, worker_settings
):
    """Doc 06 section 1 / ``scripts/cost_rollup.py``'s own docstring: a
    background memory-consolidation run "still burned real model tokens",
    and that script rolls spend up from ``llm_calls``, not ``turn_run`` --
    so the worker's own model call has to land a row there or its spend is
    invisible to every cost report."""
    settings = worker_settings()
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)

    memory_worker.run_once(
        pg_engine, settings, _role_client(settings, _ScriptedDistiller([_entries_json("x")]))
    )

    calls = _llm_calls(pg_engine, user_sub)
    assert len(calls) == 1
    assert calls[0].role == "memory"
    assert calls[0].provider == "stub"
    assert calls[0].status == "ok"
    assert calls[0].input_tokens == 11
    assert calls[0].output_tokens == 7
    assert calls[0].prompt_version == "v1"
    assert calls[0].prompt_hash


def test_the_rendered_prompt_carries_the_transcript_and_the_existing_entries(
    pg_engine, worker_settings
):
    from poseidon.core.personalization.memory import MemoryStore

    settings = worker_settings()
    user_sub = _fresh_user_sub()
    MemoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub).write_version(
        [
            {
                "type": "fact",
                "statement": "an already-known fact",
                "source_conversation_id": str(uuid7()),
                "at": "2026-01-02T03:04:05+00:00",
            }
        ],
        created_by="user",
    )
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)
    distiller = _ScriptedDistiller(["[]"])

    memory_worker.run_once(pg_engine, settings, _role_client(settings, distiller))

    assert len(distiller.calls) == 1
    system = distiller.calls[0]["system"]
    assert "user message 0" in system
    assert "assistant reply 0" in system
    assert "an already-known fact" in system


# ===========================================================================
# run_once: the retry / failure ladder
# ===========================================================================


def test_a_failed_distillation_increments_attempts_and_leaves_the_row_pending(
    pg_engine, worker_settings
):
    settings = worker_settings(memory_max_attempts=5)
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)
    failing = _ScriptedDistiller([RuntimeError("provider exploded")])

    memory_worker.run_once(pg_engine, settings, _role_client(settings, failing))

    row = _outbox_row(pg_engine, cid)
    assert row.status == "pending", "a failure must stay claimable so the NEXT cycle retries it"
    assert row.attempts == 1
    assert row.last_error is not None
    assert _memory_versions(pg_engine, user_sub) == []
    runs = _turn_runs(pg_engine, user_sub)
    assert [run.status for run in runs] == ["error"]
    assert runs[0].error is not None


def test_a_malformed_distiller_answer_is_a_failure_not_a_silent_success(
    pg_engine, worker_settings
):
    settings = worker_settings(memory_max_attempts=5)
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)

    memory_worker.run_once(
        pg_engine,
        settings,
        _role_client(settings, _ScriptedDistiller(["I'm not JSON, sorry!"])),
    )

    row = _outbox_row(pg_engine, cid)
    assert row.status == "pending"
    assert row.attempts == 1
    assert _memory_versions(pg_engine, user_sub) == []


@pytest.mark.parametrize(
    "answer,override",
    [
        # MemoryTooLarge: a well-formed answer whose rendered document
        # overflows settings.memory_max_chars.
        (_entries_json("a statement far longer than ten characters"), {"memory_max_chars": 10}),
        # MemoryValidationError: a `type` outside the closed set. Deliberately
        # NOT rejected by _parse_entries -- core/personalization/memory.py's
        # own _validate_entries owns that list, and this proves the worker
        # handles its exception rather than duplicating its rule.
        (json.dumps([{"type": "vibe", "statement": "not a real entry type"}]), {}),
    ],
)
def test_a_write_version_rejection_is_recorded_as_an_attempt_not_retried_forever(
    pg_engine, worker_settings, answer, override
):
    """Self-review regression: ``UserMemory.write_version`` raises on its own
    account, AFTER the model call has already succeeded and been paid for.
    An earlier draft guarded only the invoke, so either exception escaped to
    ``run_once``'s per-row net -- which logs but records NO attempt, leaving
    the row ``pending`` with ``attempts`` frozen at 0 and re-claimed on every
    poll forever, burning a model call each cycle on an answer that can never
    land. Both must instead land on the ordinary failure ladder."""
    settings = worker_settings(memory_max_attempts=5, **override)
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)

    memory_worker.run_once(
        pg_engine, settings, _role_client(settings, _ScriptedDistiller([answer]))
    )

    row = _outbox_row(pg_engine, cid)
    assert row.attempts == 1, "the attempt must be recorded, not silently retried forever"
    assert row.status == "pending"
    assert row.last_error is not None
    assert _memory_versions(pg_engine, user_sub) == []
    runs = _turn_runs(pg_engine, user_sub)
    assert [run.status for run in runs] == ["error"]
    # The model call itself succeeded and cost real tokens -- that spend is
    # recorded regardless of what happened to its answer afterwards.
    assert [call.status for call in _llm_calls(pg_engine, user_sub)] == ["ok"]


def test_attempts_reaching_max_attempts_moves_the_row_to_failed_with_last_error(
    pg_engine, worker_settings
):
    settings = worker_settings(memory_max_attempts=2)
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)
    role_client = _role_client(settings, _ScriptedDistiller([RuntimeError("still broken")]))

    memory_worker.run_once(pg_engine, settings, role_client)
    after_first = _outbox_row(pg_engine, cid)
    memory_worker.run_once(pg_engine, settings, role_client)
    after_second = _outbox_row(pg_engine, cid)

    assert (after_first.status, after_first.attempts) == ("pending", 1)
    assert (after_second.status, after_second.attempts) == ("failed", 2)
    assert after_second.last_error is not None
    assert "still broken" in json.dumps(after_second.last_error)


def test_a_failed_row_is_never_claimed_again(pg_engine, worker_settings):
    settings = worker_settings(memory_max_attempts=1)
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)
    role_client = _role_client(settings, _ScriptedDistiller([RuntimeError("terminal")]))
    memory_worker.run_once(pg_engine, settings, role_client)
    assert _outbox_row(pg_engine, cid).status == "failed"

    with pg_engine.begin() as conn:
        claimed = memory_worker.claim_idle_conversations(
            conn, idle_minutes=_IDLE_MINUTES, limit=100
        )

    assert cid not in [row.conversation_id for row in claimed]


def test_one_bad_row_does_not_stop_the_rest_of_the_cycle(pg_engine, worker_settings):
    """The polling loop's own resilience contract: a single row whose
    distillation blows up must not take the whole cycle (and every other
    user's queued conversation) down with it."""
    settings = worker_settings()
    doomed_sub = _fresh_user_sub()
    healthy_sub = _fresh_user_sub()
    doomed_cid = _create_conversation(pg_engine, doomed_sub)
    healthy_cid = _create_conversation(pg_engine, healthy_sub)
    _seed_outbox(pg_engine, doomed_sub, doomed_cid, minutes_ago=_IDLE_MINUTES + 20)
    _seed_outbox(pg_engine, healthy_sub, healthy_cid, minutes_ago=_IDLE_MINUTES + 10)
    # Oldest-first claim order puts the doomed row first; the script's first
    # step raises, and its (repeating) last step answers normally.
    distiller = _ScriptedDistiller([RuntimeError("boom"), _entries_json("survived")])

    memory_worker.run_once(pg_engine, settings, _role_client(settings, distiller))

    assert _outbox_row(pg_engine, doomed_cid).status == "pending"
    assert _outbox_row(pg_engine, healthy_cid).status == "done"
    assert len(_memory_versions(pg_engine, healthy_sub)) == 1


# ===========================================================================
# run_once: the minimum-turn-count skip
# ===========================================================================


def test_a_conversation_under_the_minimum_turn_count_is_skipped_untouched(
    pg_engine, worker_settings
):
    """Brief: skipped "without incrementing attempts or changing status" --
    a trivial conversation is not a failure, so it must not burn a retry,
    and it must not call the model at all."""
    settings = worker_settings()
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub, user_turns=memory_worker.MIN_USER_TURNS - 1)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)
    distiller = _ScriptedDistiller([_entries_json("never reached")])

    memory_worker.run_once(pg_engine, settings, _role_client(settings, distiller))

    row = _outbox_row(pg_engine, cid)
    assert row.status == "pending"
    assert row.attempts == 0
    assert row.last_error is None
    assert distiller.calls == [], "a skipped conversation must never reach the model"
    assert _memory_versions(pg_engine, user_sub) == []
    assert _turn_runs(pg_engine, user_sub) == []


def test_a_conversation_at_exactly_the_minimum_turn_count_is_distilled(
    pg_engine, worker_settings
):
    settings = worker_settings()
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub, user_turns=memory_worker.MIN_USER_TURNS)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)

    memory_worker.run_once(
        pg_engine,
        settings,
        _role_client(settings, _ScriptedDistiller([_entries_json("at the boundary")])),
    )

    assert _outbox_row(pg_engine, cid).status == "done"
    assert len(_memory_versions(pg_engine, user_sub)) == 1


# ===========================================================================
# durability (doc 08's own Phase Gate)
# ===========================================================================


def test_a_crash_mid_cycle_leaves_the_row_pending_and_the_next_run_once_succeeds(
    pg_engine, worker_settings
):
    """The durability proof doc 08's Phase Gate names. ``KeyboardInterrupt``
    -- a ``BaseException``, not the ``Exception`` the worker's own per-row
    guard catches -- stands in for a genuine process kill mid-cycle: it
    escapes ``run_once`` entirely, so NOTHING the worker would normally do
    on the way out (record the attempt, finalize the turn) runs at all.
    The row must nonetheless be exactly as it was -- ``pending``, attempts
    untouched -- because this schema has no intermediate "processing"
    status by design (there is no half-claimed limbo state to be stuck in,
    and so no lease-timeout mechanism this phase has to build), and the
    very next cycle must pick it straight back up and succeed."""
    settings = worker_settings()
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5)
    before = _outbox_row(pg_engine, cid)

    with pytest.raises(KeyboardInterrupt):
        memory_worker.run_once(
            pg_engine,
            settings,
            _role_client(settings, _ScriptedDistiller([KeyboardInterrupt()])),
        )

    crashed = _outbox_row(pg_engine, cid)
    assert crashed.status == "pending"
    assert crashed.attempts == before.attempts == 0
    assert crashed.last_error is None
    assert crashed.last_turn_at == before.last_turn_at
    assert _memory_versions(pg_engine, user_sub) == []

    memory_worker.run_once(
        pg_engine,
        settings,
        _role_client(settings, _ScriptedDistiller([_entries_json("recovered cleanly")])),
    )

    assert _outbox_row(pg_engine, cid).status == "done"
    recovered = _memory_versions(pg_engine, user_sub)
    assert len(recovered) == 1
    assert [e["statement"] for e in recovered[0].entries] == ["recovered cleanly"]


def test_a_turn_landing_mid_distillation_re_arms_the_row_instead_of_being_marked_done(
    pg_engine, worker_settings
):
    """The claim is not a lease, so a user CAN send a new turn while their
    conversation is being distilled -- ``touch()`` re-arms the row
    (``last_turn_at = now()``, ``status = 'pending'``). Marking that row
    ``done`` afterwards would silently swallow the new turn's own
    distillation trigger until some later turn happened to re-arm it
    again. The status transitions therefore only apply to a row whose
    ``last_turn_at`` still matches the value that was claimed."""
    from poseidon.core.personalization.outbox import OutboxStore

    worker_settings()
    user_sub = _fresh_user_sub()
    cid = _create_conversation(pg_engine, user_sub)
    claimed_last_turn_at = _seed_outbox(
        pg_engine, user_sub, cid, minutes_ago=_IDLE_MINUTES + 5
    )
    outbox = OutboxStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    outbox.touch(cid)  # the user comes back mid-distillation
    marked = outbox.mark_done(cid, claimed_last_turn_at=claimed_last_turn_at)

    assert marked is False
    row = _outbox_row(pg_engine, cid)
    assert row.status == "pending"
    assert row.last_turn_at > claimed_last_turn_at


# ===========================================================================
# run_once: cycle bookkeeping
# ===========================================================================


def test_run_once_on_an_empty_queue_processes_nothing_and_does_not_raise(
    pg_engine, worker_settings
):
    settings = worker_settings(memory_idle_minutes=100000)
    distiller = _ScriptedDistiller([_entries_json("never reached")])

    processed = memory_worker.run_once(pg_engine, settings, _role_client(settings, distiller))

    assert processed == 0
    assert distiller.calls == []


def test_run_once_returns_the_number_of_rows_it_handled(pg_engine, worker_settings):
    settings = worker_settings()
    user_sub = _fresh_user_sub()
    first = _create_conversation(pg_engine, user_sub)
    second = _create_conversation(pg_engine, user_sub)
    _seed_outbox(pg_engine, user_sub, first, minutes_ago=_IDLE_MINUTES + 30)
    _seed_outbox(pg_engine, user_sub, second, minutes_ago=_IDLE_MINUTES + 25)

    processed = memory_worker.run_once(
        pg_engine,
        settings,
        _role_client(settings, _ScriptedDistiller([_entries_json("counted")])),
    )

    assert processed >= 2


# ===========================================================================
# the distillation prompt's own hard rule (Global Constraints)
# ===========================================================================


def test_the_distill_prompt_explicitly_forbids_tool_derived_entries():
    """Global Constraints: "text returned by ``research.web_research`` or
    any other external tool must never become an entry, verbatim or
    paraphrased". Not mechanically checkable at the store layer (see
    ``core/personalization/memory.py``'s own docstring) -- so the ONE place
    it is enforced, the prompt, has to actually say it, and a reviewer
    deleting that paragraph has to fail a test rather than merely be
    noticed."""
    prompt = (
        Path(memory_worker.__file__).resolve().parents[1]
        / "config"
        / "prompts"
        / "memory"
        / "distill.md"
    ).read_text(encoding="utf-8")
    lowered = prompt.lower()

    assert lowered.startswith("{# version:"), "doc 03 section 4's version-marker convention"
    assert "{{ transcript }}" in prompt
    assert "{{ existing_entries }}" in prompt
    assert "never" in lowered
    assert "tool" in lowered
    assert "research" in lowered
    for entry_type in ("preference", "scope", "fact", "correction"):
        assert entry_type in lowered, "the closed type set has to be stated to the model"
