import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


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
    # Task 3 extends it to 0009 (the poseidon_worker claim role) --
    # `alembic current` reports only the single revision at the tip, so this
    # assertion always names the CURRENT head, not every revision the chain
    # passed through on the way there.
    current = _alembic("current", env_overrides=env)
    assert current.returncode == 0, current.stderr
    assert "0009" in current.stdout, current.stdout

    # Phase 14 Task 3: the chain has to walk BACKWARDS too. Every migration
    # since 0002 is a no-op on SQLite (each opens with a dialect guard), so
    # what this half proves is exactly the wiring -- that each revision's
    # downgrade() exists and its down_revision links up -- never the DDL
    # itself. `test_0009_round_trips_against_postgres` below is where the
    # real create/drop is exercised.
    down = _alembic("downgrade", "-1", env_overrides=env)
    assert down.returncode == 0, down.stderr
    back_at_0008 = _alembic("current", env_overrides=env)
    assert "0008" in back_at_0008.stdout, back_at_0008.stdout

    up_again = _alembic("upgrade", "head", env_overrides=env)
    assert up_again.returncode == 0, up_again.stderr
    assert "0009" in _alembic("current", env_overrides=env).stdout


@pytest.mark.pg
def test_0009_round_trips_against_postgres():
    """0009's actual DDL, down and back up, against a real Postgres.

    The SQLite test above cannot reach any of this: roles, policies and
    grants have no SQLite equivalent, so every migration since 0002 returns
    immediately there. This is therefore the only place the things 0009
    claims to do are observed -- the role exists, its ``memory_outbox``
    policy exists, both disappear on downgrade, and both come back.

    Restoring the database is done in a ``finally`` and asserted, not hoped
    for: this suite runs against a long-lived shared dev database that the
    compose worker and every other pg suite also use, and leaving it at
    0008 would break them all.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip("DATABASE_URL is not set")
    psycopg = pytest.importorskip("psycopg")
    from poseidon.core.data.synthetic_client import normalize_dsn

    env = {"DATABASE_URL": dsn, "S3_BUCKET": "poseidon-artifacts"}

    def _state() -> tuple[bool, bool, bool]:
        """``(role exists, policy exists, DSN user is a member of the role)``.

        The third element pins ``GRANT poseidon_worker TO CURRENT_USER`` --
        0009's single most RDS-critical statement, since a non-superuser may
        only ``SET ROLE`` to a role it is a MEMBER of, and without it the
        worker's claim raises on every cycle. Nothing else in either suite
        fails if that line is deleted.

        It reads ``pg_auth_members`` directly rather than asking
        ``pg_has_role(current_user, 'poseidon_worker', 'MEMBER')``, which
        would be VACUOUS here: a superuser passes ``pg_has_role`` for every
        role in the cluster whether granted or not, and this project's
        compose DSN is a superuser. Verified on that database --
        ``pg_has_role`` answers true for ``poseidon_app``, which the DSN
        user has never been granted, while the catalog query below correctly
        answers false. Only the catalog distinguishes a real GRANT.
        """
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'poseidon_worker'")
                role = cur.fetchone() is not None
                cur.execute(
                    "SELECT 1 FROM pg_policies WHERE tablename = 'memory_outbox' "
                    "AND policyname = 'memory_outbox_worker'"
                )
                policy = cur.fetchone() is not None
                cur.execute(
                    "SELECT 1 FROM pg_auth_members m "
                    "JOIN pg_roles granted ON granted.oid = m.roleid "
                    "JOIN pg_roles grantee ON grantee.oid = m.member "
                    "WHERE granted.rolname = 'poseidon_worker' "
                    "AND grantee.rolname = current_user"
                )
                member = cur.fetchone() is not None
        return role, policy, member

    if _state() != (True, True, True):
        pytest.skip("this database is not at 0009 - migrate it first")

    try:
        down = _alembic("downgrade", "0008", env_overrides=env)
        assert down.returncode == 0, down.stderr
        assert _state() == (False, False, False), (
            "downgrade must drop the worker policy, the role it was written for, and "
            "(with the role) the membership 0009 granted"
        )
    finally:
        up = _alembic("upgrade", "head", env_overrides=env)
        assert up.returncode == 0, up.stderr

    assert _state() == (True, True, True), (
        "upgrade must put the role, its policy, and the DSN user's membership in it back -- "
        "the membership is what makes SET ROLE legal for a non-superuser, i.e. on RDS"
    )
