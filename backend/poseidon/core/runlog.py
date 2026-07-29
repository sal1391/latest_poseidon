"""The run-log writer (doc 06 section 1): the parent ``turn_run`` row per
turn plus its two append-only children, ``llm_calls`` and ``tool_calls`` --
migration 0003's tables, written to from the live chat path Phase 6 wires up.

**The never-raises rule, and why.** TM1 shipped a CSV writer that recorded
every chart request to disk for later analysis; a permissions bug on that
file eventually made the writer raise, and because nothing guarded it, the
exception propagated straight into the request path and turned an
auditing nicety into a user-facing 500 -- on a feature that mattered to
nobody in the moment they hit it. Doc 06 section 1 names this explicitly:
"the whole path is wrapped so a run-log failure can never break the user's
answer". So every public method on :class:`RunLogWriter` wraps its ENTIRE
body in a single broad ``try/except`` and returns cleanly on failure ((a)
``None`` for :meth:`RunLogWriter.start_turn`, whose return value the caller
needs to keep going; (b) nothing at all for the three ``-> None`` methods,
which have nothing to return either way) -- a broken engine, a constraint
violation, a network blip mid-write are all just "this row did not get
written," never "this turn did not get answered."

That is only half the rule, though: a run-log write that fails IN SILENCE is
its own bug (doc 06's other named lesson -- "mom-comparison shipped with
zero tests" -- is the same failure shape one layer up: nothing watching
means nobody finds out). So the catch is never bare. Every failure logs at
ERROR, with the operation and enough identifying context (never the
question/answer text itself, which does not belong in a log stream) to find
the row that should have existed and did not -- and that log call is itself
the tested contract (``caplog``, see ``test_runlog_writer.py``), not an
afterthought.

**Ids.** Doc 06 marks every ``id`` column "UUIDv7" for insert locality on a
busy table; the Python standard library has no UUIDv7 generator (only
uuid1/3/4/5), so this module mints ``uuid.uuid4()`` strings instead -- a
documented v1 substitution the plan defers revisiting to Phase 10 (History +
RLS, doc 08), when durable ``conversations``/``messages`` land and id
ordering starts to matter for something more than a nice-to-have. Every id
column in migration 0003 carries no server-side default for exactly this
reason: the writer always supplies one explicitly.

**Connections.** One short-lived, transactional connection per call
(``engine.begin()``), opened and closed inside the method -- the same
per-call discipline ``SyntheticDataClient`` uses for the data path (see that
module's docstring), translated to the SQLAlchemy ``Engine`` this class is
handed rather than a raw DSN. Every statement is a plain
:func:`sqlalchemy.text` string with bound parameters, not a Core
``Table``/``insert()`` expression: these tables are Postgres-only (migration
0003 no-ops everywhere else, same as 0002), so there is no dialect to
abstract over, and a literal SQL string is both the simplest thing that
works and the easiest thing to pin the SHAPE of in an offline test (see
``test_runlog_writer.py``'s ``_RecordingEngine``).

**JSON columns.** ``parsed``/``args`` (always provided) and
``error``/``result_digest`` (optional) bind as ``json.dumps(...)`` text; an
INSERT/UPDATE parameter that lines up with a specific ``jsonb`` column lets
Postgres infer that column's type for the placeholder and parse the text
through the normal ``jsonb`` input path, so no explicit ``::jsonb`` cast is
needed (confirmed against a real Postgres in ``test_runlog_writer.py``'s pg
suite, not just asserted).
"""

import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnHandle:
    """What :meth:`RunLogWriter.start_turn` hands back: the row's id, and
    whether this call created it. ``created=False`` means ``(user_sub,
    client_turn_key)`` already existed -- a client retry landed on a turn
    that is already running or already finished, not a new one."""

    turn_run_id: str
    created: bool


_START_TURN_SQL = text(
    """
    INSERT INTO turn_run
        (id, kind, conversation_id, user_sub, client_turn_key, turn_index,
         question, mode, parsed, status, trace_id)
    VALUES
        (:id, :kind, :conversation_id, :user_sub, :client_turn_key, :turn_index,
         :question, :mode, :parsed, 'running', :trace_id)
    ON CONFLICT (user_sub, client_turn_key) DO NOTHING
    RETURNING id
    """
)

# Only reached when the insert above no-op'd: a row with this
# (user_sub, client_turn_key) already exists. NULL client_turn_key values
# never conflict under Postgres unique-constraint semantics (NULL <> NULL),
# so this branch is only ever taken when client_turn_key is not None.
_FIND_EXISTING_TURN_SQL = text(
    "SELECT id FROM turn_run WHERE user_sub = :user_sub AND client_turn_key = :client_turn_key"
)

_APPEND_LLM_CALL_SQL = text(
    """
    INSERT INTO llm_calls
        (id, turn_run_id, user_sub, seq, provider, model_id, role, prompt_version,
         prompt_hash, input_tokens, output_tokens, latency_ms, status, error)
    VALUES
        (:id, :turn_run_id, :user_sub, :seq, :provider, :model_id, :role, :prompt_version,
         :prompt_hash, :input_tokens, :output_tokens, :latency_ms, :status, :error)
    """
)

_APPEND_TOOL_CALL_SQL = text(
    """
    INSERT INTO tool_calls
        (id, turn_run_id, user_sub, seq, tool, server, args, result_digest, status,
         latency_ms, error)
    VALUES
        (:id, :turn_run_id, :user_sub, :seq, :tool, :server, :args, :result_digest, :status,
         :latency_ms, :error)
    """
)

_FINALIZE_SQL = text(
    """
    UPDATE turn_run
    SET status = :status, message_id = :message_id, answer_summary = :answer_summary,
        input_tokens = :input_tokens, output_tokens = :output_tokens,
        latency_ms = :latency_ms, error = :error, finished_at = now()
    WHERE id = :turn_run_id
    """
)


def _json_or_none(value: dict | None) -> str | None:
    return None if value is None else json.dumps(value)


class RunLogWriter:
    """Writes migration 0003's three tables. Construction touches nothing --
    ``engine`` is only ever used inside a method call, never at ``__init__``
    time -- and every public method below never raises (see the module
    docstring)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def start_turn(
        self,
        *,
        user_sub: str,
        conversation_id: str | None,
        client_turn_key: str | None,
        turn_index: int | None,
        question: str | None,
        mode: str,
        parsed: dict,
        kind: str = "chat_turn",
        trace_id: str | None = None,
    ) -> TurnHandle | None:
        """Insert the parent row (``status='running'``), or -- when
        ``client_turn_key`` names a turn that already exists for this
        ``user_sub`` -- return the EXISTING row's handle instead
        (idempotent turn creation on client retry, doc 06 section 1's
        ``unique (user_sub, client_turn_key)``). Both statements run inside
        one transaction, so the fallback lookup can only ever see a
        conflicting row that is already fully committed.

        Returns ``None`` on any failure -- see the module docstring.
        """
        try:
            new_id = str(uuid.uuid4())
            params = {
                "id": new_id,
                "kind": kind,
                "conversation_id": conversation_id,
                "user_sub": user_sub,
                "client_turn_key": client_turn_key,
                "turn_index": turn_index,
                "question": question,
                "mode": mode,
                "parsed": json.dumps(parsed),
                "trace_id": trace_id,
            }
            with self._engine.begin() as conn:
                row = conn.execute(_START_TURN_SQL, params).first()
                if row is not None:
                    return TurnHandle(turn_run_id=row[0], created=True)
                existing = conn.execute(
                    _FIND_EXISTING_TURN_SQL,
                    {"user_sub": user_sub, "client_turn_key": client_turn_key},
                ).first()
                # The insert only no-ops on a real conflict, so a matching
                # row must exist; still guarded rather than indexed blind,
                # since "never raises" allows no exceptions to this rule
                # either.
                if existing is None:
                    raise RuntimeError(
                        f"start_turn: insert conflicted for user_sub={user_sub!r} "
                        f"client_turn_key={client_turn_key!r} but no existing row was found"
                    )
                return TurnHandle(turn_run_id=existing[0], created=False)
        except Exception as exc:  # a run-log failure must never break the user's answer
            logger.error(
                "runlog start_turn failed: user_sub=%s client_turn_key=%s: %s: %s",
                user_sub,
                client_turn_key,
                type(exc).__name__,
                exc,
            )
            return None

    def append_llm_call(
        self,
        *,
        turn_run_id: str,
        user_sub: str,
        seq: int,
        provider: str,
        model_id: str,
        role: str,
        prompt_version: str,
        prompt_hash: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int | None,
        status: str,
        error: dict | None = None,
    ) -> None:
        """Append one model-call row. Never raises -- see the module
        docstring; a duplicate ``(turn_run_id, seq)`` (``unique`` violation)
        is just another failure this logs and swallows."""
        try:
            params = {
                "id": str(uuid.uuid4()),
                "turn_run_id": turn_run_id,
                "user_sub": user_sub,
                "seq": seq,
                "provider": provider,
                "model_id": model_id,
                "role": role,
                "prompt_version": prompt_version,
                "prompt_hash": prompt_hash,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "status": status,
                "error": _json_or_none(error),
            }
            with self._engine.begin() as conn:
                conn.execute(_APPEND_LLM_CALL_SQL, params)
        except Exception as exc:  # a run-log failure must never break the user's answer
            logger.error(
                "runlog append_llm_call failed: turn_run_id=%s seq=%s: %s: %s",
                turn_run_id,
                seq,
                type(exc).__name__,
                exc,
            )

    def append_tool_call(
        self,
        *,
        turn_run_id: str,
        user_sub: str,
        seq: int,
        tool: str,
        server: str | None,
        args: dict,
        result_digest: dict | None,
        status: str,
        latency_ms: int | None,
        error: dict | None = None,
    ) -> None:
        """Append one tool-dispatch row. Never raises -- see the module
        docstring; a duplicate ``(turn_run_id, seq)`` (``unique`` violation)
        is just another failure this logs and swallows."""
        try:
            params = {
                "id": str(uuid.uuid4()),
                "turn_run_id": turn_run_id,
                "user_sub": user_sub,
                "seq": seq,
                "tool": tool,
                "server": server,
                "args": json.dumps(args),
                "result_digest": _json_or_none(result_digest),
                "status": status,
                "latency_ms": latency_ms,
                "error": _json_or_none(error),
            }
            with self._engine.begin() as conn:
                conn.execute(_APPEND_TOOL_CALL_SQL, params)
        except Exception as exc:  # a run-log failure must never break the user's answer
            logger.error(
                "runlog append_tool_call failed: turn_run_id=%s seq=%s: %s: %s",
                turn_run_id,
                seq,
                type(exc).__name__,
                exc,
            )

    def finalize(
        self,
        *,
        turn_run_id: str,
        status: str,
        message_id: str | None,
        answer_summary: str | None,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        error: dict | None = None,
    ) -> None:
        """Set the terminal status, ``finished_at`` (server-side ``now()``),
        and the ``llm_calls`` token roll-up the caller already summed from
        its own appended records. Never raises -- see the module docstring;
        an invalid ``status`` (CHECK violation) is just another failure this
        logs and swallows, and the whole UPDATE rolls back with it (a single
        statement either fully applies or not at all)."""
        try:
            params = {
                "turn_run_id": turn_run_id,
                "status": status,
                "message_id": message_id,
                "answer_summary": answer_summary,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "latency_ms": latency_ms,
                "error": _json_or_none(error),
            }
            with self._engine.begin() as conn:
                conn.execute(_FINALIZE_SQL, params)
        except Exception as exc:  # a run-log failure must never break the user's answer
            logger.error(
                "runlog finalize failed: turn_run_id=%s: %s: %s",
                turn_run_id,
                type(exc).__name__,
                exc,
            )


__all__ = ["RunLogWriter", "TurnHandle"]
