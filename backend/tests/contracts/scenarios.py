"""The turn contract corpus's scenario definitions (Phase 16 Task 10, spec
section 11).

Each scenario drives the REAL shipped stack -- ``execute_turn`` over the
offline doubles ``tests/test_chat_orchestrator.py`` already owns, or
``SseEnvelopeSink`` directly -- captures the raw SSE frames it emitted, and
folds those SAME frames through ``api/live_chat.py``'s own transcript fold
(:func:`~poseidon.api.live_chat._record_transcript_frame` +
:class:`~poseidon.core.chat.history.TurnTranscriptBuffer`) to obtain the
parts the public route would have persisted for that turn. Nothing here
re-implements either side: the corpus pins what the shipped code actually
does, so a Next.js translation (Task 11) that matches these files matches
Python, not a second opinion about Python.

Why the arrange blocks are COPIED rather than imported: the four
orchestrator scenarios below reproduce the arrange code of four named tests
in ``tests/test_chat_orchestrator.py`` (each named in its own docstring),
but never call those test functions -- a test function is an assertion
script, not a fixture factory, and importing one to harvest its side
effects would couple the corpus to assertions that are free to change. The
module-level HELPERS those tests use (``REGISTRY``, ``FakeDataClient``,
``RecordingWriter``, ``_settings``, ``_dev_role_client``,
``_capturing_send``, ``_sink``, ``REFERENCE_DATE``) ARE imported: they are
the offline doubles themselves, and duplicating them would be the drift
this corpus exists to prevent. That import direction (a test module
importing another test module's doubles) is the precedent every sibling
suite already follows -- ``test_live_chat_sse.py``, ``test_internal_turns.
py``, ``test_entry_orchestration.py`` and ``test_api_auth.py`` all import
``FakeDataClient``/``RecordingWriter`` from ``test_chat_orchestrator``.

Determinism: every scenario pins its own ids (``turn-1``/``msg-1``, and
``turn-2``/``msg-2`` for the second turn of the replay scenario), passes
the fixed ``REFERENCE_DATE`` rather than letting anything reach for today,
and runs entirely over in-memory doubles -- no uuid, no clock, no
presigned URL, no database reaches any frame. ``to_json`` therefore
normalises NOTHING; if a future scenario ever introduces a value that
varies run to run, normalise it there and document it in
``contracts/turns/README.md``.

Everything here is OFFLINE: the same hand-rolled ``FakeDataClient`` /
``RecordingWriter`` / ``DevDeterministicRouter`` / ``StubProvider`` stack
``test_chat_orchestrator.py``'s own module docstring describes -- no
network, no database, no provider call.
"""

import itertools
from collections.abc import Callable
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from poseidon.api.live_chat import _decode_frame, _record_transcript_frame
from poseidon.core.chat.events import SseEnvelopeSink
from poseidon.core.chat.history import TurnTranscriptBuffer
from poseidon.core.chat.orchestrator import execute_turn
from poseidon.core.chat.report_flow import ENTRY_PHRASE_REPORT
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.identity import DISABLED_DEFAULT_USER
from poseidon.core.llm.prompt_tree import PromptTree
from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.stub import StubProvider
from poseidon.core.llm.types import LLMResponse, ToolCall
from poseidon.core.runlog import ReplayTurn
from poseidon.tasks.reporting.skills.monthly_performance import skill as skill_module
from poseidon.tasks.reporting.skills.monthly_performance.tests.fake_client import (
    FakeDataClient as ReportFakeDataClient,
)
from poseidon.tasks.reporting.skills.monthly_performance.tests.fake_client import (
    FakeReportStore,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import frames as report_frames
from poseidon.tasks.reporting.skills.monthly_performance.tools import render
from tests.test_chat_orchestrator import (
    REFERENCE_DATE,
    REGISTRY,
    FakeDataClient,
    RecordingWriter,
    _capturing_send,
    _dev_role_client,
    _settings,
    _sink,
)

# ``(raw SSE frames, persisted assistant parts)`` -- the two halves of one
# scenario's evidence, exactly as the golden test writes them out.
Recorded = tuple[list[str], list[dict]]

# The skill id every tool frame in this corpus names -- the one real skill
# the flagship turn dispatches, reused by the sink-level scenario so its
# ``label`` is a real humanized label ("Metric query"), not an invented one.
METRIC_QUERY = "data_qa.metric_query"


def _fold(frames: list[str], message_id: str) -> list[dict]:
    """The persisted parts for a turn, produced by the PUBLIC ROUTE'S OWN
    fold -- ``TurnTranscriptBuffer.start_assistant_message`` then
    :func:`_record_transcript_frame` over every frame, exactly as
    ``api/live_chat.py``'s ``stream_turn`` does (its own ``buffer =
    TurnTranscriptBuffer()`` / ``assistant = buffer.start_assistant_
    message(message_id)`` / ``_record_transcript_frame(frame, assistant,
    buffer)`` sequence). Calling the shipped fold rather than re-deriving
    parts from the frames is the whole point: the corpus pins reality.
    """
    buffer = TurnTranscriptBuffer()
    assistant = buffer.start_assistant_message(message_id)
    for frame in frames:
        _record_transcript_frame(frame, assistant, buffer)
    return assistant["parts"]


def to_json(frames: list[str], parts: list[dict]) -> dict:
    """The JSON body of one scenario's golden file (minus the ``scenario``
    key the golden test adds): every frame decoded through
    ``live_chat.py``'s own :func:`_decode_frame`, the persisted parts, and
    the turn's terminal frame broken out so a consumer can assert on it
    without re-scanning the list. ``terminal`` is the LAST frame, which is
    ``done`` on every ok/clarify/replay path and ``error`` on the error
    path (an error frame is its own terminal signal -- no ``done`` follows
    it; see ``_record_transcript_frame``'s own comment).
    """
    decoded = [{"event": name, "data": data} for name, data in map(_decode_frame, frames)]
    return {"frames": decoded, "parts": parts, "terminal": decoded[-1]}


# ===========================================================================
# Orchestrator scenarios -- execute_turn over the offline doubles
# ===========================================================================


def metric_query_flagship(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """The flagship Singapore top-GP turn. Arrange copied from
    ``test_chat_orchestrator.py::test_flagship_singapore_top_gp_frame_
    sequence_and_writer_rows``. Covers accepted, tool start/done, a table
    part, a proof part, a text token and done.
    """
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="turn-1", message_id="msg-1", send=send, registry=REGISTRY)

    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-1",
        text="Top GP customers for Port of Singapore in April 2026",
        client_turn_key="ctk-1",
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=sink,
        reference_date=REFERENCE_DATE,
    )
    return frames, _fold(frames, sink.message_id)


def clarify_ambiguous_customer(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """An ambiguous customer name ("Meridiann") clarifying with chips plus
    text and no dispatch. Arrange copied from ``test_chat_orchestrator.py::
    test_ambiguous_turn_produces_chips_and_text_parts_clarify_status_no_
    dispatch`` (ids widened from ``t``/``m`` to this corpus's uniform
    ``turn-1``/``msg-1``).
    """
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="turn-1", message_id="msg-1", send=send, registry=REGISTRY)

    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-amb",
        text="gp for Meridiann in April 2026",
        client_turn_key=None,
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=sink,
        reference_date=REFERENCE_DATE,
    )
    return frames, _fold(frames, sink.message_id)


def error_mid_turn(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """An unknown skill, scripted twice, failing the turn: two tool
    start/done pairs then an ``error`` frame and NO ``done``. Arrange copied
    from ``test_chat_orchestrator.py::test_error_turn_emits_error_event_and_
    finalizes_error_state_untouched`` (ids widened from ``t``/``m``).
    """
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    script = [
        LLMResponse(
            text="",
            tool_calls=(ToolCall(id="oops-1", name="data_qa.made_up", arguments={}),),
            stop_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        ),
        LLMResponse(
            text="",
            tool_calls=(ToolCall(id="oops-2", name="data_qa.made_up", arguments={}),),
            stop_reason="tool_use",
            input_tokens=10,
            output_tokens=5,
        ),
    ]
    role_client = RoleClient(settings, providers={"stub": StubProvider(script)})
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="turn-1", message_id="msg-1", send=send, registry=REGISTRY)

    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-err",
        text="Top GP customers for Port of Singapore in April 2026",
        client_turn_key=None,
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=sink,
        reference_date=REFERENCE_DATE,
    )
    return frames, _fold(frames, sink.message_id)


def replay_after_ok(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """A duplicate ``client_turn_key`` whose original turn finished ``ok``:
    the RETRY replays the original turn's persisted parts and a ``done``,
    never the ``duplicate_turn`` error. Arrange copied from
    ``test_chat_orchestrator.py::test_retry_after_an_ok_turn_replays_the_
    persisted_parts_instead_of_erroring`` (ids widened from ``turn-A``/
    ``msg-A`` and ``turn-B``/``msg-B`` to ``turn-1``/``msg-1`` and
    ``turn-2``/``msg-2``).

    The RETRY's frames are what this scenario records -- the first turn runs
    only to establish the duplicate key, and its own frames (already pinned
    by ``metric_query_flagship``, modulo the conversation id) are dropped.
    """
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    seeded_parts = [
        {"kind": "text", "payload": {"markdown": "the cached answer from turn one"}},
        {"kind": "proof", "payload": {"lines": ["Backend: synthetic"]}},
    ]
    writer = RecordingWriter(
        replay_by_turn_run_id={"turn-1": ReplayTurn(status="ok", parts=seeded_parts)}
    )
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    first_frames, first_send = _capturing_send()
    first_sink = SseEnvelopeSink(
        turn_id="turn-1", message_id="msg-1", send=first_send, registry=REGISTRY
    )
    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-replay",
        text="Top GP customers for Port of Singapore in April 2026",
        client_turn_key="ctk-replay",
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=first_sink,
        reference_date=REFERENCE_DATE,
    )

    retry_frames, retry_send = _capturing_send()
    retry_sink = SseEnvelopeSink(
        turn_id="turn-2", message_id="msg-2", send=retry_send, registry=REGISTRY
    )
    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-replay",
        text="Top GP customers for Port of Singapore in April 2026",
        client_turn_key="ctk-replay",
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=retry_sink,
        reference_date=REFERENCE_DATE,
    )
    return retry_frames, _fold(retry_frames, retry_sink.message_id)


# ===========================================================================
# Sink-level scenario -- every part kind the renderer registry knows, in one
# frame sequence, built straight on SseEnvelopeSink (no orchestrator)
# ===========================================================================


def all_part_kinds(_monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """Every part kind in one turn, emitted directly through the sink.

    Frame order (fixed by this task's brief, and by what Task 11's
    translator has to survive): ``accepted``; a ``phase`` frame; a ``tool``
    start for ``tool_seq`` 1; ``part`` frames of kind ``metric_grid``,
    ``table``, ``proof``, ``phase_section``, ``artifact``; the ``tool``
    done; a ``part`` of kind ``text`` IMMEDIATELY followed by a ``token``
    (the merge rule: the token folds into that text part, it does not start
    a new one); ``chips``; ``done``.

    Two disclosures about how these frames are produced:

    * The ``phase`` frame has no public producer. ``SseEnvelopeSink``
      exposes ``accepted``/``push_part``/``push_token``/``done`` and the
      ``emit`` vocabulary (``llm_call``/``tool_start``/``tool_done``/
      ``turn_error``) -- none of which emits ``phase``, though ``phase`` is
      one of the seven frame kinds the envelope contract freezes and the
      frontend's own ``SseEvent`` union already types (``frontend/src/api/
      types.ts``: ``{ phase: string; status: "start" | "done" }``). This
      scenario therefore calls ``sink._frame("phase", ...)`` -- the ONE
      private method every public method above already delegates to, so the
      bytes produced are exactly what a future ``sink.phase()`` would
      produce, not a hand-assembled string.
    * The ``tool`` done frame comes from ``emit("tool_done", ...)`` with
      empty ``parts``/``proof`` and no ``artifacts``, so it emits the tool
      frame ALONE -- the five content parts above it are pushed through
      ``push_part`` instead, which is what lets them sit between the start
      and the done frame in this order.

    Not present, deliberately: an ``error`` part. ``error`` is in the
    frontend's ``PartKind`` union but is never a PERSISTED part -- the
    stream carries it as its own terminal frame kind, and
    ``_record_transcript_frame`` folds no part for it ("the error is a
    stream event, not a persisted part"). ``error_mid_turn`` above pins
    that frame; pinning an ``error`` part here would pin a shape the
    backend cannot produce.
    """
    frames: list[str] = []
    sink = _sink(frames)

    sink.accepted(1)
    sink._frame("phase", {"phase": "contextualize", "status": "start"})
    sink.emit("tool_start", {"tool_seq": 1, "skill_id": METRIC_QUERY, "arguments": {}})
    sink.push_part(
        "metric_grid",
        {
            "periods": {
                "a": {"start": "2026-04-01", "end": "2026-05-01"},
                "b": {"start": "2025-04-01", "end": "2025-05-01"},
            },
            "metrics": [
                {"name": "GP", "friendly": "Gross Profit", "a": 204000, "b": 180501, "unit": "USD"},
                {"name": "VOLUME", "friendly": "Volume", "a": 4103, "b": 3900, "unit": "tons"},
            ],
        },
    )
    sink.push_part(
        "table",
        {"columns": ["Customer", "Gross Profit"], "rows": [["Northstar Lines", 412000]]},
    )
    sink.push_part("proof", {"lines": ["Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V", "Rows: 1"]})
    sink.push_part(
        "phase_section",
        {"title": "Contextualize", "markdown": "What we already know about this account."},
    )
    sink.push_part(
        "artifact",
        {
            "name": "brief.pdf",
            "url": "https://example.invalid/brief.pdf",
            "mime": "application/pdf",
        },
    )
    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": METRIC_QUERY,
            "status": "ok",
            "duration_ms": 12,
            "digest": "data_qa.metric_query ok",
            "parts": [],
            "proof": [],
            "problem": None,
        },
    )
    sink.push_part("text", {"markdown": "Certified answer"})
    sink.push_token(" for SINGAPORE.")
    sink.push_part(
        "chips",
        {"options": [{"id": "new_prospect", "label": "New customer prospect"}]},
    )
    sink.done({"input_tokens": 0, "output_tokens": 0})

    return frames, _fold(frames, sink.message_id)


# ===========================================================================
# Report scenarios -- the monthly report flow, pinned through deterministic hooks
# ===========================================================================


def _report_turn(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    *,
    settings,
    state: ConversationStateStore,
    store: FakeReportStore,
    data: ReportFakeDataClient,
    writer: RecordingWriter,
    role_client: RoleClient,
    turn_no: int,
) -> tuple[list[str], SseEnvelopeSink]:
    """One turn of the report conversation over throwaway-or-recorded frames."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(
        turn_id=f"turn-{turn_no}", message_id=f"msg-{turn_no}", send=send, registry=REGISTRY
    )
    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-report",
        text=text,
        client_turn_key=None,
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        sink=sink,
        reference_date=date(2026, 7, 15),
        report_store=store,
        prompt_tree=PromptTree(),
    )
    return frames, sink


def report_entry(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """The report entry chip's phrase on a fresh conversation: the perspective
    question as text plus the two perspective chips, no dispatch.
    """
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    frames, sink = _report_turn(
        monkeypatch,
        ENTRY_PHRASE_REPORT,
        settings=settings,
        state=ConversationStateStore(),
        store=FakeReportStore(),
        data=ReportFakeDataClient(),
        writer=RecordingWriter(),
        role_client=_dev_role_client(settings),
        turn_no=1,
    )
    return frames, _fold(frames, sink.message_id)


def report_turn(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    """The report turn itself: entry, perspective and office steps run on
    throwaway sinks, then the recorded month turn ("June 2026") dispatches
    ``reporting.monthly_performance``. Every non-deterministic value is pinned
    through hooks (ruling 17): ``uuid7`` (run_id), ``_now`` (generated_at),
    ``_today`` (periods), the report frames' ``time`` (each query reads 1 ms)
    and ``render_pdf`` (fixed bytes). A change to any is a contract change.
    """
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    monkeypatch.setattr(skill_module, "uuid7", lambda: "0199a000-0000-7000-8000-000000000001")
    monkeypatch.setattr(skill_module, "_now", lambda: datetime(2026, 7, 15, 9, 0, tzinfo=UTC))
    monkeypatch.setattr(skill_module, "_today", lambda: date(2026, 7, 15))
    # The brief names ``frames.monotonic``; the report frames module times each
    # query with ``time.perf_counter`` (read twice per query), so that is the hook.
    monkeypatch.setattr(
        report_frames, "time", SimpleNamespace(perf_counter=itertools.count(0, 0.001).__next__)
    )
    monkeypatch.setattr(render, "render_pdf", lambda html: b"%PDF-1.4 fake")

    state = ConversationStateStore()
    store = FakeReportStore()
    data = ReportFakeDataClient()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)
    kw = dict(
        settings=settings,
        state=state,
        store=store,
        data=data,
        writer=writer,
        role_client=role_client,
    )
    prep = (ENTRY_PHRASE_REPORT, "customer office report", "Gibraltar Office")
    for n, text in enumerate(prep, 1):
        _report_turn(monkeypatch, text, turn_no=n, **kw)
    frames, sink = _report_turn(monkeypatch, "June 2026", turn_no=1, **kw)
    return frames, _fold(frames, sink.message_id)


SCENARIOS: dict[str, Callable[[pytest.MonkeyPatch], Recorded]] = {
    "metric_query_flagship": metric_query_flagship,
    "clarify_ambiguous_customer": clarify_ambiguous_customer,
    "error_mid_turn": error_mid_turn,
    "replay_after_ok": replay_after_ok,
    "all_part_kinds": all_part_kinds,
    "report_entry": report_entry,
    "report_turn": report_turn,
}

__all__ = ["SCENARIOS", "Recorded", "to_json"]
