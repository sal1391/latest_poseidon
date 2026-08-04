"""Phase 12 Task 1 (doc 06 section 7): the Postgres-backed store behind
migration 0006's ``message_feedback`` table -- the durable replacement for
``core/chat/history.py``'s honest, in-memory ``FeedbackStubStore`` (deleted
by this task; that module's own docstring named it a stub precisely because
this table did not exist yet).

**Shape mirrors ``HistoryStore`` on purpose.** ``FeedbackStore(engine,
app_role).for_user(sub) -> UserFeedback`` is the identical construction
discipline ``HistoryStore(engine, app_role).for_user(sub) -> UserHistory``
already established (Phase 10 Task 2): cheap to construct, no connection
opened until a :class:`UserFeedback` method actually runs one, one shared
instance per process (``api/app.py``'s own wiring). ``app_role`` threads
straight through to :func:`~poseidon.core.db.rls_transaction` exactly like
every other caller of that wrapper in this codebase.

**No ``user_sub`` WHERE clauses, anywhere, on purpose** -- the same D28
reliance ``history.py``'s own module docstring states: every statement below
runs inside :func:`~poseidon.core.db.rls_transaction`, which sets
``app.user_sub`` as the first statement of the transaction; Postgres itself
ANDs the owning table's RLS predicate onto every statement from there. A
message that does not belong to the caller reads back as zero rows --
INDISTINGUISHABLE from a message that was never created at all -- which
:meth:`UserFeedback.upsert` turns into ``LookupError`` (the route's existing
404) below.

**Resolving ``run_id`` is also the existence gate.** ``message_feedback.
run_id`` (doc 06 section 7) is ``NOT NULL REFERENCES turn_run(id)`` -- there
is no honest ``run_id`` to write without first reading ``messages.turn_id``
for the target message, and that read is itself RLS-filtered by ``messages``'
own owner policy (migration 0004). :meth:`UserFeedback.upsert` therefore
needs no SEPARATE visibility check the way ``api/live_chat.py``'s own
``_message_visible`` performs for ``GET`` (that route's existence gate is
unchanged by this task) -- resolving ``run_id`` and gating existence are the
SAME query. A row of ``turn_id IS NULL`` (the opener greeting -- doc 05
section 6's own schema declares it nullable, and doc 08's ``HistoryStore.
create_conversation`` always mints the opener with ``turn_id=None``) is
VISIBLE but has no run to attach feedback to, so it raises the distinct
:class:`FeedbackNotApplicable` instead -- the plan's own boundary: "foreign/
unknown message = INVISIBLE = 404; visible-but-turnless = 422."

**Replay, and why this module does nothing special for it.** A replayed
assistant message's own persisted ``messages.turn_id`` names the ORIGINAL
run it replayed, not a fresh one -- ``api/live_chat.py``'s own module
docstring ("Phase 12 Task 1: replayed messages persist under the ORIGINAL
run's id") explains the one production fix this task made to guarantee
that. Because that guarantee lives entirely in what gets WRITTEN to
``messages.turn_id``, this module's own read (:data:`_RESOLVE_RUN_ID_SQL`)
needs no branch on whether a message came from a replay at all -- it just
reads the column, which is exactly the plan's own "no special-casing"
requirement.
"""

import uuid

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.db import rls_transaction
from poseidon.core.util.uuid7 import uuid7


class FeedbackNotApplicable(ValueError):
    """Raised by :meth:`UserFeedback.upsert` when ``message_id`` names a
    VISIBLE message whose ``turn_id`` is ``NULL`` (the opener greeting) --
    doc 06 section 7's ``run_id`` join has nothing to attach to. Mirrors
    ``core/chat/history.py``'s own ``MalformedCursor`` precedent: a
    :class:`ValueError` subclass, not a bare custom exception, so any
    pre-existing broad ``except ValueError`` a caller might already have
    keeps working unchanged. ``api/live_chat.py`` maps this to a pinned 422
    RFC-7807 problem (title ``"feedback_not_applicable"``)."""


def _parse_uuid(value: str) -> uuid.UUID | None:
    """``value`` as a :class:`uuid.UUID`, or ``None`` if it is not one --
    mirrors ``core/chat/history.py``'s own ``_parse_uuid`` exactly (a
    malformed id can never match a real row, so it is treated exactly like
    an absent one, i.e. it also raises ``LookupError`` below rather than
    reaching the database at all)."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


# Resolving run_id and gating existence are the SAME query -- see the module
# docstring's "Resolving run_id is also the existence gate". No user_sub
# filter: RLS (messages_owner, migration 0004) already scopes this to rows
# the caller may see.
_RESOLVE_TURN_ID_SQL = text("SELECT turn_id FROM messages WHERE id = :message_id")

# ON CONFLICT DO UPDATE -- doc 06 section 7: "one verdict per user per
# message; upsert amends". Columns NOT listed here (id, created_at,
# updated_at) take their DEFAULT on a fresh INSERT and are left untouched by
# the UPDATE branch except updated_at, which is explicitly bumped -- created_
# at therefore keeps first-write time across any number of amends, exactly
# the schema's own pinned contract, and the row's id never changes either.
_UPSERT_SQL = text(
    "INSERT INTO message_feedback (id, message_id, run_id, user_sub, verdict, comment) "
    "VALUES (:id, :message_id, :run_id, :user_sub, :verdict, :comment) "
    "ON CONFLICT (message_id, user_sub) DO UPDATE "
    "SET verdict = EXCLUDED.verdict, comment = EXCLUDED.comment, updated_at = now()"
)

# The route's existing GET shape (module docstring): {"verdict", "comment"}.
_GET_SQL = text(
    "SELECT verdict, comment FROM message_feedback WHERE message_id = :message_id "
    "AND user_sub = :user_sub"
)


class FeedbackStore:
    """The per-process entry point: holds the ``Engine`` (and the resolved
    ``app_role``) and hands out per-user facades. Construction touches
    nothing -- no connection is opened until a :class:`UserFeedback` method
    actually runs one (mirrors ``core/chat/history.py``'s own
    ``HistoryStore``)."""

    def __init__(self, engine: Engine, app_role: str | None = None) -> None:
        self._engine = engine
        self._app_role = app_role

    def for_user(self, user_sub: str) -> "UserFeedback":
        """A facade scoped to ``user_sub`` -- every statement it runs goes
        through :func:`~poseidon.core.db.rls_transaction` with THIS
        identity, so nothing it does can ever touch another user's row."""
        return UserFeedback(self._engine, user_sub, self._app_role)


class UserFeedback:
    """One user's view of their own feedback verdicts. Both methods open
    exactly one :func:`~poseidon.core.db.rls_transaction` (mirrors
    ``core/chat/history.py``'s own ``UserHistory``)."""

    def __init__(self, engine: Engine, user_sub: str, app_role: str | None = None) -> None:
        self._engine = engine
        self._user_sub = user_sub
        self._app_role = app_role

    def _transaction(self):
        return rls_transaction(self._engine, self._user_sub, app_role=self._app_role)

    def upsert(self, message_id: str, verdict: str | None, comment: str | None) -> None:
        """Idempotent upsert keyed on ``(message_id, user_sub)`` -- a second
        call for the same message AMENDS the first (verdict and comment both
        replaceable), never creates a second row. Raises ``LookupError`` when
        ``message_id`` is malformed, absent, or another user's (route: 404);
        raises :class:`FeedbackNotApplicable` when it names a visible message
        with no linked turn, i.e. the opener (route: 422 pinned). See the
        module docstring for why these two checks are one query, not two.

        ``verdict=None`` is the un-vote path (migration 0007's own docstring:
        "un-vote... upserts verdict = NULL into the SAME row" -- never a
        DELETE, migration 0006's own D25 NO-DELETE-grant decision stays
        untouched). ``comment`` is forced to ``None`` alongside it,
        regardless of what the caller passed -- a cleared vote must never
        carry a stale comment forward -- so this is the ONE place that
        contract is enforced, defensively, for every caller."""
        if verdict is None:
            comment = None
        parsed_message_id = _parse_uuid(message_id)
        if parsed_message_id is None:
            raise LookupError(f"message {message_id!r} is not a valid message id")
        with self._transaction() as conn:
            row = conn.execute(
                _RESOLVE_TURN_ID_SQL, {"message_id": str(parsed_message_id)}
            ).first()
            if row is None:
                raise LookupError(
                    f"message {message_id!r} does not exist or is not visible to this user"
                )
            turn_id = row[0]
            if turn_id is None:
                raise FeedbackNotApplicable(
                    f"message {message_id!r} has no linked turn (the opener) - "
                    "feedback is not applicable"
                )
            conn.execute(
                _UPSERT_SQL,
                {
                    "id": str(uuid7()),
                    "message_id": str(parsed_message_id),
                    "run_id": str(turn_id),
                    "user_sub": self._user_sub,
                    "verdict": verdict,
                    "comment": comment,
                },
            )

    def get(self, message_id: str) -> dict | None:
        """``{"verdict": str, "comment": str | None}`` -- the route's
        existing GET shape -- or ``None`` for a malformed/absent id, a
        visible message nobody has recorded a verdict for yet, OR (migration
        0007) a visible message whose ONE row has been cleared back to
        ``verdict IS NULL`` (the un-vote path -- :meth:`upsert`'s own
        docstring). A cleared vote and a never-voted message must look
        IDENTICAL from the outside -- the route's existing 404-means-no-vote
        contract stays completely unchanged for callers -- so a NULL-verdict
        row is treated exactly like no row at all, never surfaced as
        ``{"verdict": None, ...}``. Does not itself distinguish "invisible
        message" from "no feedback yet": the route's own pre-existing
        ``_message_visible`` gate (unchanged by this task) already tells
        those apart before ever calling this."""
        parsed_message_id = _parse_uuid(message_id)
        if parsed_message_id is None:
            return None
        with self._transaction() as conn:
            row = conn.execute(
                _GET_SQL, {"message_id": str(parsed_message_id), "user_sub": self._user_sub}
            ).first()
        if row is None or row.verdict is None:
            return None
        return {"verdict": row.verdict, "comment": row.comment}


__all__ = ["FeedbackNotApplicable", "FeedbackStore", "UserFeedback"]
