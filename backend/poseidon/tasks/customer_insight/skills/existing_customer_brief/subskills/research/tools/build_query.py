"""``subject`` + one fixed lens phrase -> the outbound research query, for
each of the five ``schema_name`` values the ``research`` subskill
(``poseidon.tasks.customer_insight.skills.existing_customer_brief.subskills
.research``) ever calls with.

The D30 whitelist composer pattern, one skill tree over -- see
``poseidon.tasks.research.skills.web_research.tools.build_query``'s own
module docstring for the identical discipline this MIRRORS, not imports:
this function's only inputs are its own two arguments, ``subject`` (the
subskill's own caller-supplied string -- never ``ctx.state``, never a prior
tool result) and a lens phrase fixed by ``schema_name`` -- nothing else may
enter the returned string. A skill that folded carried conversation state
into a third-party research query, meaning to be "helpful," would be an
unintentional data-exfiltration channel; taking only these two inputs makes
that structurally impossible here, the same way ``Args``' six fields make
it impossible for ``web_research``'s own ``build_query``. See
``subskill.py``'s own module docstring ("EGRESS") and
``test_brief_subskills.py``'s sentinel test for the end-to-end proof.

NOT a straight import of that sibling module's ``_LENS_SUFFIX``: this
function serves FIVE schema_names, each with its own lens phrase (four
authored fresh for Task 2, plus a fifth for ``web_research`` itself, reused
in new-prospect mode) -- a genuinely different shape from ``build_query``'s
own (``subject`` + a schema-keyed phrase, not ``Args``' six optional
fields), and ``_LENS_SUFFIX`` is underscore-prefixed (module-private)
besides -- reaching into another skill's private symbol is not this
codebase's convention (see ``fixture_tool.py``'s own precedent of
importing only the two symbols ``adapter.py`` made deliberately PUBLIC for
cross-module reuse, never a private one). The ``web_research`` entry in
``_LENS_PHRASES`` below is therefore an INDEPENDENTLY AUTHORED copy, not a
shared constant -- worded identically to ``_LENS_SUFFIX`` on purpose
(both describe the exact same schema's exact same lens), not by accident
of copy-paste.
"""

_LENS_PHRASES: dict[str, str] = {
    "sustainability": (
        "Focus on sustainability commitments, ESG initiatives, and "
        "alternative marine fuel adoption relevant to marine fuels and "
        "shipping-services sales."
    ),
    "market_position": (
        "Focus on market position, industry classification, and "
        "competitive landscape relevant to marine fuels and "
        "shipping-services sales."
    ),
    "strategic_profile": (
        "Focus on business model, financial trajectory, and market "
        "presence relevant to marine fuels and shipping-services sales."
    ),
    "operational_profile": (
        "Focus on fleet composition, vessel types, and preferred ports "
        "relevant to marine fuels and shipping-services sales."
    ),
    "web_research": "Focus on relevance to the marine fuels and shipping-services industry.",
}


def build_query(subject: str, schema_name: str) -> str:
    """``subject`` + the one fixed lens phrase for ``schema_name`` -- see
    the module docstring for the D30 discipline this composer follows.

    Raises ``KeyError`` for a ``schema_name`` outside the five
    ``_LENS_PHRASES`` names: an internal wiring bug (this module's own
    table not naming a call ``subskill.py`` actually makes), never a value
    a live caller could trigger (``subskill.py``'s own call specs are the
    only caller, and they are a fixed, closed set) -- so this raises
    plainly rather than inventing a defensive fallback phrase for an input
    this function was never designed to receive, mirroring
    ``poseidon.mcp.registry.ToolServerRegistry._build_research``'s own
    precedent: raise for a config/wiring mistake, degrade only for a
    genuine runtime failure.
    """
    lens = _LENS_PHRASES[schema_name]
    return f"{subject.strip()} {lens}"
