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
2. Detect and resolve the PORT: every "port of X" (strong), "at X" and
   "in X" (weak) cue followed by a TitleCase run or a short lowercase
   phrase is a candidate, tried in that priority order; the first one that
   is a claim (it resolves, or its miss keeps an issue) decides the port
   (see "Phrase detection" and "Strong and weak port cues" below).
3. Detect a CUSTOMER phrase, in priority order: a quoted span, then a
   "for/about/on X" cue + TitleCase run (scanned over a scratch copy with
   the claimed port span already blanked out -- see "Why the port span is
   blanked first"), then -- only if neither found anything -- the carried
   ``slots.customer`` itself, used AS the phrase. An explicit "clear the
   customer"/"reset the customer" phrase short-circuits this entirely.
4. Resolve the customer via ``customer_resolver.resolve``. Both dimension
   pools (``data.list_dimension_values``) are fetched LAZILY and at most
   once per turn -- the port pool only when step 2 found a candidate
   phrase, the customer pool only when step 3 found a phrase or step 2's
   customer-like guard needed it -- so an ordinary message that names
   neither never pays for either fetch. Each AUTO-APPLIED resolution's span
   is recorded for masking; a candidate-band or unknown result is not (it
   produces an issue, or for a weak port cue possibly nothing -- see
   "Masking" below). A carry-derived customer phrase has no span in THIS
   turn's text, so it is never a masking candidate either way.
5. Mask every recorded span out of a working copy of the text (spaces,
   same length, so every other offset stays stable) before period parsing.
6. Parse periods over the MASKED working text, via
   ``period_parser.parse_periods(working_text, slots, reference_date,
   data.available_periods(entity))``. The masked text is NOT itself
   normalized -- masking replaces a span with spaces of the same length, so
   it deliberately leaves a run of spaces behind, which is exactly what
   normalized text does not contain. What makes handing it over safe is
   that ``parse_periods`` runs ``normalize`` on its own input, collapsing
   that space run before it parses anything; the safety is that second
   normalize pass, not any idempotence property of the masking step.
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
cue word (quotes, for/about/on, port of, at, in) is actually present. This
guards against false "unknown customer" issues on ordinary capitalized
text (a sentence-initial word, an entity name that is not a cue target).
The other direction holds too, on purpose: a STRONG cue word (quotes,
for/about/on, port of) PLUS a TitleCase run that fails to resolve DOES
produce an issue -- the cue is exactly what turns an ordinary sentence into
a claim worth checking. The weak "at X"/"in X" port cues are the
exception: whether their miss is worth a question depends on the phrase;
see "Strong and weak port cues" below.

Lowercase phrases are a PORT-only detection path. Customer names typed in
lowercase after "for" are still never detected (``_CUSTOMER_CUE_RE``
needs a TitleCase run) -- the symmetric change is a parked carry-forward
(P16-R23 plan, Decision 5), not an oversight.

The run regex is ASCII-only (``[A-Z][\\w'-]*`` -- the ``[A-Z]`` initial
does not include accented capitals), so a name whose first letter is
non-ASCII (an "Elan Maritime" spelled with a capital E-acute) is silently
undetectable today: no customer, no port, no issue. Harmless at the moment
-- all 40 seeded CUST_NM and 30 seeded LOC_NM values are ASCII, verified
against the live seed -- and widening the class is a certification-time
concern, to be decided against real (non-synthetic) dimension data rather
than guessed at now.

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

Strong and weak port cues
---------------------------
Three cue words, case-insensitive, tried in priority order: "port of"
(STRONG), then "at" (weak), then "in" (weak). Every occurrence of every cue
is a candidate when a phrase follows it:

* **TitleCase phrase**: a capitalised word right after the cue starts a
  TitleCase run, captured exactly as in "Phrase detection" (a bare month
  word is skipped).
* **Lowercase (or mixed) phrase**: otherwise, up to 3 words after the cue,
  stopping at the first stop token -- any punctuation mark; the words in,
  on, at, for, of, and, by, during, from, to, vs, versus, with, than, the,
  a, an, this, last, next, previous, past, ytd, mtd, qtd; a ``q[1-4]``,
  ``h[1-2]`` or ``fy\\d*`` token; a four-digit year; a month word; or a
  metric/unit/measure word (``_METRIC_STOP_WORDS``: tons, usd, gross,
  prices, volume and the like, which fuzzy-collide with seeded port names
  such as Santos, Lagos and Piraeus). A cue
  whose FIRST following word is a stop token has no phrase at all ("in the
  last quarter", "in april 2026", "at the moment"), and when no cue has a
  phrase the port pool is never fetched.

How a phrase resolves (``customer_resolver.resolve``):

* A TitleCase run after "port of" or "at" -- the two paths that predate
  P16-R23 -- resolves in the default mode: a unique fuzzy match >= 0.80
  still auto-applies ("at Singpore" applies Singapore, as it always did).
* Everything newer -- a TitleCase run after "in", and every lowercase
  phrase after any cue -- resolves in STRICT mode
  (``auto_apply_fuzzy=False``): only an exact or word-set match applies; a
  fuzzy match, however strong, asks "did you mean". A lowercase phrase is
  tried whole, then as its shorter prefixes ("in hong kong today"
  resolves "hong kong"); the first prefix that matches exact/token wins and
  only ITS span is masked. If none does, the full phrase's own result
  (candidates or unknown) stands.

Issue retention -- what a phrase that did NOT resolve does to the turn:

* **Candidates (a near-miss) on ANY cue**: the ``customer_ambiguous``
  issue is kept, with chips. A phrase that scores within reach of a real
  port is worth one question ("in singpore", "at Rottervale").
* **Unknown after "port of"**: kept (``customer_unknown``). Nobody writes
  "port of" by accident.
* **Unknown after "at"/"in", TitleCase phrase**: kept ONLY IF the phrase
  is not period-like (its first word is not a ``q[1-4]``/``h[1-2]``/
  ``fy\\d*`` token, a four-digit year, a month word, or week, quarter,
  year, ytd, mtd, qtd) AND it does not match the CUSTOMER pool (exact/
  token, or fuzzy >= ``CANDIDATE_THRESHOLD``). So "in Narnia" asks which
  port, while "gp at Q3" (a period) and "take a look at Northstar Lines"
  (a customer) stay silent. The customer pool is fetched for this check
  only once every cheaper condition has already passed.
* **Unknown after "at"/"in", lowercase phrase**: dropped, silently. "in
  general", "at risk", "in total" are English, not failed port lookups.

Every kept port issue carries ``kind="port"`` and ``phrase=<the full
captured phrase>`` (``ParseIssue``), so the orchestrator can tell a port
question from a customer one -- the CODES stay ``customer_*``.

The first candidate that is a CLAIM (it resolved, or its miss kept an
issue) decides the turn; a silent miss is skipped, so "gross profit at
risk in singapore" still finds Singapore. Known gaps, accepted for now: a
capitalised non-port place after a weak cue ("in Europe", "in APAC") asks
"which port?", because it is neither period-like nor customer-like; and a
lowercase English word outside the stop lists that happens to sit within
fuzzy reach of a real port name is asked about rather than ignored (the
known metric/unit collisions -- "in tons" scores 0.80 against "Santos" --
are stop words for exactly that reason).

Why the port span is blanked first
------------------------------------
"for Port of Singapore" would otherwise read as a customer cue ("for") plus
a one-word TitleCase run ("Port", since "of" breaks the run immediately
after) -- ``resolve("Port", ...)`` would then almost certainly fail
("customer_unknown"), turning the flagship phrase ("Top GP customers for
Port of Singapore in April 2026") into a false clarification. Settling the
port FIRST and blanking its claimed span (cue word through the phrase
used) out of a scratch copy used only for customer-cue scanning (never the
real working text) removes the false "Port" candidate regardless of which
customer cue word happened to precede it, without hardcoding "port" as a
stopword inside the customer regex. Only a CLAIMED candidate is blanked: a
silent weak-cue miss leaves the scan text as it was.

Known limitation, quoted spans: the quoted-span branch of
``_detect_customer`` runs BEFORE the port blanking is consulted (quotes win
outright, by design -- see "Phrase detection"), so quoting a port name
('gp at "Port of Singapore"') resolves the port correctly AND hands
"Port of Singapore" to the customer resolver as a quoted phrase, which
misses and raises a spurious ``customer_unknown`` beside the correct port.
Documented rather than fixed: the reorder that would fix it (blank the port
span before the quoted-span search too) changes which phrase wins for every
quoted input, and that surface is not worth disturbing for a phrasing
nobody has actually used. Ordinary '... at Port of Singapore' without the
quotes is unaffected.

Masking
-------
Only an AUTO-APPLIED resolution's span is masked; a candidate-band or
unknown result is never masked -- whether its issue was kept (a "did you
mean...?" is never a silent answer, so there is nothing to protect it from)
or dropped as a silent weak-cue miss (a lowercase capture stops before any
month, year or quarter token, so there is no period text inside it to
protect). For a lowercase port phrase that resolved on a shorter prefix,
only that prefix is masked ("hong kong" in "in hong kong today"). Only the
CAPTURED phrase span is blanked, not the cue word ("for "/quotes/"port of
"/"at "/"in ") -- the cue carries no period-parsing risk on its own. Documented residual: a customer
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
* ``period_a``/``period_a_end``: ``a_source == "text"`` REPLACES both with
  the window's start and exclusive end (even when the window is also
  flagged ``period_unavailable``: the user named a period this turn, in
  their own words, and persisting it is more transparent than silently
  reverting to something they did not ask for); ``"carry"``/``"none"`` are
  UNSET, since neither reflects a NEW value from this turn's text. An
  explicit-but-unresolvable phrase (year-to-date at a January 1 reference)
  therefore leaves the slot untouched rather than clearing it -- an issue
  is not a silent reset.
* ``period_b``/``period_b_end`` (decision D62) follow ``b_source`` alone:
  ``"text"`` REPLACES both with the window's start and exclusive end;
  ``"carry"`` leaves both UNSET, so a carried comparison survives "why did
  GP drop?" and an unresolvable "compared to acme corp"; ``"none"`` CLEARS
  both to ``None``. ``period_parser`` reports ``"none"`` whenever this turn
  names a left-hand period but no resolvable comparison -- "GP for August"
  after "July vs June" is a question about August alone, and so is an
  unresolvable "gp ytd" on January 1. The slot is written from the very
  value ``ParsedTurn.period_b`` reports (a carry keeps the slot the parser
  carried from), so ``ParsedTurn.period_b`` and ``ParsedTurn.slots.
  period_b`` agree on every turn by construction.
* ``mode``/``region``/``topic``/``pass_through`` are never touched here
  (``SlotUpdates`` has no fields for them) -- ``apply_carry`` always carries
  them forward unchanged; ``pass_through`` population is wired by whichever
  later phase makes skills return ranked values.

Customer carry is a detection source, port carry is not
-----------------------------------------------------------
The brief's phrase-detection heuristic list for the customer step
explicitly includes "slot carry" as a third, peer source alongside quoted
spans and the cue-word run; the port step's own heuristic ("port of
X"/"at X"/"in X") does not. This module follows that literally: when neither a
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
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
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

# Port cues, in priority order (see "Strong and weak port cues"). Each regex
# matches the cue word plus the whitespace after it; the phrase itself is
# captured by ``_port_candidates``, because a TitleCase phrase and a
# lowercase one are captured by different rules.
_CUE_PORT_OF = "port of"
_CUE_AT = "at"
_CUE_IN = "in"
_PORT_CUES = (
    (_CUE_PORT_OF, re.compile(r"\b(?i:port\s+of)\s+")),
    (_CUE_AT, re.compile(r"\b(?i:at)\s+")),
    (_CUE_IN, re.compile(r"\b(?i:in)\s+")),
)
_TITLECASE_RUN_RE = re.compile(rf"({_TITLECASE_RUN})\b")

# A lowercase (or mixed-case) port phrase is at most this many words.
_LOWERCASE_MAX_WORDS = 3
# One token of a lowercase capture: a word, or a single punctuation mark
# (every punctuation mark ends the capture).
_CAPTURE_TOKEN_RE = re.compile(r"\w[\w'-]*|[^\w\s]")
# Words that end a lowercase capture (and, as the FIRST word, mean there is
# no phrase at all -- the port pool is then never fetched).
_CAPTURE_STOP_WORDS = frozenset(
    {
        "in",
        "on",
        "at",
        "for",
        "of",
        "and",
        "by",
        "during",
        "from",
        "to",
        "vs",
        "versus",
        "with",
        "than",
        "the",
        "a",
        "an",
        "this",
        "last",
        "next",
        "previous",
        "past",
        "ytd",
        "mtd",
        "qtd",
    }
)
# Metric/unit/measure words that fuzzy-collide with port names ("tons" ~
# Santos, "gross" ~ Lagos, "prices" ~ Piraeus); found by scoring sales words
# against the seeded LOC_NM pool. They end a lowercase capture like any stop
# word, so "volume in tons" has no port phrase at all.
_METRIC_STOP_WORDS = frozenset(
    {
        "tons",
        "ton",
        "tonnes",
        "tonne",
        "mt",
        "metric",
        "usd",
        "dollars",
        "dollar",
        "euros",
        "gross",
        "net",
        "price",
        "prices",
        "terms",
        "percent",
        "pct",
        "barrels",
        "bbl",
        "units",
        "volume",
        "volumes",
        "revenue",
        "profit",
        "margin",
    }
)
# Quarter, half, fiscal-year and four-digit-year tokens: period words that
# end a capture and make a capitalised phrase "period-like".
_PERIOD_TOKEN_RE = re.compile(r"q[1-4]|h[12]|fy\d*|\d{4}", re.IGNORECASE)

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

# First words that make a capitalised weak-cue phrase "period-like" (so an
# unknown "at Q3"/"in FY2026" is a period, never a "which port?" question),
# on top of every ``_PERIOD_TOKEN_RE`` match and every month word.
_PERIOD_LIKE_WORDS = _MONTH_WORDS | frozenset({"week", "quarter", "year", "ytd", "mtd", "qtd"})


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
class _PortCandidate:
    """One cue + phrase pair the port step will try to resolve.

    ``options`` is the phrase to resolve, longest first: one entry for a
    TitleCase run, and for a lowercase capture of N words the N-, (N-1)-,
    ..., 1-word prefixes (see "Strong and weak port cues"). ``options[0]``
    is always the full captured phrase.
    """

    cue: str  # _CUE_PORT_OF | _CUE_AT | _CUE_IN
    cue_start: int
    capitalised: bool
    options: tuple[_Detected, ...]


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
    span, for callers that need more than just the captured name).

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


def _is_capture_stop(token: str) -> bool:
    """Whether ``token`` ends a lowercase capture: any punctuation mark, a
    stop word (including a metric/unit word), a month word, or a
    quarter/half/fiscal/year token."""
    if not token[0].isalnum() and token[0] != "_":
        return True
    folded = token.casefold()
    return (
        folded in _CAPTURE_STOP_WORDS
        or folded in _METRIC_STOP_WORDS
        or folded in _MONTH_WORDS
        or _PERIOD_TOKEN_RE.fullmatch(folded) is not None
    )


def _lowercase_options(text: str, start: int) -> tuple[_Detected, ...]:
    """The lowercase capture at ``start`` as its prefixes, longest first --
    up to ``_LOWERCASE_MAX_WORDS`` words, stopping at the first stop token.
    Empty when the very first token is a stop token."""
    words: list[re.Match[str]] = []
    for token in _CAPTURE_TOKEN_RE.finditer(text, start):
        if _is_capture_stop(token.group(0)):
            break
        words.append(token)
        if len(words) == _LOWERCASE_MAX_WORDS:
            break
    phrase_start = words[0].start() if words else start
    return tuple(
        _Detected(text[phrase_start : words[n - 1].end()], (phrase_start, words[n - 1].end()))
        for n in range(len(words), 0, -1)
    )


def _port_candidates(text: str) -> list[_PortCandidate]:
    """Every cue + phrase pair in ``text``, in priority order: all "port of"
    occurrences, then all "at", then all "in", each in text order.

    A capitalised word after the cue starts a TitleCase run (a bare month
    word is skipped, see "Phrase detection"); anything else starts a
    lowercase capture. A cue with no phrase after it is no candidate at all.
    Detection only -- nothing here touches a ``DataClient``.
    """
    candidates: list[_PortCandidate] = []
    for cue, pattern in _PORT_CUES:
        for match in pattern.finditer(text):
            pos = match.end()
            if pos >= len(text):
                continue
            if "A" <= text[pos] <= "Z":
                run = _TITLECASE_RUN_RE.match(text, pos)
                if run is None or _is_bare_month_word(run.group(1)):
                    continue
                options = (_Detected(run.group(1), (run.start(1), run.end(1))),)
                candidates.append(_PortCandidate(cue, match.start(), True, options))
                continue
            options = _lowercase_options(text, pos)
            if options:
                candidates.append(_PortCandidate(cue, match.start(), False, options))
    return candidates


def _is_period_like(phrase: str) -> bool:
    """Whether ``phrase``'s first word reads as a period ("Q3", "FY2026",
    "2026", "April ...", "Quarter ...") rather than a place name."""
    first = phrase.split(" ", 1)[0].casefold()
    return first in _PERIOD_LIKE_WORDS or _PERIOD_TOKEN_RE.fullmatch(first) is not None


def _is_customer_like(phrase: str, customer_values: Sequence[str]) -> bool:
    """Whether ``phrase`` names (or nearly names) a customer: an exact/token
    hit, or any fuzzy score >= ``CANDIDATE_THRESHOLD``."""
    resolution = resolve(phrase, customer_values, _KIND_CUSTOMER)
    return resolution.entity is not None or bool(resolution.candidates)


def _keeps_port_issue(
    candidate: _PortCandidate,
    candidates_found: bool,
    customer_pool: Callable[[], Sequence[str]],
) -> bool:
    """The issue-retention rule for a port phrase that did not resolve; see
    "Strong and weak port cues". The customer pool is fetched only when
    every cheaper condition has already passed."""
    if candidates_found or candidate.cue == _CUE_PORT_OF:
        return True
    if not candidate.capitalised:
        return False
    phrase = candidate.options[0].phrase
    if _is_period_like(phrase):
        return False
    return not _is_customer_like(phrase, customer_pool())


def _plan_port(
    candidates: Sequence[_PortCandidate],
    port_pool: Callable[[], Sequence[str]],
    customer_pool: Callable[[], Sequence[str]],
) -> tuple[_SlotPlan, tuple[int, int] | None]:
    """The port slot's plan plus the span ``_detect_customer`` must blank.

    Candidates are tried in priority order; the first one that is a CLAIM --
    it resolves, or its miss keeps an issue -- decides the turn. A silent
    miss ("in total", "at Q3") is skipped so a later real port is still
    found. The returned span covers the cue word through the phrase used
    (see "Why the port span is blanked first"); ``None`` when nothing was
    claimed.
    """
    for candidate in candidates:
        # Strict for everything except the two pre-P16-R23 paths (a
        # TitleCase run after "port of" or "at"), which keep auto-applying a
        # unique fuzzy match.
        strict = candidate.cue == _CUE_IN or not candidate.capitalised
        values = port_pool()
        # Longest first; a shorter prefix only ever wins on exact/token
        # (strict mode never auto-applies fuzzy). Lazy, so a full-phrase
        # hit never resolves its prefixes.
        results = (
            (option, resolve(option.phrase, values, _KIND_PORT, auto_apply_fuzzy=not strict))
            for option in candidate.options
        )
        full = candidate.options[0]
        full_result = None
        for option, result in results:
            if result.entity is not None:
                plan = _SlotPlan(
                    entity=result.entity,
                    mask_span=option.span,
                    issue=None,
                    slot_update=result.entity.value,
                )
                return plan, (candidate.cue_start, option.span[1])
            full_result = full_result or result
        if full_result is None or full_result.issue is None:  # pragma: no cover - resolve contract
            continue
        if _keeps_port_issue(candidate, bool(full_result.candidates), customer_pool):
            issue = replace(full_result.issue, kind=_KIND_PORT, phrase=full.phrase)
            plan = _SlotPlan(entity=None, mask_span=None, issue=issue, slot_update=UNSET)
            return plan, (candidate.cue_start, full.span[1])
    return _SlotPlan(entity=None, mask_span=None, issue=None, slot_update=UNSET), None


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

    ``port_exclude_span`` is the port step's WHOLE cue+phrase span (see
    ``_plan_port``), blanked out of a scratch copy before the cue-run scan
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
    # still be correct. The issue records the detected phrase (P16-R24), as
    # a port issue does, so a clarify chip can rewrite the user's question.
    issue = replace(resolution.issue, phrase=detected.phrase) if resolution.issue else None
    return _SlotPlan(entity=None, mask_span=None, issue=issue, slot_update=UNSET)


def _lazy_pool(data: DataClient, entity: str, column: str) -> Callable[[], Sequence[str]]:
    """A zero-argument fetcher for one dimension pool that calls the
    ``DataClient`` at most once, and only if somebody asks."""
    cache: list[Sequence[str]] = []

    def fetch() -> Sequence[str]:
        if not cache:
            cache.append(data.list_dimension_values(entity, column))
        return cache[0]

    return fetch


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
    customer_pool = _lazy_pool(data, entity, _CUST_NM)
    port_pool = _lazy_pool(data, entity, _LOC_NM)

    # Port first: its claimed span is what the customer cue scan blanks.
    port_clear = _CLEAR_PORT_RE.search(normalized) is not None
    if port_clear:
        port_plan = _plan_resolution(None, True, (), _KIND_PORT)
        port_exclude_span = None
    else:
        port_plan, port_exclude_span = _plan_port(
            _port_candidates(normalized), port_pool, customer_pool
        )

    customer_clear = _CLEAR_CUSTOMER_RE.search(normalized) is not None
    customer_detected = (
        None if customer_clear else _detect_customer(normalized, port_exclude_span, slots)
    )

    customer_values = customer_pool() if customer_detected is not None else ()
    customer_plan = _plan_resolution(
        customer_detected, customer_clear, customer_values, _KIND_CUSTOMER
    )

    working_text = normalized
    for span in (customer_plan.mask_span, port_plan.mask_span):
        if span is not None:
            working_text = _blank(working_text, span)

    available = data.available_periods(entity)
    period_parse = parse_periods(working_text, slots, reference_date, available)

    # Slot write-back for periods (decision D62; see "Slot write-back" in
    # the module docstring). A: a left-hand phrase replaces, anything else
    # carries. B follows b_source alone: text replaces, carry keeps, none
    # clears -- so the stored comparison is the parsed one by construction.
    a_named = period_parse.a_source == "text"
    if period_parse.b_source == "text":
        period_b_update = period_parse.period_b.start
        period_b_end_update = period_parse.period_b.end
    elif period_parse.b_source == "carry":
        period_b_update = UNSET
        period_b_end_update = UNSET
    else:
        period_b_update = None
        period_b_end_update = None

    updates = SlotUpdates(
        customer=customer_plan.slot_update,
        port=port_plan.slot_update,
        period_a=period_parse.period_a.start if a_named else UNSET,
        period_a_end=period_parse.period_a.end if a_named else UNSET,
        period_b=period_b_update,
        period_b_end=period_b_end_update,
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
