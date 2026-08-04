"""Phase 13 Task 1 (doc 05 section 5): the Postgres-backed store behind
migration 0008's ``memory_outbox`` table -- one row per conversation
tracking whether it needs distilling into a new memory version. Task 2
calls :meth:`ConversationOutbox.touch` from every turn-completion site
(best-effort, matching ``RunLogWriter``'s own "never raises the turn"
posture -- Task 2's own job, not this module's); a later task's worker
claims idle rows off this table.

**Shape mirrors ``core/chat/history.py``'s ``HistoryStore``/``UserHistory``
on purpose.** ``OutboxStore(engine, app_role).for_user(sub) ->
ConversationOutbox`` is the identical construction discipline: cheap to
construct, no connection opened until a :class:`ConversationOutbox` method
actually runs one, ``app_role`` threaded straight through to
:func:`~poseidon.core.db.rls_transaction` exactly like every other caller
of that wrapper in this codebase.

**No ``user_sub`` predicate needed in :meth:`~ConversationOutbox.touch`'s
own SQL, unlike ``user_memory``'s version-keyed queries in this same
phase.** ``memory_outbox``'s primary key is ``conversation_id``, a
globally-unique UUIDv7 minted once by ``HistoryStore.create_conversation``
-- no two users' rows can ever collide on it, the same property
``core/chat/history.py``'s own module docstring already relies on for
``conversations``/``messages``. RLS's ``WITH CHECK`` on the owner policy
is still what stops a caller from touching another user's existing row: an
``INSERT ... ON CONFLICT (conversation_id) DO UPDATE`` against a
conversation_id some OTHER user already owns fails closed (Postgres
requires the pre-existing conflicting row to be visible under the table's
UPDATE policy to even attempt the ``DO UPDATE`` branch; an invisible
conflicting row raises a duplicate-key error instead of silently upserting
across the ownership boundary) -- there is no admin escape hatch on this
table either (migration 0008's own docstring), so this is the one and only
path any caller has.

**No admin escape hatch.** Migration 0008 gives ``memory_outbox`` no
``poseidon_admin`` policy at all (doc 05 section 7's "no admin policy,
deliberately" extends to this table too, alongside ``user_profile``/
``user_memory``) -- this module has nothing to gate on that account.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.db import rls_transaction


def _parse_uuid(value: str) -> uuid.UUID | None:
    """``value`` as a :class:`uuid.UUID`, or ``None`` if it is not one --
    mirrors ``core/chat/history.py``'s own ``_parse_uuid`` exactly."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


# Full re-arm on every touch (interface docstring, doc 05 section 5): a new
# turn always means new content that MIGHT be worth distilling, so a prior
# done/failed status, and any attempts/last_error it carried, are always
# reset -- there is no "only re-arm if it was pending" special case.
_TOUCH_SQL = text(
    "INSERT INTO memory_outbox (conversation_id, user_sub, last_turn_at, status, attempts) "
    "VALUES (:conversation_id, :user_sub, now(), 'pending', 0) "
    "ON CONFLICT (conversation_id) DO UPDATE "
    "SET last_turn_at = now(), status = 'pending', attempts = 0, last_error = NULL"
)


class OutboxStore:
    """The per-process entry point: holds the ``Engine`` (and the resolved
    ``app_role``) and hands out per-user facades. Construction touches
    nothing -- no connection is opened until a :class:`ConversationOutbox`
    method actually runs one (mirrors ``core/chat/history.py``'s own
    ``HistoryStore``)."""

    def __init__(self, engine: Engine, app_role: str | None = None) -> None:
        self._engine = engine
        self._app_role = app_role

    def for_user(self, user_sub: str) -> "ConversationOutbox":
        """A facade scoped to ``user_sub`` -- every statement it runs goes
        through :func:`~poseidon.core.db.rls_transaction` with THIS
        identity, so nothing it does can ever touch another user's row."""
        return ConversationOutbox(self._engine, user_sub, self._app_role)


class ConversationOutbox:
    """One user's own outbox rows. :meth:`touch` opens exactly one
    :func:`~poseidon.core.db.rls_transaction` (mirrors ``core/chat/
    history.py``'s own ``UserHistory``)."""

    def __init__(self, engine: Engine, user_sub: str, app_role: str | None = None) -> None:
        self._engine = engine
        self._user_sub = user_sub
        self._app_role = app_role

    def _transaction(self):
        return rls_transaction(self._engine, self._user_sub, app_role=self._app_role)

    def touch(self, conversation_id: str) -> None:
        """Upsert this conversation's outbox row: insert
        ``last_turn_at=now(), status='pending', attempts=0`` if absent, or
        fully re-arm the existing row (module docstring) if present.
        Raises ``ValueError`` for a malformed ``conversation_id`` -- unlike
        several ``core/chat/history.py`` writes that silently no-op on a
        bad id, this id is always minted by ``HistoryStore.create_
        conversation`` before it ever reaches this method (Task 2's own
        call sites), so a malformed value here is this module's own
        caller's bug to surface loudly, not a caller-typo to absorb
        (the same reasoning ``core/chat/history.py``'s own ``_parse_
        optional_uuid`` gives for ``turn_id``)."""
        parsed = _parse_uuid(conversation_id)
        if parsed is None:
            raise ValueError(f"conversation_id {conversation_id!r} is not a valid id")
        with self._transaction() as conn:
            conn.execute(
                _TOUCH_SQL, {"conversation_id": str(parsed), "user_sub": self._user_sub}
            )


__all__ = ["ConversationOutbox", "OutboxStore"]
