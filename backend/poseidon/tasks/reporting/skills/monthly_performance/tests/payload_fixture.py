"""The payload v1 contract in code: a complete, internally consistent report payload.

A plain function building dicts. It imports nothing from the tools package, so later
tests can use it before those modules exist. Every display string is ASCII.
"""

from __future__ import annotations

from typing import Any

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
