"""RLS tests for migration 0005 (doc 05 sections 4 and 7): the four required
tests (doc 06 section 5, L1 category) now proven on ``turn_run``/``llm_calls``/
``tool_calls`` (migration 0003) -- the three tables migration 0004's own
docstring explicitly deferred ("out of scope for Phase 10 Task 1 ... left for
a later task rather than folded in here as a drive-by"). This is that later
task.

Where ``test_rls_policies.py`` (migration 0004) exercises ``rls_transaction``/
raw SQL directly, this module goes one level up: every row this suite creates
on the three run-log tables is written THROUGH :class:`~poseidon.core.runlog.
RunLogWriter` (never a bare INSERT), per this task's own brief -- proving the
writer's cutover to ``rls_transaction`` (Task 1's other deliverable) actually
enforces isolation, not merely that raw SQL under the wrapper would.

Also covers doc 05 section 7's two other Task 1 deliverables:

- The named-operator admin read role (``poseidon_admin``): catalog assertions
  (role shape, the three ``FOR SELECT ... USING (true)`` policies, exactly
  which tables carry one, its grants) plus one functional ``SET ROLE`` read
  proving an admin session really does see every user's rows.
- The redaction contract: ``DELETE /api/conversations/{id}`` hard-deletes
  conversation content and, in the SAME transaction, redacts the linked
  ``turn_run``/``tool_calls`` rows -- proven through the real HTTP route
  (mirrors ``test_history_cutover.py``'s own ``httpx``-driven pattern), with
  a real ``RunLogWriter``-built turn as the fixture, never hand-inserted rows.

**Round-0 correction reused, not re-derived.** Like ``test_rls_policies.py``,
this dev compose database's ``DATABASE_URL`` role (``poseidon``) is the
cluster's bootstrap SUPERUSER, which unconditionally bypasses row-level
security -- so every test below that needs the wrapper's real enforcement
calls ``rls_transaction``/constructs ``RunLogWriter`` with ``app_role=
_EFFECTIVE_APP_ROLE`` (``poseidon_app`` here, ``None`` against an
already-non-privileged DSN), exactly mirroring that module's own resolution.

**Cleanup discipline, split by table.** ``conversations``/``messages`` rows
this suite creates are cleaned up through identity-scoped deletes (``poseidon_
app`` still carries full DML there, migration 0004 unchanged) -- the same
pinned pattern ``test_rls_policies.py`` uses. ``turn_run``/``llm_calls``/
``tool_calls`` rows are deliberately NEVER deleted: migration 0005 grants
``poseidon_app`` no DELETE on any of the three (doc 05 section 7's "audit rows
are never deleted"), and ``test_runlog_writer.py``'s own pg suite already
established the precedent this task follows -- fresh ``uuid4``-derived
``user_sub`` values per test, leftover rows harmless and left as evidence the
write actually happened.
"""

import os
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.db import build_engine, rls_transaction
from poseidon.core.runlog import RunLogWriter

pytestmark = pytest.mark.pg

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0005)"

_APP_ROLE = "poseidon_app"
_ADMIN_ROLE = "poseidon_admin"
_RUN_LOG_TABLES = ("turn_run", "llm_calls", "tool_calls")

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        f"DATABASE_URL is not set - pg run-log RLS tests need a Postgres: {_UP_HINT}, "
        f"{_MIGRATE_HINT}",
        allow_module_level=True,
    )

# Computed once, from the same probe connection the skip guard below already
# opens -- mirrors test_rls_policies.py's own module-level probe exactly,
# narrowed to "does turn_run.redacted_at exist" (0005, not merely 0003) so
# this suite skips with an actionable message rather than failing every
# admin/redaction test with a bare UndefinedColumn.
_DSN_ROLE_IS_SUPERUSER = False

try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'turn_run' AND column_name = 'redacted_at'"
            )
            if _cur.fetchone() is None:
                pytest.skip(
                    f"turn_run.redacted_at does not exist - {_MIGRATE_HINT}",
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

_EFFECTIVE_APP_ROLE = _APP_ROLE if _DSN_ROLE_IS_SUPERUSER else None


# ---------------------------------------------------------------------------
# fixtures and small helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_engine():
    """A fresh ``Engine`` per test -- see ``test_rls_policies.py``'s own
    identical fixture for why this is function-scoped (required test 3 below
    needs to know its pool has handed out exactly one physical connection)."""
    engine = build_engine(_DSN)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _fresh_user_sub() -> str:
    return f"test|{uuid.uuid4().hex}"


def _dev_user(name: str) -> str:
    """A fresh, run-unique ``X-Dev-User`` value that still reads as ``name``
    -- mirrors ``test_history_cutover.py``'s own helper of the same name and
    the same rationale (re-running this suite against a long-lived dev
    Postgres must never collide with a previous run's rows)."""
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _headers(user: str) -> dict[str, str]:
    return {"X-Dev-User": user}


def _writer(engine: Engine) -> RunLogWriter:
    return RunLogWriter(engine, app_role=_EFFECTIVE_APP_ROLE)


def _full_turn(
    engine: Engine, user_sub: str, question: str, conversation_id: str | None = None
) -> str:
    """One ``turn_run`` row plus one ``llm_calls`` row plus one ``tool_calls``
    row plus a ``finalize`` -- all through the real :class:`RunLogWriter`
    (never raw SQL): the brief's own "through the writer" requirement for the
    four-test pattern below. Returns the new ``turn_run.id``."""
    writer = _writer(engine)
    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=conversation_id,
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question=question,
        mode="default",
        parsed={"probe": True},
    )
    assert handle is not None and handle.created is True, "writer.start_turn must succeed"
    writer.append_llm_call(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        seq=1,
        provider="stub",
        model_id="m",
        role="router",
        prompt_version="v1",
        prompt_hash="h",
        input_tokens=10,
        output_tokens=5,
        latency_ms=11,
        status="ok",
    )
    writer.append_tool_call(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        seq=1,
        tool="data_qa.metric_query",
        server=None,
        args={"metric": "GP"},
        result_digest={"rows": 1},
        status="ok",
        latency_ms=7,
    )
    writer.finalize(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        status="ok",
        message_id=str(uuid.uuid4()),
        answer_summary="done",
        input_tokens=10,
        output_tokens=5,
        latency_ms=25,
    )
    # str(...): against a real Postgres, `RETURNING id` hands back a genuine
    # uuid.UUID object for this native uuid column, not the plain str the
    # writer minted and inserted -- test_runlog_writer.py's own pg suite
    # documents this exact round-trip behavior (its "message_id::text"
    # comment). Normalized here, once, so every caller of this helper can
    # compare the returned id against a plain string unconditionally.
    return str(handle.turn_run_id)


def _settings(pg_database_url: str, **overrides):
    from poseidon.core.config import Settings

    defaults: dict = dict(
        _env_file=None,
        database_url=pg_database_url,
        s3_bucket="poseidon-artifacts",
        llm_mode="stub",
        llm_profile="bedrock",
        chat_mode="live",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _app(pg_database_url: str, **overrides):
    """A fresh app instance (fresh ``FastAPI``, fresh ``Engine``) -- mirrors
    ``test_history_cutover.py``'s own helper of the same name."""
    from poseidon.api.app import create_app

    return create_app(_settings(pg_database_url, **overrides))


def _delete_conversations_by_dev_user(engine: Engine, dev_user_sub: str) -> None:
    """Teardown for conversations/messages rows this suite's httpx-driven
    tests create -- migration 0004's own grants are unchanged (``poseidon_
    app`` still carries DELETE there), so this mirrors ``test_rls_policies.
    py``'s own identity-scoped cleanup. Never used for turn_run/llm_calls/
    tool_calls -- see the module docstring's "Cleanup discipline"."""
    with rls_transaction(engine, dev_user_sub, app_role=_EFFECTIVE_APP_ROLE) as conn:
        conn.execute(text("DELETE FROM conversations WHERE user_sub = :u"), {"u": dev_user_sub})


# ---------------------------------------------------------------------------
# the four required tests (doc 05 section 4 / doc 06 section 5, L1 category),
# through the writer, across all three run-log tables at once
# ---------------------------------------------------------------------------


def test_two_user_isolation_through_the_writer_on_all_three_tables(pg_engine):
    """Required test 1. Two users each get a full turn (turn_run + llm_calls
    + tool_calls) through the writer; a bare, unfiltered SELECT (no WHERE
    clause) under each user's context sees only that user's own rows, on
    every one of the three tables."""
    user_a, user_b = _fresh_user_sub(), _fresh_user_sub()
    turn_a = _full_turn(pg_engine, user_a, "a's question")
    turn_b = _full_turn(pg_engine, user_b, "b's question")

    def _visible(user_sub: str) -> tuple[set[str], set[str], set[str]]:
        with rls_transaction(pg_engine, user_sub, app_role=_EFFECTIVE_APP_ROLE) as conn:
            turn_ids = {str(r[0]) for r in conn.execute(text("SELECT id FROM turn_run"))}
            llm_turn_ids = {
                str(r[0]) for r in conn.execute(text("SELECT turn_run_id FROM llm_calls"))
            }
            tool_turn_ids = {
                str(r[0]) for r in conn.execute(text("SELECT turn_run_id FROM tool_calls"))
            }
        return turn_ids, llm_turn_ids, tool_turn_ids

    turn_ids_a, llm_ids_a, tool_ids_a = _visible(user_a)
    turn_ids_b, llm_ids_b, tool_ids_b = _visible(user_b)

    assert turn_ids_a == {turn_a}
    assert llm_ids_a == {turn_a}
    assert tool_ids_a == {turn_a}
    assert turn_ids_b == {turn_b}
    assert llm_ids_b == {turn_b}
    assert tool_ids_b == {turn_b}


def test_no_context_connection_sees_zero_rows_on_all_three_run_log_tables(pg_engine):
    """Required test 2. A transaction that never had ``app.user_sub`` set AT
    ALL sees zero rows on every one of the three tables -- see
    ``test_rls_policies.py``'s own identical test for why the probe uses a
    brand-new engine rather than ``pg_engine`` (already touched by
    ``_full_turn`` above via ``rls_transaction``, no longer virgin)."""
    user = _fresh_user_sub()
    fresh_engine = build_engine(_DSN)
    try:
        _full_turn(pg_engine, user, "hidden-from-no-context")

        with fresh_engine.begin() as conn:
            if _DSN_ROLE_IS_SUPERUSER:
                conn.execute(text(f'SET LOCAL ROLE "{_APP_ROLE}"'))
            identity = conn.execute(
                text("SELECT current_setting('app.user_sub', true)")
            ).scalar_one()
            turn_count = conn.execute(text("SELECT COUNT(*) FROM turn_run")).scalar_one()
            llm_count = conn.execute(text("SELECT COUNT(*) FROM llm_calls")).scalar_one()
            tool_count = conn.execute(text("SELECT COUNT(*) FROM tool_calls")).scalar_one()

        assert identity is None, (
            "this checkout must be genuinely virgin (app.user_sub never set) to "
            "prove the missing_ok/NULL mechanism specifically, not merely reset"
        )
        assert turn_count == 0
        assert llm_count == 0
        assert tool_count == 0
    finally:
        fresh_engine.dispose()


def test_pooled_connection_does_not_leak_identity_across_writer_checkouts(pg_engine):
    """Required test 3 -- decision D28's own reason for existing, proven
    through TWO real writer calls (never a raw ``rls_transaction`` call for
    the data-creating checkouts) bracketing a manual, read-only peek at
    whatever the first checkout left behind. See ``test_rls_policies.py``'s
    own identical test for why the peek must be manual (a real
    ``rls_transaction``/writer call always re-sets identity as its own first
    statement, which would overwrite whatever it is trying to observe).

    Connection-pool reuse is proven via a SQLAlchemy ``"connect"`` event
    (fires once per NEW physical DBAPI connection, never per logical
    checkout) rather than comparing raw connection identity directly: the
    writer owns and closes its own connection internally, so this test has
    no handle of its own to compare across the three checkouts the way
    ``test_rls_policies.py``'s lower-level version can.
    """
    connect_count = 0

    @event.listens_for(pg_engine, "connect")
    def _count_new_physical_connections(dbapi_connection, connection_record) -> None:
        nonlocal connect_count
        connect_count += 1

    user_a, user_b = _fresh_user_sub(), _fresh_user_sub()
    writer = _writer(pg_engine)

    handle_a = writer.start_turn(
        user_sub=user_a,
        conversation_id=None,
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question="a-leak-check",
        mode="default",
        parsed={},
    )
    assert handle_a is not None

    with pg_engine.begin() as conn:
        leftover_identity = conn.execute(
            text("SELECT current_setting('app.user_sub', true)")
        ).scalar_one()
        leftover_role = conn.execute(text("SELECT current_user")).scalar_one()

    handle_b = writer.start_turn(
        user_sub=user_b,
        conversation_id=None,
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question="b-leak-check",
        mode="default",
        parsed={},
    )
    assert handle_b is not None
    with rls_transaction(pg_engine, user_b, app_role=_EFFECTIVE_APP_ROLE) as conn:
        questions_seen_by_b = {
            row[0] for row in conn.execute(text("SELECT question FROM turn_run"))
        }

    assert connect_count == 1, (
        "this test only proves what it claims if the pool actually reused "
        "the same physical connection across every checkout"
    )
    assert leftover_identity in (None, ""), (
        "app.user_sub leaked across a pooled-connection checkout -- "
        "set_config's is_local argument has regressed to session-scoped"
    )
    if _DSN_ROLE_IS_SUPERUSER:
        assert leftover_role != _APP_ROLE, (
            "SET LOCAL ROLE leaked across a pooled-connection checkout -- "
            "the role switch has regressed to session-scoped"
        )
    assert questions_seen_by_b == {"b-leak-check"}


def test_owner_connection_is_still_filtered_by_force_rls_on_run_log_tables(pg_engine):
    """Required test 4, on ``turn_run`` -- proving FORCE's contribution once
    is sufficient (the same division of labor ``test_rls_policies.py`` uses:
    this test proves the MECHANISM, the catalog test below proves it applies
    to all three tables uniformly). See that module's own identical test for
    the full rationale of the superuser-only ownership-swap branch."""
    with pg_engine.connect() as conn:
        owner_is_current_user = conn.execute(
            text(
                "SELECT pg_get_userbyid(relowner) = current_user FROM pg_class "
                "WHERE relname = 'turn_run'"
            )
        ).scalar_one()
    assert owner_is_current_user is True, (
        "this test only proves FORCE matters if DATABASE_URL's role really owns the table"
    )

    other_user = _fresh_user_sub()
    caller_context = _fresh_user_sub()
    _full_turn(pg_engine, other_user, "owner-must-not-see-this")

    if _DSN_ROLE_IS_SUPERUSER:
        with pg_engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE turn_run OWNER TO poseidon_app"))
                conn.execute(text(f'SET LOCAL ROLE "{_APP_ROLE}"'))
                conn.execute(
                    text("SELECT set_config('app.user_sub', :sub, true)"),
                    {"sub": caller_context},
                )
                identity_ok = conn.execute(
                    text(
                        "SELECT current_user = 'poseidon_app', "
                        "NOT (rolsuper OR rolbypassrls) "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                ).one()
                rows = conn.execute(text("SELECT question FROM turn_run")).all()
            finally:
                conn.rollback()  # never durably re-owns the table
        assert tuple(identity_ok) == (True, True)
    else:
        with rls_transaction(pg_engine, caller_context, app_role=_EFFECTIVE_APP_ROLE) as conn:
            rows = conn.execute(text("SELECT question FROM turn_run")).all()

    assert rows == []


# ---------------------------------------------------------------------------
# catalog assertions -- migration 0005 built what the tests above rely on
# ---------------------------------------------------------------------------


def test_row_level_security_is_enabled_and_forced_on_all_three_run_log_tables(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname IN ('turn_run', 'llm_calls', 'tool_calls')"
            )
        ).all()
    by_name = {row.relname: row for row in rows}

    assert set(by_name) == set(_RUN_LOG_TABLES)
    for table_name, row in by_name.items():
        assert row.relrowsecurity is True, table_name
        assert row.relforcerowsecurity is True, table_name


@pytest.mark.parametrize(
    ("table_name", "policy_name"),
    [
        ("turn_run", "turn_run_owner"),
        ("llm_calls", "llm_calls_owner"),
        ("tool_calls", "tool_calls_owner"),
    ],
)
def test_owner_policy_exists_with_the_pinned_predicate(pg_engine, table_name, policy_name):
    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT cmd, qual, with_check FROM pg_policies "
                "WHERE tablename = :table_name AND policyname = :policy_name"
            ),
            {"table_name": table_name, "policy_name": policy_name},
        ).first()

    assert row is not None, f"policy {policy_name} must exist on {table_name}"
    assert row.cmd == "ALL"
    assert "current_setting" in row.qual and "app.user_sub" in row.qual and "user_sub" in row.qual
    assert (
        "current_setting" in row.with_check
        and "app.user_sub" in row.with_check
        and "user_sub" in row.with_check
    )


@pytest.mark.parametrize("table_name", list(_RUN_LOG_TABLES))
def test_poseidon_app_has_select_insert_update_but_not_delete(pg_engine, table_name):
    """Doc 05 section 7: "audit rows are never deleted" -- migration 0005
    grants the runtime role three of the four DML verbs, deliberately never
    DELETE, on each of the three run-log tables."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = :role AND table_name = :table_name"
            ),
            {"role": _APP_ROLE, "table_name": table_name},
        ).all()
    privileges = {row.privilege_type for row in rows}

    assert privileges == {"SELECT", "INSERT", "UPDATE"}


def test_poseidon_admin_role_exists_without_bypassrls_or_login(pg_engine):
    """``poseidon_app``'s own equivalent assertion already lives in
    ``test_rls_policies.py``; this is the new role this task adds. NOLOGIN
    (``rolcanlogin is False``): doc 05 section 7's admin role is granted to
    named operators' own login roles, never authenticated directly."""
    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolbypassrls, rolsuper, rolcanlogin FROM pg_roles WHERE rolname = :r"
            ),
            {"r": _ADMIN_ROLE},
        ).first()

    assert row is not None, "poseidon_admin role must exist (migration 0005)"
    assert row.rolbypassrls is False
    assert row.rolsuper is False
    assert row.rolcanlogin is False


@pytest.mark.parametrize("table_name", list(_RUN_LOG_TABLES))
def test_admin_select_only_policy_exists_with_using_true(pg_engine, table_name):
    policy_name = f"{table_name}_admin_read"
    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT cmd, roles, qual FROM pg_policies "
                "WHERE tablename = :table_name AND policyname = :policy_name"
            ),
            {"table_name": table_name, "policy_name": policy_name},
        ).first()

    assert row is not None, f"{policy_name} must exist on {table_name}"
    assert row.cmd == "SELECT"
    assert list(row.roles) == [_ADMIN_ROLE]
    assert row.qual.strip() == "true"


def test_admin_read_policies_exist_on_exactly_the_run_log_and_feedback_tables(pg_engine):
    """"Exactly these tables": ``conversations``/``messages`` must carry NO
    admin policy at all (doc 05 section 7: "Admins have no path to another
    user's messages... those tables are RLS-scoped with no admin policy,
    deliberately") -- the one invariant this test exists to prove, and the
    one migration 0005 itself could still fully name as "the three run-log
    tables". Phase 12 Task 1 (doc 06 section 7 / D25) deliberately adds a
    FOURTH: ``message_feedback`` gets its own admin-read policy too (the
    harvest exporter and verdict roll-up need to read every user's feedback,
    the identical reason the run-log tables already have one) -- amended
    here rather than left failing, since the invariant this test actually
    protects (conversations/messages stay admin-policy-free) still holds;
    only the ENUMERATION of which OTHER tables legitimately carry one grew."""
    with pg_engine.connect() as conn:
        rows = conn.execute(text("SELECT tablename, roles FROM pg_policies")).all()
    tables_with_admin_policy = {row.tablename for row in rows if _ADMIN_ROLE in row.roles}

    assert tables_with_admin_policy == set(_RUN_LOG_TABLES) | {"message_feedback"}


@pytest.mark.parametrize("table_name", list(_RUN_LOG_TABLES))
def test_poseidon_admin_has_exactly_select_privilege(pg_engine, table_name):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = :role AND table_name = :table_name"
            ),
            {"role": _ADMIN_ROLE, "table_name": table_name},
        ).all()
    privileges = {row.privilege_type for row in rows}

    assert privileges == {"SELECT"}


# ---------------------------------------------------------------------------
# functional admin proof: a real SET ROLE session reads across users
# ---------------------------------------------------------------------------


def test_admin_role_can_read_across_users_via_set_role(pg_engine):
    """Functional proof, not just catalog shape: grant this test's OWN
    connecting role membership in ``poseidon_admin`` (mirrors how a real
    deploy grants a named operator's own login role membership), ``SET
    ROLE`` to it, and confirm a bare unfiltered SELECT sees BOTH users' rows
    across all three tables -- doc 05 section 7's whole reason this role
    exists ("the role that runs harvest, cost roll-ups, and incident
    review"). Membership is granted and revoked IN THIS TEST, not assumed
    to pre-exist, and ``RESET ROLE``/``REVOKE`` both run in a ``finally``.
    """
    user_a, user_b = _fresh_user_sub(), _fresh_user_sub()
    turn_a = _full_turn(pg_engine, user_a, "admin-should-see-a")
    turn_b = _full_turn(pg_engine, user_b, "admin-should-see-b")

    with pg_engine.connect() as conn:
        connecting_role = conn.execute(text("SELECT current_user")).scalar_one()

    granted = False
    try:
        with pg_engine.begin() as conn:
            conn.execute(text(f'GRANT {_ADMIN_ROLE} TO "{connecting_role}"'))
        granted = True

        with pg_engine.begin() as conn:
            conn.execute(text(f'SET ROLE "{_ADMIN_ROLE}"'))
            turn_ids = {str(r[0]) for r in conn.execute(text("SELECT id FROM turn_run"))}
            llm_provider_by_turn = {
                str(r[0]): r[1]
                for r in conn.execute(text("SELECT turn_run_id, provider FROM llm_calls"))
            }
            conn.execute(text("RESET ROLE"))

        assert {turn_a, turn_b} <= turn_ids
        assert llm_provider_by_turn.get(turn_a) == "stub"
        assert llm_provider_by_turn.get(turn_b) == "stub"
    finally:
        if granted:
            with pg_engine.begin() as conn:
                conn.execute(text(f'REVOKE {_ADMIN_ROLE} FROM "{connecting_role}"'))


# ---------------------------------------------------------------------------
# redact_turns_for_conversation -- direct unit proof of the -> int contract
# ---------------------------------------------------------------------------


def test_redact_turns_for_conversation_returns_count_and_clears_payload_columns(pg_engine):
    from poseidon.core.runlog import redact_turns_for_conversation

    user = _fresh_user_sub()
    conversation_id = str(uuid.uuid4())
    turn_a = _full_turn(pg_engine, user, "q1", conversation_id=conversation_id)
    turn_b = _full_turn(pg_engine, user, "q2", conversation_id=conversation_id)

    with rls_transaction(pg_engine, user, app_role=_EFFECTIVE_APP_ROLE) as conn:
        count = redact_turns_for_conversation(conn, conversation_id)

    assert count == 2
    with pg_engine.connect() as conn:
        turn_rows = conn.execute(
            text(
                "SELECT id, question, answer_summary, parsed, redacted_at, status, "
                "input_tokens, output_tokens, latency_ms "
                "FROM turn_run WHERE conversation_id = :cid"
            ),
            {"cid": conversation_id},
        ).all()
        tool_args = conn.execute(
            text(
                "SELECT args, result_digest FROM tool_calls WHERE turn_run_id IN (:a, :b)"
            ),
            {"a": turn_a, "b": turn_b},
        ).all()
        llm_rows = conn.execute(
            text("SELECT provider, input_tokens FROM llm_calls WHERE turn_run_id IN (:a, :b)"),
            {"a": turn_a, "b": turn_b},
        ).all()

    assert len(turn_rows) == 2
    for row in turn_rows:
        assert row.question is None
        assert row.answer_summary is None
        assert row.parsed == {}
        assert row.redacted_at is not None
        # survives
        assert row.status == "ok"
        assert row.input_tokens == 10
        assert row.output_tokens == 5
        assert row.latency_ms == 25

    assert len(tool_args) == 2
    assert all(row.args is None for row in tool_args)
    # I-2 (P11 final-review wave): result_digest carries content-bearing
    # proof text (entity/period/filter values) verbatim -- doc 05 section
    # 7's own governing sentence ("loses its content") requires it null
    # alongside args, not merely a column doc 05's enumerated list omitted.
    assert all(row.result_digest is None for row in tool_args)

    # llm_calls carries no payload columns -- fully untouched.
    assert len(llm_rows) == 2
    assert all(row.provider == "stub" and row.input_tokens == 10 for row in llm_rows)


# ---------------------------------------------------------------------------
# DELETE /api/conversations/{cid} -- redaction, isolation, transactionality,
# through the real HTTP route
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_conversation_redacts_turn_run_and_tool_calls_leaves_llm_calls_untouched(
    pg_engine,
):
    alice = _dev_user("alice")
    headers = _headers(alice)
    app = _app(_DSN)
    transport = httpx.ASGITransport(app=app)
    user_sub = f"dev|{alice}"
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]

        # A real turn tied to this conversation, entirely through the writer
        # -- the exact methods this task cuts over to rls_transaction.
        writer = RunLogWriter(app.state.db_engine, app_role=app.state.settings.database_app_role)
        handle = writer.start_turn(
            user_sub=user_sub,
            conversation_id=cid,
            client_turn_key=str(uuid.uuid4()),
            turn_index=1,
            question="what was my April GP",
            mode="default",
            parsed={"customer": "Acme"},
        )
        assert handle is not None and handle.created is True
        writer.append_llm_call(
            turn_run_id=handle.turn_run_id,
            user_sub=user_sub,
            seq=1,
            provider="stub",
            model_id="m",
            role="router",
            prompt_version="v1",
            prompt_hash="h",
            input_tokens=10,
            output_tokens=5,
            latency_ms=42,
            status="ok",
        )
        writer.append_tool_call(
            turn_run_id=handle.turn_run_id,
            user_sub=user_sub,
            seq=1,
            tool="data_qa.metric_query",
            server=None,
            args={"metric": "GP"},
            result_digest={"rows": 3},
            status="ok",
            latency_ms=17,
        )
        writer.finalize(
            turn_run_id=handle.turn_run_id,
            user_sub=user_sub,
            status="ok",
            message_id=str(uuid.uuid4()),
            answer_summary="GP was $412K in April.",
            input_tokens=10,
            output_tokens=5,
            latency_ms=90,
        )

        response = await client.delete(f"/api/conversations/{cid}", headers=headers)

    assert response.status_code == 204

    with pg_engine.connect() as conn:
        turn_row = conn.execute(
            text(
                "SELECT question, answer_summary, parsed, status, input_tokens, "
                "output_tokens, latency_ms, redacted_at, trace_id, kind "
                "FROM turn_run WHERE id = :id"
            ),
            {"id": handle.turn_run_id},
        ).first()
        tool_row = conn.execute(
            text(
                "SELECT args, result_digest, status, latency_ms, tool "
                "FROM tool_calls WHERE turn_run_id = :id"
            ),
            {"id": handle.turn_run_id},
        ).first()
        llm_row = conn.execute(
            text(
                "SELECT provider, model_id, input_tokens, output_tokens, status "
                "FROM llm_calls WHERE turn_run_id = :id"
            ),
            {"id": handle.turn_run_id},
        ).first()
        conversation_row = conn.execute(
            text("SELECT 1 FROM conversations WHERE id = :id"), {"id": cid}
        ).first()

    assert turn_row is not None
    assert turn_row.question is None
    assert turn_row.answer_summary is None
    assert turn_row.parsed == {}
    assert turn_row.redacted_at is not None
    # survives: ids/timestamps (proven by the row still existing at all),
    # model/provider (kind), token counts, latency, status.
    assert turn_row.status == "ok"
    assert turn_row.input_tokens == 10
    assert turn_row.output_tokens == 5
    assert turn_row.latency_ms == 90
    assert turn_row.kind == "chat_turn"

    assert tool_row is not None
    assert tool_row.args is None
    # I-2 (P11 final-review wave): result_digest survives redaction today,
    # carrying the deleted conversation's own subject matter (customer/port
    # names, period window) verbatim -- see core/runlog.py's own
    # _REDACT_TOOL_CALLS_SQL docstring citation for the fuller rationale.
    assert tool_row.result_digest is None
    assert tool_row.status == "ok"
    assert tool_row.latency_ms == 17
    assert tool_row.tool == "data_qa.metric_query"

    # llm_calls carries no payload columns -- fully untouched.
    assert llm_row == ("stub", "m", 10, 5, "ok")

    assert conversation_row is None  # hard-deleted


@pytest.mark.anyio
async def test_delete_conversation_two_user_isolation_bob_cannot_delete_alices(pg_engine):
    alice = _dev_user("alice")
    bob = _dev_user("bob")
    app = _app(_DSN)
    transport = httpx.ASGITransport(app=app)
    try:
        transport_client = httpx.AsyncClient(transport=transport, base_url="http://t")
        async with transport_client as client:
            cid = (await client.post("/api/conversations", headers=_headers(alice))).json()[
                "conversation"
            ]["id"]

            response = await client.delete(f"/api/conversations/{cid}", headers=_headers(bob))

            # sanity: alice herself still sees it -- proves bob's 404 above is
            # the RLS-visibility gate, not a bug that blocks everyone.
            alice_messages = await client.get(
                f"/api/conversations/{cid}/messages", headers=_headers(alice)
            )

        assert response.status_code == 404
        assert alice_messages.status_code == 200

        with pg_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM conversations WHERE id = :id"), {"id": cid}
            ).first()
        assert exists is not None, "bob's failed delete must not have removed alice's conversation"
    finally:
        _delete_conversations_by_dev_user(pg_engine, f"dev|{alice}")


@pytest.mark.anyio
async def test_delete_conversation_transactionality_failing_redaction_rolls_back_the_delete(
    pg_engine,
):
    """Doc 05 section 7's redaction contract must be all-or-nothing. Forces
    the REDACTION half to fail via a real (non-``pg_temp``) Postgres
    trigger: a durable, cluster-visible object fires regardless of which
    pooled connection the app's own ``Engine`` hands out for this request,
    unlike a session-scoped ``pg_temp`` function this test cannot pin to the
    right connection from the outside. Scoped narrowly to one sentinel
    ``trace_id`` this test alone mints, so it cannot affect any other row or
    any concurrently-running test.

    The turn deliberately never calls ``finalize`` -- that would itself be
    an UPDATE hitting the trigger during SETUP, before the DELETE route's
    own transaction ever begins. Leaving the row at ``status='running'`` is
    fine: this test only cares about the delete/redaction rollback, not
    about full-field survival (the previous test already covers that).
    """
    alice = _dev_user("alice")
    sentinel_trace_id = f"force-redaction-failure-{uuid.uuid4().hex}"
    suffix = uuid.uuid4().hex[:8]
    trigger_name = f"trg_test_force_redaction_failure_{suffix}"
    function_name = f"_test_force_redaction_failure_{suffix}"

    with pg_engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE OR REPLACE FUNCTION {function_name}() RETURNS trigger AS $$ "
                f"BEGIN IF NEW.trace_id = '{sentinel_trace_id}' THEN "
                "RAISE EXCEPTION 'forced redaction failure for test'; END IF; "
                "RETURN NEW; END; $$ LANGUAGE plpgsql"
            )
        )
        conn.execute(
            text(
                f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON turn_run "
                f"FOR EACH ROW EXECUTE FUNCTION {function_name}()"
            )
        )

    try:
        app = _app(_DSN)
        transport = httpx.ASGITransport(app=app)
        user_sub = f"dev|{alice}"
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            cid = (await client.post("/api/conversations", headers=_headers(alice))).json()[
                "conversation"
            ]["id"]

            writer = RunLogWriter(
                app.state.db_engine, app_role=app.state.settings.database_app_role
            )
            handle = writer.start_turn(
                user_sub=user_sub,
                conversation_id=cid,
                client_turn_key=str(uuid.uuid4()),
                turn_index=1,
                question="q",
                mode="default",
                parsed={},
                trace_id=sentinel_trace_id,
            )
            assert handle is not None and handle.created is True

            # DBAPIError: SQLAlchemy's own wrapper for any underlying DBAPI
            # (psycopg) error -- specific enough to prove this is really the
            # forced trigger failure propagating, not an unrelated bug
            # somewhere else in the request that happens to also raise.
            with pytest.raises(DBAPIError):
                await client.delete(f"/api/conversations/{cid}", headers=_headers(alice))

        with pg_engine.connect() as conn:
            conversation_exists = conn.execute(
                text("SELECT 1 FROM conversations WHERE id = :id"), {"id": cid}
            ).first()
            turn_row = conn.execute(
                text("SELECT question, redacted_at FROM turn_run WHERE id = :id"),
                {"id": handle.turn_run_id},
            ).first()

        assert conversation_exists is not None, "the DELETE must have rolled back"
        assert turn_row is not None
        assert turn_row.question == "q"
        assert turn_row.redacted_at is None
    finally:
        with pg_engine.begin() as conn:
            conn.execute(text(f"DROP TRIGGER IF EXISTS {trigger_name} ON turn_run"))
            conn.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))
        _delete_conversations_by_dev_user(pg_engine, f"dev|{alice}")


def test_runlog_rls_module_is_ascii_on_disk():
    """Matches the codebase-wide ASCII-on-disk convention (e.g.
    ``test_rls_policies_module_is_ascii_on_disk``)."""
    offending = sorted({byte for byte in Path(__file__).read_bytes() if byte > 0x7F})
    assert not offending, f"{Path(__file__).name} holds non-ASCII bytes: {offending}"
