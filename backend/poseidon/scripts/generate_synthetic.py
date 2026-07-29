"""Deterministic, fully offline synthetic-data generator for the certified
Poseidon ontology (see ``ontology/ontology.yml`` and
``docs/architecture/04-data-ontology.md`` §4). No database, no network, no
wall clock — ``generate()`` reads only ``ontology/synthetic/profiles.yml``
and a seeded ``random.Random`` instance, and returns plain-Python row dicts
in memory.

Determinism contract
---------------------
Every random draw in this module goes through exactly ONE ``random.Random``
instance (``rng``), threaded explicitly through every helper as a parameter.
Nothing here ever calls the ``random`` module's global functions, reads
``datetime.now()``/``date.today()``, calls ``uuid4()``, or iterates a
``set``/unordered ``dict`` to feed a random choice (Python dicts, and
mappings loaded by PyYAML, are insertion-ordered — safe; a bare ``set`` is
only ever used here for O(1) membership tests via ``in``, never iterated).
Given the same seed, ``generate()`` therefore returns bit-identical output
on every call, in every process, forever, which is what lets
``dataset_checksum`` stand in for a committed golden file
(``test_same_seed_same_checksum``).

Checksum canonicalization
--------------------------
``dataset_checksum`` hashes ``{"sales_rows": ..., "gl_rows": ...}`` as JSON
with ``sort_keys=True`` (so dict key order never affects the hash) and
``separators=(",", ":")`` (no incidental whitespace). The only
non-JSON-native value a row can hold is a ``datetime.date``
(``LIFT_ETA_DATE``); the ``default=`` hook renders it via ``date.isoformat()``
("YYYY-MM-DD"). Every other value (str, float, int, None) is already a
native JSON type — floats are serialized by the stdlib ``json`` encoder
using ``float.__repr__``, which is the shortest round-trip representation
and is itself a deterministic function of the float's bits on CPython. No
extra rounding/quantizing happens at hash time: every float in a row is
already the fixed output of a finite, deterministic sequence of operations
on the seeded RNG, so two ``generate()`` calls with the same seed produce
bit-identical floats before the hash ever runs.

Date-type asymmetry (deliberate, not a bug)
---------------------------------------------
``LIFT_ETA_DATE`` (MARINE_SALES_PLANNING_V) is certified ``type: DATE``, so
rows carry a real ``datetime.date`` object. ``PERIOD_DATE``
(W_MARINE_GL_SOURCE_AI) is certified ``type: VARCHAR`` — the source system
may store it as text (ontology business rule: "always wrap in
TO_DATE(PERIOD_DATE)") — so rows carry the period's first-of-month as a
plain ISO string ("YYYY-MM-01"), never a ``date`` object. This mirrors the
two columns' certified types exactly rather than normalizing them to look
alike.

Inquiry / fixture semantics (task-5 brief, doc 04 §4)
--------------------------------------------------------
Every row in MARINE_SALES_PLANNING_V is an inquiry: ``"#_INQUIRIES"`` is
always 1.0. Only WON rows (``"#_FIXTURES"`` == 1.0) carry delivered
tons/profit. LOST rows (``"#_FIXTURES"`` == 0.0) get ``FIXED_TONS`` == 0.0
and ``GROSS_PROFIT`` == 0.0 — a lost inquiry never delivered fuel, so it
cannot carry tonnage or profit, no matter how the deal might otherwise have
priced out.
"""

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from random import Random
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILES_PATH = _REPO_ROOT / "ontology" / "synthetic" / "profiles.yml"

# Mild seasonality multiplier per calendar month (1=Jan..12=Dec): slightly
# busier in the Northern-hemisphere winter bunkering season, slightly
# quieter in Northern-hemisphere summer. +-15% around 1.0 — "mild" per the
# brief, not a strong seasonal swing.
_MONTH_SEASONALITY = {
    1: 1.10, 2: 1.05, 3: 1.00, 4: 0.95, 5: 0.90, 6: 0.95,
    7: 1.00, 8: 1.00, 9: 1.05, 10: 1.10, 11: 1.10, 12: 1.15,
}

# GL hierarchy-path keyword scan (see profiles.yml comment on
# hierarchy_paths): any of these words appearing anywhere in a monetary
# path's 7 levels marks it a cost/expense path (AMOUNT_USD drawn from the
# negative `cost` band); otherwise it's treated as revenue (positive band).
_COST_MARKERS = ("Cost", "Expense", "OpEx", "Tax")
# Same scan, for whether BROKER is ever allowed to be non-"Default" on a
# monetary path (trade-side rows only — see ontology.yml's BROKER/ACCOUNT
# disambiguation: "meaningful only for trade-side rows").
_TRADE_MARKERS = ("Trade", "GP", "Commission")


@dataclass(frozen=True)
class Dataset:
    """Generated rows, keyed by the certified column names (exact strings,
    including the literal ``#_FIXTURES`` / ``#_INQUIRIES`` — no SQL quoting
    in the dict keys themselves)."""

    sales_rows: list[dict]
    gl_rows: list[dict]


@dataclass(frozen=True)
class _CustomerProfile:
    """Attributes sampled ONCE per customer and reused (sticky) across every
    row that customer appears in."""

    ports: list[str]
    margins: dict[str, float]  # port -> USD/ton margin for this customer
    win_rate: float
    supply_broker: str
    supply_team_office: str
    supply_team_region: str
    supply_team_name: str
    primary_broker: str
    primary_broker_office: str
    primary_broker_region: str
    customer_broker: str
    customer_team_office: str
    cbo_region: str
    cust_ship_type_group: str


def generate(seed: int | None = None, profiles_path: Path | None = None) -> Dataset:
    """Generate a full synthetic ``Dataset`` from ``profiles.yml``.

    ``seed`` defaults to the profile's own ``seed_default`` (1391) when not
    given. ``profiles_path`` defaults to the vendored
    ``ontology/synthetic/profiles.yml`` at the repo root. Pure function of
    (profile file contents, seed) — no other inputs, no side effects.
    """
    target = profiles_path if profiles_path is not None else DEFAULT_PROFILES_PATH
    profile = _load_profile(target)
    resolved_seed = seed if seed is not None else int(profile["seed_default"])
    rng = Random(resolved_seed)

    anchor = _coerce_date(profile["window"]["anchor"])
    window_start, window_end = _rolling_window(anchor)
    months = _months_in_window(window_start, window_end)

    sales_rows = _generate_sales_rows(profile["MARINE_SALES_PLANNING_V"], months, rng)
    gl_rows = _generate_gl_rows(profile["W_MARINE_GL_SOURCE_AI"], months, rng)
    return Dataset(sales_rows=sales_rows, gl_rows=gl_rows)


def dataset_checksum(ds: Dataset) -> str:
    """sha256 hex digest over canonical JSON of ``ds`` — see module
    docstring "Checksum canonicalization" for exactly what "canonical"
    means here and why it is stable across repeated calls."""
    canonical = json.dumps(
        {"sales_rows": ds.sales_rows, "gl_rows": ds.gl_rows},
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_default(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"dataset_checksum: not JSON-serializable: {type(value)!r}")


# --------------------------------------------------------------------------
# profile loading / window helpers
# --------------------------------------------------------------------------


def _load_profile(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _coerce_date(value: Any) -> date:
    """profiles.yml writes `anchor: 2026-07-01` unquoted, which PyYAML's
    safe loader already parses into a ``datetime.date``; this also accepts
    a plain ISO string so a quoted anchor in profiles.yml still works."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _rolling_window(anchor: date) -> tuple[date, date]:
    """Prior full calendar year + current year-to-date, relative to
    ``anchor``. Half-open: [start, end) — end (the anchor itself) is
    excluded, matching ``poseidon.core.data.specs.PeriodWindow``'s
    convention elsewhere in this codebase."""
    return date(anchor.year - 1, 1, 1), anchor


def _months_in_window(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, month) whose 1st falls in [start, end)."""
    months = []
    year, month = start.year, start.month
    while date(year, month, 1) < end:
        months.append((year, month))
        month += 1
        if month == 13:
            month = 1
            year += 1
    return months


def _weighted_choice(rng: Random, items: list[tuple[Any, float]]) -> Any:
    """Pick one item from ``[(item, weight), ...]`` with probability
    proportional to weight. ``items`` order must be stable across calls for
    a given input (it is: callers build it from YAML-mapping ``.items()``,
    which is insertion-ordered, or from a list already in a fixed order)."""
    total = sum(weight for _, weight in items)
    threshold = rng.random() * total
    cumulative = 0.0
    for item, weight in items:
        cumulative += weight
        if threshold < cumulative:
            return item
    return items[-1][0]  # float-rounding fallback; last item is as good as any


def _path_text(path: tuple) -> str:
    return " ".join(level for level in path if level)


def _is_cost_path(path: tuple) -> bool:
    text = _path_text(path)
    return any(marker in text for marker in _COST_MARKERS)


def _is_trade_path(path: tuple) -> bool:
    text = _path_text(path)
    return any(marker in text for marker in _TRADE_MARKERS)


# --------------------------------------------------------------------------
# MARINE_SALES_PLANNING_V
# --------------------------------------------------------------------------


def _build_customer_pool(customers_cfg: dict[str, Any]) -> list[str]:
    """3 named + composed "<prefix> <suffix>" names, deduped deterministically
    (nested prefix-major/suffix-minor order; no RNG involved — composition
    order alone is the determinism source), truncated to ``pool_size``."""
    named = list(customers_cfg["named"])
    pool_size = customers_cfg["pool_size"]
    prefixes = list(customers_cfg["prefixes"])
    suffixes = list(customers_cfg["suffixes"])

    seen = set(named)  # membership-only (`in`/`add`); never iterated
    pool = list(named)
    for prefix in prefixes:
        for suffix in suffixes:
            if len(pool) >= pool_size:
                return pool
            candidate = f"{prefix} {suffix}"
            if candidate not in seen:
                seen.add(candidate)
                pool.append(candidate)
    raise ValueError(
        f"prefixes x suffixes ({len(prefixes) * len(suffixes)} combinations) "
        f"cannot fill pool_size={pool_size} after {len(named)} named customers"
    )


def _build_customer_profile(
    is_named: bool,
    port_pool: list[str],
    ports_lo: int,
    ports_hi: int,
    margin_low: float,
    margin_high: float,
    beta_alpha: float,
    beta_beta: float,
    broker_pools: dict[str, list[str]],
    ship_type_items: list[tuple[str, float]],
    rng: Random,
) -> _CustomerProfile:
    k = rng.randint(ports_lo, ports_hi)
    if is_named:
        # FORCE Singapore into the affinity set by construction: sample
        # k-1 from every OTHER port, then append Singapore. This is not a
        # hope that the RNG lands on Singapore — Singapore is unconditionally
        # a member of `ports` below for every named customer.
        others = [p for p in port_pool if p != "Singapore"]
        ports = rng.sample(others, k - 1)
        ports.append("Singapore")
    else:
        ports = rng.sample(port_pool, k)

    margins = {port: round(rng.uniform(margin_low, margin_high), 2) for port in ports}
    win_rate = rng.betavariate(beta_alpha, beta_beta)

    return _CustomerProfile(
        ports=ports,
        margins=margins,
        win_rate=win_rate,
        supply_broker=rng.choice(broker_pools["supply_brokers"]),
        supply_team_office=rng.choice(broker_pools["team_offices"]),
        supply_team_region=rng.choice(broker_pools["regions"]),
        supply_team_name=rng.choice(broker_pools["supply_team_names"]),
        primary_broker=rng.choice(broker_pools["primary_brokers"]),
        primary_broker_office=rng.choice(broker_pools["team_offices"]),
        primary_broker_region=rng.choice(broker_pools["regions"]),
        customer_broker=rng.choice(broker_pools["customer_brokers"]),
        customer_team_office=rng.choice(broker_pools["team_offices"]),
        cbo_region=rng.choice(broker_pools["regions"]),
        cust_ship_type_group=_weighted_choice(rng, ship_type_items),
    )


def _generate_sales_rows(
    cfg: dict[str, Any], months: list[tuple[int, int]], rng: Random
) -> list[dict]:
    named = list(cfg["customers"]["named"])
    customer_pool = _build_customer_pool(cfg["customers"])
    port_pool = list(cfg["ports"])
    ports_lo, ports_hi = cfg["ports_per_customer"]
    mu, sigma = cfg["tons_lognormal"]["mu"], cfg["tons_lognormal"]["sigma"]
    margin_low, margin_high = cfg["margin_usd_per_ton"]["low"], cfg["margin_usd_per_ton"]["high"]
    beta_alpha, beta_beta = cfg["win_rate_beta"]["alpha"], cfg["win_rate_beta"]["beta"]
    suppliers = list(cfg["suppliers"])
    deal_class_items = list(cfg["deal_class"].items())
    ship_type_items = list(cfg["ship_types"].items())
    broker_pools = cfg["broker_pools"]

    profiles = {
        name: _build_customer_profile(
            name in named, port_pool, ports_lo, ports_hi, margin_low, margin_high,
            beta_alpha, beta_beta, broker_pools, ship_type_items, rng,
        )
        for name in customer_pool
    }

    rows = []
    for i in range(cfg["rows"]):
        customer = rng.choice(customer_pool)
        profile = profiles[customer]
        port = rng.choice(profile.ports)
        supplier = rng.choice(suppliers)
        lift_date = _seasonal_date(rng, months)
        is_won = rng.random() < profile.win_rate

        if is_won:
            tons = round(rng.lognormvariate(mu, sigma), 1)
            gross_profit = round(tons * profile.margins[port], 2)
        else:
            tons = 0.0
            gross_profit = 0.0

        rows.append({
            "POI_ID": f"POI-{i:06d}",
            "#_FIXTURES": 1.0 if is_won else 0.0,
            "#_INQUIRIES": 1.0,
            "LIFT_ETA_DATE": lift_date,
            "GROSS_PROFIT": gross_profit,
            "FIXED_TONS": tons,
            "CUST_NM": customer,
            "SUPPLIER_NM": supplier,
            "LOC_NM": port,
            "SUPPLY_TEAM_NAME": profile.supply_team_name,
            "SUPP_BRKR": profile.supply_broker,
            "PRIMARY_SUPPLY_TEAM_OFFICE": profile.supply_team_office,
            "PRIMARY_SUPPLY_TEAM_OFFICE_REGION": profile.supply_team_region,
            "PRIMARY_BRKR": profile.primary_broker,
            "PRIMARY_BRKR_OFFICE": profile.primary_broker_office,
            "PRIMARY_BRKR_REGION": profile.primary_broker_region,
            "CUSTOMER_BRKR": profile.customer_broker,
            "CUSTOMER_TEAM_NAME": profile.customer_team_office,
            "CBO_REGION": profile.cbo_region,
            "DEAL_CLASSIFICATION_TRADE_CUT": _weighted_choice(rng, deal_class_items),
            "VESSEL_DASHBOARD_SHIPTYPE_GRP": _weighted_choice(rng, ship_type_items),
            "CUST_DASHBOARD_SHIPTYPE_GRP": profile.cust_ship_type_group,
        })
    return rows


def _seasonal_date(rng: Random, months: list[tuple[int, int]]) -> date:
    """Uniform over the window, with mild per-calendar-month seasonality
    (see ``_MONTH_SEASONALITY``); day-of-month is uniform within the chosen
    month."""
    weighted = [((year, month), _MONTH_SEASONALITY[month]) for year, month in months]
    year, month = _weighted_choice(rng, weighted)
    days_in_month = calendar.monthrange(year, month)[1]
    day = rng.randint(1, days_in_month)
    return date(year, month, day)


# --------------------------------------------------------------------------
# W_MARINE_GL_SOURCE_AI
# --------------------------------------------------------------------------


def _tonnage_account(path: tuple) -> str:
    """Volume rows' ACCOUNT begins "Tonnage-" (ontology business rule).
    Curated Volume paths write CLASS3 as "Tonnage - <suffix>"; strip that
    prefix/spacing to build the compact account code."""
    class3 = path[4] or ""
    suffix = class3.replace("Tonnage - ", "").replace(" ", "") or "General"
    return f"Tonnage-{suffix}"


def _leaf_account(path: tuple, rng: Random) -> str:
    """Non-volume ACCOUNT: the deepest populated CLASS level plus a small
    deterministic variant code — ACCOUNT sits below CLASS1 in the drill
    chain (denser, always populated) per the ontology's own
    CLASS1/ACCOUNT disambiguation."""
    leaf = next((level for level in reversed(path) if level), "General Ledger")
    variant = rng.randint(1, 40)
    return f"{leaf} {variant:02d}"


def _generate_gl_rows(
    cfg: dict[str, Any], months: list[tuple[int, int]], rng: Random
) -> list[dict]:
    rows_per_period = cfg["rows_per_period"]
    if cfg["periods"] != "all_months_in_window":
        raise ValueError(f"unsupported periods spec: {cfg['periods']!r}")
    volume_share = cfg["volume_share"]

    hierarchy_paths = [tuple(p) for p in cfg["hierarchy_paths"]]
    volume_paths = [p for p in hierarchy_paths if p[3] == "Volume"]
    other_paths = [p for p in hierarchy_paths if p[3] != "Volume"]
    if len(volume_paths) < 3:
        raise ValueError("profiles.yml must curate >=3 Volume hierarchy paths (CLASS4=='Volume')")

    rev_low, rev_high = cfg["amount_bands"]["revenue"]
    cost_low, cost_high = cfg["amount_bands"]["cost"]
    vol_low, vol_high = cfg["amount_bands"]["volume_tons"]

    companies = list(cfg["companies"])
    offices = list(cfg["offices"])
    departments = list(cfg["departments"])
    brokers = list(cfg["brokers"])
    future_values = list(cfg["future_values"])
    weights = cfg["default_weights"]

    rows = []
    for year, month in months:
        period_date = date(year, month, 1).isoformat()
        for _ in range(rows_per_period):
            is_volume = rng.random() < volume_share
            path = rng.choice(volume_paths) if is_volume else rng.choice(other_paths)

            if is_volume:
                amount = round(rng.uniform(vol_low, vol_high), 1)
                account = _tonnage_account(path)
            else:
                if _is_cost_path(path):
                    amount = round(rng.uniform(cost_low, cost_high), 2)
                else:
                    amount = round(rng.uniform(rev_low, rev_high), 2)
                account = _leaf_account(path, rng)

            company = rng.choice(companies)
            office = "Default" if rng.random() < weights["office_default"] else rng.choice(offices)
            department = (
                "Default"
                if rng.random() < weights["department_default"]
                else rng.choice(departments)
            )
            if not _is_trade_path(path):
                broker = "Default"
            else:
                broker = (
                    "Default" if rng.random() < weights["broker_default"] else rng.choice(brokers)
                )
            future_val = (
                "Default" if rng.random() < weights["future_default"] else rng.choice(future_values)
            )

            rows.append({
                "CLASS6_Calc": path[0],
                "CLASS6": path[1],
                "CLASS5": path[2],
                "CLASS4": path[3],
                "CLASS3": path[4],
                "CLASS2": path[5],
                "CLASS1": path[6],
                "COMPANY": company,
                "OFFICE": office,
                "DEPARTMENT": department,
                "ACCOUNT": account,
                "BROKER": broker,
                "FUTURE": future_val,
                "AMOUNT_USD": amount,
                "PERIOD_DATE": period_date,
            })
    return rows


if __name__ == "__main__":
    _ds = generate()
    print(
        f"sales_rows={len(_ds.sales_rows)} gl_rows={len(_ds.gl_rows)} "
        f"checksum={dataset_checksum(_ds)}"
    )
