"""``parse_turn`` (doc 02 section 5): the one function the rest of the
product calls. It composes every earlier Phase 4 stage -- ``normalize``,
the customer/port resolver, the period parser, slot carry, and the skill
hinter -- into a single deterministic pass over one inbound chat message.
No LLM call, no network beyond the ``DataClient`` seam.

Order
-----
1. ``normalize`` the raw message once. The result is ``ParsedTurn.
   normalized_text`` verbatim -- the record of what the user said -- and
   the base text every later step works from.
2. Detect a PORT phrase in the normalized text: a "port of X" or "at X"
   cue immediately followed by a TitleCase run (see "Phrase detection"
   below). Detection only -- no data call yet.
3. Detect a CUSTOMER phrase, in priority order: a quoted span, then a
   "for/about/on X" cue + TitleCase run (scanned over a scratch copy with
   the port span already blanked out -- see "Why the port span is blanked
   first"), then -- only if neither found anything -- the carried
   ``slots.customer`` itself, used AS the phrase. An explicit "clear the
   customer"/"reset the customer" phrase short-circuits this entirely.
4. Resolve whatever was detected (customer, then port) via
   ``customer_resolver.resolve``, against ``data.list_dimension_values``
   pools fetched LAZILY -- only when step 2/3 actually found something to
   resolve, so an ordinary message that names neither never pays for either
   fetch. Each AUTO-APPLIED resolution's span (tier exact/token, or fuzzy
   ``>= customer_resolver.AUTO_APPLY_THRESHOLD``) is recorded for masking;
   a candidate-band or unknown result is not (it produces an issue instead
   -- see "Masking" below). A carry-derived customer phrase has no span in
   THIS turn's text, so it is never a masking candidate either way.
5. Mask every recorded span out of a working copy of the text (spaces,
   same length, so every other offset stays stable) before period parsing.
6. Parse periods over the MASKED working text, via
   ``period_parser.parse_periods(working_text, slots, reference_date,
   data.available_periods(entity))``. ``parse_periods`` normalizes its own
   input, so handing it already-normalized (and now also masked) text is
   safe and idempotent.
7. Fold this turn's resolutions into ``SlotUpdates`` (see "Slot write-back"
   below) and apply them to the PRIOR ``slots`` via ``carry.apply_carry``.
8. Hint skills from the UNMASKED normalized text and the POST-carry slots
   (masking is a period-parsing concern only; the hinter's lexicon has
   nothing to do with dates, and a mode hint should see the freshest mode).
9. Collect every issue (customer, then port, then period -- an arbitrary
   but fixed, documented order, since ``ParsedTurn.issues`` has no
   precedence rule of its own the way ``PeriodParse.issue`` does) into one
   tuple and assemble the ``ParsedTurn``.

Phrase detection
-----------------
A TitleCase run is one or more consecutive capitalized words (each
``[A-Z][\\w'-]*``), stopping at the first word that is not capitalized --
deliberately strict (no lowercase connectors like "of"/"the" survive inside
a run) because every name in this domain's fixtures and seed data is a
short run of capitalized words, and a looser run would over-reach across
ordinary sentences.

Conservative detection (hard requirement, doc 02 section 6: clarification,
never a guess): an unmatched TitleCase run with NO cue word nearby produces
NO customer/port and NO issue -- resolution is only ever attempted when a
cue word (quotes, for/about/on, port of, at) is actually present. This
guards against false "unknown customer" issues on ordinary capitalized
text (a sentence-initial word, an entity name that is not a cue target).
The other direction holds too, on purpose: a cue word PLUS a TitleCase run
that fails to resolve DOES produce an issue -- the cue is exactly what
turns an ordinary sentence into a claim worth checking.

A single-word TitleCase run that is ALSO a calendar month name (see
``_MONTH_WORDS``) is excluded from customer/port detection -- but a LONGER
run starting with one is not. This is what keeps "and for May?" (asking
about the period May, continuing to talk about whatever customer was
already carried) from being swallowed as an attempt to resolve a customer
named "May": ``resolve("May", ...)`` against a pool that happens to contain
a month-named customer ("May Shipping") auto-applies at fuzzy confidence
1.0 -- ``token_set_ratio`` scores a pure token subset almost perfectly --
so without this guard the carried customer would be silently overwritten
and the "May" span would get masked, deleting the very period phrase the
period parser needs to see. "May Shipping" itself (two words) is unaffected
-- the exclusion only ever rejects a run that is EXACTLY one bare month
word. A quoted span is exempt from this guard entirely: explicit quoting is
the strongest possible signal and overrides the heuristic.

Why the port span is blanked first
------------------------------------
"for Port of Singapore" would otherwise read as a customer cue ("for") plus
a one-word TitleCase run ("Port", since "of" breaks the run immediately
after) -- ``resolve("Port", ...)`` would then almost certainly fail
("customer_unknown"), turning the flagship phrase ("Top GP customers for
Port of Singapore in April 2026") into a false clarification. Detecting the
port span FIRST and blanking it out of a scratch copy used only for
customer-cue scanning (never the real working text) removes the false
"Port" candidate regardless of which customer cue word happened to precede
it, without hardcoding "port" as a stopword inside the customer regex.

Masking
-------
Only an AUTO-APPLIED resolution's span is masked; a candidate-band or
unknown result is never masked (that phrase's own issue already flags the
turn for clarification -- a "did you mean...?" is never a silent answer,
so there is nothing to protect it from). Only the CAPTURED phrase span is
blanked, not the cue word ("for "/quotes/"port of "/"at ") -- the cue
carries no period-parsing risk on its own. Documented residual: a customer
mention that CONTAINS a month word but does not auto-apply (e.g. "May
Traders", unresolved) is left in the working text untouched, so its
embedded "May" can still shadow-parse as a bare-month period if a gating
preposition precedes it. Accepted per the plan amendment this task inherits
(see ``period_parser``'s own module docstring, "Known residual"): the turn
already carries a ``customer_unknown``/``customer_ambiguous`` issue, so it
is a clarification turn regardless of which period the shadow-parse landed
on -- never a silently wrong answer.

Slot write-back
-----------------
``SlotUpdates`` is derived from what this turn actually established, per
slot:

* Customer/port: an auto-applied resolution REPLACES the slot with the
  certified value; an explicit "clear the customer"/"reset the port" phrase
  clears it to ``None``; anything else -- nothing detected, or a
  candidate-band/unknown result -- is :data:`~poseidon.core.parsing.carry.
  UNSET` (carry). A mention that fails to resolve deliberately does NOT
  clear the slot: the issue is the signal to the user, not a silent reset
  of context that might still be correct.
* Period: mirrors ``PeriodParse.a_source``/``b_source`` directly --
  ``"text"`` REPLACES the slot with that window's first-of-period start
  date (even when the window is also flagged ``period_unavailable``: the
  user did name a period this turn, in their own words, and persisting it
  is more transparent than silently reverting to something they did not
  ask for); ``"carry"``/``"none"`` are both UNSET, since neither reflects a
  NEW value from this turn's own text. One consequence worth naming: an
  explicit-but-unresolvable phrase (year-to-date at a January 1 reference,
  ``a_source == "none"`` despite text being present -- see
  ``period_parser``'s own docstring) also leaves the slot untouched rather
  than clearing it, for the same "an issue is not a silent reset" reason as
  customer/port above.
* ``mode``/``region``/``topic``/``pass_through`` are never touched here
  (``SlotUpdates`` has no fields for them) -- ``apply_carry`` always carries
  them forward unchanged; ``pass_through`` population is wired by whichever
  later phase makes skills return ranked values.

Customer carry is a detection source, port carry is not
-----------------------------------------------------------
The brief's phrase-detection heuristic list for the customer step
explicitly includes "slot carry" as a third, peer source alongside quoted
spans and the cue-word run; the port step's own heuristic ("port of
X"/"at X") does not. This module follows that literally: when neither a
quoted span nor a cue-run is found in the text, a customer detection still
resolves against the CARRIED ``slots.customer`` name (re-run through
``resolve`` -- trivially an exact match, since a slot can only ever hold an
already-certified value), so ``ParsedTurn.customer`` stays populated across
a pure continuation turn ("and for May?") the same way carried context
stays visible everywhere else in this pipeline. Port has no such
re-resolution step: with no cue in the text, ``ParsedTurn.port`` is simply
``None`` for that turn, while ``ParsedTurn.slots.port`` still correctly
shows the carried name via the ordinary UNSET mechanism. This asymmetry is
a deliberate reading of the brief's literal heuristic list, not an
oversight -- disclosed in the task report.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from poseidon.core.data.client import DataClient
from poseidon.core.parsing.carry import UNSET, SlotUpdates, _UnsetType, apply_carry
from poseidon.core.parsing.customer_resolver import resolve
from poseidon.core.parsing.normalize import normalize
from poseidon.core.parsing.period_parser import parse_periods
from poseidon.core.parsing.skill_hinter import hint
from poseidon.core.parsing.types import ParsedTurn, ParseIssue, ResolvedEntity
from poseidon.core.skills.context import ConversationSlots

DEFAULT_ENTITY = "MARINE_SALES_PLANNING_V"

_CUST_NM = "CUST_NM"
_LOC_NM = "LOC_NM"
_KIND_CUSTOMER = "customer"
_KIND_PORT = "port"

# One or more consecutive capitalized words -- see "Phrase detection" above
# for why the run deliberately does not allow lowercase connectors inside it.
_TITLECASE_WORD = r"[A-Z][\w'-]*"
_TITLECASE_RUN = rf"{_TITLECASE_WORD}(?:\s+{_TITLECASE_WORD})*"

_QUOTED_RE = re.compile(r'"([^"]+)"')
_CUSTOMER_CUE_RE = re.compile(rf"\b(?i:for|about|on)\s+({_TITLECASE_RUN})\b")
_PORT_OF_RE = re.compile(rf"\b(?i:port\s+of)\s+({_TITLECASE_RUN})\b")
_PORT_AT_RE = re.compile(rf"\b(?i:at)\s+({_TITLECASE_RUN})\b")

_CLEAR_CUSTOMER_RE = re.compile(r"\b(?i:clear|reset)\s+(?:the\s+)?customer\b")
_CLEAR_PORT_RE = re.compile(r"\b(?i:clear|reset)\s+(?:the\s+)?port\b")

# Calendar month words (full and abbreviated), used only to keep a BARE,
# single-word TitleCase run from being misread as a customer/port name (see
# "Phrase detection" above). Deliberately independent of ``period_parser``'s
# own private ``_MONTHS`` table -- this module only needs "is this one word a
# month name", not the parser's full month grammar, and importing another
# module's private constant would silently couple the two on an
# implementation detail neither documents as a public contract.
_MONTH_WORDS = frozenset(
    {
        "jan",
        "january",
        "feb",
        "february",
        "mar",
        "march",
        "apr",
        "april",
        "may",
        "jun",
        "june",
        "jul",
        "july",
        "aug",
        "august",
        "sep",
        "sept",
        "september",
        "oct",
        "october",
        "nov",
        "november",
        "dec",
        "december",
    }
)


@dataclass(frozen=True)
class _Detected:
    """A phrase to resolve, plus where it came from.

    ``span`` is the phrase's ``(start, end)`` offset in the CURRENT turn's
    normalized text, used both to blank a colliding cue (see the port/
    customer scan in ``_detect_customer``) and, later, to mask an
    auto-applied resolution before period parsing. ``span`` is ``None`` for
    a carry-derived customer phrase: it names no span in THIS turn's text,
    so there is nothing to blank and nothing to mask.
    """

    phrase: str
    span: tuple[int, int] | None


@dataclass(frozen=True)
class _SlotPlan:
    """One slot's (customer's or port's) complete outcome for this turn."""

    entity: ResolvedEntity | None
    mask_span: tuple[int, int] | None
    issue: ParseIssue | None
    slot_update: str | None | _UnsetType


# ---------------------------------------------------------------------------
# Phrase detection
# ---------------------------------------------------------------------------


def _is_bare_month_word(run: str) -> bool:
    """Whether ``run`` is EXACTLY one word and that word is a calendar month
    name -- the guard described in the module docstring's "Phrase detection"
    section. A run of two or more words (``" " in run``) is never excluded,
    however it starts."""
    return " " not in run and run.casefold() in _MONTH_WORDS


def _titlecase_run_match(
    pattern: re.Pattern[str], text: str
) -> tuple[_Detected, re.Match[str]] | None:
    """The first match of ``pattern`` in ``text`` whose captured TitleCase
    run is not a bare month word -- the ``_Detected`` (captured phrase only,
    for resolution/masking) alongside the raw ``re.Match`` (whole cue+run
    span, for callers that need to exclude more than just the captured
    name -- see ``_detect_port``).

    A rejected (bare-month) match does not end the scan -- ``finditer``
    tries the next occurrence, mirroring ``period_parser._parse_side``'s
    identical "skip, don't stop" rule for its own gated bare-month branch:
    a real name later in the same text is still found.
    """
    for match in pattern.finditer(text):
        run = match.group(1)
        if _is_bare_month_word(run):
            continue
        return _Detected(run, (match.start(1), match.end(1))), match
    return None


def _quoted_match(text: str) -> _Detected | None:
    """A quoted span, anywhere in the text -- the quotes are themselves an
    unambiguous cue, so no adjacent cue word and no TitleCase requirement."""
    match = _QUOTED_RE.search(text)
    if match is None:
        return None
    return _Detected(match.group(1), (match.start(1), match.end(1)))


def _detect_port(text: str) -> tuple[_Detected, tuple[int, int]] | None:
    """ "port of X" first, then "at X" -- the brief's own cue ordering.

    Returns the detected phrase (captured name only) alongside the WHOLE
    cue+run match span -- the latter is what ``_detect_customer`` blanks out
    of its scratch copy, and it must cover the cue word too ("Port of"), not
    just the captured name, or "for Port of Singapore" would still read as
    customer cue "for" + a false one-word TitleCase run "Port"; see the
    module docstring's "Why the port span is blanked first".
    """
    for pattern in (_PORT_OF_RE, _PORT_AT_RE):
        found = _titlecase_run_match(pattern, text)
        if found is not None:
            detected, match = found
            return detected, (match.start(0), match.end(0))
    return None


def _blank(text: str, span: tuple[int, int]) -> str:
    """``span`` replaced with spaces of the same length -- every other
    offset in ``text`` stays stable."""
    start, end = span
    return text[:start] + (" " * (end - start)) + text[end:]


def _detect_customer(
    text: str, port_exclude_span: tuple[int, int] | None, slots: ConversationSlots
) -> _Detected | None:
    """Quoted span, then cue-run, then carried slot -- see the module
    docstring's "Phrase detection" and "Customer carry is a detection
    source, port carry is not" sections.

    ``port_exclude_span`` is the port detection's WHOLE cue+run span (see
    ``_detect_port``), blanked out of a scratch copy before the cue-run scan
    only -- it never touches ``text`` itself, so the real working text (and
    any span this function returns) is unaffected.
    """
    quoted = _quoted_match(text)
    if quoted is not None:
        return quoted

    scan_text = _blank(text, port_exclude_span) if port_exclude_span is not None else text
    cued = _titlecase_run_match(_CUSTOMER_CUE_RE, scan_text)
    if cued is not None:
        return cued[0]

    if slots.customer is not None:
        return _Detected(slots.customer, None)
    return None


# ---------------------------------------------------------------------------
# Resolution + write-back planning
# ---------------------------------------------------------------------------


def _plan_resolution(
    detected: _Detected | None, clear_requested: bool, values: Sequence[str], kind: str
) -> _SlotPlan:
    """One slot's full resolve-and-write-back plan; see the module
    docstring's "Slot write-back" section for the rule this implements."""
    if clear_requested:
        return _SlotPlan(entity=None, mask_span=None, issue=None, slot_update=None)
    if detected is None:
        return _SlotPlan(entity=None, mask_span=None, issue=None, slot_update=UNSET)

    resolution = resolve(detected.phrase, values, kind)
    if resolution.entity is not None:
        return _SlotPlan(
            entity=resolution.entity,
            mask_span=detected.span,
            issue=None,
            slot_update=resolution.entity.value,
        )
    # Candidate-band or unknown: never masked, never clears the slot -- the
    # issue is the signal, not a silent overwrite of context that might
    # still be correct.
    return _SlotPlan(entity=None, mask_span=None, issue=resolution.issue, slot_update=UNSET)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_turn(
    message: str,
    slots: ConversationSlots,
    reference_date: date,
    data: DataClient,
    entity: str = DEFAULT_ENTITY,
) -> ParsedTurn:
    """Resolve one inbound chat message into a :class:`ParsedTurn`.

    See the module docstring for the full order, the masking rule, the
    conservative-detection guarantee, and the slot write-back mapping.
    ``message`` is raw user text -- this function normalizes it itself.
    """
    normalized = normalize(message)

    port_clear = _CLEAR_PORT_RE.search(normalized) is not None
    port_found = None if port_clear else _detect_port(normalized)
    port_detected = port_found[0] if port_found is not None else None
    port_exclude_span = port_found[1] if port_found is not None else None

    customer_clear = _CLEAR_CUSTOMER_RE.search(normalized) is not None
    customer_detected = (
        None if customer_clear else _detect_customer(normalized, port_exclude_span, slots)
    )

    customer_values = (
        data.list_dimension_values(entity, _CUST_NM) if customer_detected is not None else ()
    )
    customer_plan = _plan_resolution(
        customer_detected, customer_clear, customer_values, _KIND_CUSTOMER
    )

    port_values = data.list_dimension_values(entity, _LOC_NM) if port_detected is not None else ()
    port_plan = _plan_resolution(port_detected, port_clear, port_values, _KIND_PORT)

    working_text = normalized
    for span in (customer_plan.mask_span, port_plan.mask_span):
        if span is not None:
            working_text = _blank(working_text, span)

    available = data.available_periods(entity)
    period_parse = parse_periods(working_text, slots, reference_date, available)

    updates = SlotUpdates(
        customer=customer_plan.slot_update,
        port=port_plan.slot_update,
        period_a=(period_parse.period_a.start if period_parse.a_source == "text" else UNSET),
        period_b=(period_parse.period_b.start if period_parse.b_source == "text" else UNSET),
    )
    post_carry_slots = apply_carry(slots, updates)

    hints = hint(normalized, post_carry_slots)

    issues = tuple(
        issue
        for issue in (customer_plan.issue, port_plan.issue, period_parse.issue)
        if issue is not None
    )

    return ParsedTurn(
        normalized_text=normalized,
        slots=post_carry_slots,
        period_a=period_parse.period_a,
        period_b=period_parse.period_b,
        customer=customer_plan.entity,
        port=port_plan.entity,
        hints=hints,
        issues=issues,
    )
