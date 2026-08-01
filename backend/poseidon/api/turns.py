"""Phase 11 Task 3 (doc 01 section 5, doc 06 sections 1-2): the reconnect
reconciliation endpoint -- ``GET /api/turns/{id}`` rebuilds one turn's
progress from ``turn_run`` plus its ``llm_calls``/``tool_calls`` children
plus the linked ``messages`` row, for a client that dropped its SSE
connection mid-turn (doc 01 section 5's own client rule 3: "On connection
drop it calls ``GET /api/turns/{turn_id}`` to reconcile from the run log
... rather than replaying the model").

Response shape is pinned exactly (this task's own brief, verbatim):
``{turn: {id, conversation_id, message_id, kind, status, question, mode,
created_at, finished_at, trace_id, redacted}, llm_calls: [{seq, provider,
model_id, role, prompt_version, status, input_tokens, output_tokens,
latency_ms}], tool_calls: [{seq, tool, server, status, latency_ms}],
message: {id, parts} | null}``. ``question`` is ``null`` exactly when the
turn's conversation has been deleted (redacted); every other field this
shape carries survives redaction untouched (doc 05 section 7's own
"survives" list).

**Children never expose args/prompt hashes.** ``tool_calls.args`` and
``llm_calls.prompt_hash`` are deliberately never selected below -- doc 06
section 1 documents both as internal audit payload (the raw dispatch
arguments; a hash of the rendered system prompt actually sent), and this
task's own brief states the reason plainly: "the SPA needs progress, not
payloads". A reconciling client wants to know WHICH steps ran, in what
order, and with what terminal status -- not to replay their contents.

**RLS does the isolation work; this route does none of its own.** The one
query below runs inside :func:`~poseidon.core.db.rls_transaction` with the
caller's own ``user_sub`` -- migration 0005's owner policies on
``turn_run``/``llm_calls``/``tool_calls`` (Phase 11 Task 1) and migration
0004's owner policy on ``messages`` filter every SELECT to rows this
caller actually owns. A ``turn_id`` naming another user's turn therefore
reads back identically to one that was never minted at all -- both simply
match zero rows -- so "unknown" and "foreign" collapse into the SAME 404,
exactly like every other lookup in this codebase that relies on RLS for
isolation rather than an explicit ownership check (``api/live_chat.py``'s
own ``get_messages``/``_message_visible``). A syntactically malformed
``turn_id`` (not even a valid UUID) is parsed defensively
(:func:`_parse_uuid`, the same tiny, duplicated-on-purpose pattern every
module in this codebase that parses a path-segment id keeps for itself --
see e.g. ``api/live_chat.py``'s own ``_parse_message_id``, ``core/chat/
history.py``'s own ``_parse_uuid`` -- rather than reaching across another
module's leading-underscore boundary) and folded into the identical 404,
never a raw database error.

**Redaction (doc 05 section 7).** A deleted conversation's ``turn_run`` row
survives, redacted (Phase 11 Task 1): ``question``/``answer_summary``/
``parsed`` are nulled and ``redacted_at`` is stamped, in the DATABASE
itself, by ``redact_turns_for_conversation`` -- so ``row.question`` read
back here is ALREADY ``None`` for a redacted turn. This route performs no
redaction logic of its own; it only reports ``redacted: bool(row.
redacted_at)`` alongside whatever the row already holds, which is exactly
why reconciliation keeps working, at reduced fidelity, for a turn whose
conversation was since deleted.

**Mounted only under ``chat_mode == "live"``.** ``app.state.db_engine``
(this route's own dependency, read off ``request.app.state``) is built by
``api/app.py``'s ``_wire_live_chat``, which only runs in that mode -- see
this module's own router being included there, alongside ``live_chat.
router``, rather than unconditionally in ``create_app`` itself.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text

from poseidon.api.auth import require_sales
from poseidon.core.db import rls_transaction

router = APIRouter(prefix="/api", tags=["turns"])

_TURN_SQL = text(
    """
    SELECT id, conversation_id, message_id, kind, status, question, mode,
           created_at, finished_at, trace_id, redacted_at
    FROM turn_run
    WHERE id = :id
    """
)

_LLM_CALLS_SQL = text(
    """
    SELECT seq, provider, model_id, role, prompt_version, status,
           input_tokens, output_tokens, latency_ms
    FROM llm_calls
    WHERE turn_run_id = :turn_run_id
    ORDER BY seq
    """
)

_TOOL_CALLS_SQL = text(
    """
    SELECT seq, tool, server, status, latency_ms
    FROM tool_calls
    WHERE turn_run_id = :turn_run_id
    ORDER BY seq
    """
)

_MESSAGE_SQL = text("SELECT id, parts FROM messages WHERE id = :id")

_UNKNOWN_TURN_DETAIL = "unknown turn"


def _parse_uuid(value: str) -> uuid.UUID | None:
    """``value`` as a :class:`uuid.UUID`, or ``None`` if it is not one --
    see the module docstring's "RLS does the isolation work" for why a
    malformed id is treated exactly like an absent one."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


@router.get("/turns/{turn_id}", dependencies=[Depends(require_sales)])
def get_turn(turn_id: str, request: Request) -> dict:
    """See the module docstring for the pinned response shape and the
    RLS/redaction rationale. A malformed, unknown, or foreign ``turn_id``
    all 404 identically."""
    parsed = _parse_uuid(turn_id)
    if parsed is None:
        raise HTTPException(404, detail=_UNKNOWN_TURN_DETAIL)

    app_state = request.app.state
    with rls_transaction(
        app_state.db_engine,
        request.state.user.sub,
        app_role=app_state.settings.database_app_role,
    ) as conn:
        row = conn.execute(_TURN_SQL, {"id": str(parsed)}).first()
        if row is None:
            raise HTTPException(404, detail=_UNKNOWN_TURN_DETAIL)
        llm_rows = conn.execute(_LLM_CALLS_SQL, {"turn_run_id": str(parsed)}).all()
        tool_rows = conn.execute(_TOOL_CALLS_SQL, {"turn_run_id": str(parsed)}).all()
        message_row = None
        if row.message_id is not None:
            message_row = conn.execute(_MESSAGE_SQL, {"id": str(row.message_id)}).first()

    return {
        "turn": {
            "id": str(row.id),
            "conversation_id": (
                str(row.conversation_id) if row.conversation_id is not None else None
            ),
            "message_id": str(row.message_id) if row.message_id is not None else None,
            "kind": row.kind,
            "status": row.status,
            "question": row.question,
            "mode": row.mode,
            "created_at": row.created_at.isoformat(),
            "finished_at": row.finished_at.isoformat() if row.finished_at is not None else None,
            "trace_id": row.trace_id,
            "redacted": row.redacted_at is not None,
        },
        "llm_calls": [
            {
                "seq": call.seq,
                "provider": call.provider,
                "model_id": call.model_id,
                "role": call.role,
                "prompt_version": call.prompt_version,
                "status": call.status,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "latency_ms": call.latency_ms,
            }
            for call in llm_rows
        ],
        "tool_calls": [
            {
                "seq": call.seq,
                "tool": call.tool,
                "server": call.server,
                "status": call.status,
                "latency_ms": call.latency_ms,
            }
            for call in tool_rows
        ],
        "message": (
            {"id": str(message_row.id), "parts": message_row.parts}
            if message_row is not None
            else None
        ),
    }


__all__ = ["router"]
