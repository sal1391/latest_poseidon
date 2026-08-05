"""``fetch_metrics`` — the six certified metrics for one customer, prior full
calendar year vs year-to-date, both derived from a single anchor date.

Phase 8's ``existing_customer_brief`` skill will call this (and
``fetch_top_ports``) as its first tool, per doc 02 §4: "the six certified
metrics (VOLUME, GP, MARGIN, NUM_WON, NUM_INQUIRIES, NUM_LOST) for prior
calendar year vs YTD ... built from the ontology." Until then it lives here,
built and unit-tested on its own — ``customer_insight/task.yml`` ships
``enabled: false`` (its skill arrives Phase 8), but its tools are real code
now; see the task package's own docstring.

No SQL: like every tool in this codebase, this composes a certified
:class:`~poseidon.core.data.specs.MetricQuerySpec` and lets the caller's
:class:`~poseidon.core.data.client.DataClient` run it — this module only
decides WHICH two specs to build and how to prove them. The one thing it
imports from ``query_builder`` is
:func:`~poseidon.core.data.query_builder.resolve_row_scope_value` (Phase 14
Task 6b), which renders nothing: it maps a caller identity onto the D16
scope value the specs below carry. See ``fetch_metrics``'s own "ROW SCOPE"
paragraph.
"""

from datetime import date

from poseidon.core.data.client import DataClient, MetricResult
from poseidon.core.data.query_builder import resolve_row_scope_value
from poseidon.core.data.specs import MetricQuerySpec, PeriodWindow
from poseidon.core.identity import UserContext

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
    data: DataClient, customer: str, anchor: date, user: UserContext | None = None
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
    ``Settings`` or an ontology handle: "Customer", both windows, and the
    metric count are everything it knows for certain on its own. The richer
    style (entity FQN, backend, friendly metric names) lives in
    ``data_qa.metric_query.tools.format_parts``, which has both of those
    handles; a tool that guessed at those lines would be proving something it
    cannot actually see. ``Metrics: N requested`` says "N asked for", which is
    what a spec-building tool can honestly claim — distinct from
    ``format_parts``'s ``Metrics: N values``, which counts values actually
    rendered.

    YTD is half-open ``[Jan 1 of anchor's year, anchor)``. A January 1
    anchor would make that window empty — ``start == end`` — which
    :class:`~poseidon.core.data.specs.PeriodWindow` itself rejects at
    construction (``start >= end``). Rather than let that surface as an
    unexplained ``ValueError`` raised deep inside window construction, a
    January 1 anchor is rejected explicitly, up front, with a message that
    names the actual constraint: the earliest anchor with a non-empty YTD
    range is January 2. The Phase-8 caller owns the UX for that case (e.g.
    steering the user to a later date) — this tool only refuses to silently
    pretend a YTD window exists when it cannot.

    ROW SCOPE (D16, Phase 14 Task 6b). ``user`` is the caller identity the
    scope value is resolved from -- ``ctx.user``, at whatever call site owns
    it. It defaults to ``None`` because ``MARINE_SALES_PLANNING_V`` declares
    no ``row_scope``, so the resolver answers ``None`` for it either way and
    every existing caller keeps working unchanged. That default is safe
    precisely because the mechanism is fail-closed at the builder: the day
    this entity DOES declare a scope, a call that passed no identity raises
    ``SpecValidationError`` instead of returning rows the caller cannot see.
    One resolution, stamped onto BOTH specs, so the prior-year window can
    never end up scoped differently from the YTD one.

    Raises:
        ValueError: if ``anchor`` is January 1st (see above).
    """
    if anchor.month == 1 and anchor.day == 1:
        raise ValueError(
            f"anchor {anchor.isoformat()} has no year-to-date range — the "
            "earliest supported anchor is January 2"
        )

    filters = {"CUST_NM": (customer,)}
    prior_window = _prior_year_window(anchor)
    ytd_window = _ytd_window(anchor)
    scope_value = resolve_row_scope_value(_ENTITY, user)

    prior = data.run_metric_query(
        MetricQuerySpec(
            entity=_ENTITY,
            metrics=SIX_METRICS,
            period=prior_window,
            filters=filters,
            scope_value=scope_value,
        )
    )
    ytd = data.run_metric_query(
        MetricQuerySpec(
            entity=_ENTITY,
            metrics=SIX_METRICS,
            period=ytd_window,
            filters=filters,
            scope_value=scope_value,
        )
    )

    proof = [
        f"Customer: {customer}",
        f"Prior year: {prior_window.start}..{prior_window.end}",
        f"YTD: {ytd_window.start}..{ytd_window.end}",
        f"Metrics: {len(SIX_METRICS)} requested",
    ]
    return prior, ytd, proof
