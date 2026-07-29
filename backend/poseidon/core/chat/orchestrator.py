"""``execute_turn`` (doc 03/doc 01 section 5, doc 06 section 1; Phase 6 Task
3): the one service Task 4's HTTP layer calls for every live chat turn. It
composes, in the pinned order below, every stack shipped so far -- P4's
:func:`~poseidon.core.parsing.pipeline.parse_turn`, P5's
:func:`~poseidon.core.llm.loop.run_turn`/:class:`~poseidon.core.llm.roles.
RoleClient`/:class:`~poseidon.core.llm.prompts.PromptRegistry`, P3's
:class:`~poseidon.core.skills.registry.SkillRegistry`, Task 1's
:class:`~poseidon.core.runlog.RunLogWriter`, Task 2's
:class:`~poseidon.core.chat.state.ConversationStateStore`/
:class:`~poseidon.core.chat.dev_router.DevDeterministicRouter` -- and streams
the result through Task 3's own :class:`~poseidon.core.chat.events.
SseEnvelopeSink`.

Pinned step order (the brief's own words, verbatim, each step below tagged
with a comment naming it)::

    parse_turn -> start_turn(parsed=dataclass-to-dict) -> sink.accepted(turn_index)
    -> clarify short-circuit OR run_turn
    -> parts emission (handled by the SINK itself, from tool_done payloads --
       see events.py)
    -> final text as ONE token event then done(usage)
    -> writer.append_* per records -> writer.finalize
    -> state.put(new slots incl. pass_through repopulation)

Three terminal states, three writer/state disciplines
----------------------------------------------------------
- **ok**: ``run_turn`` returns ``status="ok"``. Token + done stream; every
  ``LLMRecord``/``ToolRecord`` appended; ``finalize(status="ok")``;
  ``state.put`` with pass-through repopulated from any group-by dispatch
  this turn produced.
- **clarify**: ``parsed.issues`` contains a ``customer_ambiguous`` issue.
  Short-circuits BEFORE ``run_turn`` is ever called -- no LLM call, no
  dispatch, per the Global Constraints' "NO skill dispatch for that turn".
  Chips + text parts, then done; ``finalize(status="clarify")``; ``state.
  put`` with the slots ``parse_turn`` already resolved THIS turn (a period
  or port may have resolved even though the customer did not -- "slots
  STILL carry-updated").
- **error**: ``run_turn`` returns ``status="error"``. The ``error`` SSE
  frame already went out (synchronously, from inside ``run_turn``, via the
  sink's own ``EventSink.emit("turn_error", ...)`` translation) before this
  function ever sees the returned ``TurnResult`` -- no ``done`` follows an
  ``error`` (it is its own terminal signal, matching ``mock_chat.py``'s own
  error path). Every record ``run_turn`` collected before failing is still
  appended (doc 06: a turn that failed on its fourth tool call still has
  three real dispatches worth logging); ``finalize(status="error", error=
  problem)``; ``state.put`` is never called -- "slots unchanged" is
  implemented as "do not touch the store at all", not as writing back
  whatever the pre-turn value was.

Ids: who mints what
-----------------------
``turn_run_id`` is minted by :meth:`~poseidon.core.runlog.RunLogWriter.
start_turn` itself (``uuid.uuid4()``, T1's own documented v1 substitution
for UUIDv7). ``message_id`` is minted by WHOEVER constructs the
:class:`~poseidon.core.chat.events.SseEnvelopeSink` this function is
handed -- Task 4's HTTP layer in production (mirroring ``mock_chat.py``'s
own ``message_id = str(uuid.uuid4())``, generated before the stream starts),
a test's own fixed string offline. This function never mints an id of its
own: it reads ``sink.message_id`` back for every use it has of that value
(``TurnOutcome.message_id``, every writer call's ``message_id``). This
follows directly from the pinned ``execute_turn`` signature itself, which
takes an already-constructed ``sink`` and no separate ``message_id``
parameter -- there would be no other consistent source for the SAME id the
SSE envelope already carries.

Why the system prompt is rendered TWICE (once here, once inside run_turn)
-------------------------------------------------------------------------------
Doc 06's ``llm_calls.prompt_hash`` needs "a hash of the rendered prompt
actually sent" for every appended row. ``run_turn`` builds that exact text
internally (its own private ``_router_system``, called once per turn) but
never returns it -- ``TurnResult`` has no such field, and adding one is
outside this task's sanctioned edits to ``loop.py``. This function
therefore reproduces the identical computation using the SAME public
building blocks ``_router_system`` itself uses
(``metric_definitions_block``/``negative_constraints_block``/
``skill_lines_block``/``assemble_system``/``render_state_block``, the same
``ROUTER_GUARDRAIL_ENTITY``), fed the exact same ``prompt_registry``,
``registry``, ``context`` and ``parsed`` objects this function ALSO hands to
``run_turn`` -- so both renders are calls to pure functions over identical
inputs and therefore produce byte-identical text. This is deliberate
duplication, not an oversight: importing ``loop.py``'s private
``_router_system`` directly was considered and rejected, matching this
codebase's established convention of never reaching across a leading-
underscore boundary (``dev_router.py`` makes the identical choice for its
own entity constant, pinned by an equality test rather than an import).
Proven, not merely asserted: a recording stub provider in this task's own
test suite captures the REAL ``system`` text a call receives and hashes it
independently, confirming it matches what this module computed
(``test_flagship_prompt_hash_matches_the_real_system_text_the_provider_
saw``) -- if the two recipes ever drift, that test fails first.

Identity (doc 08's Phase 9 note)
------------------------------------
``DEV_USER_SUB`` is the plan's own fixed dev constant, "everywhere a
user_sub is required" until Phase 9 wires a real IdentityProvider. Every
writer call in this module uses it; nothing else in the pinned
``execute_turn`` signature carries an identity of its own to use instead.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date
from time import monotonic

from poseidon.core.chat.events import SseEnvelopeSink
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.config import Settings
from poseidon.core.data.client import DataClient
from poseidon.core.llm.loop import ROUTER_GUARDRAIL_ENTITY, run_turn
from poseidon.core.llm.prompts import (
    DEFAULT_PROMPTS_DIR,
    PromptRegistry,
    assemble_system,
    metric_definitions_block,
    negative_constraints_block,
    prompt_hash,
    prompt_version,
    render_state_block,
    skill_lines_block,
)
from poseidon.core.llm.roles import RoleClient
from poseidon.core.ontology.loader import get_ontology
from poseidon.core.parsing.pipeline import parse_turn
from poseidon.core.parsing.types import ParsedTurn
from poseidon.core.runlog import RunLogWriter
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillRegistry

# The plan's own fixed identity constant (Global Constraints: "the fixed dev
# user sub `dev|local` everywhere a user_sub is required") -- Phase 9
# replaces this with the real IdentityProvider seam.
DEV_USER_SUB = "dev|local"

# doc 06's `turn_run.answer_summary` / this turn's clarify text -- "capped".
_ANSWER_SUMMARY_CAP = 500

# doc 02 section 5's pass-through cap (Global Constraints: "capped at 10").
_PASS_THROUGH_CAP = 10

_CUSTOMER_AMBIGUOUS = "customer_ambiguous"
_ROUTER_SYSTEM_PROMPT_NAME = "router/system"

# Phase 6 does not populate either section yet (Self-Review Notes: "no auth
# (P9)... no memory distillation (P13)") -- both empty strings, contributing
# no section at all (assemble_system's own "empty is empty" rule).
_USER_INSTRUCTION = ""
_MEMORY_DOC = ""


@dataclass(frozen=True)
class TurnOutcome:
    """What :func:`execute_turn` hands back to the HTTP layer: enough to
    log the request and know whether to keep the connection open for more
    (it never does today -- one POST is one turn) or reconcile from the run
    log (doc 01 section 5's ``GET /api/turns/{turn_id}``, a later phase)."""

    status: str  # ok | clarify | error
    message_id: str
    turn_run_id: str | None


def execute_turn(
    *,
    conversation_id: str,
    text: str,
    client_turn_key: str | None,
    settings: Settings,
    registry: SkillRegistry,
    data: DataClient,
    state: ConversationStateStore,
    writer: RunLogWriter | None,
    role_client: RoleClient,
    prompt_registry: PromptRegistry,
    sink: SseEnvelopeSink,
    reference_date: date,
) -> TurnOutcome:
    """Run one chat turn to completion. See the module docstring for the
    full pinned order and the three terminal-state disciplines.

    ``writer`` may be ``None`` (no ``DATABASE_URL`` configured) -- every
    writer call below is guarded by ``writer is not None and turn_run_id is
    not None`` (the second half covers a writer that IS configured but whose
    own ``start_turn`` failed, per its never-raises contract -- see
    ``runlog.py``): the turn produces the identical stream and state-store
    behavior either way, only the run log gains no rows.
    """
    prior_slots = state.get(conversation_id)
    parsed = parse_turn(text, prior_slots, reference_date, data)
    turn_index = state.next_turn_index(conversation_id)

    handle = None
    if writer is not None:
        handle = writer.start_turn(
            user_sub=DEV_USER_SUB,
            conversation_id=conversation_id,
            client_turn_key=client_turn_key,
            turn_index=turn_index,
            question=text,
            mode=parsed.slots.mode,
            parsed=_parsed_to_loggable_dict(parsed),
            kind="chat_turn",
            trace_id=None,
        )
    turn_run_id = handle.turn_run_id if handle is not None else None

    sink.accepted(turn_index)
    started = monotonic()

    ambiguous_issue = next(
        (issue for issue in parsed.issues if issue.code == _CUSTOMER_AMBIGUOUS), None
    )
    if ambiguous_issue is not None:
        return _finish_clarify(
            ambiguous_issue=ambiguous_issue,
            parsed=parsed,
            sink=sink,
            writer=writer,
            turn_run_id=turn_run_id,
            state=state,
            conversation_id=conversation_id,
            started=started,
        )

    context = SkillContext(data=data, artifacts=None, settings=settings, state=parsed.slots)
    router_version, router_hash = _router_prompt_provenance(
        settings=settings,
        prompt_registry=prompt_registry,
        registry=registry,
        context=context,
        parsed=parsed,
    )

    window = [{"role": "user", "content": [{"text": text}]}]
    turn_result = run_turn(
        role_client=role_client,
        registry=registry,
        context=context,
        prompt_registry=prompt_registry,
        user_instruction=_USER_INSTRUCTION,
        memory_doc=_MEMORY_DOC,
        parsed=parsed,
        window=window,
        sink=sink,
        max_iterations=settings.agent_max_iterations,
    )

    usage = {
        "input_tokens": sum(record.input_tokens for record in turn_result.llm_records),
        "output_tokens": sum(record.output_tokens for record in turn_result.llm_records),
    }
    if turn_result.status == "ok":
        # Final text as ONE token event then done(usage) -- pinned order.
        # Never chunked: unlike the mock's illustrative multi-chunk stream,
        # every provider here (DevDeterministicRouter, a real LLMResponse)
        # already returns the complete text in one shot, so there is
        # nothing to chunk.
        sink.push_token(turn_result.text)
        sink.done(usage)
    # else: the `error` frame already went out from inside run_turn (see
    # the module docstring) -- no `done` follows it.

    latency_ms = int((monotonic() - started) * 1000)
    if writer is not None and turn_run_id is not None:
        _append_records(
            writer=writer,
            turn_run_id=turn_run_id,
            turn_result=turn_result,
            router_version=router_version,
            router_hash=router_hash,
        )
        writer.finalize(
            turn_run_id=turn_run_id,
            status=turn_result.status,
            message_id=sink.message_id,
            answer_summary=(_capped(turn_result.text) if turn_result.status == "ok" else None),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            latency_ms=latency_ms,
            error=(turn_result.problem if turn_result.status == "error" else None),
        )

    if turn_result.status == "ok":
        final_slots = _repopulate_pass_through(parsed.slots, turn_result.tool_records, sink)
        state.put(conversation_id, final_slots)
    # else (error): slots unchanged -- state.put is simply never called.

    return TurnOutcome(
        status=turn_result.status, message_id=sink.message_id, turn_run_id=turn_run_id
    )


def _finish_clarify(
    *,
    ambiguous_issue,
    parsed: ParsedTurn,
    sink: SseEnvelopeSink,
    writer: RunLogWriter | None,
    turn_run_id: str | None,
    state: ConversationStateStore,
    conversation_id: str,
    started: float,
) -> TurnOutcome:
    """The clarify short-circuit: chips + text parts, done, finalize,
    carry-updated state.put -- see the module docstring's "clarify"
    discipline. Only the FIRST ``customer_ambiguous`` issue is surfaced
    (pipeline.py collects issues customer-then-port-then-period, so a
    customer ambiguity is reported before a port one if, rarely, both
    landed in the candidate band the same turn) -- a bounded, disclosed
    scope: this function does not attempt to merge in an unrelated
    period/other issue also present the same turn.
    """
    sink.push_part(
        "chips",
        {
            "options": [
                {"id": candidate, "label": candidate} for candidate in ambiguous_issue.candidates
            ]
        },
    )
    sink.push_part("text", {"markdown": ambiguous_issue.message})
    sink.done({"input_tokens": 0, "output_tokens": 0})

    latency_ms = int((monotonic() - started) * 1000)
    if writer is not None and turn_run_id is not None:
        writer.finalize(
            turn_run_id=turn_run_id,
            status="clarify",
            message_id=sink.message_id,
            answer_summary=_capped(ambiguous_issue.message),
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            error=None,
        )

    state.put(conversation_id, parsed.slots)
    return TurnOutcome(status="clarify", message_id=sink.message_id, turn_run_id=turn_run_id)


def _router_prompt_provenance(
    *,
    settings: Settings,
    prompt_registry: PromptRegistry,
    registry: SkillRegistry,
    context: SkillContext,
    parsed: ParsedTurn,
) -> tuple[str, str]:
    """``(prompt_version, prompt_hash)`` for ``router/system`` -- see the
    module docstring's "Why the system prompt is rendered TWICE"."""
    entity = get_ontology().entity(ROUTER_GUARDRAIL_ENTITY)
    base = prompt_registry.render(
        _ROUTER_SYSTEM_PROMPT_NAME,
        metric_definitions=metric_definitions_block(entity),
        negative_constraints=negative_constraints_block(entity),
        skill_lines=skill_lines_block(registry),
    )
    system_text = assemble_system(
        base, _USER_INSTRUCTION, _MEMORY_DOC, render_state_block(context.state, parsed)
    )
    prompts_dir = settings.prompts_dir if settings.prompts_dir is not None else DEFAULT_PROMPTS_DIR
    version = prompt_version(prompts_dir, _ROUTER_SYSTEM_PROMPT_NAME)
    return version, prompt_hash(system_text)


def _append_records(
    *, writer: RunLogWriter, turn_run_id: str, turn_result, router_version: str, router_hash: str
) -> None:
    """Every ``LLMRecord``/``ToolRecord`` the turn collected, regardless of
    whether it ultimately succeeded -- doc 06: a turn that failed on its
    Nth call still has N-1 real dispatches worth logging."""
    for record in turn_result.llm_records:
        writer.append_llm_call(
            turn_run_id=turn_run_id,
            user_sub=DEV_USER_SUB,
            seq=record.call_seq,
            provider=record.provider,
            model_id=record.model,
            role=record.role,
            prompt_version=router_version,
            prompt_hash=router_hash,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            # LLMRecord carries no per-call timing (unlike ToolRecord's
            # duration_ms) -- None is an honest "not measured", never a
            # fabricated value.
            latency_ms=None,
            status=("error" if record.stop_reason == "error" else "ok"),
        )
    for record in turn_result.tool_records:
        writer.append_tool_call(
            turn_run_id=turn_run_id,
            user_sub=DEV_USER_SUB,
            seq=record.tool_seq,
            tool=record.skill_id,
            server=None,
            args=record.arguments,
            result_digest={"digest": record.result_digest},
            status=record.status,
            latency_ms=record.duration_ms,
            # ToolRecord carries no structured problem dict of its own --
            # result_digest's own string already carries the run-log-facing
            # failure summary (see loop.py's tool_result_digest docstring:
            # "the two can never describe different failures").
            error=None,
        )


def _repopulate_pass_through(
    slots: ConversationSlots, tool_records, sink: SseEnvelopeSink
) -> ConversationSlots:
    """Doc 02 section 5's pass-through wiring: the LAST group-by dispatch
    this turn's table part becomes ``((value, value), ...)``, capped at 10,
    wholesale-replacing whatever ``slots.pass_through`` already carried.

    Gated on the DISPATCHED ``group_by`` argument (present on
    ``ToolRecord.arguments``, copied verbatim from what the router asked
    for), not on any part-kind sniffing: ``format_parts.py``'s own shape
    rules mean a table part's first column holds a certified DIMENSION
    value if and only if the call that produced it requested a breakdown --
    the OTHER table shape (a plain metric/value pair list) has no
    ``group_by`` and its first column holds METRIC names, not dimension
    values, which would be meaningless to carry forward as a filterable
    "exact value". Reads ``sink.tool_result_parts`` (keyed by ``tool_seq``)
    rather than ``TurnResult`` because the raw rows never reach ``TurnResult``
    at all -- see ``events.py``'s own docstring.

    Deliberately NOT routed through ``SlotUpdates``/``apply_carry``: doc 02
    section 5's `pass_through` field is not one of ``SlotUpdates``'s four
    known fields, and this task's sanctioned edits explicitly leave
    ``carry.py`` untouched ("If ConversationSlots/SlotUpdates need NOTHING
    -- good; you consume them as-is"). A direct ``dataclasses.replace`` on
    the already-carried ``slots`` achieves the identical "replace wholesale,
    never merge" semantics ``ConversationSlots.pass_through``'s own
    docstring requires, without extending a dataclass the brief marks
    off-limits.
    """
    new_pairs: tuple[tuple[str, str], ...] | None = None
    for record in tool_records:
        if not record.arguments.get("group_by"):
            continue
        parts = sink.tool_result_parts.get(record.tool_seq, [])
        table = next((part for part in parts if part.get("kind") == "table"), None)
        if table is None:
            continue
        rows = table["payload"].get("rows") or []
        new_pairs = tuple((row[0], row[0]) for row in rows[:_PASS_THROUGH_CAP])
    if new_pairs is None:
        return slots
    return dataclasses.replace(slots, pass_through=new_pairs)


def _capped(text: str) -> str:
    return text[:_ANSWER_SUMMARY_CAP]


def _parsed_to_loggable_dict(parsed: ParsedTurn) -> dict:
    """``ParsedTurn`` as a plain, JSON-safe dict for doc 06's
    ``turn_run.parsed`` column.

    ``dataclasses.asdict`` alone is not enough: it recurses through every
    nested dataclass correctly, but leaves ``datetime.date`` values (
    ``ConversationSlots.period_a``/``period_b``, every ``PeriodWindow``'s
    ``start``/``end``) as raw ``date`` objects, which ``json.dumps`` cannot
    serialize. This matters more than a cosmetic type mismatch: ``RunLog
    Writer.start_turn`` calls ``json.dumps(parsed)`` with no ``default=``
    (see ``runlog.py``), wrapped in ITS OWN never-raises ``try/except`` --
    so an unserializable value here would not crash this function, it would
    silently make EVERY ``start_turn`` call fail (logged at ERROR, returning
    ``None``), defeating Task 1's entire run-log writer without a single
    test-visible exception anywhere in THIS module. ``_json_safe`` walks the
    ``asdict`` output and converts every ``date`` to its ISO string; every
    other value already round-trips through ``json.dumps`` unchanged
    (tuples included -- ``json`` encodes a tuple exactly like a list).
    """
    return _json_safe(dataclasses.asdict(parsed))


def _json_safe(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


__all__ = ["DEV_USER_SUB", "TurnOutcome", "execute_turn"]
