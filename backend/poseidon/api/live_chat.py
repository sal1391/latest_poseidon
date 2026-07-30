"""Phase 6 Task 4's live chat HTTP surface (doc 01 section 5): the real
``execute_turn`` pipeline (Task 3) behind ``CHAT_MODE=live``, mounted BY
``poseidon.api.app.create_app`` INSTEAD of ``mock_chat.py``'s scripted demo
-- the two routers are never mounted together (see ``app.py``'s own mount
switch). Task 5 amends this module with the live bootstrap routes below,
closing the gap Task 4's own report disclosed (Judgment Call 1 / Concern 1).

Six routes now:

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
  /api/messages/{mid}/feedback`` -- Task 5's own addition, below.

**Task 5 amendment: the live bootstrap routes.** Task 4 shipped only the
two routes above, so a ``chat_mode="live"`` app could not serve the
frontend's own ``bootstrap()`` flow (``createConversation``/
``listConversations``/``getMessages``) end to end -- disclosed there as a
real, intentional scope boundary, not an oversight, but one that meant
"localhost stays demo-able" could not be honored the moment compose flips
``CHAT_MODE=live``. This amendment adds ``mock_chat.py``'s own four
remaining route SHAPES (create/list conversations, transcript, feedback),
backed by :class:`TranscriptStore` -- a minimal in-memory store held
ALONGSIDE :class:`~poseidon.core.chat.state.ConversationStateStore`, never
instead of it: that store holds parse-time :class:`~poseidon.core.skills.
context.ConversationSlots` (what the router reasons about), while
``TranscriptStore`` holds the rendered conversation a human reopens
(conversations, messages, feedback) -- two different shapes of "state,"
never merged into one class. Phase 10 (History + RLS) replaces
``TranscriptStore`` with persisted ``conversations``/``messages`` tables
behind this EXACT SAME four-route surface, the same "same surface, real
backing store" contract ``ConversationStateStore``'s own docstring already
established for Phase 6 as a whole.

The streaming route's own ``cid`` handling is UNCHANGED by this amendment,
on purpose: ``cid`` stays an opaque, unvalidated path segment (mirroring
``ConversationStateStore.get``'s own "unseen id -> empty slots"
permissiveness) rather than 404ing on an id ``TranscriptStore`` has never
seen, the way ``GET .../messages`` below does. This is a disclosed,
deliberate asymmetry, not an inconsistency: every EXISTING test in this
file (Task 4's own) dispatches turns against ad hoc ids like ``"conv-1"``
that were never created via ``POST /api/conversations`` -- rejecting those
would be a silent regression of already-shipped, already-tested behavior
for a case a real frontend session never hits (it always creates first).
Instead, :meth:`TranscriptStore.append_user_message`/
:meth:`~TranscriptStore.start_assistant_message` AUTO-VIVIFY an entry for
whatever ``cid`` the streaming route is handed, so a real session (which
always creates first) and an existing ad hoc test (which does not) both
end up with a transcript ``GET`` can serve -- only a ``cid`` NEITHER route
has ever touched 404s.

**Bridging a synchronous orchestrator into an async stream.**
``execute_turn`` (Task 3) is entirely synchronous -- it calls its sink's
``send`` callable directly, with no ``await`` anywhere in the call chain
(see ``events.py``'s own "Synchronous by construction"). Running it
directly on the asyncio event loop thread would block every other request
for the whole turn. Each request therefore runs ``execute_turn`` in a
worker thread (``anyio.to_thread.run_sync``), and the sink's ``send``
callable pushes each frame onto a plain, thread-safe ``queue.Queue`` that
the async generator drains one item at a time (also via
``anyio.to_thread.run_sync``, so the event loop is never blocked waiting
on it either) -- the "simplest correct equivalent" the brief invites in
place of a lower-level ``anyio.from_thread`` portal, adequate for a turn's
small, bounded frame count.

**App-state wiring** (built once per app, in ``app.py``'s own
``_wire_live_chat``, and read back here per request): ``skill_registry``,
``conversation_state_store``, ``role_client``, ``prompt_registry``,
``data_client``, ``run_log_writer`` (``None`` when ``DATABASE_URL``
could not be turned into an engine -- see ``app.py``'s own
``_build_run_log_writer``), and, since Phase 7 Task 4, ``tool_registry``
(a :class:`~poseidon.mcp.registry.ToolServerRegistry`, threaded to
``execute_turn`` as its own ``tools`` keyword -- see ``app.py``'s own
``_build_tool_registry`` for the ``LLM_MODE``-gated fixture-vs-real
choice). ``data_client`` is built ONCE, unlike
``poseidon.api.dev_runner``'s own per-request ``SyntheticDataClient``
construction: that module's docstring calls the class "cheap to build...
opens no network connection until a query actually runs" precisely because
it holds nothing but a DSN string, which is exactly what makes one shared,
long-lived instance behaviorally identical to a fresh one per request --
there is no connection pool or other per-call state to leak between
requests either way. A test therefore swaps ``app.state.data_client`` for a
fake after construction, the same substitution ``test_dev_runner.py``
already uses for ``app.state.skill_registry``.

**An unhandled exception mid-turn (fix round 1, REQUIRED F1).**
``execute_turn`` already turns every failure ITS OWN pinned contract
recognizes into a structured ``turn_error`` frame (an LLM provider error, a
skill dispatch failure, the duplicate-turn retry short-circuit -- see
``orchestrator.py``'s own module docstring). It does not, and cannot,
promise to catch EVERYTHING -- a genuine bug, or a real network exception a
provider layer failed to normalize into its own ``LLMResponse(stop_
reason="error", ...)`` contract (decision D11's own normalization promise,
not this module's to re-verify), can still escape it. Before this fix,
letting that exception propagate out of ``run_turn_sync`` crashed the whole
``anyio`` task group with an ``ExceptionGroup``, which Starlette's streaming
response machinery turns into a raw ``httpx.RemoteProtocolError`` on the
client side and a crash trace in the server log -- the frontend recovers
(its own ``sendMessage``'s ``finally`` unsticks the composer) but the user
sees a silently truncated answer with no error shown, and the ``turn_run``
row (when a writer is configured) is left orphaned at ``status='running'``
forever, since ``execute_turn``'s own ``finalize`` call is never reached.
``run_turn_sync`` therefore wraps the WHOLE ``execute_turn`` call in one
more ``except Exception`` (not ``BaseException`` -- the same "a cancelled
request must keep unwinding" discipline ``registry.py``'s own ``dispatch``
uses): it logs the failure at ERROR, emits ONE more pinned ``turn_error``
frame (``code="internal_error"``, a generic, byte-pinned message that
never leaks exception internals to the client), and -- when a writer is
configured -- finalizes the run-log row itself, keyed by ``sink.turn_id``
(the turn-id unification amendment is what makes this possible from the
HTTP layer at all: without it, this function would have no id to finalize
against). Calling ``finalize`` unconditionally here is safe even when the
crash happened before ``execute_turn`` ever reached its own ``start_turn``
call: the ``UPDATE ... WHERE id = :turn_run_id`` simply matches zero rows,
which the never-raises writer absorbs exactly as harmlessly as any other
finalize call against an id it does not recognize (``runlog.py``'s own
module docstring). The stream still ends cleanly either way -- the
``finally: frame_queue.put(_DONE)`` below is untouched, so a crash now looks
to the client like any other terminal ``error`` frame, never a broken
connection.

**Recording the transcript (Task 5 amendment).** ``TranscriptStore`` needs
each turn's assistant PARTS, but the only place those exist is already-
serialized SSE frame strings -- ``execute_turn``/``SseEnvelopeSink`` push
wire-format text through the ``send`` callable, never a structured event
object (see ``events.py``'s own "Synchronous by construction": this keeps
that module importing nothing async and knowing nothing about a caller's
storage needs). Rather than widen ``SseEnvelopeSink``'s contract to also
hand back structured data -- a sanctioned-file boundary this task does not
cross -- :func:`_record_transcript_frame` decodes each frame the SAME way
every test module in this codebase already does (``_parse_frame``/
``read_sse``'s own line-splitting discipline: ``events.py``'s wire format
is pinned byte-for-byte, so this decoding is exactly as stable as the
format itself), then folds it into the assistant message's parts under
``mock_chat.py``'s OWN accumulation rules: a ``tool`` frame with
``status="done"`` becomes one ``tool_event`` part (envelope fields
stripped, matching mock's un-enveloped ``tool_event`` payload shape
exactly); a ``part`` frame is appended verbatim (kind + payload, envelope
stripped -- the same fields the frontend's own ``applyEventTo`` keeps for
its "part" case); a ``token`` frame folds into the trailing text part, or
starts a new one, mirroring the frontend's own token-folding rule (never
mock's, since mock's illustrative multi-chunk demo has no live equivalent
to match -- see ``orchestrator.py``'s own "never chunked" note); an
``accepted``/``done``/``error`` frame persists nothing, exactly mirroring
mock's own comment that "the error is a stream event, not a persisted
part." This runs inside ``send`` itself (called synchronously, from
whichever thread is running the turn), so recording is complete by the
time the client sees the final frame -- there is no separate "at done-time"
step to race.

**The snowflake guard (Task 5 amendment).** ``dev_runner.py``'s own
``_build_ctx`` refuses ``data_backend != "synthetic"`` with a structured
501 BEFORE ever constructing a client to query with -- Task 4 disclosed
that live chat had no equivalent (that task's own report, Judgment Call 3 /
Concern 2): ``app.state.data_client`` is always a ``SyntheticDataClient``
regardless of ``settings.data_backend``, so a ``CHAT_MODE=live`` deploy
misconfigured with ``DATA_BACKEND=snowflake`` before Phase 15 ships a real
adapter would silently answer from the WRONG schema instead of failing
loudly. ``send_message`` now checks ``settings.data_backend`` first, before
building the worker-thread/queue machinery and before ``execute_turn`` (and
therefore ``parse_turn``'s own ``data.list_dimension_values`` calls) could
ever touch ``app.state.data_client`` at all -- mirroring dev_runner's exact
guard condition and ``problem()`` shape (``501``, title ``"backend not
implemented"``), adapted to this endpoint's SSE contract instead of a plain
JSON body: one ``turn_error`` frame, built through a THROWAWAY
:class:`~poseidon.core.chat.events.SseEnvelopeSink` (the same class, a
fresh instance, since the real one is never constructed on this path) whose
``send`` merely appends to a list instead of bridging a worker thread --
there is no turn to run, so there is nothing to bridge. This frame carries
no preceding ``accepted``: unlike every other terminal path in
``orchestrator.py``, this guard fires before any turn begins at all, so
there is no ``turn_index`` to report and nothing dishonest about skipping a
step that never started (the frontend's own ``applyEventTo`` is
replay-safe regardless -- "an event for an unseen message creates it").
The user's message and an (empty) assistant message are still recorded
into the transcript first, the same as every other path through
``send_message`` -- the user really did send it, and this mirrors how the
internal-error crash path also leaves behind an assistant message with
whatever parts existed before the crash.
"""

import json
import logging
import queue
import threading
import uuid
from datetime import date
from time import monotonic

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from poseidon.core.chat.events import SseEnvelopeSink, skill_label
from poseidon.core.chat.orchestrator import execute_turn
from poseidon.core.skills.registry import SkillRegistry
from poseidon.core.skills.result import problem

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat-live"])

# Sentinel put onto the frame queue once the worker thread's turn is over
# (success or failure alike) -- distinguishable from any real SSE frame
# (always a non-empty str), so the async generator knows when to stop
# draining without a separate "is it done" flag to keep in sync.
_DONE = object()

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk (house rule: backend .py files are ASCII-only) --
# the same convention orchestrator.py's own _EM_DASH constant uses.
_EM_DASH = chr(0x2014)

# Fix round 1, REQUIRED F1: an unhandled exception mid-turn (the realistic
# case once LLM_MODE=live -- a Bedrock network hiccup, a database blip inside
# a skill dispatch, anything execute_turn itself does not already turn into a
# structured `error` frame) must still end the stream cleanly, not crash the
# whole ASGI response with an ExceptionGroup and leave the turn_run row
# orphaned at status='running' forever. `title` is shared between the
# user-facing SSE frame (generic, byte-pinned -- never leaks exception
# internals to the client) and the run-log's own `error` dict (which DOES
# carry the exception class name and message, for whoever reads the row
# later); only `detail` differs between the two uses.
_INTERNAL_ERROR_TITLE = "internal_error"
_INTERNAL_ERROR_DETAIL = "the turn failed unexpectedly " + _EM_DASH + " the error has been logged"

# Task 5 amendment: the snowflake guard. Same title/condition dev_runner.py's
# own _build_ctx uses for the identical misconfiguration -- see the module
# docstring's "The snowflake guard".
_SNOWFLAKE_GUARD_TITLE = "backend not implemented"
_SYNTHETIC_DATA_BACKEND = "synthetic"

# Envelope fields SseEnvelopeSink._frame adds to EVERY frame -- stripped back
# out by _record_transcript_frame below when folding a frame into a
# transcript part, the same fields the frontend's own applyEventTo strips
# for its "part"/"tool" cases (chatStore.ts).
_ENVELOPE_KEYS = ("turn_id", "message_id", "event_seq")


def _message(role: str, parts: list[dict]) -> dict:
    """Same shape mock_chat.py's own ``_message`` builds -- a fresh id per
    message, never reused across conversations."""
    return {"id": str(uuid.uuid4()), "role": role, "parts": parts}


class TranscriptStore:
    """Task 5 amendment: the minimal in-memory conversation+message+feedback
    store backing the live bootstrap routes -- see the module docstring's
    "Task 5 amendment: the live bootstrap routes" for how this relates to
    :class:`~poseidon.core.chat.state.ConversationStateStore`. Phase 10
    (History + RLS) replaces this class with persisted ``conversations``/
    ``messages`` tables behind the exact same surface used below: ``create_
    conversation``/``list_conversations``/``get_messages``/``append_user_
    message``/``start_assistant_message``/``append_part``/``fold_token``/
    ``upsert_feedback``/``get_feedback``.

    One :class:`threading.Lock` guards every dict below -- the same
    "uvicorn workers=1 in dev; a plain dict + lock is correct enough"
    discipline :class:`~poseidon.core.chat.state.ConversationStateStore`'s
    own docstring already established for this exact deployment shape.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conversations: dict[str, dict] = {}
        self._messages: dict[str, list[dict]] = {}
        self._feedback: dict[str, dict] = {}

    def create_conversation(self) -> tuple[dict, dict]:
        """Same opener mock_chat.py's own ``create_conversation`` builds --
        Phase 6 has no live-specific flow-branching content of its own yet
        (Phase 8 owns the two brief flows the chips name), so this amendment
        does not invent different copy for a distinction that does not
        exist yet."""
        cid = str(uuid.uuid4())
        conversation = {"id": cid, "title": "New chat"}
        opener = _message(
            "assistant",
            [
                {"kind": "text", "payload": {"markdown": "Ask about your data, or pick a flow:"}},
                {
                    "kind": "chips",
                    "payload": {
                        "options": [
                            {"id": "existing_customer", "label": "Existing customer"},
                            {"id": "new_prospect", "label": "New customer prospect"},
                        ]
                    },
                },
            ],
        )
        with self._lock:
            self._conversations[cid] = conversation
            self._messages[cid] = [opener]
        return conversation, opener

    def list_conversations(self) -> list[dict]:
        """Newest first -- same ordering mock_chat.py's own
        ``list_conversations`` returns."""
        with self._lock:
            return list(reversed(list(self._conversations.values())))

    def get_messages(self, cid: str) -> list[dict] | None:
        """``None`` for a ``cid`` neither :meth:`create_conversation` nor
        the streaming route's own auto-vivification (see
        :meth:`append_user_message`) has ever touched -- the route above
        turns that into a 404, mirroring mock_chat.py's own ``get_messages``."""
        with self._lock:
            existing = self._messages.get(cid)
            return None if existing is None else list(existing)

    def append_user_message(self, cid: str, text: str) -> None:
        """Auto-vivifies ``cid`` if this is the first time anything has
        touched it -- see the module docstring's disclosed streaming-route
        asymmetry."""
        message = _message("user", [{"kind": "text", "payload": {"markdown": text}}])
        with self._lock:
            self._messages.setdefault(cid, []).append(message)

    def start_assistant_message(self, cid: str, message_id: str) -> dict:
        """Registers an (initially empty) assistant message and returns the
        SAME mutable dict :meth:`append_part`/:meth:`fold_token` fill in
        place as the turn runs -- mirroring mock_chat.py's own "register the
        assistant message before the first frame goes out, then fill it in
        place" comment verbatim."""
        assistant = {"id": message_id, "role": "assistant", "parts": []}
        with self._lock:
            self._messages.setdefault(cid, []).append(assistant)
        return assistant

    def append_part(self, assistant: dict, part: dict) -> None:
        with self._lock:
            assistant["parts"].append(part)

    def fold_token(self, assistant: dict, text: str) -> None:
        """A ``token`` frame's text folds into the trailing text part, or
        starts a new one -- the same rule the frontend's own
        ``applyEventTo`` uses for its "token" case (chatStore.ts), not
        mock_chat.py's illustrative multi-chunk demo, since live text
        arrives in exactly one chunk today (see ``orchestrator.py``'s own
        "never chunked" note) but this stays correct if that ever changes."""
        with self._lock:
            parts = assistant["parts"]
            if parts and parts[-1]["kind"] == "text":
                parts[-1] = {
                    "kind": "text",
                    "payload": {"markdown": parts[-1]["payload"]["markdown"] + text},
                }
            else:
                parts.append({"kind": "text", "payload": {"markdown": text}})

    def upsert_feedback(self, mid: str, verdict: str, comment: str | None) -> bool:
        """``False`` when ``mid`` names no message this store has ever
        recorded -- the route above turns that into a 404, mirroring
        mock_chat.py's own ``_known_message`` guard."""
        with self._lock:
            if not any(m["id"] == mid for msgs in self._messages.values() for m in msgs):
                return False
            self._feedback[mid] = {"verdict": verdict, "comment": comment}
            return True

    def get_feedback(self, mid: str) -> dict | None:
        with self._lock:
            return self._feedback.get(mid)


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


def _record_transcript_frame(frame: str, assistant: dict, store: TranscriptStore) -> None:
    """Fold one already-serialized SSE frame into ``assistant``'s persisted
    parts -- see the module docstring's "Recording the transcript" for the
    accumulation rules this implements (mock_chat.py's own rules for
    ``tool``/``part``/everything-else; the frontend's own token-folding
    rule, since mock's multi-chunk demo has no live equivalent)."""
    name, data = _decode_frame(frame)
    if name == "tool":
        if data["status"] == "done":
            payload = {key: value for key, value in data.items() if key not in _ENVELOPE_KEYS}
            store.append_part(assistant, {"kind": "tool_event", "payload": payload})
        return
    if name == "part":
        store.append_part(assistant, {"kind": data["kind"], "payload": data["payload"]})
        return
    if name == "token":
        store.fold_token(assistant, data["text"])
        return
    # "accepted"/"done"/"error": no persisted part -- mock_chat.py's own
    # comment: "the error is a stream event, not a persisted part".


class SendBody(BaseModel):
    """Same shape mock_chat.py's own ``SendBody`` accepts."""

    text: str
    client_turn_key: str | None = None


class FeedbackBody(BaseModel):
    """Same shape mock_chat.py's own ``FeedbackBody`` accepts."""

    verdict: str
    comment: str | None = None


@router.post("/conversations", status_code=201)
def create_conversation(request: Request) -> dict:
    """Same wire shape mock_chat.py's own ``create_conversation`` returns --
    see the module docstring's "Task 5 amendment: the live bootstrap
    routes"."""
    store: TranscriptStore = request.app.state.transcript_store
    conversation, opener = store.create_conversation()
    return {"conversation": conversation, "opener": opener}


@router.get("/conversations")
def list_conversations(request: Request) -> dict:
    store: TranscriptStore = request.app.state.transcript_store
    return {"conversations": store.list_conversations()}


@router.get("/conversations/{cid}/messages")
def get_messages(cid: str, request: Request) -> dict:
    store: TranscriptStore = request.app.state.transcript_store
    messages = store.get_messages(cid)
    if messages is None:
        raise HTTPException(404, detail="unknown conversation")
    return {"messages": messages}


@router.get("/skills")
def list_skills(request: Request) -> list[dict[str, str]]:
    """``[{id, label, description}]`` for every router-visible skill, in
    ``SkillRegistry.skill_ids`` order (already deterministic -- see that
    property's own docstring). ``label`` is ``events.skill_label`` (final-
    review wave item 3 / I4: the ONE shared home for this derivation --
    byte-identical to the existing static ``SkillsPicker`` entry for the
    same skill, and to the SSE tool-step label
    :class:`~poseidon.core.chat.events.SseEnvelopeSink` now emits)."""
    registry: SkillRegistry = request.app.state.skill_registry
    return [
        {
            "id": skill_id,
            "label": skill_label(skill_id),
            "description": registry.get(skill_id).description,
        }
        for skill_id in registry.skill_ids
    ]


@router.post("/conversations/{cid}/messages")
async def send_message(cid: str, body: SendBody, request: Request) -> StreamingResponse:
    """Drive one real chat turn through ``execute_turn`` and stream its SSE
    frames. See the module docstring for the thread+queue bridge, the
    app-state objects read off ``request.app.state`` below, the snowflake
    guard, and how each frame also gets folded into the transcript.
    """
    app_state = request.app.state
    settings = app_state.settings
    registry: SkillRegistry = app_state.skill_registry
    store: TranscriptStore = app_state.transcript_store

    turn_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())

    # Recorded unconditionally, before the guard below and before the turn
    # runs -- the user really did send this message, mirroring
    # mock_chat.py's own unconditional append at the top of its send_message
    # (see the module docstring's "The snowflake guard": the empty assistant
    # message left behind here is the same shape the internal-error crash
    # path below also produces).
    store.append_user_message(cid, body.text)
    assistant = store.start_assistant_message(cid, message_id)

    if settings.data_backend != _SYNTHETIC_DATA_BACKEND:
        return _snowflake_guard_response(
            turn_id=turn_id,
            message_id=message_id,
            registry=registry,
            data_backend=settings.data_backend,
        )

    frame_queue: queue.Queue[str | object] = queue.Queue()

    def send(frame: str) -> None:
        _record_transcript_frame(frame, assistant, store)
        frame_queue.put(frame)

    sink = SseEnvelopeSink(turn_id=turn_id, message_id=message_id, send=send, registry=registry)

    def run_turn_sync() -> None:
        started = monotonic()
        try:
            execute_turn(
                conversation_id=cid,
                text=body.text,
                client_turn_key=body.client_turn_key,
                settings=settings,
                registry=registry,
                data=app_state.data_client,
                state=app_state.conversation_state_store,
                writer=app_state.run_log_writer,
                role_client=app_state.role_client,
                prompt_registry=app_state.prompt_registry,
                sink=sink,
                reference_date=date.today(),
                # Phase 7 Task 4: app.py's own _wire_live_chat builds this
                # once per app -- a FixtureResearchTool override under
                # LLM_MODE=stub, a real Perplexity transport resolved lazily
                # per TOOL_TRANSPORT_PERPLEXITY under LLM_MODE=live.
                tools=app_state.tool_registry,
            )
        except Exception as exc:  # noqa: BLE001 - a crash mid-turn must still end the stream cleanly
            # execute_turn's own contract only produces a `turn_error` frame
            # for the failures it already recognizes (an LLM provider error, a
            # skill dispatch it can structure -- see loop.py's own "structured
            # failures"). Anything else escaping here -- a genuine bug, a
            # network exception the provider layer did not itself catch -- is
            # exactly what mock_chat.py never had to handle (its own turn is a
            # canned script that cannot fail this way) and doc 01 section 5
            # has no frame for: this is that frame, deliberately generic so
            # nothing about the exception leaks to the client.
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
                # turn_run_id is sink.turn_id UNCONDITIONALLY (the turn-id
                # unification amendment) -- correct even when the crash
                # happened before start_turn ever ran: the UPDATE simply
                # matches zero rows, which the never-raises writer absorbs
                # harmlessly (runlog.py's own module docstring), never a
                # doomed insert against a row that was never created.
                writer.finalize(
                    turn_run_id=sink.turn_id,
                    status="error",
                    message_id=None,
                    answer_summary=None,
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=int((monotonic() - started) * 1000),
                    error=problem(500, _INTERNAL_ERROR_TITLE, f"{type(exc).__name__}: {exc}"),
                )
        finally:
            frame_queue.put(_DONE)

    async def event_stream():
        async with anyio.create_task_group() as tg:
            tg.start_soon(anyio.to_thread.run_sync, run_turn_sync)
            while True:
                frame = await anyio.to_thread.run_sync(frame_queue.get)
                if frame is _DONE:
                    break
                yield frame

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


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
    return StreamingResponse(
        iter(frames), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.post("/messages/{mid}/feedback", status_code=204)
def upsert_feedback(mid: str, body: FeedbackBody, request: Request) -> None:
    if body.verdict not in ("up", "down"):
        raise HTTPException(422, detail="verdict must be up or down")
    store: TranscriptStore = request.app.state.transcript_store
    if not store.upsert_feedback(mid, body.verdict, body.comment):
        raise HTTPException(404, detail="unknown message")


@router.get("/messages/{mid}/feedback")
def get_feedback(mid: str, request: Request) -> dict:
    store: TranscriptStore = request.app.state.transcript_store
    feedback = store.get_feedback(mid)
    if feedback is None:
        raise HTTPException(404, detail="no feedback")
    return feedback


__all__ = ["FeedbackBody", "SendBody", "TranscriptStore", "router"]
