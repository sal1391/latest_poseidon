"""The one seam that builds a SQLAlchemy ``Engine`` (:func:`build_engine`)
and the one seam that opens an identity-scoped transaction against it
(:func:`rls_transaction`) -- doc 05 section 4, decision D28.

**Decision D28 (identity context is transaction-scoped, not session-
scoped).** Every table row-level security protects (migration 0004's
``conversations``/``messages``, and every later phase's addition to that
set) reads the caller's identity from a Postgres GUC, ``app.user_sub``, via

    USING (user_sub = current_setting('app.user_sub', true))

never from a query parameter a caller could forget to add (doc 05 section
4's opening line: chat data is isolated "in the database, not by
remembering to add WHERE clauses"). :func:`rls_transaction` is the ONLY
place that GUC is ever set, and it sets it as the FIRST statement of the
transaction it opens:

    SELECT set_config('app.user_sub', :sub, true)

Two details here are load-bearing, not incidental:

1. **The trailing ``true`` (``is_local``).** ``set_config``'s third
   argument scopes the setting to the CURRENT TRANSACTION -- it is unset
   the instant that transaction commits or rolls back. A connection pool
   hands physical connections back and forth between unrelated requests;
   without ``is_local=true`` a value set for user A would still be
   readable by whichever request happens to check the same connection
   back out next, for however long THAT session lives. ``is_local=true``
   makes a pooled connection just as safe to reuse as a fresh one --
   ``test_rls_policies.py``'s pooled-connection-context-leak test fails
   immediately the moment this ever regresses to session-scoped.
2. **``set_config``, never ``SET LOCAL``.** ``SET LOCAL app.user_sub =
   :sub`` would be the more obvious spelling of the same idea, but ``SET``
   (in any form) is SQL syntax, not a function call -- it accepts no bind
   parameter, only a literal token. Carrying a real identity value would
   then mean building it into the SQL text by hand (an f-string, string
   concatenation, whatever), which is exactly the shape of bug a bind
   parameter exists to make impossible. ``set_config(...)`` is an ordinary
   function call, so ``:sub`` is a normal bound parameter like any other --
   the identity value never touches SQL-text construction at all.

**Why an unset context fails closed (the policy's own responsibility, not
this module's, but the reason this module never has to special-case "no
identity yet").** ``current_setting('app.user_sub', true)`` -- the
``missing_ok`` form every RLS policy in this codebase uses -- returns SQL
``NULL`` instead of raising when the GUC was never set (a connection used
outside :func:`rls_transaction` entirely, or before this module's first
statement runs). ``user_sub = NULL`` is never TRUE under SQL's three-valued
logic, so the policy filters the query down to zero rows rather than
raising an exception a caller could catch and route around: an absent
identity fails closed, silently, exactly the way it should.

**Round-0 correction -- runtime enforcement on privileged DSNs
(``app_role``).** Task 1's own RLS test suite discovered that this
project's local compose Postgres bootstraps its ``DATABASE_URL`` role
(``poseidon``) as the cluster's SUPERUSER -- an artifact of the official
Postgres Docker image's ``POSTGRES_USER`` convention. Postgres superusers
(and any role with the ``BYPASSRLS`` attribute) unconditionally bypass
row-level security: this is a hard invariant with **no** schema-level
override -- not ``FORCE ROW LEVEL SECURITY``, not a policy, nothing. That
was originally worked around only inside the test suite; the plan
amendment this correction implements (``docs/superpowers/plans/2026-07-31-
phase-10-history-rls.md``) moves the fix into THIS wrapper instead, because
doc 05's store (Task 2) is deliberately WHERE-clause-free -- isolation
lives entirely in the database -- so its own isolation tests could never
pass while only the test suite, and not the runtime connection itself,
carried the fix.

``app_role`` (``None`` by default, callers pass ``Settings.
database_app_role``) names a role -- migration 0004's ``poseidon_app`` by
convention -- that :func:`rls_transaction` additionally ``SET LOCAL ROLE``s
to, immediately AFTER the ``set_config`` call, transaction-scoped exactly
like the identity GUC itself (reverts on commit or rollback, so a pooled
connection can never leak a role switch into the next checkout any more
than it can leak ``app.user_sub``). ``None`` disables this entirely -- a
real deploy whose ``DATABASE_URL`` already authenticates as an ordinary,
non-privileged role (doc 05 section 4's expected shape) needs no role
switch, and a role switch to a role the connection has no membership in
would itself raise a permissions error on every single request.

``SET LOCAL ROLE`` is SQL syntax, not a function call, exactly like ``SET
LOCAL`` itself (see point 2 above) -- it accepts no bind parameter, so the
role name has no choice but to be interpolated into SQL text. Unlike
``app_role``'s value ever being untrusted user input, this value only ever
comes from operator-controlled configuration (``Settings.
database_app_role``) -- but "only ever comes from config today" is not a
reason to skip validating it: :func:`_validate_app_role` enforces a strict
``[a-z_][a-z0-9_]{0,62}`` identifier shape (matching how Postgres itself
requires an unquoted identifier to look, and its 63-character
``NAMEDATALEN`` limit) and raises ``ValueError`` on any mismatch, BEFORE
``engine.begin()`` is ever called -- a malformed configured value fails
loudly at the first transaction attempt, never silently building a
malformed (or, worse, maliciously shaped) ``SET LOCAL ROLE`` statement.
The role name is additionally double-quoted as a Postgres identifier
(``SET LOCAL ROLE "poseidon_app"``) even though the pattern above already
rules out anything a raw identifier could not already say -- defense in
depth costs nothing here.

**Phase 14 Task 3 -- the boot privilege probe
(:func:`assert_boot_privileges`).** Everything above describes privileges
this module DEPENDS on and, until this task, never checked: whether the
configured ``app_role`` exists at all was discovered by the first real
request (a 500), and whether the DSN was privileged in the first place was
never discovered at all. Both of those are silent in exactly the way that
matters -- a privileged DSN with no ``app_role`` serves every user's rows
to every user and looks perfectly healthy doing it. :func:`assert_boot_
privileges` moves that class of question to process start-up, where a
wrong answer is a refusal to boot with a message naming the environment
variable and the fix. See that function's own docstring for the three
checks and why the worker's is opt-in.

**Phase 14 Task 3b -- the app role must also be ASSUMABLE, not merely
present.** Task 3's check (b) asked whether the configured ``app_role``
exists, which is a strictly weaker question than the one ``SET LOCAL
ROLE`` actually asks: a role that is not a superuser may only assume a
role it is a MEMBER of, and migration 0004 created ``poseidon_app``
without ever granting that membership to anybody (migration 0010 is the
grant; 0009 had carried the same statement for the worker's role since
Task 3). Every DSN in this project's local habitat is a superuser and may
assume any role in the cluster, so a probe that only checks existence
passes here and would still have let an RDS deployment fail at the second
statement of every single transaction. Check (b) therefore now REHEARSES
the switch -- the real statement, in a transaction that is thrown away --
exactly the way check (c) already rehearsed the worker's.
"""

import re
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, Engine
from sqlalchemy.exc import ArgumentError, DBAPIError
from sqlalchemy.pool import NullPool

from poseidon.core.obs import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from poseidon.core.config import Settings

logger = get_logger("db")

_SET_IDENTITY_SQL = text("SELECT set_config('app.user_sub', :sub, true)")

# Postgres identifier shape, unquoted: a letter or underscore, then up to
# 62 more letters/digits/underscores (63 total, Postgres's own NAMEDATALEN-1
# cap). Deliberately stricter than what a QUOTED identifier could actually
# hold (Postgres allows nearly anything between double quotes) -- this
# value is interpolated into SQL text with no bind-parameter alternative
# (see the module docstring), so the validation is intentionally paranoid
# rather than merely "wide enough for legitimate role names".
_APP_ROLE_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")

#: The role migration 0009 creates for the memory worker's cross-user claim
#: query, and the ONE place its name is spelled in application code
#: (``scripts/memory_worker.py`` imports it from here rather than declaring
#: its own copy, so the claim and the probe that rehearses the claim can
#: never disagree about which role they mean). Migration 0009 necessarily
#: repeats the literal -- a migration must not import application code --
#: and the pg suites fail if the two ever drift.
WORKER_ROLE = "poseidon_worker"

#: ``true`` when the connected role bypasses row-level security -- either
#: attribute does it, and this codebase has been bitten by the first one
#: (``rolsuper``, the official Postgres image's ``POSTGRES_USER``
#: convention) already. Deliberately asks about ``current_user`` rather
#: than about a name from configuration: what matters is what THIS
#: connection actually authenticated as.
_PRIVILEGE_SQL = text(
    "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
)

_ROLE_EXISTS_SQL = text("SELECT 1 FROM pg_roles WHERE rolname = :role")

#: What libpq itself falls back to when a DSN names no port, used so the
#: pre-flight below asks about the same port the real connection will.
_DEFAULT_POSTGRES_PORT = 5432

#: How long :func:`assert_boot_privileges` waits for its one connection
#: before deciding it cannot render a verdict. Mirrors ``api/health.py``'s
#: ``/ready`` probe verbatim (``connect_args={"connect_timeout": 2}``), and
#: for the same reason: an unreachable database must cost a bounded moment,
#: not an unbounded one. Measured, not assumed -- against this project's own
#: unreachable placeholder DSN (``127.0.0.1:1``, what every offline test's
#: live-mode app is built with) libpq's DEFAULT of "wait indefinitely" took
#: 130 seconds to give up, per boot. 2 is also libpq's own floor: it
#: silently rounds anything smaller up to 2.
_PROBE_CONNECT_TIMEOUT_SECONDS = 2

#: How long :func:`_tcp_reachable` waits for a bare TCP handshake before
#: reporting the database unreachable, ending the probe with no verdict.
#:
#: **Why a pre-flight exists at all.** Even bounded at libpq's 2-second
#: floor above, a boot check that cannot possibly render a verdict still
#: cost the offline suite 27 x 2s -- it builds that many ``chat_mode=
#: "live"`` apps against a deliberately unreachable placeholder DSN -- which
#: doubled its wall-clock time. A TCP handshake answers the only question
#: worth asking first ("is anything listening?") for a fraction of that.
#:
#: **Why 0.25s.** A TCP handshake is one round trip: ~0.1ms on loopback,
#: ~1-2ms between an EC2 instance and an RDS endpoint in the same VPC.
#: 250ms is more than a hundred times that headroom, while still being
#: two orders of magnitude cheaper than the libpq floor when nothing
#: answers. It bounds a handshake, never authentication, TLS or query time
#: -- all of which happen afterwards, on the real connection, unbounded by
#: this value.
_PREFLIGHT_TIMEOUT_SECONDS = 0.25

#: **There is deliberately no cache here, and there must not be one.**
#:
#: An earlier draft of this task remembered URLs it had failed to reach, so
#: that the offline suite's 27 ``chat_mode="live"`` apps would not each
#: spend :data:`_PROBE_CONNECT_TIMEOUT_SECONDS` re-learning that the
#: placeholder DSN is unreachable (it doubled that suite's runtime). It was
#: removed on review, and the reasoning is worth keeping so nobody
#: re-invents it: caching "no verdict was possible" is observationally
#: identical to caching "this did not refuse". A database that is DOWN at
#: the first probe and UP-BUT-MISCONFIGURED at a later one -- a real
#: sequence for any process that outlives a database restart -- would then
#: have its refusal silently suppressed by a stale entry, which is exactly
#: the class of silent misconfiguration this whole function exists to end.
#: A safety check that can be short-circuited by something it concluded
#: earlier is not a safety check.
#:
#: ``test_a_refusal_is_rendered_on_every_call_never_only_the_first`` fails
#: if any such cache comes back.


def _tcp_reachable(url: URL) -> bool:
    """``True`` when a TCP connection to ``url``'s host/port completes
    within :data:`_PREFLIGHT_TIMEOUT_SECONDS`, or when this URL names no
    host this function can meaningfully pre-flight.

    **Sound where a cache is not, and for a reason worth stating.** This is
    re-evaluated on EVERY call and keeps no state whatsoever between them,
    so unlike the memo that used to live here it can never suppress a
    refusal that a later call would have rendered: every boot on which TCP
    answers in time runs all three misconfiguration checks in full, no
    matter what any earlier boot found.

    Its worst case is a false negative -- a database that is reachable but
    slower than 250ms to complete a handshake -- and that degrades to
    exactly the no-verdict posture an unreachable database already takes,
    which this probe already accepts by design (see
    :func:`assert_boot_privileges`: it answers "are the privileges right",
    never "is the database up"; ``/ready`` owns the second question). So the
    pre-flight can cost a verdict on a boot where the network is already
    badly degraded, and can never cost one otherwise.

    Anything this cannot pre-flight defensively returns ``True`` and lets
    the real connection decide: a URL with no host at all (libpq will use a
    Unix socket or its own default), a Unix-socket directory in the host
    field, or libpq's comma-separated multi-host form, where "which host"
    has no single answer here and is the driver's business, not this
    function's.
    """
    host = url.host
    if not host or host.startswith("/") or host.startswith("@") or "," in host:
        return True
    try:
        with socket.create_connection(
            (host, url.port or _DEFAULT_POSTGRES_PORT), timeout=_PREFLIGHT_TIMEOUT_SECONDS
        ):
            return True
    except OSError:
        # Refused, timed out, or a name that does not resolve -- socket.gaierror
        # is an OSError too. All three mean the same thing here: nothing this
        # process can ask about privileges.
        return False


def _validate_app_role(name: str) -> None:
    """Raise ``ValueError`` unless ``name`` fully matches
    :data:`_APP_ROLE_PATTERN` -- see the module docstring's "round-0
    correction" section for why this exists and why it runs before any SQL
    is built or any connection opened."""
    if _APP_ROLE_PATTERN.fullmatch(name) is None:
        raise ValueError(
            f"app_role={name!r} is not a valid Postgres role identifier "
            "(expected [a-z_][a-z0-9_]{0,62}) -- refusing to interpolate it "
            "into SQL (SET ROLE accepts no bind parameter, unlike set_config)"
        )


def build_engine(database_url: str) -> Engine:
    """Thin ``create_engine`` wrapper -- the one place connection-pool
    policy lives for this app. No pool arguments are set explicitly today
    (SQLAlchemy's ``QueuePool`` defaults apply); the seam exists so that
    whenever a real deploy needs to tune one (``pool_size``,
    ``pool_pre_ping``, ...), there is exactly one call site to change --
    the same "the hook ships, no policy yet" shape doc 05 section 4 already
    uses for the ontology's row-scope hook (decision D16, YAGNI on policy,
    not on mechanism).

    **Phase 14 Task 4 -- a malformed value must name itself (P10 M3).** A
    ``DATABASE_URL`` that is not a parseable SQLAlchemy URL at all (an
    operator paste of a bare hostname, a missing ``://``, ...) makes
    ``create_engine`` raise ``sqlalchemy.exc.ArgumentError("Could not parse
    SQLAlchemy URL from given URL string")`` -- RED-probed directly against
    this exact call (``test_architecture_fitness.py``'s own probe, run
    before this wrap existed): the message names neither the environment
    variable at fault nor what a valid value looks like, so an operator's
    first signal is a generic parser complaint two layers removed from the
    ``.env`` line they just edited. Re-raised here with both, the same
    "name the variable and the fix" discipline :func:`assert_boot_
    privileges` above already applies to privilege misconfiguration.
    """
    try:
        return create_engine(database_url)
    except ArgumentError as exc:
        raise ArgumentError(
            f"DATABASE_URL is not a valid database connection string ({exc}). "
            "Expected a SQLAlchemy URL of the form "
            "postgresql+psycopg://<user>:<password>@<host>:<port>/<database>."
        ) from exc


@contextmanager
def rls_transaction(
    engine: Engine, user_sub: str, app_role: str | None = None
) -> Iterator[Connection]:
    """Open one transaction on ``engine`` with ``app.user_sub`` set to
    ``user_sub`` as its first statement, and yield the ``Connection``. See
    the module docstring (decision D28) for why this is a transaction-
    scoped ``set_config`` call and never ``SET LOCAL``.

    ``app_role`` (typically the caller's ``Settings.database_app_role``) is
    validated and, when not ``None``, applied via ``SET LOCAL ROLE`` as the
    SECOND statement -- immediately after ``set_config``, never before it,
    so ``set_config`` stays the first statement of the transaction exactly
    as D28 requires regardless of whether a role switch also happens. See
    the module docstring's "round-0 correction" section for the full
    rationale and the validation this performs before ever calling
    ``engine.begin()``.

    Every statement the caller runs through the yielded ``Connection`` --
    including the caller's own commit, implicit on clean exit from
    ``engine.begin()`` -- happens inside this one transaction, so the
    identity (and, when configured, the role) set here is visible to every
    row-level-security policy for the transaction's entire lifetime, and
    gone the moment it ends.
    """
    if app_role is not None:
        _validate_app_role(app_role)

    with engine.begin() as conn:
        conn.execute(_SET_IDENTITY_SQL, {"sub": user_sub})
        if app_role is not None:
            conn.execute(text(f'SET LOCAL ROLE "{app_role}"'))
        yield conn


def assert_boot_privileges(
    engine: Engine, settings: "Settings", *, require_worker_role: bool = False
) -> None:
    """Refuse to start a process whose database privileges are wired in a
    way that would fail SILENTLY at runtime. Returns ``None`` on success and
    raises ``RuntimeError`` -- naming the variable or role at fault and the
    fix -- otherwise.

    Called once per process, right after the ``Engine`` is built:
    ``api/app.py``'s live-chat wiring calls it with the default
    ``require_worker_role=False``, and ``scripts/memory_worker.py``'s
    ``main`` calls it with ``True`` before its first cycle.

    Three checks, on one connection:

    (a) **A privileged DSN with no ``DATABASE_APP_ROLE``.** A role with
        ``rolsuper`` or ``rolbypassrls`` bypasses row-level security
        unconditionally -- no schema-level override exists (see this
        module's "round-0 correction"), and ``app_role`` is this codebase's
        only answer to it. Configured that way, every RLS policy in the
        database is inert for this process and nothing about the running
        server looks wrong: every user's rows are served to every user. It
        refuses.
    (b) **A ``DATABASE_APP_ROLE`` this process cannot actually switch to.**
        Two halves, because ``SET LOCAL ROLE`` has two ways to fail and both
        of them fail on the FIRST request otherwise -- every request, in
        production, forever. The role must EXIST (a typo, or an un-migrated
        database; :func:`_validate_app_role` already rejects a malformed
        value at first use, and this moves the whole class of question to
        boot), and the switch to it must SUCCEED, rehearsed for real inside
        a transaction that is rolled back. The second half is not implied by
        the first: a role that is not a superuser may only assume a role it
        is a MEMBER of (migration 0010's ``GRANT poseidon_app TO
        CURRENT_USER``), and no superuser DSN -- which is every DSN in this
        project's local habitat -- can ever show you that gap, since a
        superuser may assume anything.
    (c) **``require_worker_role`` (the worker's flavor).** The same pair of
        questions as (b), asked about :data:`WORKER_ROLE`: it must exist AND
        ``SET LOCAL ROLE`` to it must actually succeed (migration 0009's
        ``GRANT poseidon_worker TO CURRENT_USER`` is what makes the second
        true). The consequence differs -- a worker that cannot claim
        distills nothing while logging healthy empty cycles forever, where
        an app role that cannot be assumed 500s loudly -- but the mechanism,
        the rehearsal and the fix are identical, which is why both go
        through :func:`_role_switch_failure`.

    **What this function deliberately does NOT do: decide whether the
    database is up.** A connection that cannot be opened within
    :data:`_PROBE_CONNECT_TIMEOUT_SECONDS` yields no privilege verdict, so
    it is logged and the probe returns. This is not a softening of the
    checks -- it is the boundary between them and ``/ready``, which is the
    surface that answers "is the database reachable". ``build_engine`` has
    always been lazy (``api/app.py``'s own docstring: "an unreachable-but-
    well-formed host still builds fine here and only fails later, per
    call"), and turning an unreachable database into a boot crash would be
    an unrelated behavior change smuggled in by a privilege check.

    The one connection is opened on a THROWAWAY engine built from
    ``engine.url``, not checked out of the caller's pool, purely so it can
    carry that timeout -- ``api/health.py``'s ``/ready`` probe already does
    the same thing for the same reason. Every question below is about the
    role this DSN authenticates as and about cluster-wide catalog state, so
    the answer is identical over any connection to the same URL; and the
    caller's own pool is left completely untouched, which is the strongest
    possible version of "the rehearsal's ``SET ROLE`` cannot leak into a
    later checkout".

    **This function remembers nothing between calls, on purpose** -- see the
    comment above :data:`_PROBE_CONNECT_TIMEOUT_SECONDS`'s neighbour for the
    cache that was tried and removed, and why a boot check that can skip a
    check on the strength of an earlier call is not one.

    What replaced that cache is a stateless TCP pre-flight
    (:func:`_tcp_reachable`, :data:`_PREFLIGHT_TIMEOUT_SECONDS`): before the
    connection is built at all, one bare handshake asks whether anything is
    listening, and a DSN that fails it takes the same no-verdict path an
    unopenable connection takes. It is sound by the same argument that
    condemned the cache, applied in reverse -- it holds nothing across
    calls, so it can never suppress a refusal a later boot would have
    rendered, and every boot whose TCP answers in time runs all three checks
    in full. Its only failure mode is a false negative on a database too
    slow to shake hands in 250ms, which lands on the no-verdict posture this
    function already takes for an unreachable database by design.

    Non-Postgres engines return immediately: ``pg_roles``, ``SET ROLE`` and
    row-level security are all Postgres-only, the same dialect guard every
    migration since 0002 opens with.
    """
    if engine.dialect.name != "postgresql":
        return

    app_role = settings.database_app_role
    if app_role is not None:
        # Before the connection, not after: a malformed value must never
        # reach SQL-text construction, and failing on it should not depend
        # on the database being reachable.
        _validate_app_role(app_role)

    url = str(engine.url)  # password-masked by SQLAlchemy's own __str__
    # Cheapest question first, and the only one that can be answered without
    # a database connection at all: is anything listening? A DSN that fails
    # this takes the SAME no-verdict path a failed connection takes below --
    # see _tcp_reachable for why re-asking it on every call (rather than
    # remembering the answer) is what keeps it from ever suppressing a
    # refusal.
    if not _tcp_reachable(engine.url):
        _log_no_verdict(
            url,
            "nothing accepted a TCP connection at this host and port within the "
            "pre-flight window",
        )
        return

    # A throwaway engine on the caller's own URL, not a checkout from the
    # caller's pool -- purely so this one connection can carry a bounded
    # connect timeout (see _PROBE_CONNECT_TIMEOUT_SECONDS; the app's pool
    # deliberately sets none, and a boot check must not be able to hang a
    # boot). ``api/health.py``'s /ready probe already does exactly this,
    # for the same reason. Every question asked below is about the ROLE the
    # DSN authenticates as and about cluster-wide catalog state, so the
    # answers are identical over any connection to the same URL.
    # ``NullPool``: one connection, used once, closed -- nothing to leave
    # pooled behind after ``dispose()``.
    probe_engine = create_engine(
        engine.url,
        connect_args={"connect_timeout": _PROBE_CONNECT_TIMEOUT_SECONDS},
        poolclass=NullPool,
    )
    try:
        _run_checks(probe_engine, url, app_role, require_worker_role)
    finally:
        probe_engine.dispose()


def _log_no_verdict(url: str, reason: str, *, sqlstate: str | None = None) -> None:
    """The one place a "this boot rendered no privilege verdict" line is
    emitted, so the pre-flight's version and the failed-connection version
    are the same event with the same keys rather than two shapes an operator
    has to learn separately. ``url`` is always the password-masked form."""
    logger.warning("boot_privileges_unchecked", reason=reason, sqlstate=sqlstate, url=url)


def _run_checks(
    probe_engine: Engine, url: str, app_role: str | None, require_worker_role: bool
) -> None:
    """The body of :func:`assert_boot_privileges`, split out only so that
    function's ``finally: probe_engine.dispose()`` reads as one line rather
    than wrapping every check in another indent level. ``url`` is only ever
    logged, and is SQLAlchemy's own password-masked rendering."""
    try:
        connection = probe_engine.connect()
    except DBAPIError as exc:
        # An SQLSTATE means the SERVER answered and rejected us -- wrong
        # password, no such database, not allowed by pg_hba -- which is a
        # very different operational problem from "nothing answered at all",
        # and logging both as "unreachable" sends an operator hunting for a
        # network fault that is not there. psycopg only carries `sqlstate`
        # on errors the server itself produced, so its presence is exactly
        # the distinction. Either way no privilege verdict is possible, so
        # either way this returns rather than refusing: /ready is the
        # surface that reports a database this process cannot use.
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        _log_no_verdict(
            url,
            (
                f"the server rejected this connection ({type(exc).__name__}: "
                "authentication, database or pg_hba), so no privilege check could run"
                if sqlstate
                else f"no connection to the database could be opened ({type(exc).__name__})"
            ),
            sqlstate=sqlstate,
        )
        return

    with connection as conn:
        if bool(conn.execute(_PRIVILEGE_SQL).scalar()) and app_role is None:
            raise RuntimeError(
                "DATABASE_APP_ROLE is unset, but this DATABASE_URL authenticates as a role "
                "with rolsuper or rolbypassrls: Postgres bypasses row-level security "
                "unconditionally for such a role, so every RLS policy in this database is "
                "silently inert for this process and every user's rows are readable by "
                "every user. Fix: set DATABASE_APP_ROLE=poseidon_app (the role migration "
                "0004 creates), or point DATABASE_URL at a role with neither attribute."
            )
        if app_role is not None:
            _assert_app_role(conn, app_role)
        if require_worker_role:
            _assert_worker_role(conn)


def _missing(conn: Connection, role: str) -> bool:
    """``True`` when ``role`` is not a role in this cluster. ``pg_roles`` is
    world-readable (unlike ``pg_authid``), so this answers the question for
    an unprivileged connection too -- which is the whole point on RDS."""
    return conn.execute(_ROLE_EXISTS_SQL, {"role": role}).first() is None


def _role_switch_failure(conn: Connection, role: str) -> DBAPIError | None:
    """Rehearse ``SET LOCAL ROLE <role>`` -- the real statement this process
    would run per request (:func:`rls_transaction`) or per cycle (the
    worker's claim) -- and return the error it raised, or ``None`` when it
    succeeded.

    Returns the exception instead of raising a ``RuntimeError`` of its own
    so that each caller names ITS variable, role and migration in the
    refusal: checks (b) and (c) fail for one and the same Postgres reason
    (a role that is not a superuser may only assume a role it is a MEMBER
    of) but have entirely different fixes and entirely different
    consequences, and a shared message would have to be vague about both.

    **The transaction is rolled back on every path, success included.**
    SQLAlchemy auto-begins one on a connection's first ``execute``, so this
    rollback ends the probe's own transaction and -- exactly like
    :func:`rls_transaction`'s ``SET LOCAL`` -- guarantees no role switch
    outlives the rehearsal. On the failure path it does a second job: a
    statement that raised leaves its transaction ABORTED, and every
    subsequent statement on it would fail with "current transaction is
    aborted" -- so without this, one rehearsal's failure would corrupt the
    verdict of whatever check ran next on the same connection.
    """
    try:
        conn.execute(text(f'SET LOCAL ROLE "{role}"'))
    except DBAPIError as exc:
        return exc
    else:
        return None
    finally:
        conn.rollback()


def _assert_app_role(conn: Connection, app_role: str) -> None:
    """Check (b) of :func:`assert_boot_privileges` -- kept separate only so
    that function stays readable, and shaped to mirror
    :func:`_assert_worker_role` below statement for statement, since the two
    roles fail identically and differ only in what they cost."""
    if _missing(conn, app_role):
        raise RuntimeError(
            f"DATABASE_APP_ROLE={app_role!r} names a role that does not exist in "
            "pg_roles on this database, so SET LOCAL ROLE would fail on the first "
            "request instead of here. Fix: run `python -m alembic upgrade head` "
            "against this database (migration 0004 creates poseidon_app), or set "
            "DATABASE_APP_ROLE empty if this DSN already authenticates as an "
            "ordinary, non-privileged role that needs no switch."
        )
    exc = _role_switch_failure(conn, app_role)
    if exc is not None:
        raise RuntimeError(
            f'SET LOCAL ROLE "{app_role}" failed for this DATABASE_URL '
            f"({type(exc).__name__}): DATABASE_APP_ROLE={app_role!r} names a role that "
            "exists but that this DSN's user may not assume, so every request's "
            "transaction would fail at its second statement -- a 500 per request, for "
            "every user, from the first one. Only a superuser may assume a role it is "
            "not a member of, and no RDS role is a superuser. Fix: run `python -m "
            "alembic upgrade head` against this database (migration 0010 issues GRANT "
            f"{app_role} TO CURRENT_USER), or, if this deployment's application user is "
            f"not the user that runs migrations, GRANT {app_role} TO <this DSN's user> "
            "directly."
        ) from exc


def _assert_worker_role(conn: Connection) -> None:
    """Check (c) of :func:`assert_boot_privileges` -- kept separate only so
    that function stays readable; it has exactly one caller."""
    if _missing(conn, WORKER_ROLE):
        raise RuntimeError(
            f"the memory worker requires the {WORKER_ROLE} role, which does not exist in "
            "pg_roles on this database. Its claim query reads every user's memory_outbox "
            "rows and that visibility comes from this role's policy, so without it the "
            "worker distills nothing while logging healthy empty cycles. Fix: run "
            "`python -m alembic upgrade head` against this database (migration 0009 "
            f"creates {WORKER_ROLE}, grants it on memory_outbox, and grants membership "
            "to the migration's own DSN user)."
        )
    # The rehearsal: the real statement the claim path runs, in a
    # transaction that is thrown away (see _role_switch_failure for why the
    # rollback is unconditional and what else it protects).
    exc = _role_switch_failure(conn, WORKER_ROLE)
    if exc is not None:
        raise RuntimeError(
            f'SET LOCAL ROLE "{WORKER_ROLE}" failed for this DATABASE_URL '
            f"({type(exc).__name__}), so the worker could not claim anything. Only a "
            "superuser may assume a role it is not a member of, and no RDS role is a "
            f"superuser. Fix: GRANT {WORKER_ROLE} TO <this DSN's user> -- migration 0009 "
            "does exactly that for the user that runs migrations, so a deployment whose "
            "worker user differs from its migration user must issue the GRANT itself."
        ) from exc


__all__ = ["WORKER_ROLE", "assert_boot_privileges", "build_engine", "rls_transaction"]
