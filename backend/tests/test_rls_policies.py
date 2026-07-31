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
the task brief scopes it: two plain ``user_sub`` strings and raw SQL through
``rls_transaction`` are enough to prove the database itself enforces
isolation, and staying at this layer keeps these tests meaningful even
before any store/API code exists to wrap them.

**Why almost every test below routes through ``_identity_transaction``
instead of calling ``rls_transaction`` directly (read this before editing
any test in this file).** Doc 05 section 4's last bullet designs RLS around
the application connecting as a role that is "not the table owner and has
no BYPASSRLS" -- migration 0004's ``poseidon_app`` is that role for a real
deploy. This dev compose database's ``DATABASE_URL`` role (``poseidon``) is
instead the cluster's bootstrap SUPERUSER -- a property of the official
Postgres Docker image's ``POSTGRES_USER`` convention, confirmed against
this exact database (``SELECT rolsuper FROM pg_roles WHERE rolname =
current_user`` -> true), not assumed. Postgres superusers unconditionally
bypass row-level security: this is a hard invariant with no schema-level
override -- not even ``FORCE ROW LEVEL SECURITY`` touches it, since FORCE
only removes the (separate, weaker) OWNER exemption. Concretely, this means
every test below that runs its actual SELECT/INSERT through the raw
``poseidon`` connection proves nothing at all in this environment: RLS
never even engages, regardless of ``app.user_sub``, regardless of FORCE --
confirmed the hard way (this module's first draft used ``rls_transaction``
directly, and all four required tests failed by LEAKING rows, not by
erroring, until this was diagnosed). ``_identity_transaction`` and
``_no_identity_transaction`` below layer one extra statement, ``SET LOCAL
ROLE poseidon_app``, on top of the real production ``rls_transaction`` --
transaction-scoped (verified against this database: it reverts on COMMIT
or ROLLBACK exactly like ``app.user_sub`` itself) and needing no password
(a superuser may ``SET ROLE`` to any role) -- purely to reproduce, from the
one superuser DSN this dev stack provides, the non-superuser privilege
level a real deploy's application connection already has. Both helpers
check ``_DSN_ROLE_IS_SUPERUSER`` (computed once, at collection time, from
the same connection the module-level skip guard already opens) and add
nothing when it is false, so this file behaves identically to the simplest
possible version of itself against a properly non-superuser DSN role.

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
connection before the moment it compares identity across two checkouts --
sharing one engine across every test in this module would make that
guarantee depend on execution order and what earlier tests happened to do
to the pool. Every other test here could safely share one engine (a
Postgres GUC set with ``is_local=true`` resets the instant its own
transaction ends, regardless of which physical connection carried it -- see
``core.db``'s module docstring) but all get their own, for uniformity and
so no test's correctness depends on another test having run first.
"""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.db import build_engine, rls_transaction
from poseidon.core.util.uuid7 import uuid7

pytestmark = pytest.mark.pg

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0004)"

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        f"DATABASE_URL is not set - pg RLS tests need a Postgres: {_UP_HINT}, {_MIGRATE_HINT}",
        allow_module_level=True,
    )

# Computed once, here, from the same probe connection the skip guard below
# already opens -- see the module docstring's "why almost every test routes
# through _identity_transaction" for what this drives.
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


_SET_IDENTITY_SQL = text("SELECT set_config('app.user_sub', :sub, true)")
_SET_APP_ROLE_SQL = text("SET LOCAL ROLE poseidon_app")

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


@contextmanager
def _identity_transaction(engine: Engine, user_sub: str) -> Iterator[Connection]:
    """``rls_transaction``, plus -- only when ``DATABASE_URL``'s role is
    itself a Postgres superuser (this dev compose database; see the module
    docstring) -- an extra ``SET LOCAL ROLE poseidon_app`` so the
    connection is actually subject to row-level security at all. Against a
    properly non-superuser DSN role (a real deploy's expected shape,
    doc 05 section 4), this is exactly ``rls_transaction`` with nothing
    added."""
    with rls_transaction(engine, user_sub) as conn:
        if _DSN_ROLE_IS_SUPERUSER:
            conn.execute(_SET_APP_ROLE_SQL)
        yield conn


@contextmanager
def _no_identity_transaction(engine: Engine) -> Iterator[Connection]:
    """A transaction that never sets ``app.user_sub`` at all -- required
    test 2 needs a connection with NO identity context, not merely a
    different one -- plus the same superuser workaround as
    :func:`_identity_transaction`."""
    with engine.begin() as conn:
        if _DSN_ROLE_IS_SUPERUSER:
            conn.execute(_SET_APP_ROLE_SQL)
        yield conn


def _insert_conversation(engine: Engine, user_sub: str, title: str = "chat") -> str:
    """Insert one conversation as its own owner, through
    :func:`_identity_transaction` -- never a bare admin insert -- so every
    fixture row this suite creates is itself quiet proof that a legitimate
    same-user INSERT satisfies the owner policy's ``WITH CHECK``. Returns
    the new row's id."""
    new_id = str(uuid7())
    with _identity_transaction(engine, user_sub) as conn:
        conn.execute(_INSERT_CONVERSATION_SQL, {"id": new_id, "user_sub": user_sub, "title": title})
    return new_id


def _insert_message(engine: Engine, conversation_id: str, user_sub: str, role: str = "user") -> str:
    """Insert one message on ``conversation_id``, through
    :func:`_identity_transaction` under ``user_sub`` -- same reasoning as
    :func:`_insert_conversation`."""
    new_id = str(uuid7())
    with _identity_transaction(engine, user_sub) as conn:
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
    """Teardown: delete through :func:`_identity_transaction` under the
    OWNING sub (task brief's pinned cleanup pattern, never an admin
    bypass). ``ON DELETE CASCADE`` (migration 0004) takes this user's
    messages with it."""
    with _identity_transaction(engine, user_sub) as conn:
        conn.execute(_DELETE_CONVERSATIONS_BY_USER_SQL, {"user_sub": user_sub})


# ---------------------------------------------------------------------------
# the four required tests (doc 05 section 4 / doc 06 section 5, L1 category)
# ---------------------------------------------------------------------------


def test_two_user_isolation(pg_engine):
    """Required test 1. Two users each write a conversation; a bare,
    unfiltered ``SELECT`` (no ``WHERE`` clause -- the module docstring's "not
    by remembering to add WHERE clauses") under each user's context sees
    only that user's own row."""
    user_a, user_b = _fresh_user_sub(), _fresh_user_sub()
    try:
        _insert_conversation(pg_engine, user_a, title="a-chat")
        _insert_conversation(pg_engine, user_b, title="b-chat")

        with _identity_transaction(pg_engine, user_a) as conn:
            titles_a = {row[0] for row in conn.execute(_SELECT_CONVERSATION_TITLES_SQL)}
        with _identity_transaction(pg_engine, user_b) as conn:
            titles_b = {row[0] for row in conn.execute(_SELECT_CONVERSATION_TITLES_SQL)}

        assert titles_a == {"a-chat"}
        assert titles_b == {"b-chat"}
    finally:
        _delete_conversations(pg_engine, user_a)
        _delete_conversations(pg_engine, user_b)


def test_no_context_connection_sees_zero_rows_on_every_rls_table(pg_engine):
    """Required test 2. A connection that never went through
    :func:`rls_transaction` -- ``app.user_sub`` was never set for this
    transaction -- must see zero rows on BOTH tables this migration
    protects, not just the one a caller happened to think to check."""
    user = _fresh_user_sub()
    try:
        conversation_id = _insert_conversation(pg_engine, user, title="hidden-from-no-context")
        _insert_message(pg_engine, conversation_id, user)

        with _no_identity_transaction(pg_engine) as conn:
            conversation_count = conn.execute(_COUNT_CONVERSATIONS_SQL).scalar_one()
            message_count = conn.execute(_COUNT_MESSAGES_SQL).scalar_one()

        assert conversation_count == 0
        assert message_count == 0
    finally:
        _delete_conversations(pg_engine, user)


def test_pooled_connection_does_not_leak_identity_across_checkouts(pg_engine):
    """Required test 3 -- decision D28's own reason for existing. Two
    SEQUENTIAL checkouts of the SAME pooled connection (proven below via raw
    DBAPI connection identity, not assumed from pool internals) under two
    different users: the second checkout must see none of the first's rows.

    **Why this peeks at ``current_setting`` before setting user_b's own
    identity, rather than just calling ``_identity_transaction(pg_engine,
    user_b)`` and checking what it reads back.** ``rls_transaction`` always
    calls ``set_config`` as ITS OWN first statement, unconditionally --
    that is its entire contract. So if the second checkout's identity were
    set the normal way (through ``rls_transaction``/``_identity_transaction``
    again), user_b's own explicit call would overwrite whatever user_a left
    behind BEFORE any query ran, and the two would be indistinguishable by
    result: "sees none of user_a's rows" would hold whether ``set_config``'s
    third argument is ``true`` or ``false``, because something always
    overwrites it either way. Confirmed empirically, not just reasoned
    about, before writing this version: flipping ``db.py``'s
    ``_SET_IDENTITY_SQL`` to ``false`` left an earlier draft of this exact
    test (both checkouts through ``_identity_transaction``) GREEN -- a test
    that cannot fail is not a test. This version checks out the connection
    manually and reads ``current_setting('app.user_sub', true)`` as its
    OWN first statement, before setting user_b's identity -- the one moment
    a transaction-scoped implementation guarantees NULL and a session-scoped
    one would still show user_a's value -- and was confirmed to turn RED
    under that same mutation (see the task report).
    """
    user_a, user_b = _fresh_user_sub(), _fresh_user_sub()
    try:
        with _identity_transaction(pg_engine, user_a) as conn:
            raw_connection_1 = conn.connection.dbapi_connection
            conn.execute(
                _INSERT_CONVERSATION_SQL,
                {"id": str(uuid7()), "user_sub": user_a, "title": "a-chat"},
            )

        with pg_engine.begin() as conn:
            raw_connection_2 = conn.connection.dbapi_connection
            leftover_context = conn.execute(
                text("SELECT current_setting('app.user_sub', true)")
            ).scalar_one()
            if _DSN_ROLE_IS_SUPERUSER:
                conn.execute(_SET_APP_ROLE_SQL)
            conn.execute(_SET_IDENTITY_SQL, {"sub": user_b})
            titles_seen_by_b = [row[0] for row in conn.execute(_SELECT_CONVERSATION_TITLES_SQL)]

        assert raw_connection_2 is raw_connection_1, (
            "this test only proves what it claims if the pool actually reused "
            "the same physical connection across the two checkouts"
        )
        assert leftover_context != user_a, (
            "app.user_sub leaked across a pooled-connection checkout -- "
            "set_config's is_local argument has regressed to session-scoped"
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

    In THIS environment the owner (``poseidon``) is also a superuser (see
    the module docstring), and superuser bypass cannot be tested around by
    switching roles alone -- a superuser stays exempt no matter what role
    it runs as unless it actually stops being the thing being tested. So
    this branch temporarily makes ``poseidon_app`` (confirmed non-superuser,
    non-bypassrls by the role-catalog test below) own ``conversations``,
    for exactly one transaction that is ALWAYS rolled back, never
    committed: ``ALTER TABLE ... OWNER TO`` and ``SET LOCAL ROLE`` are both
    fully transactional in Postgres (verified against this exact database
    before writing this test), so ownership and role both revert the
    instant this transaction ends, regardless of whether the test passes,
    fails, or raises. ``poseidon_app`` is never left owning anything --
    doc 05 section 4 pins it as the non-owner application role, and a
    durable ownership change here would quietly contradict that.

    Against a properly non-superuser DSN role (a real deploy), the ``else``
    branch is the whole test: a direct, un-worked-around
    ``rls_transaction`` check as the DSN role, which already is the owner.
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
                    conn.execute(_SET_APP_ROLE_SQL)
                    conn.execute(_SET_IDENTITY_SQL, {"sub": caller_context})
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
            with rls_transaction(pg_engine, caller_context) as conn:
                rows = conn.execute(_SELECT_CONVERSATION_TITLES_SQL).all()

        assert rows == []
    finally:
        _delete_conversations(pg_engine, other_user)


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
    brief, but load-bearing for this module: :func:`_identity_transaction`
    relies on ``poseidon_app`` being a genuine non-superuser role, and
    required test 4's superuser branch re-derives the same fact live (see
    its ``identity_ok`` check) precisely because it matters that much."""
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
