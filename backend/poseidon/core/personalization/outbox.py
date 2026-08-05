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

**Phase 13 Task 4 amendment (2026-08-04, controller-caught pre-dispatch):
the two status-transition methods the worker needs,
:meth:`ConversationOutbox.mark_done` and :meth:`ConversationOutbox.
record_failure`.** The phase's own worker-privilege model (see
``poseidon/scripts/memory_worker.py``'s module docstring) permits exactly
ONE operation against this table outside a per-user ``rls_transaction`` --
the cross-user "which conversations are idle" claim query, which
structurally cannot be scoped to a single user. Everything the worker does
to a row AFTER claiming it, including moving that row to ``done`` or
``failed``, has to travel the same ``OutboxStore.for_user(sub)`` path every
other per-user operation in this codebase does; Task 1 built only
:meth:`touch`, so those two methods are added here rather than open-coded
against the privileged connection. :meth:`touch`'s own behavior is
untouched by this amendment.

**Both transitions are guarded on ``last_turn_at``, and this is
load-bearing, not defensive garnish.** A claim is not a lease: the row's
``FOR UPDATE SKIP LOCKED`` lock is released the moment the claim
transaction commits, long before the (slow, model-bound) distillation
finishes. A user who comes back mid-distillation reaches :meth:`touch`,
which fully re-arms the row -- ``last_turn_at = now()``, ``status =
'pending'``, ``attempts = 0``. An UNGUARDED ``SET status = 'done'`` landing
after that would silently swallow the new turn's own distillation trigger
until some later turn happened to re-arm the row again. Both methods below
therefore match on ``last_turn_at = :claimed_last_turn_at`` -- the exact
value the claim query read -- so a re-armed row is left alone and simply
re-distilled (with the new turn included) on a later cycle. This is
ordinary optimistic concurrency control, using a column the table already
has, rather than a new ``processing`` status or a lease timestamp the
schema deliberately does not carry (doc 05 section 5, decision D31).

**``updated_at`` is stamped by both methods, unlike ``touch``.** That
column exists to say when this row's PROCESSING state last changed, so a
transition that leaves it stale would make it meaningless. :meth:`touch`'s
own ``ON CONFLICT DO UPDATE`` does not set it (Task 1's shipped behavior,
deliberately left byte-unchanged here); a row that is only ever touched
therefore keeps its insert-time value, which is a pre-existing quirk this
amendment neither introduces nor is sanctioned to fix.
"""

import json
import uuid
from datetime import datetime

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

# Phase 13 Task 4. Both statements below carry the same
# ``last_turn_at = :claimed_last_turn_at`` guard -- see the module
# docstring's "Both transitions are guarded on last_turn_at" section for
# why that predicate is load-bearing rather than defensive.
_MARK_DONE_SQL = text(
    "UPDATE memory_outbox "
    "SET status = 'done', last_error = NULL, updated_at = now() "
    "WHERE conversation_id = :conversation_id AND last_turn_at = :claimed_last_turn_at"
)

# ONE statement, not a read-then-write pair: `attempts` is incremented and
# the resulting status decided in the same UPDATE, so there is no window in
# which a concurrent writer could observe (or interleave with) a half-applied
# failure. `attempts + 1 >= :max_attempts` reads the column's PRE-update
# value, so the row moves to 'failed' on exactly the attempt that reaches the
# ceiling -- with max_attempts=5, attempts lands on 5 and status on 'failed'
# together, never a sixth pending retry.
_RECORD_FAILURE_SQL = text(
    "UPDATE memory_outbox "
    "SET attempts = attempts + 1, last_error = :last_error, updated_at = now(), "
    "    status = CASE WHEN attempts + 1 >= :max_attempts THEN 'failed' ELSE 'pending' END "
    "WHERE conversation_id = :conversation_id AND last_turn_at = :claimed_last_turn_at "
    "RETURNING status, attempts"
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

    def mark_done(self, conversation_id: str, *, claimed_last_turn_at: datetime) -> bool:
        """Move a successfully-distilled row to ``status='done'``, clearing
        any ``last_error`` a previous failed attempt left behind.

        ``claimed_last_turn_at`` is the value the WORKER's claim query read
        off this row; the UPDATE only applies while the row still carries
        it (module docstring). Returns ``True`` when the row was actually
        marked, ``False`` when it was not -- which today means exactly one
        thing in practice: a :meth:`touch` landed between the claim and
        this call, so the row is legitimately ``pending`` again and must
        stay that way. A ``False`` return is therefore an ordinary,
        expected outcome the caller logs rather than an error it raises on.

        Raises ``ValueError`` for a malformed ``conversation_id``, exactly
        like :meth:`touch` and for the identical reason (this id is always
        one the claim query just read out of the table).
        """
        parsed = _parse_uuid(conversation_id)
        if parsed is None:
            raise ValueError(f"conversation_id {conversation_id!r} is not a valid id")
        with self._transaction() as conn:
            result = conn.execute(
                _MARK_DONE_SQL,
                {
                    "conversation_id": str(parsed),
                    "claimed_last_turn_at": claimed_last_turn_at,
                },
            )
        return result.rowcount == 1

    def record_failure(
        self,
        conversation_id: str,
        *,
        claimed_last_turn_at: datetime,
        error: dict,
        max_attempts: int,
    ) -> dict | None:
        """Record one failed distillation attempt: increment ``attempts``,
        store ``error`` in ``last_error``, and -- on the attempt that
        reaches ``max_attempts`` -- move the row to ``status='failed'``,
        all in one statement (see :data:`_RECORD_FAILURE_SQL`'s own comment
        for why this is deliberately not a read-then-write pair, and why
        the two halves the plan describes separately -- "increment attempts,
        stay pending" and "exhausted: mark failed with last_error" -- are
        one method rather than two).

        Returns ``{"status", "attempts"}`` as the row now stands, or
        ``None`` when the row was re-armed by a :meth:`touch` between the
        claim and this call (same ``last_turn_at`` guard, and the same
        "ordinary expected outcome" reading, as :meth:`mark_done`) -- a
        re-armed row's ``attempts`` is deliberately back at 0, and this
        failure belongs to content that row no longer describes.

        The failed row keeps its ``last_error`` for inspection either way
        (doc 05 section 5: "never silently dropped"). ``error`` is a plain
        dict serialized to the ``jsonb`` column, mirroring
        ``core/runlog.py``'s own ``error`` binding convention.
        """
        parsed = _parse_uuid(conversation_id)
        if parsed is None:
            raise ValueError(f"conversation_id {conversation_id!r} is not a valid id")
        with self._transaction() as conn:
            row = conn.execute(
                _RECORD_FAILURE_SQL,
                {
                    "conversation_id": str(parsed),
                    "claimed_last_turn_at": claimed_last_turn_at,
                    "last_error": json.dumps(error),
                    "max_attempts": max_attempts,
                },
            ).first()
        if row is None:
            return None
        return {"status": row.status, "attempts": row.attempts}


__all__ = ["ConversationOutbox", "OutboxStore"]
