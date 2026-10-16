"""An in-memory DataClient for the reporting skill's offline tests, plus
the multi-office fixture rows every test below shares. Mirrors the real
client's contract (one post-COALESCE key per group_by column, None for a
NULLIF-guarded MARGIN, a half-open window) over plain row dicts, so the
skill under test runs the real pipeline against hand-built numbers whose
expectations are derived by hand in test_skill.py."""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from math import fsum

from poseidon.core.data.client import FrameResult, FrameRow, MetricResult, PeriodRange
from poseidon.core.data.specs import FrameQuerySpec, MetricQuerySpec

DIMS = (
    "CUST_NM",
    "SUPPLIER_NM",
    "LOC_NM",
    "CUSTOMER_TEAM_NAME",
    "PRIMARY_SUPPLY_TEAM_OFFICE",
    "DEAL_CLASSIFICATION_TRADE_CUT",
)
SUPPLY_OFFICE = {"Shell": "Med Supply Team", "BP": "NW Europe Supply"}


def _row(day, cust, supplier, port, office, deal, gp, vol, won, inq):
    return {
        "LIFT_ETA_DATE": day,
        "CUST_NM": cust,
        "SUPPLIER_NM": supplier,
        "LOC_NM": port,
        "CUSTOMER_TEAM_NAME": office,
        "PRIMARY_SUPPLY_TEAM_OFFICE": SUPPLY_OFFICE[supplier],
        "DEAL_CLASSIFICATION_TRADE_CUT": deal,
        "GP": float(gp),
        "VOLUME": float(vol),
        "NUM_WON": float(won),
        "NUM_INQUIRIES": float(inq),
    }


MAY, JUNE, APRIL = date(2026, 5, 10), date(2026, 6, 10), date(2026, 4, 10)


def multi_office_rows() -> list[dict]:
    """May and June 2026 for Gibraltar Office (the office under test), with
    Northstar and Meridian also trading under Spain Office so share_of_book
    is below 100% and the offsets have both signs; Atlas trades in one
    office only (share 100%, no offsets); Vega has zero GP in May on real
    volume (zero-base rule); Orion is new, Kestrel is lost; Malta Office
    has rows in May only (Review Focus 1); Valencia Office exists only in
    April (the no-data-in-either-month case)."""
    return [
        # May 2026 (prior)
        _row(
            MAY,
            "Northstar Lines",
            "Shell",
            "Gibraltar",
            "Gibraltar Office",
            "TRADED",
            100_000,
            4_000,
            10,
            20,
        ),
        _row(
            MAY,
            "Northstar Lines",
            "BP",
            "Algeciras",
            "Spain Office",
            "TRADED",
            50_000,
            2_000,
            5,
            10,
        ),
        _row(
            MAY,
            "Meridian Bunkering",
            "Shell",
            "Gibraltar",
            "Gibraltar Office",
            "INVENTORY",
            200_000,
            7_000,
            20,
            30,
        ),
        _row(
            MAY,
            "Meridian Bunkering",
            "BP",
            "Algeciras",
            "Spain Office",
            "INVENTORY",
            100_000,
            3_500,
            10,
            15,
        ),
        _row(
            MAY,
            "Atlas Tankers",
            "Shell",
            "Ceuta",
            "Gibraltar Office",
            "TRADED",
            60_000,
            2_000,
            6,
            10,
        ),
        _row(
            MAY, "Kestrel Lines", "BP", "Gibraltar", "Gibraltar Office", "TRADED", 20_000, 600, 2, 4
        ),
        _row(MAY, "Vega Bulk", "Shell", "Ceuta", "Gibraltar Office", "INVENTORY", 0, 100, 1, 2),
        _row(MAY, "Solent Ferries", "BP", "Malta", "Malta Office", "TRADED", 30_000, 1_000, 3, 6),
        # June 2026 (current)
        _row(
            JUNE,
            "Northstar Lines",
            "Shell",
            "Gibraltar",
            "Gibraltar Office",
            "TRADED",
            130_000,
            5_000,
            12,
            20,
        ),
        _row(
            JUNE,
            "Northstar Lines",
            "BP",
            "Algeciras",
            "Spain Office",
            "TRADED",
            40_000,
            1_600,
            4,
            10,
        ),
        _row(
            JUNE,
            "Meridian Bunkering",
            "Shell",
            "Gibraltar",
            "Gibraltar Office",
            "INVENTORY",
            140_000,
            5_000,
            15,
            30,
        ),
        _row(
            JUNE,
            "Meridian Bunkering",
            "BP",
            "Algeciras",
            "Spain Office",
            "INVENTORY",
            150_000,
            5_000,
            14,
            20,
        ),
        _row(
            JUNE,
            "Atlas Tankers",
            "Shell",
            "Ceuta",
            "Gibraltar Office",
            "TRADED",
            66_000,
            2_000,
            6,
            10,
        ),
        _row(
            JUNE,
            "Orion Offshore",
            "BP",
            "Gibraltar",
            "Gibraltar Office",
            "TRADED",
            12_000,
            500,
            1,
            2,
        ),
        _row(
            JUNE, "Vega Bulk", "Shell", "Ceuta", "Gibraltar Office", "INVENTORY", 5_000, 100, 1, 2
        ),
        # April 2026 only
        _row(
            APRIL,
            "Harbor & Sons",
            "Shell",
            "Valencia",
            "Valencia Office",
            "TRADED",
            10_000,
            400,
            1,
            2,
        ),
    ]


def _metric(rows, name):
    if name == "GP":
        return fsum(r["GP"] for r in rows)
    if name == "VOLUME":
        return fsum(r["VOLUME"] for r in rows)
    if name == "NUM_WON":
        return fsum(r["NUM_WON"] for r in rows)
    if name == "NUM_INQUIRIES":
        return fsum(r["NUM_INQUIRIES"] for r in rows)
    if name == "NUM_LOST":
        return fsum(r["NUM_INQUIRIES"] - r["NUM_WON"] for r in rows)
    if name == "MARGIN":
        vol = fsum(r["VOLUME"] for r in rows)
        return None if vol == 0 else fsum(r["GP"] for r in rows) / vol
    raise KeyError(name)


@dataclass
class FakeDataClient:
    rows: list[dict] = field(default_factory=multi_office_rows)
    tamper_total: float = 0.0  # added to every metric-query GP, to force a reconciliation failure
    fail_frames_with: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def _select(self, window, filters: Mapping[str, tuple[str, ...]]):
        return [
            r
            for r in self.rows
            if window.start <= r["LIFT_ETA_DATE"] < window.end
            and all(r[col] in values for col, values in filters.items())
        ]

    def list_dimension_values(self, entity, column, search=None, scope_value=None):
        self.calls.append("list_dimension_values")
        return sorted({r[column] for r in self.rows})

    def available_periods(self, entity, scope_value=None):
        self.calls.append("available_periods")
        days = [r["LIFT_ETA_DATE"] for r in self.rows]
        return PeriodRange(start=min(days), end=max(days))

    def list_months(self, entity, filters, scope_value=None):
        self.calls.append("list_months")
        return sorted(
            {
                r["LIFT_ETA_DATE"].replace(day=1)
                for r in self.rows
                if all(r[col] in values for col, values in filters.items())
            }
        )

    def run_metric_query(self, spec: MetricQuerySpec) -> MetricResult:
        self.calls.append("run_metric_query")
        rows = self._select(spec.period, spec.filters)
        values = {m: _metric(rows, m) for m in spec.metrics}
        if "GP" in values and values["GP"] is not None:
            values["GP"] += self.tamper_total
        return MetricResult(entity=spec.entity, period=spec.period, values=values)

    def run_breakdown_query(self, spec):
        raise NotImplementedError("the reporting skill never runs a breakdown")

    def run_frame_query(self, spec: FrameQuerySpec) -> FrameResult:
        self.calls.append("run_frame_query")
        if self.fail_frames_with is not None:
            raise self.fail_frames_with
        groups: dict[tuple[str, ...], list[dict]] = {}
        for r in self._select(spec.period, spec.filters):
            groups.setdefault(tuple(r[c] for c in spec.group_by), []).append(r)
        return FrameResult(
            entity=spec.entity,
            group_by=tuple(spec.group_by),
            rows=[
                FrameRow(keys=keys, values={m: _metric(rs, m) for m in spec.metrics})
                for keys, rs in sorted(groups.items())
            ],
        )

    @contextmanager
    def snapshot(self):
        self.calls.append("snapshot")
        yield self
