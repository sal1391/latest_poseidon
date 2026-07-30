"""Tests for Phase 8 Task 1 (docs 01/02/03; the plan's "Sanctioned additive
edits"): the emit-part progressive-display seam, the artifact-forwarding
one-liner that closes a gap ledgered since Phase 6, and the incremental part
protocol that makes both real on the wire.

Four modules, one story. ``core/skills/context.py`` grows two additive
fields (``llm``, ``emit_part``), both typed ``object`` and defaulted to
``None`` -- the same "existing call sites keep working" contract Phase 7
Task 1 established for ``tools`` (see ``test_mcp_registry.py``'s own suite
for that precedent, mirrored here). ``core/llm/loop.py``'s ``_dispatch_one``
gains one line, ``"artifacts": result.artifacts``, forwarding what P5 never
did (events.py's own docstring ledgered this as "Artifacts: coded, currently
unreachable" through Phases 6 and 7) -- this suite proves it reachable
through a REAL dispatch, sibling to ``test_chat_orchestrator.py``'s own
synthetic proof of the sink's conversion code. ``core/chat/events.py``'s
``SseEnvelopeSink`` gains ``part_emitter(tool_seq)``: a callable a skill can
invoke mid-dispatch to stream a part immediately, with a count-based guard
that makes ``_handle_tool_done`` skip re-emitting whatever was already
streamed -- proven here at the frame-sequence level, and proven NOT to
change today's behavior for the skills that never call it.
``core/chat/orchestrator.py`` wires both new fields at the one place it
builds a turn's ``SkillContext``: ``llm`` to the turn's own ``role_client``,
``emit_part`` to ``sink.part_emitter(1)`` -- proven end to end through
``execute_turn`` with a fake skill, not merely at the unit level.

Every fake skill in this file is registered directly (``SkillRegistry(skills=
{...})``, bypassing ``SkillRegistry.discover()`` and its filesystem walk --
the same "no discovery needed" construction ``test_skill_registry.py``'s own
suite uses), so nothing here reads a real task package, and no offline test
in this file ever needs a live LLM: ``LLM_MODE=stub`` throughout, exactly as
every earlier Phase 5/6/7 suite runs.

Non-ASCII characters in expected strings are written as literal ``--`` for
an em dash (never a typed Unicode character), matching the convention every
earlier Phase 4-7 suite uses. ``context.py`` itself predates that convention
(verified: ten pre-existing non-ASCII doc-section-reference lines, none of
them touched by this task's additive edit) and is deliberately NOT brought
under a whole-file ASCII guard here -- retroactively rewriting its
pre-existing prose is outside this task's sanctioned edit ("the plumbing":
two new fields), and ``test_ascii_guard_covers_only_the_files_this_task_
newly_touches_cleanly`` below states that scope explicitly rather than
leaving it implicit.
"""

import json
from dataclasses import FrozenInstanceError
from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel

from poseidon.core.chat.events import SseEnvelopeSink
from poseidon.core.chat.orchestrator import execute_turn
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.config import Settings
from poseidon.core.data.client import PeriodRange
from poseidon.core.llm.loop import RecordingSink, run_turn
from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.stub import StubProvider
from poseidon.core.llm.types import LLMResponse, ToolCall
from poseidon.core.skills import context as context_module
from poseidon.core.skills.context import ArtifactRef, ConversationSlots, SkillContext
from poseidon.core.skills.registry import RegisteredSkill, SkillRegistry
from poseidon.core.skills.result import SkillResult

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}

FAKE_SKILL = "fake_task.streaming_skill"
REFERENCE_DATE = date(2026, 4, 15)


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


def _bare_settings() -> Settings:
    """A ``Settings`` with no env-hermeticity ceremony -- for the
    ``SkillContext`` field tests below, which never invoke an LLM or touch
    ``LLM_MODE`` and therefore need no monkeypatched environment at all
    (mirrors ``test_skill_registry.py``'s own inline construction).
    ``database_url``/``s3_bucket`` are the model's own lowercase field
    names, deliberately not built from ``REQUIRED_ENV`` (whose keys are the
    UPPERCASE env var names ``_settings`` above feeds to ``monkeypatch.
    setenv`` -- a different casing convention for a different construction
    path)."""
    return Settings(
        _env_file=None,
        database_url=REQUIRED_ENV["DATABASE_URL"],
        s3_bucket=REQUIRED_ENV["S3_BUCKET"],
    )


@pytest.fixture
def settings(monkeypatch) -> Settings:
    return _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")


# ===========================================================================
# SkillContext.llm / SkillContext.emit_part -- additive fields, suites stay
# green (mirrors test_mcp_registry.py's own SkillContext.tools precedent)
# ===========================================================================


def test_skill_context_emit_part_defaults_to_none():
    """Additive per Phase 8 Task 1: every pre-existing ``SkillContext(...)``
    call site in this codebase omits ``emit_part`` entirely and must keep
    working unchanged -- provable only if the field defaults to ``None``."""
    ctx = SkillContext(data=object(), artifacts=None, settings=_bare_settings())

    assert ctx.emit_part is None


def test_skill_context_emit_part_accepts_an_explicit_callable():
    calls: list[dict] = []

    def emitter(part: dict) -> None:
        calls.append(part)

    ctx = SkillContext(data=object(), artifacts=None, settings=_bare_settings(), emit_part=emitter)

    assert ctx.emit_part is emitter
    ctx.emit_part({"kind": "text", "payload": {"markdown": "x"}})
    assert calls == [{"kind": "text", "payload": {"markdown": "x"}}]


def test_skill_context_llm_defaults_to_none():
    ctx = SkillContext(data=object(), artifacts=None, settings=_bare_settings())

    assert ctx.llm is None


def test_skill_context_llm_accepts_an_explicit_value():
    fake_role_client = object()

    ctx = SkillContext(
        data=object(), artifacts=None, settings=_bare_settings(), llm=fake_role_client
    )

    assert ctx.llm is fake_role_client


def test_skill_context_remains_frozen_with_the_two_new_fields():
    ctx = SkillContext(data=object(), artifacts=None, settings=_bare_settings())

    with pytest.raises(FrozenInstanceError):
        ctx.llm = object()
    with pytest.raises(FrozenInstanceError):
        ctx.emit_part = object()


def test_skill_context_tools_and_state_defaults_are_unaffected_by_the_new_trailing_fields():
    """``llm``/``emit_part`` are appended AFTER ``tools`` (all three now
    carry defaults) -- ``tools``'s and ``state``'s own defaults must still
    construct correctly, unaffected by two more fields sitting after them."""
    ctx = SkillContext(data=object(), artifacts=None, settings=_bare_settings())

    assert ctx.tools is None
    assert ctx.state == ConversationSlots()


def test_skill_context_every_field_can_be_supplied_together_by_keyword():
    """The full seven-field shape orchestrator.py now builds every turn --
    proven constructible in one call, keyword-only, matching how every real
    call site in this codebase already constructs it (never positionally)."""
    fake_tools = object()
    fake_llm = object()
    fake_emit = object()
    slots = ConversationSlots(customer="Northstar Lines")

    ctx = SkillContext(
        data=object(),
        artifacts=None,
        settings=_bare_settings(),
        state=slots,
        tools=fake_tools,
        llm=fake_llm,
        emit_part=fake_emit,
    )

    assert (ctx.state, ctx.tools, ctx.llm, ctx.emit_part) == (
        slots,
        fake_tools,
        fake_llm,
        fake_emit,
    )


# ===========================================================================
# Fake-skill scaffolding -- a hand-built SkillRegistry, no filesystem walk
# (mirrors test_skill_registry.py's own no-discovery construction)
# ===========================================================================


class _NoArgs(BaseModel):
    """Every fake skill in this file takes no arguments -- the scripted
    router call always names it with an empty ``{}``."""


def _registry_with(fn) -> SkillRegistry:
    return SkillRegistry(
        skills={
            FAKE_SKILL: RegisteredSkill(
                skill_id=FAKE_SKILL,
                args_model=_NoArgs,
                fn=fn,
                description="A fake skill for Phase 8 Task 1's plumbing tests -- never a real one.",
            )
        }
    )


def _skill_returning(
    *,
    parts: tuple[dict, ...] = (),
    proof: tuple[str, ...] = (),
    artifacts: tuple[ArtifactRef, ...] = (),
    early_parts: tuple[dict, ...] = (),
):
    """A fake skill's ``run`` -- streams ``early_parts`` through
    ``ctx.emit_part`` (asserting it is wired, never silently skipping), then
    returns a ``SkillResult`` carrying ``parts``/``proof``/``artifacts``
    verbatim. ``parts`` is the skill's own COMPLETE list regardless of what
    it also streamed early -- exactly what a real skill does (doc 02 section
    5: a skill's result always carries every part it produced, streamed
    early or not; see events.py's own "Incremental part streaming")."""

    def _run(ctx: SkillContext, _args: _NoArgs) -> SkillResult:
        for part in early_parts:
            assert ctx.emit_part is not None, "this fake skill requires emit_part to be wired"
            ctx.emit_part(part)
        return SkillResult(ok=True, parts=list(parts), proof=list(proof), artifacts=list(artifacts))

    return _run


def _tool_use(call: ToolCall, *, input_tokens: int = 10, output_tokens: int = 5) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=(call,),
        stop_reason="tool_use",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _end_turn(text: str, *, input_tokens: int = 12, output_tokens: int = 3) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _context(settings: Settings, **kwargs) -> SkillContext:
    return SkillContext(
        data=object(), artifacts=None, settings=settings, state=ConversationSlots(), **kwargs
    )


WINDOW = [{"role": "user", "content": "run the fake skill"}]


def _capturing_send():
    frames: list[str] = []

    def send(frame: str) -> None:
        frames.append(frame)

    return frames, send


def _parse_frame(frame: str) -> tuple[int, str, dict]:
    """Decode one SSE frame back into ``(event_seq, event_name, data)`` --
    mirrors ``test_chat_orchestrator.py``'s own helper of the same shape."""
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
# loop.py: the artifacts one-liner (_dispatch_one's tool_done payload)
# ===========================================================================


def test_tool_done_payload_forwards_a_skills_artifacts(settings):
    """The P5 gap, closed: ``_dispatch_one`` now carries ``result.artifacts``
    into the ``tool_done`` payload it emits, proven at the loop level with a
    plain ``RecordingSink`` (no wire translation involved yet -- that half
    is proven separately below, through a real ``SseEnvelopeSink``)."""
    ref = ArtifactRef(
        name="brief.pdf", url="https://example.test/brief.pdf", mime="application/pdf"
    )
    registry = _registry_with(_skill_returning(artifacts=(ref,)))
    sink = RecordingSink()
    provider = StubProvider(
        [_tool_use(ToolCall(id="c1", name=FAKE_SKILL, arguments={})), _end_turn("done")]
    )
    role_client = RoleClient(settings, providers={"stub": provider})

    run_turn(
        role_client=role_client,
        registry=registry,
        context=_context(settings),
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        user_instruction="",
        memory_doc="",
        parsed=None,
        window=WINDOW,
        sink=sink,
        max_iterations=10,
    )

    tool_done_payloads = [payload for kind, payload in sink.events if kind == "tool_done"]
    assert len(tool_done_payloads) == 1
    assert tool_done_payloads[0]["artifacts"] == [ref]


def test_tool_done_payload_artifacts_is_an_empty_list_when_the_skill_produces_none(settings):
    """Every skill before Phase 8 Task 4 -- the realistic case this suite
    must not regress: ``SkillResult.artifacts`` defaults to ``[]`` (never
    ``None``), so the forwarded payload key is an empty list, not absent."""
    registry = _registry_with(_skill_returning(parts=()))
    sink = RecordingSink()
    provider = StubProvider(
        [_tool_use(ToolCall(id="c1", name=FAKE_SKILL, arguments={})), _end_turn("done")]
    )
    role_client = RoleClient(settings, providers={"stub": provider})

    run_turn(
        role_client=role_client,
        registry=registry,
        context=_context(settings),
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        user_instruction="",
        memory_doc="",
        parsed=None,
        window=WINDOW,
        sink=sink,
        max_iterations=10,
    )

    tool_done_payload = [payload for kind, payload in sink.events if kind == "tool_done"][0]
    assert tool_done_payload["artifacts"] == []


# ===========================================================================
# loop.py -> events.py, chained: the REAL artifact path, sibling to
# test_chat_orchestrator.py's own synthetic
# test_tool_done_defensively_converts_artifact_refs_when_present (which
# hand-builds a tool_done payload carrying "artifacts" because loop.py could
# not yet produce one). This test builds NO payload by hand: a real fake
# skill returns a real ArtifactRef, a real dispatch runs, and the artifact
# frame this asserts on is whatever the REAL chain actually produced.
# ===========================================================================


def test_artifact_forwarded_end_to_end_from_a_real_dispatch_to_an_artifact_frame(settings):
    ref = ArtifactRef(
        name="brief.pdf", url="https://example.test/brief.pdf", mime="application/pdf"
    )
    registry = _registry_with(_skill_returning(parts=(), proof=(), artifacts=(ref,)))
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=registry)
    provider = StubProvider(
        [_tool_use(ToolCall(id="c1", name=FAKE_SKILL, arguments={})), _end_turn("done")]
    )
    role_client = RoleClient(settings, providers={"stub": provider})

    run_turn(
        role_client=role_client,
        registry=registry,
        context=_context(settings),
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        user_instruction="",
        memory_doc="",
        parsed=None,
        window=WINDOW,
        sink=sink,
        max_iterations=10,
    )

    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    # tool_start ("tool"), tool_done's own "tool" frame (no parts, no proof
    # -- the fake skill returned neither), then the artifact -- the ONLY
    # "part"-kind frame this turn produces.
    assert names == ["tool", "tool", "part"]
    assert decoded[0][2]["status"] == "start"
    assert decoded[1][2]["status"] == "done"
    artifact_frame = decoded[2][2]
    assert artifact_frame["kind"] == "artifact"
    assert artifact_frame["payload"] == {
        "name": "brief.pdf",
        "url": "https://example.test/brief.pdf",
        "mime": "application/pdf",
    }


# ===========================================================================
# events.py: the incremental part-streaming protocol
# (direct SseEnvelopeSink tests -- no loop.py involved, matching
# test_chat_orchestrator.py's own style for this class)
# ===========================================================================

EMPTY_REGISTRY = SkillRegistry()


def test_part_emitter_pushes_a_part_frame_immediately():
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=EMPTY_REGISTRY)
    emit = sink.part_emitter(1)

    emit({"kind": "text", "payload": {"markdown": "early"}})

    assert len(frames) == 1
    _, name, data = _parse_frame(frames[0])
    assert name == "part"
    assert data["kind"] == "text"
    assert data["payload"] == {"markdown": "early"}


def test_frame_order_tool_start_early_part_tool_done_late_only_parts_then_proof():
    """The pinned sequence: ``tool_start`` -> the emitter's early part
    (pushed BEFORE ``tool_done`` even fires) -> ``tool_done``'s own ``tool``
    frame -> the late-only parts (``early`` is NOT repeated) -> proof.
    ``event_seq`` stays strictly monotonic across the whole sequence,
    the early part included."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=EMPTY_REGISTRY)
    early = {"kind": "text", "payload": {"markdown": "early"}}
    late = {"kind": "table", "payload": {"columns": ["A"], "rows": [["x"]]}}

    sink.emit("tool_start", {"tool_seq": 1, "skill_id": FAKE_SKILL, "arguments": {}})
    emit = sink.part_emitter(1)
    emit(early)
    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": FAKE_SKILL,
            "status": "ok",
            "duration_ms": 1,
            "digest": "d",
            "parts": [early, late],
            "proof": ["Rows: 1"],
            "problem": None,
        },
    )

    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["tool", "part", "tool", "part", "part"]

    assert decoded[0][2]["status"] == "start"
    assert decoded[1][2]["kind"] == "text"
    assert decoded[1][2]["payload"] == {"markdown": "early"}
    assert decoded[2][2]["status"] == "done"
    assert decoded[3][2]["kind"] == "table"  # the LATE-ONLY part -- early not repeated
    assert decoded[3][2]["payload"] == late["payload"]
    assert decoded[4][2]["kind"] == "proof"
    assert decoded[4][2]["payload"] == {"lines": ["Rows: 1"]}

    assert [seq for seq, _n, _d in decoded] == [1, 2, 3, 4, 5]


def test_no_emitter_usage_tool_done_emits_every_part_byte_identical_to_before():
    """Regression: a dispatch that never touches ``part_emitter`` at all --
    every skill before Phase 8 Task 4 -- must still produce EXACTLY today's
    frame sequence. Same scenario ``test_chat_orchestrator.py``'s own
    ``test_tool_done_ok_emits_tool_frame_then_each_part_then_proof`` already
    pins; reproduced here so the regression is provable from this file
    alone, with no cross-file coupling."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=EMPTY_REGISTRY)
    table = {"kind": "table", "payload": {"columns": ["Customer"], "rows": [["A"]]}}

    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": FAKE_SKILL,
            "status": "ok",
            "duration_ms": 12,
            "digest": "d",
            "parts": [table],
            "proof": ["Rows: 1"],
            "problem": None,
        },
    )

    names = [_parse_frame(f)[1] for f in frames]
    assert names == ["tool", "part", "part"]
    _, _, table_frame = _parse_frame(frames[1])
    assert table_frame["kind"] == "table"
    assert table_frame["payload"] == table["payload"]
    _, _, proof_frame = _parse_frame(frames[2])
    assert proof_frame["payload"] == {"lines": ["Rows: 1"]}


def test_double_emission_impossible_parts_streamed_early_are_not_repeated_at_tool_done():
    """The count-based skip, proven directly: two parts pushed early, then a
    ``tool_done`` whose ``parts`` list carries those SAME two (exactly what a
    real skill does -- see ``_skill_returning``'s own docstring) must put
    exactly two ``part`` frames on the wire, never four."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=EMPTY_REGISTRY)
    part_a = {"kind": "text", "payload": {"markdown": "a"}}
    part_b = {"kind": "text", "payload": {"markdown": "b"}}
    emit = sink.part_emitter(1)
    emit(part_a)
    emit(part_b)

    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": FAKE_SKILL,
            "status": "ok",
            "duration_ms": 1,
            "digest": "d",
            "parts": [part_a, part_b],
            "proof": [],
            "problem": None,
        },
    )

    part_frames = [f for f in frames if _parse_frame(f)[1] == "part"]
    assert len(part_frames) == 2


def test_part_emitter_counter_is_scoped_to_its_own_tool_seq():
    """A count kept for tool_seq 1 must never be consulted for tool_seq 2's
    own ``tool_done`` -- each dispatch's count lives under its own key."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=EMPTY_REGISTRY)
    part = {"kind": "text", "payload": {"markdown": "x"}}
    sink.part_emitter(1)(part)  # streamed early for tool_seq 1 ONLY

    sink.emit(
        "tool_done",
        {
            "tool_seq": 2,
            "skill_id": FAKE_SKILL,
            "status": "ok",
            "duration_ms": 1,
            "digest": "d",
            "parts": [part],
            "proof": [],
            "problem": None,
        },
    )

    part_frames = [f for f in frames if _parse_frame(f)[1] == "part"]
    # tool_seq 1's early push (1) + tool_seq 2's own tool_done, which owes
    # nothing to tool_seq 1's count and still emits its own part (1) = 2.
    assert len(part_frames) == 2


def test_part_emitter_called_again_for_the_same_tool_seq_resets_its_count():
    """Reset semantics, proven rather than merely documented: a second
    ``part_emitter(1)`` call for an id already in progress zeroes its count,
    so a part streamed under the FIRST call is emitted AGAIN at ``tool_done``
    -- the count genuinely restarted rather than kept accumulating."""
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=EMPTY_REGISTRY)
    part = {"kind": "text", "payload": {"markdown": "x"}}
    sink.part_emitter(1)(part)  # count -> 1 for tool_seq 1
    sink.part_emitter(1)  # called again for the SAME tool_seq -- resets to 0

    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": FAKE_SKILL,
            "status": "ok",
            "duration_ms": 1,
            "digest": "d",
            "parts": [part],
            "proof": [],
            "problem": None,
        },
    )

    part_frames = [f for f in frames if _parse_frame(f)[1] == "part"]
    # The first call's early push (1) plus the late re-emit the reset
    # allowed (1) = 2 -- had the count NOT reset, tool_done would have
    # skipped this part too and this would read 1.
    assert len(part_frames) == 2


def test_part_emitter_return_value_is_a_plain_callable_matching_emit_part_shape():
    """``SkillContext.emit_part``'s documented shape is ``(part: dict) ->
    None`` -- proven here that ``part_emitter``'s return value can be wired
    to it directly, with no adapter."""
    sink = SseEnvelopeSink(
        turn_id="t", message_id="m", send=lambda _f: None, registry=EMPTY_REGISTRY
    )

    ctx = SkillContext(
        data=object(),
        artifacts=None,
        settings=_bare_settings(),
        emit_part=sink.part_emitter(1),
    )

    assert ctx.emit_part({"kind": "text", "payload": {"markdown": "x"}}) is None


# ===========================================================================
# orchestrator.py: emit_part / llm reach a REAL dispatch through execute_turn
# ===========================================================================


class _ParseOnlyDataClient:
    """A structural ``DataClient`` that answers ``parse_turn``'s own needs
    (empty dimension pools, a wide period range) but RAISES if anything ever
    tries to run a real query through it -- documenting, as an enforced
    assertion rather than a comment, that the fake skill under test in this
    section never touches ``ctx.data`` (mirrors ``metric_query/tests/
    test_tools.py``'s own ``_UnreachableDataClient`` precedent)."""

    def list_dimension_values(self, entity: str, column: str, search: str | None = None):
        return []

    def available_periods(self, entity: str) -> PeriodRange:
        return PeriodRange(date(2025, 1, 1), date(2026, 6, 30))

    def run_metric_query(self, spec):
        raise AssertionError("this fake skill never queries data directly")

    def run_breakdown_query(self, spec):
        raise AssertionError("this fake skill never queries data directly")


def test_execute_turn_wires_emit_part_so_a_fake_skill_streams_before_its_own_tool_done(
    monkeypatch,
):
    """orchestrator.py's own ``SkillContext`` construction site wires
    ``emit_part`` to ``sink.part_emitter(1)`` -- proven end to end, not
    merely "the parameter exists": a fake skill that calls ``ctx.emit_part``
    mid-dispatch, driven through the REAL ``execute_turn``, must show its
    early part on the wire BEFORE the tool's own done frame."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    early = {"kind": "text", "payload": {"markdown": "phase 1 done"}}
    late = {"kind": "text", "payload": {"markdown": "phase 2 done"}}
    registry = _registry_with(_skill_returning(parts=(early, late), early_parts=(early,)))
    provider = StubProvider(
        [_tool_use(ToolCall(id="c1", name=FAKE_SKILL, arguments={})), _end_turn("done")]
    )
    role_client = RoleClient(settings, providers={"stub": provider})
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=registry)

    outcome = execute_turn(
        conversation_id="conv-emit-part",
        text="run the fake skill",
        client_turn_key=None,
        settings=settings,
        registry=registry,
        data=_ParseOnlyDataClient(),
        state=ConversationStateStore(),
        writer=None,
        role_client=role_client,
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        sink=sink,
        reference_date=REFERENCE_DATE,
    )

    assert outcome.status == "ok"
    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["accepted", "tool", "part", "tool", "part", "token", "done"]
    assert decoded[1][2]["status"] == "start"
    early_frame = decoded[2][2]
    assert early_frame["kind"] == "text"
    assert early_frame["payload"] == {"markdown": "phase 1 done"}
    assert decoded[3][2]["status"] == "done"
    late_frame = decoded[4][2]
    assert late_frame["payload"] == {"markdown": "phase 2 done"}


def test_execute_turn_wires_llm_to_the_same_role_client_the_turn_itself_uses(monkeypatch):
    """orchestrator.py's own ``SkillContext`` construction site wires ``llm``
    to THIS call's own ``role_client`` -- proven by identity, from inside a
    fake skill's real dispatch, not asserted about the construction site in
    isolation."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    captured: list[SkillContext] = []

    def _run(ctx: SkillContext, _args: _NoArgs) -> SkillResult:
        captured.append(ctx)
        return SkillResult(ok=True, parts=[])

    registry = _registry_with(_run)
    provider = StubProvider(
        [_tool_use(ToolCall(id="c1", name=FAKE_SKILL, arguments={})), _end_turn("done")]
    )
    role_client = RoleClient(settings, providers={"stub": provider})
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=lambda _f: None, registry=registry)

    outcome = execute_turn(
        conversation_id="conv-llm-wire",
        text="run the fake skill",
        client_turn_key=None,
        settings=settings,
        registry=registry,
        data=_ParseOnlyDataClient(),
        state=ConversationStateStore(),
        writer=None,
        role_client=role_client,
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        sink=sink,
        reference_date=REFERENCE_DATE,
    )

    assert outcome.status == "ok"
    assert len(captured) == 1
    assert captured[0].llm is role_client


def test_execute_turn_without_a_dispatch_still_wires_emit_part_harmlessly(monkeypatch):
    """A turn with no tool call at all (a plain conversational reply) still
    builds its ``SkillContext`` with ``emit_part=sink.part_emitter(1)`` --
    proven here to cause no error and no stray frame when nothing ever
    dispatches to consume it, since ``execute_turn`` wires it unconditionally
    (see that call site's own comment) rather than only when a dispatch is
    about to happen."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    provider = StubProvider([_end_turn("no tools needed")])
    role_client = RoleClient(settings, providers={"stub": provider})
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id="t", message_id="m", send=send, registry=SkillRegistry())

    outcome = execute_turn(
        conversation_id="conv-no-dispatch",
        text="just say hello",
        client_turn_key=None,
        settings=settings,
        registry=SkillRegistry(),
        data=_ParseOnlyDataClient(),
        state=ConversationStateStore(),
        writer=None,
        role_client=role_client,
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        sink=sink,
        reference_date=REFERENCE_DATE,
    )

    assert outcome.status == "ok"
    names = [_parse_frame(f)[1] for f in frames]
    assert names == ["accepted", "token", "done"]


# ===========================================================================
# ASCII-only source -- scoped to exactly the files this task's own tests
# import AND that had no ascii guard before this task (loop.py/events.py/
# orchestrator.py are already covered by test_llm_loop.py/test_chat_
# orchestrator.py, which run against this task's edited versions of those
# files regardless -- same "no re-scan of files this task did not introduce
# a guard for" precedent test_perplexity_mcp_client_module_files_are_ascii_
# on_disk states explicitly). context.py is DELIBERATELY excluded: it
# predates the ASCII convention (ten pre-existing non-ASCII doc-section-
# reference lines, none of them touched by this task's two-field addition,
# verified byte-for-byte against the pre-task file) and retroactively
# rewriting its pre-existing prose is outside "the plumbing"'s sanctioned
# edit -- see the module docstring.
# ===========================================================================


def test_ascii_guard_covers_only_the_files_this_task_newly_touches_cleanly():
    paths = (Path(__file__),)
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


def test_context_new_field_lines_are_ascii_though_the_file_predates_the_guard():
    """context.py as a WHOLE is not ASCII (ten pre-existing lines; see the
    module docstring), so a whole-file guard here would fail for reasons
    this task did not create and is not chartered to fix. This test instead
    pins the NARROWER, true claim: none of the four lines this task actually
    authors in that file (the ``llm``/``emit_part`` field lines, matched by
    their unique field-name prefixes) carry a byte this convention forbids."""
    lines = Path(context_module.__file__).read_bytes().split(b"\n")
    new_field_lines = [
        line for line in lines if line.strip().startswith((b"llm: object", b"emit_part: object"))
    ]
    assert len(new_field_lines) == 2  # both new dataclass field lines, found
    for line in new_field_lines:
        offending = sorted({byte for byte in line if byte > 0x7F})
        assert not offending, f"{line!r} holds non-ASCII bytes: {offending}"
