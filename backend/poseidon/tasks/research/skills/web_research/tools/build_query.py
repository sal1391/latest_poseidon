"""``Args`` -> the outbound research query string: the D30 egress whitelist
composer (doc 05 section 7's redaction/whitelist decision).

This is the ONLY function that builds the text sent to
``ctx.tools.research.search(query=...)``, and it is built EXCLUSIVELY from
``Args``' own six fields, by f-string interpolation, with no other input --
not ``ctx.state``, not ``ctx.data``, not a prior tool result. That
restriction is the whole point: ``ConversationSlots.pass_through`` (doc 02
section 5) exists precisely to carry a PRIOR dispatch's exact certified
values (a metric figure, a table row) forward for a skill to use -- and a
skill that folded carried state into a THIRD-PARTY search query, meaning to
be "helpful," would be an unintentional data-exfiltration channel. Taking
only ``Args`` as input makes that structurally impossible: nothing this
function was never handed can leak through it. See ``tests/test_skill.py``'s
egress contract test for the end-to-end proof (a recording ``ResearchTool``
capturing the outbound query with sentinel-poisoned carried state sitting
right next to it, unread).

``recency_days`` is deliberately NOT embedded in the query text: it already
has its own structured path -- ``ResearchTool.search``'s own ``recency_days``
keyword (``skill.py`` passes ``args.recency_days`` there directly) -- a
day-count belongs in Perplexity's ``search_recency_filter`` request field,
not as prose inside the search string.
"""

from ..schema import Args

# Fixed, never templated -- reinforces the SYSTEM-level marine lens
# (poseidon.mcp.perplexity.adapter.SYSTEM_PROMPT) at the query level too,
# per the Task 4 brief's own "fixed lens suffix" requirement.
_LENS_SUFFIX = "Focus on relevance to the marine fuels and shipping-services industry."


def _subject_clause(args: Args) -> str:
    """The deterministic "about X, at Y, in Z, on W" clause -- customer,
    then port, then region, then topic, comma-joined, each only when set.
    Fixed field order (not the order a router happened to fill them in) is
    what makes this deterministic: the same ``Args`` always produces the
    byte-identical clause.
    """
    clauses = []
    if args.customer is not None:
        clauses.append(f"about {args.customer}")
    if args.port is not None:
        clauses.append(f"at {args.port}")
    if args.region is not None:
        clauses.append(f"in {args.region}")
    if args.topic is not None:
        clauses.append(f"on {args.topic}")
    return ", ".join(clauses)


def build_query(args: Args) -> str:
    """``Args`` -> the outbound query string: the question, then the
    subject clause (when any of customer/port/region/topic is set), then
    the fixed lens suffix -- see the module docstring for why nothing else
    is ever in scope."""
    segments = [args.question.strip()]
    subject = _subject_clause(args)
    if subject:
        segments.append(subject)
    segments.append(_LENS_SUFFIX)
    return " ".join(segments)
