"""The entity_context section: where each mover stands in the whole book.

For every mover of an office report, the two whole-book context frames
(filtered to the movers, every office) give its book deltas, its office's
share of its book GP per side, the reconciliation ``office change + other
offices net = book change``, the other offices that moved the opposite way
(offsets) and the ports outside the office that moved it (rest of book).

These are marginals of one grouped frame, stated side by side. Nothing here
claims that an offset caused the office's change.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from poseidon.core.analytics import mom
from poseidon.core.data import FrameResult

from .frames import rollup
from .payload import kpi_from_delta

if TYPE_CHECKING:
    from ..skill import Perspective

_TOP = 5
_MONEY = mom.KIND_BY_METRIC["GP"]


def _only(frame: FrameResult, column: str, keep) -> FrameResult:
    position = frame.group_by.index(column)
    return replace(frame, rows=[r for r in frame.rows if keep(r.keys[position])])


def _gp(groups: dict[str, dict[str, float]], key: str) -> float:
    return groups.get(key, {}).get("GP", 0.0)


def _changes(prior: FrameResult, current: FrameResult, column: str) -> dict[str, float]:
    """GP change per value of ``column``; a value on one side only counts 0 on the other."""
    a, b = rollup(prior, column), rollup(current, column)
    return {key: _gp(b, key) - _gp(a, key) for key in sorted(set(a) | set(b))}


def _items(changes: dict[str, float]) -> list[dict[str, Any]]:
    ordered = sorted(changes.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[:_TOP]
    return [{"key": k, "change": c, "display": mom.fmt_change(c, _MONEY)} for k, c in ordered]


def _share_display(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def build_entity_context(
    book_prior: FrameResult,
    book_current: FrameResult,
    office_prior: FrameResult,
    office_current: FrameResult,
    perspective: Perspective,
    office_value: str,
    movers: Sequence[str],
) -> dict[str, dict]:
    entity_column = perspective.entity_column
    office_column = perspective.office_column
    if office_column is None:
        raise ValueError("entity context needs an office perspective")
    office_a = rollup(office_prior, entity_column)
    office_b = rollup(office_current, entity_column)

    out: dict[str, dict] = {}
    for key in movers:
        mine_a = _only(book_prior, entity_column, lambda v, k=key: v == k)
        mine_b = _only(book_current, entity_column, lambda v, k=key: v == k)
        book_a = mom.totals(r.values for r in mine_a.rows)
        book_b = mom.totals(r.values for r in mine_b.rows)
        book = {m: kpi_from_delta(mom.delta(book_a[m], book_b[m]), m) for m in mom.ROW_METRICS}

        office_gp_a, office_gp_b = _gp(office_a, key), _gp(office_b, key)
        share_a = mom.share(office_gp_a, book_a["GP"])
        share_b = mom.share(office_gp_b, book_b["GP"])

        office_change = office_gp_b - office_gp_a
        book_change = book_b["GP"] - book_a["GP"]
        other_offices_net = book_change - office_change

        by_office = _changes(mine_a, mine_b, office_column)
        offsets = (
            {}
            if office_change == 0
            else {
                office: change
                for office, change in by_office.items()
                if office != office_value and change * office_change < 0
            }
        )

        def elsewhere(value: str) -> bool:
            return value != office_value

        ports = _changes(
            _only(mine_a, office_column, elsewhere),
            _only(mine_b, office_column, elsewhere),
            "LOC_NM",
        )

        out[key] = {
            "book": book,
            "share_of_book": {
                "prior": share_a,
                "current": share_b,
                "display": {"prior": _share_display(share_a), "current": _share_display(share_b)},
            },
            "reconciliation": {
                "office_change": office_change,
                "other_offices_net": other_offices_net,
                "book_change": book_change,
                "display": {
                    "office_change": mom.fmt_change(office_change, _MONEY),
                    "other_offices_net": mom.fmt_change(other_offices_net, _MONEY),
                    "book_change": mom.fmt_change(book_change, _MONEY),
                },
            },
            "offsets": _items(offsets),
            "ports_rest_of_book": _items(ports),
        }
    return out
