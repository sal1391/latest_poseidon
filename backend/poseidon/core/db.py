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
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

_SET_IDENTITY_SQL = text("SELECT set_config('app.user_sub', :sub, true)")


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
def rls_transaction(engine: Engine, user_sub: str) -> Iterator[Connection]:
    """Open one transaction on ``engine`` with ``app.user_sub`` set to
    ``user_sub`` as its first statement, and yield the ``Connection``. See
    the module docstring (decision D28) for why this is a transaction-
    scoped ``set_config`` call and never ``SET LOCAL``.

    Every statement the caller runs through the yielded ``Connection`` --
    including the caller's own commit, implicit on clean exit from
    ``engine.begin()`` -- happens inside this one transaction, so the
    identity set here is visible to every row-level-security policy for
    the transaction's entire lifetime, and gone the moment it ends.
    """
    with engine.begin() as conn:
        conn.execute(_SET_IDENTITY_SQL, {"sub": user_sub})
        yield conn


__all__ = ["build_engine", "rls_transaction"]
