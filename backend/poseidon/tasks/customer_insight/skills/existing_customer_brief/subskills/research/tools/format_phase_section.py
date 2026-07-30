"""One research call's :class:`~poseidon.mcp.registry.ResearchResult` (or a
degrade) -> one ``phase_section`` part -- the markdown-rendering half of the
``research`` subskill, mirroring ``research.web_research``'s own
``tools/format_parts.py`` one skill tree over: one module decides HOW a
research call's outcome looks, nothing here fetches anything.

``"phase_section"`` (Poseidon Phase 8, doc 02 section 4) is a NEW part
kind: ``{"kind": "phase_section", "payload": {"title": str, "markdown":
str}}`` -- a named, headed block of markdown, distinct from the plain
``"text"`` part (:func:`poseidon.core.skills.result.text_part`), which
carries no title. A brief streams one ``phase_section`` per completed
phase (this subskill contributes one per research call; Task 3's
``contextualize``/``strategize`` subskills each contribute one more of
their own); the frontend renders each under its own heading rather than
one undifferentiated wall of prose.

:func:`phase_section_part` is defined HERE, not in
``poseidon.core.skills.result`` alongside ``text_part``/``table_part``/
``metric_grid_part``, because Task 2's own sanctioned edit surface (the
plan's own File Map) does not include ``result.py`` -- promoting a shared
constructor there is a call for whichever task first needs
``phase_section`` from a SECOND call site (Task 3's ``contextualize``/
``strategize``, per this same plan) to make, not this one. Doc 02 section
1's own folder law already names this exactly right: a subskill's
``tools/`` directory holds "deterministic helpers PRIVATE to this
subskill" -- this function is one.
"""

from poseidon.mcp.registry import ResearchResult

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk -- the same convention poseidon.mcp.registry's
# own _EM_DASH, and every earlier phase's degrade-text module, uses.
_EM_DASH = chr(0x2014)


def phase_section_part(title: str, markdown: str) -> dict:
    """One ``phase_section`` part -- see the module docstring for the
    payload shape and why this constructor lives here rather than in
    ``poseidon.core.skills.result``."""
    return {"kind": "phase_section", "payload": {"title": title, "markdown": markdown}}


def _item_fields(schema_name: str, item: dict) -> tuple[str, str, str, str]:
    """``(category, label, detail, relevance)`` for one item -- the four
    markdown-relevant fields every schema this subskill formats carries
    under SOME name. ``web_research`` (Phase 7, pinned, new-prospect
    mode's second call) uses ``title``/``snippet``/``relevance`` and has
    no category; this task's own four schemas (``sustainability``/
    ``market_position``/``strategic_profile``/``operational_profile``) use
    ``label``/``detail``/``relevance``, plus ``category`` for
    ``operational_profile`` only. ``dict.get`` defaults a field a given
    schema's items do not carry to ``""`` (never a ``KeyError``), so this
    one function reads any of the five schema shapes uniformly without a
    schema-name branch anywhere else in this module.
    """
    if schema_name == "web_research":
        return "", item.get("title", ""), item.get("snippet", ""), item.get("relevance", "")
    return (
        item.get("category", ""),
        item.get("label", ""),
        item.get("detail", ""),
        item.get("relevance", ""),
    )


def _item_bullet(schema_name: str, item: dict) -> str:
    """One item -> one markdown bullet line, e.g.
    ``"- [vessel_type] **Supramax bulk carrier**: Six Supramax-class bulk
    carriers... (Relevance: Supramax-class vessels are common bunker
    customers...)"``. ``category`` (operational_profile only) prefixes the
    bullet in brackets when present; a missing ``label`` renders as
    ``"(unlabeled)"`` rather than an empty bold marker -- schema-legal but
    degenerate input (an item with every field defaulted to ``""``) still
    produces one honest, non-blank line.
    """
    category, label, detail, relevance = _item_fields(schema_name, item)
    prefix = f"[{category}] " if category else ""
    bullet = f"- {prefix}**{label}**" if label else "- (unlabeled)"
    if detail:
        bullet += f": {detail}"
    if relevance:
        bullet += f" (Relevance: {relevance})"
    return bullet


def _markdown_body(schema_name: str, result: ResearchResult) -> str:
    """summary paragraph, blank line, one bullet per item -- "markdown
    from summary + items" per the Task 2 brief. Never an empty string: a
    schema-legal success with neither a summary nor any items (unlikely
    but not forbidden by any of the five schemas) still renders one
    honest line rather than an empty part body a reader could mistake for
    a rendering bug."""
    segments = []
    if result.summary:
        segments.append(result.summary)
    if result.items:
        segments.append("\n".join(_item_bullet(schema_name, item) for item in result.items))
    return "\n\n".join(segments) if segments else "No results returned."


def format_success(schema_name: str, title: str, result: ResearchResult) -> dict:
    """A non-degraded :class:`~poseidon.mcp.registry.ResearchResult` ->
    its ``phase_section`` part. ``schema_name`` selects how ``result.
    items`` are read (see :func:`_item_fields`); ``title`` is the
    section's own human-readable heading, fixed per call by
    ``subskill.py``'s own call specs, not derived from anything in
    ``result`` itself."""
    return phase_section_part(title, _markdown_body(schema_name, result))


def format_degraded(title: str, reason: str) -> dict:
    """The pinned failure text for one section (house rule: byte-pinned
    messages): ``"Research for this section is unavailable right now --
    {reason}."`` -- worded per-SECTION, deliberately different from
    ``research.web_research.tools.format_parts.degraded_parts``'s own
    ``"External research is unavailable right now -- {reason}."``, because
    THIS subskill makes several calls per run; one of them degrading is
    one section's honest gap, never a claim that every section (or the
    whole brief) failed.
    """
    markdown = f"Research for this section is unavailable right now {_EM_DASH} {reason}."
    return phase_section_part(title, markdown)
