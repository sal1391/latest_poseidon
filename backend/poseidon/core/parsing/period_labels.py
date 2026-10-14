"""Window classification, slot-to-window reconstruction and human labels
for half-open :class:`PeriodWindow` values (Phase 17 PR 0, decision D62).

Three callers share this module so they can never disagree about what a
window IS or how it is named:

* ``period_parser`` -- carries the stored window of a slot (and needs its
  shape to shift it a year back for ``vs last year``);
* ``llm.prompts`` -- renders ``Carried period A: April 2026 (2026-04-01..
  2026-05-01)`` in the state block;
* the Phase 17 report -- labels its month pair and its month-to-date range.

``slot_window`` is the ONLY place the "a start with no end is the month
containing it" fallback lives. ``ConversationSlots`` grew its end fields
additively, so pre-Phase-17 rows and code-built slots legitimately carry a
start alone; they are read exactly as they were before this phase.

Pure functions, no clock, no I/O, pure ASCII on disk.
"""

import calendar
from datetime import date, timedelta
from typing import Literal

from poseidon.core.data.specs import PeriodWindow

WindowKind = Literal["month", "quarter", "year", "ytd", "range"]

KIND_MONTH: WindowKind = "month"
KIND_QUARTER: WindowKind = "quarter"
KIND_YEAR: WindowKind = "year"
KIND_YTD: WindowKind = "ytd"
KIND_RANGE: WindowKind = "range"

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_QUARTER_FIRST_MONTHS = (1, 4, 7, 10)


def month_window(year: int, month: int) -> PeriodWindow:
    """``[first of the month, first of the next month)``."""
    return PeriodWindow(date(year, month, 1), _add_months(date(year, month, 1), 1))


def _add_months(first_of_month: date, months: int) -> date:
    """``first_of_month`` moved forward ``months`` whole months (stays on the 1st)."""
    index = first_of_month.year * 12 + (first_of_month.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def add_months(first_of_month: date, months: int) -> date:
    """Public name for :func:`_add_months` (the report needs the prior month)."""
    return _add_months(first_of_month, months)


def days_in_month(year: int, month: int) -> int:
    """The number of days in the given calendar month."""
    return calendar.monthrange(year, month)[1]


def window_kind(window: PeriodWindow) -> WindowKind:
    """The shape that produced ``window``: checked most specific first, so a
    full year is ``year`` (not ``quarter``, not ``ytd``) and Q1 is
    ``quarter`` (not ``ytd``)."""
    start, end = window.start, window.end
    if start.day != 1:
        return KIND_RANGE
    if start.month == 1 and end == date(start.year + 1, 1, 1):
        return KIND_YEAR
    if start.month in _QUARTER_FIRST_MONTHS and end == _add_months(start, 3):
        return KIND_QUARTER
    if end == _add_months(start, 1):
        return KIND_MONTH
    if start.month == 1 and end.year == start.year:
        return KIND_YTD
    return KIND_RANGE


def slot_window(start: date, end: date | None) -> PeriodWindow:
    """The window a slot holds: ``[start, end)`` when ``end`` is a real
    exclusive end after ``start``; otherwise the month containing ``start``
    -- the pre-Phase-17 reading, kept for rows and code-built slots that
    carry no end, and for a corrupt end that would otherwise make
    ``PeriodWindow`` raise inside a chat turn."""
    if end is not None and end > start:
        return PeriodWindow(start, end)
    return month_window(start.year, start.month)


def label_window(window: PeriodWindow) -> str:
    """A human name for ``window``; see the module docstring for examples."""
    kind = window_kind(window)
    start = window.start
    if kind == KIND_MONTH:
        return f"{_MONTH_NAMES[start.month - 1]} {start.year}"
    if kind == KIND_QUARTER:
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if kind == KIND_YEAR:
        return f"{start.year}"
    if kind == KIND_YTD:
        return f"{start.year} year to date"
    return label_day_range(window)


def label_day_range(window: PeriodWindow) -> str:
    """``window`` always named as a day range ("February 1 to 28, 2026"),
    even when it happens to span a whole month, quarter or year."""
    start = window.start
    last = window.end - timedelta(days=1)
    if start.year == last.year and start.month == last.month:
        return f"{_MONTH_NAMES[start.month - 1]} {start.day} to {last.day}, {start.year}"
    if start.year == last.year:
        return (
            f"{_MONTH_NAMES[start.month - 1]} {start.day} to "
            f"{_MONTH_NAMES[last.month - 1]} {last.day}, {start.year}"
        )
    return (
        f"{_MONTH_NAMES[start.month - 1]} {start.day}, {start.year} to "
        f"{_MONTH_NAMES[last.month - 1]} {last.day}, {last.year}"
    )


def label_month_to_date(window: PeriodWindow) -> str:
    """``label_day_range`` plus the words " (month to date)", appended here only.

    The window is always rendered as a day range: a month-to-date window is
    never a whole month and must never be read as year to date (a January
    window starting Jan 1 would otherwise classify as ``ytd``)."""
    return f"{label_day_range(window)} (month to date)"


__all__ = [
    "KIND_MONTH",
    "KIND_QUARTER",
    "KIND_RANGE",
    "KIND_YEAR",
    "KIND_YTD",
    "WindowKind",
    "add_months",
    "days_in_month",
    "label_day_range",
    "label_month_to_date",
    "label_window",
    "month_window",
    "slot_window",
    "window_kind",
]
