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

**The instruction is capped, at exactly one point: :meth:`UserProfile.put`
(final whole-phase review, finding I-2).** The instruction is injected into
every router and synthesis prompt for the rest of that user's life (Task
2's wiring), so an unbounded one is not merely a large row -- it
persistently breaks that user's every subsequent turn, with no way back
except another ``put``. Mirrors ``core/personalization/memory.py``'s own
"size cap enforcement lives at exactly ONE point" discipline verbatim,
including its typed :class:`ValueError` subclass
(:class:`InstructionTooLarge`, the twin of that module's
``MemoryTooLarge``, mapped to the same RFC-7807 422 by ``api/me.py``) and
its "raises BEFORE any row is written" property -- a rejected instruction
leaves the previous one exactly as it was. Enforced HERE rather than as a
``max_length`` on ``api/me.py``'s request model for the reason that module
gives for the memory cap: the store is the one path every writer has to go
through, so a second caller cannot bypass it.

:data:`INSTRUCTION_MAX_CHARS` is 8000 -- the same number
``settings.memory_max_chars`` happens to default to, deliberately as a
SEPARATE constant rather than a read of that setting: the two cap
different documents (one machine-distilled and pruned, one hand-typed) and
an operator retuning the memory budget must not silently retune the
instruction's. 8000 characters is roughly 2000 tokens, which is a generous
ceiling for a hand-written standing instruction (doc 01 section 9's own
examples are one or two sentences) while leaving the four-section prompt
``assemble_system`` builds comfortably within budget even alongside a
full-size memory document. A plain module constant, not a new ``Settings``
field, for two reasons: this phase's sanctioned ``core/config.py`` scope is
exactly the two fields it already added, and unlike ``memory_max_chars``
(which a retention/prompt-budget tuning story genuinely wants per
environment) there is no environment-specific reason for one deployment's
typed instruction ceiling to differ from another's.
"""

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.db import rls_transaction

#: The instruction's own size cap, in characters -- see the module
#: docstring's "The instruction is capped" section for why this is a plain
#: module constant rather than a ``Settings`` field, and why 8000.
INSTRUCTION_MAX_CHARS = 8000


class InstructionTooLarge(ValueError):
    """Raised by :meth:`UserProfile.put` when the candidate instruction
    exceeds :data:`INSTRUCTION_MAX_CHARS` -- checked, and raised, BEFORE the
    upsert runs; never a silent truncation. A :class:`ValueError` subclass,
    mirroring ``core/personalization/memory.py``'s own
    :class:`~poseidon.core.personalization.memory.MemoryTooLarge` verbatim
    (which itself mirrors ``core/chat/history.py``'s ``MalformedCursor``
    precedent), so any pre-existing broad ``except ValueError`` a caller
    might already have keeps working unchanged."""


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
        enforces can only ever be one -- there is no second row to create.

        Raises :class:`InstructionTooLarge` when ``system_instruction``
        exceeds :data:`INSTRUCTION_MAX_CHARS` (module docstring). Checked
        entirely in Python, before any SQL is built or any connection
        opened, so "a rejected instruction leaves the previous one exactly
        as it was" is structural rather than merely tested -- the same
        shape ``UserMemory.write_version``'s own size check has. An empty
        string is still always accepted (the column's own ``default ''``);
        only the upper bound is enforced."""
        if len(system_instruction) > INSTRUCTION_MAX_CHARS:
            raise InstructionTooLarge(
                f"system instruction ({len(system_instruction)} chars) exceeds "
                f"the {INSTRUCTION_MAX_CHARS}-character limit"
            )
        with self._transaction() as conn:
            row = conn.execute(
                _UPSERT_SQL,
                {"user_sub": self._user_sub, "system_instruction": system_instruction},
            ).first()
        return {
            "system_instruction": row.system_instruction,
            "updated_at": row.updated_at.isoformat(),
        }


__all__ = ["INSTRUCTION_MAX_CHARS", "InstructionTooLarge", "ProfileStore", "UserProfile"]
