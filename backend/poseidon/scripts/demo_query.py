"""Human-readable smoke test of the whole synthetic data path.

Run it after ``alembic upgrade head`` + ``seed_synthetic`` and read the
output: it is the fastest way to confirm that the ontology, the query
builder, the seeded ``synthetic`` schema and ``SyntheticDataClient`` all
agree, without reading a single test name. Three sections, in the order a
person actually asks the questions:

1. **Available periods** — what range of data exists at all, per entity.
2. **Six certified metrics, prior year vs year-to-date** — the summary the
   marine sales skill is built around, for both demo windows side by side.
3. **Top 5 customers by GP, Port of Singapore, April 2026** — a filtered
   breakdown, which exercises the filter + GROUP BY + ORDER BY + LIMIT path.

Everything goes through the real ``SyntheticDataClient`` against the real
database. Nothing here computes a number in Python: every value printed came
back from certified SQL, so a wrong answer shows up as a wrong answer rather
than being papered over by local arithmetic.

Section 3 carries a short demo-continuity block: the three customers the
Phase-1 mock answered with (``profiles.yml`` forces a Singapore port affinity
onto exactly those three) are printed with their Singapore year-to-date GP,
so the continuity claim is visible even in a month where one of them misses
the top five.

Formatting is deliberately plain aligned text (no tables library, no colour,
ASCII only — an em dash renders as a question mark in a Windows console) so
the output stays legible in a container log, a terminal, or a pasted report.

Usage::

    DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \\
        python -m poseidon.scripts.demo_query
"""

import datetime as dt
import os
import sys
from collections.abc import Callable, Sequence

from poseidon.core.data.client import BreakdownRow, PeriodRange
from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.data.synthetic_client import SyntheticDataClient

SALES = "MARINE_SALES_PLANNING_V"
GL = "W_MARINE_GL_SOURCE_AI"

PRIOR_YEAR = PeriodWindow(dt.date(2025, 1, 1), dt.date(2026, 1, 1))
YTD_2026 = PeriodWindow(dt.date(2026, 1, 1), dt.date(2026, 7, 1))
APRIL_2026 = PeriodWindow(dt.date(2026, 4, 1), dt.date(2026, 5, 1))

SIX_METRICS = ("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST")

SINGAPORE = "Singapore"
# The Phase-1 mock's customers; profiles.yml pins Singapore into their port
# affinity sets by construction, so they are guaranteed to have Singapore rows.
DEMO_CUSTOMERS = ("Northstar Lines", "Blue Anchor Marine", "Crestline Freight")

# Comfortably larger than the whole synthetic customer pool (40), so the
# continuity block's LIMIT never actually bites and "rank N of M" reports the
# true number of customers trading at Singapore rather than the cap.
ALL_CUSTOMERS_TOP_N = 500

# No rows / a NULLIF-guarded divide-by-zero. Spelled out rather than blank so
# it can never be misread as a zero.
_NO_VALUE = "n/a"

# metric -> (row label, value formatter). Units live in the label so the
# numbers stay narrow; counts print as integers because "24,000.0 inquiries"
# reads like a measurement rather than a count.
_METRIC_ROWS: dict[str, tuple[str, Callable[[float], str]]] = {
    "VOLUME": ("Volume (tons)", lambda v: f"{v:,.1f}"),
    "GP": ("Gross profit (USD)", lambda v: f"{v:,.2f}"),
    "MARGIN": ("Margin (USD/ton)", lambda v: f"{v:,.2f}"),
    "NUM_WON": ("Fixtures won", lambda v: f"{v:,.0f}"),
    "NUM_INQUIRIES": ("Inquiries", lambda v: f"{v:,.0f}"),
    "NUM_LOST": ("Inquiries lost", lambda v: f"{v:,.0f}"),
}


def _format_value(metric: str, value: float | None) -> str:
    """NULL (no rows / divide-by-zero guard) prints as "n/a", never 0."""
    if value is None:
        return _NO_VALUE
    return _METRIC_ROWS[metric][1](value)


def _window_label(window: PeriodWindow) -> str:
    """Half-open [start, end) shown with an INCLUSIVE last day, because that
    is what a human means by "through June"."""
    last_day = window.end - dt.timedelta(days=1)
    return f"{window.start.isoformat()} .. {last_day.isoformat()}"


def _mask_dsn(url: str) -> str:
    """Hide the password so the demo output is safe to paste into a report."""
    scheme, separator, rest = url.partition("://")
    if not separator or "@" not in rest:
        return url
    credentials, _, host = rest.partition("@")
    user, has_password, _ = credentials.partition(":")
    return f"{scheme}{separator}{user}{':***' if has_password else ''}@{host}"


def _render_table(
    headers: Sequence[str], rows: Sequence[Sequence[str]], right_align: Sequence[bool]
) -> str:
    """Two-space-gutter aligned text table, indented two spaces under its
    section heading. Column widths come from the widest cell in each column,
    so nothing is ever truncated."""
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
        for i in range(len(headers))
    ]

    def line(cells: Sequence[str]) -> str:
        padded = [
            cells[i].rjust(widths[i]) if right_align[i] else cells[i].ljust(widths[i])
            for i in range(len(cells))
        ]
        return "  " + "  ".join(padded).rstrip()

    return "\n".join([line(headers), line(["-" * w for w in widths]), *(line(r) for r in rows)])


def _section(title: str, body: str) -> str:
    return f"{title}\n{body}\n"


def _periods_section(client: SyntheticDataClient) -> str:
    def cells(entity: str, period_range: PeriodRange) -> list[str]:
        if period_range.start is None or period_range.end is None:
            return [entity, "(no rows)", "(no rows)"]
        return [entity, period_range.start.isoformat(), period_range.end.isoformat()]

    rows = [cells(entity, client.available_periods(entity)) for entity in (SALES, GL)]
    table = _render_table(
        ["ENTITY", "EARLIEST", "LATEST"], rows, right_align=[False, False, False]
    )
    return _section("AVAILABLE PERIODS", table)


def _summary_section(client: SyntheticDataClient) -> str:
    prior = client.run_metric_query(
        MetricQuerySpec(entity=SALES, metrics=SIX_METRICS, period=PRIOR_YEAR)
    )
    ytd = client.run_metric_query(
        MetricQuerySpec(entity=SALES, metrics=SIX_METRICS, period=YTD_2026)
    )

    rows = [
        [
            _METRIC_ROWS[metric][0],
            _format_value(metric, prior.values[metric]),
            _format_value(metric, ytd.values[metric]),
        ]
        for metric in SIX_METRICS
    ]
    table = _render_table(
        [
            "METRIC",
            f"PRIOR YEAR  {_window_label(PRIOR_YEAR)}",
            f"YEAR TO DATE  {_window_label(YTD_2026)}",
        ],
        rows,
        right_align=[False, True, True],
    )
    return _section(f"SIX CERTIFIED METRICS - {SALES}", table)


def _customer_gp_rows(
    client: SyntheticDataClient, window: PeriodWindow, top_n: int
) -> list[BreakdownRow]:
    """Singapore customers by GP over ``window``, best first."""
    return client.run_breakdown_query(
        BreakdownQuerySpec(
            entity=SALES,
            metrics=("GP", "VOLUME", "MARGIN"),
            period=window,
            group_by="CUST_NM",
            order_by_metric="GP",
            top_n=top_n,
            filters={"LOC_NM": (SINGAPORE,)},
        )
    ).rows


def _breakdown_section(client: SyntheticDataClient) -> str:
    ranked = _customer_gp_rows(client, APRIL_2026, top_n=5)
    rows = [
        [
            f"{rank}.",
            row.key,
            _format_value("GP", row.values["GP"]),
            _format_value("VOLUME", row.values["VOLUME"]),
            _format_value("MARGIN", row.values["MARGIN"]),
        ]
        for rank, row in enumerate(ranked, start=1)
    ]
    table = _render_table(
        ["#", "CUSTOMER", "GP (USD)", "VOLUME (tons)", "MARGIN (USD/ton)"],
        rows,
        right_align=[False, False, True, True, True],
    )
    return _section(
        f"TOP 5 CUSTOMERS BY GP - Port of Singapore, {_window_label(APRIL_2026)}", table
    )


def _continuity_section(client: SyntheticDataClient) -> str:
    """Phase-1 mock continuity: the three named customers, ranked among all
    Singapore customers year to date."""
    ranked = _customer_gp_rows(client, YTD_2026, top_n=ALL_CUSTOMERS_TOP_N)
    by_name = {row.key: (rank, row) for rank, row in enumerate(ranked, start=1)}

    rows = []
    for name in DEMO_CUSTOMERS:
        found = by_name.get(name)
        if found is None:
            rows.append([name, "-", _NO_VALUE, _NO_VALUE, _NO_VALUE])
            continue
        rank, row = found
        rows.append(
            [
                name,
                f"{rank} of {len(ranked)}",
                _format_value("GP", row.values["GP"]),
                _format_value("VOLUME", row.values["VOLUME"]),
                _format_value("MARGIN", row.values["MARGIN"]),
            ]
        )

    table = _render_table(
        ["CUSTOMER", "SINGAPORE RANK", "GP (USD)", "VOLUME (tons)", "MARGIN (USD/ton)"],
        rows,
        right_align=[False, True, True, True, True],
    )
    return _section(
        f"DEMO CONTINUITY - named customers at Singapore, {_window_label(YTD_2026)}",
        table,
    )


def render(client: SyntheticDataClient, dsn: str) -> str:
    return "\n".join(
        [
            f"Poseidon synthetic data - {_mask_dsn(dsn)}",
            "",
            _periods_section(client),
            _summary_section(client),
            _breakdown_section(client),
            _continuity_section(client),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url.strip():
        print(
            "DATABASE_URL is required to run the demo query "
            "(e.g. postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon)",
            file=sys.stderr,
        )
        return 2

    print(render(SyntheticDataClient(url), url), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
