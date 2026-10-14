"""Phase 17 PR 0 (D62): ``core.parsing.period_labels`` -- the one module
that classifies a half-open ``PeriodWindow``, rebuilds a window from a
slot's start and optional end, and names a window for humans. The parser's
carry path, the prompt state block and (later) the report all read windows
through here, so a label can never disagree between surfaces.

Pure ASCII on disk, like the parser and its tests.
"""

from datetime import date
from pathlib import Path
from typing import NamedTuple

import pytest

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.parsing import period_labels
from poseidon.core.parsing.period_labels import (
    days_in_month,
    label_day_range,
    label_month_to_date,
    label_window,
    month_window,
    slot_window,
    window_kind,
)


class KindCase(NamedTuple):
    id: str
    window: PeriodWindow
    kind: str
    label: str


CASES = [
    KindCase("month", PeriodWindow(date(2026, 4, 1), date(2026, 5, 1)), "month", "April 2026"),
    KindCase(
        "december",
        PeriodWindow(date(2025, 12, 1), date(2026, 1, 1)),
        "month",
        "December 2025",
    ),
    KindCase("quarter_q2", PeriodWindow(date(2026, 4, 1), date(2026, 7, 1)), "quarter", "Q2 2026"),
    KindCase("quarter_q4", PeriodWindow(date(2025, 10, 1), date(2026, 1, 1)), "quarter", "Q4 2025"),
    KindCase("year", PeriodWindow(date(2025, 1, 1), date(2026, 1, 1)), "year", "2025"),
    KindCase("ytd", PeriodWindow(date(2026, 1, 1), date(2026, 7, 15)), "ytd", "2026 year to date"),
    KindCase(
        "day_range_in_one_month",
        PeriodWindow(date(2026, 10, 1), date(2026, 10, 12)),
        "range",
        "October 1 to 11, 2026",
    ),
    KindCase(
        "day_range_across_months",
        PeriodWindow(date(2025, 12, 15), date(2026, 1, 15)),
        "range",
        "December 15, 2025 to January 14, 2026",
    ),
    KindCase(
        # Starts on the first but is neither a month, quarter, year nor Jan-1
        # anchored: two months is a range.
        "two_months_is_a_range",
        PeriodWindow(date(2026, 4, 1), date(2026, 6, 1)),
        "range",
        "April 1 to May 31, 2026",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_window_kind(case: KindCase):
    assert window_kind(case.window) == case.kind


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_label_window(case: KindCase):
    assert label_window(case.window) == case.label


def test_month_window_wraps_december():
    assert month_window(2025, 12) == PeriodWindow(date(2025, 12, 1), date(2026, 1, 1))
    assert month_window(2026, 4) == PeriodWindow(date(2026, 4, 1), date(2026, 5, 1))


def test_slot_window_with_a_valid_end_is_that_window():
    assert slot_window(date(2025, 1, 1), date(2026, 1, 1)) == PeriodWindow(
        date(2025, 1, 1), date(2026, 1, 1)
    )


def test_slot_window_without_an_end_is_the_containing_month():
    """Pre-Phase-17 rows and code-built slots have a start and no end; the
    pre-PR behaviour (the month containing the start) is preserved here and
    nowhere else."""
    assert slot_window(date(2026, 4, 17), None) == PeriodWindow(date(2026, 4, 1), date(2026, 5, 1))


def test_slot_window_with_an_end_before_the_start_falls_back_to_the_month():
    """A corrupt or hand-edited end must never let PeriodWindow's inverted-
    window ValueError escape a chat turn."""
    assert slot_window(date(2026, 4, 1), date(2026, 4, 1)) == month_window(2026, 4)
    assert slot_window(date(2026, 4, 1), date(2026, 3, 1)) == month_window(2026, 4)


def test_module_is_ascii_on_disk():
    for path in (Path(period_labels.__file__), Path(__file__)):
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


def test_label_month_to_date_appends_the_words_once():
    window = PeriodWindow(date(2026, 10, 1), date(2026, 10, 12))
    assert label_month_to_date(window) == "October 1 to 11, 2026 (month to date)"


def test_days_in_month_knows_leap_years():
    assert days_in_month(2028, 2) == 29 and days_in_month(2027, 2) == 28


def test_label_day_range_never_collapses_a_whole_month():
    assert (
        label_day_range(PeriodWindow(date(2026, 2, 1), date(2026, 3, 1)))
        == "February 1 to 28, 2026"
    )
    assert (
        label_day_range(PeriodWindow(date(2026, 10, 1), date(2026, 10, 12)))
        == "October 1 to 11, 2026"
    )
