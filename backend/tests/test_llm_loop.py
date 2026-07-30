"""Tests for Phase 5 Task 4 (doc 03 section 3 and section 6): the agent
loop, the ``EventSink`` seam, utility chat titles, and the router-decision
suite -- the function Phase 6 calls for every live chat turn.

Everything here runs OFFLINE except the ``router_live``-marked live
executor at the bottom, which skips on a machine with no AWS credentials
(the same pinned reason ``test_llm_bedrock.py`` uses). The offline half
proves the LOOP: a ``StubProvider`` script stands in for the model, the
REAL :class:`~poseidon.core.skills.registry.SkillRegistry` dispatches, and
a fake ``DataClient`` (P4's ``test_parsing_hinter_pipeline.py`` fixture
pattern -- fixed in-memory values, no network, no database) answers the
queries. Nothing about that arrangement is loop-specific test scaffolding:
the ONLY difference between the offline runs and the live ones is which
provider is registered in ``RoleClient``'s provider mapping, which is
exactly what ``test_stub_seam_substitution_proof`` pins.

Two conventions inherited from the earlier Phase 5 suites: expected
strings are byte-pinned, and non-ASCII characters (there are none in this
task's messages, but the guard stays) are written as ``\\uXXXX`` escapes
so a look-alike codepoint can never silently defeat a pin --
``test_llm_loop_module_files_are_ascii_on_disk`` enforces that for this
file and the two modules it tests.
"""

import json
import os
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path

import pytest
import yaml

from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult, BreakdownRow, MetricResult, PeriodRange
from poseidon.core.llm import loop, titles
from poseidon.core.llm.loop import (
    ROUTER_GUARDRAIL_ENTITY,
    ROUTER_ROLE,
    LLMRecord,
    RecordingSink,
    ToolRecord,
    TurnResult,
    run_turn,
    tool_result_digest,
)
from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.stub import StubProvider
from poseidon.core.llm.titles import TITLE_MAX_CHARS, TITLE_PROMPT, UTILITY_ROLE, title_for
from poseidon.core.llm.types import LLMResponse, ToolCall
from poseidon.core.parsing.pipeline import DEFAULT_ENTITY
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.core.skills.result import SkillResult, problem, table_part, text_part
from poseidon.mcp.perplexity.fixture_tool import FixtureResearchTool
from poseidon.mcp.registry import ToolServerRegistry

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}

METRIC_QUERY = "data_qa.metric_query"
# Task 4 (Phase 7): the second offline-runnable routing case's own expected
# skill -- research.web_research, now registered and enabled.
RESEARCH = "research.web_research"
REGISTRY = SkillRegistry.discover()
ROUTING_CASES_PATH = Path(__file__).parent / "routing_cases.yml"


def _settings(monkeypatch, **overrides) -> Settings:
    """A hermetic ``Settings`` -- mirrors ``test_llm_types_roles.py``'s own
    helper: every Settings-derived env var is cleared first, plus the four
    dynamic per-role override names the two roles this file exercises
    (``router``/``utility``) read straight from ``os.environ``, so a real
    ``.env`` or an ambient shell variable can never reach a pinned run."""
    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    for role in ("ROUTER", "UTILITY"):
        monkeypatch.delenv(f"LLM_MODEL_{role}", raising=False)
        monkeypatch.delenv(f"LLM_PROVIDER_{role}", raising=False)

    for key, value in {**REQUIRED_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


# ===========================================================================
# Offline data: a fake DataClient over fixed in-memory values
# ===========================================================================

# Three customers with distinct, descending GP so a breakdown's ordering is
# unambiguous, and one flat total per metric for the non-breakdown shape.
# Hand-authored rather than generated: the loop's contract is about DIGESTS
# and RECORDS, so the only thing these numbers have to be is deterministic
# and recognisable when they (correctly) fail to appear in a digest.
_CUSTOMERS = ("Northstar Lines", "Blue Anchor Marine", "Crestline Freight")
_BY_CUSTOMER = {
    "GP": {
        "Northstar Lines": 412000.0,
        "Blue Anchor Marine": 268500.0,
        "Crestline Freight": 155250.0,
    },
    "VOLUME": {
        "Northstar Lines": 18500.0,
        "Blue Anchor Marine": 12100.0,
        "Crestline Freight": 7400.0,
    },
    "MARGIN": {"Northstar Lines": 22.27, "Blue Anchor Marine": 22.19, "Crestline Freight": 20.98},
}
_TOTALS = {"GP": 835750.0, "VOLUME": 38000.0, "MARGIN": 21.99}


@dataclass
class FakeDataClient:
    """A structural ``DataClient`` (see ``core.data.client``) over the fixed
    pools above -- the offline half of P4's ``FakeDataClient`` pattern,
    extended with the two query methods P4's parser never needed.

    ``calls`` records every query so a test can prove the loop reached the
    real skill rather than short-circuiting somewhere above it.
    """

    calls: list = field(default_factory=list)

    def list_dimension_values(
        self, entity: str, column: str, search: str | None = None
    ) -> list[str]:
        self.calls.append(("list_dimension_values", column))
        return list(_CUSTOMERS) if column == "CUST_NM" else ["Singapore", "Rotterdam"]

    def available_periods(self, entity: str) -> PeriodRange:
        self.calls.append(("available_periods", entity))
        return PeriodRange(date(2025, 1, 1), date(2026, 6, 30))

    def run_metric_query(self, spec) -> MetricResult:
        self.calls.append(("run_metric_query", spec))
        return MetricResult(
            entity=spec.entity,
            period=spec.period,
            values={metric: _TOTALS[metric] for metric in spec.metrics},
        )

    def run_breakdown_query(self, spec) -> BreakdownResult:
        self.calls.append(("run_breakdown_query", spec))
        rows = [
            BreakdownRow(
                key=customer,
                values={metric: _BY_CUSTOMER[metric][customer] for metric in spec.metrics},
            )
            for customer in _CUSTOMERS[: spec.top_n]
        ]
        return BreakdownResult(entity=spec.entity, group_by=spec.group_by, rows=rows)


# ===========================================================================
# Scripted-response and run helpers
# ===========================================================================

GOOD_ARGS = {
    "entity": "MARINE_SALES_PLANNING_V",
    "metrics": ["GP"],
    "period": {"start": "2026-04-01", "end": "2026-05-01"},
    "filters": [{"column": "LOC_NM", "values": ["Singapore"]}],
    "group_by": "CUST_NM",
    "top_n": 5,
}
# Missing the required ``metrics`` field -> the registry's own 422.
MISSING_METRICS_ARGS = {"period": {"start": "2026-04-01", "end": "2026-05-01"}}
# A valid shape whose window is inverted -> a DIFFERENT 422 (PeriodArg's
# own validator), so "which problem came back" is answerable byte by byte.
INVERTED_PERIOD_ARGS = {
    "metrics": ["GP"],
    "period": {"start": "2026-05-01", "end": "2026-04-01"},
}

USER_QUESTION = "top GP customers for Port of Singapore in April 2026"
WINDOW = [{"role": "user", "content": USER_QUESTION}]


def _tool_use(*calls: ToolCall, input_tokens: int = 1000, output_tokens: int = 40) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=calls,
        stop_reason="tool_use",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _end_turn(text: str, *, input_tokens: int = 1200, output_tokens: int = 25) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def _call(args: dict, *, call_id: str = "tool-1", skill: str = METRIC_QUERY) -> ToolCall:
    return ToolCall(id=call_id, name=skill, arguments=args)


def _context(
    settings: Settings, slots: ConversationSlots | None = None, tools: object | None = None
) -> SkillContext:
    return SkillContext(
        data=FakeDataClient(),
        artifacts=None,
        settings=settings,
        state=ConversationSlots() if slots is None else slots,
        tools=tools,
    )


def _role_client(settings: Settings, script, *, key: str = "stub") -> RoleClient:
    return RoleClient(settings, providers={key: StubProvider(script)})


def _run(
    settings: Settings,
    script,
    *,
    max_iterations: int = 10,
    window=None,
    slots: ConversationSlots | None = None,
    provider_key: str = "stub",
    sink: RecordingSink | None = None,
    tools: object | None = None,
) -> tuple[TurnResult, RecordingSink, StubProvider]:
    """One offline turn. Returns the result plus the two recorders every
    assertion in this file reads: the event sink and the provider stub.

    ``sink`` overrides the default recorder, which is what lets a test run
    the identical turn through a DELIBERATELY misbehaving sink (see
    ``_ArgumentMutatingSink``) without a second copy of this helper.
    ``tools`` (Task 4, Phase 7) defaults to ``None`` -- SkillContext's own
    default, unchanged for every one of this file's existing calls -- and
    is threaded straight to ``_context`` for the one routing case
    (``pivot_to_research_with_carry``) that needs a working
    ``ctx.tools.research`` to dispatch against.
    """
    provider = StubProvider(script)
    role_client = RoleClient(settings, providers={provider_key: provider})
    sink = RecordingSink() if sink is None else sink
    result = run_turn(
        role_client=role_client,
        registry=REGISTRY,
        context=_context(settings, slots, tools),
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        user_instruction="",
        memory_doc="",
        parsed=None,
        window=WINDOW if window is None else window,
        sink=sink,
        max_iterations=max_iterations,
    )
    return result, sink, provider


@pytest.fixture
def settings(monkeypatch) -> Settings:
    return _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")


# ===========================================================================
# Shapes
# ===========================================================================


def test_record_and_result_dataclasses_are_frozen():
    from dataclasses import FrozenInstanceError

    record = ToolRecord(
        tool_seq=1,
        skill_id=METRIC_QUERY,
        arguments={},
        status="ok",
        duration_ms=1,
        result_digest="d",
    )
    llm_record = LLMRecord(
        call_seq=1,
        role=ROUTER_ROLE,
        provider="bedrock",
        model="m",
        stop_reason="end_turn",
        input_tokens=1,
        output_tokens=1,
    )
    turn = TurnResult(text="", status="ok", tool_records=(), llm_records=(), problem=None)
    for instance, field_name in (
        (record, "status"),
        (llm_record, "model"),
        (turn, "status"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(instance, field_name, "other")


def test_router_guardrail_entity_matches_the_parser_default_entity():
    """The loop renders the router's certified-metric and negative-constraint
    guardrails for ONE entity. It must be the same default sales entity the
    deterministic parser probes (``parsing.pipeline.DEFAULT_ENTITY``), or the
    router would be guardrailed on a different entity than the one the turn
    was parsed against. Pinned as an equality rather than an import so the
    two modules stay decoupled."""
    assert ROUTER_GUARDRAIL_ENTITY == DEFAULT_ENTITY


# ===========================================================================
# tool_result_digest -- the context-hygiene contract, unit level
# ===========================================================================


def test_digest_success_pins_kind_row_count_and_proof():
    result = SkillResult(
        ok=True,
        parts=[table_part(columns=["Customer", "Gross Profit"], rows=[["A", 1], ["B", 2]])],
        proof=["Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V", "Rows: 2"],
    )

    assert tool_result_digest(METRIC_QUERY, result) == (
        "data_qa.metric_query ok\n"
        "parts: table(rows=2)\n"
        "proof: Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V | Rows: 2"
    )


def test_digest_never_carries_row_contents():
    """The whole point (doc 03 section 3's context hygiene): a digest names
    the SHAPE of a result, never its cells."""
    rows = [["Northstar Lines"], ["Blue Anchor Marine"]]
    result = SkillResult(
        ok=True, parts=[table_part(columns=["Customer"], rows=rows)], proof=["Rows: 2"]
    )

    digest = tool_result_digest(METRIC_QUERY, result)

    assert "Northstar Lines" not in digest
    assert "Blue Anchor Marine" not in digest


def test_digest_metric_grid_and_text_part_summaries():
    result = SkillResult(
        ok=True,
        parts=[
            {"kind": "metric_grid", "payload": {"periods": {}, "metrics": [{}, {}, {}]}},
            text_part("No data for this selection."),
        ],
        proof=["Result: empty"],
    )

    assert tool_result_digest(METRIC_QUERY, result) == (
        "data_qa.metric_query ok\n"
        "parts: metric_grid(metrics=3), text(chars=27)\n"
        "proof: Result: empty"
    )


def test_digest_unknown_part_kind_degrades_to_the_bare_kind():
    """A part kind this loop has never been taught about must summarise to
    its name, never crash a turn -- doc 01 section 4 grows the part
    vocabulary independently of this module."""
    result = SkillResult(ok=True, parts=[{"kind": "artifact", "payload": {"url": "x"}}], proof=[])

    assert tool_result_digest(METRIC_QUERY, result) == ("data_qa.metric_query ok\nparts: artifact")


def test_digest_empty_parts_and_empty_proof():
    assert tool_result_digest(METRIC_QUERY, SkillResult(ok=True)) == (
        "data_qa.metric_query ok\nparts: (none)"
    )


def test_digest_failure_pins_the_problem_summary():
    result = SkillResult(
        ok=False, error=problem(422, "invalid arguments", "metrics: Field required")
    )

    assert tool_result_digest(METRIC_QUERY, result) == (
        "data_qa.metric_query error\nproblem: 422 invalid arguments: metrics: Field required"
    )


# ===========================================================================
# Loop: end_turn immediately (no tools)
# ===========================================================================


def test_end_turn_immediately_returns_the_reply(settings):
    result, sink, provider = _run(settings, [_end_turn("I can help with that.")])

    assert result.status == "ok"
    assert result.text == "I can help with that."
    assert result.problem is None
    assert result.tool_records == ()
    assert len(result.llm_records) == 1
    assert sink.kinds == ["llm_call"]
    assert len(provider.calls) == 1


def test_end_turn_llm_record_fields(settings):
    result, _, _ = _run(settings, [_end_turn("hi", input_tokens=1234, output_tokens=7)])

    assert result.llm_records == (
        LLMRecord(
            call_seq=1,
            role="router",
            provider="bedrock",
            model="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            stop_reason="end_turn",
            input_tokens=1234,
            output_tokens=7,
        ),
    )


# ===========================================================================
# Loop: single-tool happy path
# ===========================================================================


def test_single_tool_happy_path_event_sequence(settings):
    _, sink, _ = _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("Here you go.")])

    assert sink.kinds == ["llm_call", "tool_start", "tool_done", "llm_call"]


def test_single_tool_happy_path_record_counts(settings):
    result, _, _ = _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("Here you go.")])

    assert result.status == "ok"
    assert result.text == "Here you go."
    assert len(result.llm_records) == 2
    assert len(result.tool_records) == 1


def test_single_tool_happy_path_tool_record_fields(settings):
    result, _, _ = _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("Here you go.")])

    record = result.tool_records[0]
    assert record.tool_seq == 1
    assert record.skill_id == METRIC_QUERY
    assert record.arguments == GOOD_ARGS
    assert record.status == "ok"
    assert record.result_digest == (
        "data_qa.metric_query ok\n"
        "parts: table(rows=3)\n"
        "proof: Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V | Backend: synthetic | "
        "Period: 2026-04-01..2026-05-01 | Filters: LOC_NM IN (Singapore) | "
        "Group by: CUST_NM (top 5) | Rows: 3"
    )
    # Measured, never pinned to a value -- the one permitted clock reading.
    assert isinstance(record.duration_ms, int)
    assert record.duration_ms >= 0


def test_tool_record_arguments_are_a_copy_of_the_tool_calls_own_dict(settings):
    """A record is a value handed to Phase 6's run-log writer; mutating the
    ToolCall it came from must never rewrite it after the fact."""
    arguments = dict(GOOD_ARGS)
    result, _, _ = _run(settings, [_tool_use(_call(arguments)), _end_turn("ok")])

    arguments["top_n"] = 999

    assert result.tool_records[0].arguments["top_n"] == 5


def test_two_tool_chain_records_and_events(settings):
    script = [
        _tool_use(_call(GOOD_ARGS, call_id="tool-1")),
        _tool_use(_call({**GOOD_ARGS, "metrics": ["VOLUME"]}, call_id="tool-2")),
        _end_turn("Both answered."),
    ]
    result, sink, _ = _run(settings, script)

    assert result.status == "ok"
    assert [record.tool_seq for record in result.tool_records] == [1, 2]
    assert [record.call_seq for record in result.llm_records] == [1, 2, 3]
    assert sink.kinds == [
        "llm_call",
        "tool_start",
        "tool_done",
        "llm_call",
        "tool_start",
        "tool_done",
        "llm_call",
    ]


def test_two_tool_calls_in_one_response_share_one_tool_result_message(settings):
    """Converse requires alternating roles and one toolResult block per
    toolUse block of the preceding assistant turn -- so two PARALLEL tool
    calls produce exactly two ToolRecords but only ONE appended user
    message, carrying both result blocks in call order."""
    script = [
        _tool_use(
            _call(GOOD_ARGS, call_id="tool-a"),
            _call({**GOOD_ARGS, "metrics": ["VOLUME"]}, call_id="tool-b"),
        ),
        _end_turn("Both answered."),
    ]
    result, sink, provider = _run(settings, script)

    assert len(result.tool_records) == 2
    assert sink.kinds == [
        "llm_call",
        "tool_start",
        "tool_done",
        "tool_start",
        "tool_done",
        "llm_call",
    ]
    messages = provider.calls[1]["messages"]
    assert len(messages) == 3  # the window turn + assistant echo + one results message
    assert messages[2]["role"] == "user"
    ids = [block["toolResult"]["toolUseId"] for block in messages[2]["content"]]
    assert ids == ["tool-a", "tool-b"]


# ===========================================================================
# Loop: the request the provider actually receives
# ===========================================================================


def test_system_prompt_is_assembled_once_and_reused_every_call(settings):
    _, _, provider = _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("done")])

    systems = [call["system"] for call in provider.calls]
    assert len(systems) == 2
    assert systems[0] == systems[1]


def test_system_prompt_carries_the_router_charter_skills_and_guardrails(settings):
    _, _, provider = _run(settings, [_end_turn("done")])

    system = provider.calls[0]["system"]
    assert "=== BASE SYSTEM PROMPT ===" in system
    assert "=== CONVERSATION STATE ===" in system
    assert METRIC_QUERY in system  # skill_lines
    assert "PORT_NM is not certified; use LOC_NM instead." in system  # negative constraints
    assert "MARGIN: SUM(GROSS_PROFIT) / NULLIF(SUM(FIXED_TONS), 0)" in system  # metric defs
    assert "Mode: default" in system  # the conversation state block


def test_system_prompt_carries_the_conversation_state_from_the_context(settings):
    slots = ConversationSlots(customer="Northstar Lines", mode="existing_customer")
    _, _, provider = _run(settings, [_end_turn("done")], slots=slots)

    system = provider.calls[0]["system"]
    assert "Mode: existing_customer" in system
    assert "Carried customer: Northstar Lines" in system


def test_every_provider_call_carries_the_full_tool_schemas(settings):
    _, _, provider = _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("done")])

    for call in provider.calls:
        assert call["tools"] == REGISTRY.tool_schemas
        assert [schema["name"] for schema in call["tools"]] == REGISTRY.skill_ids


def test_tool_result_message_is_digest_only_and_never_carries_bulk_rows(settings):
    result, _, provider = _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("done")])

    second_request = json.dumps(provider.calls[1]["messages"], default=str)
    for customer in _CUSTOMERS:
        assert customer not in second_request
    tool_result = provider.calls[1]["messages"][2]["content"][0]["toolResult"]
    assert tool_result["status"] == "success"
    assert tool_result["content"] == [{"text": result.tool_records[0].result_digest}]


def test_assistant_echo_uses_dotted_skill_ids_never_wire_names(settings):
    """The loop deals in dotted skill ids everywhere; translating them into
    a provider's own tool-name grammar is that provider's business (see
    ``bedrock.py``'s translation pair), never this module's."""
    _, _, provider = _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("done")])

    echo = provider.calls[1]["messages"][1]
    assert echo["role"] == "assistant"
    assert echo["content"] == [
        {"toolUse": {"toolUseId": "tool-1", "name": METRIC_QUERY, "input": GOOD_ARGS}}
    ]
    assert "data_qa__metric_query" not in json.dumps(provider.calls[1]["messages"], default=str)


def test_the_callers_window_list_is_never_mutated(settings):
    window = [{"role": "user", "content": USER_QUESTION}]
    _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("done")], window=window)

    assert window == [{"role": "user", "content": USER_QUESTION}]


def test_each_provider_call_receives_the_history_as_it_stood_at_call_time(settings):
    """Every call gets a SNAPSHOT of the message list (``RoleClient.invoke``),
    never the loop's own growing one -- so a recording double, and a provider
    that keeps a request to retry it, both hold what THIS call carried rather
    than what the history became two iterations later. Two tool iterations
    then a reply: 1, then 3, then 5 messages at call time."""
    script = [
        _tool_use(_call(GOOD_ARGS, call_id="tool-1")),
        _tool_use(_call({**GOOD_ARGS, "metrics": ["VOLUME"]}, call_id="tool-2")),
        _end_turn("Both answered."),
    ]
    _, _, provider = _run(settings, script)

    assert [len(call["messages"]) for call in provider.calls] == [1, 3, 5]
    assert provider.calls[0]["messages"] == WINDOW


# ===========================================================================
# Loop: events, payload by payload
# ===========================================================================


def test_llm_call_event_payload_is_pinned(settings):
    _, sink, _ = _run(settings, [_end_turn("done")])

    assert sink.events[0] == (
        "llm_call",
        {
            "call_seq": 1,
            "role": "router",
            "provider": "bedrock",
            "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        },
    )


def test_tool_start_event_payload_is_pinned(settings):
    _, sink, _ = _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("done")])

    kind, payload = sink.events[1]
    assert kind == "tool_start"
    assert payload == {"tool_seq": 1, "skill_id": METRIC_QUERY, "arguments": GOOD_ARGS}


def test_tool_done_event_carries_the_parts_the_ui_needs(settings):
    """The context-hygiene split, stated in one test: the MODEL gets the
    digest (proven above), the UI gets the parts -- through the sink Phase 6
    binds to SSE. TurnResult deliberately carries neither."""
    _, sink, _ = _run(settings, [_tool_use(_call(GOOD_ARGS)), _end_turn("done")])

    kind, payload = sink.events[2]
    assert kind == "tool_done"
    assert payload["tool_seq"] == 1
    assert payload["skill_id"] == METRIC_QUERY
    assert payload["status"] == "ok"
    assert payload["problem"] is None
    assert payload["duration_ms"] >= 0
    assert payload["digest"].startswith("data_qa.metric_query ok")
    assert [part["kind"] for part in payload["parts"]] == ["table"]
    assert payload["parts"][0]["payload"]["rows"][0][0] == "Northstar Lines"
    assert payload["proof"][-1] == "Rows: 3"


class _ArgumentMutatingSink(RecordingSink):
    """A sink that EDITS the arguments it is handed before recording them.

    Phase 6 binds the sink to an SSE stream and any later consumer (a run-log
    tap, a dev harness) is equally free to normalize a payload it received --
    a sink is an observer, and an observer must not be able to rewrite the
    turn it is observing.
    """

    def emit(self, kind: str, payload: dict) -> None:
        if kind == "tool_start":
            payload["arguments"]["top_n"] = 1
        super().emit(kind, payload)


def test_a_sink_editing_the_tool_start_arguments_changes_nothing_downstream(settings):
    """The event payload carries a COPY of ``ToolCall.arguments``, so a sink
    that mutates it cannot reach the dispatch that follows (still top 5 ->
    3 rows), the record Phase 6 will log, or the assistant echo the next
    request carries."""
    sink = _ArgumentMutatingSink()
    result, _, provider = _run(
        settings, [_tool_use(_call(dict(GOOD_ARGS))), _end_turn("done")], sink=sink
    )

    record = result.tool_records[0]
    assert record.arguments["top_n"] == 5
    assert "parts: table(rows=3)" in record.result_digest
    echo = provider.calls[1]["messages"][1]["content"][0]["toolUse"]
    assert echo["input"]["top_n"] == 5
    # The sink kept what it was given, edit and all -- proving the mutation
    # really happened and this test is not passing for want of one.
    assert sink.events[1][1]["arguments"]["top_n"] == 1


def test_tool_done_event_carries_the_problem_on_a_failed_dispatch(settings):
    script = [_tool_use(_call(MISSING_METRICS_ARGS)), _end_turn("sorry")]
    _, sink, _ = _run(settings, script)

    _, payload = sink.events[2]
    assert payload["status"] == "error"
    assert payload["parts"] == []
    assert payload["problem"]["status"] == 422


# ===========================================================================
# Loop: self-correction (one retry per skill), then failure
# ===========================================================================


def test_self_correction_invalid_args_then_corrected_call_succeeds(settings):
    script = [
        _tool_use(_call(MISSING_METRICS_ARGS, call_id="bad")),
        _tool_use(_call(GOOD_ARGS, call_id="good")),
        _end_turn("Fixed it."),
    ]
    result, sink, _ = _run(settings, script)

    assert result.status == "ok"
    assert result.text == "Fixed it."
    assert [record.status for record in result.tool_records] == ["error", "ok"]
    assert "turn_error" not in sink.kinds


def test_self_correction_sends_the_problem_json_back_as_the_tool_result(settings):
    script = [
        _tool_use(_call(MISSING_METRICS_ARGS, call_id="bad")),
        _tool_use(_call(GOOD_ARGS, call_id="good")),
        _end_turn("Fixed it."),
    ]
    _, _, provider = _run(settings, script)

    tool_result = provider.calls[1]["messages"][2]["content"][0]["toolResult"]
    assert tool_result["toolUseId"] == "bad"
    assert tool_result["status"] == "error"
    returned = tool_result["content"][0]["json"]
    assert returned["status"] == 422
    assert returned["title"] == "invalid arguments"
    assert "metrics" in returned["detail"]
    assert set(returned) == {"type", "title", "detail", "status"}  # the registry's own shape


def test_same_skill_failing_twice_fails_the_turn(settings):
    script = [
        _tool_use(_call(MISSING_METRICS_ARGS, call_id="bad-1")),
        _tool_use(_call(MISSING_METRICS_ARGS, call_id="bad-2")),
        _end_turn("never reached"),
    ]
    result, sink, provider = _run(settings, script)

    assert result.status == "error"
    assert result.text == ""
    assert result.problem["status"] == 422
    assert len(result.tool_records) == 2
    assert sink.kinds[-1] == "turn_error"
    assert sink.events[-1] == ("turn_error", {"problem": result.problem})
    assert len(provider.calls) == 2  # the turn stopped; the third script entry is untouched


def test_the_failing_turn_carries_the_second_problem_not_the_first(settings):
    """ "Fails the turn with THAT problem": the one that just came back, not
    the one already returned to the model a chance ago. Pinned with two
    DIFFERENT 422s from the same skill so the two are told apart byte by
    byte."""
    script = [
        _tool_use(_call(MISSING_METRICS_ARGS, call_id="bad-1")),
        _tool_use(_call(INVERTED_PERIOD_ARGS, call_id="bad-2")),
    ]
    result, _, _ = _run(settings, script)

    assert result.status == "error"
    assert "period" in result.problem["detail"]
    assert "Field required" not in result.problem["detail"]


def test_unknown_skill_404_follows_the_same_self_correction_contract(settings):
    script = [
        _tool_use(_call({}, call_id="oops", skill="data_qa.made_up")),
        _tool_use(_call(GOOD_ARGS, call_id="good")),
        _end_turn("Recovered."),
    ]
    result, _, provider = _run(settings, script)

    assert result.status == "ok"
    assert [record.skill_id for record in result.tool_records] == ["data_qa.made_up", METRIC_QUERY]
    returned = provider.calls[1]["messages"][2]["content"][0]["toolResult"]["content"][0]["json"]
    assert returned["status"] == 404
    assert returned["title"] == "unknown skill"


def test_unknown_skill_404_twice_fails_the_turn(settings):
    script = [
        _tool_use(_call({}, call_id="oops-1", skill="data_qa.made_up")),
        _tool_use(_call({}, call_id="oops-2", skill="data_qa.made_up")),
    ]
    result, sink, _ = _run(settings, script)

    assert result.status == "error"
    assert result.problem["status"] == 404
    assert sink.kinds[-1] == "turn_error"


def test_same_skill_failing_non_consecutively_still_fails_the_turn(settings):
    """A skill's one correction chance is spent for the whole TURN, not until
    its next success: fail -> succeed -> fail on the same id ends the turn
    with the second failure's problem.

    Permanent rather than reasoned about, because the tempting simplification
    -- clearing the id from ``spent_corrections`` when it later succeeds --
    leaves every other test in this section green while letting one skill
    alternate failure and success for all ten iterations of a turn."""
    script = [
        _tool_use(_call(MISSING_METRICS_ARGS, call_id="bad-1")),
        _tool_use(_call(GOOD_ARGS, call_id="good")),
        _tool_use(_call(INVERTED_PERIOD_ARGS, call_id="bad-2")),
        _end_turn("never reached"),
    ]
    result, sink, provider = _run(settings, script)

    assert result.status == "error"
    assert [(record.tool_seq, record.status) for record in result.tool_records] == [
        (1, "error"),
        (2, "ok"),
        (3, "error"),
    ]
    assert result.problem["status"] == 422
    assert "period" in result.problem["detail"]  # the LAST failure, not the first
    assert sink.kinds[-1] == "turn_error"
    assert len(provider.calls) == 3  # the scripted reply is never reached


def test_two_different_skills_failing_once_each_does_not_fail_the_turn(settings):
    """The contract is per-SKILL, not per-turn: two different names each get
    their own single self-correction chance."""
    script = [
        _tool_use(_call({}, call_id="a", skill="data_qa.made_up_one")),
        _tool_use(_call({}, call_id="b", skill="data_qa.made_up_two")),
        _end_turn("I could not run that."),
    ]
    result, sink, _ = _run(settings, script)

    assert result.status == "ok"
    assert len(result.tool_records) == 2
    assert "turn_error" not in sink.kinds


# ===========================================================================
# Loop: iteration cap and provider failures
# ===========================================================================


def test_iteration_cap_reached_returns_the_pinned_problem(settings):
    script = [_tool_use(_call(GOOD_ARGS, call_id=f"t{n}")) for n in range(11)]
    result, sink, provider = _run(settings, script, max_iterations=10)

    assert result.status == "error"
    assert result.text == ""
    assert result.problem["title"] == "agent loop exceeded max iterations"
    assert result.problem["detail"] == "cap 10"
    assert result.problem == {
        "type": "about:blank",
        "title": "agent loop exceeded max iterations",
        "detail": "cap 10",
        "status": 500,
    }
    assert len(result.llm_records) == 10
    assert len(result.tool_records) == 10
    assert len(provider.calls) == 10  # the 11th scripted response is never reached
    assert sink.kinds[-1] == "turn_error"


def test_iteration_cap_detail_names_the_cap_actually_used(settings):
    script = [_tool_use(_call(GOOD_ARGS, call_id=f"t{n}")) for n in range(3)]
    result, _, _ = _run(settings, script, max_iterations=2)

    assert result.problem["detail"] == "cap 2"
    assert len(result.llm_records) == 2


def test_provider_transport_error_fails_the_turn_instead_of_replying(settings):
    """``BedrockProvider`` never raises: a throttled or rejected call comes
    back as ``stop_reason="error"`` with the reason in ``text``. Returning
    that text as the assistant's reply would put "bedrock error:
    ThrottlingException" in a user's chat as if the model had said it."""
    error_response = LLMResponse(
        text="bedrock error: ThrottlingException",
        tool_calls=(),
        stop_reason="error",
        input_tokens=0,
        output_tokens=0,
    )
    result, sink, _ = _run(settings, [error_response])

    assert result.status == "error"
    assert result.text == ""
    assert result.problem == {
        "type": "about:blank",
        "title": "llm provider error",
        "detail": "bedrock error: ThrottlingException",
        "status": 502,
    }
    assert result.llm_records[0].stop_reason == "error"
    assert sink.kinds == ["llm_call", "turn_error"]


def test_unsupported_stop_reason_fails_the_turn(settings):
    weird = LLMResponse(
        text="", tool_calls=(), stop_reason="content_filtered", input_tokens=0, output_tokens=0
    )
    result, _, _ = _run(settings, [weird])

    assert result.status == "error"
    assert result.problem["detail"] == (
        "provider reported unsupported stop_reason 'content_filtered'"
    )


def test_empty_end_turn_reply_fails_the_turn_instead_of_returning_a_blank_ok(settings):
    """The symmetric half of the empty-tool_use guard below: Bedrock folds
    ``content_filtered``/``guardrail_intervened`` into ``end_turn`` with no
    text (``bedrock.py``'s deliberate lossy normalization), and returning
    that as a successful turn would put an empty assistant bubble in the chat
    and leave Phase 6's run log with nothing to explain it."""
    result, sink, _ = _run(settings, [_end_turn("")])

    assert result.status == "error"
    assert result.text == ""
    assert result.problem == {
        "type": "about:blank",
        "title": "llm provider error",
        "detail": "provider reported stop_reason 'end_turn' with an empty reply",
        "status": 502,
    }
    assert sink.kinds == ["llm_call", "turn_error"]
    assert sink.events[-1] == ("turn_error", {"problem": result.problem})


def test_whitespace_only_end_turn_reply_fails_the_turn_the_same_way(settings):
    """Whitespace is not a reply: a turn whose whole text is spaces and
    newlines renders as the identical empty bubble."""
    result, _, _ = _run(settings, [_end_turn("  \n\t ")])

    assert result.status == "error"
    assert result.problem["detail"] == (
        "provider reported stop_reason 'end_turn' with an empty reply"
    )


def test_tool_use_with_no_tool_calls_fails_the_turn_instead_of_spinning(settings):
    """Fail closed rather than re-send an identical request until the cap:
    a tool_use response with nothing to dispatch is a provider bug."""
    empty = LLMResponse(
        text="", tool_calls=(), stop_reason="tool_use", input_tokens=0, output_tokens=0
    )
    result, _, provider = _run(settings, [empty, _end_turn("never reached")])

    assert result.status == "error"
    assert result.problem["detail"] == "provider reported stop_reason 'tool_use' with no tool calls"
    assert len(provider.calls) == 1


# ===========================================================================
# Loop: determinism and the stub/live seam
# ===========================================================================


def _comparable(result: TurnResult) -> TurnResult:
    """The same TurnResult with every measured duration zeroed -- duration
    is the one value in this loop that a re-run is allowed to change."""
    return replace(
        result,
        tool_records=tuple(replace(record, duration_ms=0) for record in result.tool_records),
    )


def test_run_turn_is_deterministic(settings):
    script = [_tool_use(_call(GOOD_ARGS)), _end_turn("Here you go.")]
    first, first_sink, _ = _run(settings, script)
    second, second_sink, _ = _run(settings, script)

    assert _comparable(first) == _comparable(second)
    assert first_sink.kinds == second_sink.kinds


def test_stub_seam_substitution_proof(monkeypatch):
    """Doc 08's validation criterion, at the level the agent loop actually
    runs: the SAME ``run_turn`` call expression, twice, with nothing
    different but which provider the ``RoleClient`` was handed --
    ``{"stub": ...}`` under ``LLM_MODE=stub``, ``{"bedrock": ...}`` under
    ``LLM_MODE=live``. No branch, no argument and no keyword in the call
    changes, which is the whole claim: swapping the mode changes no calling
    code. (The "live" provider here is a StubProvider used purely as a
    stand-in registry entry -- a real BedrockProvider needs credentials;
    what is under test is the substitution, not Bedrock.)"""
    script = [_tool_use(_call(GOOD_ARGS)), _end_turn("Here you go.")]

    def run_once(role_client: RoleClient, run_settings: Settings) -> TurnResult:
        return run_turn(
            role_client=role_client,
            registry=REGISTRY,
            context=_context(run_settings),
            prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
            user_instruction="",
            memory_doc="",
            parsed=None,
            window=WINDOW,
            sink=RecordingSink(),
            max_iterations=10,
        )

    stub_settings = _settings(monkeypatch, LLM_MODE="stub", LLM_PROFILE="bedrock")
    stub_result = run_once(_role_client(stub_settings, script, key="stub"), stub_settings)

    live_settings = _settings(monkeypatch, LLM_MODE="live", LLM_PROFILE="bedrock")
    live_result = run_once(_role_client(live_settings, script, key="bedrock"), live_settings)

    assert _comparable(stub_result) == _comparable(live_result)
    assert stub_result.status == "ok"


def test_unregistered_provider_surfaces_before_any_tool_runs(monkeypatch):
    """The hardened RoleClient error (see ``test_llm_types_roles.py`` for
    the byte pin) reaches the loop's caller as-is: run_turn does not swallow
    a misconfiguration into a turn-level problem, because a missing provider
    is a deployment fault, not a conversation outcome."""
    live_settings = _settings(monkeypatch, LLM_MODE="live", LLM_PROFILE="bedrock")
    sink = RecordingSink()

    with pytest.raises(RuntimeError) as err:
        run_turn(
            role_client=RoleClient(live_settings, providers={}),
            registry=REGISTRY,
            context=_context(live_settings),
            prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
            user_instruction="",
            memory_doc="",
            parsed=None,
            window=WINDOW,
            sink=sink,
            max_iterations=10,
        )

    assert "no provider registered for key 'bedrock'" in str(err.value)
    assert sink.kinds == ["llm_call"]


# ===========================================================================
# Loop -> BedrockProvider, chained: the wire name lives only on the wire
# ===========================================================================


class _RecordingConverseClient:
    """A bedrock-runtime client double -- the role ``_FakeClient`` plays in
    ``test_llm_bedrock.py``, kept local so this file needs no cross-test
    import. Records every ``converse`` request and replays one scripted
    ConverseResponse per call."""

    def __init__(self, responses):
        self.requests: list[dict] = []
        self._responses = list(responses)

    def converse(self, **kwargs):
        self.requests.append(kwargs)
        return self._responses.pop(0)


# The Bedrock-safe wire spelling of METRIC_QUERY -- what toolConfig carries
# and what the model echoes back, since a dotted ToolName is rejected by
# Converse (see bedrock.py's translation paragraph).
_WIRE_NAME = "data_qa__metric_query"

_CONVERSE_TOOL_USE = {
    "output": {
        "message": {
            "role": "assistant",
            "content": [
                {"toolUse": {"toolUseId": "tool-1", "name": _WIRE_NAME, "input": dict(GOOD_ARGS)}}
            ],
        }
    },
    "stopReason": "tool_use",
    "usage": {"inputTokens": 1000, "outputTokens": 40, "totalTokens": 1040},
}

_CONVERSE_END_TURN = {
    "output": {"message": {"role": "assistant", "content": [{"text": "Here you go."}]}},
    "stopReason": "end_turn",
    "usage": {"inputTokens": 1200, "outputTokens": 25, "totalTokens": 1225},
}


def test_loop_through_bedrock_provider_keeps_the_wire_name_on_the_wire(monkeypatch):
    """The two halves pinned separately above and in ``test_llm_bedrock.py``,
    chained for real: the loop, a ``RoleClient`` in live mode, and a
    ``BedrockProvider`` over a fake ``converse`` client, running two
    iterations so the SECOND request is built from the loop's own history.

    What it proves that neither half can alone: the Bedrock-safe wire name
    exists only inside the converse payload (the tool definitions, and the
    assistant echo the provider translated on the way out), while every
    record, event and digest the loop produced stays dotted -- and the fake
    rows' customer names, which reached the UI through ``tool_done``, are
    nowhere in the request that goes back to the model."""
    from poseidon.core.llm.bedrock import BedrockProvider

    live_settings = _settings(monkeypatch, LLM_MODE="live", LLM_PROFILE="bedrock")
    client = _RecordingConverseClient([_CONVERSE_TOOL_USE, _CONVERSE_END_TURN])
    sink = RecordingSink()

    result = run_turn(
        role_client=RoleClient(
            live_settings, providers={"bedrock": BedrockProvider(client=client)}
        ),
        registry=REGISTRY,
        context=_context(live_settings),
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        user_instruction="",
        memory_doc="",
        parsed=None,
        window=WINDOW,
        sink=sink,
        max_iterations=10,
    )

    assert result.status == "ok"
    assert result.text == "Here you go."

    # Nothing the loop produced ever spells the wire name.
    records = json.dumps([asdict(record) for record in result.tool_records], default=str)
    assert result.tool_records[0].skill_id == METRIC_QUERY
    assert result.tool_records[0].result_digest.startswith(f"{METRIC_QUERY} ok")
    assert _WIRE_NAME not in records
    assert _WIRE_NAME not in json.dumps(sink.events, default=str)
    assert METRIC_QUERY in json.dumps(sink.events, default=str)

    # The wire name exists exactly where Converse requires it: the tool
    # definitions, and the assistant turn echoed back on iteration 2.
    # Membership, not exact list equality (Task 4, Phase 7): REGISTRY now
    # also carries research.web_research's own translated wire name
    # alongside metric_query's -- this test's actual claim is about ONE
    # tool's own round trip, never "the registry holds exactly one tool",
    # so asserting membership stays correct regardless of how many more
    # skills register in the future.
    second = client.requests[1]
    wire_names = [tool["toolSpec"]["name"] for tool in second["toolConfig"]["tools"]]
    assert _WIRE_NAME in wire_names
    assert second["messages"][1]["content"][0]["toolUse"]["name"] == _WIRE_NAME
    assert len(client.requests) == 2

    # Context hygiene across the same seam: the rows went to the UI, not back
    # into the window.
    payload = json.dumps(second, default=str)
    for customer in _CUSTOMERS:
        assert customer not in payload


# ===========================================================================
# titles.py -- the utility role
# ===========================================================================


def test_title_prompt_file_exists():
    assert (DEFAULT_PROMPTS_DIR / "utility" / "title.md").is_file()


def test_title_prompt_renders_as_one_line_naming_the_cap():
    """The cap lives in Python (``TITLE_MAX_CHARS``) and reaches the prompt
    through its only placeholder, so the instruction the model reads can
    never disagree with the truncation the code applies."""
    rendered = PromptRegistry(DEFAULT_PROMPTS_DIR).render(TITLE_PROMPT, max_chars=TITLE_MAX_CHARS)

    assert rendered.strip().count("\n") == 0
    assert "60" in rendered


def test_title_role_and_cap_are_pinned():
    assert UTILITY_ROLE == "utility"
    assert TITLE_MAX_CHARS == 60


def test_title_for_uses_the_utility_role_and_sends_the_text_as_the_user_turn(settings):
    provider = StubProvider([_end_turn("Singapore GP breakdown")])
    role_client = RoleClient(settings, providers={"stub": provider})

    assert title_for(USER_QUESTION, role_client) == "Singapore GP breakdown"
    assert provider.calls[0]["model"] == "us.amazon.nova-lite-v1:0"  # the utility tier
    assert provider.calls[0]["messages"] == [{"role": "user", "content": USER_QUESTION}]
    assert provider.calls[0]["tools"] == []
    assert "60" in provider.calls[0]["system"]


def test_title_for_strips_quotes_and_surrounding_whitespace(settings):
    provider = StubProvider([_end_turn('  "Top GP customers"  ')])
    role_client = RoleClient(settings, providers={"stub": provider})

    assert title_for(USER_QUESTION, role_client) == "Top GP customers"


def test_title_for_collapses_internal_whitespace(settings):
    provider = StubProvider([_end_turn("'Top GP customers\n for Singapore'")])
    role_client = RoleClient(settings, providers={"stub": provider})

    assert title_for(USER_QUESTION, role_client) == "Top GP customers for Singapore"


def test_title_for_hard_caps_at_sixty_characters(settings):
    long_title = "A" * 100
    provider = StubProvider([_end_turn(long_title)])
    role_client = RoleClient(settings, providers={"stub": provider})

    result = title_for(USER_QUESTION, role_client)

    assert len(result) == 60
    assert result == "A" * 60


def test_title_for_provider_error_yields_no_title(settings):
    """A transport failure's text ("bedrock error: ...") must never become a
    conversation's title; the caller decides the fallback."""
    error_response = LLMResponse(
        text="bedrock error: ThrottlingException",
        tool_calls=(),
        stop_reason="error",
        input_tokens=0,
        output_tokens=0,
    )
    role_client = RoleClient(settings, providers={"stub": StubProvider([error_response])})

    assert title_for(USER_QUESTION, role_client) == ""


def test_title_for_accepts_an_injected_prompt_registry(settings, tmp_path):
    (tmp_path / "utility").mkdir()
    (tmp_path / "utility" / "title.md").write_text("cap {{ max_chars }}", encoding="utf-8")
    provider = StubProvider([_end_turn("Injected")])
    role_client = RoleClient(settings, providers={"stub": provider})

    assert title_for("x", role_client, PromptRegistry(tmp_path)) == "Injected"
    assert provider.calls[0]["system"] == "cap 60"


# ===========================================================================
# routing_cases.yml -- doc 03 section 6's five cases
# ===========================================================================

DOC_CASE_IDS = [
    "existing_brief_by_name",
    "ambiguous_customer_clarifies",
    "default_data_qa_breakdown",
    "pivot_to_research_with_carry",
    "post_brief_pivot_to_internal",
]

ROUTING_CASES = yaml.safe_load(ROUTING_CASES_PATH.read_text(encoding="utf-8"))
OFFLINE_CASES = [case for case in ROUTING_CASES if not case["execution"].get("live_only")]

# doc 03 section 6 writes its expectations in the vocabulary of the QUESTION
# ("metric", "port"), not of a skill's ``Args`` model ("metrics", a LOC_NM
# dimension filter). This table is the one place that gap is bridged, so the
# doc's cases stay transcribed verbatim and still execute.
_ARGS_SUBSET_DIMENSION = {"customer": "CUST_NM", "port": "LOC_NM"}

# doc 03 section 6's setup vocabulary -> the real ConversationSlots fields.
_MODE_ALIASES = {"default": "default", "existing": "existing_customer", "prospect": "new_prospect"}


def _filter_values(arguments: dict, column: str) -> list[str]:
    return [
        value
        for entry in arguments.get("filters") or []
        if entry.get("column") == column
        for value in entry.get("values") or []
    ]


def _candidate_values(key: str, arguments: dict) -> list[str]:
    if key == "metric":
        return [str(value) for value in arguments.get("metrics") or []]
    if key == "group_by":
        group_by = arguments.get("group_by")
        return [] if group_by is None else [str(group_by)]
    values = _filter_values(arguments, _ARGS_SUBSET_DIMENSION[key])
    top_level = arguments.get(key)
    if isinstance(top_level, str):
        values.append(top_level)
    return values


def _matches(pattern: str, value: str) -> bool:
    """Case-insensitive equality, or a prefix match when the doc's expected
    value ends in ``*`` (its own convention: ``"MAERSK*"``, ``"SINGAPORE*"``)
    -- never an exact match on free text, per doc 03 section 6 mode 2."""
    if pattern.endswith("*"):
        return value.casefold().startswith(pattern[:-1].casefold())
    return value.casefold() == pattern.casefold()


def assert_args_subset(args_subset: dict, arguments: dict) -> None:
    for key, pattern in args_subset.items():
        values = _candidate_values(key, arguments)
        assert any(_matches(str(pattern), value) for value in values), (
            f"expected {key}={pattern!r} among {values!r} (arguments: {arguments!r})"
        )


def _slots_for(case: dict) -> ConversationSlots:
    setup = case.get("setup") or {}
    state = setup.get("state") or {}
    kwargs: dict = {"mode": _MODE_ALIASES[setup.get("mode", "default")]}
    if "active_customer" in state:
        kwargs["customer"] = state["active_customer"]
    if "mentioned_ports" in state:
        kwargs["pass_through"] = tuple(("mentioned port", p) for p in state["mentioned_ports"])
    return ConversationSlots(**kwargs)


def _script_from(case: dict) -> list[LLMResponse]:
    responses = []
    for step in case["execution"]["stub_script"]:
        if step["stop_reason"] == "tool_use":
            call = step["tool_use"]
            responses.append(
                _tool_use(
                    ToolCall(id=call["id"], name=call["skill"], arguments=call["arguments"]),
                    input_tokens=step["input_tokens"],
                    output_tokens=step["output_tokens"],
                )
            )
        else:
            responses.append(
                _end_turn(
                    step["text"],
                    input_tokens=step["input_tokens"],
                    output_tokens=step["output_tokens"],
                )
            )
    return responses


def test_routing_cases_are_doc_03_section_6_verbatim_in_order():
    assert [case["id"] for case in ROUTING_CASES] == DOC_CASE_IDS


def test_every_routing_case_declares_user_expect_and_execution():
    for case in ROUTING_CASES:
        assert case["user"], case["id"]
        assert case["expect"], case["id"]
        assert "execution" in case, case["id"]


def test_exactly_three_cases_are_live_only_and_each_names_its_owning_phase():
    """Task 4 (Phase 7): pivot_to_research_with_carry moves from live_only
    to offline (research.web_research is now registered and enabled), so
    the live-only count drops from 4 to 3 and OFFLINE_CASES grows from 1
    to 2 -- see the sibling test below."""
    live_only = [case for case in ROUTING_CASES if case["execution"].get("live_only")]

    assert len(live_only) == 3
    assert len(OFFLINE_CASES) == 2
    for case in live_only:
        reason = case["execution"]["reason"]
        assert "Phase" in reason, case["id"]


def test_the_offline_cases_are_metric_query_and_research_each_carrying_a_stub_script():
    """OFFLINE_CASES preserves ROUTING_CASES' own order, which
    ``test_routing_cases_are_doc_03_section_6_verbatim_in_order`` already
    pins to ``DOC_CASE_IDS`` -- so indexing ``[0]``/``[1]`` here is reading
    a guaranteed fixed order, not assuming one."""
    assert len(OFFLINE_CASES) == 2

    metric_case = OFFLINE_CASES[0]
    assert metric_case["id"] == "default_data_qa_breakdown"
    assert metric_case["expect"]["skill"] == METRIC_QUERY
    assert len(metric_case["execution"]["stub_script"]) == 2

    research_case = OFFLINE_CASES[1]
    assert research_case["id"] == "pivot_to_research_with_carry"
    assert research_case["expect"]["skill"] == RESEARCH
    assert len(research_case["execution"]["stub_script"]) == 2


def test_every_case_setup_maps_onto_real_conversation_slots():
    """The live executor builds its context from these; proving the mapping
    offline means a live run can only fail for LIVE reasons."""
    slots = {case["id"]: _slots_for(case) for case in ROUTING_CASES}

    assert slots["existing_brief_by_name"].mode == "existing_customer"
    assert slots["default_data_qa_breakdown"].mode == "default"
    assert slots["pivot_to_research_with_carry"].customer == "CUSTOMER_X"
    assert slots["post_brief_pivot_to_internal"].mode == "new_prospect"
    assert slots["post_brief_pivot_to_internal"].pass_through == (("mentioned port", "ROTTERDAM"),)


# --- the args_subset matcher itself: it has to be able to FAIL ------------


def test_args_subset_matcher_accepts_the_doc_expectation():
    assert_args_subset(
        {"metric": "GP", "group_by": "CUST_NM", "port": "SINGAPORE*"},
        GOOD_ARGS,
    )


@pytest.mark.parametrize(
    "args_subset",
    [
        pytest.param({"metric": "VOLUME"}, id="wrong_metric"),
        pytest.param({"group_by": "LOC_NM"}, id="wrong_group_by"),
        pytest.param({"port": "ROTTERDAM"}, id="wrong_port"),
        pytest.param({"port": "SINGAPOREAN"}, id="prefix_star_is_not_a_substring_match"),
        pytest.param({"customer": "MAERSK*"}, id="dimension_absent_entirely"),
    ],
)
def test_args_subset_matcher_rejects_a_wrong_expectation(args_subset):
    with pytest.raises(AssertionError):
        assert_args_subset(args_subset, GOOD_ARGS)


def test_args_subset_matcher_rejects_an_unknown_expectation_key():
    """An expectation key nobody taught the matcher must be loud, never a
    silently-skipped assertion that turns a live case green for free."""
    with pytest.raises(KeyError):
        assert_args_subset({"supplier": "X"}, GOOD_ARGS)


# --- stub executor --------------------------------------------------------


@pytest.mark.parametrize("case", OFFLINE_CASES, ids=[c["id"] for c in OFFLINE_CASES])
def test_stub_routing_case_end_to_end(case, settings):
    """The recorded router decision, replayed through the REAL registry and
    the REAL skill against the fake DataClient: the loop dispatches what the
    script asked for, with the doc's critical arguments, and the answer
    comes back as a renderable part plus both kinds of record.

    ``tools`` (Task 4, Phase 7) is a REAL ``ToolServerRegistry`` with a
    ``FixtureResearchTool`` override -- required for
    ``pivot_to_research_with_carry`` to dispatch against anything at all
    (``research.web_research``'s own ``ctx.tools is None`` path would
    otherwise render the honest "unavailable" degrade, never a table),
    and harmless for ``default_data_qa_breakdown``, which never touches
    ``ctx.tools``. Passed unconditionally rather than per-case: this test
    is parametrized generically over every offline case, and a working
    tools registry costs the metric_query case nothing.
    """
    tools = ToolServerRegistry(settings, overrides={"research": FixtureResearchTool()})
    result, sink, _ = _run(
        settings,
        _script_from(case),
        window=[{"role": "user", "content": case["user"]}],
        slots=_slots_for(case),
        tools=tools,
    )

    assert result.status == "ok", result.problem
    assert result.text == case["execution"]["stub_script"][-1]["text"]

    record = result.tool_records[0]
    assert record.skill_id == case["expect"]["skill"]
    assert_args_subset(case["expect"]["args_subset"], record.arguments)

    parts = [payload for kind, payload in sink.events if kind == "tool_done"][0]["parts"]
    assert any(part["kind"] in ("table", "metric_grid") for part in parts)

    assert record.status == "ok"
    assert record.result_digest
    assert all(llm_record.input_tokens > 0 for llm_record in result.llm_records)
    assert len(result.llm_records) == len(case["execution"]["stub_script"])


# --- live executor (skips without credentials) ----------------------------

_NO_CREDENTIALS_REASON = "no AWS credentials"
_HAS_AWS_CREDENTIALS = bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"))


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_AWS_CREDENTIALS, reason=_NO_CREDENTIALS_REASON)
@pytest.mark.parametrize("case", ROUTING_CASES, ids=[c["id"] for c in ROUTING_CASES])
def test_live_routing_case(case, monkeypatch):
    """Every case -- including the four whose expected skills are not
    registered yet -- against the real router role (doc 03 section 6 mode
    2). What is asserted is the ROUTING DECISION: the skill id and a subset
    of critical arguments read off the first ToolRecord, which records what
    the model ASKED for whether or not dispatch then answered with a 404.
    Data comes from the same offline fake as every stub run: a live model
    over fake rows is exactly the isolation this suite wants."""
    from poseidon.core.llm.bedrock import BedrockProvider

    live_settings = _settings(monkeypatch, LLM_MODE="live", LLM_PROFILE="bedrock")
    sink = RecordingSink()
    result = run_turn(
        role_client=RoleClient(live_settings, providers={"bedrock": BedrockProvider()}),
        registry=REGISTRY,
        context=_context(live_settings, _slots_for(case)),
        prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
        user_instruction="",
        memory_doc="",
        parsed=None,
        window=[{"role": "user", "content": case["user"]}],
        sink=sink,
        max_iterations=live_settings.agent_max_iterations,
    )

    expect = case["expect"]
    if expect.get("clarify"):
        assert result.tool_records == (), "an ambiguous turn must ask, not guess"
        assert result.text.strip()
        return

    assert result.tool_records, f"no skill was called for {case['id']}"
    record = result.tool_records[0]
    assert record.skill_id == expect["skill"]
    assert_args_subset(expect["args_subset"], record.arguments)


# ===========================================================================
# ASCII-only source, matching the Phase 4 parsing / Task 1-3 llm convention
# ===========================================================================


def test_llm_loop_module_files_are_ascii_on_disk():
    """Byte-pinned digests, problems and event payloads stay pinned only if
    no look-alike codepoint can slip into any of these files."""
    paths = (Path(loop.__file__), Path(titles.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
