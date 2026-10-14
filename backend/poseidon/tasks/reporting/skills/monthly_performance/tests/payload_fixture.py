"""The payload v1 contract in code: a complete, internally consistent report payload.

A plain function building dicts. It imports nothing from the tools package, so later
tests can use it before those modules exist. Every display string is ASCII.

``mockup_payload()`` carries the figures of the monthly-report mockup and is built
through ``poseidon.core.analytics.mom`` (core, not the tools package) from raw numbers,
so its display strings are the engine's own rather than hand-typed.
"""

from __future__ import annotations

from typing import Any

from poseidon.core.analytics import mom

METRICS = ("GP", "VOLUME", "MARGIN")


def _money(v: float, signed: bool = False) -> str:
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    return f"{sign}${abs(v):,.0f}"


def _tons(v: float, signed: bool = False) -> str:
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    return f"{sign}{abs(v):,.0f}"


def _per_ton(v: float, signed: bool = False) -> str:
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    return f"{sign}${abs(v):,.2f}"


def _pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    return f"{'+' if v > 0 else ''}{v:.1f}%"


_FMT = {"GP": _money, "VOLUME": _tons, "MARGIN": _per_ton}


def _delta(metric: str, prior: float, current: float) -> dict[str, Any]:
    fmt = _FMT[metric]
    change = current - prior
    pct = None if prior == 0 else change / prior * 100
    return {
        "prior": prior,
        "current": current,
        "change": change,
        "pct_change": pct,
        "display": {
            "prior": fmt(prior),
            "current": fmt(current),
            "change": fmt(change, True),
            "pct": _pct(pct),
        },
    }


def _row(
    key: str, status: str, gp: tuple[float, float], vol: tuple[float, float]
) -> dict[str, Any]:
    margin = tuple((g / v if v else 0.0) for g, v in zip(gp, vol, strict=True))
    d = {
        "GP": _delta("GP", *gp),
        "VOLUME": _delta("VOLUME", *vol),
        "MARGIN": _delta("MARGIN", *margin),
    }
    return {
        "key": key,
        "status": status,
        "prior": {m: d[m]["prior"] for m in METRICS},
        "current": {m: d[m]["current"] for m in METRICS},
        "change": {m: d[m]["change"] for m in METRICS},
        "pct": {m: d[m]["pct_change"] for m in METRICS},
        "display": {m: d[m]["display"] for m in METRICS},
    }


def _kpi_footer(kpis: dict[str, Any]) -> dict[str, Any]:
    d = {m: kpis[m] for m in METRICS}
    return {
        "key": "Total",
        "status": "both",
        "prior": {m: d[m]["prior"] for m in METRICS},
        "current": {m: d[m]["current"] for m in METRICS},
        "change": {m: d[m]["change"] for m in METRICS},
        "pct": {m: d[m]["pct_change"] for m in METRICS},
        "display": {m: dict(d[m]["display"]) for m in METRICS},
    }


def minimal_payload(**overrides: Any) -> dict[str, Any]:
    kpis = {
        "VOLUME": _delta("VOLUME", 1000, 1100),
        "GP": _delta("GP", 100000, 120000),
        "MARGIN": _delta("MARGIN", 100.0, 120000 / 1100),
        "NUM_WON": _delta("VOLUME", 10, 12),
        "NUM_INQUIRIES": _delta("VOLUME", 20, 25),
        "NUM_LOST": _delta("VOLUME", 5, 6),
        "WIN_RATE": _delta("VOLUME", 50, 48),
    }

    def table(rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {"rows": rows, "other": None, "footer": _kpi_footer(kpis)}

    northstar = _row("Northstar Lines", "both", (60000, 50000), (600, 500))
    meridian = _row("Meridian Bunkering", "both", (40000, 70000), (400, 600))
    rotterdam = _row("Rotterdam", "both", (70000, 80000), (700, 750))
    singapore = _row("Singapore", "both", (30000, 40000), (300, 350))
    spot = _row("Spot", "both", (80000, 90000), (800, 850))
    term = _row("Term", "both", (20000, 30000), (200, 250))

    inc = dict(meridian, driver="volume-driven")
    dec = dict(northstar, driver="volume-driven")
    new = _row("Oceanic Fuels", "new", (0, 5000), (0, 50))
    lost = _row("Harbor Tankers", "lost", (4000, 0), (40, 0))

    payload: dict[str, Any] = {
        "payload_version": 1,
        "run_id": "run-test-0001",
        "perspective": "customer",
        "entity": {"column": "CUST_NM", "label": "Customer"},
        "scope": {
            "office_column": "CUST_BROKER_OFFICE",
            "office_value": "Rotterdam",
            "label": "Rotterdam (Customer Broker Office)",
        },
        "periods": {
            "current": {"start": "2026-09-01", "end": "2026-09-30", "label": "September 2026"},
            "prior": {"start": "2026-08-01", "end": "2026-08-31", "label": "August 2026"},
            "window_kind": "complete",
        },
        "kpis": kpis,
        "tables": {
            "entities_by_gp": table([meridian, northstar]),
            "entities_by_volume": table([dict(meridian), dict(northstar)]),
            "ports_by_gp": table([rotterdam, singapore]),
            "ports_by_volume": table([dict(rotterdam), dict(singapore)]),
            "deal_class": table([spot, term]),
        },
        "movers": {"increases": [inc], "decreases": [dec]},
        "new_lost": {"new": [new], "lost": [lost]},
        "entity_context": {
            "Meridian Bunkering": {
                "book": {
                    "GP": _delta("GP", 90000, 130000),
                    "VOLUME": _delta("VOLUME", 900, 1200),
                    "MARGIN": _delta("MARGIN", 100.0, 130000 / 1200),
                },
                "share_of_book": {
                    "prior": 44.4,
                    "current": 53.8,
                    "display": {"prior": "44.4%", "current": "53.8%"},
                },
                "reconciliation": {
                    "office_change": 30000,
                    "other_offices_net": 10000,
                    "book_change": 40000,
                    "display": {
                        "office_change": "+$30,000",
                        "other_offices_net": "+$10,000",
                        "book_change": "+$40,000",
                    },
                },
                "offsets": [{"key": "Northstar Lines", "change": -10000, "display": "-$10,000"}],
                "ports_rest_of_book": [
                    {"key": "Singapore", "change": 10000, "display": "+$10,000"}
                ],
            }
        },
        "narrative": {
            "template_md": (
                "**Gross profit rose to $120,000 in September 2026.**\n\n"
                "Meridian Bunkering added +$30,000 while Northstar Lines gave back $10,000."
            ),
            "llm_md": None,
            "grounded": False,
            "flags": [],
        },
        "suggested_questions": [
            "Why did Meridian Bunkering grow?",
            "What drove the Northstar Lines decline?",
            "Which ports gained the most?",
        ],
        "proof": ["GP current = sum of GP for September 2026 = $120,000"],
        "queries": [{"label": "kpis", "sql_hash": "abc123", "rows": 7, "ms": 12}],
        "generated_at": "2026-10-14T09:30:00+00:00",
        "source": "synthetic",
        "generated_by": "dev|test",
    }
    payload.update(overrides)
    return payload


# --- The mockup payload (docs/reference/mockups/2026-09-04-monthly-report-mockup.html) ---


def _mdelta(prior: float, current: float, kind: mom.Kind) -> dict[str, Any]:
    """A KPI or book DELTA whose display strings come from ``mom.display``."""
    d = mom.delta(prior, current)
    return {
        "prior": d.prior,
        "current": d.current,
        "change": d.change,
        "pct_change": d.pct_change,
        "display": mom.display(d, kind),
    }


def _side_margin(gp: float, vol: float) -> float:
    return gp / vol if vol else 0.0


def _mrow(
    key: str,
    gp_prior: float,
    vol_prior: float,
    gp_current: float,
    vol_current: float,
    status: str = "both",
) -> dict[str, Any]:
    """A ROW built from ``mom.delta`` and ``mom.display`` (MARGIN per side is gp / vol).

    Named ``_mrow`` (the brief's ``_row``) because ``_row`` above already serves
    ``minimal_payload`` with a different signature.
    """
    sides = {
        "GP": (gp_prior, gp_current),
        "VOLUME": (vol_prior, vol_current),
        "MARGIN": (_side_margin(gp_prior, vol_prior), _side_margin(gp_current, vol_current)),
    }
    d = {m: mom.delta(*sides[m]) for m in METRICS}
    return {
        "key": key,
        "status": status,
        "prior": {m: d[m].prior for m in METRICS},
        "current": {m: d[m].current for m in METRICS},
        "change": {m: d[m].change for m in METRICS},
        "pct": {m: d[m].pct_change for m in METRICS},
        "display": {m: mom.display(d[m], mom.KIND_BY_METRIC[m]) for m in METRICS},
    }


def _share_display(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _context(
    office: tuple[float, float],
    book_gp: tuple[float, float],
    book_vol: tuple[float, float],
    reconciliation: tuple[float, float, float],
    offsets: list[tuple[str, float]],
    ports_rest_of_book: list[tuple[str, float]],
) -> dict[str, Any]:
    """One ``entity_context`` entry; ``reconciliation`` is (office, book, other offices)."""
    share_prior = mom.share(office[0], book_gp[0])
    share_current = mom.share(office[1], book_gp[1])
    office_change, book_change, other_offices_net = reconciliation
    money = mom.KIND_BY_METRIC["GP"]
    return {
        "book": {
            "GP": _mdelta(*book_gp, "money"),
            "VOLUME": _mdelta(*book_vol, "tons"),
            "MARGIN": _mdelta(
                _side_margin(book_gp[0], book_vol[0]),
                _side_margin(book_gp[1], book_vol[1]),
                "margin",
            ),
        },
        "share_of_book": {
            "prior": share_prior,
            "current": share_current,
            "display": {
                "prior": _share_display(share_prior),
                "current": _share_display(share_current),
            },
        },
        "reconciliation": {
            "office_change": office_change,
            "other_offices_net": other_offices_net,
            "book_change": book_change,
            "display": {
                "office_change": mom.fmt_change(office_change, money),
                "other_offices_net": mom.fmt_change(other_offices_net, money),
                "book_change": mom.fmt_change(book_change, money),
            },
        },
        "offsets": [
            {"key": k, "change": c, "display": mom.fmt_change(c, money)} for k, c in offsets
        ],
        "ports_rest_of_book": [
            {"key": k, "change": c, "display": mom.fmt_change(c, money)}
            for k, c in ports_rest_of_book
        ],
    }


# (key, GP prior, GP current, VOLUME prior, VOLUME current), ranked by the current month.
_MOCKUP_CUSTOMERS = (
    ("Northstar Lines", 142_600, 171_900, 4_810, 5_960),
    ("Meridian Bunkering", 214_800, 158_300, 7_270, 5_120),
    ("Pacific Crest Shipping", 121_400, 128_700, 4_120, 4_390),
    ("Atlas Tankers", 98_200, 104_500, 3_560, 3_480),
    ("Bluewater Carriers", 87_900, 92_100, 2_980, 3_210),
    ("Helix Container Line", 76_300, 79_800, 2_610, 2_790),
    ("Corsair Maritime", 71_500, 68_200, 2_450, 2_510),
    ("Solent Ferries", 54_100, 58_900, 1_890, 2_140),
    ("Vega Bulk", 49_800, 47_300, 1_720, 1_690),
    ("Harbor & Sons", 38_200, 41_600, 1_310, 1_480),
)
_MOCKUP_PORTS = (
    ("Gibraltar", 655_100, 612_400, 21_400, 22_900),
    ("Algeciras", 221_300, 238_900, 7_900, 9_100),
    ("Ceuta", 118_800, 121_300, 4_400, 4_600),
    ("Tangier", 104_200, 98_700, 3_900, 3_700),
    ("Las Palmas", 70_600, 62_100, 2_600, 2_300),
    ("Malta", 31_900, 28_400, 1_050, 950),
    ("Barcelona", 19_800, 12_600, 380, 420),
    ("Valencia", 14_700, 8_500, 230, 240),
)
_MOCKUP_DEAL_CLASSES = (
    ("TRADED", 742_100, 688_400, 24_900, 25_700),
    ("INVENTORY", 351_600, 356_900, 12_400, 13_100),
    ("CONTRACTED", 142_700, 137_600, 4_560, 5_410),
)

_Spec = tuple[tuple[str, float, float, float, float], ...]


def mockup_payload(**overrides: Any) -> dict[str, Any]:
    """The mockup's Gibraltar Office report, July 2026 -> August 2026, customer perspective.

    KPI raw totals go through ``mom.totals`` so MARGIN and WIN_RATE are derived, never typed.
    """
    prior = mom.totals(
        [{"VOLUME": 41_860, "GP": 1_236_400, "NUM_WON": 312, "NUM_INQUIRIES": 508, "NUM_LOST": 196}]
    )
    current = mom.totals(
        [{"VOLUME": 44_210, "GP": 1_182_900, "NUM_WON": 298, "NUM_INQUIRIES": 531, "NUM_LOST": 233}]
    )
    kpis = {m: _mdelta(prior[m], current[m], mom.KIND_BY_METRIC[m]) for m in mom.KPI_METRICS}

    def rows(spec: _Spec) -> list[dict[str, Any]]:
        return [_mrow(k, gp_a, vol_a, gp_b, vol_b) for k, gp_a, gp_b, vol_a, vol_b in spec]

    def table(spec: _Spec) -> dict[str, Any]:
        return {"rows": rows(spec), "other": None, "footer": _kpi_footer(kpis)}

    by_key = {r["key"]: r for r in rows(_MOCKUP_CUSTOMERS)}

    def mover(key: str, driver: str) -> dict[str, Any]:
        return dict(by_key[key], driver=driver)

    payload: dict[str, Any] = {
        "payload_version": 1,
        "run_id": "run-mockup-0001",
        "perspective": "customer",
        "entity": {"column": "CUST_NM", "label": "Customer"},
        "scope": {
            "office_column": "CUST_BROKER_OFFICE",
            "office_value": "Gibraltar Office",
            "label": "Gibraltar Office (Customer Broker Office)",
        },
        "periods": {
            "current": {"start": "2026-08-01", "end": "2026-08-31", "label": "August 2026"},
            "prior": {"start": "2026-07-01", "end": "2026-07-31", "label": "July 2026"},
            "window_kind": "complete",
        },
        "kpis": kpis,
        "tables": {
            "entities_by_gp": table(_MOCKUP_CUSTOMERS),
            "entities_by_volume": table(_MOCKUP_CUSTOMERS),
            "ports_by_gp": table(_MOCKUP_PORTS),
            "ports_by_volume": table(_MOCKUP_PORTS),
            "deal_class": table(_MOCKUP_DEAL_CLASSES),
        },
        "movers": {
            "increases": [
                mover("Northstar Lines", "volume-driven"),
                mover("Pacific Crest Shipping", "volume-driven"),
                mover("Atlas Tankers", "margin-driven"),
            ],
            "decreases": [
                mover("Meridian Bunkering", "volume-driven"),
                mover("Corsair Maritime", "margin-driven"),
                mover("Vega Bulk", "both"),
            ],
        },
        "new_lost": {
            "new": [_mrow("Orion Offshore", 0, 0, 12_400, 480, status="new")],
            "lost": [_mrow("Kestrel Lines", 18_900, 610, 0, 0, status="lost")],
        },
        "entity_context": {
            "Meridian Bunkering": _context(
                office=(214_800, 158_300),
                book_gp=(402_100, 389_700),
                book_vol=(13_600, 12_600),
                reconciliation=(-56_500, -12_400, 44_100),
                offsets=[("Spain Office", 41_000), ("Med Supply Team", 28_400)],
                ports_rest_of_book=[("Algeciras", 31_200)],
            ),
            "Northstar Lines": _context(
                office=(142_600, 171_900),
                book_gp=(301_200, 322_800),
                book_vol=(10_200, 11_200),
                reconciliation=(29_300, 21_600, -7_700),
                offsets=[("Rotterdam", -7_700)],
                ports_rest_of_book=[],
            ),
        },
        "narrative": {
            "template_md": "**July 2026 to August 2026, Gibraltar Office.**",
            "llm_md": None,
            "grounded": False,
            "flags": [],
        },
        "suggested_questions": [
            "Why did GP drop for Meridian Bunkering?",
            "Where else does Meridian lift fuel?",
            "Which deal class drove the margin compression?",
        ],
        "proof": ["GP current = sum of GP for August 2026 = 1,182,900"],
        "queries": [{"label": "kpis", "sql_hash": "abc123", "rows": 7, "ms": 12}],
        "generated_at": "2026-10-14T09:30:00+00:00",
        "source": "synthetic",
        "generated_by": "dev|test",
    }
    payload.update(overrides)
    return payload
