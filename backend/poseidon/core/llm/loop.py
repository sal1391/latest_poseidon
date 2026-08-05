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

**Grounded tool results.** A tool result reaches the MODEL as its digest --
the part kinds, their row counts, and the certified proof block (see
:func:`tool_result_digest`) -- FOLLOWED BY the result's own values, capped
(see :func:`_result_content_text`). The parts also reach the USER through
the ``tool_done`` event, which carries them verbatim for Phase 6 to stream
as SSE; the two channels now show the same data, deliberately.

This reverses P5's original "context hygiene" rule, which sent the digest
ALONE on the grounds that "bulk data never has to re-enter the context
window: the frontend already rendered it." That premise was false, and the
2026-08-05 walkthrough is the evidence: the same model that is denied the
rows is asked, on the next call of this same loop, to write the answer's
prose. Shown only ``table(rows=5)`` it invented every number and borrowed
plausible customer names off the state block's carried-context line -- a
credible-looking answer about data nobody had queried. The offline suites
never caught it because ``DevDeterministicRouter``, the only router they
run, never narrates data at all (it closes on the state block alone), so
its digest was sufficient for a job it declined to do.

What is kept from the old rule is the BOUND: the values are capped at
:data:`RESULT_CONTENT_MAX_ROWS` rows and :data:`RESULT_CONTENT_MAX_CHARS`
characters per result, with an explicit truncation marker, so a
5,000-row breakdown costs a bounded number of tokens and the model is told
it is looking at a prefix rather than the whole answer. The run-log column
(``ToolRecord.result_digest``) stays the short digest -- see its own
docstring for why the two deliberately differ now.

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

An EMPTY reply is one of those structured failures, both ways round: a
``tool_use`` response carrying no tool calls, and an ``end_turn`` response
whose text is blank. The second matters because ``bedrock.py`` folds every
non-``tool_use`` StopReason into ``end_turn`` on purpose (a deliberately
lossy normalization -- ``content_filtered`` and ``guardrail_intervened``
arrive here as ``end_turn`` with no text), so accepting a blank reply as
"ok" would put an empty assistant bubble in the chat and leave Phase 6's
run log with a successful turn and nothing to explain it. Failing loudly is
what keeps that recoverable; surfacing the richer stop reason itself is a
later product decision, not this loop's.
"""

import json
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
_EMPTY_REPLY_DETAIL = "provider reported stop_reason 'end_turn' with an empty reply"

_EMPTY_PARTS = "(none)"

# The bound on the values a single tool result puts back into the context
# window (2026-08-05 live-synthesis fix -- see the module docstring's
# "Grounded tool results"). 50 rows is at or above every shape a certified
# skill can return today (``data_qa.metric_query``'s own ``top_n`` is
# validated at 1-50), so nothing a user actually asks for is truncated;
# the caps exist for the skill that one day returns a 5,000-row breakdown,
# and for the character blow-up a handful of very wide cells can cause well
# short of 50 rows.
RESULT_CONTENT_MAX_ROWS = 50
RESULT_CONTENT_MAX_CHARS = 4000

# Byte-pinned content literals (see the tests of the same names). The header
# states the two facts the model cannot work out for itself: these values
# are the real result, and the user has ALREADY seen them rendered -- the
# prompt's own grounding rules (router/system.md v2) are what act on that.
_CONTENT_HEADER = "data (already shown to the user as rendered parts; use ONLY these values):"
_CONTENT_NO_PARTS = "data: (no parts returned)"
_CONTENT_TRUNCATED = f"... result content truncated at {RESULT_CONTENT_MAX_CHARS} characters"
# What a cell holding SQL NULL renders as. Not ``str(None)``: "None" reads
# as a value in a list of values, and the difference between "no rows" and
# "zero" is exactly the distinction a narrative must not blur.
_NULL_CELL = "(null)"


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

    ``result_digest`` is the OPENING of what the model saw, not a second
    summarization: it is byte-identical to the first lines of the
    ``toolResult`` content (:func:`_result_content_text` builds that content
    by appending to this exact string), so a reviewer reading the run log is
    reading the model's own words for the shape and the certified proof of
    the result.

    It is deliberately no longer the WHOLE of what the model saw. Since the
    2026-08-05 live-synthesis fix the content also carries the result's
    capped values (see the module docstring), and this column keeps the
    short summary rather than growing to match: the rows a reviewer needs
    are already persisted as the turn's message parts, ``result_digest`` is
    written into a ``jsonb`` column on every single dispatch, and P11's own
    redaction rule (``runlog.py``'s I-2) nulls it alongside ``args`` on the
    understanding that it is a proof-and-shape summary rather than a copy of
    the data.
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
    """The SHAPE and PROOF of what a dispatch produced.

    This is the run-log's ``result_digest`` (doc 06) and the opening of the
    ``toolResult`` content the model receives -- :func:`_result_content_text`
    appends the result's own values below it. Three lines at most, and never
    a cell of data::

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
            if not response.text.strip():
                # The symmetric half of the empty-tool_use guard below; see
                # the module docstring's structured-failures paragraph for
                # why a blank reply is a failure and not a short answer.
                return _failed_turn(
                    sink,
                    problem(502, _PROVIDER_ERROR_TITLE, _EMPTY_REPLY_DETAIL),
                    tool_records,
                    llm_records,
                )
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
    # `dict(call.arguments)`, not the ToolCall's own dict: a sink is an
    # OBSERVER (Phase 6 streams these as SSE; a run-log tap or dev harness
    # may normalize what it receives), and an observer editing its payload
    # must not be able to change the dispatch that follows, the record that
    # gets logged, or the assistant echo the next request carries. Same
    # reason `ToolRecord.arguments` is copied below.
    sink.emit(
        "tool_start",
        {"tool_seq": tool_seq, "skill_id": call.name, "arguments": dict(call.arguments)},
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
            # Parts, proof and artifacts go to the UI (Phase 6/8 stream
            # them). Since the 2026-08-05 live-synthesis fix the model is
            # shown the same values too, capped, through
            # `_tool_result_block` -- see the module docstring's "Grounded
            # tool results" for the split this replaces and why.
            "parts": result.parts,
            "proof": result.proof,
            # Phase 8 Task 1: closes the P5 gap events.py's own docstring
            # ledgered since Phase 6 ("Artifacts: coded, currently
            # unreachable") -- `_dispatch_one` forwarded `parts`/`proof` but
            # never `result.artifacts`, so the sink's already-coded artifact
            # conversion had nothing real to convert. This one-liner is that
            # forward; see SkillResult.artifacts (core/skills/result.py) for
            # the list[ArtifactRef] shape and events.py's `_handle_tool_done`
            # for the conversion this makes reachable.
            "artifacts": result.artifacts,
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

    Success carries the digest plus the result's own capped values as text
    (:func:`_result_content_text`); failure carries the problem dict as
    JSON, unflattened -- a model correcting itself needs the fields
    (``status``, ``detail``), not a sentence about them, and a failure has
    no values to ground anything in. ``status`` is Converse's own
    ``ToolResultStatus`` enum ("success"/"error"), which is what tells the
    model a result is a failure without it having to infer that from the
    content.
    """
    if result.ok:
        return {
            "toolResult": {
                "toolUseId": call.id,
                "content": [{"text": _result_content_text(digest, result)}],
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


def _result_content_text(digest: str, result: SkillResult) -> str:
    """The digest, then the result's own values, capped.

    Deliberately NOT markdown and deliberately not pipe-delimited: the model
    is being told never to reproduce a table in its prose (router/system.md
    v2's third grounding rule), and handing it a ready-made grid to copy
    would work directly against that. ``name=value`` lines carry the same
    information in a shape that has to be rewritten to be repeated.

    A result with no parts at all says so in one explicit line rather than
    ending after the digest: "there is nothing here" is a fact the model
    must be able to state plainly, and an absent section reads the same as a
    section nobody rendered.
    """
    lines = [digest]
    if not result.parts:
        lines.append(_CONTENT_NO_PARTS)
    else:
        lines.append(_CONTENT_HEADER)
        for index, part in enumerate(result.parts, start=1):
            lines.extend(_part_content(index, part))
    return _capped(("\n".join(lines)).rstrip())


def _part_content(index: int, part: dict) -> list[str]:
    """One part's values, tagged ``[N]`` so a multi-part result stays
    legible about which lines belong to which part.

    A kind this module has never been taught about falls back to its payload
    as JSON rather than being dropped -- the same "the part vocabulary grows
    without this loop" contract :func:`_part_summary` follows, applied to
    values instead of shapes: an unknown part rendering verbosely is
    recoverable, an unknown part rendering as nothing is another silent void
    for a model to fill.
    """
    kind = part.get("kind")
    payload = part.get("payload") or {}
    tag = f"[{index}]"
    if kind == "table":
        return _table_content(tag, payload)
    if kind == "metric_grid":
        return _metric_grid_content(tag, payload)
    if kind == "text":
        return [f"{tag} text:", str(payload.get("markdown") or "")]
    if kind == "phase_section":
        return [
            f"{tag} phase_section: {payload.get('title')}",
            str(payload.get("markdown") or ""),
        ]
    return [f"{tag} {kind}: {json.dumps(payload, default=str, sort_keys=True)}"]


def _table_content(tag: str, payload: dict) -> list[str]:
    columns = [str(column) for column in (payload.get("columns") or [])]
    rows = payload.get("rows") or []
    lines = [f"{tag} table ({len(rows)} rows)"]
    for number, row in enumerate(rows[:RESULT_CONTENT_MAX_ROWS], start=1):
        cells = ", ".join(
            f"{_column_name(columns, position)}={_cell(value)}"
            for position, value in enumerate(row)
        )
        lines.append(f"{tag} row {number}: {cells}")
    lines.extend(_row_cap_marker(tag, len(rows), "rows"))
    return lines


def _metric_grid_content(tag: str, payload: dict) -> list[str]:
    periods = payload.get("periods") or {}
    metrics = payload.get("metrics") or []
    lines = [f"{tag} metric_grid ({len(metrics)} metrics)"]
    if periods:
        rendered = ", ".join(
            f"{label}={(window or {}).get('start')}..{(window or {}).get('end')}"
            for label, window in periods.items()
        )
        lines.append(f"{tag} periods: {rendered}")
    for number, metric in enumerate(metrics[:RESULT_CONTENT_MAX_ROWS], start=1):
        pairs = ", ".join(f"{key}={_cell(value)}" for key, value in metric.items())
        lines.append(f"{tag} metric {number}: {pairs}")
    lines.extend(_row_cap_marker(tag, len(metrics), "metrics"))
    return lines


def _column_name(columns: list[str], position: int) -> str:
    """A cell's own header, or a positional stand-in when a part carries
    more cells than columns -- a malformed part must still render its
    values rather than raise inside a turn."""
    return columns[position] if position < len(columns) else f"column{position + 1}"


def _cell(value: object) -> str:
    return _NULL_CELL if value is None else str(value)


def _row_cap_marker(tag: str, total: int, noun: str) -> list[str]:
    """The explicit "you are looking at a prefix" line. Without it a capped
    result is indistinguishable from a complete one, and a model that cannot
    tell the difference will describe the prefix as the whole answer."""
    if total <= RESULT_CONTENT_MAX_ROWS:
        return []
    return [f"{tag} ... {RESULT_CONTENT_MAX_ROWS} of {total} {noun} shown (truncated)"]


def _capped(content: str) -> str:
    """The character cap, applied at a LINE boundary.

    Cutting mid-line is the one truncation this must never do: half of
    ``Gross Profit=412000`` is ``Gross Profit=41``, a plausible number that
    was never in the data -- the exact failure mode this whole change
    exists to remove.
    """
    if len(content) <= RESULT_CONTENT_MAX_CHARS:
        return content
    head = content[:RESULT_CONTENT_MAX_CHARS]
    cut = head.rfind("\n")
    if cut != -1:
        head = head[:cut]
    return f"{head}\n{_CONTENT_TRUNCATED}"


def _assistant_tool_use_message(calls: tuple[ToolCall, ...]) -> dict:
    """The model's own turn, echoed back into the history.

    Required, not decorative: Converse pairs each ``toolResult`` with the
    ``toolUse`` it answers, and Anthropic models on Bedrock require
    alternating user/assistant roles -- a results message with no assistant
    turn before it is a rejected request, not a shorter one. Names stay
    dotted; ``bedrock.py`` translates them at its own boundary.

    ``input`` is a copy of the call's arguments for the same reason the
    event payload and the record are: this dict outlives the iteration that
    built it (it stays in the history for every later call of the turn), so
    it must not alias a dict anyone downstream still holds a reference to.
    """
    return {
        "role": "assistant",
        "content": [
            {"toolUse": {"toolUseId": call.id, "name": call.name, "input": dict(call.arguments)}}
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
    "RESULT_CONTENT_MAX_CHARS",
    "RESULT_CONTENT_MAX_ROWS",
    "ROUTER_GUARDRAIL_ENTITY",
    "ROUTER_ROLE",
    "ToolRecord",
    "TurnResult",
    "run_turn",
    "tool_result_digest",
]
