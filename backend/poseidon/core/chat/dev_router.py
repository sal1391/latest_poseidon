"""The deterministic dev router (doc 03 section 1's stub seam; Phase 6): an
:class:`~poseidon.core.llm.roles.LLMProvider` that answers real, certified
questions without calling a model, so the offline dev chat (no AWS
credentials, ``LLM_MODE=stub``) can demo more than a canned script.

Contract (the phase-6 plan's Global Constraints bullet on this class): it
reads ONLY its ``invoke`` keyword arguments -- ``system``, ``messages``,
``tools``, ``model``, ``params`` -- and nothing else. It does not import the
orchestrator or any chat-wiring module (Task 3/4 import THIS, never the
reverse), holds no state across calls (no ``__init__``, nothing written to
``self``), and never reaches past the seam a real
:class:`~poseidon.core.llm.roles.LLMProvider` is handed: everything it
"knows" about the conversation has to already be textually present in
``system`` or ``messages`` -- the exact same information a real model would
have to work with. ``tools``/``model``/``params`` are accepted (the
Protocol is keyword-only and fixed-shape -- see ``roles.py``) but never
consulted; this router's entire decision surface is the rendered state
block inside ``system`` plus the message history, per the behavior table
below.

Parse source: :func:`poseidon.core.llm.prompts.render_state_block`'s pinned
line formats -- read that function's docstring and ``test_llm_prompts.py``'s
goldens before touching the regexes below; they target those exact, tested
formats, not a guess at them. The conversation-state section always lands
as the LAST section of ``system`` (doc 03 section 3's fixed assembly order;
``assemble_system``'s own ``_HEADER_STATE``), under the literal header
``"=== CONVERSATION STATE ==="`` -- a documented, tested public contract
(``test_assemble_system_all_four_sections_present_in_order`` and friends in
``test_llm_prompts.py``), so hardcoding a second copy of that exact string
below is pinning a contract, not guessing at another module's private
implementation detail.

The hints gap (disclosed; see the task report for the full writeup). The
phase-6 plan's behavior table gates the tool-call case on "hints lead with
data_qa.metric_query AND a resolved period". ``ParsedTurn.hints`` (the
skill hinter's ranked shortlist, doc 02 section 5) is never rendered into
ANY part of ``render_state_block``'s output: verified directly against
``prompts.py``'s ``_render_parsed``, which reads ``parsed.customer``,
``.port``, ``.period_a``, ``.period_b`` and ``.issues`` and never touches
``.hints`` at all, and confirmed by ``test_render_state_block_parsed_turn_
with_issues_golden`` in ``test_llm_prompts.py`` -- its ``ParsedTurn``
fixture sets ``hints=()`` while every OTHER field is populated, and the
pinned golden output carries no hint line, or any trace of hints,
anywhere. So there is no textual channel carrying hints into ``system``,
and this class's own contract (above) forbids reaching for one that
bypasses ``system``/``messages`` -- importing ``skill_hinter`` directly and
re-running it here would be exactly the invented side channel that
contract rules out.

The gate actually implemented below is "a period is resolved", alone. That
is not a weaker stand-in for the missing half of the condition -- it is
independently necessary regardless of hints: ``data_qa.metric_query``'s own
``Args.period`` is a REQUIRED field with no default (see
``metric_query/schema.py``), so no valid tool call could exist without one
either way, and hints have no channel into ``system`` today regardless (see
above).

**Fix round 1 correction (Important I1).** An earlier draft of this
docstring additionally claimed the dropped hints condition was "close to
the same signal" because ``data_qa.metric_query`` was "the only enabled,
hintable skill" in today's registry. **That claim is FALSE**, with
reproduced counterexamples, and has been struck. The error was conflating
"the only DISPATCHABLE skill" (``SkillRegistry`` state -- ``customer_
insight`` is ``enabled: false``, ``research.web_research`` has no task
directory yet) with "the only skill the HINTER can score" (``lexicon.py``
state) -- two different things. ``lexicon.py``'s own module docstring
already says the hinter is "advisory lexicon data, decoupled from what is
registered TODAY," and scores ``research.web_research``/``customer_
insight.*`` ids regardless of registry state; a research- or brief-shaped
message is scored against those ids whether or not anything would actually
dispatch them. Reproduced directly against the real hinter:
``hint("Any market news on our competitor in April 2026",
ConversationSlots())`` returns only ``(CandidateSkill('research.
web_research', 3.0),)`` -- no ``data_qa.metric_query`` candidate anywhere
-- and ``hint("Give me an overview of Maersk in April 2026", ...)`` leads
with ``customer_insight.existing_customer_brief``/``new_prospect_brief``,
again with no metric_query candidate. Both messages also resolve "April
2026" as a real period (independently reconfirmed via ``period_parser.
parse_periods`` -- period resolution has no concept of what the period is
"about").

**Known, disclosed over-trigger.** Because of the above, the period-alone
gate WILL mis-fire on this counterexample class: a research- or
brief-shaped question that also names a period gets a ``data_qa.
metric_query`` tool call from this router, and two invokes later, a
confident ``"Certified answer for..."`` label on a question it never
actually answered. There is no code-level mitigation for this today; it is
a known, accepted limitation of the dev/demo router, not a silent one.
``test_tool_call_is_identical_regardless_of_hints_value`` in the test suite
pins TODAY's actual behavior -- an otherwise-identical state block with
``hints=()`` and with a non-empty, metric-query-led ``hints`` both produce
the byte-identical tool call -- which is a symptom of this gap, not a
guarantee that should hold forever (see "Planned endgame" below).

**Architecture-wide, not dev-router-specific (Important I2).**
``render_state_block`` is SHARED infrastructure: ``loop.py``'s own
``_router_system`` calls ``render_state_block(context.state, parsed)`` to
build the REAL router's system prompt too (see ``loop.py``'s module
docstring and ``_router_system``). So no router -- the live Bedrock router
included -- can see ``ParsedTurn.hints`` today. This is a gap in the shared
prompt-assembly layer, not a shortcut this fake alone takes.

**Planned endgame.** Task 3 is sanctioned to extend ``render_state_block``
with an additive hints line (never reshaping the existing pinned lines --
the same "additive growth only" convention :class:`~poseidon.core.skills.
context.ConversationSlots`'s own docstring already established for slot
growth). Once that lands, this router's gate should upgrade to the plan's
literal condition -- "hints lead with data_qa.metric_query AND a resolved
period" -- which closes the over-trigger named above, and
``test_tool_call_is_identical_regardless_of_hints_value`` should flip from
proving invariance to proving the router RESPECTS hints (a research-led
hints tuple should then produce the capability message, or no tool call,
rather than a metric_query dispatch).

**Task 3 CLOSURE.** The planned endgame above has landed: ``render_state_
block`` now renders an additive ``"Skill hints: <id> (<score>), ..."`` line
(best-first, one line, only when ``parsed.hints`` is non-empty -- see that
function's own docstring's "Skill hints" section) directly beneath the
``Period:``/``Compare period:`` lines this router already parses. This is
architecture-wide, not a dev-router-only fix (Important I2 above): the SAME
render call builds the real, live-model router's system prompt
(``loop.py``'s ``_router_system``), so a real model gains this signal too,
the moment it lands, with no other change.

The gate below is upgraded to ``_parse_state``'s new ``hints_permit_
dispatch`` field, computed as: **no "Skill hints:" line at all -> permit
(``True``); a line present and its FIRST (best-scored) entry is
``data_qa.metric_query`` -> permit; a line present and its first entry is
anything else -> refuse (``False``).** This is deliberately NOT the plan's
literal condition read as a bare conjunction ("hints lead with metric_query
AND a period is resolved") -- a STRICT reading would refuse dispatch on
every ParsedTurn that produced no hints whatsoever (an empty shortlist is
"advisory, never a hard dispatch" per ``skill_hinter.hint``'s own docstring,
not a vote against dispatching), which would also silently break every
existing case-(b) test in this suite that never populates ``hints`` -- a
regression the plan's own author could not have intended, since this
task's OWN instructions name only ONE test as needing a flip, not the other
ten. The permissive-when-absent reading is the one that actually CLOSES the
disclosed over-trigger (a hints line that ACTIVELY leads toward research/
brief content now refuses to dispatch metric_query) while changing nothing
for a turn the hinter has no opinion about -- which is every turn in this
file's OWN test fixtures unless a test opts into an explicit ``hints=``
value. Pinned by ``test_hints_gate_permits_empty_or_metric_leading_but_
refuses_research_leading`` (formerly ``test_tool_call_is_identical_
regardless_of_hints_value`` -- flipped per this task's instructions, not
deleted): metric-leading and empty hints both still dispatch identically to
before; a research-leading hints line now produces the capability message
instead -- UNLESS Task 4 (below) also finds a subject to research, in which
case it now dispatches research instead of merely refusing metric_query.

**Task 4 CLOSURE (research pivot; Phase 7).** Task 3's closure (above) only
ever taught this router to RESPECT a research-leading hints line by
REFUSING metric_query -- the turn still fell through to the capability
message either way, since nothing yet dispatched research.web_research
itself. Task 4 closes that other half, additively: case (b)'s own gate and
``_metric_query_call`` are UNTOUCHED; this is a new, independent branch
checked only once (b) has already declined to fire, so the two skills never
compete for the same turn. The rule: a "Skill hints:" line present AND
leading with ``research.web_research`` (:attr:`_ParsedState.hints_lead_
research`, computed by the identical ``_HINTS_RE`` match ``hints_permit_
dispatch`` already reads, just checked against a different target id) AND a
customer or port is KNOWN -- resolved THIS turn (``Resolved customer:``/
``Resolved port:``) OR still carried from before (``Carried customer:``/
``Carried port:``) -- together produce ONE ``tool_use`` for ``research.
web_research`` (:func:`_research_call`): ``question`` is the last
user-authored text (:func:`_last_user_text`, the SAME helper case (b)'s own
"top" detection already uses), and ``customer``/``port`` are each
independently attached from whichever of resolved-this-turn or carried is
present (a disclosed judgment call: "resolved-or-carried" is read as
governing the ARGS the same way it governs the GATE -- per FIELD, not as an
all-resolved-or-all-carried group choice -- see the task report for the
full reasoning). This is deliberately MORE permissive than case (b)'s own
filter-building, which reads ONLY resolved-this-turn lines on purpose (see
case (b)'s own paragraph below) -- research's gate NAMES carry explicitly
("resolved-or-carried"), metric_query's does not, so the two are allowed to
differ.

A second invoke closes a research turn the SAME way case (c) closes a
metric one, but keyed on WHICH skill actually ran rather than "any
toolResult exists": :func:`_last_tool_use_name` reads the ``name`` off the
MOST RECENTLY appended ``toolUse`` block anywhere in ``messages`` (the
model's own echoed tool call -- ``loop.py``'s ``_assistant_tool_use_
message``), and when that name is ``research.web_research``, this router
ends the turn with ``f"Research summary for {subject} -- {n} sources."``
(:func:`_research_summary`) -- checked BEFORE case (c) itself, and
regardless of whether a period also happens to be resolved the same turn
(case (c)'s own condition, ``_has_tool_result(messages) and period is not
None``, would otherwise ALSO be satisfied and produce the wrong text; see
``test_research_tool_result_present_takes_priority_even_when_a_period_is_
also_resolved``). ``{subject}`` reuses the exact same resolved-or-carried
customer/port precedence the tool call itself used -- correct because
``system`` is built ONCE per turn (``loop.py``'s own module docstring) and
handed unchanged to every iteration's ``invoke()`` call, so both
computations read the identical state block. ``{n}`` is read off the
toolResult's own digest text (``loop.py``'s ``tool_result_digest``; this
skill's own proof line, ``tools/format_parts.py``'s ``"Results: {n}"``) via
the same anchored-regex discipline (:data:`_RESULTS_COUNT_RE`) every other
line this module parses already uses; a missing/malformed digest defaults
to ``0`` rather than raising, matching this router's own total-function
discipline for every other off-contract shape ``invoke()`` is exercised
against directly.

Hints NOT leading research (Task 4) or a brief skill (Task 5) change
NOTHING here. Case (b)'s own gate already refused metric_query dispatch the
moment hints lead ANYTHING other than ``data_qa.metric_query`` (Task 3
CLOSURE) -- research and the two brief skills included -- so before Task 4,
every one of those refusals fell straight through to the capability
message. Task 4 intercepted exactly ONE of them (a hints line leading
research.web_research, WITH a subject to attach) with a real dispatch; Task
5 (below) intercepts two more (a hints line leading a brief skill, WITH its
own required signal). Every OTHER refusal -- research with no customer/port
known at all, or a brief skill with no resolved customer / no brief word in
the text -- still falls through to the byte-identical capability message it
always did (pinned by ``test_hints_leading_research_without_any_subject_
falls_back_to_capability_message`` and the Task 5 CLOSURE's own "does not
dispatch" tests below).

**Task 5 CLOSURE (bubble-entry brief routing; Phase 8).** Task 4's own
closure taught this router to RESPECT a research-leading hints line; this
task teaches it to respect a BRIEF-leading one too, additively, on the
identical "checked only once (b)/(b2) have already declined" principle --
metric_query, research and the two brief skills never compete for the same
turn, since a hints line has exactly one best score.

The rule: a "Skill hints:" line present AND ``customer_insight.
existing_customer_brief`` TIES the line's best score (:func:`_hints_lead`,
below) AND a customer is resolved THIS TURN (``Resolved customer:`` --
deliberately NOT resolved-or-carried, unlike research's own gate; see
"Stricter than research's carry rule" below) AND the message text itself
names a brief/report/profile/overview word (:func:`_mentions_brief_word`;
see "The brief-word guard" below) -> ONE ``tool_use`` for ``customer_
insight.existing_customer_brief``, ``arguments={"customer": <resolved
value>}`` (:func:`_existing_brief_call`). The prospect twin
(:func:`_prospect_brief_call`) fires the identical shape for ``customer_
insight.new_prospect_brief`` when hints instead tie-lead THAT id and the
brief-word guard passes -- no customer/port gate at all, since a prospect
has nothing to resolve (D19's own "text = subject" rule, mirrored here):
``arguments={"prospect_name": <the whole last user message>}``, the same
crude, no-extraction precedent :func:`_research_call`'s own ``question``
field already established. A second invoke closes either branch the same
way case (c2) closes research's: :func:`_last_tool_use_name` names the
brief skill that actually ran -> ``f"Brief complete for {subject}."``
(:func:`_brief_summary`), checked alongside (c2) and BEFORE (c), for the
identical "a period ALSO resolved must not override the real close" reason.
``subject`` is ``parsed_state.customer`` for the existing branch (the same
system-built-once reasoning research's own close relies on) but the ECHOED
toolUse's own ``input["prospect_name"]`` for the prospect branch
(:func:`_last_tool_use_input`) -- there is no state-block line a prospect's
name could ever appear on.

"Leads" redefined -- tie-inclusive, retroactively, for EVERY gate in this
module (:func:`_hints_lead`, replacing the bare ``hints_match.group(
"first_skill") == ...`` comparison every gate used through Task 4). A real,
reproduced gap, not a hypothetical one: the moment ``ConversationSlots.
mode`` can actually BE ``"existing_customer"``/``"new_prospect"`` (Task 5's
own D19 entry is the FIRST code that ever writes either value), an
ORDINARY single-lexeme pivot question -- "any relevant news on Meridian
Global Shipping?" after a completed prospect brief, mode still carried per
this plan's own "mode stays in slots (advisory)" rule -- scores its own
skill 1.0 (the "news" lexeme) and the carried-mode's brief skill ALSO 1.0
(``lexicon.MODE_HINTS``'s own bonus), and ``skill_hinter.hint``'s
alphabetical tie-break (``"customer_insight..."`` sorts before
``"data_qa..."``/``"research..."``) would make EVERY strict-first-entry
gate in this module -- metric_query's included -- silently favor the
carried-mode's brief skill over the turn's own real intent. Reproduced
directly: ``hint("any relevant news on Meridian Global Shipping?",
ConversationSlots(mode="new_prospect"))`` returns ``(CandidateSkill(
'customer_insight.new_prospect_brief', 1.0), CandidateSkill(
'research.web_research', 1.0))`` -- research is NOT textually first, yet
the doc 08 P8 self-review's own "post-brief pivots" promise (and this
plan's own Global Constraints: "subsequent turns route normally") requires
this exact pivot to still dispatch research. :func:`_hints_lead` closes
this for every gate uniformly: "leads" now means "is present in the hints
line AND its score equals the line's maximum", which is PROVABLY identical
to the old "textually first" reading whenever there is no tie (the
hinter's own line is already best-first, so the first entry already IS an
entry achieving the max score) and therefore changes NOTHING for any
turn this suite pinned before Task 5 -- verified: no PRE-Task-5 test in
this file constructs a hints tuple with two equal-score entries, since
nothing before Task 5 could ever make ``ConversationSlots.mode`` produce
one outside a hand-built test fixture. Widened symmetrically rather than
only for research (the minimal fix THIS one reproduced case needs) because
the identical mechanism threatens metric_query's own gate the same way (a
"what is the volume for this customer" pivot after an existing-customer
brief ties 1.0/1.0 the same way) -- a principled, uniform reading of "leads"
is more defensible than an ad hoc asymmetry between the four gates this
router now has.

The brief-word guard (:func:`_mentions_brief_word`, a NEW, independent
lexical check on :func:`_last_user_text` -- the same "borrow the hinter's
matching DISCIPLINE, never its import" precedent :data:`_TOP_WORD_RE`
already set for the single lexeme case (b) reads) is what keeps the
tie-inclusive widening from over-correcting: a brief skill LEADING or
TYING the hints line is, by itself, not enough to justify placing an
actual brief dispatch (research calls, a stored PDF -- a heavier, more
side-effecting action than a metric or research call) -- the turn's own
text has to actually ask for one. Pinned directly: hints tie-leading
``customer_insight.new_prospect_brief`` 1.0/1.0 with ``research.
web_research`` on "any relevant news on Meridian Global Shipping?" (the
exact reproduced scenario above) correctly falls through PAST the
brief-word-gated (b3) check to (b2)'s own tie-inclusive research gate,
which fires -- proving the widening fixes the pivot without ever placing a
spurious brief dispatch. Word list matches ``lexicon.KEYWORDS``' own four
generic "-- brief words" entries (brief/report/profile/overview) exactly,
cross-checked by a dedicated test rather than imported (this router imports
no lexicon table -- see the module docstring's opening contract).

Stricter than research's carry rule (disclosed judgment call). Research's
own case (b2) reads "resolved-or-carried" (Task 4 CLOSURE, above); the
existing-brief branch reads ONLY "resolved THIS turn" -- deliberately, not
an oversight. A full brief is a heavier action than a read-only query, and
"Run the brief for Maersk" (the plan's own illustrative routed-path
example) always names its own subject in the SAME message anyway, so
requiring a fresh resolution costs nothing for the realistic case while
avoiding a brief firing three turns after a customer was last mentioned,
purely because it is still sitting in ``ConversationSlots.customer``.

DISTINCT FROM D19 (the orchestrator's own deterministic entry, ``core/chat/
orchestrator.py``). This router's brief branch is the ROUTED path: a
default-mode (or any-mode) turn that happens to say something like "Run
the brief for Maersk" gets there through the ordinary ``run_turn`` loop, a
real (fake, in stub mode) LLM decision, going through THIS class same as
any other dispatch. D19 is the DETERMINISTIC path: a pinned flow-chip
phrase sets ``slots.mode`` and prompts for a subject; the FOLLOWING
message, once resolved (or taken as-is for a prospect), dispatches the
brief WITHOUT ever calling ``run_turn``/this router at all -- see
``orchestrator.py``'s own module docstring, "D19 entry orchestration", for
the full contrast. The two paths reach the identical two skills through
entirely different code, on purpose: D19 never risks a live model's
judgment call for its own bubble-entry flow (zero llm_calls in stub mode,
by construction), while this router's branch is what lets an ORDINARY chat
message ALSO reach a brief, the same way any other skill is reachable
through ordinary conversation.

Behavior table (the phase-6 plan; case (a), ambiguous turns, is the
orchestrator's short-circuit -- Task 3 -- and never reaches this class, so
there is no code for it here):

(b) A period is resolved (the state block's ``Period:`` line), the hints
    gate permits it (see "Task 3 CLOSURE" above: no ``"Skill hints:"`` line
    at all, or one present and leading with ``data_qa.metric_query``), and
    no ``toolResult`` block is anywhere in ``messages`` yet -> ONE
    ``tool_use`` for ``data_qa.metric_query``. Metric defaults to ``["GP"]``. The last
    ``user``-role message in ``messages`` that carries a text block (see
    :func:`_last_user_text`) containing the standalone, casefolded word
    "top" adds ``group_by="CUST_NM"``/``top_n=5``. A resolved port/customer
    (the ``Resolved port:``/``Resolved customer:`` lines -- THIS turn's
    fresh parse, not the carried ``Carried port:``/``Carried customer:``
    slot lines) each add their own filter, customer first. A resolved
    compare period (``Compare period:``) adds ``compare_period`` -- and,
    since ``Args`` itself rejects ``group_by`` combined with
    ``compare_period`` (its own ``_reject_breakdown_with_compare``
    validator), a compare period wins over a "top" mention when both are
    present (disclosed judgment call -- the plan does not name this
    conflict; a resolved compare period is a stronger, structurally-parsed
    signal than a lexical cue on the message text). The tool call id is
    always ``"dev-1"``: this case never emits more than one call, so
    per-invoke-sequence numbering never has occasion to reach ``"dev-2"``.
(b2) (Task 4) Case (b) declined (hints do not permit metric_query), hints
    instead LEAD (tie-inclusive since Task 5 -- see "Task 5 CLOSURE" above)
    with ``research.web_research``, AND a customer or port is known,
    resolved-or-carried -> ONE ``tool_use`` for ``research.web_research``.
    See "Task 4 CLOSURE" above for the full rule.
(b3) (Task 5) Cases (b)/(b2) declined, hints LEAD (tie-inclusive) with a
    ``customer_insight`` brief skill, AND the message text names a brief/
    report/profile/overview word, AND -- existing branch only -- a customer
    is resolved THIS turn -> ONE ``tool_use`` for that brief skill. See
    "Task 5 CLOSURE" above for the full rule, the brief-word guard, and why
    the two branches (existing/prospect) gate differently.
(c) A ``toolResult`` block is present anywhere in ``messages``, the most
    recently appended ``toolUse`` did NOT name ``research.web_research`` or
    either brief skill (see (c2)/(c3), both checked first), and a period is
    resolved -> end the turn with ``f"Certified answer for {entity_label}
    -- {period_label}."`` (the module's own em dash -- see :data:`_EM_DASH`).
    ``entity_label`` is the resolved customer if present, else the resolved
    port, else the fallback ``"All Customers"``; ``period_label`` is the
    resolved period's own ``start..end`` substring, plus `` vs
    {compare_start..compare_end}`` when a compare period is also resolved
    -- reusing the block's own ISO half-open substrings verbatim rather
    than reformatting them, so the label can never disagree with the block
    it was read from. Checked BEFORE (b): once a tool has run this turn,
    the router's job is to close the turn, not place a second call. Fires
    regardless of the ``toolResult``'s own ``status`` ("success" or
    "error") -- distinguishing the two is a real model's job; this fake's
    whole point is a canned, demoable answer, not error-recovery behavior.
(c2) (Task 4) The most recently appended ``toolUse`` anywhere in
    ``messages`` names ``research.web_research`` -> end the turn with
    ``f"Research summary for {subject} -- {n} sources."``, regardless of
    whether a period is ALSO resolved (checked BEFORE (c) for exactly that
    reason -- see "Task 4 CLOSURE" above).
(c3) (Task 5) The most recently appended ``toolUse`` anywhere in
    ``messages`` names either brief skill -> end the turn with ``f"Brief
    complete for {subject}."``, checked alongside (c2) and BEFORE (c) for
    the identical reason. See "Task 5 CLOSURE" above for where ``subject``
    comes from on each branch.
(d) Anything else (most commonly: no period resolved and hints do not lead
    research or a brief with their own required signal, so none of
    (b)/(b2)/(b3) can fire and there is nothing yet to certify for
    (c)/(c2)/(c3)) -> end the turn with the pinned capability message. This
    is also the graceful fallback for a shape a real caller (``run_turn``)
    can never actually produce -- a ``toolResult`` present with NO period
    resolved, since (b)'s own precondition means a period was necessarily
    resolved before any METRIC tool could have run this turn -- rather
    than an unhandled exception on an input nothing today sends.

Every response carries ``input_tokens=0``/``output_tokens=0``: this router
is not a model, so there is nothing to count. Phase 6's run-log rows will
honestly show 0 tokens for every turn served in stub mode -- a true
statement about a fake, not a placeholder pretending to be a real usage
figure.
"""

import re
from dataclasses import dataclass

from poseidon.core.llm.types import LLMResponse, ToolCall

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk -- the same convention roles.py's own _EM_DASH
# uses (see that module's comment), byte-pinned by this task's test suite.
_EM_DASH = chr(0x2014)

_METRIC_QUERY_SKILL_ID = "data_qa.metric_query"
# Task 4: bare string constant, not an import of poseidon.tasks.research --
# this router imports no task/chat-wiring module (see the module docstring's
# opening contract paragraph), the same reason _METRIC_QUERY_SKILL_ID above
# is a literal rather than `from poseidon.tasks.data_qa... import`. Pinned
# equal to poseidon.core.parsing.lexicon.WEB_RESEARCH by
# test_research_skill_id_matches_the_hinter_lexicons_own_constant, the same
# decoupled-equality precedent _DEFAULT_ENTITY below already uses.
_RESEARCH_SKILL_ID = "research.web_research"
# Task 5: the identical bare-string-constant convention, one level further --
# pinned equal to poseidon.core.parsing.lexicon.EXISTING_CUSTOMER_BRIEF/
# NEW_PROSPECT_BRIEF by test_existing_brief_skill_id_matches_the_hinter_
# lexicons_own_constant / test_new_prospect_brief_skill_id_matches_the_
# hinter_lexicons_own_constant.
_EXISTING_CUSTOMER_BRIEF_SKILL_ID = "customer_insight.existing_customer_brief"
_NEW_PROSPECT_BRIEF_SKILL_ID = "customer_insight.new_prospect_brief"
_DEFAULT_ENTITY = "MARINE_SALES_PLANNING_V"
_DEFAULT_METRIC = "GP"
_CUST_NM = "CUST_NM"
_LOC_NM = "LOC_NM"
_GROUP_BY_TOP_N = 5
_NO_ENTITY_LABEL = "All Customers"
# Defensive-only fallback for _research_summary: unreachable through this
# router's OWN gate (case (b2) already requires a subject before ever
# placing the research tool_use that a second invoke's toolUse/toolResult
# pair would echo back), kept for the same total-function reason
# _certified_answer's own _NO_ENTITY_LABEL exists -- invoke() is exercised
# directly by this suite's own off-contract tests, not only through a real
# two-invoke cycle this router itself drove.
_NO_RESEARCH_SUBJECT_LABEL = "the requested topic"
# Task 5: the identical defensive-only fallback, one level further, for
# _brief_summary's prospect branch -- unreachable through this router's OWN
# gate (the prospect tool_use always carries a "prospect_name" input, echoed
# back verbatim by loop.py's _assistant_tool_use_message), kept for the same
# total-function reason _NO_RESEARCH_SUBJECT_LABEL exists.
_NO_PROSPECT_SUBJECT_LABEL = "the requested prospect"
_TOOL_CALL_ID = "dev-1"

_CAPABILITY_MESSAGE = (
    "I can answer certified metric questions "
    + _EM_DASH
    + " try a metric, a customer or port, and a period."
)

# The conversation-state section always comes last in `system` (doc 03
# section 3's fixed assembly order) under this literal, tested header --
# `poseidon.core.llm.prompts`'s own `_HEADER_STATE` value, pinned a second
# time here rather than imported: a leading-underscore name is that
# module's own private detail, while this exact string is the documented,
# tested PUBLIC contract of `assemble_system`'s section header (see the
# module docstring's "Parse source" paragraph).
_STATE_HEADER = "=== CONVERSATION STATE ==="

# Anchored to the WHOLE pinned line format render_state_block produces
# (prompts.py's _render_entity/_render_window), not just a loose substring
# -- so "Carried customer: X" (a different, un-resolved line) can never be
# mistaken for "Resolved customer: X", and re.MULTILINE with no DOTALL
# means "." never crosses a newline, so a match can never bleed across
# lines.
_RESOLVED_CUSTOMER_RE = re.compile(
    r"^Resolved customer: (?P<value>.+) \(tier=[a-z]+, confidence=[0-9.]+\)$", re.MULTILINE
)
_RESOLVED_PORT_RE = re.compile(
    r"^Resolved port: (?P<value>.+) \(tier=[a-z]+, confidence=[0-9.]+\)$", re.MULTILINE
)
_PERIOD_RE = re.compile(
    r"^Period: (?P<start>\d{4}-\d{2}-\d{2})\.\.(?P<end>\d{4}-\d{2}-\d{2})$", re.MULTILINE
)
_COMPARE_PERIOD_RE = re.compile(
    r"^Compare period: (?P<start>\d{4}-\d{2}-\d{2})\.\.(?P<end>\d{4}-\d{2}-\d{2})$", re.MULTILINE
)

# Task 4: the CARRIED counterparts of _RESOLVED_CUSTOMER_RE/_RESOLVED_PORT_RE
# -- prompts.py's own _render_slots emits these with no "(tier=..., confidence
# =...)" suffix at all (a carried slot is already a certified value, nothing
# left to express confidence about), so the two pairs of regexes are
# necessarily shaped differently, not merely renamed. Anchored to the WHOLE
# line for the same reason every regex in this module is: "Carried customer:"
# must never be mistaken for a differently-prefixed line sharing a substring.
_CARRIED_CUSTOMER_RE = re.compile(r"^Carried customer: (?P<value>.+)$", re.MULTILINE)
_CARRIED_PORT_RE = re.compile(r"^Carried port: (?P<value>.+)$", re.MULTILINE)

# Anchored to the WHOLE pinned line format `prompts.py`'s new `_render_hints`
# produces ("Skill hints: <id> (<score>), <id> (<score>), ..."), same
# discipline as every regex above. Task 5 CLOSURE (see the module docstring):
# EVERY entry is now captured, not just the first -- "leads" is redefined as
# "ties for the line's best score" (:func:`_hints_lead`, below), which needs
# the WHOLE line, not merely its first entry. `_HINTS_LINE_RE` isolates the
# line's own text (everything after "Skill hints: "); `_HINT_ENTRY_RE` then
# finds every `<id> (<score>)` pair inside it. Skill ids are dotted words
# (`[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+`); `[\w.]+` is simpler and just as safe
# here since nothing else in an entry's prefix can produce a `\w` or `.` run
# before its ` (`.
_HINTS_LINE_RE = re.compile(r"^Skill hints: (?P<entries>.+)$", re.MULTILINE)
_HINT_ENTRY_RE = re.compile(r"(?P<skill_id>[\w.]+) \((?P<score>[0-9.]+)\)")

# Word-boundary, casefolded -- the same discipline skill_hinter.py's own
# lexicon matching uses (its module docstring: a lexeme like "top" matches
# the standalone word "top" but never a substring inside a longer word
# such as "stopped"). This router does not import that module (see the
# "hints gap" paragraph above) but borrows its matching DISCIPLINE for the
# one lexeme the behavior table names explicitly.
_TOP_WORD_RE = re.compile(r"\btop\b")

# Task 5: the identical discipline, for the brief branch's own guard (see
# the module docstring's "The brief-word guard"). Matches lexicon.KEYWORDS'
# own four generic "-- brief words" entries exactly, cross-checked by
# test_mentions_brief_word_matches_every_lexicon_brief_lexeme rather than
# imported (this router imports no lexicon table -- see the module
# docstring's opening contract paragraph).
_BRIEF_WORD_RE = re.compile(r"\b(brief|report|profile|overview)\b")

# Task 4: read off the toolResult digest text a research.web_research
# dispatch produced -- loop.py's own tool_result_digest renders this
# skill's "Results: {n}" proof line (tools/format_parts.py) verbatim into
# the toolResult content this router's second invoke actually receives
# (_tool_result_block's own "content": [{"text": digest}] shape). Not
# anchored to line-start (unlike every regex above): the digest is a
# multi-line block this router never fully re-parses, only searches, so a
# loose search is the correct amount of parsing for one line inside a blob
# this module does not otherwise inspect the shape of.
_RESULTS_COUNT_RE = re.compile(r"Results: (?P<n>\d+)")


@dataclass(frozen=True)
class _ParsedState:
    """What this router could read out of one ``system`` string's
    conversation-state section -- ``None`` per field when that field's line
    was not present.

    ``hints_permit_dispatch`` is not a straight field read -- see the
    module docstring's "Task 3 CLOSURE" for the rule it implements (upgraded
    to tie-inclusive by Task 5's own "Task 5 CLOSURE" -- see that section
    for the reproduced gap this closes). ``hints_lead_research``/
    ``carried_customer``/``carried_port`` are Task 4 additions -- see "Task
    4 CLOSURE" (``hints_lead_research`` is likewise now tie-inclusive).
    ``hints_lead_existing_brief``/``hints_lead_new_prospect_brief`` are
    Task 5 additions -- see "Task 5 CLOSURE". All four ``hints_lead_*``/
    ``hints_permit_dispatch`` fields are computed by the SAME
    :func:`_hints_lead` helper against the SAME parsed hint-entry tuple, so
    the four gates can never disagree about what the hints line's maximum
    score is.
    """

    customer: str | None
    port: str | None
    period: tuple[str, str] | None
    compare_period: tuple[str, str] | None
    hints_permit_dispatch: bool
    hints_lead_research: bool
    hints_lead_existing_brief: bool
    hints_lead_new_prospect_brief: bool
    carried_customer: str | None
    carried_port: str | None


class DevDeterministicRouter:
    """A scripted, deterministic stand-in for a real model. See the module
    docstring for the full contract and behavior table.

    No ``__init__``: there is nothing to construct. Every call to
    :meth:`invoke` is independent and stateless, which is itself part of
    the contract (see the module docstring's opening paragraph) -- an
    instance carries nothing between calls, so one instance can safely be
    shared across every turn and every conversation.
    """

    def invoke(
        self, *, system: str, messages: list[dict], tools: list[dict], model: str, params: dict
    ) -> LLMResponse:
        """Dispatch per the module docstring's behavior table.

        ``tools``/``model``/``params`` complete the fixed
        :class:`~poseidon.core.llm.roles.LLMProvider` signature this class
        implements, but this router's decisions never depend on their
        values -- see the module docstring's opening paragraph.
        """
        parsed_state = _parse_state(system)
        last_tool_use = _last_tool_use_name(messages)
        # Case (c2), Task 4: checked FIRST, and independent of whether a
        # period is also resolved -- see the module docstring's "Task 4
        # CLOSURE" for why this must win over case (c)'s own toolResult
        # check rather than the two racing on "any toolResult exists".
        if last_tool_use == _RESEARCH_SKILL_ID:
            return _research_summary(parsed_state, messages)
        # Case (c3), Task 5: the identical priority, for either brief skill
        # -- see the module docstring's "Task 5 CLOSURE".
        if last_tool_use in (_EXISTING_CUSTOMER_BRIEF_SKILL_ID, _NEW_PROSPECT_BRIEF_SKILL_ID):
            return _brief_summary(parsed_state, messages, last_tool_use)
        if _has_tool_result(messages) and parsed_state.period is not None:
            return _certified_answer(parsed_state)
        if parsed_state.period is not None and parsed_state.hints_permit_dispatch:
            return _metric_query_call(parsed_state, messages)
        # Case (b2), Task 4: only reached once (b) has already declined.
        if parsed_state.hints_lead_research and _research_subject(parsed_state) is not None:
            return _research_call(parsed_state, messages)
        # Case (b3), Task 5: only reached once (b)/(b2) have already
        # declined -- see the module docstring's "Task 5 CLOSURE" for the
        # brief-word guard and why the existing/prospect branches gate
        # differently (a resolved customer vs. nothing to resolve at all).
        if (
            parsed_state.hints_lead_existing_brief
            and parsed_state.customer is not None
            and _mentions_brief_word(messages)
        ):
            return _existing_brief_call(parsed_state)
        if parsed_state.hints_lead_new_prospect_brief and _mentions_brief_word(messages):
            return _prospect_brief_call(messages)
        return _capability_response()


def _hints_lead(hint_entries: tuple[tuple[str, float], ...], skill_id: str) -> bool:
    """Whether ``skill_id`` appears in the parsed hints line AND its score
    equals the line's own maximum -- "leads", read tie-inclusively. See the
    module docstring's "Task 5 CLOSURE" ("Leads redefined") for why this
    replaces the old "is the textually first entry" reading for every gate
    in this module, and why that is provably identical to the old reading
    whenever there is no tie. ``False`` when ``hint_entries`` is empty (a
    permissive-when-absent caller, like ``hints_permit_dispatch``, checks
    emptiness separately -- this function only ever answers "does X lead",
    never "is there no opinion").
    """
    if not hint_entries:
        return False
    best_score = max(score for _skill_id, score in hint_entries)
    return any(
        entry_skill_id == skill_id and score == best_score for entry_skill_id, score in hint_entries
    )


def _parse_hint_entries(block: str) -> tuple[tuple[str, float], ...]:
    """Every ``(skill_id, score)`` pair on the "Skill hints:" line, in the
    order rendered (``prompts.py``'s own ``_render_hints``: already
    best-first, comma-separated) -- ``()`` when the line is absent. See the
    module docstring's "Task 5 CLOSURE" for why this reads the WHOLE line
    now, not just its first entry."""
    line_match = _HINTS_LINE_RE.search(block)
    if line_match is None:
        return ()
    return tuple(
        (entry.group("skill_id"), float(entry.group("score")))
        for entry in _HINT_ENTRY_RE.finditer(line_match.group("entries"))
    )


def _parse_state(system: str) -> _ParsedState:
    """Regex the conversation-state section of ``system`` -- see the module
    docstring's "Parse source" section for exactly which lines this reads
    and why each pattern is anchored the way it is."""
    marker = system.find(_STATE_HEADER)
    block = system[marker + len(_STATE_HEADER) :] if marker != -1 else ""

    customer_match = _RESOLVED_CUSTOMER_RE.search(block)
    port_match = _RESOLVED_PORT_RE.search(block)
    period_match = _PERIOD_RE.search(block)
    compare_match = _COMPARE_PERIOD_RE.search(block)
    hint_entries = _parse_hint_entries(block)
    carried_customer_match = _CARRIED_CUSTOMER_RE.search(block)
    carried_port_match = _CARRIED_PORT_RE.search(block)
    return _ParsedState(
        customer=customer_match.group("value") if customer_match else None,
        port=port_match.group("value") if port_match else None,
        period=(period_match.group("start"), period_match.group("end")) if period_match else None,
        compare_period=(compare_match.group("start"), compare_match.group("end"))
        if compare_match
        else None,
        # Permissive when the line is absent altogether (no opinion to
        # respect); refuses only when a line IS present and metric_query
        # does NOT tie its best score -- see the module docstring's "Task 3
        # CLOSURE" (rule) and "Task 5 CLOSURE" (tie-inclusive upgrade).
        hints_permit_dispatch=(
            not hint_entries or _hints_lead(hint_entries, _METRIC_QUERY_SKILL_ID)
        ),
        # Task 4 (tie-inclusive since Task 5): the OPPOSITE polarity from
        # hints_permit_dispatch above -- this is an opt-IN signal (a hints
        # line must be present AND actually lead/tie research), not a
        # permissive-when-absent one, since research dispatch has no OTHER
        # required signal the way case (b)'s own period requirement gives
        # metric_query -- see "Task 4 CLOSURE".
        hints_lead_research=_hints_lead(hint_entries, _RESEARCH_SKILL_ID),
        # Task 5: the identical opt-in shape, one per brief skill -- see
        # "Task 5 CLOSURE".
        hints_lead_existing_brief=_hints_lead(hint_entries, _EXISTING_CUSTOMER_BRIEF_SKILL_ID),
        hints_lead_new_prospect_brief=_hints_lead(hint_entries, _NEW_PROSPECT_BRIEF_SKILL_ID),
        carried_customer=carried_customer_match.group("value") if carried_customer_match else None,
        carried_port=carried_port_match.group("value") if carried_port_match else None,
    )


def _has_tool_result(messages: list[dict]) -> bool:
    """Whether any message in the window already carries a Converse
    ``toolResult`` block (``loop.py``'s ``_tool_result_block`` shape) --
    the signal that this turn's tool already ran and it is time to close
    the turn, not place another call."""
    return any(
        isinstance(block, dict) and "toolResult" in block
        for message in messages
        for block in (message.get("content") or [])
    )


def _last_tool_use_name(messages: list[dict]) -> str | None:
    """Task 4: the ``name`` off the MOST RECENTLY appended Converse
    ``toolUse`` block anywhere in ``messages`` (``loop.py``'s own
    ``_assistant_tool_use_message`` shape -- the model's own echoed tool
    call), or ``None`` when there is none yet. This is how a second invoke
    tells WHICH skill a ``toolResult`` is closing out, rather than merely
    that one exists (see the module docstring's "Task 4 CLOSURE" for why
    that distinction matters the moment a period is ALSO resolved the same
    turn a research call ran). This router only ever emits ONE tool call
    per turn (:data:`_TOOL_CALL_ID` is always ``"dev-1"``), so at most one
    such block can exist within a turn's ``messages`` -- scanning in
    reverse is future-proofing the read, not a response to any real
    ambiguity today.
    """
    for message in reversed(messages):
        for block in reversed(message.get("content") or []):
            if isinstance(block, dict) and "toolUse" in block:
                tool_use = block["toolUse"]
                if isinstance(tool_use, dict):
                    name = tool_use.get("name")
                    return name if isinstance(name, str) else None
    return None


def _last_tool_use_input(messages: list[dict]) -> dict:
    """Task 5: the sibling read to :func:`_last_tool_use_name` -- the
    ``input`` dict off the SAME most-recently-appended ``toolUse`` block,
    or ``{}`` when there is none (or its ``input`` is not a dict). Needed
    because :func:`_brief_summary`'s prospect branch has no state-block
    line to read a subject from the way the existing branch reads
    ``parsed_state.customer`` (a prospect is never resolved, D19's own
    "text = subject" rule mirrored here) -- the ECHOED ``toolUse`` is the
    only place the dispatched ``prospect_name`` still exists on this
    second invoke.
    """
    for message in reversed(messages):
        for block in reversed(message.get("content") or []):
            if isinstance(block, dict) and "toolUse" in block:
                tool_use = block["toolUse"]
                if isinstance(tool_use, dict):
                    input_ = tool_use.get("input")
                    return input_ if isinstance(input_, dict) else {}
    return {}


def _last_tool_result_text(messages: list[dict]) -> str:
    """Task 4: the text content of the MOST RECENTLY appended ``toolResult``
    block, or ``""`` when there is none (or its content is not the plain
    ``{"text": ...}`` shape a successful dispatch produces -- see
    ``loop.py``'s own ``_tool_result_block``; an ``error``-status result
    carries ``{"json": ...}`` instead, which this deliberately does not try
    to read: :func:`_research_summary` only ever reaches this after
    confirming the LAST toolUse was research.web_research, and this
    skill's own ``run`` (Task 4) always returns ``ok=True`` -- see
    ``skill.py``'s own module docstring -- so an error-shaped result is not
    a real shape this router needs to parse, only a shape it must not
    crash on).
    """
    for message in reversed(messages):
        for block in reversed(message.get("content") or []):
            if isinstance(block, dict) and "toolResult" in block:
                tool_result = block["toolResult"]
                if isinstance(tool_result, dict):
                    content = tool_result.get("content") or []
                    if content and isinstance(content[0], dict):
                        text = content[0].get("text")
                        if isinstance(text, str):
                            return text
    return ""


def _last_user_text(messages: list[dict]) -> str | None:
    """The text content of the LAST ``user``-role message that carries a
    text block, or ``None`` when there is not one.

    A ``user``-role message can also carry a ``toolResult`` block with no
    ``text`` at all (``loop.py`` appends tool results as ``role: "user"``
    too -- Converse's own request/response pairing convention); this skips
    those rather than returning empty prose, so a trailing tool-result
    message never masks the actual last thing the user said.
    """
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        texts = [block["text"] for block in (message.get("content") or []) if "text" in block]
        if texts:
            return " ".join(texts)
    return None


def _mentions_top(messages: list[dict]) -> bool:
    text = _last_user_text(messages)
    return text is not None and _TOP_WORD_RE.search(text.casefold()) is not None


def _mentions_brief_word(messages: list[dict]) -> bool:
    """Task 5: whether the LAST user message says brief/report/profile/
    overview -- the brief branch's own required signal, checked ALONGSIDE
    ``hints_lead_existing_brief``/``hints_lead_new_prospect_brief`` rather
    than folded into either boolean, so a test (and a reader) can exercise
    each half of the gate independently. See the module docstring's "The
    brief-word guard" for why this exists at all."""
    text = _last_user_text(messages)
    return text is not None and _BRIEF_WORD_RE.search(text.casefold()) is not None


def _metric_query_call(parsed_state: _ParsedState, messages: list[dict]) -> LLMResponse:
    """Case (b): build the one tool call. ``parsed_state.period`` is
    guaranteed non-``None`` by :meth:`DevDeterministicRouter.invoke`."""
    start, end = parsed_state.period
    arguments: dict[str, object] = {
        "entity": _DEFAULT_ENTITY,
        "metrics": [_DEFAULT_METRIC],
        "period": {"start": start, "end": end},
    }

    filters = []
    if parsed_state.customer is not None:
        filters.append({"column": _CUST_NM, "values": [parsed_state.customer]})
    if parsed_state.port is not None:
        filters.append({"column": _LOC_NM, "values": [parsed_state.port]})
    if filters:
        arguments["filters"] = filters

    if parsed_state.compare_period is not None:
        # Wins over a "top" mention -- see the module docstring's case (b).
        compare_start, compare_end = parsed_state.compare_period
        arguments["compare_period"] = {"start": compare_start, "end": compare_end}
    elif _mentions_top(messages):
        arguments["group_by"] = _CUST_NM
        arguments["top_n"] = _GROUP_BY_TOP_N

    call = ToolCall(id=_TOOL_CALL_ID, name=_METRIC_QUERY_SKILL_ID, arguments=arguments)
    return LLMResponse(
        text="", tool_calls=(call,), stop_reason="tool_use", input_tokens=0, output_tokens=0
    )


def _research_subject(parsed_state: _ParsedState) -> str | None:
    """Task 4: the resolved-or-carried customer/port that both
    :func:`_research_call` (the tool call's own args) and
    :func:`_research_summary` (the close's own label) read -- customer
    before port, resolved before carried, matching the same "customer wins
    over port" precedence :func:`_certified_answer`'s own ``entity_label``
    already uses. ``None`` when NEITHER customer nor port is known any way
    -- the AND-gate condition case (b2) checks before ever calling
    :func:`_research_call`."""
    return (
        parsed_state.customer
        or parsed_state.carried_customer
        or parsed_state.port
        or parsed_state.carried_port
    )


def _research_call(parsed_state: _ParsedState, messages: list[dict]) -> LLMResponse:
    """Case (b2), Task 4: build the one tool call for ``research.
    web_research``. ``parsed_state`` is guaranteed to have a customer or
    port, resolved-or-carried, by :meth:`DevDeterministicRouter.invoke`'s
    own gate (:func:`_research_subject` is not ``None``).

    ``customer``/``port`` are attached INDEPENDENTLY -- both, when both are
    known -- from whichever of resolved-this-turn or carried is present per
    field (the module docstring's "Task 4 CLOSURE" disclosed judgment
    call), unlike :func:`_metric_query_call`'s own filters, which read ONLY
    resolved-this-turn lines on purpose.
    """
    question = _last_user_text(messages) or ""
    arguments: dict[str, object] = {"question": question}
    customer = parsed_state.customer or parsed_state.carried_customer
    port = parsed_state.port or parsed_state.carried_port
    if customer is not None:
        arguments["customer"] = customer
    if port is not None:
        arguments["port"] = port

    call = ToolCall(id=_TOOL_CALL_ID, name=_RESEARCH_SKILL_ID, arguments=arguments)
    return LLMResponse(
        text="", tool_calls=(call,), stop_reason="tool_use", input_tokens=0, output_tokens=0
    )


def _research_summary(parsed_state: _ParsedState, messages: list[dict]) -> LLMResponse:
    """Case (c2), Task 4: close a research turn. ``system`` is unchanged
    across a turn's iterations (``loop.py``'s own "built ONCE per turn"),
    so ``parsed_state`` here is byte-identical to what :func:`_research_call`
    computed on the first invoke -- the subject label can never disagree
    with the tool call that produced it.
    """
    subject = _research_subject(parsed_state) or _NO_RESEARCH_SUBJECT_LABEL
    count_match = _RESULTS_COUNT_RE.search(_last_tool_result_text(messages))
    count = int(count_match.group("n")) if count_match else 0
    text = f"Research summary for {subject} {_EM_DASH} {count} sources."
    return LLMResponse(
        text=text, tool_calls=(), stop_reason="end_turn", input_tokens=0, output_tokens=0
    )


def _existing_brief_call(parsed_state: _ParsedState) -> LLMResponse:
    """Case (b3), Task 5, existing branch: build the one tool call for
    ``customer_insight.existing_customer_brief``. ``parsed_state.customer``
    is guaranteed non-``None`` by :meth:`DevDeterministicRouter.invoke`'s
    own gate. Only ``customer`` is ever supplied -- ``recency_days`` is
    left to the skill's own default (365), the same "minimal args, skill
    defaults do the rest" discipline :func:`_metric_query_call`'s own
    ``metrics`` default already follows.
    """
    call = ToolCall(
        id=_TOOL_CALL_ID,
        name=_EXISTING_CUSTOMER_BRIEF_SKILL_ID,
        arguments={"customer": parsed_state.customer},
    )
    return LLMResponse(
        text="", tool_calls=(call,), stop_reason="tool_use", input_tokens=0, output_tokens=0
    )


def _prospect_brief_call(messages: list[dict]) -> LLMResponse:
    """Case (b3), Task 5, prospect branch: build the one tool call for
    ``customer_insight.new_prospect_brief``. No customer/port gate here --
    a prospect is BY DEFINITION not a certified dimension value, so there
    is nothing for this router to resolve (D19's own "text = subject" rule,
    mirrored here); the whole last user message becomes ``prospect_name``
    verbatim, the same crude, no-extraction precedent :func:`_research_call`
    already established for its own ``question`` field.
    """
    prospect_name = _last_user_text(messages) or ""
    call = ToolCall(
        id=_TOOL_CALL_ID,
        name=_NEW_PROSPECT_BRIEF_SKILL_ID,
        arguments={"prospect_name": prospect_name},
    )
    return LLMResponse(
        text="", tool_calls=(call,), stop_reason="tool_use", input_tokens=0, output_tokens=0
    )


def _brief_summary(parsed_state: _ParsedState, messages: list[dict], skill_id: str) -> LLMResponse:
    """Case (c3), Task 5: close a brief turn -- either branch, told apart
    by ``skill_id`` (:meth:`DevDeterministicRouter.invoke`'s own
    :func:`_last_tool_use_name` read). ``system`` is unchanged across a
    turn's iterations (``loop.py``'s own "built ONCE per turn"), so the
    existing branch's ``parsed_state.customer`` here is byte-identical to
    what :func:`_existing_brief_call` read on the first invoke -- the same
    reasoning :func:`_research_summary` already relies on. The prospect
    branch has no such state-block line to re-read (a prospect is never
    resolved), so it reads the SAME echoed ``toolUse`` input
    :func:`_prospect_brief_call` built instead (:func:`_last_tool_use_input`).
    """
    if skill_id == _EXISTING_CUSTOMER_BRIEF_SKILL_ID:
        subject = parsed_state.customer or _NO_ENTITY_LABEL
    else:
        prospect_name = _last_tool_use_input(messages).get("prospect_name")
        subject = prospect_name or _NO_PROSPECT_SUBJECT_LABEL
    text = f"Brief complete for {subject}."
    return LLMResponse(
        text=text, tool_calls=(), stop_reason="end_turn", input_tokens=0, output_tokens=0
    )


def _certified_answer(parsed_state: _ParsedState) -> LLMResponse:
    """Case (c). ``parsed_state.period`` is guaranteed non-``None`` by
    :meth:`DevDeterministicRouter.invoke`."""
    entity_label = parsed_state.customer or parsed_state.port or _NO_ENTITY_LABEL
    start, end = parsed_state.period
    period_label = f"{start}..{end}"
    if parsed_state.compare_period is not None:
        compare_start, compare_end = parsed_state.compare_period
        period_label = f"{period_label} vs {compare_start}..{compare_end}"
    text = f"Certified answer for {entity_label} {_EM_DASH} {period_label}."
    return LLMResponse(
        text=text, tool_calls=(), stop_reason="end_turn", input_tokens=0, output_tokens=0
    )


def _capability_response() -> LLMResponse:
    """Case (d)."""
    return LLMResponse(
        text=_CAPABILITY_MESSAGE,
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=0,
        output_tokens=0,
    )


__all__ = ["DevDeterministicRouter"]
