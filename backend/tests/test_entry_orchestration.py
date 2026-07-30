"""Tests for Phase 8 Task 5 (D19, doc 02 section 4's "Entry orchestration
rule"): the two new branches ``execute_turn`` checks BEFORE ``parse_turn``'s
normal path -- the bubble-entry phrase (sets ``slots.mode``, asks for the
subject, finalizes ``clarify``, no dispatch, no router) and the subject turn
that follows it (resolves the subject -- the full customer-resolver
ambiguity contract for "existing", raw text for "prospect" -- then
dispatches the matching brief skill DETERMINISTICALLY: ``registry.dispatch``
directly, never ``run_turn``, so a stub-mode brief turn logs ONE
``tool_calls`` row and ZERO ``llm_calls`` rows).

Everything here is OFFLINE, mirroring ``test_chat_orchestrator.py``'s own
discipline exactly -- this file reuses that module's ``REGISTRY``/
``FakeDataClient``/``RecordingWriter`` (public names; the same cross-test-
module reuse ``test_live_chat_sse.py`` already does) rather than re-deriving
them, since the flagship existing-customer dispatch below runs the REAL
``customer_insight.existing_customer_brief`` skill against that SAME fixed
data pool. Every other small helper (``_settings``/``_capturing_send``/
``_parse_frame``/``_dev_role_client``) is duplicated locally rather than
imported, matching the established "each test module owns its own private
helpers" convention (``test_llm_loop.py``, ``test_chat_orchestrator.py`` and
``test_chat_state_devrouter.py`` each already do this independently -- none
of them import another module's underscore-prefixed helper).

Two customer names drive the resolution-contract tests below, both
probe-verified directly against ``customer_resolver.resolve`` over
``test_chat_orchestrator.FakeDataClient``'s own six-name pool before being
pinned here: "Meridiann" lands in the fuzzy candidate band (the Meridian
family, three candidates -- the identical shape
``test_chat_orchestrator.py``'s own ambiguous-turn test already pins for
the SAME pool), and "Zzyxx Nonexistent Corp" resolves to nothing at all
(``customer_unknown``, zero candidates).

``test_chat_orchestrator.FakeDataClient`` answers ``run_metric_query`` with
ONLY a "GP" value (built for ``data_qa.metric_query``'s own single-metric
calls) -- too thin for a real brief dispatch, which always requests all
SIX certified metrics (``fetch_metrics.SIX_METRICS``) in one call. The
tests that carry a subject turn all the way through a REAL brief dispatch
therefore use this file's own :class:`_BriefFakeDataClient` instead --
``list_dimension_values`` reusing the identical six-name pool (so the
SAME "Meridiann"/"Northstar Lines" resolutions stay valid) plus full
six-metric ``run_metric_query``/``run_breakdown_query`` support, mirroring
``test_brief_skills.py``'s own ``_FakeDataClient`` shape (call-count-based
prior-vs-YTD -- ``fetch_metrics`` always runs prior then YTD, in that
order) rather than importing it: that fixture deliberately RAISES on
``list_dimension_values`` ("a brief skill must never list dimension
values" -- true for a skill dispatch alone, but false for THIS file, which
also drives the subject turn's OWN resolution step first).
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from poseidon.core.chat import orchestrator
from poseidon.core.chat.dev_router import DevDeterministicRouter
from poseidon.core.chat.events import SseEnvelopeSink
from poseidon.core.chat.orchestrator import execute_turn
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult, BreakdownRow, MetricResult, PeriodRange
from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient
from poseidon.core.skills.context import ConversationSlots
from tests.test_chat_orchestrator import REGISTRY, FakeDataClient, RecordingWriter

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}

REFERENCE_DATE = date(2026, 4, 15)

EXISTING_BRIEF_SKILL = "customer_insight.existing_customer_brief"
NEW_PROSPECT_BRIEF_SKILL = "customer_insight.new_prospect_brief"

ENTRY_TEXT_EXISTING = "start an existing-customer brief"
ENTRY_TEXT_PROSPECT = "start a new-prospect brief"
SUBJECT_PROMPT_EXISTING = "Which customer is this brief for?"
SUBJECT_PROMPT_PROSPECT = "What company should I research?"


def _settings(monkeypatch, **overrides) -> Settings:
    """Mirrors ``test_chat_orchestrator.py``'s own helper exactly."""
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
    """Decode one SSE frame -- see ``test_chat_orchestrator.py``'s own
    identical helper for the full contract this mirrors."""
    assert frame.endswith("\n\n"), repr(frame)
    id_line, event_line, data_line = frame[:-2].split("\n")
    event_seq = int(id_line[len("id: ") :])
    name = event_line[len("event: ") :]
    data = json.loads(data_line[len("data: ") :])
    assert data["event_seq"] == event_seq
    return event_seq, name, data


def _sink(turn_id: str = "turn-1", message_id: str = "msg-1"):
    frames, send = _capturing_send()
    sink = SseEnvelopeSink(turn_id=turn_id, message_id=message_id, send=send, registry=REGISTRY)
    return frames, sink


def _run(
    *,
    conversation_id: str,
    text: str,
    settings: Settings,
    state: ConversationStateStore,
    writer: RecordingWriter | None,
    sink: SseEnvelopeSink,
    client_turn_key: str | None = None,
    data: object | None = None,
    role_client: RoleClient | None = None,
):
    return execute_turn(
        conversation_id=conversation_id,
        text=text,
        client_turn_key=client_turn_key,
        settings=settings,
        registry=REGISTRY,
        data=data if data is not None else FakeDataClient(),
        state=state,
        writer=writer,
        role_client=role_client if role_client is not None else _dev_role_client(settings),
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        sink=sink,
        reference_date=REFERENCE_DATE,
    )


# Six-metric values (fetch_metrics.SIX_METRICS) -- see the module
# docstring for why this file needs its own richer data client alongside
# test_chat_orchestrator.FakeDataClient's thinner, single-metric one.
_BRIEF_PRIOR_VALUES = {
    "VOLUME": 1000.0,
    "GP": 50000.0,
    "MARGIN": 50.0,
    "NUM_WON": 10.0,
    "NUM_INQUIRIES": 15.0,
    "NUM_LOST": 5.0,
}
_BRIEF_YTD_VALUES = {
    "VOLUME": 600.0,
    "GP": 30000.0,
    "MARGIN": 50.0,
    "NUM_WON": 6.0,
    "NUM_INQUIRIES": 9.0,
    "NUM_LOST": 3.0,
}
_BRIEF_PORTS = [("Singapore", 20000.0), ("Rotterdam", 10000.0)]


@dataclass
class _BriefFakeDataClient:
    """See the module docstring's own paragraph on this class: the SAME
    six-name ``CUST_NM`` pool ``test_chat_orchestrator.FakeDataClient``
    uses (so every resolution-contract test stays valid), PLUS full
    six-metric ``run_metric_query``/``run_breakdown_query`` support (so a
    real ``customer_insight.existing_customer_brief`` dispatch, which
    always requests all six certified metrics, does not KeyError)."""

    metric_specs: list = field(default_factory=list)

    def list_dimension_values(self, entity: str, column: str, search: str | None = None):
        return _brief_customer_pool()

    def available_periods(self, entity: str) -> PeriodRange:
        return PeriodRange(date(2025, 1, 1), date(2026, 6, 30))

    def run_metric_query(self, spec) -> MetricResult:
        self.metric_specs.append(spec)
        values = _BRIEF_PRIOR_VALUES if len(self.metric_specs) == 1 else _BRIEF_YTD_VALUES
        return MetricResult(entity=spec.entity, period=spec.period, values=dict(values))

    def run_breakdown_query(self, spec) -> BreakdownResult:
        rows = [BreakdownRow(key=port, values={"GP": gp}) for port, gp in _BRIEF_PORTS]
        return BreakdownResult(entity=spec.entity, group_by=spec.group_by, rows=rows)


def _brief_customer_pool() -> list[str]:
    """The SAME six-name pool ``test_chat_orchestrator.FakeDataClient``
    resolves against -- re-declared here (rather than reaching into that
    module's own private ``_CUSTOMERS`` constant) since this file's own
    resolution-contract tests ("Northstar Lines" exact, "Meridiann"
    ambiguous) were probe-verified against exactly this list, by name."""
    return [
        "Northstar Lines",
        "Blue Anchor Marine",
        "Crestline Freight",
        "Meridian Tankers",
        "Meridian Lines",
        "Meridian Shipping",
    ]


# ===========================================================================
# The entry branch: pinned phrase -> mode set, subject prompt, clarify
# finalize, no dispatch, no router
# ===========================================================================


def test_existing_entry_phrase_sets_mode_and_prompts_for_the_subject(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    writer = RecordingWriter()
    frames, sink = _sink()

    outcome = _run(
        conversation_id="conv-entry-1",
        text=ENTRY_TEXT_EXISTING,
        settings=settings,
        state=state,
        writer=writer,
        sink=sink,
        client_turn_key="ctk-entry-1",
    )

    assert outcome.status == "clarify"
    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["accepted", "part", "done"]

    text_data = decoded[1][2]
    assert text_data["kind"] == "text"
    assert text_data["payload"] == {"markdown": SUBJECT_PROMPT_EXISTING}

    # No dispatch, no router: no tool frames, no tool_calls row, no
    # llm_calls row (DevDeterministicRouter/run_turn is never reached).
    assert "tool" not in names
    assert writer.append_tool_calls == []
    assert writer.append_llm_calls == []

    assert len(writer.finalize_calls) == 1
    assert writer.finalize_calls[0]["status"] == "clarify"
    assert writer.finalize_calls[0]["input_tokens"] == 0
    assert writer.finalize_calls[0]["output_tokens"] == 0

    final_slots = state.get("conv-entry-1")
    assert final_slots.mode == "existing_customer"
    assert state.get_brief_done("conv-entry-1") is False


def test_prospect_entry_phrase_sets_mode_and_prompts_for_the_subject(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    frames, sink = _sink()

    outcome = _run(
        conversation_id="conv-entry-2",
        text=ENTRY_TEXT_PROSPECT,
        settings=settings,
        state=state,
        writer=None,
        sink=sink,
    )

    assert outcome.status == "clarify"
    decoded = [_parse_frame(f) for f in frames]
    text_data = decoded[1][2]
    assert text_data["payload"] == {"markdown": SUBJECT_PROMPT_PROSPECT}
    assert state.get("conv-entry-2").mode == "new_prospect"


def test_entry_phrase_matches_casefolded(monkeypatch):
    """D19's own rule: "matches EXACTLY those (casefolded)" -- a chip
    always sends the exact lowercase phrase, but this proves the match is
    genuinely casefolded, not merely lucky."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    _, sink = _sink()

    outcome = _run(
        conversation_id="conv-entry-case",
        text="START AN EXISTING-CUSTOMER BRIEF",
        settings=settings,
        state=state,
        writer=None,
        sink=sink,
    )

    assert outcome.status == "clarify"
    assert state.get("conv-entry-case").mode == "existing_customer"


def test_entry_phrase_must_match_exactly_not_merely_be_a_substring(monkeypatch):
    """A message that merely CONTAINS the pinned phrase (rather than being
    it, exactly) falls through to the normal parse path -- D19 is not a
    keyword trigger."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    _, sink = _sink()

    _run(
        conversation_id="conv-entry-substr",
        text="please start an existing-customer brief now",
        settings=settings,
        state=state,
        writer=None,
        sink=sink,
    )

    assert state.get("conv-entry-substr").mode == "default"


def test_entry_phrase_preserves_other_carried_slots(monkeypatch):
    """``dataclasses.replace`` touches ONLY ``mode`` -- every other carried
    slot survives a flow-chip click untouched (the P6 pass_through
    precedent this task's own instructions name)."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.put(
        "conv-entry-preserve",
        ConversationSlots(region="APAC", pass_through=(("Northstar Lines", "Northstar Lines"),)),
    )
    _, sink = _sink()

    _run(
        conversation_id="conv-entry-preserve",
        text=ENTRY_TEXT_EXISTING,
        settings=settings,
        state=state,
        writer=None,
        sink=sink,
    )

    final_slots = state.get("conv-entry-preserve")
    assert final_slots.mode == "existing_customer"
    assert final_slots.region == "APAC"
    assert final_slots.pass_through == (("Northstar Lines", "Northstar Lines"),)


def test_entry_phrase_resets_brief_done_for_a_second_brief_in_the_same_conversation(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.set_brief_done("conv-entry-reset", True)
    _, sink = _sink()

    _run(
        conversation_id="conv-entry-reset",
        text=ENTRY_TEXT_EXISTING,
        settings=settings,
        state=state,
        writer=None,
        sink=sink,
    )

    assert state.get_brief_done("conv-entry-reset") is False


def test_entry_turn_retry_with_same_client_turn_key_short_circuits(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    writer = RecordingWriter()

    _, sink_a = _sink(turn_id="turn-A", message_id="msg-A")
    first = _run(
        conversation_id="conv-entry-retry",
        text=ENTRY_TEXT_EXISTING,
        settings=settings,
        state=state,
        writer=writer,
        sink=sink_a,
        client_turn_key="ctk-entry-retry",
    )
    assert first.status == "clarify"

    frames_b, sink_b = _sink(turn_id="turn-B", message_id="msg-B")
    second = _run(
        conversation_id="conv-entry-retry",
        text=ENTRY_TEXT_EXISTING,
        settings=settings,
        state=state,
        writer=writer,
        sink=sink_b,
        client_turn_key="ctk-entry-retry",
    )

    assert second.status == "error"
    names = [_parse_frame(f)[1] for f in frames_b]
    assert names == ["accepted", "error"]
    error_data = _parse_frame(frames_b[1])[2]
    assert error_data["code"] == "duplicate_turn"
    assert len(writer.finalize_calls) == 1  # only the first turn's


def test_entry_turn_works_identically_with_no_writer(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    frames, sink = _sink()

    outcome = _run(
        conversation_id="conv-entry-nowriter",
        text=ENTRY_TEXT_EXISTING,
        settings=settings,
        state=state,
        writer=None,
        sink=sink,
    )

    assert outcome.status == "clarify"
    assert outcome.turn_run_id is None
    names = [_parse_frame(f)[1] for f in frames]
    assert names == ["accepted", "part", "done"]


# ===========================================================================
# The subject turn, existing mode: the full customer-resolver ambiguity
# contract -- resolved -> deterministic dispatch; candidate band -> chips;
# unknown -> text only
# ===========================================================================


def test_subject_turn_existing_mode_resolves_and_dispatches_the_brief(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.put("conv-subj-1", ConversationSlots(mode="existing_customer"))
    writer = RecordingWriter()
    frames, sink = _sink()

    outcome = _run(
        conversation_id="conv-subj-1",
        text="Northstar Lines",
        settings=settings,
        state=state,
        writer=writer,
        sink=sink,
        client_turn_key="ctk-subj-1",
        data=_BriefFakeDataClient(),
    )

    assert outcome.status == "ok"
    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    # tool(start), then EVERY part streamed EARLY via ctx.emit_part --
    # metric_grid, table, then FIVE phase_sections (contextualize=1,
    # research=3 -- one per lens call, existing mode's own sustainability/
    # market_position/strategic_profile -- strategize=1; probe-verified
    # against the real skill, and pinned identically by test_brief_skills.
    # py's own "kinds == [metric_grid, table] + [phase_section] * 5") --
    # events.py's own "an emitter's early pushes land BETWEEN tool_start
    # and the eventual tool done frame". Then tool(done), then proof
    # (never streamed early -- proof is not one of SkillResult.parts). NO
    # token: a deterministic dispatch places no router call, so there is
    # no natural-language "reply" for a router to compose.
    assert names == (["accepted", "tool"] + ["part"] * 7 + ["tool", "part", "done"])
    assert "token" not in names
    assert names.count("tool") == 2

    tool_start = decoded[1][2]
    assert tool_start["tool"] == EXISTING_BRIEF_SKILL
    assert tool_start["status"] == "start"
    tool_done = [
        data for _seq, name, data in decoded if name == "tool" and data["status"] == "done"
    ][0]
    assert tool_done["tool"] == EXISTING_BRIEF_SKILL

    part_kinds = [data["kind"] for _seq, name, data in decoded if name == "part"]
    assert part_kinds == (["metric_grid", "table"] + ["phase_section"] * 5 + ["proof"])
    proof_parts = [
        data for _seq, name, data in decoded if name == "part" and data["kind"] == "proof"
    ]
    proof_lines = proof_parts[0]["payload"]["lines"]
    assert any(line.startswith("Artifact:") for line in proof_lines)

    assert writer.append_llm_calls == []
    assert len(writer.append_tool_calls) == 1
    tool_row = writer.append_tool_calls[0]
    assert tool_row["tool"] == EXISTING_BRIEF_SKILL
    assert tool_row["args"] == {"customer": "Northstar Lines"}
    assert tool_row["status"] == "ok"

    assert len(writer.finalize_calls) == 1
    assert writer.finalize_calls[0]["status"] == "ok"

    assert state.get_brief_done("conv-subj-1") is True
    assert state.get("conv-subj-1").mode == "existing_customer"  # mode stays, advisory


def test_subject_turn_existing_mode_ambiguous_customer_produces_chips_no_dispatch(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.put("conv-subj-amb", ConversationSlots(mode="existing_customer"))
    writer = RecordingWriter()
    frames, sink = _sink()

    outcome = _run(
        conversation_id="conv-subj-amb",
        text="Meridiann",
        settings=settings,
        state=state,
        writer=writer,
        sink=sink,
    )

    assert outcome.status == "clarify"
    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["accepted", "part", "part", "done"]

    chips_data = decoded[1][2]
    assert chips_data["kind"] == "chips"
    # Bare send_text (falls back to label) -- NOT the "for <name>" cue
    # _finish_clarify's own NORMAL clarify chips use: the subject turn
    # feeds the entire next message straight back into customer_resolver.
    # resolve() with no cue-word requirement, so a bare candidate name
    # already resolves at exact tier 1.0.
    assert chips_data["payload"] == {
        "options": [
            {"id": "Meridian Tankers", "label": "Meridian Tankers"},
            {"id": "Meridian Lines", "label": "Meridian Lines"},
            {"id": "Meridian Shipping", "label": "Meridian Shipping"},
        ]
    }
    text_data = decoded[2][2]
    assert text_data["payload"] == {
        "markdown": "did you mean one of: Meridian Tankers, Meridian Lines, Meridian Shipping?"
    }

    assert writer.append_tool_calls == []
    assert state.get_brief_done("conv-subj-amb") is False
    # Still in subject-turn mode for a retry next turn.
    assert state.get("conv-subj-amb").mode == "existing_customer"


def test_subject_turn_existing_mode_unknown_customer_produces_text_only(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.put("conv-subj-unk", ConversationSlots(mode="existing_customer"))
    frames, sink = _sink()

    outcome = _run(
        conversation_id="conv-subj-unk",
        text="Zzyxx Nonexistent Corp",
        settings=settings,
        state=state,
        writer=None,
        sink=sink,
    )

    assert outcome.status == "clarify"
    decoded = [_parse_frame(f) for f in frames]
    names = [name for _seq, name, _data in decoded]
    assert names == ["accepted", "part", "done"]  # no chips -- nothing to suggest
    text_data = decoded[1][2]
    assert text_data["kind"] == "text"
    assert text_data["payload"] == {"markdown": "no customer matching 'Zzyxx Nonexistent Corp'"}
    assert state.get_brief_done("conv-subj-unk") is False


def test_subject_turn_existing_mode_a_second_attempt_after_ambiguity_can_dispatch(monkeypatch):
    """The retry loop this design enables: an ambiguous first attempt
    leaves brief_done False, so the NEXT message -- one of the offered
    chip labels -- is still read as a subject turn and dispatches."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.put("conv-subj-retry", ConversationSlots(mode="existing_customer"))
    writer = RecordingWriter()

    _run(
        conversation_id="conv-subj-retry",
        text="Meridiann",
        settings=settings,
        state=state,
        writer=writer,
        sink=_sink()[1],
    )
    assert state.get_brief_done("conv-subj-retry") is False

    outcome = _run(
        conversation_id="conv-subj-retry",
        text="Meridian Shipping",
        settings=settings,
        state=state,
        writer=writer,
        sink=_sink()[1],
        data=_BriefFakeDataClient(),
    )

    assert outcome.status == "ok"
    assert state.get_brief_done("conv-subj-retry") is True
    assert writer.append_tool_calls[0]["args"] == {"customer": "Meridian Shipping"}


# ===========================================================================
# The subject turn, prospect mode: raw text = subject, no resolver at all
# ===========================================================================


class _ExplodingDataClient:
    """Any method call is a test failure -- proves the prospect subject
    turn never touches the customer resolver OR any internal data tool
    (``new_prospect_brief``'s own "NO INTERNAL DATA TOOLS" design)."""

    def __getattr__(self, name):
        def _boom(*_args, **_kwargs):
            raise AssertionError(f"data client method {name!r} must never be called for a prospect")

        return _boom


def test_subject_turn_prospect_mode_dispatches_raw_text_and_touches_no_data_client(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.put("conv-subj-prospect", ConversationSlots(mode="new_prospect"))
    writer = RecordingWriter()
    frames, sink = _sink()

    outcome = _run(
        conversation_id="conv-subj-prospect",
        text="Meridian Global Shipping",
        settings=settings,
        state=state,
        writer=writer,
        sink=sink,
        data=_ExplodingDataClient(),
    )

    assert outcome.status == "ok"
    decoded = [_parse_frame(f) for f in frames]
    tool_start = [d for _s, n, d in decoded if n == "tool" and d["status"] == "start"][0]
    assert tool_start["tool"] == NEW_PROSPECT_BRIEF_SKILL

    assert writer.append_llm_calls == []
    tool_row = writer.append_tool_calls[0]
    assert tool_row["tool"] == NEW_PROSPECT_BRIEF_SKILL
    # The certified value "Meridian Shipping" is a real name in the
    # fixture pool this test deliberately never exposes any data client
    # to -- the raw text is used VERBATIM, unresolved, proving there is no
    # resolver step to accidentally collide with it.
    assert tool_row["args"] == {"prospect_name": "Meridian Global Shipping"}

    assert state.get_brief_done("conv-subj-prospect") is True


def test_subject_turn_prospect_mode_blank_text_fails_as_a_structured_error(monkeypatch):
    """``prospect_name`` has ``min_length=1`` -- a whitespace-only subject
    strips to empty and the skill's own Args validation reports a
    structured 422, not a silent no-op. brief_done stays False so the user
    can try again."""
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.put("conv-subj-blank", ConversationSlots(mode="new_prospect"))
    writer = RecordingWriter()
    frames, sink = _sink()

    outcome = _run(
        conversation_id="conv-subj-blank",
        text="   ",
        settings=settings,
        state=state,
        writer=writer,
        sink=sink,
    )

    assert outcome.status == "error"
    names = [_parse_frame(f)[1] for f in frames]
    assert names == ["accepted", "tool", "tool", "error"]
    error_data = _parse_frame(frames[-1])[2]
    assert error_data["code"] == "invalid arguments"

    assert len(writer.append_tool_calls) == 1
    assert writer.append_tool_calls[0]["status"] == "error"
    assert writer.finalize_calls[0]["status"] == "error"
    assert state.get_brief_done("conv-subj-blank") is False


def test_subject_turn_retry_with_same_client_turn_key_short_circuits(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.put("conv-subj-dup", ConversationSlots(mode="existing_customer"))
    writer = RecordingWriter()

    first = _run(
        conversation_id="conv-subj-dup",
        text="Northstar Lines",
        settings=settings,
        state=state,
        writer=writer,
        sink=_sink(turn_id="turn-A", message_id="msg-A")[1],
        client_turn_key="ctk-subj-dup",
        data=_BriefFakeDataClient(),
    )
    assert first.status == "ok"

    frames_b, sink_b = _sink(turn_id="turn-B", message_id="msg-B")
    second = _run(
        conversation_id="conv-subj-dup",
        text="Northstar Lines",
        settings=settings,
        state=state,
        writer=writer,
        sink=sink_b,
        client_turn_key="ctk-subj-dup",
    )

    assert second.status == "error"
    assert [_parse_frame(f)[1] for f in frames_b] == ["accepted", "error"]
    assert len(writer.append_tool_calls) == 1  # no re-dispatch


# ===========================================================================
# After the brief: mode stays in slots (advisory); subsequent turns route
# normally through the full registry, never treated as another subject turn
# ===========================================================================


def test_after_brief_completes_a_normal_turn_routes_through_the_full_registry(monkeypatch):
    settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    state = ConversationStateStore()
    state.put("conv-postbrief", ConversationSlots(mode="existing_customer"))
    state.set_brief_done("conv-postbrief", True)
    writer = RecordingWriter()
    frames, sink = _sink()

    outcome = _run(
        conversation_id="conv-postbrief",
        text="Top GP customers for Port of Singapore in April 2026",
        settings=settings,
        state=state,
        writer=writer,
        sink=sink,
    )

    assert outcome.status == "ok"
    names = [_parse_frame(f)[1] for f in frames]
    # The NORMAL flagship shape (test_chat_orchestrator.py's own pin):
    # tool/tool/part/part/token/done -- a router call happened (2
    # llm_calls rows), proving this went through run_turn, not the
    # subject-turn dispatch path.
    assert names == ["accepted", "tool", "tool", "part", "part", "token", "done"]
    assert len(writer.append_llm_calls) == 2
    tool_row = writer.append_tool_calls[0]
    assert tool_row["tool"] == "data_qa.metric_query"


# ===========================================================================
# ASCII-only source
# ===========================================================================


def test_entry_orchestration_module_files_are_ascii_on_disk():
    paths = (Path(orchestrator.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
