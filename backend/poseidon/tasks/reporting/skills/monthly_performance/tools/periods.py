"""Period resolution for the monthly report (spec 5.2).

Pure: no data access, no clock. ``resolve_periods`` is the only function that
knows what a comparison basis means. Whether the month is present in the data
is the skill's check, not this module's.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.parsing.period_labels import (
    add_months,
    label_month_to_date,
    label_window,
    month_window,
)

Basis = Literal["prior_month"]
WindowKind = Literal["complete", "mtd"]


class PeriodError(ValueError):
    """Carries the 422 detail the orchestrator shows the user."""


@dataclass(frozen=True)
class ResolvedPeriods:
    current: PeriodWindow
    prior: PeriodWindow
    label_current: str
    label_prior: str
    window_kind: WindowKind


def _label_mtd_prior(prior: PeriodWindow) -> str:
    """``label_window(prior)``, except a prior clamped to its whole month is
    still named as a day range ("February 1 to 28, 2026"): label_window would
    call it "February 2026", which reads as the full month next to a
    month-to-date current."""
    label = label_window(prior)
    if prior.start.day == 1 and add_months(prior.start, 1) == prior.end:
        last = prior.end - timedelta(days=1)
        return f"{calendar.month_name[last.month]} 1 to {last.day}, {last.year}"
    return label


def resolve_periods(
    basis: Basis, month: date, window_kind: WindowKind, reference_date: date
) -> ResolvedPeriods:
    if month.day != 1:
        raise PeriodError("month must be the first day of a month")
    if basis != "prior_month":
        raise PeriodError(f"unknown basis {basis!r}")
    prior_first = add_months(month, -1)

    if window_kind == "complete":
        current = month_window(month.year, month.month)
        if current.end > reference_date:
            raise PeriodError("that month is not complete yet")
        prior = month_window(prior_first.year, prior_first.month)
        return ResolvedPeriods(
            current, prior, label_window(current), label_window(prior), "complete"
        )

    if month != reference_date.replace(day=1):
        raise PeriodError("month to date applies to the running month only")
    n = reference_date.day - 1
    if n == 0:
        raise PeriodError("no complete days in this month yet")
    current = PeriodWindow(month, month + timedelta(days=n))
    prior = PeriodWindow(prior_first, min(prior_first + timedelta(days=n), month))
    return ResolvedPeriods(
        current, prior, label_month_to_date(current), _label_mtd_prior(prior), "mtd"
    )
