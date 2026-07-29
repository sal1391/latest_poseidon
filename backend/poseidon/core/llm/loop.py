"""The agent loop (doc 03 section 3): one user turn in, one
:class:`TurnResult` out.

:func:`run_turn` is the function Phase 6 calls for every live chat turn. It
assembles the router request once (Task 2's fixed order), presents the FULL
skill registry as tools, and then alternates provider call -> dispatch ->
digest until the model stops asking for tools, something fails twice, or the
iteration cap is reached.

Four properties this module is built to hold.

**Mode-blind.** Nothing here reads ``LLM_MODE`` or imports a provider.
``RoleClient`` decides which provider answers (``roles.py``'s seam); this
loop only ever says ``role_client.invoke("router", ...)``. That is what
lets the identical call path run against a scripted stub offline and
against Bedrock live -- pinned by the substitution proof in
``test_llm_loop.py``.

**Dotted ids everywhere.** Skill ids are dotted (``data_qa.metric_query``)
in tool schemas, in ``ToolCall.name``, in the assistant history this loop
appends, and in every record and event it emits. Bedrock's ``ToolName``
grammar excludes ``.``, but translating for it is ``bedrock.py``'s job at
its own request boundary; no wire name is ever constructed, stored, or
compared here.

**Context hygiene.** A tool result reaches the MODEL as a digest -- the
part kinds, their row counts, and the certified proof block (see
:func:`tool_result_digest`). The rows themselves reach the USER through the
``tool_done`` event, which carries the skill's parts verbatim for Phase 6
to stream as SSE. Bulk data therefore never re-enters the context window,
and it never has to: the frontend already rendered it.

**Structured failures.** ``SkillRegistry.dispatch`` never raises; its
RFC-7807 problem dict is what goes back to the model, once, as that tool's
result (the self-correction chance doc 03 section 3 item 4 describes). The
SAME skill failing a second time in the same turn ends the turn with that
problem. A provider-level failure (``stop_reason == "error"``) and the
iteration cap end the turn the same structured way -- ``run_turn`` returns
a failed :class:`TurnResult`, it does not raise. The one exception is a
MISCONFIGURATION (no provider registered for the mode's key), which
``RoleClient`` raises and this loop deliberately does not catch: that is a
deployment fault, not a conversation outcome.
"""

from dataclasses import dataclass
from time import monotonic
from typing import Protocol

from poseidon.core.llm.prompts import (
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
from poseidon.core.parsing.types import ParsedTurn
from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.core.skills.result import SkillResult, problem

# The role every call in this loop uses. Doc 03 section 2: "Tool selection
# and argument filling over TOOL_SCHEMAS; clarification decisions; final
# conversational replies" -- one role for the whole turn.
ROUTER_ROLE = "router"

# The certified entity whose metric definitions and negative constraints the
# router system prompt is guardrailed on. Must stay equal to
# ``parsing.pipeline.DEFAULT_ENTITY`` (the entity the turn was PARSED
# against) -- pinned as an equality test rather than an import so the
# parsing and llm packages stay decoupled.
ROUTER_GUARDRAIL_ENTITY = "MARINE_SALES_PLANNING_V"

# Byte-pinned failure titles/details (see the tests of the same names).
_CAP_TITLE = "agent loop exceeded max iterations"
_PROVIDER_ERROR_TITLE = "llm provider error"
_NO_TOOL_CALLS_DETAIL = "provider reported stop_reason 'tool_use' with no tool calls"

_EMPTY_PARTS = "(none)"


class EventSink(Protocol):
    """Where a turn's progress is announced as it happens.

    Phase 6 binds this to the SSE stream (doc 01 section 5); Phase 6's run
    log reads the RECORDS instead (returned on :class:`TurnResult`), because
    an event is a thing that happened live and a record is a thing that gets
    persisted. Kinds: ``llm_call``, ``tool_start``, ``tool_done``,
    ``turn_error``.
    """

    def emit(self, kind: str, payload: dict) -> None: ...


class RecordingSink:
    """An :class:`EventSink` that keeps everything it was handed.

    Shipped rather than re-rolled per test file: Phase 6's own tests and any
    dev harness need the same double, and two implementations of "remember
    the events" would drift on what a payload is allowed to be.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))

    @property
    def kinds(self) -> list[str]:
        """Just the kinds, in order -- what an event-sequence assertion
        reads, so the sequence is legible without the payloads."""
        return [kind for kind, _payload in self.events]


@dataclass(frozen=True)
class ToolRecord:
    """One dispatch, shaped for Phase 6's run-log writer (doc 06).

    ``result_digest`` is the same string the model saw, not a second
    summarization: what the run log shows a reviewer is exactly what the
    model was told. Bulk rows are deliberately absent -- they are the
    frontend's, through the ``tool_done`` event.
    """

    tool_seq: int
    skill_id: str
    arguments: dict
    status: str  # "ok" | "error"
    duration_ms: int
    result_digest: str


@dataclass(frozen=True)
class LLMRecord:
    """One provider call, shaped for Phase 6's run-log writer (doc 06).

    ``provider``/``model`` are the CONFIGURED pair (``RoleClient.resolve``),
    which is what config says this role uses -- truthful in either mode, and
    the only answer available to a loop that never learns which provider
    actually answered (that is the seam's whole point). Whether a stub stood
    in is the deployment's ``LLM_MODE``, recorded alongside by Phase 6.
    """

    call_seq: int
    role: str
    provider: str
    model: str
    stop_reason: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class TurnResult:
    """The whole turn: the reply, how it ended, and the two record streams.

    No ``parts`` field, deliberately: structured parts are streamed as they
    are produced (through ``tool_done``), not collected and returned at the
    end -- doc 03 section 3 item 3, "structured parts already streamed by
    skills are not re-generated by the model".
    """

    text: str
    status: str  # "ok" | "error"
    tool_records: tuple[ToolRecord, ...]
    llm_records: tuple[LLMRecord, ...]
    problem: dict | None  # RFC-7807 dict whenever status is "error"


def tool_result_digest(skill_id: str, result: SkillResult) -> str:
    """What the MODEL is told a dispatch produced.

    Three lines at most, and never a cell of data::

        data_qa.metric_query ok
        parts: table(rows=3)
        proof: Entity: ... | Backend: synthetic | ... | Rows: 3

    A failure is the same first line plus the problem's status/title/detail
    -- the run-log-facing summary of the SAME problem dict that goes back to
    the model as structured JSON (see :func:`_tool_result_block`), so the
    two can never describe different failures.

    Part summaries name the shape a kind is measured in: ``rows`` for a
    table, ``metrics`` for a metric grid, ``chars`` for prose. A kind this
    module has never been taught summarises to its bare name rather than
    raising -- doc 01 section 4's part vocabulary grows without this loop.
    """
    if not result.ok:
        detail = result.error or {}
        return (
            f"{skill_id} error\n"
            f"problem: {detail.get('status')} {detail.get('title')}: {detail.get('detail')}"
        )
    parts = ", ".join(_part_summary(part) for part in result.parts) or _EMPTY_PARTS
    lines = [f"{skill_id} ok", f"parts: {parts}"]
    if result.proof:
        lines.append("proof: " + " | ".join(result.proof))
    return "\n".join(lines)


def _part_summary(part: dict) -> str:
    kind = part.get("kind")
    payload = part.get("payload") or {}
    if kind == "table":
        return f"table(rows={len(payload.get('rows') or [])})"
    if kind == "metric_grid":
        return f"metric_grid(metrics={len(payload.get('metrics') or [])})"
    if kind == "text":
        return f"text(chars={len(payload.get('markdown') or '')})"
    return str(kind)


def run_turn(
    *,
    role_client: RoleClient,
    registry: SkillRegistry,
    context: SkillContext,
    prompt_registry: PromptRegistry,
    user_instruction: str,
    memory_doc: str,
    parsed: ParsedTurn | None,
    window: list[dict],
    sink: EventSink,
    max_iterations: int,
) -> TurnResult:
    """Run one turn to completion (doc 03 section 3's per-turn loop).

    ``window`` is the conversation window INCLUDING the current user
    message -- this function adds no turn of its own to the front, it only
    appends what the loop itself produces (the assistant's tool calls and
    their results). The caller's list is never mutated.

    ``parsed`` is the deterministic parse of the current turn (Phase 4), or
    ``None`` when there is nothing to report; ``context.state`` is the
    carried conversation state. Both go into the system prompt's state
    block, never into the message history -- doc 03's context-hygiene rule
    that "slot state travels in the system prompt, not as accumulated prose
    history".

    ``user_instruction``/``memory_doc`` are plain strings (Phases 9 and 13
    populate them); empty ones contribute no section at all.
    """
    config = role_client.resolve(ROUTER_ROLE)
    system = _router_system(
        prompt_registry=prompt_registry,
        registry=registry,
        context=context,
        parsed=parsed,
        user_instruction=user_instruction,
        memory_doc=memory_doc,
    )
    tools = registry.tool_schemas
    messages = list(window)
    llm_records: list[LLMRecord] = []
    tool_records: list[ToolRecord] = []
    # Which skills have already spent their one self-correction chance THIS
    # turn. Per skill id, not per turn: two different skills each failing
    # once is two independent corrections, not an escalation.
    spent_corrections: set[str] = set()

    for _iteration in range(max_iterations):
        call_seq = len(llm_records) + 1
        sink.emit(
            "llm_call",
            {
                "call_seq": call_seq,
                "role": ROUTER_ROLE,
                "provider": config.provider,
                "model": config.model,
            },
        )
        response = role_client.invoke(ROUTER_ROLE, system=system, messages=messages, tools=tools)
        llm_records.append(
            LLMRecord(
                call_seq=call_seq,
                role=ROUTER_ROLE,
                provider=config.provider,
                model=config.model,
                stop_reason=response.stop_reason,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
        )

        if response.stop_reason == "end_turn":
            return TurnResult(
                text=response.text,
                status="ok",
                tool_records=tuple(tool_records),
                llm_records=tuple(llm_records),
                problem=None,
            )
        if response.stop_reason != "tool_use":
            return _failed_turn(sink, _provider_problem(response), tool_records, llm_records)
        if not response.tool_calls:
            return _failed_turn(
                sink,
                problem(502, _PROVIDER_ERROR_TITLE, _NO_TOOL_CALLS_DETAIL),
                tool_records,
                llm_records,
            )

        result_blocks = []
        for call in response.tool_calls:
            record, block, failure = _dispatch_one(
                call=call,
                registry=registry,
                context=context,
                sink=sink,
                tool_seq=len(tool_records) + 1,
            )
            tool_records.append(record)
            if failure is not None and call.name in spent_corrections:
                # Doc 03 section 3 item 4: an error returns to the model
                # ONCE as structured content, then fails the turn. The
                # problem returned is THIS failure's, not the one already
                # answered -- it is the newer information.
                return _failed_turn(sink, failure, tool_records, llm_records)
            if failure is not None:
                spent_corrections.add(call.name)
            result_blocks.append(block)

        messages.append(_assistant_tool_use_message(response.tool_calls))
        messages.append({"role": "user", "content": result_blocks})

    return _failed_turn(
        sink,
        problem(500, _CAP_TITLE, f"cap {max_iterations}"),
        tool_records,
        llm_records,
    )


def _router_system(
    *,
    prompt_registry: PromptRegistry,
    registry: SkillRegistry,
    context: SkillContext,
    parsed: ParsedTurn | None,
    user_instruction: str,
    memory_doc: str,
) -> str:
    """Doc 03 section 3's fixed assembly order, built ONCE per turn.

    Once, not per iteration, because nothing it is made of can change
    mid-turn: the registry, the ontology guardrails and the conversation
    state are all fixed for the turn, and rebuilding an identical string per
    provider call would only cost prompt-cache hits.
    """
    entity = get_ontology().entity(ROUTER_GUARDRAIL_ENTITY)
    base = prompt_registry.render(
        "router/system",
        metric_definitions=metric_definitions_block(entity),
        negative_constraints=negative_constraints_block(entity),
        skill_lines=skill_lines_block(registry),
    )
    return assemble_system(
        base, user_instruction, memory_doc, render_state_block(context.state, parsed)
    )


def _dispatch_one(
    *,
    call: ToolCall,
    registry: SkillRegistry,
    context: SkillContext,
    sink: EventSink,
    tool_seq: int,
) -> tuple[ToolRecord, dict, dict | None]:
    """One tool call: announce it, dispatch it, announce the outcome.

    Returns the run-log record, the Converse ``toolResult`` block to append
    to the history, and the problem dict when the dispatch failed (``None``
    otherwise) -- so the caller can apply the one-correction-per-skill rule
    without re-reading the result.

    ``registry.dispatch`` never raises (its own contract), so there is no
    try/except here and no failure mode this function has to invent: a 404,
    a 422 and a 500 all arrive as a :class:`SkillResult` with ``ok=False``.

    ``monotonic`` -- not ``time()`` -- because this is a DURATION, and a
    wall clock that steps backwards mid-turn would produce a negative one.
    It is the only clock this module reads.
    """
    sink.emit(
        "tool_start",
        {"tool_seq": tool_seq, "skill_id": call.name, "arguments": call.arguments},
    )
    started = monotonic()
    result = registry.dispatch(call.name, dict(call.arguments), context)
    duration_ms = int((monotonic() - started) * 1000)

    digest = tool_result_digest(call.name, result)
    status = "ok" if result.ok else "error"
    sink.emit(
        "tool_done",
        {
            "tool_seq": tool_seq,
            "skill_id": call.name,
            "status": status,
            "duration_ms": duration_ms,
            "digest": digest,
            # Parts and proof go to the UI (Phase 6 streams them); the model
            # only ever sees `digest`. That split IS the context-hygiene
            # rule -- see the module docstring.
            "parts": result.parts,
            "proof": result.proof,
            "problem": result.error,
        },
    )
    record = ToolRecord(
        tool_seq=tool_seq,
        skill_id=call.name,
        arguments=dict(call.arguments),
        status=status,
        duration_ms=duration_ms,
        result_digest=digest,
    )
    return record, _tool_result_block(call, result, digest), result.error


def _tool_result_block(call: ToolCall, result: SkillResult, digest: str) -> dict:
    """The Converse ``toolResult`` block for one dispatch.

    Success carries the digest as text; failure carries the problem dict as
    JSON, unflattened -- a model correcting itself needs the fields
    (``status``, ``detail``), not a sentence about them. ``status`` is
    Converse's own ``ToolResultStatus`` enum ("success"/"error"), which is
    what tells the model a result is a failure without it having to infer
    that from the content.
    """
    if result.ok:
        return {
            "toolResult": {
                "toolUseId": call.id,
                "content": [{"text": digest}],
                "status": "success",
            }
        }
    return {
        "toolResult": {
            "toolUseId": call.id,
            "content": [{"json": result.error}],
            "status": "error",
        }
    }


def _assistant_tool_use_message(calls: tuple[ToolCall, ...]) -> dict:
    """The model's own turn, echoed back into the history.

    Required, not decorative: Converse pairs each ``toolResult`` with the
    ``toolUse`` it answers, and Anthropic models on Bedrock require
    alternating user/assistant roles -- a results message with no assistant
    turn before it is a rejected request, not a shorter one. Names stay
    dotted; ``bedrock.py`` translates them at its own boundary.
    """
    return {
        "role": "assistant",
        "content": [
            {"toolUse": {"toolUseId": call.id, "name": call.name, "input": call.arguments}}
            for call in calls
        ],
    }


def _provider_problem(response: LLMResponse) -> dict:
    """A stop reason that is neither ``end_turn`` nor ``tool_use``.

    ``error`` is the provider's own transport failure, already carrying a
    reason in ``text`` (``bedrock.py`` never raises -- it returns one of
    these), and returning that text as the assistant's REPLY would put
    "bedrock error: ThrottlingException" in a user's chat as though the
    model had said it. Anything else is a provider that broke
    ``types.py``'s three-value contract, and is named as such.
    """
    if response.stop_reason == "error":
        return problem(502, _PROVIDER_ERROR_TITLE, response.text)
    return problem(
        502,
        _PROVIDER_ERROR_TITLE,
        f"provider reported unsupported stop_reason {response.stop_reason!r}",
    )


def _failed_turn(
    sink: EventSink,
    problem_detail: dict,
    tool_records: list[ToolRecord],
    llm_records: list[LLMRecord],
) -> TurnResult:
    """Every failure exit goes through here, so ``turn_error`` can never be
    forgotten on one of them and the records collected so far are always
    returned rather than discarded -- a turn that failed on its fourth tool
    call still has three real dispatches worth logging."""
    sink.emit("turn_error", {"problem": problem_detail})
    return TurnResult(
        text="",
        status="error",
        tool_records=tuple(tool_records),
        llm_records=tuple(llm_records),
        problem=problem_detail,
    )


__all__ = [
    "EventSink",
    "LLMRecord",
    "RecordingSink",
    "ROUTER_GUARDRAIL_ENTITY",
    "ROUTER_ROLE",
    "ToolRecord",
    "TurnResult",
    "run_turn",
    "tool_result_digest",
]
