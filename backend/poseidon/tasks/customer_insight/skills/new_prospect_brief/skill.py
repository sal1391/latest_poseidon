"""Skill ``customer_insight.new_prospect_brief`` (Phase 8 Task 4; doc 02
section 4, decision D10): a brief for a company that is not yet a
certified customer.

ORDER (D10, doc 02 section 4's own numbered list for this flow): research
FIRST (``operational_profile``, then ``web_research`` -- see
``subskills/research/subskill.py``'s own ``MODE_PROSPECT`` call list) ->
contextualize IN PROSPECT MODE, CONSUMING research's own
``synthesis_inputs`` (contrast with ``existing_customer_brief``'s
concurrent contextualize -- see "SEQUENTIAL, NOT CONCURRENT" below) ->
strategize (renders the pinned "Prospect -- no current services" rule via
``mode=MODE_PROSPECT`` -- see ``strategize.subskill``'s own "CURRENT
SERVICES" docstring section) -> ``build_brief_pdf``, or a pinned skip line
when no artifact store is configured (mirrors ``existing_customer_brief``'s
identical dev-reality branch, doc 07).

SEQUENTIAL, NOT CONCURRENT (contrast with ``existing_customer_brief``,
disclosed judgment call). Doc 02 section 4's own "Concurrency" paragraph
says contextualize/research run concurrently "for existing customers"
ONLY. D10's prospect ordering is explicitly research THEN contextualize
because contextualize's whole value here comes FROM research: a prospect
has no internal data block at all (doc 02's own "nothing exists for a
prospect"), so there is nothing for contextualize to run in parallel WITH
-- it needs research's actual output as an input, which a concurrent
dispatch could not honestly provide (research might still be running, and
``contextualize.subskill.run``'s own ``research_inputs`` argument would
have to be a placeholder rather than the real thing). ``existing_customer_
brief`` is the mirror image: its contextualize draws on the
ALREADY-FETCHED internal data block, so it has no dependency on research's
output and the two run side by side there instead.

NO INTERNAL DATA TOOLS (the brief's own instruction, documented here, as
directed). Neither ``fetch_metrics`` nor ``fetch_top_ports`` is called:
both filter on ``CUST_NM``, and a prospect is BY DEFINITION not a value in
that certified dimension. Calling either anyway would return an honest-
looking but MISLEADING answer -- an empty/all-None result that renders
identically to "a real customer with zero activity this period", not to
"not a customer at all" -- which is worse than not calling the tool.
``data_block``/``data_summary`` are therefore always ``{}`` for this flow;
both subskills already handle that explicitly and honestly
(``contextualize``'s own byte-pinned "Data block metrics: 0" digest in
stub mode; ``strategize``'s own mode check short-circuits the "Current
Services" field to the pinned prospect text before ``data_summary`` is
ever consulted -- see that module's docstring).

SUBSKILL + TOOL REUSE -- IMPORT, NOT A SIBLING COPY (the adjudicated
sharing decision, Phase 8 Task 2's own "SUBSKILL-SHARING SEAM" carry,
resolved by this task as directed, reviewer-verified import-clean).

This module imports ``research``/``contextualize``/``strategize`` from
``existing_customer_brief.subskills`` rather than shipping byte-identical
sibling copies under a ``new_prospect_brief/subskills/`` tree of its own.
Doc 02 section 1's folder law shows each skill owning its own
``subskills/`` directory, which this import departs from in LETTER --
flagged explicitly here rather than silently -- but not in SPIRIT: the
whole point of each subskill's own ``mode`` parameter
(``MODE_EXISTING``/``MODE_PROSPECT``, already handled by all three) is to
let ONE implementation serve both flows, and a sibling copy would only be
a second place for the sustainability/market_position/strategic_profile/
operational_profile call list, the CRM header list, or the field
dictionary to silently drift out of sync with the original -- precisely
the risk the folder law's own "every skill owns its tools" principle
exists to avoid one level over (two skills disagreeing about a shared
certified concept). ``tools.build_brief_pdf`` is imported the same way,
one level further: rendering markdown to a stored PDF has nothing
customer-specific about it at all (``title``/``markdown_body``/
``key_prefix`` are its whole surface), so a byte-identical copy here would
be pure duplication with no offsetting benefit -- unlike ``fetch_metrics``/
``fetch_top_ports``, which this skill does NOT import, because those two
are genuinely inapplicable here (see "NO INTERNAL DATA TOOLS" above), not
merely inconvenient to duplicate.

PROOF LINES: subject, phases completed/failed, transport, artifact status
-- the same shape ``existing_customer_brief``'s own proof block uses,
MINUS the certified-metrics lines (``Entity:``/``Backend:``/period lines):
this flow never touches ``ctx.data`` at all, so there is no entity, no
backend, and no period to honestly name.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import date
from typing import Protocol, TypeVar, cast

from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.result import SkillResult
from poseidon.mcp.registry import ResearchTool

from ..existing_customer_brief.subskills.contextualize import subskill as contextualize_subskill
from ..existing_customer_brief.subskills.research import subskill as research_subskill
from ..existing_customer_brief.subskills.research.subskill import MODE_PROSPECT
from ..existing_customer_brief.subskills.strategize import subskill as strategize_subskill
from ..existing_customer_brief.tools.build_brief_pdf import build_brief_pdf
from .schema import Args

# See existing_customer_brief/skill.py's own identical constant for the
# full "why doc-02 order, not call order" rationale -- this flow's own
# order (research first) is D10's, not doc 02 section 4.1's.
_PHASE_ORDER = ("research", "contextualize", "strategize")

_ARTIFACT_SKIP_PROOF = "Artifact: skipped (no artifact store configured)"

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


def _today() -> date:
    """Only used to date-partition the PDF's storage key -- see
    ``existing_customer_brief.skill``'s own ``_today`` for the fuller
    rationale (identical here; this flow has no window/anchor math to make
    deterministic, only a storage key)."""
    return date.today()


def _run_subskill_or_failed(
    phase: str, call: Callable[[], _T], failed_result: Callable[[], _T]
) -> _T:
    """See ``existing_customer_brief.skill._run_subskill_or_failed``'s own
    docstring for the full exception-escape-guard rationale (P8
    whole-branch final-review wave, 2026-07-30, item 2 / I-4; identical
    here, independently declared -- this flow's own subskill dispatches
    are all direct ``.run(...)`` calls rather than
    ``ThreadPoolExecutor`` futures, so every call site below wraps ``call``
    in a small closure instead of passing a future's own ``.result``, but
    the guard itself is byte-identical logic)."""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001 - one subskill's escape must not fail the whole brief
        logger.error("brief subskill %r raised: %s: %s", phase, type(exc).__name__, exc)
        return failed_result()


class _ToolServer(Protocol):
    """See ``existing_customer_brief.skill``'s own identical ``_ToolServer``
    -- declared locally here too rather than imported, matching every
    subskill's own precedent for this exact Protocol."""

    @property
    def research(self) -> ResearchTool: ...


def _transport_name(ctx: SkillContext) -> str:
    """See ``existing_customer_brief.skill._transport_name``'s own
    docstring for the full rationale (identical here, independently
    declared)."""
    if ctx.tools is None:
        return "none"
    return type(cast(_ToolServer, ctx.tools).research).__name__


def _emit(ctx: SkillContext, part: dict) -> None:
    if ctx.emit_part is not None:
        ctx.emit_part(part)


def _phase_proof(failed_by_phase: Mapping[str, bool]) -> list[str]:
    completed = [name for name in _PHASE_ORDER if not failed_by_phase[name]]
    failed = [name for name in _PHASE_ORDER if failed_by_phase[name]]
    return [
        f"Phases completed: {', '.join(completed) if completed else 'none'}",
        f"Phases failed: {', '.join(failed) if failed else 'none'}",
    ]


def _render_markdown(prospect_name: str, parts: list[dict]) -> str:
    """This flow only ever produces ``phase_section`` parts (no internal
    data tool means no ``metric_grid``/``table`` -- see the module
    docstring), so this is simpler than ``existing_customer_brief``'s own
    ``_render_markdown``: one heading per part, nothing to branch on."""
    sections = [f"# {prospect_name} Brief (Prospect)"]
    for part in parts:
        payload = part["payload"]
        sections.append(f"## {payload['title']}\n\n{payload['markdown']}")
    return "\n\n".join(sections)


def run(ctx: SkillContext, args: Args) -> SkillResult:
    """See the module docstring for the full D10 ordering and sharing-
    decision rationale. Each subskill call below is guarded (item 2 / I-4,
    see ``_run_subskill_or_failed``'s own docstring): a raw exception
    escaping any one of them becomes that phase's own failed-phase result,
    never a crash that discards the whole brief."""
    research_result = _run_subskill_or_failed(
        "research",
        lambda: research_subskill.run(ctx, MODE_PROSPECT, args.prospect_name),
        lambda: research_subskill.failed_result(MODE_PROSPECT),
    )
    parts: list[dict] = list(research_result.parts)
    for part in research_result.parts:
        _emit(ctx, part)

    contextualize_result = _run_subskill_or_failed(
        "contextualize",
        lambda: contextualize_subskill.run(
            ctx, MODE_PROSPECT, args.prospect_name, {}, research_result.synthesis_inputs
        ),
        contextualize_subskill.failed_result,
    )
    parts.extend(contextualize_result.parts)
    for part in contextualize_result.parts:
        _emit(ctx, part)

    strategize_result = _run_subskill_or_failed(
        "strategize",
        lambda: strategize_subskill.run(
            ctx,
            MODE_PROSPECT,
            args.prospect_name,
            contextualize_result.synthesis_inputs[0]["text"],
            research_result.synthesis_inputs,
            {},
        ),
        strategize_subskill.failed_result,
    )
    parts.extend(strategize_result.parts)
    for part in strategize_result.parts:
        _emit(ctx, part)

    proof = [
        f"Subject: {args.prospect_name}",
        *_phase_proof(
            {
                "research": research_result.failed,
                "contextualize": contextualize_result.failed,
                "strategize": strategize_result.failed,
            }
        ),
        f"Transport: {_transport_name(ctx)}",
    ]

    artifacts = []
    if ctx.artifacts is not None:
        markdown_body = _render_markdown(args.prospect_name, parts)
        ref, pdf_proof = build_brief_pdf(
            ctx.artifacts,
            title=f"{args.prospect_name} Prospect Brief",
            markdown_body=markdown_body,
            key_prefix=f"new-prospect-brief/{_today().isoformat()}",
        )
        artifacts.append(ref)
        proof.extend(pdf_proof)
    else:
        proof.append(_ARTIFACT_SKIP_PROOF)

    return SkillResult(ok=True, parts=parts, proof=proof, artifacts=artifacts)
