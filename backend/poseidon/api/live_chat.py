"""Phase 6 Task 4's live chat HTTP surface (doc 01 section 5): the real
``execute_turn`` pipeline behind ``CHAT_MODE=live``, mounted BY
``poseidon.api.app.create_app`` INSTEAD of ``mock_chat.py``'s scripted demo
-- the two routers are never mounted together (see ``app.py``'s own mount
switch).

Seven routes:

- ``POST /api/conversations/{cid}/messages`` drives ONE real chat turn
  through :func:`~poseidon.core.chat.orchestrator.execute_turn` and streams
  doc 01 section 5's SSE envelope exactly as ``mock_chat.py``'s own
  ``_sse()`` already does (byte-identical wire format -- the frontend's
  parser is not this task's to touch; see ``events.py``'s module
  docstring). Same path, same request/response shape as the mock's own
  ``send_message``.
- ``GET /api/skills`` reports the registry's real, router-visible skills
  -- ``[{id, label, description}]`` -- for the frontend's ``SkillsPicker``.
- ``POST /api/conversations``, ``GET /api/conversations``, ``GET
  /api/conversations/{cid}/messages``, ``POST``/``GET
  /api/messages/{mid}/feedback`` -- the live bootstrap routes (Phase 6 Task
  5), now (Phase 10 Task 3) backed by persisted history instead of an
  in-memory store -- see "Phase 10 Task 3: the persistent-history cutover"
  below.
- ``DELETE /api/conversations/{cid}`` (Phase 11 Task 1) hard-deletes a
  conversation and redacts its linked run-log audit rows in one transaction
  -- see :func:`delete_conversation`'s own docstring, doc 05 section 7.

**Bridging a synchronous orchestrator into an async stream.**
``execute_turn`` is entirely synchronous -- it calls its sink's ``send``
callable directly, with no ``await`` anywhere in the call chain (see
``events.py``'s own "Synchronous by construction"). Running it directly on
the asyncio event loop thread would block every other request for the whole
turn. Each request therefore runs ``execute_turn`` in a worker thread
(``anyio.to_thread.run_sync``), and the sink's ``send`` callable pushes each
frame onto a plain, thread-safe ``queue.Queue`` that the async generator
drains one item at a time (also via ``anyio.to_thread.run_sync``, so the
event loop is never blocked waiting on it either).

**App-state wiring** (built once per app, in ``app.py``'s own
``_wire_live_chat``, and read back here per request): ``skill_registry``,
``history_store`` (Phase 10 Task 3, replacing ``conversation_state_store``/
``transcript_store``), ``feedback_store``, ``db_engine``, ``role_client``,
``prompt_registry``, ``data_client``, ``run_log_writer``, and
``tool_registry`` (a :class:`~poseidon.mcp.registry.ToolServerRegistry`,
threaded to ``execute_turn`` as its own ``tools`` keyword -- see ``app.py``'s
own ``_build_tool_registry`` for the ``LLM_MODE``-gated fixture-vs-real
choice). ``data_client`` is built ONCE, unlike ``poseidon.api.dev_runner``'s
own per-request ``SyntheticDataClient`` construction -- there is no
connection pool or other per-call state to leak between requests either way.

**An unhandled exception mid-turn.** ``execute_turn`` already turns every
failure ITS OWN pinned contract recognizes into a structured ``turn_error``
frame (an LLM provider error, a skill dispatch failure, the duplicate-turn
retry short-circuit -- see ``orchestrator.py``'s own module docstring). It
does not, and cannot, promise to catch EVERYTHING -- a genuine bug, or a
real network exception a provider layer failed to normalize, can still
escape it. Letting that exception propagate out of ``run_turn_sync``
uncaught would crash the whole ``anyio`` task group with an
``ExceptionGroup``, which Starlette's streaming response machinery turns
into a raw ``httpx.RemoteProtocolError`` on the client side -- the frontend
recovers, but the user sees a silently truncated answer with no error shown,
and the ``turn_run`` row (when a writer is configured) is left orphaned at
``status='running'`` forever. ``run_turn_sync`` therefore wraps the WHOLE
``execute_turn`` call in one more ``except Exception`` (not ``BaseException``
-- a cancelled request must keep unwinding): it logs the failure at ERROR,
emits ONE more pinned ``turn_error`` frame (``code="internal_error"``, a
generic, byte-pinned message that never leaks exception internals to the
client), and -- when a writer is configured -- finalizes the run-log row
itself, keyed by ``sink.turn_id`` (the turn-id unification: ``turn_run.id``
IS the SSE ``turn_id`` every frame already carries). The stream still ends
cleanly either way -- ``finally: frame_queue.put(_DONE)`` is untouched, so a
crash now looks to the client like any other terminal ``error`` frame, never
a broken connection.

**Recording the transcript.** :class:`~poseidon.core.chat.history.
TurnTranscriptBuffer` needs each turn's assistant PARTS, but the only place
those exist is already-serialized SSE frame strings -- ``execute_turn``/
``SseEnvelopeSink`` push wire-format text through the ``send`` callable,
never a structured event object (see ``events.py``'s own "Synchronous by
construction": this keeps that module importing nothing async and knowing
nothing about a caller's storage needs). Rather than widen
``SseEnvelopeSink``'s contract to also hand back structured data --
``events.py`` is not a file this task may touch -- :func:`_record_
transcript_frame` decodes each frame the SAME way every test module in this
codebase already does (``_parse_frame``/``read_sse``'s own line-splitting
discipline: ``events.py``'s wire format is pinned byte-for-byte, so this
decoding is exactly as stable as the format itself), then folds it into the
assistant message's parts, mirroring the frontend's own ``applyEventTo``
rules. This runs inside ``send`` itself (called synchronously, from
whichever thread is running the turn), so the buffer is complete by the
time the client sees the final frame.

The SAME technique -- decode an already-serialized frame, mutate it, and
re-serialize it byte-for-byte in the SAME pinned wire shape -- is how
:func:`_inject_done_title` adds the additive ``"title"`` field to the
``done`` frame (see "Phase 10 Task 3" below for why that field cannot be
known at the moment ``sink.done()`` fires), and (Phase 11 Task 3) the
additive ``"replayed"`` field.

**Phase 11 Task 3: true duplicate-turn replay, and why this module needs
almost no new code for it.** ``core/chat/orchestrator.py``'s own ``_begin_
turn`` (doc 01 section 5) now replays an earlier ``ok`` turn's persisted
answer via ``sink.push_part``/``sink.done`` -- the SAME two public
``SseEnvelopeSink`` methods the clarify short-circuit already calls
directly. Because ``send`` (above) and :func:`_record_transcript_frame`
already fold every ``part``/``done`` frame into the transcript buffer
GENERICALLY, with no branch on WHY a frame was produced, a replayed turn's
frames flow through this module's entire existing pipeline -- transcript
folding, the held-back-``done``/title machinery, persistence -- completely
unchanged: the only two edits this task makes here are threading
``TurnOutcome.replayed`` into :func:`_inject_done_title` (the wire-level
additive field) and into :func:`_finalize_turn`'s own title-computation
guard (see that function's own docstring for why the guard is defensive
rather than load-bearing in practice). A replayed turn's OWN assistant
message still persists as a new row, under this request's own freshly
minted ``message_id`` -- never the original turn's -- exactly mirroring
this module's own pre-existing "every retry duplicates the user's own
message row too" behavior (``append_user_message`` runs, unconditionally,
before the duplicate-turn check is ever reached); this is disclosed as an
accepted, symmetric consequence, not a gap this task closes.

**Phase 12 Task 1: replayed messages persist under the ORIGINAL run's id.**
The paragraph above's own "freshly minted ``message_id`` -- never the
original turn's" is exactly right about the ROW's id; it does not extend to
that row's ``turn_id`` COLUMN. Before this task, :func:`_persist_once`
unconditionally persisted every assistant message -- replayed or not --
under this request's own freshly minted ``turn_id`` (the SAME value the SSE
envelope carries, per ``_begin_turn``'s own "not ``sink.turn_id``" comment
and its test's own ``replay_turn_id != first_turn_id`` assertion). For a
TRUE replay that id is never written to ``turn_run`` at all -- the entire
point of a replay is that the pipeline, and therefore ``writer.start_turn``,
never runs again -- so the persisted message ended up with an ORPHANED
``turn_id``, matched by no ``turn_run`` row, confirmed empirically against
this environment's own compose Postgres (a throwaway probe script, this
codebase's own "not guessed at" discipline). Phase 12's ``message_feedback.
run_id`` is ``NOT NULL REFERENCES turn_run(id)`` (doc 06 section 7) with no
special-casing for replay anywhere in :mod:`~poseidon.core.chat.feedback`
(that module's own docstring: "no special-casing... a test proves it"),
which is only satisfiable if ``messages.turn_id`` already names a real run
for every message, replayed or not. :func:`_persist_once` therefore now
prefers ``outcome_holder``'s ``TurnOutcome.turn_run_id`` when one is
available -- ``_begin_turn`` already resolves this to the ORIGINAL row's id
on a true replay -- falling back to the closure ``turn_id`` exactly when no
outcome exists yet (a mid-turn structured failure reaches ``_persist_once``
via ``send``'s own ``"error"`` branch, synchronously, WHILE ``execute_turn``
is still on the stack -- ``run_turn_sync`` has no outcome to record until
that call returns) or carries no run at all (no writer configured, or
``start_turn`` itself failed -- ``RunLogWriter`` never raises, degrading to
``turn_run_id=None`` instead per its own module docstring). For every
NON-replay turn, ``TurnOutcome.turn_run_id`` already equals ``turn_id``
byte-for-byte (``_begin_turn`` passes ``turn_run_id=sink.turn_id`` to
``start_turn``, which uses it verbatim as the new row's id when it creates
one), so this is a no-op change for every other path -- confirmed by this
task's full regression run, and by ``messages.turn_id`` having had no
production reader anywhere in this codebase before Phase 12 (nothing else
ever selected that column), so no existing behavior could have depended on
the old, orphaned value.

**The snowflake guard.** ``dev_runner.py``'s own ``_build_ctx`` refuses
``data_backend != "synthetic"`` with a structured 501 BEFORE ever
constructing a client to query with. ``send_message`` checks ``settings.
data_backend`` first, before building the worker-thread/queue machinery and
before ``execute_turn`` (and therefore ``parse_turn``'s own ``data.list_
dimension_values`` calls) could ever touch ``app_state.data_client`` at
all -- mirroring dev_runner's exact guard condition and ``problem()`` shape
(``501``, title ``"backend not implemented"``), adapted to this endpoint's
SSE contract instead of a plain JSON body: one ``turn_error`` frame, built
through a THROWAWAY :class:`~poseidon.core.chat.events.SseEnvelopeSink`
whose ``send`` merely appends to a list, since there is no turn to run and
therefore nothing to bridge into a worker thread. This frame carries no
preceding ``accepted``: this guard fires before any turn begins at all, so
there is no ``turn_index`` to report. The user's message and an (empty)
assistant message are still persisted first, the same as every other path
through ``send_message`` -- the user really did send it, and this mirrors
how the internal-error crash path also leaves behind an assistant message
with whatever parts existed before the crash.

**Phase 10 Task 3: the persistent-history cutover.** Live chat moves from
two in-memory stores (``TranscriptStore``, deleted by this task --
conversations/messages/feedback; ``ConversationStateStore`` -- per-
conversation slots/turn-index/brief-done, KEPT in ``core/chat/state.py``
since ``core/chat/orchestrator.py`` still imports it as a type hint, but no
longer constructed here) to :class:`~poseidon.core.chat.history.
HistoryStore`/:class:`~poseidon.core.chat.history.DbStateStore` (Postgres,
row-level-security-scoped per caller). Every route below resolves a fresh
:class:`~poseidon.core.chat.history.UserHistory` per request --
``request.app.state.history_store.for_user(request.state.user.sub)`` --
rather than reading one shared, unscoped store, so nothing returned can
ever be another user's row (RLS enforces this in the database; this module
adds no ``user_sub`` filtering of its own, matching ``history.py``'s own
"no WHERE clauses, anywhere, on purpose" discipline).

*Id minting.* ``turn_id``/``message_id``/the user message's own id are all
minted here, via :func:`~poseidon.core.util.uuid7.uuid7` -- never
``uuid.uuid4`` -- since Phase 10 Task 2's migration declares no server-side
default for either table's ``id`` column and ``uuid7`` is this phase's only
id mint (real time-ordering for the busy ``messages`` table's insert
locality, doc 05 section 6). ``uuid.uuid4`` remains imported for a DIFFERENT
reason -- ``uuid.UUID(...)`` parsing (:func:`_parse_message_id`), never
minting.

*A conversation that does not exist (or is not this caller's) now 404s at
SEND time too.* ``TranscriptStore.append_user_message`` used to auto-vivify
any ``cid`` it had never seen (a disclosed Task 4/5 asymmetry with ``GET
.../messages``, which always 404'd on an unknown id). ``UserHistory.
append_user_message`` cannot do that: ``messages.conversation_id`` is a real
foreign key, so a bare INSERT naming a conversation that does not exist (or
belongs to someone else) raises ``LookupError`` rather than silently
succeeding or auto-creating a row. This is not a gap to route around --
auto-vivifying a real, persisted, RLS-owned table would mean minting
``conversations`` rows nobody ever asked for -- so ``send_message`` maps
that ``LookupError`` straight to the SAME 404 ``GET .../messages`` already
uses for an unknown/foreign conversation.

*Titles at turn one.* After a SUCCESSFUL first turn (``turn_index == 1``,
``outcome.status == "ok"``), the route calls :func:`~poseidon.core.llm.
titles.title_for`, falls back to ``question[:60]`` on ``""``, and both
persists the result (``UserHistory.set_title``) and folds it into that
turn's own ``done`` frame as an additive ``"title": str|null`` field (``null``
on every other ``done`` frame -- a later turn, or a turn-one ``clarify``).
The catch: ``sink.done(usage)`` is called by ``orchestrator.py`` itself,
synchronously, BEFORE ``execute_turn`` returns its ``TurnOutcome`` -- by the
time this route could inspect ``outcome.status``, the ``done`` frame may
already be serialized and queued for the client. ``core/chat/orchestrator.py``
is out of this task's sanctioned edits (zero orchestrator changes, by
design), so the fix cannot move up a layer into ``sink.done`` itself
either. Instead, ``send`` holds back (never immediately queues) the ONE
``done`` frame a turn ever produces; once ``execute_turn`` returns (the
``else:`` branch of ``run_turn_sync``'s ``try``), the real outcome is known,
the title is computed (or left ``None``), and :func:`_inject_done_title`
re-serializes the buffered frame with that field before it ever reaches the
client. ``turn_index`` itself is read back off the ``accepted`` frame
(``TurnOutcome`` carries no such field) -- the one other frame ``send``
inspects rather than merely folding into the transcript buffer or forwarding
verbatim. A title-computation failure (a role-client bug, a ``set_title``
call landing on a dropped connection -- ``title_for`` itself already
swallows a provider error into ``""``, per its own docstring, but nothing
guards the ``RoleClient.invoke``/``set_title`` calls THIS route makes) is
caught, logged, and treated as "no title" rather than allowed to break the
``done`` frame an already-finished, otherwise-successful turn owes the
client -- the identical "a second failure must never re-terminate an
already-finished turn" discipline ``orchestrator.py``'s own pass-through
repopulation guard uses.

**Fix round 1, Important-3: persistence happens-before the terminal
frame.** The in-memory ``TranscriptStore`` this cutover replaced mutated
the SAME dict object a reopened transcript read, in place, before any
frame (including the terminal one) was ever queued -- "the client saw the
terminal frame" and "the transcript reflects it" were inseparable, since
there was no commit step to race. Moving to a real database introduces
exactly that commit step, and the first cut of this cutover queued each
turn's terminal frame (``done`` or ``error``) BEFORE ``_persist_assistant_
message`` ran in ``run_turn_sync``'s own ``finally`` -- a client reacting
to the terminal SSE frame by immediately issuing ``GET .../messages``
could, in principle, race the still-in-flight ``INSERT``. Closed by moving
the persist call to the exact two call sites that ever queue a terminal
frame -- ``send``'s own ``"error"`` branch, and :func:`_finalize_turn`
immediately before it queues the (title-injected) ``done`` frame -- via
:func:`_persist_once`, so the write ALWAYS completes, synchronously, on
this turn's one worker thread, before ``frame_queue.put`` ever makes that
frame visible to the async generator (and therefore the client) at all.
``run_turn_sync``'s own ``finally`` still calls ``_persist_once`` too --
now a pure safety net for a path that produces neither frame at all
(unreachable through any code this app controls today), never a second,
independent write: ``_persist_once``'s own guard makes a repeated call
into a no-op, which matters concretely here -- ``messages.id`` is this
turn's one fixed, already-minted ``message_id``, so a genuine second
``INSERT`` would fail the table's own primary key, not merely waste work.

*Malformed cursors.* ``UserHistory.list_conversations``/``.get_messages``
raise :class:`~poseidon.core.chat.history.MalformedCursor` (a ``ValueError``
subclass) for a cursor that fails to decode -- see that module's own
docstring for why this is the one input that does NOT fail closed to a
harmless default. :func:`malformed_cursor_response`, registered by
``app.py`` as this exception's handler (the same pattern ``api/auth.py``'s
``AuthError``/``RateLimitExceeded`` already establish), maps it to a 400
RFC-7807 problem detail -- never a bare 500, and never FastAPI's default
``{"detail": ...}`` shape.

*The feedback existence gate (Phase 10 Task 3; POST's own path changed by
Phase 12 Task 1).* ``TranscriptStore``'s own membership check (does
``_messages`` hold a message with this id) is gone along with the class.
:func:`_message_visible` closes that gap with an RLS-filtered ``SELECT 1
FROM messages WHERE id = :id`` -- absent or another user's message both
read back as "not visible," indistinguishable by construction. ``GET``
below still calls it first, unchanged since Phase 10 Task 3: it needs to
tell "unknown message" (404, ``_message_visible`` false) apart from "known
message, no feedback yet" (404, a different detail string) BEFORE ever
asking the store, and :meth:`~poseidon.core.chat.feedback.UserFeedback.get`
itself does not perform that distinction (that method's own docstring).
``POST`` no longer calls it: :meth:`~poseidon.core.chat.feedback.
UserFeedback.upsert` (Phase 12 Task 1) needs the identical RLS-filtered read
anyway, to resolve ``run_id`` from ``messages.turn_id`` (doc 06 section 7) --
resolving ``run_id`` and gating existence are the SAME query there, so a
separate ``_message_visible`` call first would just be the same SELECT
running twice. ``upsert`` raises ``LookupError`` on zero rows, which this
route maps to the SAME 404 ``_message_visible`` already produces, and
:class:`~poseidon.core.chat.feedback.FeedbackNotApplicable` (a VISIBLE
message whose ``turn_id`` is ``NULL`` -- the opener) to a pinned 422
RFC-7807 problem (title ``"feedback_not_applicable"``, the same ``problem()``
constructor :func:`malformed_cursor_response` already renders through).
:func:`_message_visible` is the ONE place this cutover reaches for
:func:`~poseidon.core.db.rls_transaction` directly rather than going through
:class:`~poseidon.core.chat.history.UserHistory`: a bare message id, with no
conversation id to scope a call through, is not a lookup ``UserHistory``'s
shipped interface (Task 2, review closed) can perform -- and reaching into
that class's own private ``_engine``/``_transaction`` from here would cross
the same leading-underscore boundary this codebase treats as a hard rule
elsewhere. ``core/db.py``'s ``rls_transaction`` is public, general-purpose
infrastructure (not private to ``history.py``), and this route already has
everything the call needs -- the shared ``Engine`` (``app.state.db_engine``,
the SAME object ``HistoryStore``/``RunLogWriter``/``FeedbackStore`` hold),
the caller's ``user_sub``, and ``Settings.database_app_role`` -- so no
second connection pool and no new ``History``/``Feedback`` method are
needed to close this one gap.

*Disclosed, scoped gap: ``list_conversations``' item shape.* The brief's own
interface line for ``GET /api/conversations`` asks each item to carry
``title``, ``mode``, ``updated_at``. ``UserHistory.list_conversations``
(Task 2, review closed) queries ``id, title, updated_at`` but returns only
``{"id", "title"}`` per item -- ``mode`` is not even in that SELECT, and
``updated_at`` is read for cursor encoding but dropped before the item dict
is built. Serving the fuller shape would need a ``history.py`` change (add
``mode`` to the SELECT; add both fields to the item dict) that this task's
scope explicitly defers ("no history.py changes ... report BLOCKED with
specifics"). This route therefore serves exactly what ``UserHistory``
returns today -- ``{"id", "title"}`` per item, inside the new ``{"items",
"next_cursor"}`` envelope -- and this gap is reported, with the precise fix
above, as a concern for Task 4/a follow-up rather than worked around by
reaching past ``UserHistory`` (which would replicate the exact SQL/
transaction plumbing this class exists to own in exactly one place) or by
silently shipping a shape the frontend cannot actually rely on.
"""

import json
import logging
import queue
import uuid
from datetime import date
from time import monotonic

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from poseidon.api.auth import rate_limit_chat_send, require_sales
from poseidon.core.chat.events import SseEnvelopeSink, skill_label
from poseidon.core.chat.feedback import FeedbackNotApplicable, FeedbackStore
from poseidon.core.chat.history import (
    DbStateStore,
    HistoryStore,
    MalformedCursor,
    TurnTranscriptBuffer,
    UserHistory,
)
from poseidon.core.chat.orchestrator import TurnOutcome, execute_turn
from poseidon.core.db import rls_transaction
from poseidon.core.llm.titles import title_for
from poseidon.core.runlog import redact_turns_for_conversation
from poseidon.core.skills.registry import SkillRegistry
from poseidon.core.skills.result import problem
from poseidon.core.util.uuid7 import uuid7

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat-live"])

# Phase 11 Task 2 (plan carryforward): every SSE response this router
# returns -- both the real event_stream() below and the throwaway
# snowflake-guard stream -- shares this SAME header set, so it is named
# once here rather than duplicated at each StreamingResponse(...) call
# site. `X-Accel-Buffering: no` tells an nginx (or nginx-compatible)
# reverse proxy sitting in front of this app to never buffer this
# response: nginx's default proxy buffering holds a streamed body until it
# accumulates a full buffer (or the upstream closes), which would turn a
# live, incremental SSE stream into one large delayed chunk delivered all
# at once -- silently defeating progressive streaming for any deploy that
# happens to sit behind such a proxy, with no error or symptom at this
# application layer to point at the cause. A proxy that does not honor
# this header (or is not nginx-based at all) simply ignores it -- there is
# no downside to always sending it.
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

# Sentinel put onto the frame queue once the worker thread's turn is over
# (success or failure alike) -- distinguishable from any real SSE frame
# (always a non-empty str), so the async generator knows when to stop
# draining without a separate "is it done" flag to keep in sync.
_DONE = object()

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk (house rule: backend .py files are ASCII-only) --
# the same convention orchestrator.py's own _EM_DASH constant uses.
_EM_DASH = chr(0x2014)

# An unhandled exception mid-turn (the realistic case once LLM_MODE=live -- a
# Bedrock network hiccup, a database blip inside a skill dispatch, anything
# execute_turn itself does not already turn into a structured `error` frame)
# must still end the stream cleanly, not crash the whole ASGI response with
# an ExceptionGroup and leave the turn_run row orphaned at status='running'
# forever. `title` is shared between the user-facing SSE frame (generic,
# byte-pinned -- never leaks exception internals to the client) and the
# run-log's own `error` dict (which DOES carry the exception class name and
# message, for whoever reads the row later); only `detail` differs.
_INTERNAL_ERROR_TITLE = "internal_error"
_INTERNAL_ERROR_DETAIL = "the turn failed unexpectedly " + _EM_DASH + " the error has been logged"

# The snowflake guard: same title/condition dev_runner.py's own _build_ctx
# uses for the identical misconfiguration -- see the module docstring's
# "The snowflake guard".
_SNOWFLAKE_GUARD_TITLE = "backend not implemented"
_SYNTHETIC_DATA_BACKEND = "synthetic"

# Phase 10 Task 3: MalformedCursor's RFC-7807 title (see malformed_cursor_
# response below).
_MALFORMED_CURSOR_TITLE = "malformed cursor"

# Phase 12 Task 1: FeedbackNotApplicable's RFC-7807 title -- the plan's own
# "pinned code" (doc 06 section 7), read back verbatim by the frontend/tests
# from the problem body's "title" field, the same convention _MALFORMED_
# CURSOR_TITLE/_SNOWFLAKE_GUARD_TITLE/_INTERNAL_ERROR_TITLE above already
# establish (and events.py's own SseEnvelopeSink.emit mirrors for its wire-
# level "code" key: `problem["title"]`, never a separate field).
_FEEDBACK_NOT_APPLICABLE_TITLE = "feedback_not_applicable"

# Envelope fields SseEnvelopeSink._frame adds to EVERY frame -- stripped back
# out by _record_transcript_frame below when folding a frame into a
# transcript part, the same fields the frontend's own applyEventTo strips
# for its "part"/"tool" cases (chatStore.ts).
_ENVELOPE_KEYS = ("turn_id", "message_id", "event_seq")

# Phase 10 Task 3: the feedback existence gate's own SQL -- see the module
# docstring's "The feedback existence gate" for why this runs directly
# through rls_transaction rather than through UserHistory.
_MESSAGE_VISIBLE_SQL = text("SELECT 1 FROM messages WHERE id = :id")

# Phase 11 Task 1: the DELETE route's own delete statement -- see that
# route's own docstring for why it runs this inline (through its own
# rls_transaction, shared with redact_turns_for_conversation) rather than
# calling UserHistory.delete_conversation, whose own rls_transaction is a
# separate, self-contained one.
_DELETE_CONVERSATION_SQL = text("DELETE FROM conversations WHERE id = :id")


def _decode_frame(frame: str) -> tuple[str, dict]:
    """The inverse of ``SseEnvelopeSink._frame``'s wire format (``"id:
    N\\nevent: name\\ndata: {...}\\n\\n"``) -- the same discipline every test
    module in this codebase already uses (``test_chat_orchestrator.py``'s
    own ``_parse_frame``, ``test_mock_chat.py``'s/this file's own
    ``read_sse``): the wire format is pinned byte-for-byte (``events.py``'s
    module docstring), so this decoding is exactly as stable as the format
    itself."""
    _id_line, event_line, data_line = frame[:-2].split("\n")
    name = event_line[len("event: ") :]
    data = json.loads(data_line[len("data: ") :])
    return name, data


def _record_transcript_frame(frame: str, assistant: dict, buffer: TurnTranscriptBuffer) -> None:
    """Fold one already-serialized SSE frame into ``assistant``'s persisted
    parts -- see the module docstring's "Recording the transcript". A
    ``tool`` frame -- EVERY status, not only "done" -- folds through
    ``buffer.record_tool_event`` (both statuses matter so a reloaded
    transcript's part order agrees with the live view's under progressive
    streaming; see that method's own docstring)."""
    name, data = _decode_frame(frame)
    if name == "tool":
        payload = {key: value for key, value in data.items() if key not in _ENVELOPE_KEYS}
        buffer.record_tool_event(assistant, payload)
        return
    if name == "part":
        buffer.append_part(assistant, {"kind": data["kind"], "payload": data["payload"]})
        return
    if name == "token":
        buffer.fold_token(assistant, data["text"])
        return
    # "accepted"/"done"/"error": no persisted part -- mock_chat.py's own
    # comment: "the error is a stream event, not a persisted part".


def _inject_done_title(frame: str, title: str | None, *, replayed: bool = False) -> str:
    """Re-serialize a buffered ``done`` SSE frame with the additive
    ``"title"`` field -- see the module docstring's "Titles at turn one" for
    why this rewrites already-serialized frame text (the same technique
    :func:`_record_transcript_frame` already uses to READ a frame) rather
    than widening ``SseEnvelopeSink``'s own contract, which this task may
    not touch. Mirrors ``SseEnvelopeSink._frame``'s own wire-format
    construction exactly -- same ``id``/``event`` line, ``data`` re-dumped
    with one or two more keys appended.

    ``replayed`` (Phase 11 Task 3, doc 01 section 5's true-replay upgrade):
    unlike ``title`` -- always present, ``str | None`` -- this key is added
    ONLY when ``True``. A duplicate-``client_turn_key`` replay is the one
    and only caller that ever passes ``replayed=True`` (see
    :func:`_finalize_turn`'s own call site, reading ``TurnOutcome.
    replayed``); every ordinary turn's ``done`` frame stays byte-identical
    to what it was before this task -- no new key at all -- which is what
    keeps every PRE-EXISTING test in this codebase that asserts a normal
    turn's exact ``done`` frame shape passing unchanged."""
    name, data = _decode_frame(frame)
    data["title"] = title
    if replayed:
        data["replayed"] = True
    return f"id: {data['event_seq']}\nevent: {name}\ndata: {json.dumps(data)}\n\n"


def _persist_assistant_message(
    user_history: UserHistory, cid: str, assistant: dict, turn_id: str
) -> None:
    """Single insert at stream end -- ``assistant`` is the finished dict a
    :class:`TurnTranscriptBuffer` folded over the course of one turn. Never
    raises: by the time this runs, the turn's own terminal frame (``done``
    or ``error``) has already reached the client, so a persistence failure
    here must not re-terminate an already-finished stream -- the same
    "a second failure must never re-terminate a finished turn" discipline
    ``orchestrator.py``'s own pass-through repopulation guard uses."""
    try:
        user_history.write_assistant_message(cid, assistant, turn_id)
    except Exception as exc:  # noqa: BLE001 - a persistence failure must not break an already-finished stream
        logger.error(
            "failed to persist assistant message: conversation_id=%s message_id=%s: %s: %s",
            cid,
            assistant["id"],
            type(exc).__name__,
            exc,
        )


def _parse_message_id(mid: str) -> uuid.UUID | None:
    """``mid`` as a :class:`uuid.UUID`, or ``None`` if it is not one --
    mirrors ``core/chat/history.py``'s own ``_parse_uuid`` exactly (a
    malformed id can never match a real row, so it is treated exactly like
    an absent one), reproduced here rather than imported: that helper is
    private (leading underscore) to a module this task may not edit."""
    try:
        return uuid.UUID(mid)
    except (ValueError, AttributeError, TypeError):
        return None


def _message_visible(request: Request, mid: str) -> bool:
    """Whether ``mid`` names a message visible to the calling user -- an
    RLS-filtered ``SELECT``, never a membership check against anything held
    in process memory. See the module docstring's "The feedback existence
    gate" for why this runs directly through ``rls_transaction`` (a bare
    message id has no conversation id to scope a ``UserHistory`` call
    through) rather than through ``UserHistory`` itself. A malformed
    ``mid`` reads as "not visible," the same fail-closed treatment
    ``history.py``'s own methods give any malformed id."""
    parsed = _parse_message_id(mid)
    if parsed is None:
        return False
    app_state = request.app.state
    with rls_transaction(
        app_state.db_engine,
        request.state.user.sub,
        app_role=app_state.settings.database_app_role,
    ) as conn:
        return conn.execute(_MESSAGE_VISIBLE_SQL, {"id": str(parsed)}).first() is not None


def malformed_cursor_response(request: Request, exc: MalformedCursor) -> JSONResponse:
    """``app.py``'s registered handler for
    :class:`~poseidon.core.chat.history.MalformedCursor` -- the ONE place a
    cursor decode failure becomes an RFC-7807 body, via the SAME
    :func:`~poseidon.core.skills.result.problem` constructor every other
    problem response in this codebase already renders through (byte-
    identical shape to ``api/auth.py``'s ``AuthError``/``RateLimitExceeded``
    handlers, never FastAPI's default ``{"detail": ...}`` wrapper)."""
    return JSONResponse(status_code=400, content=problem(400, _MALFORMED_CURSOR_TITLE, str(exc)))


class SendBody(BaseModel):
    """Same shape mock_chat.py's own ``SendBody`` accepts."""

    text: str
    client_turn_key: str | None = None


class FeedbackBody(BaseModel):
    """Same shape mock_chat.py's own ``FeedbackBody`` accepts.

    ``verdict: str | None`` -- un-vote follow-up to Phase 12 (migration
    0007): ``None`` clears a previously recorded verdict back to neutral
    (:meth:`~poseidon.core.chat.feedback.UserFeedback.upsert`'s own
    docstring). ``upsert_feedback``'s validation below accepts it as a
    third legitimate value alongside ``"up"``/``"down"``, not an error."""

    verdict: str | None
    comment: str | None = None


@router.post("/conversations", status_code=201, dependencies=[Depends(require_sales)])
def create_conversation(request: Request) -> dict:
    """Same wire shape mock_chat.py's own ``create_conversation`` returns.

    Gated by ``require_sales`` -- this route, and every other
    ``/api/conversations*``/``/api/messages*`` route below, are guarded the
    same way ``/api/skills``/``/api/dev/*`` already are (Global Constraints'
    enforcement scope).
    """
    history_store: HistoryStore = request.app.state.history_store
    user_history = history_store.for_user(request.state.user.sub)
    conversation, opener = user_history.create_conversation()
    return {"conversation": conversation, "opener": opener}


@router.get("/conversations", dependencies=[Depends(require_sales)])
def list_conversations(
    request: Request,
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description=(
            "Page size, 1-200. 200 is a generous ceiling for a conversations "
            "sidebar -- no UI ever needs more per page. Out-of-range values "
            "422 (final-review wave, I-1: limit<=0 used to 500 via an "
            "IndexError in history.py's own pagination slicing)."
        ),
    ),
    cursor: str | None = None,
) -> dict:
    """``{"items": [...], "next_cursor": str|null}`` -- Phase 10 Task 3's
    breaking change from the old bare ``{"conversations": [...]}`` array
    (Task 4, the frontend, follows immediately). ``MalformedCursor`` from a
    bad ``cursor`` propagates to ``app.py``'s registered handler
    (:func:`malformed_cursor_response`) -- never caught here, the same
    "one place this mapping happens" discipline ``AuthError`` uses. See the
    module docstring's disclosed gap: each item is ``{"id", "title"}``
    today, not the fuller ``{"id", "title", "mode", "updated_at"}`` this
    route's own interface eventually wants.
    """
    history_store: HistoryStore = request.app.state.history_store
    user_history = history_store.for_user(request.state.user.sub)
    items, next_cursor = user_history.list_conversations(limit=limit, cursor=cursor)
    return {"items": items, "next_cursor": next_cursor}


@router.get("/conversations/{cid}/messages", dependencies=[Depends(require_sales)])
def get_messages(
    cid: str,
    request: Request,
    limit: int = Query(
        200,
        ge=1,
        le=500,
        description=(
            "Page size, 1-500. 500 is a generous ceiling for a single "
            "transcript page -- larger than any realistic turn count. "
            "Out-of-range values 422 (final-review wave, I-1: same "
            "IndexError defect as list_conversations' own limit)."
        ),
    ),
    cursor: str | None = None,
) -> dict:
    """``{"items": [...], "next_cursor": str|null}`` -- same envelope as
    :func:`list_conversations`. ``None`` from ``UserHistory.get_messages``
    (``cid`` absent, malformed, or another user's -- RLS makes the three
    indistinguishable on purpose) becomes this route's 404, mirroring
    mock_chat.py's own ``get_messages``. A malformed ``cursor`` still
    raises ``MalformedCursor`` even against such a ``cid`` (checked first,
    inside ``UserHistory.get_messages`` itself) -- the client hears about
    the cursor it built, not a 404 for input it also got wrong.
    """
    history_store: HistoryStore = request.app.state.history_store
    user_history = history_store.for_user(request.state.user.sub)
    result = user_history.get_messages(cid, limit=limit, cursor=cursor)
    if result is None:
        raise HTTPException(404, detail="unknown conversation")
    items, next_cursor = result
    return {"items": items, "next_cursor": next_cursor}


@router.delete("/conversations/{cid}", status_code=204, dependencies=[Depends(require_sales)])
def delete_conversation(cid: str, request: Request) -> None:
    """Doc 05 section 7: hard-delete the conversation (``ON DELETE CASCADE``
    removes its messages with it) and, in the SAME transaction, redact the
    linked ``turn_run``/``tool_calls`` audit rows. 404 when ``cid`` is
    malformed, absent, or another user's -- the delete matches zero rows,
    indistinguishable by RLS design, the same convention :func:`get_messages`
    above already uses.

    **Why this opens its own ``rls_transaction`` directly instead of calling
    ``UserHistory.delete_conversation``.** That method (``core/chat/
    history.py``, this same task) is self-contained -- like every other
    method on that class, it opens and commits its own transaction -- so it
    cannot also be the transaction :func:`~poseidon.core.runlog.redact_
    turns_for_conversation` needs to share. This mirrors :func:`_message_
    visible` above's own precedent one section up: a cross-cutting need
    ``UserHistory``'s shipped interface does not fit gets a direct
    ``rls_transaction`` call here, using the same ``app_state.db_engine``/
    ``app_state.settings.database_app_role`` every other direct call in
    this module already reads.

    **Why there is no ``try/except`` around the transaction.** Unlike
    ``RunLogWriter``'s four normal write methods -- never raises, TM1's
    CSV-writer rule, for a best-effort BACKGROUND audit write that must
    never break a chat turn -- a failing redaction here must roll back the
    whole transaction, content delete included: silently returning 204
    while quietly leaving the caller's question/answer text un-redacted in
    the audit trail would violate doc 05 section 7's explicit "deletion
    redacts, unconditionally" contract. Letting the exception propagate is
    therefore correct: it unwinds out of ``rls_transaction``'s own
    ``engine.begin()``, which rolls back the entire transaction -- the
    DELETE included -- exactly the same way any other unhandled exception
    inside a route already becomes a 500 with no special handling in this
    codebase (``api/app.py`` registers no catch-all ``Exception`` handler).
    """
    parsed_cid = _parse_message_id(cid)  # a generic UUID parser despite its name
    if parsed_cid is None:
        raise HTTPException(404, detail="unknown conversation")

    app_state = request.app.state
    with rls_transaction(
        app_state.db_engine,
        request.state.user.sub,
        app_role=app_state.settings.database_app_role,
    ) as conn:
        result = conn.execute(_DELETE_CONVERSATION_SQL, {"id": str(parsed_cid)})
        deleted = result.rowcount > 0
        if deleted:
            redact_turns_for_conversation(conn, str(parsed_cid))

    if not deleted:
        raise HTTPException(404, detail="unknown conversation")


@router.get("/skills", dependencies=[Depends(require_sales)])
def list_skills(request: Request) -> list[dict[str, str]]:
    """``[{id, label, description}]`` for every router-visible skill, in
    ``SkillRegistry.skill_ids`` order (already deterministic -- see that
    property's own docstring). ``label`` is ``events.skill_label`` -- the
    ONE shared home for this derivation -- byte-identical to the existing
    static ``SkillsPicker`` entry for the same skill, and to the SSE
    tool-step label :class:`~poseidon.core.chat.events.SseEnvelopeSink`
    emits.
    """
    registry: SkillRegistry = request.app.state.skill_registry
    return [
        {
            "id": skill_id,
            "label": skill_label(skill_id),
            "description": registry.get(skill_id).description,
        }
        for skill_id in registry.skill_ids
    ]


@router.post(
    "/conversations/{cid}/messages",
    dependencies=[Depends(require_sales), Depends(rate_limit_chat_send)],
)
async def send_message(cid: str, body: SendBody, request: Request) -> StreamingResponse:
    """Drive one real chat turn through ``execute_turn`` and stream its SSE
    frames. See the module docstring for the thread+queue bridge, the
    app-state objects read off ``request.app.state`` below, the snowflake
    guard, the title-at-turn-one mechanism, and how each frame also gets
    folded into the transcript buffer.

    Gated by BOTH ``require_sales`` and ``rate_limit_chat_send``, in that
    ORDER -- an unauthenticated/role-less caller must always get a 401/403
    from ``require_sales`` first, never a 429 from the rate limiter.
    FastAPI resolves a route's ``dependencies=[...]`` list in the order
    given, so this ordering is a direct, load-bearing property of this
    list's literal element order.
    """
    app_state = request.app.state
    settings = app_state.settings
    registry: SkillRegistry = app_state.skill_registry
    history_store: HistoryStore = app_state.history_store
    user_history = history_store.for_user(request.state.user.sub)

    # Phase 10 Task 3: uuid7, never uuid4 -- these ids land in real uuid
    # columns now (module docstring's "Id minting"). turn_id is shared by
    # the user's own message row, the assistant's, and every SSE frame's
    # envelope -- the one value that ties all three together.
    turn_id = str(uuid7())
    message_id = str(uuid7())
    user_message_id = str(uuid7())

    # Recorded before the guard below and before the turn runs -- the user
    # really did send this message. A cid that does not exist, is
    # malformed, or belongs to someone else raises LookupError here (module
    # docstring's "A conversation that does not exist... now 404s at send
    # time too") -- mapped to the SAME 404 GET .../messages already uses.
    #
    # Final-review wave, I-3: this is a synchronous engine.begin() transaction
    # (pool checkout, connect on first use, two statements, commit) called
    # directly in THIS route's own async def body -- unlike every write below,
    # which runs inside run_turn_sync, already bridged onto a worker thread
    # via anyio.to_thread.run_sync (module docstring's "Bridging a synchronous
    # orchestrator into an async stream"). Left synchronous here, it would run
    # on the event loop thread itself: under concurrent turns holding
    # connections from the same QueuePool, a checkout can block for up to the
    # pool's 30-second timeout, freezing every other request in the process
    # (including /health/*) for that whole window. Off-loaded the identical
    # way run_turn_sync itself is, via await anyio.to_thread.run_sync(...),
    # keeping the except LookupError -> 404 mapping around the await: a
    # single to_thread.run_sync call re-raises the wrapped function's own
    # exception to the awaiter directly, never wrapped in an ExceptionGroup
    # (that only happens inside a task group, which this is not).
    try:
        await anyio.to_thread.run_sync(
            user_history.append_user_message, cid, user_message_id, body.text, turn_id
        )
    except LookupError as exc:
        raise HTTPException(404, detail="unknown conversation") from exc

    buffer = TurnTranscriptBuffer()
    assistant = buffer.start_assistant_message(message_id)

    if settings.data_backend != _SYNTHETIC_DATA_BACKEND:
        # Final-review wave, I-3: the SAME class of defect as the call above
        # -- _persist_assistant_message's own write_assistant_message call is
        # a second synchronous transaction reachable directly from this async
        # body (the snowflake-guard path returns before run_turn_sync/the
        # worker-thread bridge is ever set up), so it gets the identical fix.
        await anyio.to_thread.run_sync(
            _persist_assistant_message, user_history, cid, assistant, turn_id
        )
        return _snowflake_guard_response(
            turn_id=turn_id,
            message_id=message_id,
            registry=registry,
            data_backend=settings.data_backend,
        )

    frame_queue: queue.Queue[str | object] = queue.Queue()
    # The turn's ONE `done` frame (if any -- an `error`-terminated turn has
    # none) is held here, not queued immediately, until the real outcome is
    # known -- see the module docstring's "Titles at turn one".
    pending_done: list[str] = []
    # `accepted`'s own turn_index, the ONE other frame this route reads
    # rather than merely folding or forwarding -- TurnOutcome carries no
    # such field.
    turn_index_holder: list[int] = []
    # Fix round 1, Important-3 (persistence-ordering race): set the instant
    # _persist_once actually runs, so a SECOND call (the terminal-frame call
    # site below, racing the `finally` safety net) becomes a no-op instead
    # of a second INSERT against the SAME message_id -- which would violate
    # messages' own primary key. No lock needed: every caller of
    # _persist_once runs on the ONE worker thread this turn's own
    # run_turn_sync call owns -- send()'s calls happen synchronously,
    # inline, from wherever that thread's call stack already is, never from
    # a second thread that could interleave with it.
    persisted_holder: list[bool] = []
    # Phase 12 Task 1: the TurnOutcome, once execute_turn returns -- see the
    # module docstring's "Phase 12 Task 1: replayed messages persist under
    # the ORIGINAL run's id" for why _persist_once reads this instead of
    # unconditionally closing over turn_id.
    outcome_holder: list[TurnOutcome] = []

    def _persist_once() -> None:
        """See the module docstring's "Persistence happens-before the
        terminal frame" -- called from the terminal-frame call sites below
        AND, as a safety net, unconditionally from run_turn_sync's own
        `finally`; idempotent via persisted_holder so at most one INSERT
        ever reaches the database for this turn's assistant message.

        Persists under ``outcome_holder[0].turn_run_id`` when ``execute_
        turn`` has already returned one (module docstring's "Phase 12 Task
        1" section) -- ``turn_id`` (this request's own, freshly minted
        envelope id) otherwise: no writer configured, a mid-turn structured
        failure (this closure runs from ``send``'s own ``"error"`` branch,
        synchronously, DURING ``execute_turn`` -- before ``run_turn_sync``
        ever receives an outcome to record here), or a crash escaping
        ``execute_turn`` entirely.

        ``str(...)``: against a real Postgres, ``RunLogWriter.start_turn``'s
        own ``RETURNING id``/lookup hands back a genuine ``uuid.UUID`` object
        for ``TurnHandle.turn_run_id`` (that dataclass's ``str`` annotation
        notwithstanding -- ``test_runlog_writer.py``'s own pg suite
        documents this exact round trip), never the plain ``str`` the
        closure ``turn_id`` already is -- ``write_assistant_message``'s own
        id parsing expects a string to parse, not an already-parsed
        ``UUID``."""
        if persisted_holder:
            return
        persisted_holder.append(True)
        persisted_turn_id = turn_id
        if outcome_holder and outcome_holder[0].turn_run_id is not None:
            persisted_turn_id = str(outcome_holder[0].turn_run_id)
        _persist_assistant_message(user_history, cid, assistant, persisted_turn_id)

    def send(frame: str) -> None:
        _record_transcript_frame(frame, assistant, buffer)
        name, data = _decode_frame(frame)
        if name == "accepted":
            turn_index_holder.append(data["turn_index"])
        elif name == "done":
            pending_done.append(frame)
            return
        elif name == "error":
            # Fix round 1, Important-3: "error" is ALWAYS this turn's
            # terminal frame (orchestrator.py's own pinned contract: no
            # `done` ever follows an `error`) -- persist BEFORE the client
            # can possibly observe it and race a GET against a row that has
            # not committed yet.
            _persist_once()
            frame_queue.put(frame)
            return
        frame_queue.put(frame)

    sink = SseEnvelopeSink(turn_id=turn_id, message_id=message_id, send=send, registry=registry)

    def _finalize_turn(outcome: TurnOutcome) -> None:
        """Flush the buffered ``done`` frame now that ``execute_turn`` has
        returned -- see the module docstring's "Titles at turn one" for the
        full rationale. A no-op when the turn produced no ``done`` frame at
        all (a duplicate-turn error retry, or a turn-one entry/subject
        clarify that still needs no title).

        ``outcome.replayed`` (Phase 11 Task 3) gates title computation off
        too, defensively, alongside ``turn_index == 1``: a true replay's
        freshly-minted ``turn_index`` is, in every reachable case, at least
        2 (the ORIGINAL turn it replays already consumed an earlier index
        for itself -- ``core/chat/orchestrator.py``'s own ``_begin_turn``
        mints a fresh index unconditionally, before the duplicate check
        ever runs), so this guard is not expected to change behavior on any
        real request today -- it exists so a replayed turn can never, even
        in a theoretical race, trigger a spurious ``title_for``/``set_
        title`` call for content the pipeline never actually produced this
        time. Threaded into :func:`_inject_done_title` either way, so the
        additive ``"replayed": true`` field reaches the wire exactly when
        ``execute_turn`` said this was one -- never guessed at here.
        """
        if not pending_done:
            return
        frame = pending_done[0]
        turn_index = turn_index_holder[0] if turn_index_holder else None
        title = None
        if outcome.status == "ok" and turn_index == 1 and not outcome.replayed:
            try:
                computed = title_for(body.text, app_state.role_client, app_state.prompt_registry)
                title = computed if computed else body.text[:60]
                user_history.set_title(cid, title)
            except Exception as exc:  # noqa: BLE001 - a title failure must not break an already-finished turn's done frame
                logger.error(
                    "title computation failed: conversation_id=%s: %s: %s",
                    cid,
                    type(exc).__name__,
                    exc,
                )
                title = None
        # Fix round 1, Important-3: persist BEFORE queuing the done frame --
        # the client must never be able to see "done" and then GET a
        # conversation whose last message row does not exist yet.
        _persist_once()
        frame_queue.put(_inject_done_title(frame, title, replayed=outcome.replayed))

    def run_turn_sync() -> None:
        started = monotonic()
        try:
            outcome = execute_turn(
                conversation_id=cid,
                # Phase 9 Task 1: request.state.user is set by app.py's
                # identity middleware for EVERY request, ahead of this
                # handler ever running.
                user=request.state.user,
                text=body.text,
                client_turn_key=body.client_turn_key,
                settings=settings,
                registry=registry,
                data=app_state.data_client,
                # Phase 10 Task 3: DbStateStore is signature-identical to
                # the ConversationStateStore execute_turn was written
                # against -- same five method names, same conversation_id
                # parameter name -- so the orchestrator needed zero edits.
                state=DbStateStore(user_history),
                writer=app_state.run_log_writer,
                role_client=app_state.role_client,
                prompt_registry=app_state.prompt_registry,
                sink=sink,
                reference_date=date.today(),
                tools=app_state.tool_registry,
                # Phase 14 final-review wave (C3): the process-wide
                # ArtifactStore app.py's own _wire_live_chat builds for
                # every deploy_mode -- threaded straight through,
                # unexamined here any more than tool_registry above is.
                # Without this line the orchestrator's SkillContext carries
                # artifacts=None on every real HTTP turn and the brief
                # skills' `if ctx.artifacts is not None:` PDF step is dead
                # code on a deployed target (the gate's "artifact download"
                # row could never pass).
                artifacts=app_state.artifact_store,
                # Phase 13 Task 2 (plan amendment, commit bf43d34): the
                # three personalization stores app.py's own _wire_live_chat
                # already puts on app.state -- threaded straight through,
                # unexamined here any more than role_client/data/writer
                # above are, so a real HTTP turn actually gets the real
                # per-turn instruction/memory injection and outbox touch
                # execute_turn itself has been capable of since this task's
                # first commit (fda07f3).
                profile_store=app_state.profile_store,
                memory_store=app_state.memory_store,
                outbox_store=app_state.outbox_store,
            )
        except Exception as exc:  # noqa: BLE001 - a crash mid-turn must still end the stream cleanly
            # execute_turn's own contract only produces a `turn_error` frame
            # for the failures it already recognizes. Anything else
            # escaping here is exactly what doc 01 section 5 has no frame
            # for: this is that frame, deliberately generic so nothing
            # about the exception leaks to the client.
            logger.error(
                "live chat turn failed: conversation_id=%s: %s: %s",
                cid,
                type(exc).__name__,
                exc,
            )
            sink.emit(
                "turn_error",
                {"problem": problem(500, _INTERNAL_ERROR_TITLE, _INTERNAL_ERROR_DETAIL)},
            )
            writer = app_state.run_log_writer
            if writer is not None:
                # turn_run_id is sink.turn_id UNCONDITIONALLY -- correct
                # even when the crash happened before start_turn ever ran:
                # the UPDATE simply matches zero rows, which the
                # never-raises writer absorbs harmlessly. user_sub (Phase 11
                # Task 1, additive): finalize now needs an identity to open
                # rls_transaction with, same as every other writer call site
                # in this module/orchestrator.py -- request.state.user is
                # always set by this point (api/app.py's identity
                # middleware runs ahead of every route).
                writer.finalize(
                    turn_run_id=sink.turn_id,
                    user_sub=request.state.user.sub,
                    status="error",
                    message_id=None,
                    answer_summary=None,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=int((monotonic() - started) * 1000),
                    error=problem(500, _INTERNAL_ERROR_TITLE, f"{type(exc).__name__}: {exc}"),
                )
        else:
            # Phase 12 Task 1: recorded BEFORE _finalize_turn (which is what
            # actually calls _persist_once for the "ok"/"clarify" done-frame
            # path) so persistence can read outcome.turn_run_id -- see
            # _persist_once's own docstring.
            outcome_holder.append(outcome)
            _finalize_turn(outcome)
        finally:
            # Persisted on EVERY path, success or crash alike -- whatever
            # parts the buffer accumulated before a crash still get
            # recorded, mirroring the snowflake guard's own "record what
            # happened before the failure" discipline. Fix round 1,
            # Important-3: this is now a SAFETY NET, not the primary write
            # -- the terminal-frame call sites in send()/_finalize_turn
            # above already persisted before queuing "error"/"done"; this
            # call only does real work on a path that produced NEITHER
            # (unreachable through any code this app controls today, but
            # defensive regardless). _persist_once's own idempotency guard
            # makes this safe to call unconditionally every time.
            _persist_once()
            frame_queue.put(_DONE)

    async def event_stream():
        async with anyio.create_task_group() as tg:
            tg.start_soon(anyio.to_thread.run_sync, run_turn_sync)
            while True:
                frame = await anyio.to_thread.run_sync(frame_queue.get)
                if frame is _DONE:
                    break
                yield frame

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _snowflake_guard_response(
    *, turn_id: str, message_id: str, registry: SkillRegistry, data_backend: str
) -> StreamingResponse:
    """One pinned ``error`` frame, built through a throwaway
    :class:`~poseidon.core.chat.events.SseEnvelopeSink` whose ``send``
    merely appends to a list -- there is no turn to run, so there is
    nothing to bridge into a worker thread. See the module docstring's
    "The snowflake guard" for why this frame carries no preceding
    ``accepted`` and why ``app_state.data_client`` is never read here."""
    frames: list[str] = []
    guard_sink = SseEnvelopeSink(
        turn_id=turn_id, message_id=message_id, send=frames.append, registry=registry
    )
    guard_sink.emit(
        "turn_error",
        {
            "problem": problem(
                501,
                _SNOWFLAKE_GUARD_TITLE,
                f"data_backend={data_backend!r} has no adapter yet - the Snowflake client "
                "arrives Phase 15; live chat only supports data_backend=synthetic today",
            )
        },
    )
    return StreamingResponse(iter(frames), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/messages/{mid}/feedback", status_code=204, dependencies=[Depends(require_sales)])
def upsert_feedback(mid: str, body: FeedbackBody, request: Request) -> Response:
    """See the module docstring's "The feedback existence gate" for why this
    route no longer calls :func:`_message_visible` itself: :meth:`~poseidon.
    core.chat.feedback.UserFeedback.upsert` performs the identical
    RLS-filtered read as its own ``run_id`` resolution and raises
    ``LookupError`` on zero rows, which this route maps to the SAME 404
    ``_message_visible`` used to gate directly.

    Returns a bare :class:`~fastapi.Response` explicitly, on every path
    (never a plain ``None``/dict FastAPI would itself wrap): the ONE success
    shape (204, no body) and the ONE pinned-422 shape both need their own
    status code, and FastAPI passes any ``Response`` instance an endpoint
    returns straight through unchanged, ignoring this decorator's own
    ``status_code=204`` (kept here purely as accurate OpenAPI documentation
    of the default/success case) -- the same pass-through behavior
    :func:`malformed_cursor_response`/:func:`send_message` already rely on.

    ``verdict=None`` (un-vote follow-up to Phase 12, migration 0007) is a
    legitimate request, not an error, on any visible turn-backed message --
    including one with no prior verdict at all (a no-op amend). Nothing
    else about this route's 404/422 handling changes: :meth:`~poseidon.
    core.chat.feedback.UserFeedback.upsert` still raises ``LookupError``/
    :class:`~poseidon.core.chat.feedback.FeedbackNotApplicable` the same
    way regardless of which of the three ``verdict`` values was sent.
    """
    if body.verdict not in ("up", "down", None):
        raise HTTPException(422, detail="verdict must be up, down, or null")
    feedback_store: FeedbackStore = request.app.state.feedback_store
    user_feedback = feedback_store.for_user(request.state.user.sub)
    try:
        user_feedback.upsert(mid, body.verdict, body.comment)
    except LookupError as exc:
        raise HTTPException(404, detail="unknown message") from exc
    except FeedbackNotApplicable as exc:
        return JSONResponse(
            status_code=422,
            content=problem(422, _FEEDBACK_NOT_APPLICABLE_TITLE, str(exc)),
        )
    return Response(status_code=204)


@router.get("/messages/{mid}/feedback", dependencies=[Depends(require_sales)])
def get_feedback(mid: str, request: Request) -> dict:
    if not _message_visible(request, mid):
        raise HTTPException(404, detail="unknown message")
    feedback_store: FeedbackStore = request.app.state.feedback_store
    feedback = feedback_store.for_user(request.state.user.sub).get(mid)
    if feedback is None:
        raise HTTPException(404, detail="no feedback")
    return feedback


__all__ = ["FeedbackBody", "SendBody", "malformed_cursor_response", "router"]
