"""Tests for Phase 6 Task 3 (doc 03/doc 01 section 5, doc 06 section 1): the
SSE envelope sink and the ``execute_turn`` orchestrator that composes every
stack shipped so far -- P4's ``parse_turn``, P5's ``run_turn``/``RoleClient``/
``PromptRegistry``, P3's ``SkillRegistry``, Task 1's ``RunLogWriter``, Task
2's ``ConversationStateStore``/``DevDeterministicRouter`` -- into the one
service the HTTP layer (Task 4) will call.

Everything here is OFFLINE: a hand-rolled ``FakeDataClient`` (P4/P5's own
fixture pattern -- fixed in-memory dimension pools and query results, no
network, no database), a ``RecordingWriter`` double standing in for
:class:`~poseidon.core.runlog.RunLogWriter` (same public method names,
``**kwargs`` capture -- a call-shape drift between what this suite expects
and what the orchestrator actually sends shows up as a wrong-keys assertion
failure, not a silent pass), ``DevDeterministicRouter`` (Task 2) as the
``RoleClient``'s stub provider for every non-error scenario, and a
frame-capturing ``send`` callable (a plain list-append, never an actual
socket or async anything -- see :class:`SseEnvelopeSink`'s own docstring for
why its methods never await ``send``'s return value).

Every scenario below was verified against a throwaway probe script run
directly against the REAL ``parse_turn``/``run_turn``/``DevDeterministicRouter``
stack before being pinned here (not guessed at): the exact resolved
entities, period windows, table rows and certified-answer text below are
what those real, already-shipped modules actually produce for the given
fixture pool and reference date -- this suite adds no new parsing/routing
behavior of its own, it only proves ``execute_turn`` wires the pieces
together in the pinned order.

Non-ASCII characters in expected strings are written as explicit
``\\uXXXX`` escapes (the ``_EM_DASH`` convention every earlier Phase 4/5/6
suite uses) -- ``test_chat_orchestrator_module_files_are_ascii_on_disk``
enforces that for this file and the two modules it tests.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pytest

from poseidon.core.chat import events, orchestrator
from poseidon.core.chat.dev_router import DevDeterministicRouter
from poseidon.core.chat.events import SseEnvelopeSink
from poseidon.core.chat.orchestrator import (
    TurnOutcome,
    _repopulate_pass_through,
    execute_turn,
)
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult, BreakdownRow, MetricResult, PeriodRange
from poseidon.core.identity import DISABLED_DEFAULT_USER
from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.stub import StubProvider
from poseidon.core.llm.types import LLMResponse, ToolCall
from poseidon.core.runlog import ReplayTurn, TurnHandle
from poseidon.core.skills.context import ArtifactRef, ConversationSlots
from poseidon.core.skills.registry import SkillRegistry
from poseidon.mcp.perplexity.fixture_tool import FixtureResearchTool
from poseidon.mcp.registry import ToolServerRegistry

# U+2014 EM DASH, written as an escape (not a typed literal) -- the
# convention every earlier Phase 4/5/6 suite uses: an em dash, an en dash
# and a hyphen are visually indistinguishable in most editors, so a
# byte-pinned message that used a typed character could silently pin the
# wrong codepoint.
_EM_DASH = "\u2014"

METRIC_QUERY = "data_qa.metric_query"
REGISTRY = SkillRegistry.discover()
# Final-review wave item 3 (I4): the SSE tool-step label is the humanized
# short name ("data_qa.metric_query" -> "Metric query"), never the raw
# SKILL_META description (172 chars, model-facing prose, not a UI label) --
# see events.py's own module-level skill_label, which this hardcodes rather
# than recomputes, since the whole point is pinning the ACTUAL text a human
# reads, independent of the implementation that produces it.
_METRIC_QUERY_LABEL = "Metric query"

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}

# The reference "today" every test parses against: mid-April 2026, so
# "April 2026" is the current month (matching the flagship phrase) and an
# explicit "May 2026" in a follow-up is unambiguously the NEXT month, not a
# relative-year guess -- see the module docstring's probe-first discipline.
REFERENCE_DATE = date(2026, 4, 15)


# ===========================================================================
# Offline data: a fake DataClient over fixed in-memory pools (P4/P5 pattern)
# ===========================================================================

_CUSTOMERS = (
    "Northstar Lines",
    "Blue Anchor Marine",
    "Crestline Freight",
    # Three deliberately similar names: "Meridiann" (double n) against this
    # pool lands in the fuzzy candidate band (verified directly against the
    # real customer_resolver.resolve before writing the ambiguous-turn test
    # below), the same "did you mean...?" shape doc 08's own live-seed
    # "Meridiann" evidence exercises against a larger pool.
    "Meridian Tankers",
    "Meridian Lines",
    "Meridian Shipping",
)
_PORTS = ("SINGAPORE", "ROTTERDAM")
_GP_BY_CUSTOMER = {
    "Northstar Lines": 412000.0,
    "Blue Anchor Marine": 268500.0,
    "Crestline Freight": 155250.0,
}
_TOTALS = {"GP": 835750.0}


@dataclass
class FakeDataClient:
    calls: list = field(default_factory=list)

    def list_dimension_values(self, entity: str, column: str, search: str | None = None):
        self.calls.append(("list_dimension_values", column))
        return list(_CUSTOMERS) if column == "CUST_NM" else list(_PORTS)

    def available_periods(self, entity: str) -> PeriodRange:
        self.calls.append(("available_periods", entity))
        return PeriodRange(date(2025, 1, 1), date(2026, 6, 30))

    def run_metric_query(self, spec) -> MetricResult:
        self.calls.append(("run_metric_query", spec))
        return MetricResult(
            entity=spec.entity, period=spec.period, values={m: _TOTALS[m] for m in spec.metrics}
        )

    def run_breakdown_query(self, spec) -> BreakdownResult:
        self.calls.append(("run_breakdown_query", spec))
        rows = [
            BreakdownRow(key=customer, values={m: _GP_BY_CUSTOMER[customer] for m in spec.metrics})
            for customer in _CUSTOMERS[: spec.top_n]
            if customer in _GP_BY_CUSTOMER
        ]
        return BreakdownResult(entity=spec.entity, group_by=spec.group_by, rows=rows)


# ===========================================================================
# Writer double -- same public method names as RunLogWriter, **kwargs
# capture so a call-shape drift fails an assertion, not silently
# ===========================================================================


class RecordingWriter:
    """Stands in for :class:`~poseidon.core.runlog.RunLogWriter`. Records the
    exact kwargs of every call (never validates them itself -- each test
    asserts the shape it cares about), and hands back a real
    :class:`TurnHandle`.

    Mirrors the real writer's own idempotent-insert contract (Phase 6 Task 4's
    amendment) rather than always minting a fresh id: ``turn_run_id``, when
    the caller supplies one (the orchestrator always does, post-amendment --
    its own SSE sink's ``turn_id``), is used verbatim, exactly like
    ``RunLogWriter.start_turn`` now does; and a SECOND call with the same
    ``(user_sub, client_turn_key)`` returns the FIRST call's handle with
    ``created=False`` instead of a new one -- the same "client retry lands on
    the turn that already exists" shape ``runlog.py``'s own ``ON CONFLICT ...
    DO NOTHING`` fallback implements against real Postgres. ``client_turn_key
    =None`` never conflicts (matches Postgres unique-constraint semantics for
    NULL, and ``runlog.py``'s own test coverage of it), so only calls that
    supply the SAME non-None key ever short-circuit each other.

    ``replay_by_turn_run_id`` (Phase 11 Task 3, additive): what
    :meth:`read_for_replay` hands back for a given ``turn_run_id``, or
    ``None`` (the default, empty-dict behavior) for every id this double was
    never seeded for. Empty by default so every pre-existing test in this
    suite -- which constructs a bare ``RecordingWriter()`` and never seeds
    anything -- keeps exercising exactly today's byte-pinned ``duplicate_
    turn`` error path: this offline double has no real ``messages`` table to
    replay FROM, so "no replay data available" is the honest answer, not
    merely a convenient default.
    """

    def __init__(self, replay_by_turn_run_id: dict[str, ReplayTurn] | None = None) -> None:
        self.start_turn_calls: list[dict] = []
        self.append_llm_calls: list[dict] = []
        self.append_tool_calls: list[dict] = []
        self.finalize_calls: list[dict] = []
        self.read_for_replay_calls: list[dict] = []
        self._next_id = 1
        self._existing_by_key: dict[tuple[str, str], str] = {}
        self._replay_by_turn_run_id = replay_by_turn_run_id or {}

    def start_turn(self, **kwargs) -> TurnHandle:
        self.start_turn_calls.append(kwargs)
        user_sub = kwargs["user_sub"]
        client_turn_key = kwargs.get("client_turn_key")
        if client_turn_key is not None:
            key = (user_sub, client_turn_key)
            existing = self._existing_by_key.get(key)
            if existing is not None:
                return TurnHandle(turn_run_id=existing, created=False)
        turn_run_id = kwargs.get("turn_run_id") or f"run-{self._next_id}"
        self._next_id += 1
        if client_turn_key is not None:
            self._existing_by_key[(user_sub, client_turn_key)] = turn_run_id
        return TurnHandle(turn_run_id=turn_run_id, created=True)

    def append_llm_call(self, **kwargs) -> None:
        self.append_llm_calls.append(kwargs)

    def append_tool_call(self, **kwargs) -> None:
        self.append_tool_calls.append(kwargs)

    def finalize(self, **kwargs) -> None:
        self.finalize_calls.append(kwargs)

    def read_for_replay(self, **kwargs) -> ReplayTurn | None:
        """Stands in for :meth:`~poseidon.core.runlog.RunLogWriter.
        read_for_replay` -- see this class's own docstring for why an
        unseeded ``turn_run_id`` returning ``None`` is the correct offline
        behavior, not a stub-only shortcut."""
        self.read_for_replay_calls.append(kwargs)
        return self._replay_by_turn_run_id.get(kwargs["turn_run_id"])


# ===========================================================================
# Settings / role client helpers
# ===========================================================================


def _settings(monkeypatch, **overrides) -> Settings:
    """A hermetic ``Settings`` -- mirrors ``test_llm_loop.py``'s own helper:
    every Settings-derived env var cleared first, so a real ``.env`` or an
    ambient shell variable can never reach a pinned run."""
    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LLM_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_ROUTER", raising=False)
    for key, value in {**REQUIRED_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def _dev_role_client(settings: Settings) -> RoleClient:
    return RoleClient(settings, providers={"stub": DevDeterministicRouter()})


def _capturing_send() -> tuple[list[str], Callable[[str], None]]:
    frames: list[str] = []

    def send(frame: str) -> None:
        frames.append(frame)

    return frames, send


def _parse_frame(frame: str) -> tuple[int, str, dict]:
    """Decode one SSE frame back into ``(event_seq, event_name, data)`` --
    the inverse of the pinned wire format, so assertions read structured
    data instead of raw text. Fails loudly on anything not shaped exactly
    like ``mock_chat.py``'s own ``_sse()`` output (``id: N\\nevent: name\\n
    data: {...}\\n\\n``)."""
    assert frame.endswith("\n\n"), repr(frame)
    id_line, event_line, data_line = frame[:-2].split("\n")
    assert id_line.startswith("id: "), repr(frame)
    assert event_line.startswith("event: "), repr(frame)
    assert data_line.startswith("data: "), repr(frame)
    event_seq = int(id_line[len("id: ") :])
    name = event_line[len("event: ") :]
    data = json.loads(data_line[len("data: ") :])
    assert data["event_seq"] == event_seq
    return event_seq, name, data


# ===========================================================================
# SseEnvelopeSink -- loop events + parts -> doc 01 section 5 frames
# ===========================================================================


def _sink(frames=None, *, turn_id="turn-1", message_id="msg-1", registry=REGISTRY):
    if frames is None:
        frames = []

    def send(frame: str) -> None:
        frames.append(frame)

    return SseEnvelopeSink(turn_id=turn_id, message_id=message_id, send=send, registry=registry)


def test_sink_exposes_turn_id_and_message_id():
    sink = _sink(turn_id="t-9", message_id="m-9")
    assert sink.turn_id == "t-9"
    assert sink.message_id == "m-9"


def test_llm_call_produces_no_frame():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.emit("llm_call", {"call_seq": 1, "role": "router", "provider": "bedrock", "model": "x"})

    assert frames == []


def test_accepted_frame_shape():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t-1", message_id="m-1", send=send, registry=REGISTRY)

    sink.accepted(3)

    assert len(frames) == 1
    event_seq, name, data = _parse_frame(frames[0])
    assert (event_seq, name) == (1, "accepted")
    assert data == {"turn_id": "t-1", "message_id": "m-1", "event_seq": 1, "turn_index": 3}


def test_tool_start_frame_uses_the_humanized_skill_label_not_the_full_description():
    """Final-review wave item 3 (I4): the tool step line used to carry
    ``SKILL_META['description']`` verbatim (172 characters of model-facing
    prose meant for the router prompt, not a UI label) -- it now carries the
    same short, humanized name ``GET /api/skills`` already showed the
    SkillsPicker (``events.skill_label``, the shared home both now use)."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.emit(
        "tool_start", {"tool_seq": 1, "skill_id": METRIC_QUERY, "arguments": {"metrics": ["GP"]}}
    )

    _, name, data = _parse_frame(frames[0])
    assert name == "tool"
    assert data == {
        "turn_id": "t",
        "message_id": "m",
        "event_seq": 1,
        "tool_seq": 1,
        "tool": METRIC_QUERY,
        "server": None,
        "status": "start",
        "label": _METRIC_QUERY_LABEL,
    }


def test_tool_start_unrecognized_skill_id_still_gets_a_humanized_label():
    """``skill_label`` is a pure string derivation over the dotted id, never
    a registry lookup (final-review wave item 3) -- an id the registry has
    never heard of (an unknown-skill dispatch, or a live router
    hallucinating a name) still produces a legible, humanized label the
    exact same way a real one does, rather than falling back to the bare
    dotted id: there is no registry-membership check left to fail."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.emit("tool_start", {"tool_seq": 1, "skill_id": "data_qa.made_up", "arguments": {}})

    _, _, data = _parse_frame(frames[0])
    assert data["label"] == "Made up"


def test_tool_done_ok_emits_tool_frame_then_each_part_then_proof():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)
    table = {"kind": "table", "payload": {"columns": ["Customer"], "rows": [["A"]]}}

    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": METRIC_QUERY,
            "status": "ok",
            "duration_ms": 12,
            "digest": "data_qa.metric_query ok",
            "parts": [table],
            "proof": ["Entity: X", "Rows: 1"],
            "problem": None,
        },
    )

    names = [_parse_frame(f)[1] for f in frames]
    assert names == ["tool", "part", "part"]

    _, _, tool_data = _parse_frame(frames[0])
    assert tool_data["status"] == "done"
    assert tool_data["tool"] == METRIC_QUERY
    assert tool_data["label"] == _METRIC_QUERY_LABEL

    _, _, table_frame = _parse_frame(frames[1])
    assert table_frame["kind"] == "table"
    assert table_frame["payload"] == table["payload"]

    _, _, proof_frame = _parse_frame(frames[2])
    assert proof_frame == {
        "turn_id": "t",
        "message_id": "m",
        "event_seq": 3,
        "kind": "proof",
        "payload": {"lines": ["Entity: X", "Rows: 1"]},
    }


def test_tool_done_error_status_maps_to_error_in_the_tool_frame():
    """doc 01 section 5's ``tool`` event vocabulary is ``start|done|error``;
    loop.py's own ``tool_done`` payload status is ``ok|error`` -- this is the
    one translation the sink has to get right, since the two vocabularies
    are NOT byte-identical for the failure case."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": METRIC_QUERY,
            "status": "error",
            "duration_ms": 5,
            "digest": "data_qa.metric_query error",
            "parts": [],
            "proof": [],
            "problem": {
                "status": 422,
                "title": "invalid arguments",
                "detail": "x",
                "type": "about:blank",
            },
        },
    )

    assert len(frames) == 1  # no parts, no proof -- just the tool frame
    _, name, data = _parse_frame(frames[0])
    assert name == "tool"
    assert data["status"] == "error"


def test_tool_done_with_no_proof_emits_no_proof_part():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": METRIC_QUERY,
            "status": "ok",
            "duration_ms": 1,
            "digest": "d",
            "parts": [],
            "proof": [],
            "problem": None,
        },
    )

    assert len(frames) == 1  # tool frame only


def test_tool_done_records_parts_by_tool_seq_for_pass_through_lookup():
    """The orchestrator's pass-through repopulation (doc 02 section 5) needs
    the RAW parts a dispatch produced, which never reach it any other way --
    ``ToolRecord.result_digest`` is a lossy string summary, and
    ``TurnResult`` carries no parts of its own (doc 03's context-hygiene
    split). This is the one piece of state the sink retains beyond simply
    forwarding frames, disclosed here and in the sink's own docstring."""
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=lambda _f: None, registry=REGISTRY)
    table = {"kind": "table", "payload": {"columns": ["Customer"], "rows": [["A"]]}}

    sink.emit(
        "tool_done",
        {
            "tool_seq": 7,
            "skill_id": METRIC_QUERY,
            "status": "ok",
            "duration_ms": 1,
            "digest": "d",
            "parts": [table],
            "proof": [],
            "problem": None,
        },
    )

    assert sink.tool_result_parts[7] == [table]


def test_tool_done_defensively_converts_artifact_refs_when_present():
    """loop.py's own ``tool_done`` payload never carries ``artifacts`` today
    (verified directly against ``_dispatch_one`` -- it forwards ``parts``/
    ``proof`` but not ``result.artifacts``; disclosed as a carry in this
    task's report, out of this task's sanctioned edits since fixing it means
    touching ``loop.py``). This test proves the CONVERSION code path itself
    is correct and ready the moment that gap closes, via a synthetic payload
    a real caller cannot produce yet."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)
    ref = ArtifactRef(
        name="brief.pdf", url="https://example.test/brief.pdf", mime="application/pdf"
    )

    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": METRIC_QUERY,
            "status": "ok",
            "duration_ms": 1,
            "digest": "d",
            "parts": [],
            "proof": [],
            "problem": None,
            "artifacts": [ref],
        },
    )

    assert len(frames) == 2  # the tool frame (no parts, no proof), then the artifact part
    _, tool_name, _ = _parse_frame(frames[0])
    assert tool_name == "tool"
    _, name, data = _parse_frame(frames[1])
    assert name == "part"
    assert data["kind"] == "artifact"
    assert data["payload"] == {
        "name": "brief.pdf",
        "url": "https://example.test/brief.pdf",
        "mime": "application/pdf",
    }


def test_tool_done_with_parts_proof_and_artifacts_together_orders_part_then_proof_then_artifact():
    """Final-review wave item 8: combines what
    ``test_tool_done_ok_emits_tool_frame_then_each_part_then_proof`` and
    ``test_tool_done_defensively_converts_artifact_refs_when_present`` each
    prove separately -- a single ``tool_done`` payload carrying parts, proof
    AND artifacts all at once (not yet producible by any real skill today,
    see events.py's own "Artifacts: coded, currently unreachable") emits
    tool, then each part, then proof, then each artifact, exactly matching
    ``_handle_tool_done``'s own emission order."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)
    table = {"kind": "table", "payload": {"columns": ["Customer"], "rows": [["A"]]}}
    ref = ArtifactRef(
        name="brief.pdf", url="https://example.test/brief.pdf", mime="application/pdf"
    )

    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": METRIC_QUERY,
            "status": "ok",
            "duration_ms": 1,
            "digest": "d",
            "parts": [table],
            "proof": ["Rows: 1"],
            "problem": None,
            "artifacts": [ref],
        },
    )

    names = [_parse_frame(f)[1] for f in frames]
    assert names == ["tool", "part", "part", "part"]

    _, _, table_frame = _parse_frame(frames[1])
    assert table_frame["kind"] == "table"
    assert table_frame["payload"] == table["payload"]

    _, _, proof_frame = _parse_frame(frames[2])
    assert proof_frame["kind"] == "proof"
    assert proof_frame["payload"] == {"lines": ["Rows: 1"]}

    _, _, artifact_frame = _parse_frame(frames[3])
    assert artifact_frame["kind"] == "artifact"
    assert artifact_frame["payload"] == {
        "name": "brief.pdf",
        "url": "https://example.test/brief.pdf",
        "mime": "application/pdf",
    }


def test_turn_error_emits_error_frame_with_code_and_message():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.emit(
        "turn_error",
        {
            "problem": {
                "type": "about:blank",
                "title": "llm provider error",
                "detail": "bedrock error: ThrottlingException",
                "status": 502,
            }
        },
    )

    _, name, data = _parse_frame(frames[0])
    assert name == "error"
    assert data == {
        "turn_id": "t",
        "message_id": "m",
        "event_seq": 1,
        "code": "llm provider error",
        "message": "bedrock error: ThrottlingException",
    }


def test_push_part_frame_shape():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.push_part("chips", {"options": [{"id": "A", "label": "A"}]})

    _, name, data = _parse_frame(frames[0])
    assert name == "part"
    assert data["kind"] == "chips"
    assert data["payload"] == {"options": [{"id": "A", "label": "A"}]}


def test_push_token_frame_shape():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.push_token("Certified answer.")

    _, name, data = _parse_frame(frames[0])
    assert (name, data["text"]) == ("token", "Certified answer.")


def test_done_frame_shape():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.done({"input_tokens": 10, "output_tokens": 5})

    _, name, data = _parse_frame(frames[0])
    assert (name, data["usage"]) == ("done", {"input_tokens": 10, "output_tokens": 5})


def test_event_seq_is_monotonic_from_one_across_every_frame_producing_call():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    sink.accepted(1)
    sink.emit("llm_call", {})  # no frame -- must not consume a sequence number
    sink.emit("tool_start", {"tool_seq": 1, "skill_id": METRIC_QUERY, "arguments": {}})
    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": METRIC_QUERY,
            "status": "ok",
            "duration_ms": 1,
            "digest": "d",
            "parts": [{"kind": "table", "payload": {"columns": [], "rows": []}}],
            "proof": ["Rows: 0"],
            "problem": None,
        },
    )
    sink.push_token("done text")
    sink.done({"input_tokens": 0, "output_tokens": 0})

    sequences = [_parse_frame(f)[0] for f in frames]
    assert sequences == list(range(1, len(frames) + 1))
    names = [_parse_frame(f)[1] for f in frames]
    assert names == ["accepted", "tool", "tool", "part", "part", "token", "done"]


def test_every_frame_is_the_exact_mock_chat_wire_format():
    """Byte-for-byte the same shape as ``mock_chat.py``'s own ``_sse()`` --
    the frontend's SSE parser is not this task's to change, so the wire
    format has to match exactly: ``id: N\\nevent: name\\ndata: {...}\\n\\n``."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="turn-abc", message_id="msg-xyz", send=send, registry=REGISTRY)

    sink.accepted(1)

    payload = {"turn_id": "turn-abc", "message_id": "msg-xyz", "event_seq": 1, "turn_index": 1}
    assert frames[0] == f"id: 1\nevent: accepted\ndata: {json.dumps(payload)}\n\n"


# ===========================================================================
# execute_turn -- the flagship scripted turn (Singapore top-GP), pinned
# frame-by-frame
# ===========================================================================


def test_flagship_singapore_top_gp_frame_sequence_and_writer_rows(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)

    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="turn-1", message_id="msg-1", send=send, registry=REGISTRY)

    outcome = execute_turn(
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

    # --- TurnOutcome ---
    # turn_run_id IS the sink's own turn_id (Phase 6 Task 4's turn-id
    # unification amendment) -- never a writer-minted id of its own.
    assert outcome == TurnOutcome(status="ok", message_id="msg-1", turn_run_id="turn-1")

    # --- frame sequence, event by event, event_seq 1..N, envelope on every
    #     data JSON (doc 01 section 5 verbatim) ---
    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["accepted", "tool", "tool", "part", "part", "token", "done"]
    assert [seq for seq, _name, _data in decoded] == list(range(1, 8))
    for _seq, _name, data in decoded:
        assert data["turn_id"] == "turn-1"
        assert data["message_id"] == "msg-1"

    accepted_data = decoded[0][2]
    assert accepted_data["turn_index"] == 1

    tool_start_data = decoded[1][2]
    assert tool_start_data == {
        "turn_id": "turn-1",
        "message_id": "msg-1",
        "event_seq": 2,
        "tool_seq": 1,
        "tool": METRIC_QUERY,
        "server": None,
        "status": "start",
        "label": _METRIC_QUERY_LABEL,
    }
    tool_done_data = decoded[2][2]
    assert tool_done_data["status"] == "done"
    assert tool_done_data["tool_seq"] == 1

    table_part = decoded[3][2]
    assert table_part["kind"] == "table"
    assert table_part["payload"] == {
        "columns": ["Customer", "Gross Profit"],
        "rows": [
            ["Northstar Lines", 412000],
            ["Blue Anchor Marine", 268500],
            ["Crestline Freight", 155250],
        ],
    }

    proof_part = decoded[4][2]
    assert proof_part["kind"] == "proof"
    assert proof_part["payload"] == {
        "lines": [
            "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
            "Backend: synthetic",
            "Period: 2026-04-01..2026-05-01",
            "Filters: LOC_NM IN (SINGAPORE)",
            "Group by: CUST_NM (top 5)",
            "Rows: 3",
        ]
    }

    token_data = decoded[5][2]
    assert (
        token_data["text"]
        == "Certified answer for SINGAPORE " + _EM_DASH + " 2026-04-01..2026-05-01."
    )

    done_data = decoded[6][2]
    assert done_data["usage"] == {"input_tokens": 0, "output_tokens": 0}

    # --- writer rows: call-shape assertions per the doc 06 row contract ---
    assert len(writer.start_turn_calls) == 1
    start = writer.start_turn_calls[0]
    assert start["user_sub"] == DISABLED_DEFAULT_USER.sub == "dev|local"
    assert start["conversation_id"] == "conv-1"
    assert start["client_turn_key"] == "ctk-1"
    assert start["turn_index"] == 1
    assert start["question"] == "Top GP customers for Port of Singapore in April 2026"
    assert start["mode"] == "default"
    assert start["kind"] == "chat_turn"
    # Turn-id unification (Phase 6 Task 4 amendment): the orchestrator threads
    # the sink's OWN turn_id into start_turn so turn_run.id IS the SSE turn_id.
    assert start["turn_run_id"] == "turn-1"
    # The parsed dict must be genuinely JSON-safe (dates as ISO strings) --
    # not merely dataclasses.asdict output, which still holds raw `date`
    # objects json.dumps cannot serialize (see the orchestrator's own
    # docstring for why this matters: RunLogWriter.start_turn's own
    # `json.dumps(parsed)` has no `default=`, so an unserializable value
    # here would silently fail EVERY turn via the writer's never-raises
    # wrapper).
    json.dumps(start["parsed"])  # must not raise
    assert start["parsed"]["port"]["value"] == "SINGAPORE"
    assert start["parsed"]["period_a"] == {"start": "2026-04-01", "end": "2026-05-01"}
    assert start["parsed"]["slots"]["port"] == "SINGAPORE"

    assert len(writer.append_llm_calls) == 2
    first_llm, second_llm = writer.append_llm_calls
    for row in (first_llm, second_llm):
        assert row["turn_run_id"] == "turn-1"
        assert row["user_sub"] == DISABLED_DEFAULT_USER.sub
        # Final-review wave item 4 (I3): LLM_MODE=stub means
        # DevDeterministicRouter answered, not the CONFIGURED provider
        # ("bedrock", from LLM_PROFILE) RoleClient.resolve reports -- a live
        # row must never claim a paid provider name for a call that cost
        # nothing and touched no network (doc 06 section 1 reserves the
        # literal "stub" for exactly this case).
        assert row["provider"] == "stub"
        assert row["role"] == "router"
        assert row["status"] == "ok"
        assert row["prompt_version"]  # non-empty; exact value pinned below
        assert row["prompt_hash"]  # sha256 hexdigest; exact value pinned below
    assert first_llm["seq"] == 1
    assert second_llm["seq"] == 2
    assert first_llm["prompt_hash"] == second_llm["prompt_hash"]  # one system per turn
    assert first_llm["prompt_version"] == second_llm["prompt_version"]

    assert len(writer.append_tool_calls) == 1
    tool_row = writer.append_tool_calls[0]
    assert tool_row["turn_run_id"] == "turn-1"
    assert tool_row["seq"] == 1
    assert tool_row["tool"] == METRIC_QUERY
    assert tool_row["server"] is None
    assert tool_row["args"]["group_by"] == "CUST_NM"
    assert tool_row["result_digest"] == {
        "digest": (
            "data_qa.metric_query ok\n"
            "parts: table(rows=3)\n"
            "proof: Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V | Backend: synthetic | "
            "Period: 2026-04-01..2026-05-01 | Filters: LOC_NM IN (SINGAPORE) | "
            "Group by: CUST_NM (top 5) | Rows: 3"
        )
    }
    assert tool_row["status"] == "ok"

    assert len(writer.finalize_calls) == 1
    finalize = writer.finalize_calls[0]
    assert finalize["turn_run_id"] == "turn-1"
    assert finalize["status"] == "ok"
    assert finalize["message_id"] == "msg-1"
    assert (
        finalize["answer_summary"]
        == "Certified answer for SINGAPORE " + _EM_DASH + " 2026-04-01..2026-05-01."
    )
    assert finalize["input_tokens"] == 0
    assert finalize["output_tokens"] == 0
    assert isinstance(finalize["latency_ms"], int)
    assert finalize["latency_ms"] >= 0
    assert finalize["error"] is None

    # --- pass-through repopulation: the breakdown's ranked customer names,
    #     capped at 10, wholesale-replacing the prior (empty) value ---
    final_slots = state.get("conv-1")
    assert final_slots.pass_through == (
        ("Northstar Lines", "Northstar Lines"),
        ("Blue Anchor Marine", "Blue Anchor Marine"),
        ("Crestline Freight", "Crestline Freight"),
    )
    assert final_slots.port == "SINGAPORE"


def test_flagship_prompt_hash_matches_the_real_system_text_the_provider_saw(monkeypatch):
    """The strongest possible proof that the orchestrator's independently
    rendered system text (needed for ``prompt_hash`` -- ``run_turn`` builds
    its own copy internally and never returns it) is byte-identical to what
    the provider actually received: a recording stub captures the REAL
    ``system`` argument, and this test hashes it the same way ``prompt_
    hash`` does and compares. If the orchestrator's duplicate of ``loop.py``'s
    private ``_router_system`` recipe ever drifts, this fails first."""

    class _RecordingStub:
        """Wraps the REAL DevDeterministicRouter so this test still exercises
        genuine routing decisions -- only ``system`` is intercepted."""

        def __init__(self):
            self._inner = DevDeterministicRouter()
            self.systems: list[str] = []

        def invoke(self, *, system, messages, tools, model, params):
            self.systems.append(system)
            return self._inner.invoke(
                system=system, messages=messages, tools=tools, model=model, params=params
            )

    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    stub = _RecordingStub()
    role_client = RoleClient(settings, providers={"stub": stub})
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-hash",
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

    assert len(stub.systems) == 2
    assert stub.systems[0] == stub.systems[1]  # one system per turn, reused
    real_hash = hashlib.sha256(stub.systems[0].encode("utf-8")).hexdigest()
    assert writer.append_llm_calls[0]["prompt_hash"] == real_hash


# ===========================================================================
# execute_turn -- Final-review wave item 4 (I3): provider truthfulness in
# stub mode -- doc 06 section 1 reserves the literal "stub" for a call the
# DevDeterministicRouter answered; a live row must never claim a paid
# provider name for a call that cost nothing and touched no network.
# ===========================================================================


def test_llm_call_rows_record_provider_stub_under_llm_mode_stub_not_the_configured_provider(
    monkeypatch,
):
    """``RoleClient.resolve``'s CONFIGURED provider ("bedrock", from
    ``LLM_PROFILE=bedrock``) is truthful about CONFIG, never about who
    actually ANSWERED -- ``LLM_MODE=stub`` always substitutes
    ``DevDeterministicRouter`` at call time (``roles.py``'s own module
    docstring). ``execute_turn``'s ``_append_records`` is the one place that
    holds both ``settings.llm_mode`` and the record, so it is the one place
    that can tell the difference; ``LLMRecord.provider`` itself cannot (see
    that dataclass's own docstring: "the only answer available to a loop
    that never learns which provider actually answered")."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=lambda _f: None, registry=REGISTRY)

    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-provider",
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

    assert len(writer.append_llm_calls) == 2
    assert all(row["provider"] == "stub" for row in writer.append_llm_calls)
    # model_id is untouched by this fix -- still the CONFIGURED model, which
    # stays meaningful even under a stub (it names what a live call WOULD
    # have used).
    assert all(row["model_id"] for row in writer.append_llm_calls)


# ===========================================================================
# execute_turn -- carry-over turns (port carried but not re-resolved;
# customer carried AND re-resolved -- the documented pipeline.py asymmetry)
# ===========================================================================


def test_carry_over_turn_period_replaced_port_carried_but_not_refiltered(monkeypatch):
    """Turn 2 of doc 08's own canonical scripted conversation ("and for
    May?"): the period replaces, the port carries in ConversationSlots (
    ``state.put`` proven), but -- because ``pipeline.py``'s port-carry has no
    re-resolution step (a documented asymmetry vs. customer, see that
    module's own "Customer carry is a detection source, port carry is not")
    -- no fresh "Resolved port:" line exists for turn 2, so
    ``DevDeterministicRouter`` (which only ever reads "Resolved", never
    "Carried") dispatches with NO port filter this time. Written as "May
    2026" rather than doc 08's bare "and for May?": explicit year removes
    this test's correctness from depending on ``period_parser``'s relative-
    year-guessing behavior at a specific reference date, which is not what
    this test is about (see the module docstring's probe-first discipline
    -- a bare "and for May?" against REFERENCE_DATE empirically resolves to
    May 2025, the most recently COMPLETED May, not the intuitive "next"
    one)."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)

    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-carry",
        text="Top GP customers for Port of Singapore in April 2026",
        client_turn_key=None,
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=SseEnvelopeSink(
            turn_id="t1", message_id="m1", send=lambda _f: None, registry=REGISTRY
        ),
        reference_date=REFERENCE_DATE,
    )
    assert state.get("conv-carry").port == "SINGAPORE"

    frames2, send2 = _capturing_send()
    outcome2 = execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-carry",
        text="and for May 2026?",
        client_turn_key=None,
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=SseEnvelopeSink(turn_id="t2", message_id="m2", send=send2, registry=REGISTRY),
        reference_date=REFERENCE_DATE,
    )

    assert outcome2.status == "ok"
    second_start_turn_call = writer.start_turn_calls[1]
    assert second_start_turn_call["turn_index"] == 2
    assert second_start_turn_call["parsed"]["period_a"] == {
        "start": "2026-05-01",
        "end": "2026-06-01",
    }
    # No "Resolved port:" line this turn -> no port filter on the dispatch.
    second_tool_call = writer.append_tool_calls[1]
    assert "filters" not in second_tool_call["args"]

    # state.put still carries the port forward (period replaced alongside).
    final_slots = state.get("conv-carry")
    assert final_slots.port == "SINGAPORE"
    assert final_slots.period_a == date(2026, 5, 1)

    token_frame = [f for f in frames2 if "token" in f][0]
    _, _, token_data = _parse_frame(token_frame)
    # entity_label falls back to "All Customers": neither customer nor port
    # was FRESHLY resolved this turn (see dev_router.py's own case (c) --
    # keyed off "Resolved", never "Carried").
    assert (
        token_data["text"]
        == "Certified answer for All Customers " + _EM_DASH + " 2026-05-01..2026-06-01."
    )


def test_carry_over_turn_customer_carried_and_still_refiltered(monkeypatch):
    """The other half of the pipeline.py asymmetry (contrast with the port
    test above): a customer mention IS a carry-eligible detection source
    (``pipeline.py``'s "Customer carry is a detection source, port carry is
    not"), so a pure continuation turn with no new customer cue still
    produces a FRESH "Resolved customer:" line -- verified directly against
    the real ``parse_turn`` before writing this test. The second turn's
    dispatch therefore STILL carries a customer filter, unlike the port
    scenario."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)

    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-cust-carry",
        text="gp for Northstar Lines in April 2026",
        client_turn_key=None,
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=SseEnvelopeSink(
            turn_id="t1", message_id="m1", send=lambda _f: None, registry=REGISTRY
        ),
        reference_date=REFERENCE_DATE,
    )
    assert state.get("conv-cust-carry").customer == "Northstar Lines"

    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-cust-carry",
        text="and for May 2026?",
        client_turn_key=None,
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=SseEnvelopeSink(
            turn_id="t2", message_id="m2", send=lambda _f: None, registry=REGISTRY
        ),
        reference_date=REFERENCE_DATE,
    )

    second_tool_call = writer.append_tool_calls[1]
    assert second_tool_call["args"]["filters"] == [
        {"column": "CUST_NM", "values": ["Northstar Lines"]}
    ]
    assert second_tool_call["args"]["period"] == {"start": "2026-05-01", "end": "2026-06-01"}
    assert state.get("conv-cust-carry").customer == "Northstar Lines"


# ===========================================================================
# execute_turn -- ambiguous turn: chips + clarify, no dispatch
# ===========================================================================


def test_ambiguous_turn_produces_chips_and_text_parts_clarify_status_no_dispatch(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)

    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    outcome = execute_turn(
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

    assert outcome.status == "clarify"
    assert outcome.message_id == "m"

    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["accepted", "part", "part", "done"]

    chips_data = decoded[1][2]
    assert chips_data["kind"] == "chips"
    # Final-review wave item 2 (I1 + M6): clarification chips carry a
    # "for <name>" send_text -- SCOPED to clarify chips only (never a blanket
    # prefix; see ChipsPart.tsx's own option.send_text ?? option.label and
    # the opener flow chips, which carry no send_text at all).
    assert chips_data["payload"] == {
        "options": [
            {
                "id": "Meridian Tankers",
                "label": "Meridian Tankers",
                "send_text": "for Meridian Tankers",
            },
            {
                "id": "Meridian Lines",
                "label": "Meridian Lines",
                "send_text": "for Meridian Lines",
            },
            {
                "id": "Meridian Shipping",
                "label": "Meridian Shipping",
                "send_text": "for Meridian Shipping",
            },
        ]
    }

    text_data = decoded[2][2]
    assert text_data["kind"] == "text"
    assert text_data["payload"] == {
        "markdown": "did you mean one of: Meridian Tankers, Meridian Lines, Meridian Shipping?"
    }

    done_data = decoded[3][2]
    assert done_data["usage"] == {"input_tokens": 0, "output_tokens": 0}

    # No skill dispatch: no tool frames, no tool_calls row, no llm_calls row
    # (parse_turn is deterministic Python -- no model was invoked either).
    assert "tool" not in names
    assert writer.append_tool_calls == []
    assert writer.append_llm_calls == []

    assert len(writer.finalize_calls) == 1
    finalize = writer.finalize_calls[0]
    assert finalize["status"] == "clarify"
    assert finalize["input_tokens"] == 0
    assert finalize["output_tokens"] == 0
    assert finalize["error"] is None
    assert finalize["answer_summary"] == (
        "did you mean one of: Meridian Tankers, Meridian Lines, Meridian Shipping?"
    )

    # Slots STILL carry-updated: the period resolved this turn even though
    # the customer did not (Global Constraints: "clarify... slots STILL
    # carry-updated").
    assert state.get("conv-amb").period_a == date(2026, 4, 1)
    assert state.get("conv-amb").customer is None


# ===========================================================================
# execute_turn -- error turn: an unknown skill, scripted twice, fails the
# turn (loop.py's own self-correction contract) -- error event + finalize
# error, records still logged, state unchanged
# ===========================================================================


def test_error_turn_emits_error_event_and_finalizes_error_state_untouched(monkeypatch):
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
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    outcome = execute_turn(
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

    assert outcome.status == "error"
    # turn_run_id IS the sink's own turn_id ("t"), not a writer-minted one --
    # the turn-id unification amendment applies on every terminal path.
    assert outcome.turn_run_id == "t"

    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    # Two failed dispatches (tool_start/tool_done pairs) then the error --
    # no token, no done: an error frame is its own terminal signal (matches
    # mock_chat.py's own error path, which also sends no `done` after it).
    assert names == ["accepted", "tool", "tool", "tool", "tool", "error"]
    assert "token" not in names
    assert "done" not in names

    error_data = decoded[-1][2]
    assert error_data["code"] == "unknown skill"
    assert "data_qa.made_up" in error_data["message"]

    # Both failed dispatches are still logged (doc 06: a turn that failed on
    # its second tool call still has real dispatches worth logging).
    assert len(writer.append_tool_calls) == 2
    assert all(row["status"] == "error" for row in writer.append_tool_calls)
    assert len(writer.append_llm_calls) == 2
    assert all(row["status"] == "ok" for row in writer.append_llm_calls)  # the CALLS succeeded

    assert len(writer.finalize_calls) == 1
    finalize = writer.finalize_calls[0]
    assert finalize["status"] == "error"
    assert finalize["error"]["status"] == 404
    assert finalize["message_id"] == "m"
    # Final-review wave item 7: usage is summed from turn_result.llm_records
    # UNCONDITIONALLY (before the ok/error branch), so an error-terminated
    # turn's finalize call still reports the real token spend of the two
    # calls the script scripted (10/5 input/output tokens each) -- never 0,
    # which would misreport a turn that really did call the provider twice.
    assert finalize["input_tokens"] == 20
    assert finalize["output_tokens"] == 10

    # Slots unchanged: state.put is never called on the error path.
    assert state.get("conv-err") == ConversationSlots()


# ===========================================================================
# execute_turn -- Phase 6 Task 4 amendments: turn-id unification (turn_run.id
# IS the sink's SSE turn_id) and the client_turn_key retry short-circuit
# (a duplicate start_turn call ends the turn with ONE pinned error frame,
# no dispatch, no finalize -- the ORIGINAL turn owns the row)
# ===========================================================================


def test_retry_with_same_client_turn_key_short_circuits_with_duplicate_turn_error(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    first_frames, first_send = _capturing_send()
    first_sink = SseEnvelopeSink(
        turn_id="turn-A", message_id="msg-A", send=first_send, registry=REGISTRY
    )
    first_outcome = execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-retry",
        text="Top GP customers for Port of Singapore in April 2026",
        client_turn_key="ctk-retry",
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

    # The FIRST send: an ordinary, successful turn. turn_run_id IS the first
    # sink's own turn_id -- the turn-id unification half of this amendment.
    assert first_outcome == TurnOutcome(status="ok", message_id="msg-A", turn_run_id="turn-A")

    # A retry: a NEW HTTP request (a new sink, its own fresh turn_id/message_id
    # -- mirroring mock_chat.py's own per-request minting) but the SAME
    # client_turn_key, so the writer reports created=False.
    retry_frames, retry_send = _capturing_send()
    retry_sink = SseEnvelopeSink(
        turn_id="turn-B", message_id="msg-B", send=retry_send, registry=REGISTRY
    )
    retry_outcome = execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-retry",
        text="Top GP customers for Port of Singapore in April 2026",
        client_turn_key="ctk-retry",
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

    # turn_run_id on the outcome is the ORIGINAL row's id (what start_turn
    # reported, created=False) -- the row the retry does NOT own -- while
    # every SSE frame of THIS response still carries ITS OWN sink's turn_id
    # ("turn-B"), read back below via _parse_frame.
    assert retry_outcome == TurnOutcome(status="error", message_id="msg-B", turn_run_id="turn-A")

    decoded = [_parse_frame(f) for f in retry_frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["accepted", "error"]
    for _seq, _name, data in decoded:
        assert data["turn_id"] == "turn-B"
        assert data["message_id"] == "msg-B"

    error_data = decoded[1][2]
    assert error_data["code"] == "duplicate_turn"
    assert error_data["message"] == (
        "this turn was already processed " + _EM_DASH + " refresh to load the conversation"
    )

    # No re-dispatch and no finalize on the retry: only the FIRST turn's
    # dispatch/finalize rows exist, even though start_turn was called twice.
    assert len(writer.start_turn_calls) == 2
    assert len(writer.append_tool_calls) == 1
    assert len(writer.finalize_calls) == 1
    assert writer.finalize_calls[0]["turn_run_id"] == "turn-A"


# ===========================================================================
# execute_turn -- Phase 11 Task 3 (doc 01 section 5): the duplicate-turn
# short-circuit's true-replay upgrade. _begin_turn now asks the writer
# (read_for_replay) what the ORIGINAL turn's status/persisted parts were --
# an "ok" answer with real parts replays them instead of erroring; anything
# else (no data at all, or a non-"ok" status) falls through to exactly the
# same byte-pinned duplicate_turn error the test above already proves.
# ===========================================================================


def test_retry_after_an_ok_turn_replays_the_persisted_parts_instead_of_erroring(monkeypatch):
    """The docstring's own promised upgrade: a duplicate ``client_turn_key``
    whose ORIGINAL turn already finished ``ok`` replays that turn's
    persisted parts (via ``sink.push_part`` -- the SAME public method the
    clarify short-circuit already calls directly, never ``part_emitter``,
    so the P8 retired-dispatch guard never enters the picture at all) and a
    ``done`` frame, rather than the ``duplicate_turn`` error. No dispatch, no
    ``run_turn`` call, no new ``writer.start_turn``/``append_*``/``finalize``
    beyond the SECOND ``start_turn`` call the retry itself always makes
    (identical to the pre-existing error-path test above)."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    seeded_parts = [
        {"kind": "text", "payload": {"markdown": "the cached answer from turn one"}},
        {"kind": "proof", "payload": {"lines": ["Backend: synthetic"]}},
    ]
    writer = RecordingWriter(
        replay_by_turn_run_id={"turn-A": ReplayTurn(status="ok", parts=seeded_parts)}
    )
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    first_frames, first_send = _capturing_send()
    first_sink = SseEnvelopeSink(
        turn_id="turn-A", message_id="msg-A", send=first_send, registry=REGISTRY
    )
    first_outcome = execute_turn(
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
    assert first_outcome.status == "ok"
    calls_after_first_turn = (
        len(writer.append_llm_calls),
        len(writer.append_tool_calls),
        len(writer.finalize_calls),
    )

    retry_frames, retry_send = _capturing_send()
    retry_sink = SseEnvelopeSink(
        turn_id="turn-B", message_id="msg-B", send=retry_send, registry=REGISTRY
    )
    retry_outcome = execute_turn(
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

    # A true replay is still an "ok" outcome (the ORIGINAL turn succeeded) --
    # `replayed=True` is the one new, additive signal distinguishing it from
    # an ordinary fresh "ok" turn.
    assert retry_outcome == TurnOutcome(
        status="ok", message_id="msg-B", turn_run_id="turn-A", replayed=True
    )

    decoded = [_parse_frame(f) for f in retry_frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["accepted", "part", "part", "done"]
    for _seq, _name, frame_data in decoded:
        assert frame_data["turn_id"] == "turn-B"
        assert frame_data["message_id"] == "msg-B"

    part_frames = [data for _seq, name, data in decoded if name == "part"]
    assert [{"kind": f["kind"], "payload": f["payload"]} for f in part_frames] == seeded_parts

    done_frame = decoded[-1][2]
    assert done_frame["usage"] == {"input_tokens": 0, "output_tokens": 0}

    # No re-dispatch: the pipeline never ran for the retry -- only the
    # SECOND start_turn call happened; append_llm_call/append_tool_call/
    # finalize counts are UNCHANGED from right after the first turn.
    assert len(writer.start_turn_calls) == 2
    assert (
        len(writer.append_llm_calls),
        len(writer.append_tool_calls),
        len(writer.finalize_calls),
    ) == calls_after_first_turn
    assert writer.read_for_replay_calls == [
        {"turn_run_id": "turn-A", "user_sub": DISABLED_DEFAULT_USER.sub}
    ]


@pytest.mark.parametrize(
    ("seeded_status", "seeded_parts"),
    [
        ("running", None),
        ("error", None),
        # Defensive: even an "ok" status with no reachable parts (a
        # never-realistic shape today, since finalize(status="ok") always
        # follows a real persisted message -- see live_chat.py's own
        # Fix-round-1 persistence-ordering guarantee) must not fabricate a
        # replay out of nothing.
        ("ok", None),
    ],
)
def test_retry_falls_back_to_duplicate_turn_error_when_replay_data_is_not_ok(
    monkeypatch, seeded_status, seeded_parts
):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter(
        replay_by_turn_run_id={"turn-A": ReplayTurn(status=seeded_status, parts=seeded_parts)}
    )
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    first_sink = SseEnvelopeSink(
        turn_id="turn-A", message_id="msg-A", send=lambda _f: None, registry=REGISTRY
    )
    execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-no-replay",
        text="hello",
        client_turn_key="ctk-no-replay",
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
        turn_id="turn-C", message_id="msg-C", send=retry_send, registry=REGISTRY
    )
    retry_outcome = execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-no-replay",
        text="hello",
        client_turn_key="ctk-no-replay",
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

    assert retry_outcome == TurnOutcome(status="error", message_id="msg-C", turn_run_id="turn-A")
    decoded = [_parse_frame(f) for f in retry_frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["accepted", "error"]
    error_data = decoded[1][2]
    assert error_data["code"] == "duplicate_turn"


# ===========================================================================
# execute_turn -- writer may be None (no DATABASE_URL): the turn works
# identically without it
# ===========================================================================


def test_writer_none_turn_works_identically_without_a_run_log(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    role_client = _dev_role_client(settings)

    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    outcome = execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-no-writer",
        text="Top GP customers for Port of Singapore in April 2026",
        client_turn_key=None,
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=None,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=sink,
        reference_date=REFERENCE_DATE,
    )

    assert outcome.status == "ok"
    assert outcome.turn_run_id is None
    assert outcome.message_id == "m"
    names = [_parse_frame(f)[1] for f in frames]
    assert names == ["accepted", "tool", "tool", "part", "part", "token", "done"]
    assert state.get("conv-no-writer").pass_through[0] == ("Northstar Lines", "Northstar Lines")


def test_writer_none_ambiguous_turn_also_works(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    role_client = _dev_role_client(settings)

    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    outcome = execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-no-writer-amb",
        text="gp for Meridiann in April 2026",
        client_turn_key=None,
        settings=settings,
        registry=REGISTRY,
        data=FakeDataClient(),
        state=state,
        writer=None,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=SseEnvelopeSink(turn_id="t", message_id="m", send=lambda _f: None, registry=REGISTRY),
        reference_date=REFERENCE_DATE,
    )

    assert outcome.status == "clarify"
    assert outcome.turn_run_id is None


# ===========================================================================
# Pass-through cap: at most 10 pairs, wholesale replace
# ===========================================================================


def test_pass_through_capped_at_ten_rows():
    """A breakdown wider than the cap (top_n up to 50 per Args) still
    populates at most 10 pass-through pairs."""

    class _FakeSink:
        tool_result_parts = {
            1: [
                {
                    "kind": "table",
                    "payload": {
                        "columns": ["Customer", "GP"],
                        "rows": [[f"Customer {i}", i] for i in range(12)],
                    },
                }
            ]
        }

    @dataclass(frozen=True)
    class _FakeRecord:
        tool_seq: int
        arguments: dict

    slots = ConversationSlots()
    new_slots = _repopulate_pass_through(
        slots, (_FakeRecord(tool_seq=1, arguments={"group_by": "CUST_NM"}),), _FakeSink()
    )

    assert len(new_slots.pass_through) == 10
    assert new_slots.pass_through[0] == ("Customer 0", "Customer 0")
    assert new_slots.pass_through[-1] == ("Customer 9", "Customer 9")


def test_pass_through_untouched_when_no_group_by_dispatch_happened():
    @dataclass(frozen=True)
    class _FakeRecord:
        tool_seq: int
        arguments: dict

    slots = ConversationSlots(pass_through=(("Old", "Old"),))

    class _FakeSink:
        tool_result_parts: dict = {}

    new_slots = _repopulate_pass_through(
        slots, (_FakeRecord(tool_seq=1, arguments={}),), _FakeSink()
    )

    assert new_slots.pass_through == (("Old", "Old"),)  # untouched, still carried


# ===========================================================================
# execute_turn -- Final-review wave item 5 (I5): the double-terminal guard.
# _repopulate_pass_through's own docstring names two reachable raisers
# (table["payload"] KeyError; row[0] IndexError on an empty row) -- by the
# time that call runs, sink.done() and writer.finalize(status="ok") have
# ALREADY gone out for this turn, so a second, unguarded exception here would
# either force run_turn_sync's own try/except into a SECOND finalize/error
# frame for an already-finished turn (the "double terminal" this item is
# named for), or -- offline, with no HTTP layer to catch it -- propagate out
# of execute_turn entirely.
# ===========================================================================


class _RowCorruptingSink(SseEnvelopeSink):
    """Wraps the real sink but corrupts its OWN cached ``tool_result_parts``
    right after ``_handle_tool_done`` populates them -- simulating exactly
    the ``IndexError`` ``_repopulate_pass_through``'s own docstring names as
    reachable (a table part whose row is unexpectedly empty), without
    touching ``format_parts.py`` or inventing a payload shape a real
    dispatch could ever produce on the WIRE: the frames already sent to the
    frontend by the real ``_handle_tool_done`` call below are untouched
    (``super()._handle_tool_done`` runs first) -- only the cached copy
    ``_repopulate_pass_through`` reads AFTER ``run_turn`` returns is broken."""

    def _handle_tool_done(self, payload: dict) -> None:
        super()._handle_tool_done(payload)
        for tool_seq, parts in list(self.tool_result_parts.items()):
            self.tool_result_parts[tool_seq] = [
                (
                    {"kind": "table", "payload": {"rows": [[]]}}
                    if part.get("kind") == "table"
                    else part
                )
                for part in parts
            ]


def test_pass_through_repopulation_failure_is_caught_logged_and_does_not_break_the_turn(
    monkeypatch, caplog
):
    """A table part with an empty row -> the turn still reports "ok", with
    exactly one ``done`` frame and one ``finalize`` call, an ERROR logged,
    and the pass-through slot simply not repopulated -- the guard wraps ONLY
    the ``state.put``/``_repopulate_pass_through`` pair, so a failure there
    skips state.put for this turn entirely rather than orphaning the
    already-finalized turn_run row or double-signaling its terminal state."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    frames, send = _capturing_send()
    sink = _RowCorruptingSink(turn_id="t", message_id="m", send=send, registry=REGISTRY)

    with caplog.at_level(logging.ERROR, logger="poseidon.core.chat.orchestrator"):
        outcome = execute_turn(
            user=DISABLED_DEFAULT_USER,
            conversation_id="conv-corrupt",
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

    assert outcome.status == "ok"
    names = [_parse_frame(f)[1] for f in frames]
    assert names.count("done") == 1
    assert names.count("error") == 0

    assert len(writer.finalize_calls) == 1
    assert writer.finalize_calls[0]["status"] == "ok"

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "conv-corrupt" in errors[0].message

    # Pass-through simply not repopulated: state.put was never reached this
    # turn (the guard wraps repopulate AND state.put as one unit), so the
    # conversation's slots stay whatever they were before this turn -- here,
    # the store's own default-for-an-unseen-id, ConversationSlots().
    assert state.get("conv-corrupt") == ConversationSlots()


# ===========================================================================
# execute_turn -- tools threading (Phase 7 Task 4)
# ===========================================================================


def test_execute_turn_threads_tools_into_the_skill_context(monkeypatch):
    """``execute_turn``'s new ``tools`` keyword (defaulted to ``None`` --
    every call above this one keeps working unchanged) must reach the
    ``SkillContext`` a dispatch actually runs against. Proven end to end,
    not merely "the parameter exists": a research-leading turn ("news"
    hints research.web_research -- see dev_router.py's own lexicon-scored
    gate) driven through the REAL dev router and REAL skill registry, with
    a ``ToolServerRegistry`` override installing a ``FixtureResearchTool`` --
    the rendered parts carry that fixture's own sources table, which could
    only happen if ``ctx.tools`` was not ``None``.
    """
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(
        turn_id="turn-research-1", message_id="msg-research-1", send=send, registry=REGISTRY
    )
    tools = ToolServerRegistry(settings, overrides={"research": FixtureResearchTool()})

    outcome = execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-research-1",
        text="any relevant news on Northstar Lines I should be aware of?",
        client_turn_key="ctk-research-1",
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=sink,
        reference_date=REFERENCE_DATE,
        tools=tools,
    )

    assert outcome.status == "ok"
    decoded = [_parse_frame(f) for f in frames]
    tool_frames = [d for _seq, name, d in decoded if name == "tool"]
    assert any(frame["tool"] == "research.web_research" for frame in tool_frames)
    table_parts = [d for _seq, name, d in decoded if name == "part" and d["kind"] == "table"]
    assert table_parts
    assert table_parts[0]["payload"]["columns"] == ["Title", "Source", "Relevance"]


def test_execute_turn_without_tools_still_completes_with_the_honest_degrade(monkeypatch):
    """The symmetric default case: omitting ``tools`` entirely (every
    OTHER call site in this file does -- ``tools`` defaults to ``None``,
    ``SkillContext``'s own default) must still complete the turn rather
    than crash -- ``ctx.tools is None`` renders research's own honest
    "unavailable" message (``skill.py``'s own module docstring) instead of
    a table, so ``execute_turn``'s new keyword is additive in truth, not
    just in signature."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    data = FakeDataClient()
    state = ConversationStateStore()
    writer = RecordingWriter()
    role_client = _dev_role_client(settings)
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(
        turn_id="turn-research-2", message_id="msg-research-2", send=send, registry=REGISTRY
    )

    outcome = execute_turn(
        user=DISABLED_DEFAULT_USER,
        conversation_id="conv-research-2",
        text="any relevant news on Northstar Lines I should be aware of?",
        client_turn_key="ctk-research-2",
        settings=settings,
        registry=REGISTRY,
        data=data,
        state=state,
        writer=writer,
        role_client=role_client,
        prompt_registry=prompt_registry,
        sink=sink,
        reference_date=REFERENCE_DATE,
        # tools omitted entirely -- defaults to None.
    )

    assert outcome.status == "ok"
    decoded = [_parse_frame(f) for f in frames]
    text_parts = [d for _seq, name, d in decoded if name == "part" and d["kind"] == "text"]
    assert any(
        part["payload"]["markdown"].startswith(
            "External research is unavailable right now " + _EM_DASH + " "
        )
        for part in text_parts
    )


# ===========================================================================
# ASCII-only source
# ===========================================================================


def test_chat_orchestrator_module_files_are_ascii_on_disk():
    paths = (Path(events.__file__), Path(orchestrator.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
