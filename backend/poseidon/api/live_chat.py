"""Phase 6 Task 4's live chat HTTP surface (doc 01 section 5): the real
``execute_turn`` pipeline (Task 3) behind ``CHAT_MODE=live``, mounted BY
``poseidon.api.app.create_app`` INSTEAD of ``mock_chat.py``'s scripted demo
-- the two routers are never mounted together (see ``app.py``'s own mount
switch).

Two routes, deliberately narrow -- this task's own pinned scope:

- ``POST /api/conversations/{cid}/messages`` drives ONE real chat turn
  through :func:`~poseidon.core.chat.orchestrator.execute_turn` and streams
  doc 01 section 5's SSE envelope exactly as ``mock_chat.py``'s own
  ``_sse()`` already does (byte-identical wire format -- the frontend's
  parser is not this task's to touch; see ``events.py``'s module
  docstring). Same path, same request/response shape as the mock's own
  ``send_message``.
- ``GET /api/skills`` reports the registry's real, router-visible skills
  -- ``[{id, label, description}]`` -- for the frontend's ``SkillsPicker``.

**What this module deliberately does NOT implement.** Conversation
creation/listing, transcript retrieval and feedback -- ``mock_chat.py``'s
other four routes -- have no live equivalent here. ``ConversationStateStore``
is an in-memory ``conversation_id -> slots`` map (see ``core/chat/state.py``'s
own module docstring); there is no ``conversations`` table, no persisted
transcript and no feedback store until Phase 10 (History + RLS) lands real
persistence. This is a disclosed Task 4 scope boundary, not an oversight:
the brief pins exactly the two routes above, so a live-mode app cannot yet
serve the frontend's own bootstrap flow (``createConversation``/
``listConversations``) end to end -- proving ``execute_turn``'s HTTP wiring
is this task's job, not completing conversation persistence. ``cid`` is
therefore accepted as an opaque path segment and never validated against
anything (mirroring ``ConversationStateStore.get``'s own "unseen id ->
empty slots" permissiveness, since nothing here owns a conversation
registry to validate against).

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
``data_client`` and ``run_log_writer`` (``None`` when ``DATABASE_URL``
could not be turned into an engine -- see ``app.py``'s own
``_build_run_log_writer``). ``data_client`` is built ONCE, unlike
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
"""

import logging
import queue
import uuid
from datetime import date
from time import monotonic

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from poseidon.core.chat.events import SseEnvelopeSink
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


class SendBody(BaseModel):
    """Same shape mock_chat.py's own ``SendBody`` accepts."""

    text: str
    client_turn_key: str | None = None


def _label(skill_id: str) -> str:
    """Human-readable label derived from a skill id -- SKILL_META itself
    carries no such field (only ``description``/``examples``; see
    ``registry.py``'s own ``_register``), so this derives one the same way
    the frontend's own static skills list already names things: the skill's
    bare name (the segment after the last dot -- the task prefix dropped),
    underscores as spaces, first letter capitalized
    (``"data_qa.metric_query"`` -> ``"metric_query"`` -> ``"Metric query"``,
    byte-identical to the existing static ``SkillsPicker`` entry for the
    same skill).
    """
    name = skill_id.rpartition(".")[2]
    return name.replace("_", " ").capitalize()


@router.get("/skills")
def list_skills(request: Request) -> list[dict[str, str]]:
    """``[{id, label, description}]`` for every router-visible skill, in
    ``SkillRegistry.skill_ids`` order (already deterministic -- see that
    property's own docstring)."""
    registry: SkillRegistry = request.app.state.skill_registry
    return [
        {
            "id": skill_id,
            "label": _label(skill_id),
            "description": registry.get(skill_id).description,
        }
        for skill_id in registry.skill_ids
    ]


@router.post("/conversations/{cid}/messages")
async def send_message(cid: str, body: SendBody, request: Request) -> StreamingResponse:
    """Drive one real chat turn through ``execute_turn`` and stream its SSE
    frames. See the module docstring for the thread+queue bridge and the
    app-state objects read off ``request.app.state`` below.
    """
    app_state = request.app.state
    settings = app_state.settings
    registry: SkillRegistry = app_state.skill_registry

    turn_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    frame_queue: queue.Queue[str | object] = queue.Queue()

    def send(frame: str) -> None:
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


__all__ = ["SendBody", "router"]
