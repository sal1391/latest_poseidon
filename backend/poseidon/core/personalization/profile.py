"""Phase 13 Task 1 (doc 05 section 5): the Postgres-backed store behind
migration 0008's ``user_profile`` table -- one row per user holding their
personal system instruction, injected into every router/synthesis prompt
(Task 2 wires the injection; this task only builds the store).

**Shape mirrors ``core/chat/history.py``'s ``HistoryStore``/``UserHistory``
on purpose.** ``ProfileStore(engine, app_role).for_user(sub) -> UserProfile``
is the identical construction discipline: cheap to construct, no connection
opened until a :class:`UserProfile` method actually runs one, ``app_role``
threaded straight through to :func:`~poseidon.core.db.rls_transaction`
exactly like every other caller of that wrapper in this codebase.

**No admin escape hatch.** Migration 0008 gives ``user_profile`` no
``poseidon_admin`` policy at all (doc 05 section 7: "Admins have no path to
another user's ... `user_profile`") -- this module has nothing to gate on
that account; RLS's owner policy is the only path to any row, for anyone.

**``get()`` never 404s, unlike almost every read in this codebase.** A user
who has never called :meth:`UserProfile.put` has no row in ``user_profile``
at all, but the settings surface (doc 01 section 9) always has SOMETHING to
show -- an empty instruction is a valid, common state, not an error. So
``get()`` returns the same default shape (``{"system_instruction": "",
"updated_at": None}``) for "never written" that a real row with an empty
string would produce, rather than ``None`` or a raise -- there is no
"not found" outcome for this method to have at all.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.db import rls_transaction

_GET_SQL = text(
    "SELECT system_instruction, updated_at FROM user_profile WHERE user_sub = :user_sub"
)

# ON CONFLICT DO UPDATE -- "a row is upserted, never removed by the app"
# (migration 0008's own docstring: no DELETE grant on this table).
# RETURNING avoids a second round trip / second transaction for the
# caller's own put()-then-return-the-new-shape contract.
_UPSERT_SQL = text(
    "INSERT INTO user_profile (user_sub, system_instruction, updated_at) "
    "VALUES (:user_sub, :system_instruction, now()) "
    "ON CONFLICT (user_sub) DO UPDATE "
    "SET system_instruction = EXCLUDED.system_instruction, updated_at = now() "
    "RETURNING system_instruction, updated_at"
)


def _default_shape() -> dict:
    """The shape :meth:`UserProfile.get` returns for a user with no row yet
    -- see the module docstring for why this is never an error/``None``."""
    return {"system_instruction": "", "updated_at": None}


class ProfileStore:
    """The per-process entry point: holds the ``Engine`` (and the resolved
    ``app_role``) and hands out per-user facades. Construction touches
    nothing -- no connection is opened until a :class:`UserProfile` method
    actually runs one (mirrors ``core/chat/history.py``'s own
    ``HistoryStore``)."""

    def __init__(self, engine: Engine, app_role: str | None = None) -> None:
        self._engine = engine
        self._app_role = app_role

    def for_user(self, user_sub: str) -> "UserProfile":
        """A facade scoped to ``user_sub`` -- every statement it runs goes
        through :func:`~poseidon.core.db.rls_transaction` with THIS
        identity, so nothing it does can ever touch another user's row."""
        return UserProfile(self._engine, user_sub, self._app_role)


class UserProfile:
    """One user's own system-instruction row. Both methods open exactly
    one :func:`~poseidon.core.db.rls_transaction` (mirrors ``core/chat/
    history.py``'s own ``UserHistory``)."""

    def __init__(self, engine: Engine, user_sub: str, app_role: str | None = None) -> None:
        self._engine = engine
        self._user_sub = user_sub
        self._app_role = app_role

    def _transaction(self):
        return rls_transaction(self._engine, self._user_sub, app_role=self._app_role)

    def get(self) -> dict:
        """``{"system_instruction": str, "updated_at": str | None}`` -- the
        default shape (module docstring) for a user with no row yet, the
        real row's shape otherwise. Never raises for an absent row: RLS
        already confines this query to (at most) this user's own row, so a
        zero-row result here means exactly one thing -- ``put`` was never
        called -- not "another user's row was hidden," the ambiguity
        ``core/chat/history.py``'s reads have to account for."""
        with self._transaction() as conn:
            row = conn.execute(_GET_SQL, {"user_sub": self._user_sub}).first()
        if row is None:
            return _default_shape()
        return {
            "system_instruction": row.system_instruction,
            "updated_at": row.updated_at.isoformat() if row.updated_at is not None else None,
        }

    def put(self, system_instruction: str) -> dict:
        """Upsert this user's instruction, returning the updated shape.
        Idempotent-amend, not create-or-fail: a second call for the same
        user AMENDS the one row the ``user_sub`` primary key structurally
        enforces can only ever be one -- there is no second row to create."""
        with self._transaction() as conn:
            row = conn.execute(
                _UPSERT_SQL,
                {"user_sub": self._user_sub, "system_instruction": system_instruction},
            ).first()
        return {
            "system_instruction": row.system_instruction,
            "updated_at": row.updated_at.isoformat(),
        }


__all__ = ["ProfileStore", "UserProfile"]
