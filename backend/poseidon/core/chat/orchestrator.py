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
  this turn produced -- guarded (final-review wave item 5): a
  ``_repopulate_pass_through`` failure logs at ERROR and skips ``state.put``
  for this turn only, rather than raising back through a turn whose
  ``done``/``finalize`` already went out (see that function's own
  docstring).
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

A fourth, EARLIER short-circuit (Phase 6 Task 4 amendment, Phase 11 Task 3
upgrade): retry / true replay
------------------------------------------------------------------------
Before any of the three terminal states above is even reached: when
``writer.start_turn`` reports ``created=False`` -- ``(user_sub,
client_turn_key)`` already named a row some EARLIER request created -- this
function asks the writer (:meth:`~poseidon.core.runlog.RunLogWriter.
read_for_replay`) what that ORIGINAL turn's terminal status and persisted
answer were (Phase 11 Task 3, doc 01 section 5). An ``"ok"`` status with a
real, persisted message REPLAYS that answer -- its parts pushed verbatim,
as one part-frame sequence, via ``sink.push_part`` (never ``part_emitter``,
so the P8 retired-dispatch guard is never in the call path at all -- see
:func:`_begin_turn`'s own inline comment at this branch), then ``sink.
done`` -- and returns a ``TurnOutcome`` with ``replayed=True``. Anything
else (``"running"``, ``"error"``, no writer to ask, or a replay lookup that
itself failed -- :meth:`~poseidon.core.runlog.RunLogWriter.read_for_replay`
never raises, matching every other ``RunLogWriter`` method's own
never-raises rule) falls through to the ORIGINAL, byte-pinned behavior:
this function emits ONE pinned ``error`` frame (``code="duplicate_turn"``)
and returns immediately. Either branch: no ``run_turn`` call, no dispatch,
no ``writer.append_*``, no ``writer.finalize`` (the ORIGINAL turn owns the
row; writing to it again from here would race that request), and no
``state.put``. ``next_turn_index`` was already consumed before this check
runs (a known, accepted residual: a dense-numbering gap on retry, harmless --
``turn_index`` is advisory ordering, never a primary key).

D19 entry orchestration (Phase 8 Task 5): two MORE short-circuits, both
BEFORE parse_turn's normal path
-------------------------------------------------------------------------
The bubble-entry flow (doc 02 section 4's "Entry orchestration rule") adds
two branches this function checks BEFORE ``parse_turn`` is ever called --
literally before, not merely before ``run_turn``: neither branch has any
customer/port/period cue for ``parse_turn`` to find, and D19's own contract
("no dispatch, no router") means neither ever needs the carry/hint
machinery ``parse_turn`` exists to run.

**The entry branch** (:func:`_finish_entry`). ``text`` is compared,
casefolded, against the two pinned flow-chip phrases (``"start an
existing-customer brief"`` / ``"start a new-prospect brief"`` -- the SAME
strings ``api/live_chat.py``'s opener chips now carry as ``send_text``).
An EXACT match sets ``slots.mode`` via ``dataclasses.replace(prior_slots,
mode=...)`` -- the P6 pass_through precedent
(:func:`_repopulate_pass_through`'s own identical "replace wholesale,
never merge" pattern, applied here to one field instead of one whole
object) -- pushes ONE text part asking for the subject, and finalizes
``clarify``. No skill dispatch, no ``run_turn`` call, matching the
Global Constraints' D19 rule verbatim (mirrors the ambiguous-customer
clarify short-circuit's own "chips/text, done, finalize, clarify" shape,
minus the chips -- there is nothing to pick from yet, only a question to
answer). :meth:`~poseidon.core.chat.state.ConversationStateStore.
set_brief_done` is reset to ``False`` here too: a SECOND flow-chip click
mid-conversation starts a SECOND brief, never blocked by the first one's
completion.

**The subject branch** (:func:`_finish_subject_turn`). Reached when
``prior_slots.mode`` is one of the two D19 modes AND :meth:`~poseidon.core.
chat.state.ConversationStateStore.get_brief_done` is still ``False`` for
this conversation -- i.e. exactly the turn immediately following a
successful entry branch, or a retry after that turn failed to resolve a
subject. ``mode == "existing_customer"`` resolves the ENTIRE message
directly through :func:`~poseidon.core.parsing.customer_resolver.resolve`
(never ``parse_turn`` -- there is no cue word to gate on; the whole
message IS the candidate phrase, "the customer resolver (full ambiguity
contract)" the Global Constraints name): a confident match dispatches: a
candidate-band or unknown result surfaces the SAME shape the ambiguous
short-circuit does (chips -- bare ``send_text``, no "for " cue, since this
branch never goes back through ``parse_turn``'s cue-gated detector -- or
just text for an unknown match), ``clarify``, ``brief_done`` left
``False`` so the very next message retries. ``mode == "new_prospect"``
skips resolution entirely -- ``text.strip()`` IS the subject (D19's own
"text = subject" rule: a prospect is by definition not a certified
dimension value, so there is nothing to resolve against).

Once a subject is in hand, dispatch is DETERMINISTIC: ``registry.
dispatch`` is called directly, never ``run_turn`` -- no router, live or
stub, ever sees this turn. The tool events (``tool_start``/``tool_done``,
carrying ``parts``/``proof``/``artifacts`` exactly like a routed dispatch)
and the run-log rows (one ``tool_calls`` row; ZERO ``llm_calls`` rows in
stub mode, since ``run_turn``'s own two-call loop never runs -- the one
concrete, testable difference from a normal routed dispatch, which always
logs at least two) are built by hand here, reusing ``loop.py``'s PUBLIC
:func:`~poseidon.core.llm.loop.tool_result_digest` and its public
:class:`~poseidon.core.llm.loop.ToolRecord`/:class:`~poseidon.core.llm.
loop.TurnResult` dataclasses (never its private ``_dispatch_one`` --
leading-underscore boundary, the same convention ``dev_router.py`` and
this module's own "Why the system prompt is rendered TWICE" section
already establish) so that :func:`_append_records`/
:func:`_repopulate_pass_through` below can be reused UNCHANGED for this
path too, rather than each growing a parallel implementation. A
successful dispatch (``result.ok``) streams progressively exactly like a
brief reached through the ROUTED path (``dev_router.py``'s own brief
branch, distinct code, same two skills) -- ``ctx.emit_part`` wired to
``sink.part_emitter(1)`` the identical way the normal path wires it below
-- then marks :meth:`~poseidon.core.chat.state.ConversationStateStore.
set_brief_done` ``True`` and ends with ``sink.done`` -- no
``sink.push_token``: there is no router call to have composed a reply,
and the brief's own streamed parts already ARE the answer. A FAILED
dispatch (``not result.ok`` -- a structural 422, or a genuine skill bug)
ends the turn the same way any other failure does: one ``turn_error``
frame carrying ``result.error`` verbatim, ``finalize(status="error", ...)``,
``brief_done`` left ``False`` so a corrected subject can retry.

After a successful dispatch, ``mode`` is NOT reset -- it rides in
``slots`` (via the unchanged ``state.put``/pass-through path below)
exactly as the entry branch left it, ADVISORY context for
:func:`~poseidon.core.parsing.skill_hinter.hint` from then on (see
``dev_router.py``'s own "Task 5 CLOSURE" for how that advisory weight is
kept from silently hijacking an ordinary post-brief pivot). ``get_brief_
done`` being ``True`` is what actually gates this branch shut: every turn
after a successful dispatch falls straight through the two D19 checks and
reaches ``parse_turn``'s normal path exactly as before this task existed.

DISTINCT FROM ``dev_router.py``'s OWN brief branch (Task 5, the ROUTED
path -- "Run the brief for Maersk" typed directly, any mode, no bubble
click at all). That path goes through the ordinary ``run_turn`` loop, a
real (fake, in stub mode) router decision, and lands on the identical two
skills through ``SkillRegistry.dispatch`` the SAME way every other routed
call does -- llm_calls rows included. D19 never risks a router's
judgment call for its own bubble-entry flow; the routed path is what lets
an ORDINARY chat message ALSO reach a brief, the same way any other skill
already is. See ``dev_router.py``'s own module docstring, "Task 5
CLOSURE", for the full contrast from that side.

Shared turn bookkeeping (:func:`_begin_turn`). All three branches --
normal, entry, subject -- open with the identical sequence: mint
``turn_index``, open the run-log row (``mode``/``parsed`` differ per
branch, computed by the caller), emit ``accepted``, and apply the retry
short-circuit above. Factored into one helper once a THIRD call site
needed it (this task) -- the same "duplication tolerated until a second,
then extracted" judgment call ``result.py``'s own ``phase_section_part``
promotion already used one call site earlier -- rather than three
independently near-identical copies drifting apart. Behavior-preserving
for the pre-existing normal path: identical kwargs, identical order,
verified against this module's own pre-Task-5 test suite (zero diffs).

Ids: who mints what
-----------------------
``turn_run_id`` IS :attr:`~poseidon.core.chat.events.SseEnvelopeSink.
turn_id` (Phase 6 Task 4 amendment, closing the seam doc 06 section 1's own
comment names): this function passes ``sink.turn_id`` to :meth:`~poseidon.
core.runlog.RunLogWriter.start_turn` as its new ``turn_run_id`` keyword, so
the row ``start_turn`` creates has that id verbatim, rather than a second,
independently-minted uuid4 of its own -- Phase 11's reconciliation endpoint
looks a turn up by the SAME id every frame of it already carries on the
wire. (Before this amendment, ``start_turn`` minted its own id independently
of ``sink.turn_id``, and Task 4's HTTP layer never had a reason to make the
two agree -- this is the amendment that makes them the same value BY
construction, not by convention.) ``message_id`` is minted by WHOEVER
constructs the :class:`~poseidon.core.chat.events.SseEnvelopeSink` this
function is handed -- Task 4's HTTP layer in production (mirroring
``mock_chat.py``'s own ``message_id = str(uuid.uuid4())``, generated before
the stream starts), a test's own fixed string offline. This function never
mints an id of its own: it reads ``sink.turn_id``/``sink.message_id`` back
for every use it has of either value (``TurnOutcome.message_id``/
``turn_run_id``, every writer call's ``message_id``). This follows directly
from the pinned ``execute_turn`` signature itself, which takes an
already-constructed ``sink`` and no separate ``message_id``/``turn_id``
parameter -- there would be no other consistent source for the SAME ids the
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

Identity (Phase 9 Task 1: real, no longer a placeholder)
----------------------------------------------------------------
This module used to hardcode ``DEV_USER_SUB = "dev|local"``, "everywhere a
user_sub is required" (the plan's own words), until Phase 9 wired a real
IdentityProvider -- that placeholder is gone. ``execute_turn`` now takes a
required ``user: UserContext`` (``poseidon.core.identity``), resolved
upstream by ``api/app.py``'s identity middleware from the actual request
(``api/live_chat.py`` reads ``request.state.user`` and passes it straight
through) and threaded into every writer call's ``user_sub`` (``user.sub``)
and into ``SkillContext.user`` verbatim -- including ``writer.finalize``
(Phase 11 Task 1, additive: that method did not take a ``user_sub`` before,
since it had no ``rls_transaction`` of its own to open yet -- see
``core/runlog.py``'s own docstring). The disabled-mode DEFAULT identity
is still the fixed ``UserContext("dev|local", ...)`` (now ``core.identity.
DISABLED_DEFAULT_USER``) -- so every existing row/test that pinned
``"dev|local"`` stays coherent -- but it now arrives THROUGH the seam
(``DisabledProvider``), not as a constant this module reaches for directly.
See ``core/identity.py``'s own module docstring for the full provider-
blindness contract (doc 05 section 2, decision D22): this module never
branches on identity_mode or imports a provider -- it only ever sees the
one ``UserContext`` the middleware already resolved.
"""

import dataclasses
import logging
from dataclasses import dataclass
from datetime import date
from time import monotonic

from poseidon.core.chat.events import SseEnvelopeSink
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.config import Settings
from poseidon.core.data.client import DataClient
from poseidon.core.identity import UserContext
from poseidon.core.llm.loop import (
    ROUTER_GUARDRAIL_ENTITY,
    ToolRecord,
    TurnResult,
    run_turn,
    tool_result_digest,
)
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
from poseidon.core.obs import span
from poseidon.core.ontology.loader import get_ontology
from poseidon.core.parsing import customer_resolver
from poseidon.core.parsing.pipeline import DEFAULT_ENTITY, parse_turn
from poseidon.core.parsing.types import ParsedTurn, ParseIssue
from poseidon.core.runlog import RunLogWriter
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.core.skills.result import problem

logger = logging.getLogger(__name__)

# doc 06's `turn_run.answer_summary` / this turn's clarify text -- "capped".
_ANSWER_SUMMARY_CAP = 500

# doc 02 section 5's pass-through cap (Global Constraints: "capped at 10").
_PASS_THROUGH_CAP = 10

# ---------------------------------------------------------------------------
# D19 entry orchestration (Phase 8 Task 5) -- see the module docstring's own
# "D19 entry orchestration" section for the full contract these constants
# and the functions below implement.
# ---------------------------------------------------------------------------

# ConversationSlots.mode values the bubble-entry flow writes -- the SAME
# strings poseidon.core.parsing.lexicon.MODE_SLOT_ALIASES already keys off
# of (that table has anticipated these values since Phase 4; this task is
# simply the first code that ever WRITES either one) and the SAME strings
# poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills.
# research.subskill.MODE_EXISTING/MODE_PROSPECT already hold. Declared here
# as bare string literals rather than imported from that subskill module:
# core/chat must not depend on poseidon.tasks (the same layering
# dev_router.py's own module docstring states explicitly for its own
# skill-id constants).
_MODE_EXISTING = "existing_customer"
_MODE_PROSPECT = "new_prospect"

# The two pinned flow-chip phrases (P8 whole-branch final-review wave,
# 2026-07-30, item 6 / I-6: now PUBLIC and imported by api/live_chat.py's
# own opener chips, which carry these verbatim as `send_text`, rather than
# keeping an independent literal copy of each string -- before this wave,
# a lockstep copy tweak here could silently break the D19 branch with
# every test green, since nothing cross-checked the two copies against
# each other. `poseidon.api.live_chat` already imports
# `poseidon.core.chat.events` -- this import keeps the SAME layering
# direction (api -> core.chat), never the reverse) -- matched
# casefolded-exact against the raw turn text, before parse_turn ever
# runs. The five literal copies already pinned directly in this suite's
# OWN tests (test_entry_orchestration.py, test_chat_e2e_scripted.py,
# test_live_chat_sse.py) stay as independent pins on purpose: they now
# guard these shared constants' VALUES, not merely duplicate them.
ENTRY_PHRASE_EXISTING = "start an existing-customer brief"
ENTRY_PHRASE_PROSPECT = "start a new-prospect brief"
_ENTRY_MODE_BY_PHRASE = {
    ENTRY_PHRASE_EXISTING: _MODE_EXISTING,
    ENTRY_PHRASE_PROSPECT: _MODE_PROSPECT,
}

_SUBJECT_PROMPT_BY_MODE = {
    _MODE_EXISTING: "Which customer is this brief for?",
    _MODE_PROSPECT: "What company should I research?",
}

# Bare string literals, not imports -- the same "core/chat names a skill id
# it never imports" convention dev_router.py's own _RESEARCH_SKILL_ID etc.
# already establish, for the identical layering reason.
_EXISTING_CUSTOMER_BRIEF_SKILL_ID = "customer_insight.existing_customer_brief"
_NEW_PROSPECT_BRIEF_SKILL_ID = "customer_insight.new_prospect_brief"

_CUST_NM = "CUST_NM"

_CUSTOMER_AMBIGUOUS = "customer_ambiguous"
_ROUTER_SYSTEM_PROMPT_NAME = "router/system"

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk (test_chat_orchestrator_module_files_are_ascii_
# on_disk) -- the same convention roles.py's/dev_router.py's own _EM_DASH use.
_EM_DASH = chr(0x2014)

# The retry short-circuit (Phase 6 Task 4 amendment, post-T3-review): when
# `writer.start_turn` reports `created=False`, a client retried a turn whose
# (user_sub, client_turn_key) already names an existing row -- the ORIGINAL
# request owns it. Pinned code/message; Phase 11 upgrades this to true replay
# (doc 01 section 5) once the reconciliation endpoint can look the original
# turn back up by id (exactly why turn_run_id IS the sink's own turn_id --
# see the module docstring's "Ids: who mints what").
_DUPLICATE_TURN_TITLE = "duplicate_turn"
_DUPLICATE_TURN_DETAIL = (
    "this turn was already processed " + _EM_DASH + " refresh to load the conversation"
)

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
    log (doc 01 section 5's ``GET /api/turns/{turn_id}``, a later phase).

    ``replayed`` (Phase 11 Task 3, additive -- defaults to ``False`` so
    every pre-existing construction of this dataclass, every call site in
    this module, and every equality assertion pinned before this task
    keeps meaning exactly what it meant before it): ``True`` exactly when
    ``_begin_turn``'s duplicate-turn branch replayed an earlier ``ok``
    turn's persisted answer instead of running the pipeline. ``api/live_
    chat.py`` reads this back to inject the additive ``"replayed": true``
    field into the ``done`` frame (never onto an ordinary turn's) and to
    skip a spurious title recompute."""

    status: str  # ok | clarify | error
    message_id: str
    turn_run_id: str | None
    replayed: bool = False


def execute_turn(
    *,
    conversation_id: str,
    user: UserContext,
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
    tools: object | None = None,
) -> TurnOutcome:
    """Run one chat turn to completion. See the module docstring for the
    full pinned order and the three terminal-state disciplines.

    ``writer`` may be ``None`` (no ``DATABASE_URL`` configured) -- every
    writer call below is guarded by ``writer is not None and turn_run_id is
    not None`` (the second half covers a writer that IS configured but whose
    own ``start_turn`` failed, per its never-raises contract -- see
    ``runlog.py``): the turn produces the identical stream and state-store
    behavior either way, only the run log gains no rows.

    ``tools`` (Phase 7 Task 4, additive -- defaults to ``None`` so every
    call site before this task keeps working unchanged) is threaded
    straight to ``SkillContext.tools`` below, unexamined: this function has
    no opinion about what it is or where it came from, the same "typed
    ``object``, cast at the skill's own call site" seam ``SkillContext``
    itself documents. ``poseidon.api.live_chat`` supplies the real
    ``ToolServerRegistry`` ``api/app.py`` builds once per app; a skill that
    never reads ``ctx.tools`` (``data_qa.metric_query`` today) is
    unaffected either way.

    ``SkillContext.llm``/``SkillContext.emit_part`` (Phase 8 Task 1, both
    additive -- default ``None``, so every call site before this task keeps
    working unchanged) are wired below, at the one place this function
    builds a ``SkillContext``: ``llm`` to this call's own ``role_client``
    (the identical object ``run_turn`` uses for routing -- the only
    truthful choice), ``emit_part`` to ``sink.part_emitter(1)`` (see that
    call site's own comment for the disclosed tool_seq-1 scoping this
    implies). Neither is examined here any more than ``tools`` is -- a
    skill that never reads ``ctx.llm``/``ctx.emit_part`` is unaffected
    either way.

    ``user`` (Phase 9 Task 1) is the resolved caller identity -- a
    :class:`~poseidon.core.identity.UserContext` the HTTP layer already
    resolved from the request (``api/app.py``'s identity middleware)
    before this function was ever called. REQUIRED, not defaulted, unlike
    ``tools``/``llm``/``emit_part`` above: every turn needs SOME identity
    to log against, and there is no honest default this function could
    invent on its own (the disabled-mode fixed default is a choice the
    IdentityProvider seam makes, upstream of here, not this function's to
    fabricate). Threaded into every ``writer.start_turn``/``append_llm_
    call``/``append_tool_call`` call below via ``user.sub`` -- replacing
    the old fixed ``DEV_USER_SUB`` constant -- and into ``SkillContext.
    user`` verbatim, unexamined here any more than ``tools`` is.
    """
    prior_slots = state.get(conversation_id)

    # D19, branch 1 (Phase 8 Task 5): a pinned flow-chip phrase, matched
    # BEFORE parse_turn ever runs -- see the module docstring's "D19 entry
    # orchestration".
    entry_mode = _ENTRY_MODE_BY_PHRASE.get(text.casefold())
    if entry_mode is not None:
        return _finish_entry(
            entry_mode=entry_mode,
            text=text,
            conversation_id=conversation_id,
            user=user,
            client_turn_key=client_turn_key,
            prior_slots=prior_slots,
            state=state,
            writer=writer,
            sink=sink,
        )

    # D19, branch 2: the subject turn following a successful entry branch
    # (or a retry after one that failed to resolve a subject) -- also
    # checked BEFORE parse_turn; see the module docstring's same section.
    if prior_slots.mode in _SUBJECT_PROMPT_BY_MODE and not state.get_brief_done(conversation_id):
        return _finish_subject_turn(
            text=text,
            conversation_id=conversation_id,
            user=user,
            client_turn_key=client_turn_key,
            prior_slots=prior_slots,
            settings=settings,
            registry=registry,
            data=data,
            state=state,
            writer=writer,
            role_client=role_client,
            sink=sink,
            tools=tools,
        )

    # Phase 11 Task 2 (doc 06 section 3): a pure span() wrapper around this
    # EXISTING call -- zero logic change, no re-timing (parse_turn's own
    # return value/behavior is untouched) -- see core/obs.py's own
    # docstring for why this is the one sanctioned touch this task makes to
    # this file's own call sites.
    with span("parse"):
        parsed = parse_turn(text, prior_slots, reference_date, data)
    turn_index, turn_run_id, retry_outcome = _begin_turn(
        conversation_id=conversation_id,
        user=user,
        text=text,
        client_turn_key=client_turn_key,
        mode=parsed.slots.mode,
        parsed_loggable=_parsed_to_loggable_dict(parsed),
        state=state,
        writer=writer,
        sink=sink,
    )
    if retry_outcome is not None:
        return retry_outcome

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
            user=user,
            started=started,
        )

    context = SkillContext(
        data=data,
        artifacts=None,
        settings=settings,
        state=parsed.slots,
        tools=tools,
        user=user,
        # Phase 8 Task 1: `llm` is simply THIS turn's own role_client -- a
        # subskill calling `ctx.llm.invoke(role=..., ...)` reaches the exact
        # same RoleClient `run_turn` below uses for routing, never a second,
        # independently-configured one.
        llm=role_client,
        # `emit_part` is wired to tool_seq 1 -- the FIRST dispatch of this
        # turn. `context` is built once, here, before `run_turn` even starts
        # (so before ANY tool call exists, tool_seq 1 is the only one that
        # can be known yet), and stays fixed for every dispatch of the turn
        # (`run_turn`/`_dispatch_one` are outside this task's sanctioned
        # edits -- see loop.py's own docstring -- so this function gets
        # exactly one chance to wire it, not one per dispatch). This is
        # correct for every emit_part consumer this plan actually ships: a
        # brief skill (Phase 8 Task 4) is always the ONLY tool call of its
        # turn, whether reached through D19's deterministic entry (Task 5)
        # or a normal routed dispatch, so tool_seq is always 1 for it.
        #
        # Fix round 1 correction: an earlier version of this comment
        # claimed a hypothetical second dispatch that also tries
        # `ctx.emit_part` would "simply not get early streaming ... safe,
        # not silently wrong." That claim is FALSE, reproduced: `context`
        # is built ONCE per turn, so `ctx.emit_part` is this SAME tool_seq-1
        # closure for every dispatch, never a fresh one per tool_seq -- a
        # second dispatch's skill that calls it DOES push a real part frame
        # to the wire, then raises KeyError on the count increment, because
        # tool_seq 1's own key was already popped when tool_seq 1's
        # tool_done fired (which always completes before a later dispatch's
        # skill function even starts). `SkillRegistry.dispatch`'s own
        # try/except catches that KeyError into a structured
        # `SkillResult(ok=False, ...)`, so the second dispatch fails
        # LOUDLY -- with one already-streamed, orphaned part frame, and a
        # likely-identical failure on the model's one self-correction retry
        # -- never a silent, harmless no-op. See events.py's own module
        # docstring ("Incremental part streaming", its own Fix round 1
        # correction) for the full failure mode and why popping (loud
        # failure) beats not popping (silent double-emission) regardless.
        # The real fix -- rebinding `ctx.emit_part` per dispatch -- needs a
        # loop.py change (`_dispatch_one` calling
        # `sink.part_emitter(current_tool_seq)` again before each call),
        # deliberately out of this phase's sanctioned edit (the one
        # artifacts line). Until then: correct for every consumer this plan
        # ships (a brief skill is always the sole dispatch of its turn), a
        # real, sharp failure mode for a hypothetical second one -- not the
        # harmless fallback earlier claimed.
        #
        # Second correction (P8 whole-branch final-review wave, 2026-07-30,
        # item 3 / I-1): "a hypothetical second one" above undersold ONE of
        # its own two paragraphs -- the self-correction-retry case this
        # SAME comment already named ("a likely-identical failure on the
        # model's one self-correction retry") is not hypothetical at all.
        # tool_seq counts DISPATCHES, not successes (`_dispatch_one`'s own
        # `tool_seq=len(tool_records) + 1`), so a routed brief dispatch that
        # fails once and gets retried by loop.py's own one-shot correction
        # reaches tool_seq 2 on an entirely ordinary turn, while this
        # closure -- built once, right here, before any dispatch exists --
        # stays bound to tool_seq 1 regardless. Reproduced directly, not
        # merely re-argued: see events.py's own module docstring for its
        # identical second correction and the pinning test. This wave adds
        # the guard events.py's own `part_emitter` now documents (a retired
        # tool_seq's closure no-ops instead of pushing-then-KeyError-ing),
        # which makes the CONSUMER-FACING claim two paragraphs up --
        # "correct for every consumer this plan ships" -- actually true
        # for this reachable case too: the retry now succeeds, and its
        # streamed part reaches the wire once, at its own real tool_done.
        # The remaining, still-unfixed shape is narrower than either
        # earlier paragraph implied: two DIFFERENTLY-NAMED skills each
        # dispatched once in the same turn, both wanting to stream early --
        # THAT still needs the per-dispatch rebinding described above, and
        # remains genuinely out of this phase's sanctioned edit.
        emit_part=sink.part_emitter(1),
    )
    router_version, router_hash = _router_prompt_provenance(
        settings=settings,
        prompt_registry=prompt_registry,
        registry=registry,
        context=context,
        parsed=parsed,
    )

    window = [{"role": "user", "content": [{"text": text}]}]
    # Phase 11 Task 2: a pure span() wrapper around this EXISTING call --
    # same rationale as the "parse" span above. turn_id is threaded as an
    # ordinary named context field (not the JSON envelope's own top-level
    # turn_id key, which stays null in this task -- see obs.py's module
    # docstring) purely because sink.turn_id already happens to be in scope
    # here, for free, and is genuinely useful for correlating a span line
    # back to one turn.
    with span("route", turn_id=sink.turn_id):
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
            user=user,
            turn_result=turn_result,
            router_version=router_version,
            router_hash=router_hash,
            settings=settings,
        )
        writer.finalize(
            turn_run_id=turn_run_id,
            user_sub=user.sub,
            status=turn_result.status,
            message_id=sink.message_id,
            answer_summary=(_capped(turn_result.text) if turn_result.status == "ok" else None),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            latency_ms=latency_ms,
            error=(turn_result.problem if turn_result.status == "error" else None),
        )

    if turn_result.status == "ok":
        # Double-terminal guard (final-review wave item 5 / I5): by this
        # point sink.done() and writer.finalize(status="ok") have ALREADY
        # gone out for this turn (above). _repopulate_pass_through's own
        # docstring names two reachable raisers on a malformed table part
        # (table["payload"] KeyError; row[0] IndexError on an empty row) --
        # letting either escape here would propagate out of execute_turn
        # entirely, and at the HTTP layer would drive run_turn_sync's own
        # crash handler into a SECOND, contradictory finalize/error frame for
        # a turn that already finished successfully (live_chat.py's own
        # "Fix round 1, REQUIRED F1" guard was written for a failure BEFORE
        # this point, not a second one after it). Scoped to ONLY this pair,
        # not the append/finalize block above it: an append failure must
        # still reach finalize, or the turn_run row orphans at 'running'
        # forever (see that guard's own docstring one level up).
        try:
            final_slots = _repopulate_pass_through(parsed.slots, turn_result.tool_records, sink)
            state.put(conversation_id, final_slots)
        except Exception as exc:  # noqa: BLE001 - a second failure must never re-terminate a finished turn
            logger.error(
                "pass-through repopulation failed: conversation_id=%s turn_run_id=%s: %s: %s",
                conversation_id,
                turn_run_id,
                type(exc).__name__,
                exc,
            )
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
    user: UserContext,
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

    ``user`` (Phase 11 Task 1, additive) is new: ``writer.finalize`` below
    now takes a ``user_sub`` (``core/runlog.py``'s own docstring), and this
    was the one ``execute_turn`` helper with no identity of its own to
    supply it -- threaded straight from ``execute_turn``'s own ``user``
    parameter, unexamined otherwise, the same "argument-addition-only"
    change this task's brief sanctions for every writer call site here.
    """
    sink.push_part(
        "chips",
        {
            "options": [
                # send_text SCOPED to clarify chips only (final-review wave
                # item 2 / I1 + M6) -- a "for <name>" cue is what makes the
                # customer resolver treat the click as naming a customer at
                # tier-exact 1.0 (verified against the full seeded pool: 40/40
                # customers, 0/30 ports misresolved). This is NOT a blanket
                # prefix: "for Existing customer" would corrupt an opener
                # flow-chip click into customer_unknown, which is why the
                # opener's own two chips (api/live_chat.py's TranscriptStore.
                # create_conversation) carry a DIFFERENT send_text of their
                # own instead (Phase 8 Task 5: the D19 bubble-entry phrases,
                # "start an existing-customer brief"/"start a new-prospect
                # brief") rather than either this "for <name>" cue or no
                # send_text at all -- this scoping note updates the earlier,
                # now-stale claim that opener chips carried none whatsoever.
                # See ChipsPart.tsx's own option.send_text ?? option.label
                # for the frontend fallback either shape relies on.
                {"id": candidate, "label": candidate, "send_text": f"for {candidate}"}
                for candidate in ambiguous_issue.candidates
            ]
        },
    )
    sink.push_part("text", {"markdown": ambiguous_issue.message})
    sink.done({"input_tokens": 0, "output_tokens": 0})

    latency_ms = int((monotonic() - started) * 1000)
    if writer is not None and turn_run_id is not None:
        writer.finalize(
            turn_run_id=turn_run_id,
            user_sub=user.sub,
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


def _begin_turn(
    *,
    conversation_id: str,
    user: UserContext,
    text: str,
    client_turn_key: str | None,
    mode: str,
    parsed_loggable: dict,
    state: ConversationStateStore,
    writer: RunLogWriter | None,
    sink: SseEnvelopeSink,
) -> tuple[int, str | None, "TurnOutcome | None"]:
    """Turn bookkeeping shared by every ``execute_turn`` path -- see the
    module docstring's "Shared turn bookkeeping" for why this was factored
    out once a THIRD call site (this task's two D19 branches, alongside the
    pre-existing normal path) needed the identical sequence: mint
    ``turn_index``, open the run-log row, emit ``accepted``, and apply the
    retry short-circuit (Task 4 amendment -- see the module docstring's "A
    fourth, EARLIER short-circuit").

    ``mode``/``parsed_loggable`` are computed by the CALLER, since each of
    the three paths has a different notion of both (the normal path's own
    ``parsed.slots.mode``/``_parsed_to_loggable_dict(parsed)``; the D19
    branches' own small hand-built dicts -- see :func:`_finish_entry`/
    :func:`_finish_subject_turn`).

    Returns ``(turn_index, turn_run_id, retry_outcome)``. ``retry_outcome``
    is non-``None`` exactly when the caller must return it immediately,
    without doing anything else -- the SAME "the ORIGINAL turn owns the
    row" contract the pre-Task-5 inline version of this code already
    implemented, moved here unchanged.
    """
    turn_index = state.next_turn_index(conversation_id)
    handle = None
    if writer is not None:
        handle = writer.start_turn(
            user_sub=user.sub,
            conversation_id=conversation_id,
            client_turn_key=client_turn_key,
            turn_index=turn_index,
            question=text,
            mode=mode,
            parsed=parsed_loggable,
            kind="chat_turn",
            trace_id=None,
            # Turn-id unification (Task 4 amendment): turn_run.id IS the SSE
            # turn_id every frame of this response already carries -- see the
            # module docstring's "Ids: who mints what".
            turn_run_id=sink.turn_id,
        )
    turn_run_id = handle.turn_run_id if handle is not None else None

    sink.accepted(turn_index)

    if handle is not None and not handle.created:
        # turn_run_id here is the ORIGINAL row's id (the one start_turn
        # found, not sink.turn_id), since this request never created a row
        # of its own.
        #
        # Phase 11 Task 3 (doc 01 section 5): true replay. Ask the writer
        # (read_for_replay -- see runlog.py's own docstring for why THAT
        # module, not this one, owns the messages join) what the ORIGINAL
        # turn's terminal status and persisted answer were. Only an "ok"
        # status with real, persisted parts replays; a "running"/"error"
        # status, a writer that could not be asked (writer is None -- never
        # actually reachable here, since handle is only non-None when
        # writer was, but checked explicitly rather than assumed), or a
        # lookup failure (read_for_replay never raises -- see its own
        # docstring) all fall through to the SAME duplicate_turn error this
        # branch has always produced, byte-identical.
        replay = (
            writer.read_for_replay(turn_run_id=turn_run_id, user_sub=user.sub)
            if writer is not None
            else None
        )
        if replay is not None and replay.status == "ok" and replay.parts is not None:
            # Replay the ORIGINAL answer's parts verbatim, as one part-frame
            # sequence, via push_part -- the SAME public sink method the
            # clarify short-circuit above already calls directly. This is
            # why the P8 "retired-dispatch guard" (events.py's own
            # part_emitter/_streamed_counts bookkeeping) never enters the
            # picture: that guard only ever triggers through the closure
            # sink.part_emitter(tool_seq) returns to a SKILL mid-dispatch,
            # and nothing on this path ever calls part_emitter or dispatches
            # anything -- push_part/done are unconditional, always-safe
            # public methods that never read or write _streamed_counts.
            # No run_turn call, no registry.dispatch, no writer.append_*/
            # finalize -- the pipeline genuinely never executes, so no new
            # turn_run row and no new llm_calls/tool_calls rows are ever
            # produced for a replayed turn.
            for part in replay.parts:
                sink.push_part(part["kind"], part["payload"])
            sink.done({"input_tokens": 0, "output_tokens": 0})
            return (
                turn_index,
                turn_run_id,
                TurnOutcome(
                    status="ok",
                    message_id=sink.message_id,
                    turn_run_id=turn_run_id,
                    replayed=True,
                ),
            )
        sink.emit(
            "turn_error", {"problem": problem(409, _DUPLICATE_TURN_TITLE, _DUPLICATE_TURN_DETAIL)}
        )
        return (
            turn_index,
            turn_run_id,
            TurnOutcome(status="error", message_id=sink.message_id, turn_run_id=turn_run_id),
        )

    return turn_index, turn_run_id, None


def _finish_entry(
    *,
    entry_mode: str,
    text: str,
    conversation_id: str,
    user: UserContext,
    client_turn_key: str | None,
    prior_slots: ConversationSlots,
    state: ConversationStateStore,
    writer: RunLogWriter | None,
    sink: SseEnvelopeSink,
) -> TurnOutcome:
    """D19 branch 1: the bubble-entry phrase itself -- see the module
    docstring's "D19 entry orchestration" for the full contract. Mode set
    via ``dataclasses.replace`` (the P6 pass_through precedent), one text
    part asking the subject, ``clarify`` finalize, no dispatch, no router.
    """
    new_slots = dataclasses.replace(prior_slots, mode=entry_mode)
    turn_index, turn_run_id, retry_outcome = _begin_turn(
        conversation_id=conversation_id,
        user=user,
        text=text,
        client_turn_key=client_turn_key,
        mode=entry_mode,
        parsed_loggable=_entry_loggable_dict(text, new_slots),
        state=state,
        writer=writer,
        sink=sink,
    )
    if retry_outcome is not None:
        return retry_outcome

    started = monotonic()
    prompt_text = _SUBJECT_PROMPT_BY_MODE[entry_mode]
    sink.push_part("text", {"markdown": prompt_text})
    sink.done({"input_tokens": 0, "output_tokens": 0})

    latency_ms = int((monotonic() - started) * 1000)
    if writer is not None and turn_run_id is not None:
        writer.finalize(
            turn_run_id=turn_run_id,
            user_sub=user.sub,
            status="clarify",
            message_id=sink.message_id,
            answer_summary=_capped(prompt_text),
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            error=None,
        )

    state.put(conversation_id, new_slots)
    # A second flow-chip click starts a SECOND brief in the same
    # conversation -- never blocked by an earlier one's completion.
    state.set_brief_done(conversation_id, False)
    return TurnOutcome(status="clarify", message_id=sink.message_id, turn_run_id=turn_run_id)


def _finish_subject_turn(
    *,
    text: str,
    conversation_id: str,
    user: UserContext,
    client_turn_key: str | None,
    prior_slots: ConversationSlots,
    settings: Settings,
    registry: SkillRegistry,
    data: DataClient,
    state: ConversationStateStore,
    writer: RunLogWriter | None,
    role_client: RoleClient,
    sink: SseEnvelopeSink,
    tools: object | None,
) -> TurnOutcome:
    """D19 branch 2: the subject turn -- see the module docstring's "D19
    entry orchestration" for the full contract (resolution rules, the
    deterministic-dispatch shape, the ``brief_done`` gating).
    """
    mode = prior_slots.mode
    turn_index, turn_run_id, retry_outcome = _begin_turn(
        conversation_id=conversation_id,
        user=user,
        text=text,
        client_turn_key=client_turn_key,
        mode=mode,
        parsed_loggable={"subject_text": text, "mode": mode},
        state=state,
        writer=writer,
        sink=sink,
    )
    if retry_outcome is not None:
        return retry_outcome

    started = monotonic()

    if mode == _MODE_EXISTING:
        resolution = _resolve_subject_customer(text, data)
        if resolution.entity is None:
            return _finish_subject_clarify(
                issue=resolution.issue,
                sink=sink,
                writer=writer,
                turn_run_id=turn_run_id,
                user=user,
                started=started,
            )
        subject = resolution.entity.value
        skill_id = _EXISTING_CUSTOMER_BRIEF_SKILL_ID
        arguments: dict[str, object] = {"customer": subject}
    else:  # _MODE_PROSPECT -- D19's own "text = subject" rule: no resolver.
        subject = text.strip()
        skill_id = _NEW_PROSPECT_BRIEF_SKILL_ID
        arguments = {"prospect_name": subject}

    context = SkillContext(
        data=data,
        artifacts=None,
        settings=settings,
        state=prior_slots,
        tools=tools,
        user=user,
        llm=role_client,
        # tool_seq 1 -- the SAME "this turn's only dispatch" reasoning
        # execute_turn's own SkillContext construction documents at length
        # for the normal path; identically true here (see the module
        # docstring's "The subject branch").
        emit_part=sink.part_emitter(1),
    )

    sink.emit("tool_start", {"tool_seq": 1, "skill_id": skill_id, "arguments": dict(arguments)})
    dispatch_started = monotonic()
    result = registry.dispatch(skill_id, dict(arguments), context)
    duration_ms = int((monotonic() - dispatch_started) * 1000)
    digest = tool_result_digest(skill_id, result)
    status = "ok" if result.ok else "error"
    sink.emit(
        "tool_done",
        {
            "tool_seq": 1,
            "skill_id": skill_id,
            "status": status,
            "duration_ms": duration_ms,
            "digest": digest,
            "parts": result.parts,
            "proof": result.proof,
            "artifacts": result.artifacts,
            "problem": result.error,
        },
    )
    tool_record = ToolRecord(
        tool_seq=1,
        skill_id=skill_id,
        arguments=dict(arguments),
        status=status,
        duration_ms=duration_ms,
        result_digest=digest,
    )
    # A synthetic TurnResult carrying this ONE record and NO llm_records --
    # never a real run_turn output (there was no router call to produce
    # one) -- built purely so _append_records below can be reused
    # unchanged; see the module docstring's "The subject branch".
    fake_turn_result = TurnResult(
        text="",
        status=status,
        tool_records=(tool_record,),
        llm_records=(),
        problem=(result.error if not result.ok else None),
    )

    if not result.ok:
        sink.emit("turn_error", {"problem": result.error})
        latency_ms = int((monotonic() - started) * 1000)
        if writer is not None and turn_run_id is not None:
            _append_records(
                writer=writer,
                turn_run_id=turn_run_id,
                user=user,
                turn_result=fake_turn_result,
                router_version="",
                router_hash="",
                settings=settings,
            )
            writer.finalize(
                turn_run_id=turn_run_id,
                user_sub=user.sub,
                status="error",
                message_id=sink.message_id,
                answer_summary=None,
                input_tokens=0,
                output_tokens=0,
                latency_ms=latency_ms,
                error=result.error,
            )
        # brief_done left False -- a corrected subject can retry.
        return TurnOutcome(status="error", message_id=sink.message_id, turn_run_id=turn_run_id)

    # No sink.push_token: there was no router call to compose a reply, and
    # the brief's own streamed parts already ARE the answer (see the module
    # docstring's "The subject branch").
    sink.done({"input_tokens": 0, "output_tokens": 0})
    answer_summary = f"Brief generated for {subject}."

    latency_ms = int((monotonic() - started) * 1000)
    if writer is not None and turn_run_id is not None:
        _append_records(
            writer=writer,
            turn_run_id=turn_run_id,
            user=user,
            turn_result=fake_turn_result,
            router_version="",
            router_hash="",
            settings=settings,
        )
        writer.finalize(
            turn_run_id=turn_run_id,
            user_sub=user.sub,
            status="ok",
            message_id=sink.message_id,
            answer_summary=_capped(answer_summary),
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            error=None,
        )

    state.set_brief_done(conversation_id, True)
    # D19 subject carry (P8 whole-branch final-review wave, 2026-07-30,
    # item 5 / I-3 -- a SEPARATE, unrelated "item 5" from the double-
    # terminal guard comment immediately below, which is this SAME file's
    # own earlier-phase wave numbering): a successful EXISTING-mode
    # dispatch's resolved customer becomes this conversation's carried
    # subject, so a later bare pivot ("any relevant news I should know
    # about?" -- no subject named) still has a customer to attach (doc
    # 08's own "pivots with carried entities" promise, previously UNMET on
    # this path -- prior_slots.customer stayed whatever it was before the
    # D19 entry branch ever ran, since _repopulate_pass_through only ever
    # touches pass_through, never customer, and a brief skill's own
    # arguments never include group_by). Deliberately EXISTING MODE ONLY:
    # a prospect's subject (`text.strip()`, D19's own "text = subject"
    # rule) is never resolved against the certified customer dimension, so
    # writing it into slots.customer would carry an UNCERTIFIED value into
    # a slot later code treats as certified (dev_router.py's own filters,
    # the customer resolver's own hint machinery) -- the SAME shape of
    # hazard morning decision 8 already names for the resolver's own
    # fuzzy-capture on pivots (a prospect name sharing tokens with a real
    # customer). This carry does not create that hazard on its own, but it
    # would hand the resolver's existing fuzzy-match behavior a plausible-
    # looking "certified" customer to latch onto that was never actually
    # certified -- so prospect mode is excluded here, not merely
    # unimplemented.
    carried_slots = (
        dataclasses.replace(prior_slots, customer=subject)
        if mode == _MODE_EXISTING
        else prior_slots
    )
    # The identical double-terminal guard execute_turn's own normal path
    # uses (final-review wave item 5 / I5): by this point sink.done() and
    # writer.finalize(status="ok") have already gone out for this turn.
    try:
        final_slots = _repopulate_pass_through(carried_slots, (tool_record,), sink)
        state.put(conversation_id, final_slots)
    except Exception as exc:  # noqa: BLE001 - a second failure must never re-terminate a finished turn
        logger.error(
            "pass-through repopulation failed: conversation_id=%s turn_run_id=%s: %s: %s",
            conversation_id,
            turn_run_id,
            type(exc).__name__,
            exc,
        )

    return TurnOutcome(status="ok", message_id=sink.message_id, turn_run_id=turn_run_id)


def _resolve_subject_customer(text: str, data: DataClient) -> customer_resolver.Resolution:
    """The subject turn's OWN customer resolution (D19, existing mode): the
    WHOLE subject message -- never cue-gated the way ``parse_turn``'s own
    customer detector is -- is handed directly to ``customer_resolver.
    resolve()``, "the customer resolver (full ambiguity contract)" the
    Global Constraints name. This deliberately bypasses ``parse_turn``
    entirely (see the module docstring's "D19 entry orchestration" --
    checked BEFORE parse_turn's normal path), so this is the one place in
    this module that calls ``customer_resolver.resolve()`` directly rather
    than reading a already-resolved ``ParsedTurn.customer``.
    """
    values = data.list_dimension_values(DEFAULT_ENTITY, _CUST_NM)
    return customer_resolver.resolve(text.strip(), values, "customer")


def _finish_subject_clarify(
    *,
    issue: ParseIssue,
    sink: SseEnvelopeSink,
    writer: RunLogWriter | None,
    turn_run_id: str | None,
    user: UserContext,
    started: float,
) -> TurnOutcome:
    """The subject turn's own customer-resolution failure (candidate-band
    or unknown) -- the same ``clarify`` discipline :func:`_finish_clarify`
    uses for a normal turn's ``customer_ambiguous`` issue, scoped to the
    subject turn's own direct ``customer_resolver.resolve()`` call.

    ``user`` (Phase 11 Task 1, additive): same reason as :func:`_finish_
    clarify`'s identical addition -- threaded from :func:`_finish_subject_
    turn`'s own ``user`` parameter so ``writer.finalize`` below has an
    identity to open ``rls_transaction`` with.

    Chips (when ``issue.candidates`` is non-empty) carry BARE ``send_text``
    (falls back to ``label``) rather than :func:`_finish_clarify`'s own
    "for <name>" cue: the subject turn feeds the ENTIRE next message
    straight back into ``customer_resolver.resolve()`` (no cue-word
    requirement), so a bare candidate name already resolves at exact tier
    1.0 without needing the "for " prefix that only matters for
    ``parse_turn``'s own cue-gated detector.

    No ``state.put``: nothing resolved this turn (an ambiguous or unknown
    result carries no new customer/port/period), so ``prior_slots`` is
    already exactly what ``state.get`` would return -- unlike
    :func:`_finish_clarify`, which ran ``parse_turn`` and so may have a
    freshly resolved period to carry even when the customer did not.
    ``brief_done`` is likewise left untouched (still ``False``, or this
    branch would never have been reached) -- so the very next message is
    read as another subject-turn attempt.
    """
    if issue.candidates:
        sink.push_part(
            "chips",
            {"options": [{"id": candidate, "label": candidate} for candidate in issue.candidates]},
        )
    sink.push_part("text", {"markdown": issue.message})
    sink.done({"input_tokens": 0, "output_tokens": 0})

    latency_ms = int((monotonic() - started) * 1000)
    if writer is not None and turn_run_id is not None:
        writer.finalize(
            turn_run_id=turn_run_id,
            user_sub=user.sub,
            status="clarify",
            message_id=sink.message_id,
            answer_summary=_capped(issue.message),
            input_tokens=0,
            output_tokens=0,
            latency_ms=latency_ms,
            error=None,
        )

    return TurnOutcome(status="clarify", message_id=sink.message_id, turn_run_id=turn_run_id)


def _entry_loggable_dict(text: str, slots: ConversationSlots) -> dict:
    """A small, JSON-safe dict for the entry turn's run-log ``parsed``
    column -- there is no ``ParsedTurn`` here (``parse_turn`` never runs
    for this branch), so this is not :func:`_parsed_to_loggable_dict`'s
    concern; reuses :func:`_json_safe` regardless, since a carried
    ``slots.period_a``/``period_b`` (from a conversation already in
    progress before the flow-chip click) can still hold raw ``date``
    values ``json.dumps`` cannot serialize on its own.
    """
    return {"entry_phrase": text, "slots": _json_safe(dataclasses.asdict(slots))}


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
    *,
    writer: RunLogWriter,
    turn_run_id: str,
    user: UserContext,
    turn_result,
    router_version: str,
    router_hash: str,
    settings: Settings,
) -> None:
    """Every ``LLMRecord``/``ToolRecord`` the turn collected, regardless of
    whether it ultimately succeeded -- doc 06: a turn that failed on its
    Nth call still has N-1 real dispatches worth logging.

    ``provider`` (final-review wave item 4 / I3): ``record.provider`` is the
    CONFIGURED provider (``RoleClient.resolve``'s own answer -- see
    ``LLMRecord``'s docstring, "truthful in either mode... the only answer
    available to a loop that never learns which provider actually
    answered"), which is exactly WRONG for a run-log row: under
    ``LLM_MODE=stub`` the call was actually answered by
    ``DevDeterministicRouter``, not the configured provider, and doc 06
    section 1 reserves the literal ``"stub"`` for that case. ``settings`` is
    the one thing ``_append_records`` has that ``record`` itself does not --
    the ``llm_mode`` the loop that produced ``record`` never gets to see.
    """
    for record in turn_result.llm_records:
        writer.append_llm_call(
            turn_run_id=turn_run_id,
            user_sub=user.sub,
            seq=record.call_seq,
            provider=("stub" if settings.llm_mode == "stub" else record.provider),
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
            user_sub=user.sub,
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

    Can raise on a malformed table part -- ``table["payload"]`` (``KeyError``
    if a table part somehow carries no ``"payload"`` key) and ``row[0]``
    (``IndexError`` if a row is unexpectedly empty). Neither is reachable
    through any real dispatch this codebase can produce today (verified: no
    caller builds a table part this way), but the caller
    (:func:`execute_turn`) guards this call regardless -- final-review wave
    item 5's double-terminal guard -- because by the time this runs,
    ``sink.done()`` and ``writer.finalize(status="ok")`` have already gone
    out for this turn, so a raise here must never be allowed to re-terminate
    an already-finished turn.
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


__all__ = [
    "ENTRY_PHRASE_EXISTING",
    "ENTRY_PHRASE_PROSPECT",
    "TurnOutcome",
    "execute_turn",
]
