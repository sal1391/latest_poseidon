"""Phase 17 PR 0 acceptance (spec 2026-10-12 section 4.6, decision D62):
the C03 scenarios run through the REAL path a conversation takes between
two messages -- parse_turn -> slots_to_json -> slots_from_json -> parse_turn
-- so a carried year, a carried comparison and a replaced comparison are
proven across the serializer, not only inside the parser. The jsonb leg
itself is pinned in test_history_store.py (pg); this file is offline.

No customer names appear in the texts: customer resolution is not the
subject here, and keeping the texts period-only keeps every case about the
period rule alone.
"""

from dataclasses import dataclass, field
from datetime import date

from poseidon.core.chat.history import slots_from_json, slots_to_json
from poseidon.core.data.client import PeriodRange
from poseidon.core.data.specs import PeriodWindow
from poseidon.core.parsing.pipeline import ParsedTurn, parse_turn
from poseidon.core.skills.context import ConversationSlots

REF = date(2026, 10, 12)
AVAILABLE = PeriodRange(date(2024, 1, 1), date(2026, 9, 30))


@dataclass
class _Client:
    """A minimal offline DataClient: parse_turn only ever asks it for
    dimension values (none are named here) and the available range."""

    calls: list[str] = field(default_factory=list)

    def list_dimension_values(self, entity: str, column: str, search: str | None = None):
        self.calls.append(column)
        return []

    def available_periods(self, entity: str) -> PeriodRange:
        return AVAILABLE

    def run_metric_query(self, spec):  # pragma: no cover
        raise NotImplementedError

    def run_breakdown_query(self, spec):  # pragma: no cover
        raise NotImplementedError


def _month(year: int, month: int) -> PeriodWindow:
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return PeriodWindow(date(year, month, 1), date(end_year, end_month, 1))


def _reload(slots: ConversationSlots) -> ConversationSlots:
    """The round trip every real turn makes through the conversations.state
    jsonb column."""
    return slots_from_json(slots_to_json(slots))


def _conversation(*texts: str, start: ConversationSlots | None = None) -> list[ParsedTurn]:
    slots = start if start is not None else ConversationSlots()
    turns = []
    for text in texts:
        turn = parse_turn(text, slots, REF, _Client())
        turns.append(turn)
        slots = _reload(turn.slots)
    return turns


def test_1_a_full_year_survives_then_by_port():
    first, second = _conversation("gp for 2025 by month", "then by port")

    assert first.period_a == PeriodWindow(date(2025, 1, 1), date(2026, 1, 1))
    assert second.period_a == PeriodWindow(date(2025, 1, 1), date(2026, 1, 1))
    assert second.slots.period_a == date(2025, 1, 1)
    assert second.slots.period_a_end == date(2026, 1, 1)


def test_2_a_report_comparison_survives_why_did_gp_drop():
    first, second = _conversation("gp in july 2026 vs june 2026", "why did gp drop?")

    assert (first.period_a, first.period_b) == (_month(2026, 7), _month(2026, 6))
    assert (second.period_a, second.period_b) == (_month(2026, 7), _month(2026, 6))
    assert second.slots.period_b == date(2026, 6, 1)
    assert second.slots.period_b_end == date(2026, 7, 1)


def test_3_compare_to_another_month_swaps_only_the_comparison():
    turns = _conversation(
        "gp in july 2026 vs june 2026", "why did gp drop?", "compare to may 2026 instead"
    )
    third = turns[-1]

    assert (third.period_a, third.period_b) == (_month(2026, 7), _month(2026, 5))
    assert third.slots.period_a == date(2026, 7, 1)
    assert third.slots.period_b == date(2026, 5, 1)
    assert third.slots.period_b_end == date(2026, 6, 1)


def test_4_a_single_period_drops_the_comparison():
    turns = _conversation("gp in july 2026 vs june 2026", "why did gp drop?", "gp for august 2026")
    third = turns[-1]

    assert third.period_a == _month(2026, 8)
    assert third.period_b is None
    assert third.slots.period_a == date(2026, 8, 1)
    assert third.slots.period_a_end == date(2026, 9, 1)
    assert third.slots.period_b is None
    assert third.slots.period_b_end is None


def test_5_a_quarter_survives_a_reload():
    first, second = _conversation("gp for q2 2026", "and margin")

    assert first.period_a == PeriodWindow(date(2026, 4, 1), date(2026, 7, 1))
    assert second.period_a == PeriodWindow(date(2026, 4, 1), date(2026, 7, 1))
    assert second.slots.period_a_end == date(2026, 7, 1)


def test_6_a_pre_phase_17_row_reloads_as_the_month_window():
    """A row written before this PR holds starts only. It carries the month
    containing the start -- the pre-PR behaviour -- and the next turn's
    write-back fills the ends in."""
    legacy = slots_from_json({"period_a": "2025-01-01", "period_b": None, "mode": "default"})
    assert legacy.period_a_end is None

    (turn,) = _conversation("then by port", start=legacy)

    assert turn.period_a == _month(2025, 1)
    assert turn.slots.period_a == date(2025, 1, 1)
    # Carried, not re-named this turn: the end stays unset until a phrase
    # names a period; the parser keeps reading it as the month.
    assert turn.slots.period_a_end is None
