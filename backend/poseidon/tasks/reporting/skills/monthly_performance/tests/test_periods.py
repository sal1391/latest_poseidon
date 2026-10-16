from datetime import date

import pytest

from poseidon.core.data.specs import PeriodWindow
from poseidon.tasks.reporting.skills.monthly_performance.tools.periods import (
    PeriodError,
    resolve_periods,
)

OCT_12 = date(2026, 10, 12)


def test_complete_month_labels_both_months_by_name():
    r = resolve_periods("prior_month", date(2026, 9, 1), "complete", OCT_12)
    assert r.current == PeriodWindow(date(2026, 9, 1), date(2026, 10, 1))
    assert r.prior == PeriodWindow(date(2026, 8, 1), date(2026, 9, 1))
    assert (r.label_current, r.label_prior) == ("September 2026", "August 2026")
    assert r.window_kind == "complete"


def test_january_prior_is_december_of_the_year_before():
    r = resolve_periods("prior_month", date(2026, 1, 1), "complete", OCT_12)
    assert r.prior == PeriodWindow(date(2025, 12, 1), date(2026, 1, 1))
    assert r.label_prior == "December 2025"


def test_running_month_as_complete_is_refused():
    with pytest.raises(PeriodError, match="not complete yet"):
        resolve_periods("prior_month", date(2026, 10, 1), "complete", OCT_12)


def test_mtd_builds_day_matched_windows_and_names_them():
    r = resolve_periods("prior_month", date(2026, 10, 1), "mtd", OCT_12)
    assert r.current == PeriodWindow(date(2026, 10, 1), date(2026, 10, 12))
    assert r.prior == PeriodWindow(date(2026, 9, 1), date(2026, 9, 12))
    assert r.label_current == "October 1 to 11, 2026 (month to date)"
    assert r.label_prior == "September 1 to 11, 2026"
    assert r.window_kind == "mtd"


def test_mtd_prior_is_clamped_to_the_prior_month_length():
    r = resolve_periods("prior_month", date(2026, 3, 1), "mtd", date(2026, 3, 31))
    assert r.current == PeriodWindow(date(2026, 3, 1), date(2026, 3, 31))
    assert r.prior == PeriodWindow(date(2026, 2, 1), date(2026, 3, 1))
    assert r.label_prior == "February 1 to 28, 2026"


def test_mtd_on_the_first_of_the_month_refuses():
    with pytest.raises(PeriodError, match="no complete days"):
        resolve_periods("prior_month", date(2026, 10, 1), "mtd", date(2026, 10, 1))


def test_mtd_for_a_month_that_is_not_running_is_refused():
    with pytest.raises(PeriodError, match="running month only"):
        resolve_periods("prior_month", date(2026, 9, 1), "mtd", OCT_12)


def test_month_must_be_a_first_day():
    with pytest.raises(PeriodError, match="first day of a month"):
        resolve_periods("prior_month", date(2026, 9, 15), "complete", OCT_12)


def test_mtd_in_january_labels_a_day_range_and_prior_december():
    r = resolve_periods("prior_month", date(2027, 1, 1), "mtd", date(2027, 1, 12))
    assert r.label_current == "January 1 to 11, 2027 (month to date)"
    assert r.prior == PeriodWindow(date(2026, 12, 1), date(2026, 12, 12))
    assert r.label_prior == "December 1 to 11, 2026"
