"""The ``research`` subskill: external, Perplexity-shaped structured
research for both brief flows (Phase 8 Task 2; doc 02 section 4.3).

``run(ctx, mode, subject) -> SubskillResult``. Existing-customer mode
(``mode == MODE_EXISTING``) makes three SEQUENTIAL calls -- sustainability,
market position, strategic profile, in that order, doc 02 section 4's own
list -- each against its own new Task 2 schema
(``poseidon/mcp/perplexity/schemas/{sustainability,market_position,
strategic_profile}.json``). New-prospect mode (``mode == MODE_PROSPECT``)
makes two: operational profile, then ``web_research`` (Phase 7's existing
schema, reused verbatim) -- doc 02 section 4's own new-prospect list names
"Perplexity operational profile ... plus the research output", operational
profile first. Every call's outcome becomes exactly one ``phase_section``
part (see ``tools/format_phase_section.py``): markdown built from that
call's ``summary`` + ``items`` on success, or the pinned per-section
failure text on a degrade -- never silently dropped, never a fabricated
answer. ``failed`` is ``True`` only when EVERY call in the mode's list
degraded (or ``ctx.tools`` was ``None`` to begin with) -- doc 02 section
6's own anti-happy-path rule ("the phase fails, previously streamed
deterministic parts stand") read at this subskill's own grain: one
degraded lens among three is this subskill still doing its job for the
other two, not a failure of the whole call.

OPERATIONAL_PROFILE'S ITEMS-SHAPED DESIGN (disclosed judgment call -- see
``poseidon/mcp/perplexity/schemas/operational_profile.json``'s own
description and ``fixture_tool.py``'s module docstring for the mechanical
half of this): the Task 2 brief's own field list for this schema names
``vessel_types[]``/``preferred_ports[]``/``notes``/``summary`` -- a
DIFFERENT top-level shape from the other three schemas' ``items[]`` +
``summary``. ``poseidon.mcp.perplexity.adapter.validate_and_normalize``
derives every per-item key from ``schema["properties"]["items"]["items"]
["properties"]`` and unconditionally reads the parsed response's top-level
``"items"`` key -- built for exactly ONE top-level shape, shared by every
schema and both real transports. Rather than extend that shared function
with a schema-specific branch (a change to code every transport and
schema in this package depends on, for the sake of one schema), this task
gives ``operational_profile`` an ``items[]`` representation too: each
vessel type, each preferred port, and each operational note becomes one
item, distinguished by that item's own ``category`` field
(``"vessel_type"`` / ``"preferred_port"`` / ``"note"``) rather than three
separate arrays. This is the brief's own pre-approved alternative
("simpler, consistent") -- it keeps ``validate_and_normalize`` completely
unmodified, and it is what lets THIS module format all five schema_names
(the four new ones plus ``web_research``) through the SAME two functions
in ``tools/format_phase_section.py`` with no schema-specific branch of its
own beyond the one ``web_research`` already needed for its differently-
named fields.

STUB-MODE SCOPING (doc 02 section 4.3's "then Sonnet synthesis", disclosed
per the Task 2 brief): this subskill is LLM-free BY DESIGN -- it only
searches (``ctx.tools.research.search``) and formats (deterministic
markdown, ``tools/format_phase_section.py``); it never reads ``ctx.llm``
at all. The "then Sonnet synthesis" doc 02 section 4.3 describes for the
research phase happens one level up, in Task 3's ``contextualize``/
``strategize`` subskills, which CONSUME this subskill's own
``synthesis_inputs`` (below) as their own prompt material -- this v1 slice
only has to produce honest, structured research content for something
else to synthesize later, not synthesize it itself. Stated explicitly
because doc 02 section 4.3's own prose predates subskills being split out
this finely and could otherwise be misread as naming a step INSIDE this
module.

``SubskillResult`` (frozen dataclass, defined LOCALLY -- "local to the
subskill package" per this plan's own Self-Review Notes on type
consistency): the subskill-layer contract ``run`` returns.

- ``parts: tuple[dict, ...]`` -- one ``phase_section`` part per call, in
  call order, ready to append to the skill's own ``SkillResult.parts``
  (Task 4) or stream via ``ctx.emit_part`` (Phase 8 Task 1's seam) as soon
  as this subskill returns.
- ``synthesis_inputs: tuple[dict, ...]`` -- one record per call, SAME
  order as ``parts``, carrying what Task 3's subskills will actually
  consume as their own ``research_inputs``: ``{"schema_name", "title",
  "summary", "items", "degraded", "degrade_reason"}``. Deliberately plain
  dicts (not a second dataclass): these feed a Jinja prompt template in
  live mode (Task 3's own ``prompts/*.md``) and a deterministic digest in
  stub mode, both of which template dict access more naturally than
  dataclass attribute access -- and every OTHER part-adjacent shape in
  this codebase (``parts`` themselves, ``poseidon.core.skills.result``'s
  own constructors) is a plain dict for the identical reason.
- ``failed: bool`` -- see above.

``ctx.tools is None`` (no ``ToolServerRegistry`` wired at all -- real
until ``api/app.py`` installs one, exactly the precondition ``research
.web_research``'s own ``skill.py`` already documents for the identical
field): every call in the mode's list renders as degraded, with reason
``"no research tools configured"``, and ``failed`` is unconditionally
``True``. ``ctx.tools.research`` is never touched in this branch -- proven
directly by ``test_run_with_ctx_tools_none_never_crashes_and_never_needs_
a_research_tool``.

EGRESS (decision D30, doc 02 sections 5 and 7, extended to brief research
per this plan's own Global Constraints): ``run``'s only inputs are ``ctx``
(read ONLY for ``.tools``, never ``.state``) and ``subject`` (a plain
string, this function's own second argument) -- ``tools/build_query.py``'s
``build_query(subject, schema_name)`` builds every outbound query from
exactly those two things plus a phrase FIXED by ``schema_name``, nothing
else. A conversation's carried customer/port/metric context
(``ctx.state.pass_through``, doc 02 section 5) is never read by this
subskill at all, which makes the leak this discipline guards against
structurally impossible here rather than merely avoided by convention --
proven directly by this module's own sentinel-poisoned-state test in
``test_brief_subskills.py``.

SUBSKILL-SHARING SEAM (flagged for Task 4, deliberately NOT resolved
here): Task 2's own File Map places ``research/`` under
``existing_customer_brief/subskills/`` -- ``new_prospect_brief/`` (Task 4)
has no subskills tree of its own yet. Doc 02 section 1's folder law shows
each skill owning its OWN ``subskills/`` directory ("every skill owns its
... subskills"), which would mean Task 4 either imports THIS module
cross-skill (an unusual coupling doc 02's own tree diagram shows no
precedent for) or ships a sibling copy under
``new_prospect_brief/subskills/research/`` (the doc-02-literal reading,
and the one with a real precedent already in this codebase -- see
``test_perplexity_mcp_client.py``'s own "re-declared locally rather than
imported cross-test-file" convention for near-identical siblings). This
module takes NO position on which Task 4 should choose: ``run``'s own
``mode`` parameter already handles both flows from ONE copy if Task 4
chooses to import it, and nothing in this module's own contract would
need to change either way -- the choice, and its disclosure, belong to
Task 4.
"""

from dataclasses import dataclass
from typing import Protocol, cast

from poseidon.core.skills.context import SkillContext
from poseidon.mcp.registry import ResearchResult, ResearchTool

from .tools.build_query import build_query
from .tools.format_phase_section import format_degraded, format_success

# Byte-pinned (house rule): the reason every phase_section carries when
# ctx.tools is None. Deliberately NOT imported from research.web_research
# .skill's own _NO_TOOLS_REASON ("no research tool server is configured")
# -- a private, underscore-prefixed symbol in a different skill's own
# module, for the identical reason build_query.py's own module docstring
# gives for not importing _LENS_SUFFIX from that skill's tools package.
_NO_TOOLS_REASON = "no research tools configured"

# Defensive fallback only -- mirrors research.web_research.tools
# .format_parts._UNKNOWN_DEGRADE_REASON's identical precedent: every real
# ResearchTool.search() degrade path sets degrade_reason alongside
# degraded=True (each transport's own _degrade() helper does this
# together, always), so this is never expected to be exercised by a real
# transport, only by a test double that does not follow that contract.
_UNKNOWN_REASON = "unknown error"

# The two mode values this subskill accepts -- the exact literal strings
# already established across this codebase for ConversationSlots.mode
# (see e.g. test_parsing_hinter_pipeline.py, test_llm_loop.py, api/live_
# chat.py's flow-chip ids) -- not reinvented here, so a future D19 entry
# orchestration (Task 5) setting slots.mode and a Task 4 brief skill
# reading it to call this subskill agree by construction, not convention.
MODE_EXISTING = "existing_customer"
MODE_PROSPECT = "new_prospect"


@dataclass(frozen=True)
class _CallSpec:
    """One research call this subskill can make: which schema to search,
    and the human-readable title its phase_section renders under."""

    schema_name: str
    title: str


_EXISTING_CALLS: tuple[_CallSpec, ...] = (
    _CallSpec("sustainability", "Sustainability & ESG"),
    _CallSpec("market_position", "Market Position"),
    _CallSpec("strategic_profile", "Strategic Profile"),
)

_PROSPECT_CALLS: tuple[_CallSpec, ...] = (
    _CallSpec("operational_profile", "Operational Profile"),
    _CallSpec("web_research", "Web Research"),
)

_MODE_CALLS: dict[str, tuple[_CallSpec, ...]] = {
    MODE_EXISTING: _EXISTING_CALLS,
    MODE_PROSPECT: _PROSPECT_CALLS,
}


class _ToolServer(Protocol):
    """Structural shape this subskill needs from ``ctx.tools`` -- the same
    local-Protocol-cast pattern ``research.web_research``'s own
    ``skill.py`` uses and documents (see that module's docstring for why
    this is declared locally rather than in ``poseidon.mcp.registry``
    itself)."""

    @property
    def research(self) -> ResearchTool: ...


@dataclass(frozen=True)
class SubskillResult:
    """The subskill-layer contract ``research.run`` returns -- see the
    module docstring for the full field-by-field rationale."""

    parts: tuple[dict, ...]
    synthesis_inputs: tuple[dict, ...]
    failed: bool


def _success_input(call: _CallSpec, result: ResearchResult) -> dict:
    return {
        "schema_name": call.schema_name,
        "title": call.title,
        "summary": result.summary,
        "items": result.items,
        "degraded": False,
        "degrade_reason": None,
    }


def _degraded_input(call: _CallSpec, reason: str) -> dict:
    return {
        "schema_name": call.schema_name,
        "title": call.title,
        "summary": "",
        "items": (),
        "degraded": True,
        "degrade_reason": reason,
    }


def _all_degraded(calls: tuple[_CallSpec, ...], reason: str) -> SubskillResult:
    """The ``ctx.tools is None`` shape -- every call in ``calls`` renders
    degraded with the SAME reason, ``ctx.tools.research`` never touched."""
    parts = tuple(format_degraded(call.title, reason) for call in calls)
    inputs = tuple(_degraded_input(call, reason) for call in calls)
    return SubskillResult(parts=parts, synthesis_inputs=inputs, failed=True)


def run(ctx: SkillContext, mode: str, subject: str) -> SubskillResult:
    """Dispatch ``mode``'s calls in order, format each outcome, and report
    ``failed`` -- see the module docstring for the full contract.

    Raises ``ValueError`` for a ``mode`` outside ``{MODE_EXISTING,
    MODE_PROSPECT}``: an internal wiring bug (whichever skill calls this
    subskill passing a mode it never validated), never a value a real user
    turn could produce (a future D19 entry orchestration is what ever sets
    ``mode``, from its own pinned two-value set) -- so this raises
    plainly rather than inventing a third, silent behavior for an input
    this subskill was never designed to receive.
    """
    try:
        calls = _MODE_CALLS[mode]
    except KeyError:
        raise ValueError(
            f"research subskill: unknown mode {mode!r} -- expected one of {sorted(_MODE_CALLS)}"
        ) from None

    if ctx.tools is None:
        return _all_degraded(calls, _NO_TOOLS_REASON)

    research_tool = cast(_ToolServer, ctx.tools).research

    parts = []
    inputs = []
    any_success = False
    for call in calls:
        query = build_query(subject, call.schema_name)
        result = research_tool.search(query=query, schema_name=call.schema_name)
        if result.degraded:
            reason = result.degrade_reason or _UNKNOWN_REASON
            parts.append(format_degraded(call.title, reason))
            inputs.append(_degraded_input(call, reason))
        else:
            any_success = True
            parts.append(format_success(call.schema_name, call.title, result))
            inputs.append(_success_input(call, result))

    return SubskillResult(
        parts=tuple(parts), synthesis_inputs=tuple(inputs), failed=not any_success
    )
