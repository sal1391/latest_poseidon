"""``fetch_metrics`` — the six certified metrics for one customer, prior full
calendar year vs year-to-date, both derived from a single anchor date.

Phase 8's ``existing_customer_brief`` skill will call this (and
``fetch_top_ports``) as its first tool, per doc 02 §4: "the six certified
metrics (VOLUME, GP, MARGIN, NUM_WON, NUM_INQUIRIES, NUM_LOST) for prior
calendar year vs YTD ... built from the ontology." Until then it lives here,
built and unit-tested on its own — ``customer_insight/task.yml`` ships
``enabled: false`` (its skill arrives Phase 8), but its tools are real code
now; see the task package's own docstring.

No SQL, no ``query_builder`` import: like every tool in this codebase, this
composes a certified :class:`~poseidon.core.data.specs.MetricQuerySpec` and
lets the caller's :class:`~poseidon.core.data.client.DataClient` run it —
this module only decides WHICH two specs to build and how to prove them.
"""

from datetime import date

from poseidon.core.data.client import DataClient, MetricResult
from poseidon.core.data.specs import MetricQuerySpec, PeriodWindow

_ENTITY = "MARINE_SALES_PLANNING_V"

# The six certified metrics doc 02 §4 names for the brief. WIN_RATE — the
# ontology's seventh metric, a diagnostic-only ratio guarded by a HAVING
# clause (see query_builder.py's `_SMALL_SAMPLE_HAVING`) — is deliberately
# excluded, matching the brief's own list verbatim.
SIX_METRICS = ("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST")


def _prior_year_window(anchor: date) -> PeriodWindow:
    """Jan 1 of the year before ``anchor`` to Jan 1 of ``anchor``'s own
    year — the prior FULL calendar year, half-open, matching every other
    window in this codebase."""
    return PeriodWindow(date(anchor.year - 1, 1, 1), date(anchor.year, 1, 1))


def _ytd_window(anchor: date) -> PeriodWindow:
    """Jan 1 of ``anchor``'s year up to (excluding) ``anchor`` itself —
    year-to-date, half-open."""
    return PeriodWindow(date(anchor.year, 1, 1), anchor)


def fetch_metrics(
    data: DataClient, customer: str, anchor: date
) -> tuple[MetricResult, MetricResult, list[str]]:
    """The six certified metrics for ``customer``, prior full calendar year
    and year-to-date, both windows derived from ``anchor`` alone.

    Two :class:`~poseidon.core.data.specs.MetricQuerySpec` runs, identical
    except for the period — the prior-year window and the YTD window (see
    the two window helpers above) — each filtered to ``CUST_NM =
    (customer,)``. Returns ``(prior, ytd, proof_lines)``; the caller (the
    future skill) decides how to render them, this tool only fetches and
    proves.

    The proof block is deliberately terse — four lines, always in this
    order — because this tool only ever receives a ``DataClient``, never a
    ``Settings`` or an ontology handle (contrast
    ``data_qa.metric_query.tools.format_parts``'s richer proof, which has
    both): "Customer", both windows, and the metric count are everything
    this tool itself knows for certain.
    """
    filters = {"CUST_NM": (customer,)}
    prior_window = _prior_year_window(anchor)
    ytd_window = _ytd_window(anchor)

    prior = data.run_metric_query(
        MetricQuerySpec(entity=_ENTITY, metrics=SIX_METRICS, period=prior_window, filters=filters)
    )
    ytd = data.run_metric_query(
        MetricQuerySpec(entity=_ENTITY, metrics=SIX_METRICS, period=ytd_window, filters=filters)
    )

    proof = [
        f"Customer: {customer}",
        f"Prior year: {prior_window.start}..{prior_window.end}",
        f"YTD: {ytd_window.start}..{ytd_window.end}",
        "Metrics: 6",
    ]
    return prior, ytd, proof
