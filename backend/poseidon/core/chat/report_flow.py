"""The monthly report flow (Phase 17 PR A2): the third deterministic
conversation flow, ``mode = "report"``.

The opener's "Monthly report" chip sends :data:`ENTRY_PHRASE_REPORT`; the
orchestrator's entry branch resets the four report slots and asks the
perspective. Each later turn in report mode is one step -- perspective,
office (skipped for the whole book), month -- answered from chips or typed
text. The month step validates the month itself (present in the data,
complete against the reference date, or month to date with at least one
complete day) before anything is dispatched, so a bad month is a clarify
turn with no tool frames. A valid month makes the orchestrator dispatch
``reporting.monthly_performance`` by hand with :func:`report_arguments`.

This module is PURE: it holds every copy string and every step function,
and imports only ``core.parsing``, ``core.skills.context`` and
``core.data.client``. It never imports ``poseidon.tasks`` or the
orchestrator; the orchestrator owns the turn plumbing (frames, run log,
state writes). :data:`REPORT_SKILL_ID` is a bare string literal for the same
layering reason the orchestrator's own brief skill ids are.
"""

import dataclasses
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from poseidon.core.data.client import DataClient
from poseidon.core.parsing.period_labels import (
    KIND_MONTH,
    label_window,
    month_window,
    window_kind,
)
from poseidon.core.parsing.period_parser import parse_periods
from poseidon.core.skills.context import ConversationSlots

ENTITY = "MARINE_SALES_PLANNING_V"
MODE_REPORT = "report"
ENTRY_PHRASE_REPORT = "start a monthly report"
REPORT_SKILL_ID = "reporting.monthly_performance"

OFFICE_COLUMN = {
    "customer": "CUSTOMER_TEAM_NAME",
    "supplier": "PRIMARY_SUPPLY_TEAM_OFFICE",
    "book": None,
}
ENTITY_COLUMN = {"customer": "CUST_NM", "supplier": "SUPPLIER_NM", "book": "CUST_NM"}

PERSPECTIVE_PROMPT = "Monthly performance report. Which perspective?"
PERSPECTIVE_REASK = "Pick a perspective: Customer office, Supplier office or Whole book."
PERSPECTIVE_CHIPS = [
    {"id": "customer", "label": "Customer office", "send_text": "customer office report"},
    {"id": "supplier", "label": "Supplier office", "send_text": "supplier office report"},
    {"id": "book", "label": "Whole book", "send_text": "whole book report"},
]
OFFICE_PROMPT = {"customer": "Which customer office?", "supplier": "Which supply office?"}
OFFICE_REASK = "I don't recognise that office. Pick one:"
MONTH_PROMPT = "Which month? Pick one or type it, for example 'June 2026' or 'month to date'."
MONTH_NOT_COMPLETE = "That month is not complete yet."
MONTH_NO_DATA = "No data for {label}."
MONTH_UNREADABLE = "I couldn't read that as a month. Pick one or type it like 'June 2026'."
MTD_DAY_ONE = "No complete days in this month yet."
MTD_RE = re.compile(r"\b(month to date|mtd|so far this month|this month so far)\b")

# Casefolded exact phrases that pick a perspective: the three chip
# send_texts plus the short forms a person types.
_PERSPECTIVE_BY_PHRASE = {
    "customer office report": "customer",
    "supplier office report": "supplier",
    "whole book report": "book",
    "customer": "customer",
    "supplier": "supplier",
    "book": "book",
    "whole book": "book",
    "customer office": "customer",
    "supplier office": "supplier",
}

_MONTH_CHIP_COUNT = 3
_LATEST_SUFFIX = " (latest)"

Step = Literal["perspective", "office", "month"]


def _clean(text: str) -> str:
    """Casefolded, trimmed, single-spaced."""
    return " ".join(text.casefold().split())


def flow_slots_reset(slots: ConversationSlots) -> ConversationSlots:
    """A fresh report flow: mode ``report`` and the five report fields
    cleared; every other slot (customer, periods, pass_through) is kept."""
    return dataclasses.replace(
        slots,
        mode=MODE_REPORT,
        perspective=None,
        office_column=None,
        office_value=None,
        report_ref=None,
        report_label=None,
    )


def step_for(slots: ConversationSlots) -> Step:
    """Which question the flow is waiting on."""
    if slots.perspective is None:
        return "perspective"
    if OFFICE_COLUMN[slots.perspective] is not None and slots.office_value is None:
        return "office"
    return "month"


def match_perspective(text: str) -> str | None:
    """``customer`` | ``supplier`` | ``book`` for an exact (casefolded)
    phrase, else ``None``."""
    return _PERSPECTIVE_BY_PHRASE.get(_clean(text))


def match_office(text: str, values: Sequence[str]) -> str | None:
    """The certified value ``text`` names, casefolded exact, else ``None``."""
    wanted = _clean(text)
    return next((value for value in values if _clean(value) == wanted), None)


@dataclass(frozen=True)
class MonthChoice:
    month: date  # first day of the month
    window_kind: str  # "complete" | "mtd"


def resolve_month(text: str, data: DataClient, reference_date: date) -> MonthChoice | str:
    """The month ``text`` names, validated; a ``str`` is the re-ask reason.

    Month to date is read here: the four MTD phrases, or the running month
    named directly ("June 2026" or "june" while June is running). Any other
    month goes through ``parse_periods`` unchanged and must be a single
    calendar month. The running month needs at least one complete day; a
    later month is not complete; every month must be present in the data.
    """
    running = reference_date.replace(day=1)
    if MTD_RE.search(_clean(text)):
        month = running
    else:
        available = data.available_periods(ENTITY)
        parsed = parse_periods(text, ConversationSlots(), reference_date, available)
        window = parsed.period_a
        if window is None or window_kind(window) != KIND_MONTH:
            return MONTH_UNREADABLE
        month = window.start
    if month > running:
        return MONTH_NOT_COMPLETE
    kind = "mtd" if month == running else "complete"
    if kind == "mtd" and reference_date.day == 1:
        return MTD_DAY_ONE
    if month not in data.list_months(ENTITY, {}):
        return MONTH_NO_DATA.format(label=label_window(month_window(month.year, month.month)))
    return MonthChoice(month=month, window_kind=kind)


def month_chips(data: DataClient, reference_date: date) -> list[dict]:
    """The last three complete months present in the data, newest first;
    the newest is labelled ``(latest)``. A month is complete when its end
    is on or before ``reference_date``."""
    complete = [
        month
        for month in data.list_months(ENTITY, {})
        if month_window(month.year, month.month).end <= reference_date
    ]
    newest = sorted(complete, reverse=True)[:_MONTH_CHIP_COUNT]
    chips = []
    for index, month in enumerate(newest):
        name = label_window(month_window(month.year, month.month))
        chips.append(
            {
                "id": month.isoformat(),
                "label": name + (_LATEST_SUFFIX if index == 0 else ""),
                "send_text": name,
            }
        )
    return chips


def office_chips(data: DataClient, office_column: str) -> list[dict]:
    """One chip per certified office value, in the data client's order."""
    return [
        {"id": value, "label": value, "send_text": value}
        for value in data.list_dimension_values(ENTITY, office_column)
    ]


def step_parts(
    step: Step,
    slots: ConversationSlots,
    data: DataClient,
    reference_date: date,
    reask: str | None = None,
) -> list[dict]:
    """The step's question as ``[text part, chips part]``; the text is the
    step's prompt, or ``reask`` when the last answer was not usable. The
    chips part is left out only when there is nothing to offer."""
    if step == "perspective":
        prompt = PERSPECTIVE_PROMPT
        options = [dict(chip) for chip in PERSPECTIVE_CHIPS]
    elif step == "office":
        perspective = slots.perspective or ""
        prompt = OFFICE_PROMPT[perspective]
        options = office_chips(data, OFFICE_COLUMN[perspective] or "")
    else:
        prompt = MONTH_PROMPT
        options = month_chips(data, reference_date)
    parts = [{"kind": "text", "payload": {"markdown": reask if reask is not None else prompt}}]
    if options:
        parts.append({"kind": "chips", "payload": {"options": options}})
    return parts


def report_arguments(slots: ConversationSlots, choice: MonthChoice) -> dict:
    """The ``reporting.monthly_performance`` arguments the flow built."""
    return {
        "perspective": slots.perspective,
        "office_value": slots.office_value,
        "month": choice.month.isoformat(),
        "window_kind": choice.window_kind,
    }


def slots_after_report(slots: ConversationSlots, payload: dict) -> ConversationSlots:
    """Back to ``default`` mode carrying what the report was about: its two
    windows as the periods, its run id, the office (kept from ``slots``),
    and the top ten entities by GP as the pass-through."""
    periods = payload["periods"]
    column = ENTITY_COLUMN[payload["perspective"]]
    rows = payload["tables"]["entities_by_gp"]["rows"][:10]
    return dataclasses.replace(
        slots,
        mode="default",
        period_a=date.fromisoformat(periods["current"]["start"]),
        period_a_end=date.fromisoformat(periods["current"]["end"]),
        period_b=date.fromisoformat(periods["prior"]["start"]),
        period_b_end=date.fromisoformat(periods["prior"]["end"]),
        report_ref=payload["run_id"],
        report_label=(
            f"{slots.office_value or 'Whole book'}, "
            f"{periods['current']['label']} vs {periods['prior']['label']}"
        ),
        pass_through=tuple((column, row["key"]) for row in rows),
    )


def answer_summary(payload: dict) -> str:
    """The run log's one-line summary of a finished report."""
    periods = payload["periods"]
    return (
        f"Report generated: {payload['scope']['label']}, "
        f"{periods['current']['label']} vs {periods['prior']['label']}."
    )
