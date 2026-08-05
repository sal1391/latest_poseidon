"""``fetch_top_ports`` — GP by port for one customer, over any window.

The second tool for the future ``existing_customer_brief`` skill (Phase 8;
see ``fetch_metrics.py``'s module docstring for why this lives here,
unwired, today). Unlike ``fetch_metrics``, the window is the caller's
choice — a :class:`~poseidon.core.data.specs.PeriodWindow`, not an anchor
date — because "top ports" is not pinned to a particular period by the
brief; the skill that eventually calls this decides which window (prior
year, YTD, or otherwise) it wants ports for.
"""

from poseidon.core.data.client import BreakdownResult, DataClient
from poseidon.core.data.query_builder import resolve_row_scope_value
from poseidon.core.data.specs import BreakdownQuerySpec, PeriodWindow
from poseidon.core.identity import UserContext

_ENTITY = "MARINE_SALES_PLANNING_V"
_DEFAULT_TOP_N = 5


def fetch_top_ports(
    data: DataClient,
    customer: str,
    window: PeriodWindow,
    top_n: int = _DEFAULT_TOP_N,
    user: UserContext | None = None,
) -> tuple[BreakdownResult, list[str]]:
    """GP by ``LOC_NM`` for ``customer`` over ``window``, top ``top_n``
    ports.

    One :class:`~poseidon.core.data.specs.BreakdownQuerySpec`: metric ``GP``
    only, grouped by ``LOC_NM`` — the certified port dimension, never
    ``PORT_NM``, a documented hallucination (``ontology.yml``'s
    ``negative_constraints``) — ordered by that same metric, filtered to
    ``CUST_NM = (customer,)``. Returns ``(result, proof_lines)``.

    ``top_n`` can legitimately cap a customer with fewer distinct ports than
    requested: the proof line's "of requested {top_n}" makes that visible
    rather than letting a shorter table silently look like the request was
    for fewer ports than it was.

    ROW SCOPE (D16, Phase 14 Task 6b). ``user`` is the caller identity the
    scope value is resolved from; see ``fetch_metrics``'s own "ROW SCOPE"
    paragraph for why it defaults to ``None`` and why that default is safe
    (fail-closed at the builder, never a silent unscoped query).
    """
    spec = BreakdownQuerySpec(
        entity=_ENTITY,
        metrics=("GP",),
        period=window,
        group_by="LOC_NM",
        order_by_metric="GP",
        top_n=top_n,
        filters={"CUST_NM": (customer,)},
        scope_value=resolve_row_scope_value(_ENTITY, user),
    )
    result = data.run_breakdown_query(spec)

    proof = [
        f"Customer: {customer}",
        f"Window: {window.start}..{window.end}",
        f"Top ports: {len(result.rows)} of requested {top_n}",
    ]
    return result, proof
