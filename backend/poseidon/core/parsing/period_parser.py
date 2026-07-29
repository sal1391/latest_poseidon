"""The deterministic period parser (doc 02 section 5): the stage that turns
date prose into the ``{period_a, period_b}`` pair every downstream skill
needs -- no LLM call, no network, no wall clock.

Everything resolves against the caller's ``reference_date``. This module
constructs ``date`` values but never reads ``date.today()``: "now" is an
argument, which is what makes the whole grammar table-testable.

``parse_periods`` normalizes and casefolds its own input (callers hand it raw
user text, not pre-cleaned text), so every pattern below matches clean,
lowercase, single-spaced text.

Grammar
-------
Only the FIRST period phrase on each side of a comparison is used; a second
phrase on the same side is ignored.

* **Month-year** -- ``april 2026``, ``apr 2026`` (a trailing ``.`` on the
  abbreviation is allowed), ``2026-04``. Day-level periods are out of scope
  in v1, so a full ISO date (``2026-04-15``) resolves to its MONTH.
* **Bare month** -- ``in april``: the most recent occurrence that has already
  STARTED, i.e. the reference year when the month's first day is at or before
  the reference month's first day, otherwise the previous year. At a
  2026-07-15 reference, ``in july`` is July 2026 and ``in december`` is
  December 2025. Note the consequence: the returned window may extend past
  the reference date (July 2026 runs to August 1), because a period is
  returned whole, never truncated to "so far". One month word is gated --
  see AMBIGUOUS BARE MONTHS below.
* **Year** -- a bare ``2025`` (only 1900-2099 are read as years, so ``1500
  tonnes`` is a quantity); ``last year``/``prior year``/``previous year`` for
  the prior FULL calendar year; ``this year``/``ytd``/``year to date``/
  ``year-to-date`` for ``[Jan 1 of the reference year, reference_date)``.
* **Quarters** -- ``q1 2026``, or a bare ``q3`` under the same most-recent
  rule as a bare month (compared on the quarter's first month).
* **Comparison** -- ``vs``/``versus``/``compared to`` splits the text once;
  the left side resolves ``period_a`` and the right side ``period_b``. When
  the right side is only a "one year back" phrase (``vs last year``) and
  ``period_a`` resolved, ``period_b`` becomes ``period_a`` shifted back one
  year IN THE SAME SHAPE -- a month stays a month, a quarter a quarter, a
  year-to-date window keeps its partial-year cut-off. The shift rebuilds the
  window from its first-of-period anchor rather than moving raw bounds; the
  one bound that is not a first-of-period date (a year-to-date end) is
  day-clamped, so a February 29 reference shifts to February 28.
  ``prior year vs ytd`` needs no shift -- both sides name a period.
* **Carry-over** -- when the text names no period on the left, ``period_a``
  falls back to ``slots.period_a`` (source ``"carry"``); a new left-hand
  phrase replaces it (source ``"text"``). ``period_b`` is NEVER carried: a
  comparison has to be asked for again, so ``b_source`` is only ever
  ``"text"`` or ``"none"``.
* **Validation** -- a resolved window that does not intersect ``available``
  still comes back, alongside a ``period_unavailable`` issue. ``PeriodRange``
  ends INCLUSIVE while ``PeriodWindow`` ends EXCLUSIVE, so the availability
  end is advanced by one day before the intersection test; without that,
  a window starting on the last available day would look empty.

``a_source``/``b_source`` are ``"none"`` exactly when the matching window is
``None`` -- a source describes where a window came from, so there is no such
thing as a ``"text"`` source with nothing to show for it.

Only one ``ParseIssue`` fits in a ``PeriodParse``, so when several apply the
order is: a phrase that could not resolve at all (year-to-date at a January 1
reference) first, then a ``period_a`` availability miss, then ``period_b``'s.

CARRIED GRANULARITY -- KNOWN v1 LIMITATION
------------------------------------------
``ConversationSlots.period_a`` stores a first-of-period DATE, not a window,
so the carry path cannot know which granularity produced it and always
reconstructs the MONTH containing that date. If the previous turn parsed a
quarter or a year, Phase 6 stores that period's first month and this parser
carries a month -- the carried period silently degrades in granularity. It is
deliberate for v1 and deliberately silent: no ``ParseIssue`` is emitted for
it, because nothing about the current turn is wrong. Widening the slot to
carry a granularity (or the window itself) is the fix, and it belongs to
whichever phase revisits ``ConversationSlots``, not here.

AMBIGUOUS BARE MONTHS
---------------------
``may`` is a month AND the modal verb that opens most polite requests, so a
BARE ``may`` is accepted only when the word immediately before it is one of
``in``/``for``/``during``/``of``/``since``/``through``. Otherwise the token
is not a period phrase at all: the text reads on exactly as if the word were
absent, so ``may i see top customers`` carries the previous turn's period or
returns none instead of silently scoping the answer to May. The cost is
deliberate -- ``show me may`` no longer parses, and a non-parse the user can
recover from beats a silently wrong answer. ``AMBIGUOUS_BARE_MONTHS`` is the
extension point; ``may 2026`` and every other month are untouched.

Known residual: a customer whose name is a month (``May Shipping``) still
collides when a preposition precedes it. Masking customer spans before the
period parse is the pipeline's concern, not this module's.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta

from poseidon.core.data.client import PeriodRange
from poseidon.core.data.specs import PeriodWindow
from poseidon.core.parsing.normalize import normalize
from poseidon.core.parsing.types import ParseIssue
from poseidon.core.skills.context import ConversationSlots

_SOURCE_TEXT = "text"
_SOURCE_CARRY = "carry"
_SOURCE_NONE = "none"

_ISSUE_CODE = "period_unavailable"

# Pinned in tests: the mirror of the P3 ``fetch_metrics`` contract, which
# refuses a January 1 anchor for the same reason -- [Jan 1, Jan 1) is empty.
_YTD_AT_YEAR_START = "year-to-date has no days before January 2"

_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}

# Longest name first so ``december`` is tried before ``dec``; ties broken
# alphabetically to keep the compiled pattern byte-stable across runs.
_MONTH_ALT = "|".join(sorted(_MONTHS, key=lambda name: (-len(name), name)))

# Month words that need a preposition in front of them before a BARE
# occurrence counts as a date. Only ``may`` for v1: it is the modal auxiliary
# that opens polite requests constantly ("may i see ..."), while the other
# homographs ("march", "august") only collide in contrived sales questions --
# and gating those would break the perfectly plausible "show me march". This
# is the extension point if real traffic turns up another offender.
AMBIGUOUS_BARE_MONTHS = frozenset({"may"})

# The words that make a gated month word read as a date rather than as
# itself. Kept to prepositions that can only introduce a period.
_BARE_MONTH_PREPOSITIONS = frozenset({"in", "for", "during", "of", "since", "through"})

# ``re`` has no variable-length lookbehind, so the gate is a post-match check
# on the match offset: the last word token before the match, with whatever
# whitespace and punctuation sits between them skipped.
_PRECEDING_WORD_RE = re.compile(r"(\w+)\W*$")

# 1900-2099 only: four digits alone are far more often a quantity than a year.
_YEAR = r"(?:19|20)\d{2}"

# One ordered alternation, scanned left to right: the most specific spellings
# come first so ``2026-04`` is a month rather than the year 2026, and
# ``april 2026`` is a month-year rather than a bare month followed by a bare
# year. Only the first match is used, so the ordering is the whole grammar's
# precedence rule.
_PHRASE_RE = re.compile(
    r"(?P<iso>\b(?P<iso_y>" + _YEAR + r")-(?P<iso_m>0[1-9]|1[0-2])\b)"
    r"|(?P<monthyear>\b(?P<my_m>" + _MONTH_ALT + r")\b\.?\s+(?P<my_y>" + _YEAR + r")\b)"
    r"|(?P<quarteryear>\bq(?P<qy_q>[1-4])\s+(?P<qy_y>" + _YEAR + r")\b)"
    r"|(?P<ytd>\bytd\b|\byear[-\s]to[-\s]date\b|\bthis\s+year\b)"
    r"|(?P<prioryear>\b(?:last|prior|previous)\s+year\b)"
    r"|(?P<baremonth>\b(?P<bm_m>" + _MONTH_ALT + r")\b)"
    r"|(?P<barequarter>\bq(?P<bq_q>[1-4])\b)"
    r"|(?P<bareyear>\b(?P<by_y>" + _YEAR + r")\b)"
)

# ``\bvs\b\.?`` and not ``\bvs\.?\b``: a word boundary cannot follow the dot
# in "vs. march" (dot and space are both non-word), so the boundary has to be
# taken on the ``s`` and the dot consumed afterwards.
_COMPARISON_RE = re.compile(r"\bvs\b\.?|\bversus\b|\bcompared\s+to\b")

_KIND_MONTH = "month"
_KIND_QUARTER = "quarter"
_KIND_YEAR = "year"
_KIND_YTD = "ytd"


@dataclass(frozen=True)
class PeriodParse:
    """One turn's resolved periods, where each came from, and what went wrong.

    ``issue`` is never raised and never suppresses a window: a period that
    falls outside the available data still comes back so the caller can offer
    the range it does have (doc 02 section 6 -- clarification, not a guess).
    """

    period_a: PeriodWindow | None
    period_b: PeriodWindow | None
    a_source: str  # "text" | "carry" | "none"
    b_source: str  # "text" | "none" -- period_b is never carried
    issue: ParseIssue | None  # period_unavailable


@dataclass(frozen=True)
class _Resolved:
    """A window plus the SHAPE that produced it, which the -1 year shift
    needs: shifting a window means rebuilding that shape a year earlier, not
    moving its raw bounds."""

    window: PeriodWindow
    kind: str


@dataclass(frozen=True)
class _SideParse:
    """What one side of a comparison (or the whole text) yielded."""

    resolved: _Resolved | None
    matched: bool  # a period phrase was present, even if it resolved to None
    prior_year_relative: bool  # the phrase was "last"/"prior"/"previous year"
    ytd_unavailable: bool  # the phrase was year-to-date at a January 1 reference


_NO_PHRASE = _SideParse(resolved=None, matched=False, prior_year_relative=False,
                        ytd_unavailable=False)


# ---------------------------------------------------------------------------
# Window construction
# ---------------------------------------------------------------------------


def _month_window(year: int, month: int) -> PeriodWindow:
    """``[first of the month, first of the next month)``."""
    if month == 12:
        return PeriodWindow(date(year, 12, 1), date(year + 1, 1, 1))
    return PeriodWindow(date(year, month, 1), date(year, month + 1, 1))


def _quarter_window(year: int, quarter: int) -> PeriodWindow:
    """``[first of the quarter, first of the next quarter)``."""
    start_month = 3 * quarter - 2
    end_month = start_month + 3
    if end_month > 12:
        return PeriodWindow(date(year, start_month, 1), date(year + 1, 1, 1))
    return PeriodWindow(date(year, start_month, 1), date(year, end_month, 1))


def _year_window(year: int) -> PeriodWindow:
    """``[Jan 1, Jan 1 of the next year)`` -- one full calendar year."""
    return PeriodWindow(date(year, 1, 1), date(year + 1, 1, 1))


def _ytd_window(reference: date) -> PeriodWindow | None:
    """``[Jan 1 of the reference's year, reference)``, or ``None`` on Jan 1.

    A January 1 reference makes ``start == end``, which ``PeriodWindow``
    rejects outright; returning ``None`` lets the caller report the empty
    range instead of letting a ``ValueError`` escape from window
    construction.
    """
    if reference.month == 1 and reference.day == 1:
        return None
    return PeriodWindow(date(reference.year, 1, 1), reference)


def _same_day_prior_year(day: date) -> date:
    """``day`` one year earlier, clamped to the last day of that month.

    Only February 29 needs the clamp, and only a year-to-date window can put
    a non-first-of-month date in a window bound at all.
    """
    year = day.year - 1
    last_day_of_month = calendar.monthrange(year, day.month)[1]
    return date(year, day.month, min(day.day, last_day_of_month))


def _shift_back_one_year(resolved: _Resolved) -> _Resolved:
    """The same period SHAPE, one year earlier.

    Rebuilt from the shape's own anchor rather than by moving both bounds, so
    a month stays a whole month and a quarter a whole quarter regardless of
    how many days each has.
    """
    start = resolved.window.start
    if resolved.kind == _KIND_MONTH:
        return _Resolved(_month_window(start.year - 1, start.month), _KIND_MONTH)
    if resolved.kind == _KIND_QUARTER:
        quarter = (start.month - 1) // 3 + 1
        return _Resolved(_quarter_window(start.year - 1, quarter), _KIND_QUARTER)
    if resolved.kind == _KIND_YEAR:
        return _Resolved(_year_window(start.year - 1), _KIND_YEAR)
    shifted_end = _same_day_prior_year(resolved.window.end)
    return _Resolved(PeriodWindow(date(start.year - 1, 1, 1), shifted_end), _KIND_YTD)


# ---------------------------------------------------------------------------
# Phrase resolution
# ---------------------------------------------------------------------------


def _preceding_word(side: str, start: int) -> str:
    """The word token immediately before ``start``, or ``""`` when the match
    opens ``side``. Whitespace and punctuation in between are skipped, so
    ``for may`` and ``for, may`` read alike.

    ``side`` is one side of a comparison, so a right-hand match sees no
    preceding word rather than the connective that split the text. The two
    readings cannot disagree: no connective ends in a gate preposition.
    """
    match = _PRECEDING_WORD_RE.search(side[:start])
    return match.group(1) if match is not None else ""


def _bare_month_is_gated_out(side: str, match: re.Match[str]) -> bool:
    """Whether an ambiguous bare month (``may``) lacks the preposition that
    makes it a date. Every other month, and every month-year spelling,
    matches unconditionally."""
    if match.group("bm_m") not in AMBIGUOUS_BARE_MONTHS:
        return False
    return _preceding_word(side, match.start()) not in _BARE_MONTH_PREPOSITIONS


def _parse_side(side: str, reference: date) -> _SideParse:
    """Resolve the first period phrase in ``side`` against ``reference``.

    A gated-out bare month does not end the scan: the text reads on exactly
    as if the word were not a month at all, so a real phrase further along
    the same side is still found, and a side left with nothing takes the
    ordinary no-phrase path (carry, else none).
    """
    for match in _PHRASE_RE.finditer(side):
        if match.group("baremonth") is not None and _bare_month_is_gated_out(side, match):
            continue
        return _resolve(match, reference)
    return _NO_PHRASE


def _resolve(match: re.Match[str], reference: date) -> _SideParse:
    """Turn one accepted phrase match into a window plus its shape."""
    if match.group("iso") is not None:
        window = _month_window(int(match.group("iso_y")), int(match.group("iso_m")))
        return _phrase(window, _KIND_MONTH)

    if match.group("monthyear") is not None:
        window = _month_window(int(match.group("my_y")), _MONTHS[match.group("my_m")])
        return _phrase(window, _KIND_MONTH)

    if match.group("quarteryear") is not None:
        window = _quarter_window(int(match.group("qy_y")), int(match.group("qy_q")))
        return _phrase(window, _KIND_QUARTER)

    if match.group("ytd") is not None:
        window = _ytd_window(reference)
        if window is None:
            return _SideParse(None, matched=True, prior_year_relative=False,
                              ytd_unavailable=True)
        return _phrase(window, _KIND_YTD)

    if match.group("prioryear") is not None:
        return _SideParse(
            _Resolved(_year_window(reference.year - 1), _KIND_YEAR),
            matched=True,
            prior_year_relative=True,
            ytd_unavailable=False,
        )

    if match.group("baremonth") is not None:
        month = _MONTHS[match.group("bm_m")]
        return _phrase(_month_window(_most_recent_year(month, reference), month), _KIND_MONTH)

    if match.group("barequarter") is not None:
        quarter = int(match.group("bq_q"))
        year = _most_recent_year(3 * quarter - 2, reference)
        return _phrase(_quarter_window(year, quarter), _KIND_QUARTER)

    return _phrase(_year_window(int(match.group("by_y"))), _KIND_YEAR)


def _phrase(window: PeriodWindow, kind: str) -> _SideParse:
    return _SideParse(
        _Resolved(window, kind), matched=True, prior_year_relative=False, ytd_unavailable=False
    )


def _most_recent_year(start_month: int, reference: date) -> int:
    """The year of the most recent occurrence of a period starting in
    ``start_month`` that has already begun: the reference year when that
    month is at or before the reference month, else the year before."""
    return reference.year if start_month <= reference.month else reference.year - 1


# ---------------------------------------------------------------------------
# Availability validation
# ---------------------------------------------------------------------------


def _availability_issue(resolved: _Resolved | None, available: PeriodRange) -> ParseIssue | None:
    """A ``period_unavailable`` issue when ``resolved`` holds no data.

    ``available.end`` is the newest date PRESENT (inclusive), so it becomes a
    half-open bound by advancing one day before the overlap test; two
    half-open ranges intersect when each starts before the other ends.
    """
    if resolved is None:
        return None

    window = resolved.window
    span = f"{window.start.isoformat()}..{window.end.isoformat()}"

    if available.start is None or available.end is None:
        return ParseIssue(_ISSUE_CODE, f"no data for {span} \u2014 no periods available")

    available_end_exclusive = available.end + timedelta(days=1)
    if window.start < available_end_exclusive and available.start < window.end:
        return None

    return ParseIssue(
        _ISSUE_CODE,
        f"no data for {span} \u2014 available "
        f"{available.start.isoformat()}..{available.end.isoformat()}",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_periods(
    text: str,
    slots: ConversationSlots,
    reference_date: date,
    available: PeriodRange,
) -> PeriodParse:
    """Resolve ``text`` into ``period_a``/``period_b`` windows.

    See the module docstring for the grammar, the carry rule (and its
    documented granularity limitation), the shift rule, and the issue
    precedence. ``text`` is raw user input: it is normalized and casefolded
    here.
    """
    cleaned = normalize(text).casefold()

    split = _COMPARISON_RE.search(cleaned)
    if split is None:
        left = _parse_side(cleaned, reference_date)
        right = _NO_PHRASE
    else:
        left = _parse_side(cleaned[: split.start()], reference_date)
        right = _parse_side(cleaned[split.end() :], reference_date)

    # period_a: the left-hand phrase, or last turn's period when the text
    # named none. An explicit phrase suppresses the carry even when it
    # resolved to nothing -- the user asked for a period, not for last turn's.
    resolved_a = left.resolved
    a_source = _SOURCE_TEXT if resolved_a is not None else _SOURCE_NONE
    if not left.matched and slots.period_a is not None:
        carried = slots.period_a
        resolved_a = _Resolved(_month_window(carried.year, carried.month), _KIND_MONTH)
        a_source = _SOURCE_CARRY

    # period_b: only ever from this turn's text.
    resolved_b = right.resolved
    if right.prior_year_relative and resolved_a is not None:
        resolved_b = _shift_back_one_year(resolved_a)
    b_source = _SOURCE_TEXT if resolved_b is not None else _SOURCE_NONE

    if left.ytd_unavailable or right.ytd_unavailable:
        issue = ParseIssue(_ISSUE_CODE, _YTD_AT_YEAR_START)
    else:
        issue = _availability_issue(resolved_a, available) or _availability_issue(
            resolved_b, available
        )

    return PeriodParse(
        period_a=resolved_a.window if resolved_a is not None else None,
        period_b=resolved_b.window if resolved_b is not None else None,
        a_source=a_source,
        b_source=b_source,
        issue=issue,
    )
