"""``ResearchResult`` -> typed parts + a certified proof block.

Two shapes only, mirroring ``data_qa.metric_query``'s own ``format_parts``
discipline (one module deciding HOW an answer looks, nothing here fetches
anything):

- SUCCESS (``result.degraded`` is ``False``): a ``text`` part carrying the
  model's own overall synthesis (``ResearchResult.summary``, threaded
  through by Task 4's amendment -- see ``poseidon.mcp.registry
  .ResearchResult``'s own docstring), then a ``table`` part -- columns
  ``Title``/``Source``/``Relevance``, one row per item, the item's ``url``
  standing in as ``Source`` (never ``snippet``, which is not rendered as a
  table cell today) -- then proof lines naming the outbound query, the
  transport that answered, and the result count.
- DEGRADED, or no research tools configured at all (``skill.py``'s own
  ``ctx.tools is None`` branch calls :func:`degraded_parts` directly, with
  no ``ResearchResult`` to route through :func:`format_parts` at all): one
  honest ``text`` part saying research is unavailable right now, plus one
  proof line naming why -- never a silent empty answer, and never a
  fabricated result.
"""

from poseidon.core.skills.result import table_part, text_part
from poseidon.mcp.registry import ResearchResult

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk -- the same convention poseidon.mcp.registry's
# own _EM_DASH uses.
_EM_DASH = chr(0x2014)

_COLUMNS = ["Title", "Source", "Relevance"]

# A degraded ResearchResult always carries a degrade_reason (every
# transport's own _degrade() sets both together) -- this is a purely
# defensive fallback for a shape no real transport can actually produce,
# never a literal string a test needs to have witnessed for real.
_UNKNOWN_DEGRADE_REASON = "unknown error"


def degraded_parts(reason: str) -> tuple[list[dict], list[str]]:
    """The honest "unavailable" rendering -- byte-pinned (Task 4 brief):
    text ``"External research is unavailable right now -- {reason}."`` plus
    proof ``"Research: degraded ({reason})"``. Shared by both callers that
    need it: :func:`format_parts` (a real degraded ``ResearchResult``) and
    ``skill.py`` directly (no tools configured at all, so there is no
    ``ResearchResult`` to route through :func:`format_parts` in the first
    place).
    """
    text = f"External research is unavailable right now {_EM_DASH} {reason}."
    proof = [f"Research: degraded ({reason})"]
    return [text_part(text)], proof


def _sources_table(result: ResearchResult) -> dict:
    rows = [
        [item.get("title", ""), item.get("url", ""), item.get("relevance", "")]
        for item in result.items
    ]
    return table_part(columns=_COLUMNS, rows=rows)


def format_parts(query: str, result: ResearchResult) -> tuple[list[dict], list[str]]:
    """Shape one already-dispatched ``ResearchResult`` into ``(parts,
    proof)`` -- see the module docstring for the two shape rules.
    """
    if result.degraded:
        return degraded_parts(result.degrade_reason or _UNKNOWN_DEGRADE_REASON)

    parts = [text_part(result.summary), _sources_table(result)]
    proof = [
        f"Query: {query}",
        f"Transport: {result.transport}",
        f"Results: {len(result.items)}",
    ]
    return parts, proof
