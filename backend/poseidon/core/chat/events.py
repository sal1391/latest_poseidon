"""The SSE envelope sink (doc 01 section 5; Phase 6 Task 3): translates P5's
agent-loop :class:`~poseidon.core.llm.loop.EventSink` protocol AND the
orchestrator's own direct part/token/lifecycle pushes into doc-01-section-5
SSE frames, byte-for-byte the same wire shape ``poseidon/api/mock_chat.py``'s
``_sse()`` already produces (``id: <event_seq>\\nevent: <name>\\ndata:
<json>\\n\\n``) -- the frontend's SSE parser is not this task's to touch, so
the wire format has to match exactly.

Two call surfaces, one instance
---------------------------------
:class:`SseEnvelopeSink` is handed to :func:`~poseidon.core.llm.loop.run_turn`
as its ``sink`` argument (satisfying the ``EventSink`` protocol's
``emit(kind, payload)`` method -- called synchronously, from inside
``run_turn``'s own loop, for ``llm_call``/``tool_start``/``tool_done``/
``turn_error``), AND called directly by
:func:`~poseidon.core.chat.orchestrator.execute_turn` itself for everything
``run_turn`` does not produce on its own: :meth:`accepted` (before dispatch
even starts), :meth:`push_part`/:meth:`push_token` (the clarify short-circuit
and the final-answer text), and :meth:`done` (the terminal, non-error
signal). One instance serves both roles because both need the SAME
``event_seq`` counter -- doc 01 section 5's contract is that ``event_seq`` is
monotonic across EVERY frame of the turn, not per-source.

Translation table (loop.py's ``EventSink.emit`` kinds -> doc 01 section 5
events):

- ``llm_call`` -> no frame at all (recorded by ``run_turn`` itself into the
  ``TurnResult.llm_records`` the orchestrator reads directly; nothing here
  needs re-deriving from a live event stream).
- ``tool_start`` -> one ``tool`` frame, ``status="start"``.
- ``tool_done`` -> one ``tool`` frame first (``status`` translated from
  loop.py's ``ok``/``error`` vocabulary to doc 01 section 5's
  ``start|done|error`` -- see "The one real translation" below), then one
  ``part`` frame per entry in the payload's ``parts`` list, then -- only
  when the payload's ``proof`` is non-empty -- one more ``part`` frame,
  ``kind="proof"``, ``payload={"lines": [...]}`` (the doc-01/doc-02 proof
  reconciliation the phase-6 plan adjudicates: skills keep ``proof`` as a
  FIELD on their result; the chat emitter is what turns it into a part).
- ``turn_error`` -> one ``error`` frame. ``code`` is the RFC-7807 problem's
  ``title`` (a stable, roughly-constant string per failure TYPE -- "llm
  provider error", "agent loop exceeded max iterations", "unknown skill" --
  the closest thing this loop's problem dicts have to a machine code);
  ``message`` is the problem's ``detail`` (the occurrence-specific text).
  No ``hint``: doc 01 section 5's own event table lists only
  ``{code, message}`` for the STREAM event (unlike the richer ``error``
  PART kind in doc 01 section 4, which is a client-side rendering concern,
  not this sink's).

The one real translation, and why it matters
-----------------------------------------------
loop.py's ``tool_done`` payload carries ``status`` in ``{"ok", "error"}``
(``_dispatch_one``'s own vocabulary). Doc 01 section 5's ``tool`` event
documents ITS ``status`` field as ``start|done|error`` -- ``"ok"`` is never
a valid value on the wire. Sending the payload's status through unchanged
would put a value the frontend's own type (``ToolEventPayload.status``) does
not declare onto the wire for every single successful dispatch. This sink
maps ``"ok"`` -> ``"done"`` and passes ``"error"`` through unchanged.

Human labels (doc 01 section 4's ``tool_event``/``tool`` ``label`` field)
-----------------------------------------------------------------------------
The interface this task's brief pins names three constructor keywords
(``turn_id``, ``message_id``, ``send``); this implementation adds a fourth,
required, keyword-only ``registry: SkillRegistry`` -- disclosed here and in
the task report as a necessary, minimal interface extension.

**Final-review wave amendment (item 3 / I4).** The label was originally
``SKILL_META['description']`` (``registry.get(skill_id).description``,
falling back to the bare skill id on ``KeyError``) -- correct as "a string
the registry can produce," but wrong as a UI label: that description is
model-facing prose meant for the router prompt (172 characters for
``data_qa.metric_query``), not something a human wants to read as a tool
step's name. It also duplicated a SECOND, independent derivation
``poseidon.api.live_chat`` already had for its ``GET /api/skills`` response
(the segment after a skill id's last dot, underscores as spaces, first
letter capitalized -- ``"data_qa.metric_query"`` -> ``"Metric query"``).
:func:`skill_label` below is that derivation, moved to this module as the
ONE shared home; ``live_chat.py`` now imports it instead of keeping its own
copy. Being a pure string transform over the id itself, it needs no
registry lookup and therefore never raises -- an id the registry has never
heard of (an unknown-skill dispatch, or a live model hallucinating a name)
gets the identical humanized treatment a real one does, which is why
``_label`` below no longer catches ``KeyError``. ``registry`` stays a
required constructor keyword regardless: it is still this task's pinned
interface shape, and removing it would ripple into every call site and test
in this suite for no behavioral gain.

Parts retained for pass-through (doc 02 section 5)
------------------------------------------------------
``tool_done``'s payload carries the dispatch's raw ``parts`` (the ONLY place
they ever appear -- ``ToolRecord.result_digest`` is a lossy string summary,
and ``TurnResult`` carries no parts of its own, doc 03's context-hygiene
split). The orchestrator's pass-through repopulation (a ranked/tabular
dimension-value table becomes ``ConversationSlots.pass_through`` for the
next turn) needs those raw rows AFTER ``run_turn`` has already returned, so
this sink retains them, keyed by ``tool_seq``, on the public
:attr:`tool_result_parts` attribute -- the one piece of state this sink
keeps beyond simply forwarding frames, and the only way the orchestrator can
reach data that otherwise only ever flowed to the wire.

Artifacts: reachable since Phase 8 Task 1
-------------------------------------------
The phase-6 plan adjudicated ``ArtifactRef -> part{kind:"artifact",
payload:{name,url,mime}}`` as part of the same proof/artifact reconciliation
above, and this sink implemented the conversion right away, reading
``payload.get("artifacts", ())`` defensively -- but loop.py's own
``_dispatch_one`` never forwarded ``SkillResult.artifacts`` into the
``tool_done`` payload at all (verified directly against that function's
source at the time: it emitted ``parts``/``proof``, not ``artifacts``), so
this conversion sat coded and synthetically tested but PRODUCTION-
UNREACHABLE -- a real, disclosed gap in P5's shipped code, ledgered through
P6 and P7 as not each of those tasks' to fix.

Phase 8 Task 1 closes it: ``_dispatch_one`` now adds ``"artifacts":
result.artifacts`` to the ``tool_done`` payload it emits (see loop.py's own
comment at that call site), so the conversion below runs for real the first
time any dispatched skill's :class:`~poseidon.core.skills.result.
SkillResult` carries a non-empty ``artifacts`` list --
``customer_insight.existing_customer_brief`` (Phase 8 Task 4) will be the
first. The synthetic test that constructed the payload shape loop.py could
not yet produce (``test_chat_orchestrator.py::
test_tool_done_defensively_converts_artifact_refs_when_present``) now has a
real-path sibling that drives the SAME conversion through an actual
dispatch -- see ``test_emit_seam_loop_events.py``. ``payload.get("artifacts",
())`` stays a ``.get`` with a default, not a bare index, on purpose even
though every real caller now supplies the key: a payload some OTHER, older
test in this suite still builds by hand (most of them predate this task and
have no reason to grow an ``"artifacts"`` key they never asserted on) must
keep working unchanged.

Incremental part streaming (Phase 8 Task 1)
-------------------------------------------
A subskill inside one dispatch can run for a while (Phase 8's briefs run
several phases -- fetch metrics, fetch ports, contextualize, research,
strategize, render a PDF -- behind a SINGLE tool call), and doc 02's
progressive-display promise is that the user sees each phase's part as soon
as that phase finishes, not all at once when the whole dispatch ends. The
seam for that is :meth:`SseEnvelopeSink.part_emitter`: given the dispatch's
own ``tool_seq``, it returns a callable a skill invokes directly (through
``SkillContext.emit_part``, wired to this by
``core/chat/orchestrator.py``) once per completed phase. That callable
pushes a ``part`` frame IMMEDIATELY -- mid-dispatch, before ``tool_done`` has
even fired -- and counts how many parts it streamed for that ``tool_seq``.

``_handle_tool_done`` still receives the dispatch's COMPLETE ``parts`` list
(a skill's own :class:`~poseidon.core.skills.result.SkillResult` carries
every part it produced, streamed early or not -- doc 06's run-log digest and
``events.py``'s own ``tool_result_parts`` cache both need the full list
regardless of how it was delivered to the wire). Re-emitting all of them
after already having pushed the early ones live would double every
progressively-streamed part on the wire, so ``_handle_tool_done`` looks up
how many parts ``part_emitter`` already streamed for THIS ``tool_seq`` and
slices them off: ``parts[n_streamed:]`` is emitted, never the full list --
the "late-only" parts a skill did NOT stream early. A ``tool_seq`` no one
ever called ``part_emitter`` for reads back a count of zero (the lookup
defaults to it), so ``parts[0:]`` is the full list -- BYTE-IDENTICAL to this
sink's pre-Phase-8 behavior of unconditionally looping over every part.
Today's callers (every skill before Phase 8 Task 4) never call
``ctx.emit_part`` at all, so this is not a hypothetical fallback, it is the
literal, exercised path every existing dispatch still takes.

Order stays pinned exactly as it was: the ``tool`` frame (translated
start/done status) first, then parts (now only the late-only ones), then
proof, then artifacts -- an emitter's early pushes land BETWEEN the
``tool_start`` frame and the eventual ``tool`` done frame, never after it,
which is what lets a frame-order assertion tell "streamed early" and
"emitted late" apart.

The per-``tool_seq`` count is reset, to zero, every time
:meth:`part_emitter` is called for that ``tool_seq`` -- calling it again for
an id already in progress starts that id's count over rather than adding to
it. Nothing in this codebase calls ``part_emitter`` twice for the same
``tool_seq`` today (``core/chat/orchestrator.py`` calls it once, building the
turn's ``SkillContext`` before any dispatch happens -- see that module for
the current, disclosed scope of WHICH ``tool_seq`` it binds), so this reset
is defensive rather than load-bearing, and documented so a future caller that
DOES rebind the same id mid-turn gets the obviously-correct behavior (a fresh
count) rather than a count that quietly kept accumulating across two
unrelated dispatches. The count is also POPPED, not merely read, inside
``_handle_tool_done`` -- once a ``tool_seq``'s ``tool_done`` has been
handled, that id is finished, and popping (rather than leaving a stale zero
behind) keeps this dict's size bounded by in-flight dispatches, never by the
turn's total dispatch count.

**Fix round 1 correction.** An earlier version of this section, and
``core/chat/orchestrator.py``'s own wiring comment, characterized a
hypothetical SECOND dispatch in one turn calling ``ctx.emit_part`` as a
safe no-op -- "parts simply arrive at that dispatch's own ``tool_done`` as
they always have." Reproduced and found FALSE. ``SkillContext`` is built
ONCE per turn, so ``ctx.emit_part`` is the SAME closure (the one
``part_emitter(1)`` returned) for EVERY dispatch of the turn, never a
fresh one per ``tool_seq`` -- a second dispatch's skill that also calls it
DOES push a real ``part`` frame to the wire (:meth:`push_part` runs first
and cannot be undone), and only then raises ``KeyError`` on
``self._streamed_counts[tool_seq] += 1`` -- the read half of that
increment -- because ``tool_seq`` 1's own key was already popped when
``tool_seq`` 1's ``tool_done`` fired, which always finishes before a
LATER dispatch's skill function even starts running (``run_turn`` drives
dispatches strictly sequentially). That ``KeyError`` propagates out of the
skill's ``run()``; ``SkillRegistry.dispatch``'s own try/except catches it
(its documented never-raises contract) and turns it into a structured
``SkillResult(ok=False, error=problem(500, "skill failure", "KeyError:
1"))`` -- so the second dispatch fails LOUDLY, with one already-streamed,
orphaned ``part`` frame on the wire that no later successful ``tool_done``
will ever explain, and -- if the model retries the identical skill call
(loop.py's own one-shot self-correction) -- a repeat of the same
deterministic ``KeyError``, ending the turn.

This is judged the better of the two available designs, not merely an
accepted defect: popping (this module's actual choice) fails loudly and
is caught as ordinary structured content the model can see and the run
log can record. The alternative -- never popping, just reading -- would
instead let the stale entry silently accept the second dispatch's part as
though it belonged to the first, mis-slicing (or duplicating) whatever
THAT dispatch's own parts turn out to be at ITS ``tool_done`` -- a silent
corruption strictly worse than a loud failure. The real constraint this
failure mode names: supporting more than one progressively-streaming
dispatch in a single turn needs ``ctx.emit_part`` REBOUND per dispatch
(``sink.part_emitter(current_tool_seq)`` called again before each one),
which only ``loop.py``'s own ``_dispatch_one`` is positioned to do --
deliberately out of this phase's sanctioned edit (the one
artifacts-forwarding line; see loop.py's own docstring). Not a gap this
sink can close on its own.

**Second correction (P8 whole-branch final-review wave, 2026-07-30, item
3 / I-1) -- NOT the "final-review wave item N" labels used elsewhere in
this codebase for earlier phases' own review rounds.** Fix round 1 above
already corrected "safe no-op" to "loud ``KeyError`` failure, judged the
better design" -- but its own framing still undersold the second
dispatch as close to theoretical ("if the model retries the identical
skill call... a repeat of the same deterministic ``KeyError``"). It is
not theoretical. It needs no multi-dispatch turn and no two different
skills: ANY routed dispatch of a progressively-streaming skill (a brief,
reached through ``dev_router.py``'s own brief branch -- "Run the brief
for Maersk" typed directly, not only D19's deterministic entry) that
fails its first attempt and gets retried by ``loop.py``'s own one-shot
self-correction reaches this exact shape on an entirely ordinary turn,
because ``tool_seq`` counts DISPATCHES, not successes (``_dispatch_one``'s
own call site: ``tool_seq=len(tool_records) + 1``) -- the retry is
tool_seq 2 while ``ctx.emit_part`` is still tool_seq 1's own closure.
Reproduced directly, not merely re-argued: ``test_emit_seam_loop_events.
py::test_part_emitter_on_a_self_correction_retry_does_not_orphan_or_
keyerror`` fails a fake skill's first attempt with a structured 422,
retries it exactly the way ``loop.py``'s own per-skill correction chance
allows, and (before this wave's own fix below) produces precisely the
orphaned frame and the ``KeyError``-turned-second-failure this section
already described in the abstract.

This wave's guard (:meth:`part_emitter`'s own ``_emit`` closure -- see
its docstring's identical second correction) resolves the reachable gap
the OTHER way from Fix round 1's own verdict: rather than continuing to
accept a loud failure as the lesser evil, a RETIRED ``tool_seq`` (one
whose ``tool_done`` already fired, so its key is gone from
``_streamed_counts``) now makes the stale closure a silent no-op instead
of pushing an orphan and raising. This is safe, not merely convenient,
precisely BECAUSE of the fact the loud-failure design above already
established: ``run_turn`` drives dispatches strictly sequentially, so a
stale closure is only ever called from a genuine retry of the SAME
logical call it was built for, and that retry's own ``SkillResult.parts``
still carries whatever it tried to stream early (a real skill's
contract, per "Incremental part streaming" above) -- its OWN
``tool_done`` then emits that part normally, since a ``tool_seq``
``part_emitter`` was never actually called FOR defaults its
streamed-early count to zero. The user sees the identical part either
way; only the orphan frame and the spurious second failure are removed.
This makes the VERY FIRST version of this docstring's claim -- the one
Fix round 1 corrected to "loud failure" -- true again, but for the right
reason: not because a second dispatch was always harmless, but because
this guard now makes it so.

Synchronous by construction
-------------------------------
Every method here is a plain, synchronous function -- required, since
``EventSink.emit`` is itself a synchronous Protocol method that
:func:`~poseidon.core.llm.loop.run_turn` calls with no ``await``, and
:func:`~poseidon.core.chat.orchestrator.execute_turn` (this sink's other
caller) is equally synchronous. ``send``'s pinned type,
``Callable[[str], Awaitable[None] | None]``, is deliberately liberal about
what a caller MAY hand in, but this sink never inspects or awaits whatever
``send`` returns -- it calls ``send(frame)`` and moves on. Bridging into an
async world (Task 4's "queue-bridged async send": a StreamingResponse's
async generator reading off a queue this synchronous ``send`` pushes onto,
e.g. via a thread-safe queue or ``loop.call_soon_threadsafe``) is entirely
the CALLER's responsibility to make appear synchronous from here -- this
module imports no ``asyncio`` and makes no assumption that an event loop is
even running in the thread that calls it, which is what keeps it trivially
testable offline with a plain list-appending ``send``.
"""

import json
from collections.abc import Awaitable, Callable

from poseidon.core.skills.registry import SkillRegistry

# doc 01 section 5's tool-event status vocabulary is start|done|error;
# loop.py's own tool_done payload status is ok|error (_dispatch_one's
# vocabulary) -- this is the one translation table this sink needs, since
# "ok" is never a valid value on the wire (see the module docstring's "The
# one real translation").
_TOOL_STATUS_ON_WIRE = {"ok": "done", "error": "error"}


def skill_label(skill_id: str) -> str:
    """Human-readable label derived from a skill id -- SKILL_META itself
    carries no such field (only ``description``/``examples``; see
    ``registry.py``'s own ``_register``), so this derives one the same way
    the frontend's own static skills list already names things: the bare
    skill name (the segment after the last dot -- the task prefix dropped),
    underscores as spaces, first letter capitalized
    (``"data_qa.metric_query"`` -> ``"metric_query"`` -> ``"Metric query"``).

    The shared home for this derivation (final-review wave item 3 / I4):
    previously duplicated between this module's own
    :meth:`SseEnvelopeSink._label` (which returned the SKILL_META
    description instead -- see this module's own "Human labels" section)
    and ``poseidon.api.live_chat``'s own module-level ``_label`` (this exact
    computation). ``live_chat.py`` now imports this function instead of
    defining its own copy. Pure string manipulation over the id itself --
    never raises, so an id no registry has ever heard of still gets a
    legible label.
    """
    name = skill_id.rpartition(".")[2]
    return name.replace("_", " ").capitalize()


class SseEnvelopeSink:
    """Implements the P5 :class:`~poseidon.core.llm.loop.EventSink` protocol
    (``emit``) AND the orchestrator's direct part/token/lifecycle pushes
    (``push_part``/``push_token``/``accepted``/``done``). See the module
    docstring for the full translation table and every disclosed judgment
    call.
    """

    def __init__(
        self,
        *,
        turn_id: str,
        message_id: str,
        send: Callable[[str], Awaitable[None] | None],
        registry: SkillRegistry,
    ) -> None:
        self._turn_id = turn_id
        self._message_id = message_id
        self._send = send
        self._registry = registry
        self._event_seq = 0
        # tool_seq -> that dispatch's raw parts list -- see the module
        # docstring's "Parts retained for pass-through".
        self.tool_result_parts: dict[int, list[dict]] = {}
        # tool_seq -> how many of that dispatch's parts `part_emitter`
        # already pushed live -- see the module docstring's "Incremental
        # part streaming". Private: nothing outside this class needs to read
        # it, unlike `tool_result_parts`, which the orchestrator reads back
        # after the turn.
        self._streamed_counts: dict[int, int] = {}

    @property
    def turn_id(self) -> str:
        return self._turn_id

    @property
    def message_id(self) -> str:
        """Read back by :func:`~poseidon.core.chat.orchestrator.execute_turn`
        for ``TurnOutcome.message_id`` and every ``RunLogWriter`` call that
        needs it -- this sink, not the orchestrator, is where the caller
        (Task 4's HTTP layer in production; a test directly in this suite)
        actually mints/supplies the id, so this is the one place downstream
        code can read it back from."""
        return self._message_id

    # -----------------------------------------------------------------
    # EventSink protocol -- called BY run_turn, synchronously, mid-loop
    # -----------------------------------------------------------------

    def emit(self, kind: str, payload: dict) -> None:
        """See the module docstring's translation table."""
        if kind == "llm_call":
            return
        if kind == "tool_start":
            self._send_tool_frame(payload["tool_seq"], payload["skill_id"], status="start")
            return
        if kind == "tool_done":
            self._handle_tool_done(payload)
            return
        if kind == "turn_error":
            problem = payload["problem"]
            self._frame("error", {"code": problem["title"], "message": problem["detail"]})
            return
        raise ValueError(f"SseEnvelopeSink.emit: unknown event kind {kind!r}")

    def _handle_tool_done(self, payload: dict) -> None:
        tool_seq = payload["tool_seq"]
        self.tool_result_parts[tool_seq] = payload["parts"]
        wire_status = _TOOL_STATUS_ON_WIRE[payload["status"]]
        self._send_tool_frame(tool_seq, payload["skill_id"], status=wire_status)
        # Only the parts `part_emitter` did NOT already stream live for this
        # dispatch -- see the module docstring's "Incremental part
        # streaming". `.pop(..., 0)` both reads and retires the count: a
        # tool_seq nobody streamed early (every dispatch before Phase 8 Task
        # 4, and every tool_seq no caller ever wired an emitter for) defaults
        # to zero, so `parts[0:]` is the full list -- byte-identical to this
        # method's pre-Phase-8 unconditional loop over every part.
        # Popping here is also what makes a LATER call to the same tool_seq-1
        # closure (a hypothetical second dispatch reusing today's one
        # per-turn `ctx.emit_part`) fail loudly with KeyError instead of
        # silently corrupting a finished dispatch's own count -- see the
        # module docstring's own Fix round 1 correction for the full,
        # reproduced failure mode and why that trade is the right one.
        n_streamed = self._streamed_counts.pop(tool_seq, 0)
        for part in payload["parts"][n_streamed:]:
            self.push_part(part["kind"], part["payload"])
        if payload["proof"]:
            self.push_part("proof", {"lines": list(payload["proof"])})
        # Reachable since Phase 8 Task 1 -- see the module docstring's
        # "Artifacts: reachable since Phase 8 Task 1". `.get` with a default
        # rather than a bare index: older tests in this suite build
        # `tool_done` payloads by hand with no `"artifacts"` key at all and
        # must keep working unchanged.
        for artifact in payload.get("artifacts", ()):
            self.push_part(
                "artifact",
                {"name": artifact.name, "url": artifact.url, "mime": artifact.mime},
            )

    def part_emitter(self, tool_seq: int) -> Callable[[dict], None]:
        """A callable ``ctx.emit_part`` can be wired to for ONE dispatch:
        invoking it pushes ``part`` immediately (as a live ``part`` frame,
        through :meth:`push_part`) and counts it against ``tool_seq``, so
        ``_handle_tool_done`` knows how many of that dispatch's eventual
        parts were already streamed and must not re-emit them -- see the
        module docstring's "Incremental part streaming" for the full
        protocol and why double emission is impossible by construction
        (a count-based skip, not a de-duplication check on part CONTENT).

        Resets ``tool_seq``'s count to zero on every call, including a
        second call for an id already in progress -- see the module
        docstring's "per-``tool_seq`` count is reset" paragraph for why that
        is documented, defensive behavior rather than a load-bearing one
        today.

        **Fix round 1 correction:** calling the returned callable for a
        ``tool_seq`` whose own ``tool_done`` has ALREADY fired -- the shape
        a hypothetical second progressively-streaming dispatch in one turn
        would take, reusing today's single per-turn ``ctx.emit_part`` --
        raises ``KeyError`` rather than quietly doing nothing. Deliberately
        loud, not a silent fallback: see the module docstring's own Fix
        round 1 correction (under "Incremental part streaming") for the
        reproduced failure mode and why that is the better of the two
        available designs.

        **Second correction (P8 whole-branch final-review wave, 2026-07-30,
        item 3 / I-1):** "hypothetical" above was wrong -- this shape is
        reachable on an entirely ordinary turn, via ``loop.py``'s own
        one-shot self-correction retrying a routed brief dispatch that
        failed its first attempt (``tool_seq`` counts DISPATCHES, not
        successes, so the retry lands on ``tool_seq`` 2 while this same
        closure, built for ``tool_seq`` 1, never gets rebound). Reproduced
        directly (see the module docstring's own second correction for the
        pinning test). The verdict changes too: calling this closure for a
        retired ``tool_seq`` now returns silently, before ``push_part``,
        instead of raising -- see :meth:`part_emitter`'s own ``_emit``
        implementation just below for the guard and why a stale closure can
        only ever be reached by a genuine retry of the call it was built
        for, never a second, unrelated dispatch's own fresh part.
        """
        self._streamed_counts[tool_seq] = 0

        def _emit(part: dict) -> None:
            # P8 final-review wave (2026-07-30), item 3 / I-1 -- the
            # retired-dispatch guard, reviewer-verified by patching and
            # re-running. A ``tool_seq`` no longer in ``_streamed_counts``
            # means ITS OWN ``tool_done`` already fired (the dict entry was
            # popped there -- see ``_handle_tool_done``), so this closure is
            # STALE: this is the self-correction-retry shape (see this
            # module's own "Fix round 1 correction" and its second
            # correction just below), not a fresh dispatch this closure was
            # ever rebound for. Returning here, before ``push_part``, is
            # what makes the retry's own stream call a silent no-op instead
            # of an orphaned wire frame followed by a ``KeyError`` -- the
            # part the skill wanted to stream early still reaches the user
            # exactly once, carried in its own ``SkillResult.parts`` and
            # emitted normally by the retry's OWN ``tool_done`` below (that
            # tool_seq's own count was never incremented, so it defaults to
            # zero streamed-early and emits its full parts list, same as
            # any dispatch that never called ``part_emitter`` at all).
            if tool_seq not in self._streamed_counts:
                return
            self.push_part(part["kind"], part["payload"])
            self._streamed_counts[tool_seq] += 1

        return _emit

    def _send_tool_frame(self, tool_seq: int, skill_id: str, *, status: str) -> None:
        self._frame(
            "tool",
            {
                "tool_seq": tool_seq,
                "tool": skill_id,
                # No MCP server / adapter for an in-process skill dispatch
                # today (doc 06's own `tool_calls.server` is nullable for
                # exactly this reason) -- mirrors the run-log writer's own
                # `server=None` for the same dispatch.
                "server": None,
                "status": status,
                "label": self._label(skill_id),
            },
        )

    def _label(self, skill_id: str) -> str:
        """The tool step line's human label -- see the module docstring's
        "Human labels" and the module-level :func:`skill_label` this
        delegates to."""
        return skill_label(skill_id)

    # -----------------------------------------------------------------
    # Direct pushes -- called BY execute_turn, never by run_turn
    # -----------------------------------------------------------------

    def push_part(self, kind: str, payload: dict) -> None:
        self._frame("part", {"kind": kind, "payload": payload})

    def push_token(self, text: str) -> None:
        self._frame("token", {"text": text})

    def accepted(self, turn_index: int) -> None:
        self._frame("accepted", {"turn_index": turn_index})

    def done(self, usage: dict) -> None:
        self._frame("done", {"usage": usage})

    # -----------------------------------------------------------------
    # Wire format -- byte-for-byte mock_chat.py's own `_sse()`
    # -----------------------------------------------------------------

    def _frame(self, name: str, fields: dict) -> None:
        self._event_seq += 1
        data = {
            "turn_id": self._turn_id,
            "message_id": self._message_id,
            "event_seq": self._event_seq,
            **fields,
        }
        frame = f"id: {self._event_seq}\nevent: {name}\ndata: {json.dumps(data)}\n\n"
        self._send(frame)


__all__ = ["SseEnvelopeSink", "skill_label"]
