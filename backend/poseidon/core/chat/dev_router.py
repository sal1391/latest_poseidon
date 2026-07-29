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

Behavior table (the phase-6 plan; case (a), ambiguous turns, is the
orchestrator's short-circuit -- Task 3 -- and never reaches this class, so
there is no code for it here):

(b) A period is resolved (the state block's ``Period:`` line) and no
    ``toolResult`` block is anywhere in ``messages`` yet -> ONE ``tool_use``
    for ``data_qa.metric_query``. Metric defaults to ``["GP"]``. The last
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
(c) A ``toolResult`` block is present anywhere in ``messages`` -> end the
    turn with ``f"Certified answer for {entity_label} -- {period_label}."``
    (the module's own em dash -- see :data:`_EM_DASH`). ``entity_label`` is
    the resolved customer if present, else the resolved port, else the
    fallback ``"All Customers"``; ``period_label`` is the resolved period's
    own ``start..end`` substring, plus `` vs {compare_start..compare_end}``
    when a compare period is also resolved -- reusing the block's own ISO
    half-open substrings verbatim rather than reformatting them, so the
    label can never disagree with the block it was read from. Checked
    BEFORE (b): once a tool has run this turn, the router's job is to close
    the turn, not place a second call. Fires regardless of the
    ``toolResult``'s own ``status`` ("success" or "error") -- distinguishing
    the two is a real model's job; this fake's whole point is a canned,
    demoable answer, not error-recovery behavior.
(d) Anything else (most commonly: no period resolved, so (b) cannot fire
    and there is nothing yet to certify for (c)) -> end the turn with the
    pinned capability message. This is also the graceful fallback for a
    shape a real caller (``run_turn``) can never actually produce -- a
    ``toolResult`` present with NO period resolved, since (b)'s own
    precondition means a period was necessarily resolved before any tool
    could have run this turn -- rather than an unhandled exception on an
    input nothing today sends.

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
_DEFAULT_ENTITY = "MARINE_SALES_PLANNING_V"
_DEFAULT_METRIC = "GP"
_CUST_NM = "CUST_NM"
_LOC_NM = "LOC_NM"
_GROUP_BY_TOP_N = 5
_NO_ENTITY_LABEL = "All Customers"
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

# Word-boundary, casefolded -- the same discipline skill_hinter.py's own
# lexicon matching uses (its module docstring: a lexeme like "top" matches
# the standalone word "top" but never a substring inside a longer word
# such as "stopped"). This router does not import that module (see the
# "hints gap" paragraph above) but borrows its matching DISCIPLINE for the
# one lexeme the behavior table names explicitly.
_TOP_WORD_RE = re.compile(r"\btop\b")


@dataclass(frozen=True)
class _ParsedState:
    """What this router could read out of one ``system`` string's
    conversation-state section -- ``None`` per field when that field's line
    was not present."""

    customer: str | None
    port: str | None
    period: tuple[str, str] | None
    compare_period: tuple[str, str] | None


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
        if _has_tool_result(messages) and parsed_state.period is not None:
            return _certified_answer(parsed_state)
        if parsed_state.period is not None:
            return _metric_query_call(parsed_state, messages)
        return _capability_response()


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
    return _ParsedState(
        customer=customer_match.group("value") if customer_match else None,
        port=port_match.group("value") if port_match else None,
        period=(period_match.group("start"), period_match.group("end")) if period_match else None,
        compare_period=(compare_match.group("start"), compare_match.group("end"))
        if compare_match
        else None,
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
