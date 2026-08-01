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
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

_SET_IDENTITY_SQL = text("SELECT set_config('app.user_sub', :sub, true)")

# Postgres identifier shape, unquoted: a letter or underscore, then up to
# 62 more letters/digits/underscores (63 total, Postgres's own NAMEDATALEN-1
# cap). Deliberately stricter than what a QUOTED identifier could actually
# hold (Postgres allows nearly anything between double quotes) -- this
# value is interpolated into SQL text with no bind-parameter alternative
# (see the module docstring), so the validation is intentionally paranoid
# rather than merely "wide enough for legitimate role names".
_APP_ROLE_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")


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
    not on mechanism)."""
    return create_engine(database_url)


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


__all__ = ["build_engine", "rls_transaction"]
