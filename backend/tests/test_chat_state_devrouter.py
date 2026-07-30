"""Tests for Phase 6 Task 2 (doc 03 section 3's conversation-state assembly,
items 4-5): the in-memory :class:`ConversationStateStore` and the
:class:`DevDeterministicRouter` that makes the offline dev chat
(``LLM_MODE=stub``) answer real, certified questions instead of a canned
script.

Every ``system`` string a router test builds goes through the REAL
``render_state_block``/``assemble_system`` pipeline (``poseidon.core.llm.
prompts``), never a hand-typed approximation of its output -- the same
discipline ``test_llm_prompts.py`` pins those two functions with. That is
what makes this suite fail loudly (an unexpected parse, not a silent
pass) if a future change to either function's line format ever drifts
from what ``dev_router.py``'s regexes target.

See ``dev_router.py``'s own module docstring for the full history of the
"hints gap" and its Task 3 closure: ``ParsedTurn.hints`` used to be
invisible to every router (dev and real alike) because ``render_state_
block`` never rendered it, so this router's tool-call gate was "a period is
resolved" alone -- a KNOWN, DISCLOSED OVER-TRIGGER for research-/brief-
shaped questions that also name a period (e.g. "market news ... in April
2026"). Task 3 closed it: ``render_state_block`` now renders an additive
"Skill hints: ..." line, and this router's gate upgrades to the plan's
literal condition, permissively -- see ``dev_router.py``'s "Task 3 CLOSURE"
for the exact rule (no hints line at all still permits dispatch; a hints
line present and leading with something other than ``data_qa.metric_query``
now refuses). ``test_hints_gate_permits_empty_or_metric_leading_but_
refuses_research_leading`` below (formerly ``test_tool_call_is_identical_
regardless_of_hints_value``, flipped per the Task 3 brief) pins the new
behavior.

Non-ASCII characters in expected strings are written as explicit
``\\uXXXX`` escapes (see ``_EM_DASH``), the same convention every earlier
Phase 4/5 suite uses: an em dash, an en dash and a hyphen are visually
indistinguishable in most editors, so a byte-pinned message that used a
typed character could silently pin the wrong codepoint.
``test_chat_module_files_are_ascii_on_disk`` enforces that for this file
and the three ``chat`` package modules it tests (fix round 1 added
``__init__.py`` to the scanned set -- clean today, and the guard should
keep it so).
"""

import threading
from datetime import date
from pathlib import Path

import pytest

from poseidon.core import chat
from poseidon.core.chat import dev_router, state
from poseidon.core.chat.dev_router import DevDeterministicRouter
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.config import Settings
from poseidon.core.data.specs import PeriodWindow
from poseidon.core.llm.prompts import (
    DEFAULT_PROMPTS_DIR,
    PromptRegistry,
    assemble_system,
    metric_definitions_block,
    negative_constraints_block,
    render_state_block,
    skill_lines_block,
)
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.types import LLMResponse, ToolCall
from poseidon.core.ontology.loader import get_ontology
from poseidon.core.parsing.lexicon import EXISTING_CUSTOMER_BRIEF, NEW_PROSPECT_BRIEF, WEB_RESEARCH
from poseidon.core.parsing.pipeline import DEFAULT_ENTITY
from poseidon.core.parsing.types import CandidateSkill, ParsedTurn, ResolvedEntity
from poseidon.core.skills.context import ConversationSlots
from poseidon.core.skills.registry import SkillRegistry
from poseidon.tasks.customer_insight.skills.existing_customer_brief.schema import (
    Args as ExistingBriefArgs,
)
from poseidon.tasks.customer_insight.skills.new_prospect_brief.schema import (
    Args as ProspectBriefArgs,
)
from poseidon.tasks.data_qa.skills.metric_query.schema import Args
from poseidon.tasks.research.skills.web_research.schema import Args as ResearchArgs

# U+2014 EM DASH, written as an escape for the reason given in the module
# docstring.
_EM_DASH = "\u2014"

_ENTITY_NAME = "MARINE_SALES_PLANNING_V"
_METRIC_QUERY = "data_qa.metric_query"
_RESEARCH = "research.web_research"
_EXISTING_BRIEF = "customer_insight.existing_customer_brief"
_NEW_PROSPECT_BRIEF = "customer_insight.new_prospect_brief"

_PERIOD_A = PeriodWindow(start=date(2026, 4, 1), end=date(2026, 5, 1))
_PERIOD_B = PeriodWindow(start=date(2025, 4, 1), end=date(2025, 5, 1))
_MAERSK = ResolvedEntity(value="MAERSK LINE", source_text="maersk", confidence=1.0, tier="exact")
_SINGAPORE = ResolvedEntity(
    value="SINGAPORE", source_text="singapore", confidence=0.93, tier="fuzzy"
)

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}


# ---------------------------------------------------------------------------
# ConversationStateStore -- in-memory conversation_id -> ConversationSlots,
# plus an independent per-conversation turn counter
# ---------------------------------------------------------------------------


def test_get_unknown_conversation_id_returns_empty_slots():
    store = ConversationStateStore()

    assert store.get("never-seen") == ConversationSlots()


def test_put_then_get_round_trips():
    store = ConversationStateStore()
    slots = ConversationSlots(customer="MAERSK LINE", port="SINGAPORE")

    store.put("conv-1", slots)

    assert store.get("conv-1") == slots


def test_put_replaces_wholesale_not_merged():
    store = ConversationStateStore()
    store.put("conv-1", ConversationSlots(customer="MAERSK LINE", port="SINGAPORE"))

    store.put("conv-1", ConversationSlots(port="ROTTERDAM"))

    # customer is gone, not carried over from the first put -- a wholesale
    # replace, never a merge.
    assert store.get("conv-1") == ConversationSlots(port="ROTTERDAM")


def test_get_and_put_are_isolated_per_conversation_id():
    store = ConversationStateStore()
    store.put("conv-1", ConversationSlots(customer="MAERSK LINE"))

    assert store.get("conv-2") == ConversationSlots()
    assert store.get("conv-1") == ConversationSlots(customer="MAERSK LINE")


def test_next_turn_index_starts_at_one_and_increments_monotonically():
    store = ConversationStateStore()

    assert store.next_turn_index("conv-1") == 1
    assert store.next_turn_index("conv-1") == 2
    assert store.next_turn_index("conv-1") == 3


def test_next_turn_index_independent_per_conversation_id():
    store = ConversationStateStore()

    assert store.next_turn_index("conv-1") == 1
    assert store.next_turn_index("conv-2") == 1
    assert store.next_turn_index("conv-1") == 2


def test_next_turn_index_independent_of_get_and_put():
    store = ConversationStateStore()
    store.next_turn_index("conv-1")
    store.next_turn_index("conv-1")

    store.put("conv-1", ConversationSlots(customer="MAERSK LINE"))
    store.get("conv-1")

    assert store.next_turn_index("conv-1") == 3


def test_next_turn_index_is_thread_safe_under_concurrent_calls():
    """20 threads x 50 calls each for the SAME conversation_id. A bare
    ``dict`` read-increment-write with no lock would lose updates under
    real interleaving (two threads reading the same current value before
    either writes back) and produce duplicate or skipped indices; the
    ``threading.Lock`` around the counter is what this test actually
    exercises -- every index from 1 to 1000 must appear EXACTLY once.
    """
    store = ConversationStateStore()
    conversation_id = "concurrent-conv"
    thread_count = 20
    calls_per_thread = 50
    results: list[int] = []
    results_lock = threading.Lock()

    def worker() -> None:
        local = [store.next_turn_index(conversation_id) for _ in range(calls_per_thread)]
        with results_lock:
            results.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == thread_count * calls_per_thread
    assert sorted(results) == list(range(1, thread_count * calls_per_thread + 1))


# ---------------------------------------------------------------------------
# ConversationStateStore.get_brief_done/set_brief_done (Phase 8 Task 5): an
# additive per-conversation flag alongside slots, NOT a ConversationSlots
# field -- see state.py's own module docstring for why (D19's "brief
# completed" marker rides the store itself, the same P6-store/P10-replaced
# surface next_turn_index already uses, rather than extending the parked
# ConversationSlots shape).
# ---------------------------------------------------------------------------


def test_get_brief_done_defaults_to_false_for_an_unseen_conversation_id():
    store = ConversationStateStore()

    assert store.get_brief_done("never-seen") is False


def test_set_brief_done_then_get_round_trips():
    store = ConversationStateStore()

    store.set_brief_done("conv-1", True)

    assert store.get_brief_done("conv-1") is True


def test_set_brief_done_is_isolated_per_conversation_id():
    store = ConversationStateStore()
    store.set_brief_done("conv-1", True)

    assert store.get_brief_done("conv-2") is False
    assert store.get_brief_done("conv-1") is True


def test_set_brief_done_can_be_reset_back_to_false():
    """A second flow-chip click starts a SECOND brief in the same
    conversation -- the entry branch resets this flag, proven here at the
    store level independent of the orchestrator."""
    store = ConversationStateStore()
    store.set_brief_done("conv-1", True)

    store.set_brief_done("conv-1", False)

    assert store.get_brief_done("conv-1") is False


def test_brief_done_is_independent_of_slots_and_turn_index():
    """The three dicts this store now holds (slots, turn_index, brief_done)
    are independently keyed -- writing one must not disturb the others."""
    store = ConversationStateStore()
    store.put("conv-1", ConversationSlots(customer="MAERSK LINE"))
    store.next_turn_index("conv-1")

    store.set_brief_done("conv-1", True)

    assert store.get("conv-1") == ConversationSlots(customer="MAERSK LINE")
    assert store.next_turn_index("conv-1") == 2
    assert store.get_brief_done("conv-1") is True


# ---------------------------------------------------------------------------
# DevDeterministicRouter -- fixtures and message-shape helpers
# ---------------------------------------------------------------------------


def _parsed(
    *,
    customer: ResolvedEntity | None = None,
    port: ResolvedEntity | None = None,
    period_a: PeriodWindow | None = None,
    period_b: PeriodWindow | None = None,
    hints: tuple[CandidateSkill, ...] = (),
) -> ParsedTurn:
    return ParsedTurn(
        normalized_text="test message",
        slots=ConversationSlots(),
        period_a=period_a,
        period_b=period_b,
        customer=customer,
        port=port,
        hints=hints,
        issues=(),
    )


def _system(parsed: ParsedTurn | None, slots: ConversationSlots | None = None) -> str:
    """The exact shape :class:`DevDeterministicRouter` parses:
    ``render_state_block``'s output threaded through ``assemble_system`` --
    never a hand-typed approximation (see the module docstring)."""
    if slots is None:
        slots = ConversationSlots()
    return assemble_system(
        base="BASE SYSTEM PROMPT",
        user_instruction="",
        memory_doc="",
        state_block=render_state_block(slots, parsed),
    )


def _user_message(text: str) -> dict:
    return {"role": "user", "content": [{"text": text}]}


def _assistant_text_message(text: str) -> dict:
    return {"role": "assistant", "content": [{"text": text}]}


def _tool_use_message(call_id: str, name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": [{"toolUse": {"toolUseId": call_id, "name": name, "input": args}}],
    }


def _tool_result_message(call_id: str, text: str, status: str = "success") -> dict:
    return {
        "role": "user",
        "content": [
            {"toolResult": {"toolUseId": call_id, "content": [{"text": text}], "status": status}}
        ],
    }


def _assert_valid_metric_query_args(call: ToolCall) -> None:
    """The brief's own bar: whatever the router emits has to validate
    against the REAL ``data_qa.metric_query`` schema -- correct field
    names, no invented ones -- not just look plausible."""
    assert call.name == _METRIC_QUERY
    Args.model_validate(call.arguments)


def _assert_valid_research_args(call: ToolCall) -> None:
    """Same bar as :func:`_assert_valid_metric_query_args`, for
    ``research.web_research``'s own real schema (Task 4)."""
    assert call.name == _RESEARCH
    ResearchArgs.model_validate(call.arguments)


def _research_tool_use_message(call_id: str, args: dict) -> dict:
    return _tool_use_message(call_id, _RESEARCH, args)


def _research_tool_result_message(
    call_id: str, results: int, *, transport: str = "fixture"
) -> dict:
    """A ``research.web_research`` toolResult, shaped like the REAL digest
    ``loop.py``'s own ``tool_result_digest`` builds from this skill's own
    ``tools/format_parts.py`` proof lines -- "Results: {n}" is what
    :func:`~poseidon.core.chat.dev_router._research_summary` (Task 4) reads
    the source count back out of, the same anchored-regex discipline every
    other line this router parses already uses."""
    digest = (
        f"{_RESEARCH} ok\n"
        f"parts: text(chars=42), table(rows={results})\n"
        f"proof: Query: q | Transport: {transport} | Results: {results}"
    )
    return _tool_result_message(call_id, digest)


# ---------------------------------------------------------------------------
# Case (d): nothing resolved -> the pinned capability message
# ---------------------------------------------------------------------------


def test_no_period_resolved_returns_pinned_capability_message():
    router = DevDeterministicRouter()
    system = _system(None)

    response = router.invoke(
        system=system, messages=[_user_message("hello")], tools=[], model="m", params={}
    )

    assert response == LLMResponse(
        text=(
            "I can answer certified metric questions "
            + _EM_DASH
            + " try a metric, a customer or port, and a period."
        ),
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )


def test_carried_customer_without_a_fresh_resolution_does_not_add_a_filter():
    """``slots.customer`` (a "Carried customer:" line) is not the same
    signal as ``parsed.customer`` (a "Resolved customer:" line) -- see the
    module docstring's "resolved, not carried" reading. A period alone
    still fires the tool call, but with no customer filter."""
    router = DevDeterministicRouter()
    slots = ConversationSlots(customer="MAERSK LINE")
    parsed = _parsed(period_a=_PERIOD_A)  # customer=None: nothing resolved THIS turn
    system = _system(parsed, slots)
    assert "Carried customer: MAERSK LINE" in system  # sanity: the line IS present
    assert "Resolved customer" not in system  # sanity: but not as a resolution

    response = router.invoke(
        system=system, messages=[_user_message("gp this period")], tools=[], model="m", params={}
    )

    assert response.tool_calls == (
        ToolCall(
            id="dev-1",
            name=_METRIC_QUERY,
            arguments={
                "entity": _ENTITY_NAME,
                "metrics": ["GP"],
                "period": {"start": "2026-04-01", "end": "2026-05-01"},
            },
        ),
    )


# ---------------------------------------------------------------------------
# Case (b): a resolved period -> ONE tool_use for data_qa.metric_query
# ---------------------------------------------------------------------------


def test_resolved_period_alone_emits_minimal_metric_query_tool_call():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A))

    response = router.invoke(
        system=system, messages=[_user_message("gp this period")], tools=[], model="m", params={}
    )

    expected_call = ToolCall(
        id="dev-1",
        name=_METRIC_QUERY,
        arguments={
            "entity": _ENTITY_NAME,
            "metrics": ["GP"],
            "period": {"start": "2026-04-01", "end": "2026-05-01"},
        },
    )
    assert response == LLMResponse(
        text="",
        tool_calls=(expected_call,),
        stop_reason="tool_use",
        input_tokens=0,
        output_tokens=0,
    )
    _assert_valid_metric_query_args(expected_call)


def test_top_in_last_user_message_adds_group_by_and_top_n():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A))

    response = router.invoke(
        system=system,
        messages=[_user_message("Top GP customers this period")],
        tools=[],
        model="m",
        params={},
    )

    call = response.tool_calls[0]
    assert call.arguments["group_by"] == "CUST_NM"
    assert call.arguments["top_n"] == 5
    _assert_valid_metric_query_args(call)


def test_top_as_a_substring_of_another_word_does_not_trigger_group_by():
    """Word-boundary match, not a bare substring search: "stopped" contains
    "top" but is not the standalone word "top" -- the same discipline
    ``skill_hinter.py``'s own lexicon matching uses."""
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A))

    response = router.invoke(
        system=system,
        messages=[_user_message("why has growth stopped this period")],
        tools=[],
        model="m",
        params={},
    )

    call = response.tool_calls[0]
    assert "group_by" not in call.arguments
    assert "top_n" not in call.arguments


def test_top_matches_case_insensitively():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A))

    response = router.invoke(
        system=system,
        messages=[_user_message("TOP customers please")],
        tools=[],
        model="m",
        params={},
    )

    assert response.tool_calls[0].arguments["group_by"] == "CUST_NM"


def test_last_user_message_decides_not_an_earlier_one():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A))
    messages = [
        _user_message("top gp for maersk"),
        _assistant_text_message(
            "Certified answer for MAERSK LINE " + _EM_DASH + " 2026-03-01..2026-04-01."
        ),
        _user_message("and the total for this period instead"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert "group_by" not in response.tool_calls[0].arguments


def test_resolved_customer_adds_a_customer_filter():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A, customer=_MAERSK))

    response = router.invoke(
        system=system, messages=[_user_message("gp for maersk")], tools=[], model="m", params={}
    )

    call = response.tool_calls[0]
    assert call.arguments["filters"] == [{"column": "CUST_NM", "values": ["MAERSK LINE"]}]
    _assert_valid_metric_query_args(call)


def test_resolved_port_adds_a_port_filter():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A, port=_SINGAPORE))

    response = router.invoke(
        system=system, messages=[_user_message("gp at singapore")], tools=[], model="m", params={}
    )

    call = response.tool_calls[0]
    assert call.arguments["filters"] == [{"column": "LOC_NM", "values": ["SINGAPORE"]}]
    _assert_valid_metric_query_args(call)


def test_resolved_customer_and_port_both_add_filters_customer_first():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A, customer=_MAERSK, port=_SINGAPORE))

    response = router.invoke(
        system=system,
        messages=[_user_message("gp for maersk at singapore")],
        tools=[],
        model="m",
        params={},
    )

    call = response.tool_calls[0]
    assert call.arguments["filters"] == [
        {"column": "CUST_NM", "values": ["MAERSK LINE"]},
        {"column": "LOC_NM", "values": ["SINGAPORE"]},
    ]
    _assert_valid_metric_query_args(call)


def test_compare_period_present_adds_compare_period_arg():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A, period_b=_PERIOD_B))

    response = router.invoke(
        system=system,
        messages=[_user_message("gp vs prior year")],
        tools=[],
        model="m",
        params={},
    )

    call = response.tool_calls[0]
    assert call.arguments["compare_period"] == {"start": "2025-04-01", "end": "2025-05-01"}
    assert "group_by" not in call.arguments
    _assert_valid_metric_query_args(call)


def test_compare_period_takes_priority_over_a_top_mention():
    """``Args`` itself rejects ``group_by`` combined with ``compare_period``
    (``_reject_breakdown_with_compare``); the router resolves the conflict
    by preferring the structurally-resolved compare period over the
    lexical "top" cue -- a disclosed judgment call the plan's behavior
    table does not itself address (see ``dev_router.py``'s module
    docstring)."""
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A, period_b=_PERIOD_B))

    response = router.invoke(
        system=system,
        messages=[_user_message("top gp vs prior year")],
        tools=[],
        model="m",
        params={},
    )

    call = response.tool_calls[0]
    assert call.arguments["compare_period"] == {"start": "2025-04-01", "end": "2025-05-01"}
    assert "group_by" not in call.arguments
    assert "top_n" not in call.arguments
    _assert_valid_metric_query_args(call)


def test_hints_gate_permits_empty_or_metric_leading_but_refuses_research_leading():
    """Post-Task-3 behavior (formerly ``test_tool_call_is_identical_
    regardless_of_hints_value``, flipped per the Task 3 brief instead of
    deleted -- see the module docstring and ``dev_router.py``'s "Task 3
    CLOSURE"): ``render_state_block`` now renders ``parsed.hints``, and this
    router's gate respects it, permissively.

    Three states, one gate: an EMPTY hints shortlist (no line at all) and a
    hints shortlist LEADING with ``data_qa.metric_query`` both still
    dispatch the tool call, byte-identically to each other and to every
    other case-(b) test in this file that never sets ``hints=`` (the
    permissive-when-absent half of the rule -- an advisory hinter with
    nothing to say is not a vote against dispatching). A hints shortlist
    LEADING with something else (``research.web_research``, as
    ``skill_hinter.hint`` actually returns for a research-shaped message
    per fix round 1's reproduced counterexamples) now refuses and falls
    back to the capability message instead -- this is the disclosed
    over-trigger, closed.
    """
    router = DevDeterministicRouter()
    messages = [_user_message("gp this period")]

    empty_hints_system = _system(_parsed(period_a=_PERIOD_A, hints=()))
    metric_leading_system = _system(
        _parsed(
            period_a=_PERIOD_A,
            hints=(CandidateSkill(skill_id=_METRIC_QUERY, score=3.0),),
        )
    )
    research_leading_system = _system(
        _parsed(
            period_a=_PERIOD_A,
            hints=(CandidateSkill(skill_id="research.web_research", score=3.0),),
        )
    )

    empty_response = router.invoke(
        system=empty_hints_system, messages=messages, tools=[], model="m", params={}
    )
    metric_response = router.invoke(
        system=metric_leading_system, messages=messages, tools=[], model="m", params={}
    )
    research_response = router.invoke(
        system=research_leading_system, messages=messages, tools=[], model="m", params={}
    )

    assert empty_response == metric_response
    assert empty_response.stop_reason == "tool_use"
    assert empty_response.tool_calls[0].name == _METRIC_QUERY

    assert research_response == LLMResponse(
        text=(
            "I can answer certified metric questions "
            + _EM_DASH
            + " try a metric, a customer or port, and a period."
        ),
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )


# ---------------------------------------------------------------------------
# Case (c): a toolResult already in `messages` -> end_turn certified answer
# ---------------------------------------------------------------------------


def test_tool_result_present_returns_certified_answer_with_customer_label():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A, customer=_MAERSK))
    messages = [
        _user_message("gp for maersk"),
        _tool_use_message("dev-1", _METRIC_QUERY, {"metrics": ["GP"]}),
        _tool_result_message("dev-1", "data_qa.metric_query ok\nparts: table(rows=1)"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response == LLMResponse(
        text="Certified answer for MAERSK LINE " + _EM_DASH + " 2026-04-01..2026-05-01.",
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )


def test_tool_result_present_with_port_only_uses_port_label():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A, port=_SINGAPORE))
    messages = [
        _user_message("gp at singapore"),
        _tool_use_message("dev-1", _METRIC_QUERY, {}),
        _tool_result_message("dev-1", "data_qa.metric_query ok"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.text == (
        "Certified answer for SINGAPORE " + _EM_DASH + " 2026-04-01..2026-05-01."
    )


def test_tool_result_present_with_customer_and_port_prefers_customer_label():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A, customer=_MAERSK, port=_SINGAPORE))
    messages = [
        _user_message("gp for maersk at singapore"),
        _tool_use_message("dev-1", _METRIC_QUERY, {}),
        _tool_result_message("dev-1", "data_qa.metric_query ok"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.text.startswith("Certified answer for MAERSK LINE ")


def test_tool_result_present_with_neither_customer_nor_port_uses_fallback_label():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A))
    messages = [
        _user_message("total gp this period"),
        _tool_use_message("dev-1", _METRIC_QUERY, {}),
        _tool_result_message("dev-1", "data_qa.metric_query ok"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.text == (
        "Certified answer for All Customers " + _EM_DASH + " 2026-04-01..2026-05-01."
    )


def test_tool_result_present_with_compare_period_includes_both_periods_in_label():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A, period_b=_PERIOD_B, customer=_MAERSK))
    messages = [
        _user_message("gp for maersk vs prior year"),
        _tool_use_message("dev-1", _METRIC_QUERY, {}),
        _tool_result_message("dev-1", "data_qa.metric_query ok"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.text == (
        "Certified answer for MAERSK LINE "
        + _EM_DASH
        + " 2026-04-01..2026-05-01 vs 2025-04-01..2025-05-01."
    )


def test_tool_result_with_error_status_still_returns_certified_answer():
    """This fake does not distinguish a successful dispatch from a failed
    one -- that distinction is a real model's job; see the module
    docstring's case (c)."""
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A))
    messages = [
        _user_message("gp this period"),
        _tool_use_message("dev-1", _METRIC_QUERY, {}),
        _tool_result_message("dev-1", '{"status": 500}', status="error"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.stop_reason == "end_turn"
    assert response.text.startswith("Certified answer for")


def test_tool_result_present_takes_priority_over_placing_a_new_tool_call():
    router = DevDeterministicRouter()
    system = _system(_parsed(period_a=_PERIOD_A))
    messages = [
        _user_message("top gp this period"),  # would trigger group_by on its own
        _tool_use_message("dev-1", _METRIC_QUERY, {}),
        _tool_result_message("dev-1", "data_qa.metric_query ok"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.stop_reason == "end_turn"
    assert response.tool_calls == ()


def test_tool_result_present_but_no_period_resolved_falls_back_to_capability_message():
    """Off-contract for a real caller (``run_turn``): case (b)'s own
    precondition means a period is always resolved by the time a
    ``toolResult`` could exist. ``invoke()`` is exercised directly here
    though, so this pins the documented, total-function guard rather than
    an unhandled exception on a shape nothing today actually sends."""
    router = DevDeterministicRouter()
    system = _system(None)
    messages = [
        _user_message("gp this period"),
        _tool_result_message("dev-1", "data_qa.metric_query ok"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.stop_reason == "end_turn"
    assert response.text.startswith("I can answer certified metric questions")


# ---------------------------------------------------------------------------
# Case (b2)/(c2), Task 4 CLOSURE: hints lead research.web_research AND a
# customer/port is known (resolved-or-carried) -> ONE tool_use; a second
# invoke closes with a research summary. See dev_router.py's own "Task 4
# CLOSURE" for the full rule.
# ---------------------------------------------------------------------------


def test_hints_leading_research_with_resolved_customer_emits_research_tool_call():
    router = DevDeterministicRouter()
    system = _system(
        _parsed(customer=_MAERSK, hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),))
    )
    question = "any relevant news on Maersk I should be aware of?"

    response = router.invoke(
        system=system, messages=[_user_message(question)], tools=[], model="m", params={}
    )

    assert response.stop_reason == "tool_use"
    call = response.tool_calls[0]
    assert call.id == "dev-1"
    assert call.arguments == {"question": question, "customer": "MAERSK LINE"}
    _assert_valid_research_args(call)


def test_hints_leading_research_with_resolved_port_emits_research_tool_call():
    router = DevDeterministicRouter()
    system = _system(
        _parsed(port=_SINGAPORE, hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),))
    )
    question = "any market news at Singapore?"

    response = router.invoke(
        system=system, messages=[_user_message(question)], tools=[], model="m", params={}
    )

    call = response.tool_calls[0]
    assert call.arguments == {"question": question, "port": "SINGAPORE"}
    _assert_valid_research_args(call)


def test_hints_leading_research_with_resolved_customer_and_port_attaches_both():
    router = DevDeterministicRouter()
    system = _system(
        _parsed(
            customer=_MAERSK,
            port=_SINGAPORE,
            hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),),
        )
    )

    response = router.invoke(
        system=system,
        messages=[_user_message("any news on Maersk at Singapore?")],
        tools=[],
        model="m",
        params={},
    )

    call = response.tool_calls[0]
    assert call.arguments["customer"] == "MAERSK LINE"
    assert call.arguments["port"] == "SINGAPORE"
    _assert_valid_research_args(call)


def test_hints_leading_research_with_carried_customer_only_emits_research_tool_call():
    """The pivot-with-carry scenario (``routing_cases.yml``'s own
    ``pivot_to_research_with_carry``): NOTHING resolved fresh this turn,
    but a customer is still carried from a prior one -- "resolved-or-
    carried" means the carry alone is enough to both PERMIT dispatch and
    SUPPLY the subject arg."""
    router = DevDeterministicRouter()
    slots = ConversationSlots(customer="CUSTOMER_X")
    parsed = _parsed(hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),))
    system = _system(parsed, slots)
    assert "Carried customer: CUSTOMER_X" in system  # sanity: the line IS present
    assert "Resolved customer" not in system  # sanity: but not as a fresh resolution
    question = "any relevant news on them I should be aware of?"

    response = router.invoke(
        system=system, messages=[_user_message(question)], tools=[], model="m", params={}
    )

    assert response.stop_reason == "tool_use"
    call = response.tool_calls[0]
    assert call.arguments == {"question": question, "customer": "CUSTOMER_X"}
    _assert_valid_research_args(call)


def test_hints_leading_research_with_carried_port_only_emits_research_tool_call():
    router = DevDeterministicRouter()
    slots = ConversationSlots(port="ROTTERDAM")
    parsed = _parsed(hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),))
    system = _system(parsed, slots)

    response = router.invoke(
        system=system,
        messages=[_user_message("any news I should know about?")],
        tools=[],
        model="m",
        params={},
    )

    call = response.tool_calls[0]
    assert call.arguments == {"question": "any news I should know about?", "port": "ROTTERDAM"}


def test_hints_leading_research_without_any_subject_falls_back_to_capability_message():
    """The AND half of the gate: hints leading research alone is not
    enough -- no customer or port known, resolved or carried, means there
    is nothing to research ABOUT, so this still falls through to the
    unchanged capability message (case (d))."""
    router = DevDeterministicRouter()
    system = _system(_parsed(hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),)))

    response = router.invoke(
        system=system,
        messages=[_user_message("any relevant news?")],
        tools=[],
        model="m",
        params={},
    )

    assert response.stop_reason == "end_turn"
    assert response.tool_calls == ()
    assert response.text.startswith("I can answer certified metric questions")


def test_hints_leading_research_without_a_brief_word_still_dispatches_research():
    """Sanity check for the Task 5 gate additions below: an ordinary
    research turn with no brief-family word anywhere is unaffected --
    :func:`dev_router._mentions_brief_word` only ever GATES the two new
    brief branches, it never blocks research's own pre-existing gate."""
    router = DevDeterministicRouter()
    system = _system(
        _parsed(customer=_MAERSK, hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),))
    )

    response = router.invoke(
        system=system,
        messages=[_user_message("any relevant news on Maersk?")],
        tools=[],
        model="m",
        params={},
    )

    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].name == _RESEARCH


# ---------------------------------------------------------------------------
# Case (b3)/(c3), Task 5 CLOSURE: hints lead a customer_insight brief skill
# (brief lexemes + mode hints -- lexicon.py's own "Brief words and mode
# hints" section) -> ONE tool_use for that brief. See dev_router.py's own
# "Task 5 CLOSURE" for the full rule, including why "leads" is redefined
# here (and for research's own gate, retroactively) as "ties for the best
# score", not "is textually first" -- a real, reproduced gap: the moment
# ConversationSlots.mode can actually BE "existing_customer"/"new_prospect"
# (this task is what first makes that true), an ORDINARY single-lexeme
# pivot question tallies 1.0 for its own skill and 1.0 for the carried
# mode's brief skill, and skill_hinter.hint's own alphabetical tie-break
# ("customer_insight..." < "data_qa..."/"research...") would otherwise
# always favor the brief -- silently breaking "route normally" for the doc
# 08 P8 self-review's own "post-brief pivots" promise. Reproduced directly:
# hint("any relevant news on Meridian Global Shipping?",
# ConversationSlots(mode="new_prospect")) == (CandidateSkill(
# 'customer_insight.new_prospect_brief', 1.0), CandidateSkill(
# 'research.web_research', 1.0)) -- research is NOT textually first.
# ---------------------------------------------------------------------------


def test_hints_leading_existing_brief_with_resolved_customer_and_brief_word_emits_tool_call():
    router = DevDeterministicRouter()
    system = _system(
        _parsed(
            customer=_MAERSK,
            hints=(
                CandidateSkill(skill_id=_EXISTING_BRIEF, score=2.0),
                CandidateSkill(skill_id=_NEW_PROSPECT_BRIEF, score=1.0),
            ),
        )
    )

    response = router.invoke(
        system=system,
        messages=[_user_message("Run the brief for Maersk")],
        tools=[],
        model="m",
        params={},
    )

    assert response.stop_reason == "tool_use"
    call = response.tool_calls[0]
    assert call.id == "dev-1"
    assert call.arguments == {"customer": "MAERSK LINE"}
    ExistingBriefArgs.model_validate(call.arguments)


def test_hints_leading_existing_brief_tied_via_mode_alone_still_dispatches_when_text_says_brief():
    """The POSITIVE half of the tie-inclusive widening: a genuine "brief"
    request whose hints line only TIES (not textually leads) still
    dispatches -- the brief-word guard is what makes this safe, not
    avoiding ties altogether."""
    router = DevDeterministicRouter()
    system = _system(
        _parsed(
            customer=_MAERSK,
            hints=(
                CandidateSkill(skill_id=_EXISTING_BRIEF, score=1.0),
                CandidateSkill(skill_id=_METRIC_QUERY, score=1.0),
            ),
        )
    )

    response = router.invoke(
        system=system,
        messages=[_user_message("give me a brief overview")],
        tools=[],
        model="m",
        params={},
    )

    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].name == _EXISTING_BRIEF


def test_hints_leading_existing_brief_no_resolved_customer_falls_back_to_capability_message():
    router = DevDeterministicRouter()
    system = _system(_parsed(hints=(CandidateSkill(skill_id=_EXISTING_BRIEF, score=2.0),)))

    response = router.invoke(
        system=system,
        messages=[_user_message("give me a brief")],
        tools=[],
        model="m",
        params={},
    )

    assert response.stop_reason == "end_turn"
    assert response.tool_calls == ()
    assert response.text.startswith("I can answer certified metric questions")


def test_hints_leading_existing_brief_with_carried_only_customer_does_not_dispatch():
    """Deliberately stricter than research's own resolved-or-carried rule
    (dev_router.py's own docstring, disclosed): the brief branch requires a
    customer resolved THIS turn, not merely carried from a prior one -- a
    full brief dispatch (external research, a stored PDF) is heavier-weight
    than a read-only metric/research call, so it does not fire on stale
    carried context alone."""
    router = DevDeterministicRouter()
    slots = ConversationSlots(customer="MAERSK LINE")
    system = _system(_parsed(hints=(CandidateSkill(skill_id=_EXISTING_BRIEF, score=2.0),)), slots)

    response = router.invoke(
        system=system,
        messages=[_user_message("give me a brief")],
        tools=[],
        model="m",
        params={},
    )

    assert response.tool_calls == ()
    assert response.text.startswith("I can answer certified metric questions")


def test_hints_leading_existing_brief_without_a_brief_word_in_text_does_not_dispatch():
    """The tie-break safety net itself, isolated: hints LEAD (not merely
    tie) the brief skill, a customer IS resolved, but the message never
    actually says brief/report/profile/overview -- this must not fire (see
    the section docstring above for the reproduced scenario this guards
    against)."""
    router = DevDeterministicRouter()
    system = _system(
        _parsed(customer=_MAERSK, hints=(CandidateSkill(skill_id=_EXISTING_BRIEF, score=1.0),))
    )

    response = router.invoke(
        system=system,
        messages=[_user_message("what is the volume for this customer")],
        tools=[],
        model="m",
        params={},
    )

    assert response.tool_calls == ()
    assert response.text.startswith("I can answer certified metric questions")


def test_hints_leading_new_prospect_brief_with_brief_word_emits_tool_call_with_free_text_name():
    """The prospect twin: no customer/port resolution exists for a
    prospect (D19's own "text = subject" rule, mirrored here) -- the whole
    last user message becomes ``prospect_name`` verbatim, the same crude
    "no clean extraction" precedent ``_research_call``'s own ``question``
    field already uses."""
    router = DevDeterministicRouter()
    system = _system(
        _parsed(
            hints=(
                CandidateSkill(skill_id=_NEW_PROSPECT_BRIEF, score=2.0),
                CandidateSkill(skill_id=_EXISTING_BRIEF, score=1.0),
            )
        )
    )
    question = "Start a new prospect brief for Meridian Global Shipping"

    response = router.invoke(
        system=system, messages=[_user_message(question)], tools=[], model="m", params={}
    )

    assert response.stop_reason == "tool_use"
    call = response.tool_calls[0]
    assert call.id == "dev-1"
    assert call.arguments == {"prospect_name": question}
    ProspectBriefArgs.model_validate(call.arguments)


def test_hints_leading_new_prospect_brief_tied_via_mode_alone_declines_without_brief_word():
    """The exact reproduced scenario from the section docstring: a research
    pivot after a prospect brief, mode carried, hints tie 1.0/1.0 -- no
    brief word in text, so the prospect twin correctly declines (falls
    through to the research check below it, case (b2))."""
    router = DevDeterministicRouter()
    system = _system(
        _parsed(
            customer=_MAERSK,
            hints=(
                CandidateSkill(skill_id=_NEW_PROSPECT_BRIEF, score=1.0),
                CandidateSkill(skill_id=_RESEARCH, score=1.0),
            ),
        )
    )

    response = router.invoke(
        system=system,
        messages=[_user_message("any relevant news on Maersk?")],
        tools=[],
        model="m",
        params={},
    )

    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].name == _RESEARCH


def test_hints_leading_new_prospect_brief_without_a_brief_word_falls_back_to_capability_message():
    """The same scenario with no customer/port at all (so research's own
    AND-gate also declines) -- proves the prospect twin's decline does not
    accidentally leave the turn stuck on a tool_use with nothing to call."""
    router = DevDeterministicRouter()
    system = _system(_parsed(hints=(CandidateSkill(skill_id=_NEW_PROSPECT_BRIEF, score=1.0),)))

    response = router.invoke(
        system=system,
        messages=[_user_message("any relevant news?")],
        tools=[],
        model="m",
        params={},
    )

    assert response.tool_calls == ()
    assert response.text.startswith("I can answer certified metric questions")


def test_existing_brief_tool_result_present_closes_with_a_brief_complete_summary():
    router = DevDeterministicRouter()
    system = _system(
        _parsed(customer=_MAERSK, hints=(CandidateSkill(skill_id=_EXISTING_BRIEF, score=2.0),))
    )
    messages = [
        _user_message("Run the brief for Maersk"),
        _tool_use_message("dev-1", _EXISTING_BRIEF, {"customer": "MAERSK LINE"}),
        _tool_result_message("dev-1", f"{_EXISTING_BRIEF} ok\nparts: metric_grid(metrics=6)"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response == LLMResponse(
        text="Brief complete for MAERSK LINE.",
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )


def test_existing_brief_tool_result_present_takes_priority_even_when_a_period_is_also_resolved():
    """Mirrors research's own identical precedence test: checked BEFORE
    case (c), so a period ALSO being resolved this turn never overrides
    the brief close with the metric-flavored "Certified answer" text."""
    router = DevDeterministicRouter()
    system = _system(
        _parsed(
            customer=_MAERSK,
            period_a=_PERIOD_A,
            hints=(CandidateSkill(skill_id=_EXISTING_BRIEF, score=2.0),),
        )
    )
    messages = [
        _user_message("Run the brief for Maersk this period"),
        _tool_use_message("dev-1", _EXISTING_BRIEF, {"customer": "MAERSK LINE"}),
        _tool_result_message("dev-1", f"{_EXISTING_BRIEF} ok"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.text == "Brief complete for MAERSK LINE."
    assert "Certified answer" not in response.text


def test_new_prospect_brief_tool_result_present_closes_with_the_echoed_prospect_name():
    """The prospect close reads its subject off the ECHOED toolUse input
    (there is no resolved-customer state block line for a prospect) --
    proven distinct from the existing-brief close, which reads
    ``parsed_state.customer`` instead."""
    router = DevDeterministicRouter()
    system = _system(_parsed(hints=(CandidateSkill(skill_id=_NEW_PROSPECT_BRIEF, score=2.0),)))
    question = "Start a new prospect brief for Meridian Global Shipping"
    messages = [
        _user_message(question),
        _tool_use_message("dev-1", _NEW_PROSPECT_BRIEF, {"prospect_name": question}),
        _tool_result_message("dev-1", f"{_NEW_PROSPECT_BRIEF} ok"),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.text == f"Brief complete for {question}."


def test_existing_brief_skill_id_matches_the_hinter_lexicons_own_constant():
    assert dev_router._EXISTING_CUSTOMER_BRIEF_SKILL_ID == EXISTING_CUSTOMER_BRIEF


def test_new_prospect_brief_skill_id_matches_the_hinter_lexicons_own_constant():
    assert dev_router._NEW_PROSPECT_BRIEF_SKILL_ID == NEW_PROSPECT_BRIEF


@pytest.mark.parametrize("word", ["brief", "report", "profile", "overview"])
def test_mentions_brief_word_matches_every_lexicon_brief_lexeme(word):
    """Pinned against lexicon.py's own four generic brief words (its
    "-- brief words" KEYWORDS section) -- dev_router.py does not import
    that table (see its own module docstring's opening contract), so this
    is the cross-check that keeps the two from silently drifting apart,
    the same discipline the skill-id equality tests above already use."""
    assert dev_router._mentions_brief_word([_user_message(f"give me a {word} please")]) is True


def test_mentions_brief_word_false_for_ordinary_text():
    assert dev_router._mentions_brief_word([_user_message("any relevant news?")]) is False


def test_mentions_brief_word_false_for_no_user_text():
    assert dev_router._mentions_brief_word([]) is False


def test_research_tool_result_present_closes_with_research_summary():
    router = DevDeterministicRouter()
    system = _system(
        _parsed(customer=_MAERSK, hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),))
    )
    question = "any relevant news on Maersk?"
    messages = [
        _user_message(question),
        _research_tool_use_message("dev-1", {"question": question, "customer": "MAERSK LINE"}),
        _research_tool_result_message("dev-1", 2),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response == LLMResponse(
        text="Research summary for MAERSK LINE " + _EM_DASH + " 2 sources.",
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )


def test_research_tool_result_with_port_only_uses_port_label():
    router = DevDeterministicRouter()
    system = _system(
        _parsed(port=_SINGAPORE, hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),))
    )
    messages = [
        _user_message("any news at Singapore?"),
        _research_tool_use_message(
            "dev-1", {"question": "any news at Singapore?", "port": "SINGAPORE"}
        ),
        _research_tool_result_message("dev-1", 3),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.text == "Research summary for SINGAPORE " + _EM_DASH + " 3 sources."


def test_research_tool_result_with_only_carried_customer_uses_the_carried_label():
    """The SAME ``system`` string (unchanged across a turn's iterations --
    ``loop.py``'s own "built ONCE per turn") means the close reads the
    identical carried customer the tool call itself used."""
    router = DevDeterministicRouter()
    slots = ConversationSlots(customer="CUSTOMER_X")
    system = _system(_parsed(hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),)), slots)
    question = "any relevant news on them I should be aware of?"
    messages = [
        _user_message(question),
        _research_tool_use_message("dev-1", {"question": question, "customer": "CUSTOMER_X"}),
        _research_tool_result_message("dev-1", 1),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.text == "Research summary for CUSTOMER_X " + _EM_DASH + " 1 sources."


def test_research_tool_result_with_zero_results_reports_zero_sources():
    router = DevDeterministicRouter()
    system = _system(
        _parsed(customer=_MAERSK, hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),))
    )
    messages = [
        _user_message("any news?"),
        _research_tool_use_message("dev-1", {"question": "any news?", "customer": "MAERSK LINE"}),
        _research_tool_result_message("dev-1", 0),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.text == "Research summary for MAERSK LINE " + _EM_DASH + " 0 sources."


def test_research_tool_result_present_takes_priority_even_when_a_period_is_also_resolved():
    """The collision case this task's own precedence rule exists for: a
    period is ALSO resolved this turn (so case (c)'s own condition --
    ``_has_tool_result(messages) and period is not None`` -- would ALSO be
    true), yet the toolUse that actually ran was research.web_research, not
    data_qa.metric_query. The close must name the research summary, never
    the metric-flavored "Certified answer for..." text -- proving
    ``_last_tool_use_name`` is checked, not merely "a toolResult exists
    somewhere"."""
    router = DevDeterministicRouter()
    system = _system(
        _parsed(
            customer=_MAERSK,
            period_a=_PERIOD_A,
            hints=(CandidateSkill(skill_id=_RESEARCH, score=1.0),),
        )
    )
    question = "any news on Maersk this period?"
    messages = [
        _user_message(question),
        _research_tool_use_message("dev-1", {"question": question, "customer": "MAERSK LINE"}),
        _research_tool_result_message("dev-1", 2),
    ]

    response = router.invoke(system=system, messages=messages, tools=[], model="m", params={})

    assert response.stop_reason == "end_turn"
    assert response.tool_calls == ()
    assert response.text.startswith("Research summary for")
    assert "Certified answer" not in response.text


# ---------------------------------------------------------------------------
# Cross-module consistency: this router's hardcoded entity must match the
# entity the deterministic parser actually parses against (fix round 1,
# minor fold-in 2)
# ---------------------------------------------------------------------------


def test_default_entity_matches_the_parser_default_entity():
    """Mirrors ``loop.py``'s own ``ROUTER_GUARDRAIL_ENTITY == DEFAULT_ENTITY``
    pin (``test_llm_loop.py``'s ``test_router_guardrail_entity_matches_the_
    parser_default_entity``): this router's hardcoded entity must be the
    same default sales entity the deterministic parser probes
    (``parsing.pipeline.DEFAULT_ENTITY``), or a tool call this router builds
    would name an entity the turn was never actually parsed against. Pinned
    as an equality against the module's own private constant, not an import
    into the router itself -- the same decoupling ``loop.py``'s own
    precedent uses."""
    assert dev_router._DEFAULT_ENTITY == DEFAULT_ENTITY


def test_research_skill_id_matches_the_hinter_lexicons_own_constant():
    """Same decoupling precedent as the entity pin above, for Task 4's own
    new constant: this router's ``_RESEARCH_SKILL_ID`` must name the exact
    same skill id ``lexicon.py``'s ``WEB_RESEARCH`` scores hints against,
    or a hints line that genuinely leads research would never be
    recognized as leading research by this router's own gate."""
    assert dev_router._RESEARCH_SKILL_ID == WEB_RESEARCH


# ---------------------------------------------------------------------------
# Integration: the REAL rendered router/system.md as the base section, and
# real interop through RoleClient's stub-provider seam
# ---------------------------------------------------------------------------

_REAL_ENTITY = get_ontology().entity(_ENTITY_NAME)
_REAL_REGISTRY = SkillRegistry.discover()
_REAL_BASE_PROMPT = PromptRegistry(DEFAULT_PROMPTS_DIR).render(
    "router/system",
    metric_definitions=metric_definitions_block(_REAL_ENTITY),
    negative_constraints=negative_constraints_block(_REAL_ENTITY),
    skill_lines=skill_lines_block(_REAL_REGISTRY),
)


def test_parses_correctly_inside_the_real_rendered_router_prompt():
    """Not a throwaway placeholder base -- the REAL ``router/system.md``,
    rendered with the REAL ontology and skill registry, ahead of the state
    block exactly as doc 03 section 3's actual assembly order produces.
    Proves no guardrail prose line collides with this router's anchored
    regexes."""
    router = DevDeterministicRouter()
    parsed = _parsed(period_a=_PERIOD_A, customer=_MAERSK)
    system = assemble_system(
        base=_REAL_BASE_PROMPT,
        user_instruction="",
        memory_doc="",
        state_block=render_state_block(ConversationSlots(), parsed),
    )

    response = router.invoke(
        system=system, messages=[_user_message("gp for maersk")], tools=[], model="m", params={}
    )

    call = response.tool_calls[0]
    assert call.arguments["filters"] == [{"column": "CUST_NM", "values": ["MAERSK LINE"]}]


def _settings(monkeypatch, **overrides) -> Settings:
    """A hermetic ``Settings`` -- mirrors ``test_llm_loop.py``'s own
    helper: every Settings-derived env var is cleared first, plus the two
    dynamic router-override names, so a real ``.env`` or ambient shell
    variable can never reach this test."""
    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LLM_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_ROUTER", raising=False)

    for key, value in {**REQUIRED_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_registers_as_the_role_clients_stub_provider(monkeypatch):
    """End-to-end proof of the brief's own framing -- "registered as the
    'stub' provider by the CHAT wiring (Task 4)" -- exercised now, through
    the REAL ``RoleClient`` seam (``roles.py``), not just a direct
    ``.invoke()`` call."""
    settings = _settings(monkeypatch)  # llm_mode defaults to "stub"
    role_client = RoleClient(settings, providers={"stub": DevDeterministicRouter()})
    system = _system(_parsed(period_a=_PERIOD_A))

    response = role_client.invoke(
        "router", system=system, messages=[_user_message("gp this period")], tools=[]
    )

    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].name == _METRIC_QUERY


# ---------------------------------------------------------------------------
# ASCII-only source, matching every earlier Phase 4/5/6 convention
# ---------------------------------------------------------------------------


def test_chat_module_files_are_ascii_on_disk():
    # fix round 1, minor fold-in 1: `chat.__file__` (the package's
    # `__init__.py`) added to the scanned set -- clean today, and this
    # guard should keep it so.
    paths = (
        Path(chat.__file__),
        Path(state.__file__),
        Path(dev_router.__file__),
        Path(__file__),
    )
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
