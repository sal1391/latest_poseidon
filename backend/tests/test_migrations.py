import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Phase 16 Task 8 / ruling P16-R10: the name of the throwaway database the two
# pg round-trips below migrate. Drizzle owns the application schema now (D58),
# so Alembic must never be pointed at the app database again -- `upgrade head`
# there restarts from revision zero (no `alembic_version` table) and dies on
# Drizzle's tables. These tests create this database, migrate it, and drop it.
_PROBE_DB = "poseidon_alembic_probe"


@pytest.fixture
def alembic_probe():
    """Yield the DSN of a freshly created, Alembic-only throwaway database.

    Migrations 0001-0010 are frozen history and still have to be proven to be
    *valid* history -- their DDL applied and reversed against a real Postgres,
    which SQLite cannot show (every migration since 0002 is a dialect no-op
    there). What they no longer get to do is own the database the application
    uses. So each test gets its own database, created here and dropped in the
    teardown whether it passed or not.

    The DSN is yielded in SQLAlchemy spelling (``postgresql+psycopg://``),
    because that is what ``_alembic`` hands to Alembic through ``DATABASE_URL``;
    callers that open their own psycopg connection normalize it first, exactly
    as the rest of this suite does.

    **What this fixture cannot isolate, and no fixture could.** Roles and role
    memberships are CLUSTER-wide objects, not per-database ones, so 0009's
    ``CREATE ROLE``/``DROP ROLE`` and 0009's and 0010's ``GRANT ... TO
    CURRENT_USER`` still reach across every database on the server, including
    the application's. Each test therefore still restores cluster state in a
    ``finally`` -- see their own comments.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    psycopg = pytest.importorskip("psycopg")
    from poseidon.core.data.synthetic_client import normalize_dsn

    admin = psycopg.connect(normalize_dsn(dsn), autocommit=True, connect_timeout=5)
    try:
        admin.execute(f"DROP DATABASE IF EXISTS {_PROBE_DB}")
        admin.execute(f"CREATE DATABASE {_PROBE_DB}")
        yield dsn.rsplit("/", 1)[0] + "/" + _PROBE_DB
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {_PROBE_DB}")
        admin.close()


def _alembic(*args, env_overrides: dict) -> subprocess.CompletedProcess:
    """One ``python -m alembic ...`` run against ``BACKEND``, with
    ``env_overrides`` layered onto this process's environment.

    Factored out of ``test_upgrade_head_on_sqlite``'s two inline
    ``subprocess.run`` calls when Phase 14 Task 3 added a second (pg)
    migration test with four more of them -- same shape, same cwd, same
    ``capture_output``, just no longer copy-pasted six times.
    """
    env = dict(os.environ)
    env.update(env_overrides)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        env=env,
    )


def test_upgrade_head_on_sqlite(tmp_path):
    db = tmp_path / "mig.db"
    env = {
        "DATABASE_URL": f"sqlite:///{db.as_posix()}",
        "S3_BUCKET": "poseidon-artifacts",
    }
    result = _alembic("upgrade", "head", env_overrides=env)
    assert result.returncode == 0, result.stderr
    assert db.exists()

    # Phase 10 Task 1: the chain must actually reach head, not just exit 0 --
    # `alembic upgrade head` would also exit 0 (a silent no-op) if a revision
    # were never chained onto its predecessor's down_revision, so `alembic
    # current` is checked explicitly rather than trusting the upgrade
    # command's own return code alone. Phase 11 Task 1 extended this same
    # check from 0004 to 0005 (run-log RLS, admin role, redaction support);
    # Phase 12 Task 1 extended it again, 0005 to 0006 (message_feedback); the
    # un-vote follow-up extended it once more, 0006 to 0007 (verdict becomes
    # nullable); Phase 13 Task 1 extended it again, 0007 to 0008
    # (personalization: user_profile/user_memory/memory_outbox); Phase 14
    # Task 3 extends it to 0009 (the poseidon_worker claim role), and Task 3b
    # once more to 0010 (poseidon_app's membership grant, 0009's symmetric
    # twin) -- `alembic current` reports only the single revision at the tip,
    # so this assertion always names the CURRENT head, not every revision the
    # chain passed through on the way there.
    current = _alembic("current", env_overrides=env)
    assert current.returncode == 0, current.stderr
    assert "0010" in current.stdout, current.stdout

    # Phase 14 Task 3: the chain has to walk BACKWARDS too. Every migration
    # since 0002 is a no-op on SQLite (each opens with a dialect guard), so
    # what this half proves is exactly the wiring -- that each revision's
    # downgrade() exists and its down_revision links up -- never the DDL
    # itself. `test_0009_round_trips_against_postgres` and
    # `test_0010_round_trips_against_postgres` below are where the real
    # create/drop and grant/revoke are exercised.
    down = _alembic("downgrade", "-1", env_overrides=env)
    assert down.returncode == 0, down.stderr
    back_at_0009 = _alembic("current", env_overrides=env)
    assert "0009" in back_at_0009.stdout, back_at_0009.stdout

    up_again = _alembic("upgrade", "head", env_overrides=env)
    assert up_again.returncode == 0, up_again.stderr
    assert "0010" in _alembic("current", env_overrides=env).stdout


@pytest.mark.pg
def test_0009_round_trips_against_postgres(alembic_probe):
    """0009's per-database DDL, down and back up, against a real Postgres.

    The SQLite test above cannot reach any of this: policies and grants have
    no SQLite equivalent, so every migration since 0002 returns immediately
    there. This is where 0009's ``memory_outbox`` policy and its SELECT/UPDATE
    grants are observed being created, dropped and recreated for real.

    **Phase 16 Task 8 / ruling P16-R10: this runs on a throwaway database.**
    It used to migrate the shared dev database, guarded by "if the state is
    not already (True, True, True), skip -- migrate it first". That guard was
    a proxy for "this database is at Alembic 0009", and D58 broke the proxy:
    Drizzle makes those facts true on a database Alembic has never touched, so
    the guard stopped skipping and the downgrade failed with "Destination 0008
    is not a valid downgrade target". The guard is **gone** -- ``alembic_probe``
    hands over a database this test migrates from zero itself.

    **What this test can no longer prove, and why (ruling P16-R13).** Roles and
    role memberships are CLUSTER-wide objects, not per-database ones. On any
    cluster that hosts the application database, Drizzle's ``0000_roles.sql``
    has already created ``poseidon_worker`` and ``0002_grants_force_rls.sql``
    has already granted it to CURRENT_USER before this test runs -- measured on
    a freshly created probe database, with Alembic not yet invoked:
    ``role=True, member=True, policy=False, grants=()``. So an assertion that
    the role or the membership exists after ``upgrade head`` would pass whether
    or not 0009 issued its ``CREATE ROLE`` and ``GRANT ... TO CURRENT_USER`` at
    all, and an assertion that they vanish on downgrade would depend on the app
    database's Drizzle grant keeping the role alive -- true here, false on a
    bare cluster holding only the probe. Both would be pins that test the
    environment rather than the migration, so **neither is asserted**. Those
    two objects are covered where they now actually live: by the Drizzle
    migrations on the application database, and by ``test_rls_policies.py``.

    Everything asserted below is therefore per-database, and each fact is
    checked on the fresh probe first, so a value that was already true before
    Alembic ran cannot masquerade as something 0009 did.

    Restoring is still done in a ``finally`` and asserted, not hoped for --
    the probe database is dropped either way, but the walk down to 0008 passes
    THROUGH 0010's ``REVOKE poseidon_app FROM CURRENT_USER``, which is
    cluster-wide and would otherwise leave the real application database
    unable to ``SET LOCAL ROLE`` on every request.
    """
    psycopg = pytest.importorskip("psycopg")
    from poseidon.core.data.synthetic_client import normalize_dsn

    dsn = alembic_probe
    env = {"DATABASE_URL": dsn, "S3_BUCKET": "poseidon-artifacts"}

    def _policy() -> bool:
        """Whether 0009's ``memory_outbox_worker`` policy exists **on this
        database**.

        ``pg_policies`` is a per-database catalog, so unlike the role and the
        membership this is genuinely 0009's doing on the probe and nobody
        else's -- it reads False on the fresh probe, which the test asserts
        before it migrates anything.
        """
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_policies WHERE tablename = 'memory_outbox' "
                    "AND policyname = 'memory_outbox_worker'"
                )
                return cur.fetchone() is not None

    def _outbox_grants() -> tuple[str, ...]:
        """The privileges ``poseidon_worker`` explicitly holds on this
        database's ``memory_outbox``.

        Per-database, like the policy and unlike the role and the membership,
        so this is 0009's own work on the probe and observable going away and
        coming back. ``information_schema.role_table_grants`` lists only real
        GRANTs, which is the point -- ``has_table_privilege`` would also answer
        true for anything PUBLIC happens to hold.
        """
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = 'poseidon_worker' AND table_name = 'memory_outbox' "
                    "ORDER BY privilege_type"
                )
                return tuple(row[0] for row in cur.fetchall())

    # The baseline, taken BEFORE Alembic runs: neither fact is true yet on this
    # database, so what the upgrade below turns True is 0009's own work and not
    # something the cluster already happened to hold.
    assert _policy() is False, "a fresh probe database cannot already carry 0009's policy"
    assert _outbox_grants() == (), "a fresh probe database cannot already carry 0009's grants"

    at_head = _alembic("upgrade", "head", env_overrides=env)
    assert at_head.returncode == 0, at_head.stderr
    assert _policy() is True, "upgrade must create the memory_outbox_worker policy"
    assert _outbox_grants() == ("SELECT", "UPDATE"), (
        "the claim is SELECT ... FOR UPDATE SKIP LOCKED, and row locking is checked "
        "against UPDATE -- both grants are load-bearing"
    )

    try:
        down = _alembic("downgrade", "0008", env_overrides=env)
        assert down.returncode == 0, down.stderr
        assert _policy() is False, "downgrade must drop the memory_outbox_worker policy"
        assert _outbox_grants() == (), (
            "downgrade must revoke the worker's SELECT and UPDATE on memory_outbox"
        )
    finally:
        up = _alembic("upgrade", "head", env_overrides=env)
        assert up.returncode == 0, up.stderr

    assert _policy() is True, "upgrade must put the policy back"
    assert _outbox_grants() == ("SELECT", "UPDATE"), "upgrade must re-grant both privileges"


@pytest.mark.pg
def test_0010_round_trips_against_postgres(alembic_probe):
    """0010's one statement -- ``GRANT poseidon_app TO CURRENT_USER`` --
    revoked and re-granted against a real Postgres.

    The SQLite test above cannot see any of this for the same reason it
    cannot see 0009's: role memberships have no SQLite equivalent, so what
    it proves there is the wiring (this revision's ``downgrade`` exists and
    its ``down_revision`` links up) and nothing about the grant.

    **Why this one migrates the database itself instead of skipping when it
    finds one that is behind.** This test always ran ``upgrade head`` first,
    because the row it pins is created by the very revision under test, so a
    "skip if the state looks wrong" guard would have turned its RED run into
    a SKIP -- indistinguishable from a pass, proving nothing. Phase 16 Task 8
    (ruling P16-R10) kept that shape and gave it a database of its own: the
    upgrade now runs against ``alembic_probe`` rather than the application
    database, which Drizzle owns since D58. Every assertion below is
    unchanged.

    The membership itself is CLUSTER-wide, so unlike 0009's policy and table
    grants it cannot be isolated by a throwaway database -- this test really
    does revoke, and restore, the membership the running application relies
    on. Hence the ``finally``.
    """
    psycopg = pytest.importorskip("psycopg")
    from poseidon.core.data.synthetic_client import normalize_dsn

    dsn = alembic_probe
    env = {"DATABASE_URL": dsn, "S3_BUCKET": "poseidon-artifacts"}

    def _member() -> bool:
        """``True`` when the DSN user is a MEMBER of ``poseidon_app``.

        Reads ``pg_auth_members`` directly rather than asking
        ``pg_has_role(current_user, 'poseidon_app', 'MEMBER')``, for the
        reason 0009's own pin states and this database proved twice over: a
        superuser passes ``pg_has_role`` for every role in the cluster
        whether it was granted or not, and this project's compose DSN is a
        superuser. ``pg_has_role`` answered TRUE for ``poseidon_app`` here
        while ``pg_auth_members`` -- correctly -- held no such row at all,
        which is exactly the gap migration 0010 exists to close. Only the
        catalog distinguishes a real GRANT.
        """
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM pg_auth_members m "
                    "JOIN pg_roles granted ON granted.oid = m.roleid "
                    "JOIN pg_roles grantee ON grantee.oid = m.member "
                    "WHERE granted.rolname = 'poseidon_app' "
                    "AND grantee.rolname = current_user"
                )
                return cur.fetchone() is not None

    at_head = _alembic("upgrade", "head", env_overrides=env)
    assert at_head.returncode == 0, at_head.stderr
    assert _member() is True, (
        "a database at head must leave its DSN user a MEMBER of poseidon_app: that "
        "membership is 0010's only statement, and the only thing that makes "
        "rls_transaction's per-request SET LOCAL ROLE legal for a non-superuser DSN "
        "(i.e. on RDS, where no role is a superuser)"
    )

    try:
        down = _alembic("downgrade", "0009", env_overrides=env)
        assert down.returncode == 0, down.stderr
        assert _member() is False, (
            "downgrade must REVOKE the membership explicitly -- poseidon_app is shared and "
            "0004 deliberately never drops it, so unlike 0009's role drop there is nothing "
            "here to take the membership away with it"
        )
    finally:
        # Same discipline as 0009's round-trip, and for a reason the probe
        # database does NOT remove: this membership is cluster-wide, so
        # leaving it revoked would leave the real application database's
        # every request unable to SET LOCAL ROLE the moment its DSN stops
        # being a superuser.
        up = _alembic("upgrade", "head", env_overrides=env)
        assert up.returncode == 0, up.stderr

    assert _member() is True, "upgrade must put the membership back"
