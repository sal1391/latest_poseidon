"""RLS tests for migration 0004 (doc 05 section 4, decision D28): the four
required tests (doc 06 section 5, L1 category) proving row-level security
really isolates ``conversations``/``messages`` by user -- not by "remembering
to add WHERE clauses" -- plus catalog assertions that migration 0004 built
the objects those tests rely on with the exact shape this module pins.

All ``@pytest.mark.pg`` (module-level, like ``test_synthetic_client_pg.py``:
this whole file is pg-only, unlike ``test_runlog_writer.py``'s mixed
offline+pg file), skipped with an actionable reason when ``DATABASE_URL`` is
unset, unreachable within 2 seconds, or reachable but not yet migrated to
0004 -- the same three-stage guard ``test_synthetic_client_pg.py`` uses,
narrowed to "does ``conversations`` exist".

**This task tests the ``rls_transaction``/SQL layer directly**, one level
below any ``UserContext``/store abstraction (Task 2's concern) -- exactly as
the task brief scopes it.

**Round-0 correction -- these tests now exercise the WRAPPER's real
enforcement, not a per-test workaround.** The first version of this module
discovered that this dev compose database's ``DATABASE_URL`` role
(``poseidon``) is the cluster's bootstrap SUPERUSER (the official Postgres
Docker image's own ``POSTGRES_USER`` convention), and Postgres superusers
unconditionally bypass row-level security -- a hard invariant no policy or
``FORCE`` clause can override. That version worked around it entirely
inside this test file (a bespoke ``SET LOCAL ROLE`` wrapper used by every
test). The plan amendment this correction implements moves the fix into
``poseidon.core.db.rls_transaction`` itself, via a new ``app_role``
parameter (``Settings.database_app_role``, default ``"poseidon_app"``) --
because doc 05's store (Task 2) is deliberately WHERE-clause-free, so its
own isolation tests could never pass while only the TEST SUITE, and not the
runtime connection, carried the fix. Every test below that needs a
protected identity now calls ``rls_transaction(engine, user_sub,
app_role=_EFFECTIVE_APP_ROLE)`` directly -- proving what the real
application will actually do -- with exactly two narrow, disclosed
exceptions (required test 2 and required test 4), each documented in place
for a structural reason the wrapper's own contract makes unavoidable, never
to paper over an unfixed wrapper. ``test_superuser_bypasses_rls_without_
wrapper`` below is new: documentation-of-record that a bare connection,
with no wrapper involved at all, really does leak on this database --
proof the wrapper is load-bearing, not a defensive nicety.

**Cleanup pattern (pinned by the task brief):** every test uses fresh,
unique ``user_sub`` values (``f"test|{uuid4().hex}"``) so re-running this
suite against a long-lived dev Postgres never collides with a previous
run's rows, and every teardown deletes through identity-scoped helpers
**under the owning sub** -- never a bare admin ``DELETE`` -- so a passing
teardown is itself quiet proof that a legitimate same-user delete satisfies
the owner policy's ``USING`` clause. ``ON DELETE CASCADE`` (migration 0004)
takes a deleted conversation's messages with it, so one delete cleans up
both tables.

**Why the ``pg_engine`` fixture is function-scoped, not module state:**
required test 3 (pooled-connection context leak) needs to know its own
engine's connection pool has never handed out more than one physical
connection before the moment it compares identity across checkouts --
sharing one engine across every test in this module would make that
guarantee depend on execution order and what earlier tests happened to do
to the pool. Every other test here could safely share one engine (a
Postgres GUC set with ``is_local=true`` resets the instant its own
transaction ends, regardless of which physical connection carried it) but
all get their own, for uniformity and so no test's correctness depends on
another test having run first.
"""

import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.db import build_engine, rls_transaction
from poseidon.core.util.uuid7 import uuid7

pytestmark = pytest.mark.pg

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0004)"

# migration 0004's own non-owner, non-BYPASSRLS role -- the same default
# Settings.database_app_role resolves to (poseidon.core.config).
_APP_ROLE = "poseidon_app"

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        f"DATABASE_URL is not set - pg RLS tests need a Postgres: {_UP_HINT}, {_MIGRATE_HINT}",
        allow_module_level=True,
    )

# Computed once, here, from the same probe connection the skip guard below
# already opens -- see the module docstring's "round-0 correction" section
# for what this drives.
_DSN_ROLE_IS_SUPERUSER = False

try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute("SELECT to_regclass('public.conversations')")
            if _cur.fetchone()[0] is None:
                pytest.skip(
                    f"conversations does not exist - {_MIGRATE_HINT}", allow_module_level=True
                )
            _cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            _DSN_ROLE_IS_SUPERUSER = _cur.fetchone()[0]
except psycopg.Error as exc:
    pytest.skip(
        f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}",
        allow_module_level=True,
    )

# The app_role value this test suite passes to rls_transaction in THIS
# environment: poseidon_app when the DSN role needs the round-0 correction
# (this compose database), None otherwise -- exactly mirroring what a real
# deploy's Settings.database_app_role would resolve to in each case (see
# that field's own docstring in poseidon.core.config).
_EFFECTIVE_APP_ROLE = _APP_ROLE if _DSN_ROLE_IS_SUPERUSER else None


# ---------------------------------------------------------------------------
# fixtures and small helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_engine():
    """A fresh ``Engine`` per test -- see the module docstring for why this
    is function-scoped rather than shared."""
    engine = build_engine(_DSN)
    try:
        yield engine
    finally:
        engine.dispose()


def _fresh_user_sub() -> str:
    """A ``user_sub`` unique to this test invocation (task brief's pinned
    pattern) -- so re-running this suite against a long-lived dev Postgres
    never collides with a previous run's rows."""
    return f"test|{uuid4().hex}"


_INSERT_CONVERSATION_SQL = text(
    "INSERT INTO conversations (id, user_sub, title) VALUES (:id, :user_sub, :title)"
)
_INSERT_MESSAGE_SQL = text(
    "INSERT INTO messages (id, conversation_id, user_sub, role, parts) "
    "VALUES (:id, :conversation_id, :user_sub, :role, :parts)"
)
_SELECT_CONVERSATION_TITLES_SQL = text("SELECT title FROM conversations")
_COUNT_CONVERSATIONS_SQL = text("SELECT COUNT(*) FROM conversations")
_COUNT_MESSAGES_SQL = text("SELECT COUNT(*) FROM messages")
_DELETE_CONVERSATIONS_BY_USER_SQL = text("DELETE FROM conversations WHERE user_sub = :user_sub")


def _insert_conversation(engine: Engine, user_sub: str, title: str = "chat") -> str:
    """Insert one conversation as its own owner, through the real
    ``rls_transaction`` (``app_role`` included, exactly as the application
    calls it) -- never a bare admin insert -- so every fixture row this
    suite creates is itself quiet proof that a legitimate same-user INSERT
    satisfies the owner policy's ``WITH CHECK``. Returns the new row's id.
    """
    new_id = str(uuid7())
    with rls_transaction(engine, user_sub, app_role=_EFFECTIVE_APP_ROLE) as conn:
        conn.execute(_INSERT_CONVERSATION_SQL, {"id": new_id, "user_sub": user_sub, "title": title})
    return new_id


def _insert_message(engine: Engine, conversation_id: str, user_sub: str, role: str = "user") -> str:
    """Insert one message on ``conversation_id``, through ``rls_transaction``
    under ``user_sub`` -- same reasoning as :func:`_insert_conversation`."""
    new_id = str(uuid7())
    with rls_transaction(engine, user_sub, app_role=_EFFECTIVE_APP_ROLE) as conn:
        conn.execute(
            _INSERT_MESSAGE_SQL,
            {
                "id": new_id,
                "conversation_id": conversation_id,
                "user_sub": user_sub,
                "role": role,
                "parts": json.dumps([{"type": "text", "text": "hello"}]),
            },
        )
    return new_id


def _delete_conversations(engine: Engine, user_sub: str) -> None:
    """Teardown: delete through ``rls_transaction`` under the OWNING sub
    (task brief's pinned cleanup pattern, never an admin bypass). ``ON
    DELETE CASCADE`` (migration 0004) takes this user's messages with it.
    """
    with rls_transaction(engine, user_sub, app_role=_EFFECTIVE_APP_ROLE) as conn:
        conn.execute(_DELETE_CONVERSATIONS_BY_USER_SQL, {"user_sub": user_sub})


# ---------------------------------------------------------------------------
# the four required tests (doc 05 section 4 / doc 06 section 5, L1 category)
# ---------------------------------------------------------------------------


def test_two_user_isolation(pg_engine):
    """Required test 1. Two users each write a conversation; a bare,
    unfiltered ``SELECT`` (no ``WHERE`` clause -- the module docstring's "not
    by remembering to add WHERE clauses") under each user's context --
    established through the real ``rls_transaction``, ``app_role`` included
    -- sees only that user's own row."""
    user_a, user_b = _fresh_user_sub(), _fresh_user_sub()
    try:
        _insert_conversation(pg_engine, user_a, title="a-chat")
        _insert_conversation(pg_engine, user_b, title="b-chat")

        with rls_transaction(pg_engine, user_a, app_role=_EFFECTIVE_APP_ROLE) as conn:
            titles_a = {row[0] for row in conn.execute(_SELECT_CONVERSATION_TITLES_SQL)}
        with rls_transaction(pg_engine, user_b, app_role=_EFFECTIVE_APP_ROLE) as conn:
            titles_b = {row[0] for row in conn.execute(_SELECT_CONVERSATION_TITLES_SQL)}

        assert titles_a == {"a-chat"}
        assert titles_b == {"b-chat"}
    finally:
        _delete_conversations(pg_engine, user_a)
        _delete_conversations(pg_engine, user_b)


def test_no_context_connection_sees_zero_rows_on_every_rls_table(pg_engine):
    """Required test 2. A transaction that never had ``app.user_sub`` set
    AT ALL -- true SQL NULL via ``current_setting(..., true)``, confirmed
    empirically to be a DIFFERENT state from "reset since an earlier
    transaction on this same session" (which lands on ``''``, a placeholder
    GUC's boot value, once ``set_config`` has ever run on that session --
    also never matches a real ``user_sub``, but for a different reason, and
    conflating the two would test the wrong mechanism) -- must see zero
    rows on BOTH tables this migration protects.

    Uses its OWN brand-new engine, never touched by ``_insert_conversation``/
    ``_insert_message`` above (both of which run through ``rls_transaction``
    on ``pg_engine`` and would leave ITS one physical connection no longer
    virgin): reusing ``pg_engine`` here would silently start testing "reset
    to ''" instead of "never set", which is not the same claim.

    A genuinely virgin, non-privileged connection cannot be produced by
    ``rls_transaction`` itself -- it always sets identity as its first
    statement, by design (see its own docstring) -- so this is the one
    place in this module that still applies ``SET LOCAL ROLE`` directly
    rather than through the wrapper. This is NOT the old per-test
    workaround (which used this mechanism for every test because the
    wrapper itself was unfixed): the other three required tests below call
    the real, fixed ``rls_transaction`` directly. This one exception exists
    only because "protected, but with identity truly never established" is
    a state the wrapper's own "always sets identity" contract makes
    structurally impossible to reach through it.
    """
    user = _fresh_user_sub()
    fresh_engine = build_engine(_DSN)
    try:
        conversation_id = _insert_conversation(pg_engine, user, title="hidden-from-no-context")
        _insert_message(pg_engine, conversation_id, user)

        with fresh_engine.begin() as conn:
            if _DSN_ROLE_IS_SUPERUSER:
                conn.execute(text(f'SET LOCAL ROLE "{_APP_ROLE}"'))
            identity = conn.execute(
                text("SELECT current_setting('app.user_sub', true)")
            ).scalar_one()
            conversation_count = conn.execute(_COUNT_CONVERSATIONS_SQL).scalar_one()
            message_count = conn.execute(_COUNT_MESSAGES_SQL).scalar_one()

        assert identity is None, (
            "this checkout must be genuinely virgin (app.user_sub never set) to "
            "prove the missing_ok/NULL mechanism specifically, not merely reset"
        )
        assert conversation_count == 0
        assert message_count == 0
    finally:
        fresh_engine.dispose()
        _delete_conversations(pg_engine, user)


def test_pooled_connection_does_not_leak_identity_across_checkouts(pg_engine):
    """Required test 3 -- decision D28's own reason for existing. THREE
    sequential touches of the SAME pooled connection (proven below via raw
    DBAPI connection identity, not assumed from pool internals): a real
    ``rls_transaction`` checkout for user_a; a manual, read-only peek that
    observes whatever identity/role state that checkout left behind BEFORE
    anything overwrites it; and a real ``rls_transaction`` checkout for
    user_b that must see none of user_a's rows.

    **Why the peek is manual, not another ``rls_transaction`` call.**
    ``rls_transaction`` always calls ``set_config`` (and, when configured,
    ``SET LOCAL ROLE``) as its own first statements, unconditionally --
    that is its entire contract. If the peek were itself a normal
    ``rls_transaction`` call, its own explicit ``set_config``/``SET LOCAL
    ROLE`` would overwrite whatever user_a left behind BEFORE anything
    could observe it, making this test unable to distinguish
    transaction-scoped from session-scoped no matter which one the wrapper
    actually implements. Confirmed empirically before settling on this
    design: an earlier draft that routed both checkouts through the
    wrapper stayed GREEN even after ``core/db.py``'s ``is_local`` argument
    was deliberately flipped to ``false`` -- a test that cannot fail is not
    a test. This version -- reading ``current_setting``/``current_user`` as
    the peek's OWN first statements, before calling anything from the
    wrapper -- was confirmed to turn RED under that exact mutation (see the
    task report).
    """
    user_a, user_b = _fresh_user_sub(), _fresh_user_sub()
    try:
        with rls_transaction(pg_engine, user_a, app_role=_EFFECTIVE_APP_ROLE) as conn:
            raw_connection_1 = conn.connection.dbapi_connection
            conn.execute(
                _INSERT_CONVERSATION_SQL,
                {"id": str(uuid7()), "user_sub": user_a, "title": "a-chat"},
            )

        with pg_engine.begin() as conn:
            raw_connection_2 = conn.connection.dbapi_connection
            leftover_identity = conn.execute(
                text("SELECT current_setting('app.user_sub', true)")
            ).scalar_one()
            leftover_role = conn.execute(text("SELECT current_user")).scalar_one()

        with rls_transaction(pg_engine, user_b, app_role=_EFFECTIVE_APP_ROLE) as conn:
            raw_connection_3 = conn.connection.dbapi_connection
            titles_seen_by_b = [row[0] for row in conn.execute(_SELECT_CONVERSATION_TITLES_SQL)]

        assert raw_connection_1 is raw_connection_2 is raw_connection_3, (
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
        assert titles_seen_by_b == []
    finally:
        _delete_conversations(pg_engine, user_a)
        _delete_conversations(pg_engine, user_b)


def test_owner_connection_is_still_filtered_by_force_rls(pg_engine):
    """Required test 4. ``DATABASE_URL``'s role is also the table OWNER
    (migrations run as that same role) -- confirmed below, not just
    assumed -- and plain ``ENABLE ROW LEVEL SECURITY`` exempts a table's
    owner by default. Migration 0004 additionally declares ``FORCE ROW
    LEVEL SECURITY`` on both tables specifically so that exemption does not
    apply; this is the test that would only start passing right up until
    FORCE is dropped, and only then start leaking every row to the owner
    connection.

    **Why this cannot simply call ``rls_transaction(engine, user,
    app_role=_EFFECTIVE_APP_ROLE)`` the way tests 1-3 now do.** Postgres's
    RLS owner-exemption check is keyed off the CURRENT effective role
    (``SET ROLE`` included, not the originally authenticated
    ``session_user``) -- so a connection that has ``SET LOCAL ROLE``'d to
    ``poseidon_app`` is, for the purposes of this exact check, no longer
    "the owner", regardless of which role the physical connection
    authenticated as. That is exactly what makes ``poseidon_app`` safe for
    tests 1-3 (an ordinary non-owner, non-superuser role, correctly
    filtered by plain ``ENABLE`` alone, FORCE or not) and exactly why it
    cannot be used to test FORCE's OWNER-specific contribution -- testing
    that requires the checked role to actually BE the owner while also not
    being a superuser, a combination no existing role on this dev database
    can provide.

    So this branch manufactures it for exactly one transaction that is
    ALWAYS rolled back, never committed: ``ALTER TABLE ... OWNER TO`` and
    ``SET LOCAL ROLE`` are both fully transactional in Postgres (verified
    against this exact database before this was written), so ownership and
    role both revert the instant this transaction ends, regardless of
    whether the test passes, fails, or raises. ``poseidon_app`` is never
    left owning anything -- doc 05 section 4 pins it as the non-owner
    application role, and a durable ownership change here would quietly
    contradict that. The ``SET LOCAL ROLE`` statement below is deliberately
    written in the exact same quoted-identifier form ``rls_transaction``
    itself uses, for consistency, even though it cannot be produced BY the
    wrapper here.

    Against a properly non-superuser DSN role (a real deploy, ``app_role=
    None``), the ``else`` branch is the whole test: a direct
    ``rls_transaction`` check as the DSN role, which already is the owner
    and already is not a superuser.
    """
    with pg_engine.connect() as conn:
        owner_is_current_user = conn.execute(
            text(
                "SELECT pg_get_userbyid(relowner) = current_user FROM pg_class "
                "WHERE relname = 'conversations'"
            )
        ).scalar_one()
    assert owner_is_current_user is True, (
        "this test only proves FORCE matters if DATABASE_URL's role really owns the table"
    )

    other_user = _fresh_user_sub()
    caller_context = _fresh_user_sub()
    try:
        _insert_conversation(pg_engine, other_user, title="owner-must-not-see-this")

        if _DSN_ROLE_IS_SUPERUSER:
            with pg_engine.connect() as conn:
                try:
                    conn.execute(text("ALTER TABLE conversations OWNER TO poseidon_app"))
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
                    rows = conn.execute(_SELECT_CONVERSATION_TITLES_SQL).all()
                finally:
                    conn.rollback()  # always -- see the docstring: never durably re-owns the table
            assert tuple(identity_ok) == (True, True), (
                "the ownership/role swap must actually take effect for this to prove anything"
            )
        else:
            with rls_transaction(pg_engine, caller_context, app_role=_EFFECTIVE_APP_ROLE) as conn:
                rows = conn.execute(_SELECT_CONVERSATION_TITLES_SQL).all()

        assert rows == []
    finally:
        _delete_conversations(pg_engine, other_user)


# ---------------------------------------------------------------------------
# documentation-of-record + the wrapper's own parameter contract
# ---------------------------------------------------------------------------


def test_superuser_bypasses_rls_without_wrapper(pg_engine):
    """Documentation-of-record, not a defensive nicety: proves the
    wrapper's ``SET LOCAL ROLE`` is genuinely load-bearing by showing what
    happens without it. A raw connection that never goes through
    ``rls_transaction`` at all -- exactly what every one of the four
    required tests above would have been doing before the round-0
    correction -- bypasses row-level security entirely when
    ``DATABASE_URL``'s role is a Postgres superuser (confirmed true for
    this compose database above), seeing another user's row through a
    bare, unfiltered ``SELECT``. Skipped (not vacuously passed) against a
    properly non-privileged DSN role, where this premise does not hold --
    plain ``ENABLE`` + ``FORCE`` already protects a bare connection there.
    """
    if not _DSN_ROLE_IS_SUPERUSER:
        pytest.skip(
            "DATABASE_URL's role is not a superuser in this environment -- a bare "
            "connection is already non-privileged and this test's premise "
            "(unconditional superuser bypass) does not apply"
        )

    other_user = _fresh_user_sub()
    try:
        _insert_conversation(pg_engine, other_user, title="visible-without-the-wrapper")

        with pg_engine.begin() as conn:  # deliberately bare: no rls_transaction at all
            titles = {row[0] for row in conn.execute(_SELECT_CONVERSATION_TITLES_SQL)}

        assert "visible-without-the-wrapper" in titles
    finally:
        _delete_conversations(pg_engine, other_user)


def test_app_role_none_skips_set_role_but_still_sets_identity(pg_engine):
    """``Settings.database_app_role=None`` (the documented escape hatch for
    an already-non-privileged DSN) must not also silently skip
    identity-setting -- ``set_config`` is unconditional; only the ``SET
    LOCAL ROLE`` step is gated on ``app_role``. Passes ``app_role=None``
    regardless of ``_DSN_ROLE_IS_SUPERUSER``: this test is about
    ``rls_transaction``'s own parameter contract, not about working around
    this environment's superuser DSN."""
    with pg_engine.connect() as conn:
        base_role = conn.execute(text("SELECT current_user")).scalar_one()

    user = _fresh_user_sub()
    with rls_transaction(pg_engine, user, app_role=None) as conn:
        identity = conn.execute(text("SELECT current_setting('app.user_sub', true)")).scalar_one()
        role = conn.execute(text("SELECT current_user")).scalar_one()

    assert identity == user, "set_config must still run unconditionally when app_role is None"
    assert role == base_role, "app_role=None must not issue any SET ROLE"


# ---------------------------------------------------------------------------
# catalog assertions -- migration 0004 built what the tests above rely on
# ---------------------------------------------------------------------------


def test_row_level_security_is_enabled_and_forced_on_both_tables(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname IN ('conversations', 'messages')"
            )
        ).all()
    by_name = {row.relname: row for row in rows}

    assert set(by_name) == {"conversations", "messages"}
    for table_name, row in by_name.items():
        assert row.relrowsecurity is True, table_name
        assert row.relforcerowsecurity is True, table_name


def test_poseidon_app_role_exists_without_bypassrls(pg_engine):
    """Also asserts ``rolsuper is False`` -- not itself required by the task
    brief, but load-bearing for this module: the round-0 correction relies
    on ``poseidon_app`` being a genuine non-superuser role, and required
    test 4's superuser branch re-derives the same fact live (see its
    ``identity_ok`` check) precisely because it matters that much."""
    with pg_engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = 'poseidon_app'")
        ).first()

    assert row is not None, "poseidon_app role must exist (migration 0004)"
    assert row.rolbypassrls is False
    assert row.rolsuper is False


@pytest.mark.parametrize("table_name", ["conversations", "messages"])
def test_poseidon_app_has_exactly_the_four_dml_privileges(pg_engine, table_name):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = 'poseidon_app' AND table_name = :table_name"
            ),
            {"table_name": table_name},
        ).all()
    privileges = {row.privilege_type for row in rows}

    assert privileges == {"SELECT", "INSERT", "UPDATE", "DELETE"}


@pytest.mark.parametrize(
    ("table_name", "policy_name"),
    [("conversations", "conversations_owner"), ("messages", "messages_owner")],
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


def test_rls_policies_module_is_ascii_on_disk():
    """Matches the codebase-wide ASCII-on-disk convention (e.g.
    ``test_runlog_module_is_ascii_on_disk``)."""
    offending = sorted({byte for byte in Path(__file__).read_bytes() if byte > 0x7F})
    assert not offending, f"{Path(__file__).name} holds non-ASCII bytes: {offending}"
