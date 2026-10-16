"""The template narrative: mom-patterns' Overall Summary, Key Drivers and New/Lost
templates, filled from the payload's display strings with zero model calls.

Nothing here computes a number. Every figure is a display string copied verbatim,
and the direction words (increased / decreased, up / down, more / fewer, improved /
declined) are read off the sign character of those same strings. That is why the
template always passes the grounding check against its own payload.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_LIST_CAP = 10


def _d(payload: Mapping[str, Any], metric: str) -> Mapping[str, str]:
    return payload["kpis"][metric]["display"]


def _unsigned(text: str) -> str:
    return text[1:] if text[:1] in "+-" else text


def _change(display: Mapping[str, str], noun: str, unit: str = "") -> str:
    """CHANGE(m): '{noun} increased by 5.6% (+2,350 tons)' and its flat / new forms."""
    pct = display["pct"]
    if pct == "flat":
        return f"{noun} was flat"
    if pct == "new":
        return f"{noun} rose from a zero base to {display['current']}"
    verb = "decreased" if pct.startswith("-") else "increased"
    return f"{noun} {verb} by {_unsigned(pct)} ({display['change']}{unit})"


def _count(display: Mapping[str, str]) -> str:
    change = display["change"]
    if change == "flat":
        return "unchanged"
    return f"{_unsigned(change)} {'fewer' if change.startswith('-') else 'more'}"


def _updown(display: Mapping[str, str]) -> str:
    pct = display["pct"]
    if pct == "flat":
        return "flat"
    if pct == "new":
        return "up from a zero base"
    return f"{'down' if pct.startswith('-') else 'up'} {_unsigned(pct)}"


def _rate(display: Mapping[str, str]) -> str:
    change = display["change"]
    if change == "flat":
        return "held"
    return "declined" if change.startswith("-") else "improved"


def _margin_change(change: str) -> str:
    return "unchanged" if change == "flat" else change


def _summary_section(payload: Mapping[str, Any]) -> str:
    periods = payload["periods"]
    prior, current = periods["prior"]["label"], periods["current"]["label"]
    scope = payload["scope"]
    holder = "office" if scope.get("office_column") else "book"
    margin = _d(payload, "MARGIN")
    won = _d(payload, "NUM_WON")
    inquiries = _d(payload, "NUM_INQUIRIES")
    rate = _d(payload, "WIN_RATE")
    margin_change = margin["change"]
    margin_tail = "unchanged" if margin_change == "flat" else f"{margin_change}/ton"
    return " ".join(
        (
            f"**{prior} to {current}, {scope['label']}.**",
            f"{_change(_d(payload, 'VOLUME'), 'Overall volume', ' tons')},"
            f" while {_change(_d(payload, 'GP'), 'gross profit')}.",
            f"Margin moved from {margin['prior']}/ton to {margin['current']}/ton ({margin_tail}).",
            f"The {holder} won {won['current']} transactions in {current}"
            f" vs {won['prior']} in {prior} ({_count(won)}),"
            f" with inquiries {_updown(inquiries)}"
            f" ({inquiries['current']} vs {inquiries['prior']}).",
            f"The win rate {_rate(rate)} from {rate['prior']} to {rate['current']}.",
        )
    )


def _mover_lines(movers: Sequence[Mapping[str, Any]]) -> list[str]:
    if not movers:
        return ["none"]
    lines: list[str] = []
    for n, mover in enumerate(movers, start=1):
        gp, vol, margin = (mover["display"][m] for m in ("GP", "VOLUME", "MARGIN"))
        lines += [
            f"{n}. **{mover['key']}**: GP {gp['change']} ({gp['pct']}), {mover['driver']}",
            f"   - Volume: {vol['prior']} -> {vol['current']} tons ({vol['change']}, {vol['pct']})",
            f"   - Margin: {margin['prior']} -> {margin['current']}/ton"
            f" ({_margin_change(margin['change'])})",
        ]
    return lines


def _drivers_section(payload: Mapping[str, Any]) -> str:
    movers = payload.get("movers") or {}
    lines = [
        "## Key drivers",
        "",
        "*Top increases by GP:*",
        *_mover_lines(movers.get("increases") or []),
        "*Top decreases by GP:*",
        *_mover_lines(movers.get("decreases") or []),
    ]
    return "\n".join(lines)


def _entries(rows: Sequence[Mapping[str, Any]], side: str) -> str:
    """Up to ten entries, then "; and others".

    The overflow carries no count on purpose: "and 12 more" would be a figure the
    grounding index does not hold (counts over ten are not exempt), so the template
    would fail its own check. The full list is in the report's new/lost tables.
    """
    if not rows:
        return "none"
    shown = [
        f"{row['key']} (GP {row['display']['GP'][side]},"
        f" {row['display']['VOLUME'][side]} tons,"
        f" margin {row['display']['MARGIN'][side]})"
        for row in rows[:_LIST_CAP]
    ]
    text = "; ".join(shown)
    if len(rows) > _LIST_CAP:
        text += "; and others"
    return text


def _new_lost_section(payload: Mapping[str, Any]) -> str:
    periods = payload["periods"]
    new_lost = payload.get("new_lost") or {}
    plural = payload["entity"]["label"].lower() + "s"
    return "\n".join(
        (
            f"## New and lost {plural}",
            "",
            f"**New in {periods['current']['label']}:**"
            f" {_entries(new_lost.get('new') or [], 'current')}",
            f"**Lost from {periods['prior']['label']}:**"
            f" {_entries(new_lost.get('lost') or [], 'prior')}",
        )
    )


def render_template_narrative(payload: Mapping[str, Any]) -> str:
    sections = (
        _summary_section(payload),
        _drivers_section(payload),
        _new_lost_section(payload),
    )
    return "\n\n".join(sections) + "\n"
