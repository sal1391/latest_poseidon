"""The idle-triggered memory-distillation worker (Phase 13 Task 4; doc 05
section 5, decision D31).

Every turn-completion site upserts the finished conversation's
``memory_outbox`` row, bumping ``last_turn_at`` (Task 2). This process is
the other end of that queue: it wakes up periodically, claims the rows
nobody has touched for ``settings.memory_idle_minutes``, runs the
``memory`` LLM role over each conversation plus that user's existing
memory entries, and appends the result as a new ``user_memory`` version
attributed to the distiller.

**A genuinely new architectural shape for this codebase, and the one thing
to understand before reading anything else here.** Every prior background
job in ``poseidon/scripts/`` is a one-shot CLI: it runs, it prints, it
exits (``seed_synthetic``, ``cost_rollup``, ``export_router_cases``). This
is the first long-running poller. The whole design consequence is that
``main`` is deliberately trivial -- a ``while True`` around exactly one
call -- and ALL of the actual behavior lives in :func:`run_once`, which
does one cycle and returns. That split is not stylistic: an infinite loop
cannot be tested, and the durability guarantees this worker makes (below)
are exactly the kind of thing that has to be proven against a real
Postgres, not reasoned about. ``backend/tests/test_memory_worker.py``
drives :func:`run_once` and :func:`claim_idle_conversations` directly and
never touches the loop.

The privilege model (the one subtle thing in this phase)
-------------------------------------------------------
:func:`claim_idle_conversations` is the ONLY operation in this entire
phase that reads ``memory_outbox`` outside a per-user
:func:`~poseidon.core.db.rls_transaction`. It cannot be otherwise: "which
conversations, across all users, have gone idle" is a question with no
single ``app.user_sub`` to scope it to, and row-level security's own
``USING (user_sub = current_setting('app.user_sub', true))`` predicate
returns ZERO rows when that setting is absent (it fails closed -- see
``core/db.py``'s module docstring). So the claim runs on the raw
``DATABASE_URL`` connection with no ``rls_transaction`` wrapper and no
``SET LOCAL ROLE`` -- the same privileged posture ``alembic upgrade`` and
``poseidon.scripts.seed_synthetic`` already use -- and it sees every user's
rows because that role bypasses RLS (this project's compose Postgres
bootstraps ``DATABASE_URL``'s role as the cluster superuser; verified
empirically before this module was written, and re-proved on every pg run
by ``test_the_claim_connection_actually_bypasses_row_level_security``).

Everything after the claim is ordinary. Reading the claimed conversation's
messages, reading and writing that user's memory, moving their outbox row
to ``done``/``failed``, writing the ``turn_run``/``llm_calls`` rows -- each
goes through the normal ``Store.for_user(row.user_sub)`` /
``rls_transaction`` path every other per-user operation in this codebase
uses, with ``settings.database_app_role`` threaded through exactly as
``api/app.py`` threads it. The privilege escalation is scoped to the one
query that structurally cannot be scoped to a single user, and to nothing
else.

Durability: why there is no ``processing`` status, and no lease
--------------------------------------------------------------
``memory_outbox.status`` is ``pending`` | ``done`` | ``failed``. There is
deliberately no intermediate ``processing`` value and no lease timestamp
(doc 05 section 5's schema, decision D31). A claim is a ``SELECT ... FOR
UPDATE SKIP LOCKED`` inside a short transaction that commits immediately;
the row itself is never mutated at claim time.

The consequence is the property doc 08's own Phase Gate asks for: a worker
killed at ANY point in a cycle leaves every row it was working on exactly
``pending``, with ``attempts`` untouched, because nothing was written
before the work finished. There is no half-claimed limbo state to recover
from, and therefore no lease-expiry sweeper this phase has to build and
test. The next cycle simply picks the row back up. (Pinned directly by
``test_a_crash_mid_cycle_leaves_the_row_pending_and_the_next_run_once_
succeeds``, which raises a ``BaseException`` straight through
:func:`run_once` rather than merely simulating a handled failure.)

The cost of that choice, stated plainly: the ``FOR UPDATE`` lock is
released when the claim transaction commits, not when the distillation
finishes, so ``SKIP LOCKED`` prevents two workers from claiming the same
row in the same instant but not from claiming it minutes apart. Today's
deployment runs exactly one worker container (``infra/docker-compose.yml``'s
``worker`` service), so that window is not reachable; a future multi-worker
deployment would need a real lease, which is a schema change this phase
does not make. The worst case if it were reached is a duplicate memory
version -- append-only, one click to restore -- never a corrupted one.

The related race that IS reachable today, and is handled: a user coming
back mid-distillation. Their turn reaches ``ConversationOutbox.touch``,
which fully re-arms the row. Both status transitions
(:meth:`~poseidon.core.personalization.outbox.ConversationOutbox.mark_done`
/ ``record_failure``) are therefore guarded on the ``last_turn_at`` value
the claim actually read, so a re-armed row is left ``pending`` and simply
re-distilled -- with the new turn included -- on a later cycle. See
``core/personalization/outbox.py``'s own module docstring for the full
reasoning.

What the distiller is and is not allowed to write
-------------------------------------------------
``config/prompts/memory/distill.md`` carries doc 05 section 5's hard rule
verbatim: an entry is admissible only if it derives from something the user
said or a choice the user confirmed, and text returned by
``research.web_research`` or any other external tool may never become one,
"verbatim or paraphrased" -- otherwise a poisoned search result becomes a
permanent instruction injected into every future turn. That rule is not
mechanically checkable from a stored entry's shape (see
``core/personalization/memory.py``'s own docstring), so the prompt is where
it lives, and ``test_the_distill_prompt_explicitly_forbids_tool_derived_
entries`` fails if that paragraph is ever deleted.

This module hardens the same rule structurally, as far as it can:
:func:`_render_transcript` renders TEXT message parts only. A ``table``,
``chart``, ``tool_event`` or ``artifact`` part is rendered as a bare
``[table]`` placeholder with its payload dropped entirely, so certified
query results and research payloads are never in the model's context to be
copied out of in the first place. The model still learns that a table was
shown -- which is genuine conversational context -- without the numbers in
it being available to paste into a permanent memory entry.

Provenance is authored HERE, never by the model (:func:`_stamp_entries`).
The distiller returns only ``type``/``statement``; this module attaches
``source_conversation_id`` (the row it actually claimed) and ``at`` (its own
clock). An entry the model echoes back unchanged from the existing document
keeps its ORIGINAL attribution rather than being re-stamped with this run's
conversation -- doc 05 section 5's "the distiller treats user-authored
entries as ground truth it must preserve verbatim", applied to provenance
as well as to text.

Accounting
----------
Each distillation is one ``turn_run`` row with ``kind='memory_update'``
(doc 06 section 1's own second kind, reserved by migration 0003 for exactly
this) plus one ``llm_calls`` row for the model call. Both matter:
``scripts/cost_rollup.py`` rolls spend up from ``llm_calls``, never from
``turn_run``, and its own docstring names ``kind='memory_update'`` as real
spend that must be counted -- so a worker that wrote only the parent row
would be invisible to every cost report. Neither write can break the job:
``RunLogWriter``'s never-raises contract already guarantees that, and this
module leans on it rather than re-implementing it.

Usage::

    DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \\
    S3_BUCKET=poseidon-artifacts LLM_MODE=stub \\
        python -m poseidon.scripts.memory_worker
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from poseidon.core.config import Settings, get_settings
from poseidon.core.db import build_engine
from poseidon.core.llm.bedrock import BedrockProvider
from poseidon.core.llm.prompts import (
    DEFAULT_PROMPTS_DIR,
    PromptRegistry,
    prompt_hash,
    prompt_version,
)
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.types import LLMResponse
from poseidon.core.obs import configure_json_logging, get_logger, new_trace_id, span, trace_id_var
from poseidon.core.personalization.memory import MemoryStore
from poseidon.core.personalization.outbox import OutboxStore
from poseidon.core.runlog import RunLogWriter
from poseidon.core.util.uuid7 import uuid7

logger = get_logger("memory_worker")

#: The prompt this worker renders, under ``poseidon/config/prompts/``.
DISTILL_PROMPT = "memory/distill"

#: The LLM role name resolved from ``models.yml`` (Claude Sonnet 4.5 by
#: default on both profiles; ``max_tokens: 2048``, ``temperature: 0.1``).
#: Pre-existing config -- this module adds no role and changes no model.
MEMORY_ROLE = "memory"

#: How many user-authored messages a conversation must carry before it is
#: worth spending a model call on.
#:
#: **A new threshold, not an inherited one.** Nothing in this codebase had
#: a "skip trivial conversations" rule before this task, so there was no
#: precedent to reuse and this number is a fresh, disclosed judgment call.
#: 2 is the smallest value that makes the rule mean anything. A one-user-
#: message conversation is, essentially by definition, a single question
#: ("what was GP for Maersk in April?") -- the ask and its answer, with no
#: second turn in which the user could refine, correct, or confirm
#: anything. Three of the four entry types (``preference``, ``correction``,
#: and any confirmed ``scope``) can only be evidenced by the user reacting
#: to what they got back, which takes a second turn; distilling the first
#: turn alone reliably yields either nothing or a restatement of the
#: question, at the cost of a Sonnet call, a ``turn_run`` row and a memory
#: version per one-shot question anyone ever asks. Deliberately not made
#: configurable: it is a property of what a conversation can possibly
#: contain, not an environment-specific tuning knob like
#: ``memory_idle_minutes``, and ``Settings`` gained exactly the two fields
#: this task's own scope sanctions.
MIN_USER_TURNS = 2

#: How many rows one cycle claims. Bounded so a large backlog is worked
#: through over several cycles rather than in one very long transaction-less
#: run that a restart would have to redo from the top; large enough that a
#: normal queue drains in a single cycle.
CLAIM_BATCH_SIZE = 20

#: How much of a conversation the distiller sees: the most recent N
#: messages (``UserHistory.get_messages``'s cursor-less page is already the
#: LATEST page -- see that method's own Phase 12 Task 4 amendment). A cap is
#: needed because a very long conversation would otherwise blow the model's
#: context; the most RECENT window is the right half to keep, since anything
#: durable the user said earlier has already had previous idle cycles to be
#: distilled into the existing entries this prompt also carries.
TRANSCRIPT_MESSAGE_LIMIT = 200

#: Seconds between cycles. Not a ``Settings`` field (this task's sanctioned
#: config scope is exactly ``memory_idle_minutes``/``memory_max_attempts``):
#: with a 30-minute idle threshold, polling granularity is irrelevant to
#: behavior anywhere below a few minutes, so there is nothing here an
#: operator would need to tune per environment.
POLL_SECONDS = 60

#: The one user-role message the distill call carries. Everything the model
#: reasons over -- the transcript, the existing entries, the rules -- is in
#: the rendered system prompt (the same shape ``core/llm/loop.py`` uses for
#: the router: state in ``system``, not smuggled into the message list).
#: Converse requires at least one message, so this exists to satisfy that
#: contract with a restatement of the output shape rather than an empty
#: block.
_DISTILL_REQUEST = "Return the updated memory document now, as a JSON array and nothing else."

#: A fenced code block the model may have wrapped its JSON in, despite
#: being told not to. Tolerated on READ (stripping it costs one regex and
#: turns an otherwise-perfect answer into a success) but still forbidden by
#: the prompt, so the model is never taught that fences are fine.
_FENCE_RE = re.compile(r"\A\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*\Z", re.DOTALL)

#: What ``_parse_entries`` keeps off each element the model returns. Any
#: other key -- including a ``source_conversation_id``/``at`` the model
#: invented -- is dropped on the floor: provenance is this module's to
#: author (see :func:`_stamp_entries`), never the model's to assert.
_MODEL_ENTRY_FIELDS = ("type", "statement")

# The claim query -- the ONE statement in this phase that runs outside a
# per-user rls_transaction (see the module docstring's privilege-model
# section). `FOR UPDATE SKIP LOCKED` is what makes two concurrent claims
# disjoint rather than duplicated-or-blocked: a row another transaction
# already locked is passed over silently instead of waited on, so one slow
# cycle can never stall another. Oldest-idle-first so a backlog drains in
# the order it accumulated. `make_interval(mins => ...)` rather than string-
# concatenating an interval literal: the threshold is a bound parameter like
# any other value, never SQL text.
_CLAIM_SQL = text(
    "SELECT conversation_id, user_sub, last_turn_at, attempts "
    "FROM memory_outbox "
    "WHERE status = 'pending' "
    "  AND last_turn_at < now() - make_interval(mins => :idle_minutes) "
    "ORDER BY last_turn_at ASC "
    "LIMIT :batch_limit "
    "FOR UPDATE SKIP LOCKED"
)


class DistillationError(ValueError):
    """The ``memory`` role answered, but not with the JSON array
    ``distill.md`` pins (unparseable text, a non-array, a non-object
    element, a missing ``statement``). A ``ValueError`` subclass, matching
    ``core/personalization/memory.py``'s own ``MemoryValidationError``/
    ``MemoryTooLarge`` precedent.

    Deliberately a FAILURE, not a silent "distilled to nothing": the model
    burned tokens and produced something this module could not use, which
    is exactly the condition ``attempts``/``memory_max_attempts`` exist to
    bound. Treating it as an empty answer instead would mark the row
    ``done`` and quietly lose the conversation forever.
    """


@dataclass(frozen=True)
class ClaimedConversation:
    """One row :func:`claim_idle_conversations` returned.

    ``last_turn_at`` is carried (not merely logged) because both status
    transitions are guarded on it -- see ``core/personalization/outbox.py``'s
    module docstring for why a claim that is not a lease needs that guard.
    """

    conversation_id: str
    user_sub: str
    last_turn_at: datetime
    attempts: int


def claim_idle_conversations(
    conn: Connection, *, idle_minutes: int, limit: int
) -> tuple[ClaimedConversation, ...]:
    """Every ``pending`` outbox row whose ``last_turn_at`` is older than
    ``idle_minutes``, oldest first, at most ``limit`` of them, locked
    ``FOR UPDATE SKIP LOCKED``.

    Takes an already-open :class:`~sqlalchemy.engine.Connection` rather
    than an ``Engine``, for two reasons that both matter. The row locks
    this statement takes live for the caller's transaction, so the caller
    -- not this function -- has to own its boundary; and a test proving
    ``SKIP LOCKED`` actually skips needs to hold one claim's transaction
    open while a second runs, which is only expressible if the transaction
    is the caller's.

    The connection MUST be the raw privileged one (``engine.begin()``, no
    ``rls_transaction``) -- see the module docstring's privilege-model
    section. Passing an identity-scoped connection is not an error this
    function can detect; it would simply return that one user's rows.
    """
    rows = conn.execute(
        _CLAIM_SQL, {"idle_minutes": idle_minutes, "batch_limit": limit}
    ).all()
    return tuple(
        ClaimedConversation(
            conversation_id=str(row.conversation_id),
            user_sub=row.user_sub,
            last_turn_at=row.last_turn_at,
            attempts=row.attempts,
        )
        for row in rows
    )


def _render_transcript(items: list[dict]) -> str:
    """The conversation as plain ``role: text`` blocks, TEXT parts only.

    Every non-text part (``table``, ``chart``, ``tool_event``, ``chips``,
    ``artifact``, anything a later phase adds) renders as a bare
    ``[<kind>]`` placeholder with its payload dropped entirely -- see the
    module docstring's "What the distiller is and is not allowed to write":
    the surest way for tool output never to become a memory entry is for it
    never to be in the model's context at all. The kind itself is kept
    because "a table was shown here" is real conversational context; the
    numbers inside it are not needed to decide what the USER wants
    remembered.
    """
    blocks = []
    for item in items:
        rendered = []
        for part in item.get("parts") or []:
            kind = part.get("kind")
            if kind == "text":
                markdown = (part.get("payload") or {}).get("markdown") or ""
                if markdown.strip():
                    rendered.append(markdown.strip())
            else:
                rendered.append(f"[{kind}]")
        if rendered:
            blocks.append(f"{item.get('role')}: " + "\n".join(rendered))
    return "\n\n".join(blocks)


def _render_existing_entries(entries: list[dict]) -> str:
    """The current memory document, in the SAME ``{type, statement}`` JSON
    shape the model must answer in -- so "return these unchanged, then
    append" is a copy, not a translation the model could get subtly wrong.
    Provenance fields are deliberately not shown: the model has no use for
    them and no authority over them (:func:`_stamp_entries` re-attaches the
    originals by matching on type+statement)."""
    if not entries:
        return "(none yet - this user has no memory document.)"
    return json.dumps(
        [{"type": entry["type"], "statement": entry["statement"]} for entry in entries],
        indent=2,
    )


def _parse_entries(answer: str) -> list[dict]:
    """The model's answer as a list of ``{"type", "statement"}`` dicts.

    Raises :class:`DistillationError` on anything that is not a JSON array
    of objects each carrying a non-blank ``statement`` and a ``type``. The
    ``type`` value itself is NOT validated here -- ``UserMemory.
    write_version``'s own ``_validate_entries`` owns the closed set
    (``preference``/``scope``/``fact``/``correction``) and is the single
    place that contract is enforced, exactly as its docstring intends; a
    second copy of that list here could only ever drift from it.
    """
    fenced = _FENCE_RE.match(answer)
    payload = fenced.group("body") if fenced else answer.strip()
    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise DistillationError(
            f"the memory role did not answer with JSON ({exc}); first 200 chars: "
            f"{payload[:200]!r}"
        ) from exc
    if not isinstance(parsed, list):
        raise DistillationError(
            f"the memory role answered with {type(parsed).__name__}, expected a JSON array"
        )
    entries = []
    for element in parsed:
        if not isinstance(element, dict):
            raise DistillationError(
                f"every memory entry must be an object, got {type(element).__name__}"
            )
        entry = {field: element.get(field) for field in _MODEL_ENTRY_FIELDS}
        if not entry["type"] or not str(entry["statement"] or "").strip():
            raise DistillationError(f"memory entry {element!r} is missing type or statement")
        entries.append({"type": entry["type"], "statement": str(entry["statement"]).strip()})
    return entries


def _stamp_entries(
    entries: list[dict], existing: list[dict], *, conversation_id: str, at: str
) -> list[dict]:
    """Attach provenance to each entry the model returned -- ``source_
    conversation_id`` and ``at`` -- here, never from the model's own answer
    (module docstring).

    An entry whose ``(type, statement)`` pair already exists in ``existing``
    keeps that entry's ORIGINAL provenance: the model echoing a preference
    the user stated three conversations ago has not re-earned it here, and
    re-stamping would silently rewrite the audit trail of where a user's own
    memory came from every single time it survived a distillation. Anything
    else is new, and is attributed to the conversation that actually
    produced it.
    """
    original = {
        (entry.get("type"), entry.get("statement")): entry
        for entry in existing
        if entry.get("source_conversation_id") and entry.get("at")
    }
    stamped = []
    for entry in entries:
        prior = original.get((entry["type"], entry["statement"]))
        stamped.append(
            {
                "type": entry["type"],
                "statement": entry["statement"],
                "source_conversation_id": (
                    prior["source_conversation_id"] if prior else conversation_id
                ),
                "at": prior["at"] if prior else at,
            }
        )
    return stamped


def _read_conversation(engine: Engine, settings: Settings, row: ClaimedConversation):
    """This conversation's most recent messages, through the OWNING user's
    own ``rls_transaction`` (never the privileged claim connection) --
    ``None`` when the conversation is not visible, which the
    ``ON DELETE CASCADE`` on ``memory_outbox.conversation_id`` makes
    unreachable in practice (a deleted conversation takes its outbox row
    with it) but which is cheap to handle honestly rather than index into
    blindly."""
    # Imported here, not at module scope: core/chat/history.py imports
    # core/chat/orchestrator.py, which pulls the whole skills/parsing/
    # ontology import graph behind it. Deferring it keeps `python -m
    # poseidon.scripts.memory_worker --help`-style startup (and this
    # module's own import in a test collection) off that path until a row
    # is actually being processed.
    from poseidon.core.chat.history import HistoryStore

    history = HistoryStore(engine, app_role=settings.database_app_role).for_user(row.user_sub)
    return history.get_messages(row.conversation_id, limit=TRANSCRIPT_MESSAGE_LIMIT)


def _distill(
    *,
    settings: Settings,
    role_client: RoleClient,
    registry: PromptRegistry,
    prompts_dir,
    transcript: str,
    existing: list[dict],
) -> tuple[LLMResponse, list[dict], str, str]:
    """One model call: render the prompt, invoke the ``memory`` role, parse
    the answer. Returns ``(response, entries, version, hash)`` -- the last
    two being the prompt's own provenance for the ``llm_calls`` row.

    Raises whatever the provider raised (a transport failure) or
    :class:`DistillationError` (an unusable answer); both are failures the
    caller records against ``attempts`` identically, since from the outbox
    row's point of view they are the same thing: this attempt produced no
    memory.
    """
    system = registry.render(
        DISTILL_PROMPT,
        transcript=transcript,
        existing_entries=_render_existing_entries(existing),
    )
    messages = [{"role": "user", "content": [{"text": _DISTILL_REQUEST}]}]
    response = role_client.invoke(MEMORY_ROLE, system=system, messages=messages)
    entries = _parse_entries(response.text)
    return (
        response,
        entries,
        prompt_version(prompts_dir, DISTILL_PROMPT),
        prompt_hash(system),
    )


def _process_one(
    engine: Engine,
    settings: Settings,
    role_client: RoleClient,
    registry: PromptRegistry,
    prompts_dir,
    row: ClaimedConversation,
) -> str:
    """Distill one claimed conversation end to end, and move its outbox row
    to its resulting state. Returns the outcome for logging: ``"done"``,
    ``"skipped"``, ``"failed"``, ``"rearmed"`` or ``"vanished"``.

    Never swallows an exception on its own account -- :func:`run_once`'s
    per-row guard is the one place that happens -- but DOES convert a failed
    distillation into a recorded attempt and a normal return, because
    "this attempt did not work" is an expected outcome of this function, not
    an error in it.
    """
    memory = MemoryStore(engine, app_role=settings.database_app_role).for_user(row.user_sub)
    outbox = OutboxStore(engine, app_role=settings.database_app_role).for_user(row.user_sub)
    writer = RunLogWriter(engine, app_role=settings.database_app_role)

    page = _read_conversation(engine, settings, row)
    if page is None:
        logger.warning(
            "memory_distill_conversation_missing", conversation_id=row.conversation_id
        )
        return "vanished"
    items, _next_cursor = page

    user_turns = sum(1 for item in items if item.get("role") == "user")
    if user_turns < MIN_USER_TURNS:
        # Marked `done`, never left `pending` (plan amendment a9316d3,
        # 2026-08-04, Carlos-directed -- this reverses the brief's original
        # "skipped without changing status" wording, on this task's own
        # reproduced evidence).
        #
        # `attempts` is still untouched: a trivial conversation has not
        # FAILED, so it must not burn a retry. But leaving the STATUS
        # `pending` starves the queue. A skipped row keeps its old
        # `last_turn_at`, so it stays permanently at the head of the
        # oldest-first claim; once CLAIM_BATCH_SIZE trivial conversations
        # sit there, no newer eligible conversation is ever reached again.
        # Reproduced live on the compose stack before this fix: the same
        # conversation id was skipped on 14 consecutive cycles while a
        # genuinely idle two-turn conversation was never claimed at all.
        # That is ordinary production state, not a test artifact -- every
        # user who asks one question and leaves creates exactly one such
        # row, permanently.
        #
        # Marking `done` loses nothing, which is the whole reason this is
        # safe: `ConversationOutbox.touch` fully re-arms a `done` row
        # (status back to `pending`, attempts back to 0, fresh
        # `last_turn_at`) on that conversation's very next turn. So a
        # trivial conversation that later becomes substantial is naturally
        # re-queued -- exactly the property leaving it `pending` was trying
        # to preserve -- without the starvation, and with no schema change
        # and no new status value.
        #
        # A re-armed row (the user came back between claim and here) is
        # left alone by `mark_done`'s own `last_turn_at` guard, so the turn
        # that just landed is not swallowed by this branch either.
        marked = outbox.mark_done(
            row.conversation_id, claimed_last_turn_at=row.last_turn_at
        )
        logger.info(
            "memory_distill_skipped_trivial",
            conversation_id=row.conversation_id,
            user_turns=user_turns,
            minimum=MIN_USER_TURNS,
            marked_done=marked,
        )
        return "skipped" if marked else "rearmed"

    current = memory.get_current()
    existing = current["entries"] if current is not None else []

    handle = writer.start_turn(
        user_sub=row.user_sub,
        conversation_id=row.conversation_id,
        client_turn_key=None,
        turn_index=None,
        # Never the conversation's text: doc 05 section 7 treats
        # turn_run.question as redactable user content, and this turn asked
        # no question of its own -- it consumed a whole conversation.
        question=None,
        mode=MEMORY_ROLE,
        parsed={},
        kind="memory_update",
        turn_run_id=str(uuid7()),
    )
    turn_run_id = handle.turn_run_id if handle is not None else None

    started = monotonic()
    response = None
    entries: list[dict] = []
    written = None
    # ONE guard around the model call AND everything downstream of it, not
    # around the call alone. `UserMemory.write_version` raises on its own
    # account -- MemoryTooLarge when the distilled document overflows
    # settings.memory_max_chars, MemoryValidationError when the model
    # returned a `type` outside the closed set -- and both are exactly the
    # "this attempt produced no memory" condition the attempts/max_attempts
    # ladder exists to bound. Guarding only the invoke would let either
    # escape to run_once's per-row net, which logs but records NO attempt:
    # the row would stay pending with attempts frozen and be re-claimed on
    # every poll forever, spending a model call each cycle on an answer that
    # can never land.
    try:
        with span(f"llm:{MEMORY_ROLE}", conversation_id=row.conversation_id):
            response, entries, version, digest = _distill(
                settings=settings,
                role_client=role_client,
                registry=registry,
                prompts_dir=prompts_dir,
                transcript=_render_transcript(items),
                existing=existing,
            )
        latency_ms = int((monotonic() - started) * 1000)
        # The call completed, so the tokens were spent -- logged before
        # anything downstream can go wrong with what it RETURNED (doc 06's
        # per-call accounting; see the module docstring's "Accounting").
        # `resolve` reports what models.yml CONFIGURES for this role, which
        # stays true in stub mode -- so the literal "stub" is substituted
        # for the provider, never for the model id, mirroring
        # orchestrator.py's own `_append_records` verbatim: doc 06 section 1
        # reserves that value for exactly this case.
        config = role_client.resolve(MEMORY_ROLE)
        if turn_run_id is not None:
            writer.append_llm_call(
                turn_run_id=turn_run_id,
                user_sub=row.user_sub,
                seq=1,
                provider=("stub" if settings.llm_mode == "stub" else config.provider),
                model_id=config.model,
                role=MEMORY_ROLE,
                prompt_version=version,
                prompt_hash=digest,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                latency_ms=latency_ms,
                status="ok",
            )
        if entries:
            written = memory.write_version(
                _stamp_entries(
                    entries,
                    existing,
                    conversation_id=row.conversation_id,
                    at=datetime.now(UTC).isoformat(),
                ),
                created_by="distiller",
            )
    except Exception as exc:  # noqa: BLE001 - recorded as an attempt, never raised on
        latency_ms = int((monotonic() - started) * 1000)
        error = {"type": type(exc).__name__, "message": str(exc)}
        if turn_run_id is not None:
            writer.finalize(
                turn_run_id=turn_run_id,
                user_sub=row.user_sub,
                status="error",
                message_id=None,
                answer_summary=None,
                input_tokens=response.input_tokens if response is not None else 0,
                output_tokens=response.output_tokens if response is not None else 0,
                latency_ms=latency_ms,
                error=error,
            )
        outcome = outbox.record_failure(
            row.conversation_id,
            claimed_last_turn_at=row.last_turn_at,
            error=error,
            max_attempts=settings.memory_max_attempts,
        )
        if outcome is None:
            logger.info("memory_distill_rearmed", conversation_id=row.conversation_id)
            return "rearmed"
        logger.error(
            "memory_distill_failed",
            conversation_id=row.conversation_id,
            status=outcome["status"],
            attempts=outcome["attempts"],
            max_attempts=settings.memory_max_attempts,
            **error,
        )
        return "failed"

    # An empty answer is a real, correct outcome ("nothing here is worth
    # remembering") -- it must not churn an identical new version, and it
    # must not leave the row pending to be re-distilled on every poll
    # forever. Both halves are why this marks done without writing.
    summary = (
        f"memory version {written['version']}: {len(entries)} entries"
        if written is not None
        else "no memory changes"
    )
    if turn_run_id is not None:
        writer.finalize(
            turn_run_id=turn_run_id,
            user_sub=row.user_sub,
            status="ok",
            message_id=None,
            answer_summary=summary,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=latency_ms,
        )

    if not outbox.mark_done(row.conversation_id, claimed_last_turn_at=row.last_turn_at):
        logger.info("memory_distill_rearmed", conversation_id=row.conversation_id)
        return "rearmed"
    logger.info(
        "memory_distill_done",
        conversation_id=row.conversation_id,
        entries=len(entries),
        version=written["version"] if written is not None else None,
    )
    return "done"


def run_once(engine: Engine, settings: Settings, role_client: RoleClient) -> int:
    """One full cycle: claim, then process each claimed row. Returns how
    many rows this cycle HANDLED.

    "Handled" counts every claimed row this cycle carried to an outcome --
    distilled, skipped as trivial, or recorded as a failed attempt -- not
    only the successes. That is the number the polling shell wants (it
    answers "was there anything to do", which is what distinguishes an idle
    poll from a busy one), and a definition where a failing row silently
    reported 0 would make a worker stuck in a retry loop look identical to
    an empty queue.

    **One bad row never takes the cycle down.** Each row is processed
    inside its own guard, so a failure that :func:`_process_one` did not
    already convert into a recorded attempt (an outbox write that itself
    failed, a store raising something unanticipated) costs that one row and
    nothing else. ``BaseException`` deliberately passes straight through:
    a ``KeyboardInterrupt``/``SystemExit`` is the operator stopping this
    process, and swallowing it to keep grinding through a batch would be
    exactly wrong -- see the module docstring's durability section for why
    doing nothing on the way out is safe.
    """
    with engine.begin() as conn:
        claimed = claim_idle_conversations(
            conn, idle_minutes=settings.memory_idle_minutes, limit=CLAIM_BATCH_SIZE
        )
    if not claimed:
        return 0

    prompts_dir = settings.prompts_dir if settings.prompts_dir is not None else DEFAULT_PROMPTS_DIR
    registry = PromptRegistry(prompts_dir)

    handled = 0
    for row in claimed:
        token = trace_id_var.set(new_trace_id())
        try:
            _process_one(engine, settings, role_client, registry, prompts_dir, row)
        except Exception as exc:  # noqa: BLE001 - one bad row must not end the cycle
            logger.error(
                "memory_distill_row_error",
                conversation_id=row.conversation_id,
                type=type(exc).__name__,
                message=str(exc),
            )
        finally:
            trace_id_var.reset(token)
        handled += 1
    return handled


class StubMemoryDistiller:
    """What answers the ``memory`` role when ``LLM_MODE=stub`` -- the same
    in-repo deterministic-fake seam ``core/chat/dev_router.py``'s
    ``DevDeterministicRouter`` fills for the router role.

    It always answers ``"[]"``: an empty memory document, which
    :func:`_process_one` treats as the honest "nothing here is worth
    remembering" outcome (row marked ``done``, no version written, no
    memory invented). That is the only defensible thing a fake distiller
    can say. ``DevDeterministicRouter`` can produce a genuinely useful
    canned answer because routing is a decision derivable from the state
    block it is handed; distillation is not -- a stub that fabricated
    plausible-looking preferences would write permanent, injected-every-turn
    instructions the user never gave, which is precisely the failure mode
    ``distill.md``'s own hard rule exists to prevent. Tests that need real
    entries register their own scripted provider instead (see
    ``tests/test_memory_worker.py``).
    """

    def invoke(
        self, *, system: str, messages: list[dict], tools: list[dict], model: str, params: dict
    ) -> LLMResponse:
        return LLMResponse(
            text="[]", tool_calls=(), stop_reason="end_turn", input_tokens=0, output_tokens=0
        )


def _build_role_client(settings: Settings) -> RoleClient:
    """Mirrors ``api/app.py``'s own ``RoleClient`` construction -- both
    providers registered, ``RoleClient.invoke`` reading exactly one per
    call per ``LLM_MODE`` -- with :class:`StubMemoryDistiller` standing in
    for ``DevDeterministicRouter``, since this process only ever invokes
    the ``memory`` role."""
    return RoleClient(
        settings, providers={"stub": StubMemoryDistiller(), "bedrock": BedrockProvider()}
    )


def main(argv: list[str] | None = None) -> int:
    """The polling shell: configure logging, build the process-wide engine
    and role client once, then :func:`run_once` forever.

    Deliberately thin and deliberately untested (see the module
    docstring): every decision this worker makes lives in
    :func:`run_once`. The only judgment here is that a cycle which raises
    is logged and slept off rather than allowed to kill the container --
    a database that is briefly unreachable is a condition the next poll
    should retry, not a crash loop for the orchestrator to restart through.
    ``KeyboardInterrupt``/``SystemExit`` still stop the process, since they
    are the operator asking it to.

    Takes ``argv`` it never reads, so the ``if __name__ == "__main__":
    raise SystemExit(main())`` shape every other script in this package
    uses applies here unchanged; this worker has no options to parse (its
    entire configuration is the environment contract ``Settings`` already
    validates).
    """
    configure_json_logging()
    settings = get_settings()
    engine = build_engine(settings.database_url)
    role_client = _build_role_client(settings)
    logger.info(
        "memory_worker_started",
        idle_minutes=settings.memory_idle_minutes,
        max_attempts=settings.memory_max_attempts,
        poll_seconds=POLL_SECONDS,
        llm_mode=settings.llm_mode,
    )
    while True:
        try:
            handled = run_once(engine, settings, role_client)
            logger.info("memory_worker_cycle", handled=handled)
        except Exception as exc:  # noqa: BLE001 - a bad cycle must not kill the worker
            logger.error(
                "memory_worker_cycle_error", type=type(exc).__name__, message=str(exc)
            )
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
