"""Table-driven tests for Phase 4 Task 2: the deterministic period parser
(doc 02 section 5) -- date grammar, carry-over, and availability validation.

Every bullet of the task brief's Grammar section is a contract, so every
bullet gets at least one row in ``CASES``: each row is a complete
``(text, slots, reference_date, available) -> exact PeriodParse`` tuple, so a
change in ANY field of the result (window bounds, either source string, or
the issue) fails a specific, named case rather than a vague assertion.

What the table pins, bullet by bullet:

* month-year in all three spellings (``april 2026`` / ``apr 2026`` /
  ``2026-04``), plus a messy-whitespace uppercase row that proves
  ``parse_periods`` normalizes and casefolds its own input;
* the bare-month recency rule in BOTH directions -- ``in july`` at the
  reference month resolves forward-inclusive (2026), ``in december`` rolls
  back a year (2025);
* year words (bare ``2025``, ``last``/``prior``/``previous year``,
  ``this year``/``ytd``/``year to date``) and the January-1 year-to-date
  edge, whose message is byte-pinned;
* quarters in both forms, again in both directions of the recency rule;
* all three comparison connectives, the ``vs last year`` shift (on a month,
  on a quarter, and on a year-to-date window -- the last across a leap day),
  and ``prior year vs ytd``;
* carry-over present / absent / replaced, and the "period_b is never
  carried" rule;
* availability hit and miss, including the case that can only pass if the
  INCLUSIVE ``PeriodRange.end`` is converted to a half-open bound (+1 day),
  with both miss messages byte-pinned.

Non-ASCII characters in expected strings are written as explicit ``\\uXXXX``
escapes (see ``_EM_DASH``) rather than typed literals, per the Task 1
handoff note: an em dash, an en dash and a hyphen are visually
indistinguishable in most editors, so a byte-pinned message that used a
typed character could silently pin the wrong codepoint. Everything else in
this file is plain ASCII.
"""

import ast
from dataclasses import FrozenInstanceError
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

import pytest

from poseidon.core.data.client import PeriodRange
from poseidon.core.data.specs import PeriodWindow
from poseidon.core.parsing import period_parser
from poseidon.core.parsing.period_parser import PeriodParse, parse_periods
from poseidon.core.parsing.types import ParseIssue
from poseidon.core.skills.context import ConversationSlots

# ---------------------------------------------------------------------------
# Fixtures-as-constants and window builders
# ---------------------------------------------------------------------------

# Every case pins its own reference date -- the parser must never consult the
# wall clock, so these values are the ONLY source of "now" in this file.
REF = date(2026, 7, 15)  # a mid-July reference: month 7 of 2026, quarter 3
JAN_1 = date(2026, 1, 1)  # the year-to-date edge
LEAP_DAY = date(2024, 2, 29)  # the -1 year shift edge

NO_SLOTS = ConversationSlots()

# Wide enough that most rows never trip availability validation, so a row that
# DOES report an issue is reporting it for the reason its id names.
WIDE = PeriodRange(date(2018, 1, 1), date(2026, 12, 31))
NARROW = PeriodRange(date(2023, 1, 1), date(2026, 7, 31))

# U+2014 EM DASH, written as an escape so the pinned message below cannot be
# confused with a hyphen-minus or an en dash by an editor or an encoding
# round-trip. See the module docstring.
_EM_DASH = "\u2014"

_YTD_JAN_1_MESSAGE = "year-to-date has no days before January 2"

# Explicit quarter -> (first month, month after) table: the expectations are
# built from a lookup, not from the same arithmetic the parser uses, so a
# broken quarter formula cannot agree with itself.
_QUARTER_MONTHS = {1: (1, 4), 2: (4, 7), 3: (7, 10), 4: (10, 13)}


def _month(year: int, month: int) -> PeriodWindow:
    """The half-open window covering exactly the given calendar month."""
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return PeriodWindow(date(year, month, 1), date(end_year, end_month, 1))


def _quarter(year: int, quarter: int) -> PeriodWindow:
    """The half-open window covering exactly the given calendar quarter."""
    start_month, end_month = _QUARTER_MONTHS[quarter]
    if end_month == 13:
        return PeriodWindow(date(year, start_month, 1), date(year + 1, 1, 1))
    return PeriodWindow(date(year, start_month, 1), date(year, end_month, 1))


def _year(year: int) -> PeriodWindow:
    """The half-open window covering exactly the given full calendar year."""
    return PeriodWindow(date(year, 1, 1), date(year + 1, 1, 1))


def _ytd(reference: date) -> PeriodWindow:
    """Jan 1 of the reference's year up to (excluding) the reference itself."""
    return PeriodWindow(date(reference.year, 1, 1), reference)


class Case(NamedTuple):
    """One complete parser contract: four inputs, one exact expected output."""

    id: str
    text: str
    slots: ConversationSlots
    reference: date
    available: PeriodRange
    expected: PeriodParse


CASES = [
    # -- Grammar: month-year, three spellings ------------------------------
    Case(
        "month_year_full_name",
        "gp for april 2026",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "text", "none", None),
    ),
    Case(
        "month_year_abbreviated",
        "gp for apr 2026",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "text", "none", None),
    ),
    Case(
        "month_year_iso_numeric",
        "gp for 2026-04",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "text", "none", None),
    ),
    Case(
        # Proves parse_periods normalizes + casefolds its own input: callers
        # hand it raw user text, not pre-cleaned text.
        "month_year_uppercase_and_ragged_whitespace",
        "  GP   for\tAPRIL\n2026 ",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "text", "none", None),
    ),
    Case(
        "month_year_abbreviation_with_trailing_dot",
        "gp for apr. 2026",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "text", "none", None),
    ),
    Case(
        # December is the year-rollover month for the window's END bound.
        "month_year_december_rolls_end_into_next_year",
        "december 2025",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2025, 12), None, "text", "none", None),
    ),
    # -- Grammar: bare month, both directions of the recency rule ----------
    Case(
        # December 2026 has not started yet at a July reference, so the most
        # recent December is 2025.
        "bare_month_in_the_future_rolls_back_a_year",
        "in december",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2025, 12), None, "text", "none", None),
    ),
    Case(
        # The reference month itself: start == reference month start, so the
        # rule keeps the reference year (window extends past the reference).
        "bare_month_equal_to_reference_month_keeps_year",
        "in july",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 7), None, "text", "none", None),
    ),
    Case(
        "bare_month_earlier_this_year_keeps_year",
        "in january",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 1), None, "text", "none", None),
    ),
    Case(
        # One month past the reference month -- the first month that rolls back.
        "bare_month_one_past_reference_rolls_back_a_year",
        "in august",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2025, 8), None, "text", "none", None),
    ),
    # -- Grammar: year words -----------------------------------------------
    Case(
        "bare_four_digit_year",
        "2025 numbers",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_year(2025), None, "text", "none", None),
    ),
    Case(
        "last_year_is_the_prior_full_calendar_year",
        "last year",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_year(2025), None, "text", "none", None),
    ),
    Case(
        "prior_year_is_a_synonym_of_last_year",
        "prior year",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_year(2025), None, "text", "none", None),
    ),
    Case(
        "previous_year_is_a_synonym_of_last_year",
        "previous year",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_year(2025), None, "text", "none", None),
    ),
    Case(
        # "this year" means year-to-date, NOT the full calendar year.
        "this_year_means_year_to_date",
        "this year",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_ytd(REF), None, "text", "none", None),
    ),
    Case(
        "ytd_abbreviation",
        "ytd",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_ytd(REF), None, "text", "none", None),
    ),
    Case(
        "year_to_date_hyphenated",
        "year-to-date",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_ytd(REF), None, "text", "none", None),
    ),
    Case(
        "year_to_date_spaced",
        "year to date",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_ytd(REF), None, "text", "none", None),
    ),
    Case(
        # The [Jan 1, reference) window is empty at a Jan 1 reference; the
        # parser reports it instead of constructing an invalid window.
        "ytd_at_a_january_first_reference_is_unavailable",
        "ytd",
        NO_SLOTS,
        JAN_1,
        WIDE,
        PeriodParse(
            None, None, "none", "none", ParseIssue("period_unavailable", _YTD_JAN_1_MESSAGE)
        ),
    ),
    Case(
        # An explicit phrase that resolves to nothing still suppresses carry:
        # the user asked for year-to-date, not for last turn's period.
        "ytd_at_january_first_does_not_fall_back_to_carry",
        "ytd",
        ConversationSlots(period_a=date(2026, 4, 1)),
        JAN_1,
        WIDE,
        PeriodParse(
            None, None, "none", "none", ParseIssue("period_unavailable", _YTD_JAN_1_MESSAGE)
        ),
    ),
    # -- Grammar: quarters, both forms and both directions -----------------
    Case(
        "quarter_with_explicit_year",
        "q1 2026",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_quarter(2026, 1), None, "text", "none", None),
    ),
    Case(
        "quarter_with_explicit_past_year",
        "q2 2025",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_quarter(2025, 2), None, "text", "none", None),
    ),
    Case(
        # Q3 starts in July -- the reference month -- so it keeps 2026 even
        # though the window runs past the reference date.
        "bare_quarter_containing_reference_keeps_year",
        "q3",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_quarter(2026, 3), None, "text", "none", None),
    ),
    Case(
        "bare_quarter_in_the_future_rolls_back_a_year",
        "q4",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_quarter(2025, 4), None, "text", "none", None),
    ),
    # -- Grammar: comparison ------------------------------------------------
    Case(
        "comparison_with_vs",
        "april 2026 vs march 2026",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), _month(2026, 3), "text", "text", None),
    ),
    Case(
        "comparison_with_versus",
        "april 2026 versus march 2026",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), _month(2026, 3), "text", "text", None),
    ),
    Case(
        "comparison_with_compared_to",
        "april 2026 compared to march 2026",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), _month(2026, 3), "text", "text", None),
    ),
    Case(
        # "vs last year" shifts period_a by -1 year rather than resolving the
        # right side as the prior full calendar year.
        "comparison_vs_last_year_shifts_a_month_window",
        "april 2026 vs last year",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), _month(2025, 4), "text", "text", None),
    ),
    Case(
        "comparison_vs_last_year_shifts_a_quarter_window",
        "q1 2026 vs last year",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_quarter(2026, 1), _quarter(2025, 1), "text", "text", None),
    ),
    Case(
        # A full calendar year shifted by -1 year is the previous full year --
        # the shift rule and the plain reading agree here, by construction.
        "comparison_year_vs_last_year_shifts_to_previous_year",
        "2026 vs last year",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_year(2026), _year(2025), "text", "text", None),
    ),
    Case(
        # The shift keeps the year-to-date SHAPE: same partial-year cut-off,
        # one year earlier -- not the whole prior calendar year.
        "comparison_ytd_vs_last_year_shifts_the_ytd_cutoff",
        "ytd vs last year",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(
            _ytd(REF),
            PeriodWindow(date(2025, 1, 1), date(2025, 7, 15)),
            "text",
            "text",
            None,
        ),
    ),
    Case(
        # Feb 29 has no counterpart in the prior year -- clamp to Feb 28
        # rather than raising.
        "comparison_ytd_vs_last_year_clamps_february_29",
        "ytd vs last year",
        NO_SLOTS,
        LEAP_DAY,
        WIDE,
        PeriodParse(
            PeriodWindow(date(2024, 1, 1), date(2024, 2, 29)),
            PeriodWindow(date(2023, 1, 1), date(2023, 2, 28)),
            "text",
            "text",
            None,
        ),
    ),
    Case(
        # The brief's named shape: left side names a period of its own, so no
        # shift happens -- both sides resolve independently.
        "comparison_prior_year_vs_ytd",
        "prior year vs ytd",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_year(2025), _ytd(REF), "text", "text", None),
    ),
    Case(
        # Only the right side names a period, so period_a comes from carry and
        # the shift applies to the CARRIED window.
        "comparison_vs_last_year_shifts_the_carried_window",
        "vs last year",
        ConversationSlots(period_a=date(2026, 4, 1)),
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), _month(2025, 4), "carry", "text", None),
    ),
    Case(
        # Nothing to shift: the right side falls back to its plain reading.
        "comparison_vs_last_year_without_a_resolved_a",
        "vs last year",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(None, _year(2025), "none", "text", None),
    ),
    Case(
        "comparison_whose_right_side_names_no_period",
        "april 2026 vs acme corp",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "text", "none", None),
    ),
    Case(
        # Two phrases with no connective is not a comparison: only the first
        # phrase is taken.
        "two_phrases_without_a_connective_is_not_a_comparison",
        "april 2026 march 2026",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "text", "none", None),
    ),
    # -- Carry-over ---------------------------------------------------------
    Case(
        "carry_when_the_text_names_no_period",
        "how about volume",
        ConversationSlots(period_a=date(2026, 4, 1)),
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "carry", "none", None),
    ),
    Case(
        "no_carry_available_and_no_period_in_the_text",
        "how about volume",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(None, None, "none", "none", None),
    ),
    Case(
        # period_b is never carried: a comparison must be re-asked for.
        "period_b_is_never_carried",
        "how about volume",
        ConversationSlots(period_a=date(2026, 4, 1), period_b=date(2025, 4, 1)),
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "carry", "none", None),
    ),
    Case(
        "a_new_phrase_replaces_the_carried_period",
        "volume in may 2026",
        ConversationSlots(period_a=date(2026, 4, 1)),
        REF,
        WIDE,
        PeriodParse(_month(2026, 5), None, "text", "none", None),
    ),
    Case(
        "a_new_comparison_replaces_both_carried_periods",
        "q1 2026 vs q2 2026",
        ConversationSlots(period_a=date(2020, 1, 1), period_b=date(2019, 1, 1)),
        REF,
        WIDE,
        PeriodParse(_quarter(2026, 1), _quarter(2026, 2), "text", "text", None),
    ),
    Case(
        # Slots hold first-of-period dates by contract, but a mid-month date
        # still reconstructs the month that contains it rather than a partial
        # window.
        "carry_from_a_mid_month_slot_date_yields_the_whole_month",
        "how about volume",
        ConversationSlots(period_a=date(2026, 4, 17)),
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "carry", "none", None),
    ),
    # -- Availability validation -------------------------------------------
    Case(
        "availability_hit_reports_no_issue",
        "april 2026",
        NO_SLOTS,
        REF,
        NARROW,
        PeriodParse(_month(2026, 4), None, "text", "none", None),
    ),
    Case(
        # Windows are still returned -- the caller decides what to do.
        "availability_miss_before_the_available_range",
        "april 2019",
        NO_SLOTS,
        REF,
        NARROW,
        PeriodParse(
            _month(2019, 4),
            None,
            "text",
            "none",
            ParseIssue(
                "period_unavailable",
                "no data for 2019-04-01..2019-05-01 "
                + _EM_DASH
                + " available 2023-01-01..2026-07-31",
            ),
        ),
    ),
    Case(
        # PeriodRange.end is INCLUSIVE: 2026-04-01 data exists, so April 2026
        # intersects. Only passes if the parser converts with +1 day.
        "availability_inclusive_end_is_converted_before_intersecting",
        "april 2026",
        NO_SLOTS,
        REF,
        PeriodRange(date(2026, 3, 1), date(2026, 4, 1)),
        PeriodParse(_month(2026, 4), None, "text", "none", None),
    ),
    Case(
        # The window's exclusive end lands exactly on the available start, so
        # they touch without overlapping -- a miss.
        "availability_miss_when_window_ends_exactly_at_available_start",
        "april 2026",
        NO_SLOTS,
        REF,
        PeriodRange(date(2026, 5, 1), date(2026, 12, 31)),
        PeriodParse(
            _month(2026, 4),
            None,
            "text",
            "none",
            ParseIssue(
                "period_unavailable",
                "no data for 2026-04-01..2026-05-01 "
                + _EM_DASH
                + " available 2026-05-01..2026-12-31",
            ),
        ),
    ),
    Case(
        # period_a is fine, period_b is not -- the issue names period_b's
        # window and both windows are still returned.
        "availability_miss_on_period_b_only",
        "april 2026 vs april 2019",
        NO_SLOTS,
        REF,
        NARROW,
        PeriodParse(
            _month(2026, 4),
            _month(2019, 4),
            "text",
            "text",
            ParseIssue(
                "period_unavailable",
                "no data for 2019-04-01..2019-05-01 "
                + _EM_DASH
                + " available 2023-01-01..2026-07-31",
            ),
        ),
    ),
    Case(
        # PeriodRange(None, None) means the entity holds no rows at all.
        "availability_with_an_empty_period_range",
        "april 2026",
        NO_SLOTS,
        REF,
        PeriodRange(None, None),
        PeriodParse(
            _month(2026, 4),
            None,
            "text",
            "none",
            ParseIssue(
                "period_unavailable",
                "no data for 2026-04-01..2026-05-01 " + _EM_DASH + " no periods available",
            ),
        ),
    ),
    Case(
        # Two issues are possible here (2019 is outside `available`, and YTD
        # is empty at a Jan 1 reference) but PeriodParse holds one: the
        # phrase that could not resolve at all wins.
        "unresolvable_ytd_outranks_an_availability_miss",
        "2019 vs ytd",
        NO_SLOTS,
        JAN_1,
        NARROW,
        PeriodParse(
            _year(2019),
            None,
            "text",
            "none",
            ParseIssue("period_unavailable", _YTD_JAN_1_MESSAGE),
        ),
    ),
    # -- No period at all ---------------------------------------------------
    Case(
        "empty_text",
        "",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(None, None, "none", "none", None),
    ),
    Case(
        "text_with_no_period_words_at_all",
        "who are the top customers",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(None, None, "none", "none", None),
    ),
    Case(
        "a_small_number_is_not_a_year",
        "top 10 customers",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(None, None, "none", "none", None),
    ),
    Case(
        # Four digits outside the 1900-2099 window are a quantity, not a year.
        "a_four_digit_quantity_outside_the_year_range_is_not_a_year",
        "1500 tonnes",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(None, None, "none", "none", None),
    ),
    Case(
        "a_comparison_of_two_non_periods_still_falls_back_to_carry",
        "acme vs beta shipping",
        ConversationSlots(period_a=date(2026, 4, 1)),
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "carry", "none", None),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_parse_periods_case(case: Case):
    assert parse_periods(case.text, case.slots, case.reference, case.available) == case.expected


# ---------------------------------------------------------------------------
# Invariants that hold across the whole table
# ---------------------------------------------------------------------------


def test_case_ids_are_unique():
    ids = [case.id for case in CASES]
    assert len(set(ids)) == len(ids)


def test_table_covers_at_least_twenty_five_cases():
    assert len(CASES) >= 25


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_source_is_none_exactly_when_the_window_is_none(case: Case):
    """``a_source``/``b_source`` describe where a window came from, so
    ``"none"`` and a ``None`` window must always agree -- otherwise a caller
    could read a source of ``"text"`` off a result that has no window."""
    result = parse_periods(case.text, case.slots, case.reference, case.available)
    assert (result.period_a is None) == (result.a_source == "none")
    assert (result.period_b is None) == (result.b_source == "none")


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_sources_are_from_the_declared_vocabulary(case: Case):
    result = parse_periods(case.text, case.slots, case.reference, case.available)
    assert result.a_source in {"text", "carry", "none"}
    # period_b is never carried, so "carry" can never appear on the b side.
    assert result.b_source in {"text", "none"}


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_parse_periods_is_deterministic(case: Case):
    """Same inputs, same output -- no hidden state, no wall clock."""
    first = parse_periods(case.text, case.slots, case.reference, case.available)
    second = parse_periods(case.text, case.slots, case.reference, case.available)
    assert first == second


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_every_issue_uses_the_period_unavailable_code(case: Case):
    result = parse_periods(case.text, case.slots, case.reference, case.available)
    if result.issue is not None:
        assert result.issue.code == "period_unavailable"
        assert result.issue.candidates == ()


# ---------------------------------------------------------------------------
# Shape and hygiene
# ---------------------------------------------------------------------------


def test_period_parse_is_frozen():
    result = PeriodParse(_month(2026, 4), None, "text", "none", None)
    with pytest.raises(FrozenInstanceError):
        result.period_a = None  # type: ignore[misc]


def test_period_parse_exposes_the_five_interface_fields():
    result = PeriodParse(_month(2026, 4), _month(2025, 4), "text", "text", None)
    assert result.period_a == _month(2026, 4)
    assert result.period_b == _month(2025, 4)
    assert (result.a_source, result.b_source) == ("text", "text")
    assert result.issue is None


def test_module_never_reads_the_wall_clock():
    """The parser resolves everything from its ``reference_date`` argument.
    A single wall-clock read would make the whole grammar untestable against
    a fixed table, so it is pinned at the source level too.

    Checked over the parsed AST rather than by substring: the module's own
    docstring explains that it never calls the clock, and a substring scan
    cannot tell that sentence apart from the call it forbids.
    """
    tree = ast.parse(Path(period_parser.__file__).read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        elif isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert called.isdisjoint({"today", "now", "utcnow", "time", "monotonic", "fromtimestamp"})


_ONE_PHRASE_PER_GRAMMAR_BRANCH = (
    "april 2026",
    "apr 2026",
    "2026-04",
    "in april",
    "in december",
    "2025",
    "last year",
    "ytd",
    "this year",
    "q1 2026",
    "q3",
    "april 2026 vs march 2026",
    "ytd vs last year",
    "q3 vs last year",
    "in february vs last year",
    "prior year vs ytd",
    "vs last year",
    "how about volume",
)


@pytest.mark.parametrize("phrase", _ONE_PHRASE_PER_GRAMMAR_BRANCH)
def test_no_reference_date_can_build_an_invalid_window(phrase: str):
    """``PeriodWindow`` raises on ``start >= end``, so an off-by-one anywhere
    in the grammar surfaces as an exception rather than a wrong window.

    Every reference date of a leap year and the year that follows it is swept
    against every grammar branch -- including the -1 year shift, which crosses
    February 29 exactly once in that span, and a carried slot date that is
    itself February 29.
    """
    slots = ConversationSlots(period_a=date(2024, 2, 29))
    for offset in range(366 + 365):
        reference = date(2024, 1, 1) + timedelta(days=offset)
        result = parse_periods(phrase, slots, reference, WIDE)
        for window in (result.period_a, result.period_b):
            if window is not None:
                assert window.start < window.end


def test_module_documents_the_carried_granularity_limitation():
    """The carry path reconstructs a MONTH window from ``slots.period_a``,
    so a carried quarter or year silently degrades to a month. That is a
    deliberate v1 limitation and must stay documented where a reader of the
    module will find it."""
    doc = period_parser.__doc__ or ""
    assert "granularity" in doc.casefold()
