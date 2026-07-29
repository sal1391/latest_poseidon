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
from poseidon.core.data.specs import BreakdownQuerySpec, PeriodWindow

_ENTITY = "MARINE_SALES_PLANNING_V"
_DEFAULT_TOP_N = 5


def fetch_top_ports(
    data: DataClient, customer: str, window: PeriodWindow, top_n: int = _DEFAULT_TOP_N
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
    """
    spec = BreakdownQuerySpec(
        entity=_ENTITY,
        metrics=("GP",),
        period=window,
        group_by="LOC_NM",
        order_by_metric="GP",
        top_n=top_n,
        filters={"CUST_NM": (customer,)},
    )
    result = data.run_breakdown_query(spec)

    proof = [
        f"Customer: {customer}",
        f"Window: {window.start}..{window.end}",
        f"Top ports: {len(result.rows)} of requested {top_n}",
    ]
    return result, proof
