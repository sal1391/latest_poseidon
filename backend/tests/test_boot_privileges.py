"""Tests for Phase 14 Task 3's boot privilege probe
(:func:`poseidon.core.db.assert_boot_privileges`) -- the fused P10 I-5 +
P13 T4-M6 check that turns two production-only, SILENT failure modes into a
refusal at boot.

The two modes, both of which this project has already met once:

1. **RLS silently disabled.** A ``DATABASE_URL`` whose role carries
   ``rolsuper`` or ``rolbypassrls`` bypasses row-level security
   unconditionally -- no schema-level override exists (``core/db.py``'s own
   "round-0 correction"). ``DATABASE_APP_ROLE`` is what this codebase uses
   to claw that back (``SET LOCAL ROLE`` per transaction), so a privileged
   DSN with ``DATABASE_APP_ROLE`` unset is a server serving every user's
   rows to every user -- and nothing about it looks wrong at runtime.
2. **The worker claims nothing, forever.** The distillation worker's claim
   query is cross-user by construction and now runs under an explicitly
   granted ``poseidon_worker`` role (migration 0009). On a database where
   that role is missing, or where the DSN's own role is not a member of it,
   the claim raises or returns zero rows on every cycle while the worker's
   logs stay perfectly healthy.

**Why some tests here are NOT pg-marked.** Unlike every other pg suite in
this repo, this file deliberately splits: the three tests that need no
database at all (the non-Postgres no-op, the unreachable-database
tolerance, the malformed-role refusal) run in the OFFLINE suite, because
they are precisely the behaviors that keep the offline suite's own
``chat_mode="live"`` apps -- every one of which is built against the
unreachable placeholder DSN ``postgresql+psycopg://nobody:nope@
127.0.0.1:1/void`` -- booting. A regression in those would show up as
dozens of unrelated failures elsewhere; pinning them here, where the
behavior lives, is what makes that diagnosable. The rest carry
``@pytest.mark.pg`` individually and take the ``pg_engine`` fixture, which
skips (never fails) when no usable Postgres is configured.

**The RDS rehearsal.** ``nonprivileged_dsn`` creates a throwaway
``LOGIN``-capable role with neither ``rolsuper`` nor ``rolbypassrls`` --
the shape of every real RDS master user, which cannot be a superuser and
cannot be granted ``BYPASSRLS`` -- and connects as it. That is the habitat
this whole task exists for, rehearsed on compose: it proves the probe
passes for a non-privileged DSN with no app role, refuses before the
``GRANT``, and passes after it.
"""

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError

from poseidon.core.config import Settings
from poseidon.core.db import (
    WORKER_ROLE,
    _tcp_reachable,
    assert_boot_privileges,
    build_engine,
)

CONNECT_TIMEOUT_SECONDS = 2

#: The same unreachable-but-well-formed DSN every offline suite's
#: ``chat_mode="live"`` app is built against (``test_live_chat_sse.py``'s
#: own ``_PLACEHOLDER_DSN``, reused verbatim rather than re-invented).
#:
#: An earlier version of this comment asserted that port 1 on loopback
#: "refuses instantly, so these tests cost milliseconds". That is not what
#: happens on this project's Windows host: the SYN is DROPPED, not refused,
#: and a connection attempt with libpq's default of "wait indefinitely"
#: took a measured 130 seconds to give up. That measurement is the entire
#: reason ``core/db.py``'s probe carries an explicit
#: ``connect_timeout``; with it, each attempt here costs about 2 seconds
#: (libpq's own floor). Do not "simplify" that timeout away.
_UNREACHABLE_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

_DSN = os.environ.get("DATABASE_URL", "")

_APP_ROLE = "poseidon_app"

#: Name prefix of the throwaway role ``nonprivileged_dsn`` creates.
#: Deliberately unmistakable and namespaced: it is created and dropped
#: inside one test, but a crashed run could leave it behind in a long-lived
#: dev database, and whoever finds it should be able to tell instantly what
#: made it.
#:
#: The per-fixture random suffix is not decoration. Roles are CLUSTER-scoped,
#: so two pg runs against the same Postgres (two developers, or a local run
#: overlapping CI) would otherwise share one role name -- and this fixture
#: both creates and drops it, so one run would delete the other's role
#: mid-test. A fixed name makes concurrent pg runs actively destructive to
#: each other; a suffixed one makes them merely simultaneous.
_PROBE_ROLE_PREFIX = "poseidon_boot_probe_nonpriv"
_PROBE_PASSWORD = "probe"


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        _env_file=None,
        database_url=_DSN or _UNREACHABLE_DSN,
        s3_bucket="poseidon-artifacts",
    )
    defaults.update(overrides)
    return Settings(**defaults)


# ===========================================================================
# offline: the three behaviors that keep every OTHER suite's live-mode app
# booting (see this module's docstring)
# ===========================================================================


def test_the_probe_is_a_no_op_on_a_non_postgres_engine():
    """``pg_roles``, ``SET ROLE`` and row-level security are all Postgres-
    only, so on any other dialect there is no verdict to render -- the same
    ``bind.dialect.name != "postgresql"`` guard every migration since 0002
    opens with."""
    engine = build_engine("sqlite://")
    try:
        assert assert_boot_privileges(engine, _settings(database_url="sqlite://")) is None
    finally:
        engine.dispose()


def test_an_unreachable_database_is_not_a_privilege_verdict():
    """A probe that cannot connect renders NO verdict and does not raise.

    This is the deliberate boundary of what this function is for. It
    answers "are this database's privileges wired the way this process
    needs", not "is the database up" -- ``/ready`` is the surface that
    answers the second question, and ``api/app.py``'s own contract
    (``build_engine`` is lazy: "an unreachable-but-well-formed host still
    builds fine here and only fails later, per call") is unchanged by this
    task. Turning a down database into a boot crash would also break every
    offline test that builds a ``chat_mode="live"`` app against the
    placeholder DSN, which is not a behavior change this task is allowed
    to make in passing.
    """
    engine = build_engine(_UNREACHABLE_DSN)
    try:
        assert assert_boot_privileges(engine, _settings(database_app_role=_APP_ROLE)) is None
    finally:
        engine.dispose()


def test_the_preflight_reports_an_unreachable_host_and_falls_through_when_it_cannot_tell():
    """The TCP pre-flight that decides whether opening a real connection is
    worth 2 seconds -- pinned directly, since its verdict is what routes a
    boot to the no-verdict path.

    The fall-through half matters as much as the detection half: a DSN with
    no host (libpq's Unix-socket/default case), a Unix-socket directory in
    the host field, and libpq's comma-separated multi-host form are all
    things this pre-flight cannot meaningfully ask about, and every one of
    them must answer ``True`` so the REAL connection decides rather than the
    probe skipping a database that is perfectly fine.
    """
    assert _tcp_reachable(make_url(_UNREACHABLE_DSN)) is False, (
        "nothing listens on port 1 -- the pre-flight has to say so, or every boot "
        "against a down database pays the full libpq timeout again"
    )
    for undecidable in (
        "postgresql+psycopg://user@/poseidon",  # no host at all
        "postgresql+psycopg://user@/poseidon?host=/var/run/postgresql",
        "postgresql+psycopg://user@/tmp:5432/poseidon",
    ):
        assert _tcp_reachable(make_url(undecidable)) is True, (
            f"{undecidable} is not something a TCP pre-flight can decide -- it must fall "
            "through to the real connection, never skip the checks"
        )


def test_a_malformed_app_role_is_refused_before_any_connection_is_opened():
    """``DATABASE_APP_ROLE`` is interpolated into SQL text (``SET ROLE``
    takes no bind parameter), so ``core/db.py``'s existing
    ``_validate_app_role`` gate applies here too -- and it has to run BEFORE
    the connection attempt, which this test proves by using a DSN that could
    never connect: a probe that connected first would return quietly instead
    of raising."""
    engine = build_engine(_UNREACHABLE_DSN)
    try:
        with pytest.raises(ValueError, match="not a valid Postgres role identifier"):
            assert_boot_privileges(engine, _settings(database_app_role="poseidon app; DROP"))
    finally:
        engine.dispose()


# ===========================================================================
# pg fixtures
# ===========================================================================


@pytest.fixture
def pg_engine():
    """The compose ``DATABASE_URL`` engine, skipped (never failed) when no
    usable Postgres is configured -- the same skip discipline
    ``test_memory_worker.py``'s module-level probe uses, expressed as a
    fixture so this file's offline tests still run without one."""
    if not _DSN:
        pytest.skip(
            "DATABASE_URL is not set - start it with "
            "`docker compose -f infra/docker-compose.yml up -d db`"
        )
    engine = build_engine(_DSN)
    try:
        # The reachability/migration probe is the ONLY thing this except
        # covers -- deliberately not the `yield` below it, or an
        # OperationalError raised by a TEST would be silently converted into
        # a skip, which is how a red suite turns green without anyone
        # noticing.
        try:
            with engine.connect() as conn:
                role = conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": WORKER_ROLE}
                ).first()
        except OperationalError as exc:
            pytest.skip(f"Postgres at DATABASE_URL is not usable ({type(exc).__name__})")
        if role is None:
            pytest.skip(
                f"{WORKER_ROLE} does not exist - migrate this database with "
                "`python -m alembic upgrade head` (revision 0009)"
            )
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def dsn_role_is_privileged(pg_engine) -> bool:
    with pg_engine.connect() as conn:
        return bool(
            conn.execute(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).scalar()
        )


@pytest.fixture
def nonprivileged_dsn(pg_engine):
    """A DSN authenticating as a throwaway ``LOGIN`` role with neither
    ``rolsuper`` nor ``rolbypassrls``: the RDS habitat, rehearsed against
    compose (see this module's docstring).

    Dropped again on the way out, including when the test fails. Its name
    carries a random suffix so two concurrent pg runs against the same
    cluster cannot drop each other's role (see :data:`_PROBE_ROLE_PREFIX`).

    **Skips, never fails, on any habitat difference.** BOTH server-dependent
    steps are covered: creating the role at all (a ``DATABASE_URL`` role
    without ``CREATEROLE`` -- a perfectly reasonable way to run this suite --
    would otherwise hard-error where the docstring promises a skip), and
    logging in as it afterwards (password auth over TCP is a property of the
    server's ``pg_hba.conf``, not of this codebase). Anything else still
    fails loudly.
    """
    role = f"{_PROBE_ROLE_PREFIX}_{uuid.uuid4().hex[:8]}"
    try:
        with pg_engine.begin() as conn:
            conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD '{_PROBE_PASSWORD}'"))
    except DBAPIError as exc:
        pytest.skip(
            f"cannot create {role} ({type(exc).__name__}) - this DATABASE_URL role has no "
            "CREATEROLE, so the non-privileged-DSN rehearsal cannot run here"
        )
    dsn = make_url(_DSN).set(username=role, password=_PROBE_PASSWORD)
    dsn_text = dsn.render_as_string(hide_password=False)
    try:
        probe = build_engine(dsn_text)
        try:
            with probe.connect():
                pass
        except OperationalError as exc:
            pytest.skip(
                f"{role} cannot log in ({type(exc).__name__}) - this server's "
                "pg_hba.conf does not accept password auth for a new role"
            )
        finally:
            probe.dispose()
        yield dsn_text, role
    finally:
        with pg_engine.begin() as conn:
            conn.execute(text(f"DROP ROLE IF EXISTS {role}"))


# ===========================================================================
# pg: the three checks
# ===========================================================================


@pytest.mark.pg
def test_the_probe_passes_on_this_database_with_the_default_app_role(pg_engine):
    settings = _settings(database_app_role=_APP_ROLE)

    assert assert_boot_privileges(pg_engine, settings) is None
    assert assert_boot_privileges(pg_engine, settings, require_worker_role=True) is None


@pytest.mark.pg
def test_a_privileged_dsn_with_no_app_role_is_refused_by_name(pg_engine, dsn_role_is_privileged):
    """Check (a), P10 I-5's exact scenario: this DSN bypasses row-level
    security and nothing is configured to claw it back. The message has to
    name the variable an operator would set, or the refusal is just a
    crash."""
    if not dsn_role_is_privileged:
        pytest.skip("this DATABASE_URL role is not privileged - check (a) cannot trigger")

    with pytest.raises(RuntimeError, match="DATABASE_APP_ROLE") as excinfo:
        assert_boot_privileges(pg_engine, _settings(database_app_role=None))

    assert "row-level security" in str(excinfo.value).lower()
    assert _APP_ROLE in str(excinfo.value), "the message must name the role that fixes it"


@pytest.mark.pg
def test_an_app_role_that_does_not_exist_is_refused_by_name(pg_engine):
    """Check (b): ``SET LOCAL ROLE`` would fail on the FIRST request
    instead -- a 500 per request, in production, from a typo. At boot is
    the point."""
    with pytest.raises(RuntimeError, match="poseidon_nonexistent_role") as excinfo:
        assert_boot_privileges(
            pg_engine, _settings(database_app_role="poseidon_nonexistent_role")
        )

    assert "DATABASE_APP_ROLE" in str(excinfo.value)
    assert "alembic" in str(excinfo.value), "the message must name the fix"


@pytest.mark.pg
def test_the_preflight_does_not_false_negative_against_a_real_database(pg_engine):
    """The other side of the pre-flight's boundary: a database that IS there
    must pass it, or the checks below would be skipped on every real boot
    and this whole module would go quietly inert. 250ms is >100x a
    loopback/VPC handshake, and this asserts that headroom is real on the
    habitat the suite actually runs against rather than assumed."""
    assert _tcp_reachable(make_url(_DSN)) is True


@pytest.mark.pg
def test_a_refusal_is_rendered_on_every_call_never_only_the_first(pg_engine):
    """**The anti-cache regression guard.** Nothing this function concludes
    may ever be remembered across calls.

    An earlier draft of this task memoized URLs it had failed to reach, to
    spare the offline suite 27 pointless connection timeouts. Review killed
    it, correctly: caching "no verdict was possible" is observationally
    caching "this did not refuse", and a process that probes while the
    database is DOWN and probes again once it is UP-BUT-MISCONFIGURED would
    have had its refusal silently swallowed by the stale entry -- the exact
    class of silent misconfiguration this function exists to end.

    A timing assertion cannot catch that (and would be vacuous on any host
    whose refused connections are instant). This asserts the property
    itself: the same probe, same process, same URL, refuses EVERY time.
    Any future short-circuit that returns early on a remembered outcome
    fails here on the second call.
    """
    settings = _settings(database_app_role="poseidon_nonexistent_role")

    for _attempt in range(3):
        # pytest.raises IS the assertion: a call that returned instead of
        # refusing fails the iteration it happened on.
        with pytest.raises(RuntimeError, match="poseidon_nonexistent_role"):
            assert_boot_privileges(pg_engine, settings)


@pytest.mark.pg
def test_a_missing_worker_role_is_refused_by_name(pg_engine, monkeypatch):
    """Check (c), first half. The absent role is simulated by pointing the
    probe at a role name that cannot exist rather than by dropping the real
    ``poseidon_worker`` -- a deliberate, disclosed implementer's call: this
    suite runs against a shared long-lived dev database, and a crashed run
    mid-``DROP ROLE`` would leave every other pg suite (and the running
    compose worker) broken. ``test_migrations.py``'s own pg round-trip
    exercises the genuine create/drop path instead."""
    monkeypatch.setattr("poseidon.core.db.WORKER_ROLE", "poseidon_worker_absent")

    with pytest.raises(RuntimeError, match="poseidon_worker_absent") as excinfo:
        assert_boot_privileges(
            pg_engine, _settings(database_app_role=_APP_ROLE), require_worker_role=True
        )

    assert "alembic" in str(excinfo.value), "the message must name the fix"
    assert "0009" in str(excinfo.value), "the message must name the migration that creates it"


# ===========================================================================
# pg: the RDS rehearsal (a non-privileged DSN, on purpose)
# ===========================================================================


@pytest.mark.pg
def test_a_nonprivileged_dsn_with_no_app_role_passes(pg_engine, nonprivileged_dsn):
    """The RDS shape: a DSN that does NOT bypass RLS needs no
    ``DATABASE_APP_ROLE`` at all (``Settings``'s own comment: an operator
    running a non-privileged DSN sets it empty). Check (a) must not fire
    here, or the probe would refuse to boot the very deployment it exists
    to protect."""
    dsn, _role = nonprivileged_dsn
    engine = build_engine(dsn)
    try:
        assert assert_boot_privileges(engine, _settings(database_app_role=None)) is None
    finally:
        engine.dispose()


@pytest.mark.pg
def test_a_dsn_that_is_not_a_member_of_the_worker_role_is_refused_then_passes_after_the_grant(
    pg_engine, nonprivileged_dsn
):
    """Check (c), second half -- and the empirical proof of WHY migration
    0009 ends with ``GRANT poseidon_worker TO CURRENT_USER``.

    A non-superuser may only ``SET ROLE`` to a role it is a member of. The
    freshly created probe role is not one, so the rehearsal transaction
    fails and the probe refuses, naming the ``GRANT``. Granting membership
    -- exactly what the migration does for the DSN user that ran it -- and
    nothing else makes the same probe pass. On compose this is the only
    reachable way to observe that failure at all: the ordinary
    ``DATABASE_URL`` role is a superuser, and a superuser can ``SET ROLE``
    to anything.
    """
    dsn, role = nonprivileged_dsn
    engine = build_engine(dsn)
    settings = _settings(database_app_role=None)
    try:
        with pytest.raises(RuntimeError, match=WORKER_ROLE) as excinfo:
            assert_boot_privileges(engine, settings, require_worker_role=True)
        assert "GRANT" in str(excinfo.value), "the message must name the fix"

        with pg_engine.begin() as conn:
            conn.execute(text(f"GRANT {WORKER_ROLE} TO {role}"))

        assert assert_boot_privileges(engine, settings, require_worker_role=True) is None
    finally:
        engine.dispose()


@pytest.mark.pg
def test_the_probe_leaves_no_role_switch_behind_on_the_connection_it_used(pg_engine):
    """The rehearsal's ``SET ROLE`` must not be observable afterwards on the
    engine the application actually uses -- a leaked ``poseidon_worker``
    would hand the next unrelated checkout a role with almost no privileges.

    Two independent things make that true today (the probe borrows nothing
    from this pool, and its own transaction is rolled back), and this test
    is deliberately blind to which: it asserts the property, so it still
    catches a future change that goes back to probing on the caller's own
    pooled connection and forgets the rollback."""
    assert_boot_privileges(
        pg_engine, _settings(database_app_role=_APP_ROLE), require_worker_role=True
    )

    with pg_engine.connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar() != WORKER_ROLE
