"""Skill ``customer_insight.existing_customer_brief`` (Phase 8 Task 4; doc
02 section 4.1): assembles the tools and subskills Tasks 1-3 shipped into
the brief the whole overhaul exists for.

ORDER (doc 02 section 4.1's own numbered list, extended by this plan's own
"Concurrency" paragraph):

1. ``fetch_metrics`` + ``fetch_top_ports`` -- the six certified metrics
   (prior year vs YTD) and a top-ports breakdown. Their ``metric_grid``/
   ``table`` parts are emitted IMMEDIATELY via ``ctx.emit_part`` (when
   wired), before anything else runs -- the user sees certified numbers
   the instant they are ready, not after three more phases complete.
2. ``contextualize`` (Sonnet synthesis over the internal data) and
3. ``research`` (three Perplexity-shaped structured calls) run
   CONCURRENTLY, ``ThreadPoolExecutor(max_workers=2)`` -- see "CONCURRENT,
   NOT SEQUENTIAL" below for why this is safe. Both futures are always
   ``.result()``-ed in the SAME fixed order (contextualize, then research)
   regardless of which one's worker actually finishes first, and every
   part is appended to this skill's own ``parts`` list -- and streamed via
   ``ctx.emit_part`` -- in that identical fixed order too. Concurrency
   changes only WHEN the work happens, never what order its OUTPUT
   appears in (a house rule this whole phase holds to).
4. ``strategize`` -- awaits both of the above, fills the CRM template.
5. ``build_brief_pdf`` -- renders everything to a stored PDF, or skips
   with a pinned proof line when no artifact store is configured (doc 07's
   own dev-reality: a local dev box need not run MinIO to demo the rest of
   this skill).

CONCURRENT, NOT SEQUENTIAL (contrast with ``new_prospect_brief``, disclosed
judgment call). ``contextualize.subskill.run``'s signature is fixed (by
Task 3) to accept ``research_inputs`` as a required positional argument --
but that does NOT mean this skill must wait for research before calling
contextualize. Doc 02 section 4.1's own numbered list describes
contextualize as synthesis "over the internal data block + the ontology
field dictionary" -- no research dependency in the EXISTING-customer
case, unlike the PROSPECT case (see ``new_prospect_brief/skill.py``'s own
module docstring, "SEQUENTIAL, NOT CONCURRENT", for the contrasting
reasoning one skill over). Because contextualize's real value here comes
entirely from data this skill ALREADY fetched (step 1, before the
concurrent section even starts), it has nothing to wait on research FOR --
so this skill calls it with ``research_inputs=()`` (empty), and dispatches
it side by side with research rather than paying for research's latency
twice. This is exactly what doc 02's own "Concurrency" paragraph
describes: "for existing customers, contextualize and research run
concurrently ... strategize awaits both" -- strategize is the step that
actually needs BOTH outputs, and it runs strictly after this section joins.

ONE INTERNAL DATA VIEW (disclosed judgment call). ``contextualize``'s own
``data_block`` and ``strategize``'s own ``data_summary`` are both opaque
``Mapping[str, object]`` parameters neither subskill prescribes a shape
for (see each one's own module docstring). Rather than author two
independently-shaped dicts describing the SAME real, certified facts to
two different downstream consumers -- an invitation for them to quietly
drift apart -- this skill builds ONE dict (``_data_summary``) from the
already-fetched ``MetricResult``/``BreakdownResult`` pair and passes it as
BOTH arguments.

CURRENT SERVICES -- NO FABRICATION (the T3-flagged carry, resolved here).
``strategize.subskill``'s own "CURRENT SERVICES" docstring section
documents an optional ``"services"`` key in ``data_summary``: a sequence of
service-name strings, rendered verbatim when present, otherwise the
"[requires live synthesis]" placeholder for an EXISTING customer (a
prospect gets the different, pinned "no current services" text instead --
not this skill's concern; see ``strategize.subskill`` directly). Neither
``fetch_metrics`` nor ``fetch_top_ports`` exposes a service-line dimension
-- ``ontology/ontology.yml``'s own ``MARINE_SALES_PLANNING_V`` entity has
no such column at all (checked directly against the certified column list:
``POI_ID``, the two fixture/inquiry measures, ``LIFT_ETA_DATE``,
``GROSS_PROFIT``, ``FIXED_TONS``, ``CUST_NM``, ``LOC_NM`` -- nothing that
distinguishes bunkering from brokering from any other marine service this
customer might use). Inferring "this customer buys bunkering" from the
mere PRESENCE of sales figures on a general marine-sales entity would be
guessing a business fact this tool never asked for and never received --
exactly what the T4 dispatch carry's own instruction warns against. This
module's ``_data_summary`` therefore never sets a ``"services"`` key,
letting ``strategize.subskill``'s own documented fallback apply honestly.

METRIC DISPLAY -- A FIXED, HAND-AUTHORED CONSTANT, NOT AN ONTOLOGY LOOKUP
(disclosed judgment call, mirroring ``contextualize.subskill``'s own
``_FIELD_DICTIONARY`` precedent for the identical reasoning). ``fetch_
metrics`` returns raw ``MetricResult`` pairs and explicitly "defers
grid-shaping" to this skill (see that tool's own module docstring); the
"richer style" of friendly names/units/rounding it points to lives in
``data_qa.metric_query.tools.format_parts`` -- but that module's own
``_friendly_and_unit``/``_round`` are PRIVATE (underscore-prefixed)
helpers of a DIFFERENT skill, and this codebase's own established
convention (``research.web_research``'s own docstring, ``research``
subskill's own docstring) is to never import a private symbol
cross-skill. Rather than build a second ontology-reading formatter for
what is always exactly the same six certified names
(``fetch_metrics.SIX_METRICS`` never varies per request), ``_METRIC_
DISPLAY`` is a small, fixed constant covering exactly those six names,
cross-checked against them by this skill's own co-located test
(``test_metric_display_keys_match_fetch_metrics_six_metrics_exactly``) so
the two lists cannot silently drift -- the display text was arrived at
independently by reading ``ontology/ontology.yml``'s own column
definitions, not imported. ``_round`` (0dp except MARGIN's 2dp) is
reproduced locally too, since it needs no ontology access at all -- a pure
numeric rule keyed only by metric name.

PROOF-LINE COMPOSITION. This skill's own proof block opens with two header
lines (``Entity:``/``Backend:``) this skill IS positioned to prove (it
holds ``ctx.settings``, which none of the P3 tools receive -- see
``existing_customer_brief/tools/__init__.py``'s own "Proof-line ownership"
section, written ahead of this task specifically to hand that
responsibility here), then the P3 tools' own already-tested proof
fragments VERBATIM (never reformatted -- a reader sees the SAME lines
``fetch_metrics``'/``fetch_top_ports``'s own tests already pin), then this
skill's own summary lines: which of the three subskill PHASES (
contextualize, research, strategize -- the only three with an honest
``failed`` bool in this architecture; ``fetch_metrics``/``fetch_top_ports``
either succeed or the whole dispatch fails, see below) completed vs
failed, which research transport answered, and the artifact's status.

"ANCHOR" AND THE WALL CLOCK. ``Args`` carries no date field (doc 02 section
4.1 never asks for one -- a brief is always "as of now"), so this module
is the one place allowed to read ``date.today()``: see ``_today``'s own
docstring for why that read goes through a tiny, monkeypatchable seam
rather than a bare call, mirroring ``api/live_chat.py``'s own
``reference_date=date.today()`` boundary-read pattern one level further
in (``core/parsing/period_parser.py``'s own docstring argues "now" should
always be an ARGUMENT from the caller's point of view; this module is
that caller).

A JANUARY 1 ANCHOR is the one input shape ``fetch_metrics`` itself
rejects (its YTD window would be empty) -- caught here and reported as a
structured 422 rather than the registry's generic 500-wraps-a-ValueError
fallback, the same "well-understood failure gets its own clean status"
discipline ``data_qa.metric_query``'s own ``skill.py`` uses for
``SpecValidationError``.
"""

from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Protocol, cast

from poseidon.core.data.client import BreakdownResult, MetricResult
from poseidon.core.data.specs import PeriodWindow
from poseidon.core.ontology.loader import get_ontology
from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.result import (
    SkillResult,
    metric_grid_part,
    problem,
    table_part,
)
from poseidon.mcp.registry import ResearchTool

from .schema import Args
from .subskills.contextualize import subskill as contextualize_subskill
from .subskills.research import subskill as research_subskill
from .subskills.research.subskill import MODE_EXISTING
from .subskills.strategize import subskill as strategize_subskill
from .tools.build_brief_pdf import build_brief_pdf
from .tools.fetch_metrics import SIX_METRICS, fetch_metrics
from .tools.fetch_top_ports import fetch_top_ports

_ENTITY = "MARINE_SALES_PLANNING_V"
_TOP_N = 5

# Hand-authored (friendly, unit) for exactly fetch_metrics.SIX_METRICS --
# see the module docstring's "METRIC DISPLAY" section for why this is a
# fixed constant rather than an ontology lookup. Values read from
# ontology/ontology.yml's own MARINE_SALES_PLANNING_V column definitions
# (FIXED_TONS -> Volume/tons, GROSS_PROFIT -> Gross Profit/USD) and
# data_qa.metric_query.tools.format_parts's own _DISPLAY_OVERRIDES
# precedent (MARGIN, "# Lost" for NUM_LOST's #_INQUIRIES-sharing collision).
_METRIC_DISPLAY: dict[str, tuple[str, str | None]] = {
    "VOLUME": ("Volume", "tons"),
    "GP": ("Gross Profit", "USD"),
    "MARGIN": ("Margin", None),
    "NUM_WON": ("# Won", None),
    "NUM_INQUIRIES": ("# Inquiries", None),
    "NUM_LOST": ("# Lost", None),
}

# MARGIN alone renders at 2dp (a ratio's natural range is sub-100, where
# whole-number rounding would flatten distinct values together); every
# other certified metric here renders at 0dp -- the identical rule
# data_qa.metric_query.tools.format_parts's own _round applies, reproduced
# locally since it needs no ontology access (see the module docstring).
_RATIO_METRICS = frozenset({"MARGIN"})

_PORTS_COLUMNS = ["Port", "Gross Profit"]

# The three subskill phases this skill runs, in doc 02 section 4.1's own
# numbered order (2. contextualize, 3. research, 4. strategize) -- the
# fixed order every proof/part list below uses, regardless of which
# subskill's ThreadPoolExecutor worker actually finishes first.
_PHASE_ORDER = ("contextualize", "research", "strategize")

_ARTIFACT_SKIP_PROOF = "Artifact: skipped (no artifact store configured)"


def _today() -> date:
    """The brief's anchor date. A bare ``date.today()``, but named and
    called through this module-level seam rather than inline, so a test
    can pin it deterministically (``monkeypatch.setattr(skill, "_today",
    lambda: FIXED_DATE)``) -- see the module docstring's "ANCHOR AND THE
    WALL CLOCK" section for the fuller rationale."""
    return date.today()


def _round(metric: str, value: float | None) -> float | int | None:
    """``None`` (SQL NULL -- "no rows") passes through unchanged; MARGIN
    renders at 2dp, every other certified metric at 0dp."""
    if value is None:
        return None
    if metric in _RATIO_METRICS:
        return round(value, 2)
    return round(value)


def _ports_window(anchor: date, recency_days: int) -> PeriodWindow:
    """The ``recency_days`` days immediately before ``anchor`` -- see the
    schema module's own docstring for why this is the window ``fetch_top_
    ports`` receives (its own docstring explicitly leaves the window
    choice to its caller)."""
    return PeriodWindow(anchor - timedelta(days=recency_days), anchor)


def _metric_grid(prior: MetricResult, ytd: MetricResult) -> dict:
    periods = {
        "a": {"start": prior.period.start.isoformat(), "end": prior.period.end.isoformat()},
        "b": {"start": ytd.period.start.isoformat(), "end": ytd.period.end.isoformat()},
    }
    metrics = []
    for metric in SIX_METRICS:
        friendly, unit = _METRIC_DISPLAY[metric]
        metrics.append(
            {
                "name": metric,
                "friendly": friendly,
                "a": _round(metric, prior.values.get(metric)),
                "b": _round(metric, ytd.values.get(metric)),
                "unit": unit,
            }
        )
    return metric_grid_part(periods=periods, metrics=metrics)


def _ports_table(result: BreakdownResult) -> dict:
    rows = [[row.key, _round("GP", row.values.get("GP"))] for row in result.rows]
    return table_part(columns=_PORTS_COLUMNS, rows=rows)


def _data_summary(
    prior: MetricResult, ytd: MetricResult, ports: BreakdownResult
) -> dict[str, object]:
    """The internal facts this brief actually fetched -- passed to BOTH
    ``contextualize``'s own ``data_block`` and ``strategize``'s own
    ``data_summary`` (see the module docstring's "ONE INTERNAL DATA VIEW").

    Deliberately carries no ``"services"`` key -- see the module
    docstring's "CURRENT SERVICES -- NO FABRICATION" section.
    """
    summary: dict[str, object] = {}
    for metric in SIX_METRICS:
        friendly, _unit = _METRIC_DISPLAY[metric]
        summary[f"{friendly} (prior year)"] = _round(metric, prior.values.get(metric))
        summary[f"{friendly} (YTD)"] = _round(metric, ytd.values.get(metric))
    if ports.rows:
        summary["Top ports by Gross Profit"] = ", ".join(
            f"{row.key} ({_round('GP', row.values.get('GP'))})" for row in ports.rows
        )
    return summary


def _phase_proof(failed_by_phase: Mapping[str, bool]) -> list[str]:
    completed = [name for name in _PHASE_ORDER if not failed_by_phase[name]]
    failed = [name for name in _PHASE_ORDER if failed_by_phase[name]]
    return [
        f"Phases completed: {', '.join(completed) if completed else 'none'}",
        f"Phases failed: {', '.join(failed) if failed else 'none'}",
    ]


class _ToolServer(Protocol):
    """Structural shape this skill needs from ``ctx.tools`` -- declared
    locally, same as every subskill's own identical ``_ToolServer``
    Protocol (see ``research.subskill``'s own docstring for why this is
    never centralized in ``poseidon.mcp.registry`` itself)."""

    @property
    def research(self) -> ResearchTool: ...


def _transport_name(ctx: SkillContext) -> str:
    """The proof block's "Transport" line. ``research.subskill``'s own
    ``SubskillResult`` does not preserve each call's ``ResearchResult
    .transport`` string (only ``schema_name``/``title``/``summary``/
    ``items``/``degraded``/``degrade_reason`` -- see that module's own
    ``_success_input``), so this skill cannot report the PER-CALL
    transport without either duplicating a search or reaching past the
    subskill's own contract -- neither of which this task's brief asks
    for. What it CAN honestly report, with no extra call and no broken
    encapsulation: the class name of the ``ResearchTool`` ``ctx.tools
    .research`` resolves to (every call research.subskill makes within one
    dispatch goes through the SAME, lazily-cached instance -- see
    ``poseidon.mcp.registry.ToolServerRegistry``'s own docstring), or
    ``"none"`` when ``ctx.tools`` was never wired at all -- the identical
    convention ``research.web_research``'s own ``skill.py`` uses for its
    absent-tools case.
    """
    if ctx.tools is None:
        return "none"
    return type(cast(_ToolServer, ctx.tools).research).__name__


def _emit(ctx: SkillContext, part: dict) -> None:
    if ctx.emit_part is not None:
        ctx.emit_part(part)


def _metric_grid_markdown(payload: dict) -> str:
    lines = ["## Metrics", "", "| Metric | Prior Year | YTD |", "| --- | --- | --- |"]
    for metric in payload["metrics"]:
        lines.append(f"| {metric['friendly']} | {metric['a']} | {metric['b']} |")
    return "\n".join(lines)


def _ports_table_markdown(payload: dict) -> str:
    columns = payload["columns"]
    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join(["---"] * len(columns)) + " |"
    lines = ["## Top Ports", "", header, divider]
    for row in payload["rows"]:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def _render_markdown(customer: str, parts: list[dict]) -> str:
    """The brief's markdown body for ``build_brief_pdf`` -- one section per
    part, in the SAME order the user sees them streamed. Generic over the
    three kinds this skill ever produces (``metric_grid``, ``table``,
    ``phase_section``); any other kind is skipped rather than crashing the
    PDF over a shape this skill never emits."""
    sections = [f"# {customer} Brief"]
    for part in parts:
        payload = part["payload"]
        if part["kind"] == "metric_grid":
            sections.append(_metric_grid_markdown(payload))
        elif part["kind"] == "table":
            sections.append(_ports_table_markdown(payload))
        elif part["kind"] == "phase_section":
            sections.append(f"## {payload['title']}\n\n{payload['markdown']}")
    return "\n\n".join(sections)


def run(ctx: SkillContext, args: Args) -> SkillResult:
    """See the module docstring for the full ordering, concurrency, and
    proof-composition contract."""
    anchor = _today()
    try:
        prior, ytd, metrics_proof = fetch_metrics(ctx.data, args.customer, anchor)
    except ValueError as exc:
        return SkillResult(ok=False, error=problem(422, "invalid brief request", str(exc)))

    ports_result, ports_proof = fetch_top_ports(
        ctx.data, args.customer, _ports_window(anchor, args.recency_days), top_n=_TOP_N
    )

    grid_part = _metric_grid(prior, ytd)
    ports_part = _ports_table(ports_result)
    parts: list[dict] = [grid_part, ports_part]
    _emit(ctx, grid_part)
    _emit(ctx, ports_part)

    data_summary = _data_summary(prior, ytd, ports_result)

    with ThreadPoolExecutor(max_workers=2) as pool:
        contextualize_future = pool.submit(
            contextualize_subskill.run, ctx, MODE_EXISTING, args.customer, data_summary, ()
        )
        research_future = pool.submit(research_subskill.run, ctx, MODE_EXISTING, args.customer)
        # FIXED consumption order (doc 02 section 4.1's own numbering:
        # contextualize before research) regardless of which future's
        # worker actually finished first -- see the module docstring's
        # "ORDER" section.
        contextualize_result = contextualize_future.result()
        research_result = research_future.result()

    parts.extend(contextualize_result.parts)
    parts.extend(research_result.parts)
    for part in contextualize_result.parts:
        _emit(ctx, part)
    for part in research_result.parts:
        _emit(ctx, part)

    strategize_result = strategize_subskill.run(
        ctx,
        MODE_EXISTING,
        args.customer,
        contextualize_result.synthesis_inputs[0]["text"],
        research_result.synthesis_inputs,
        data_summary,
    )
    parts.extend(strategize_result.parts)
    for part in strategize_result.parts:
        _emit(ctx, part)

    entity = get_ontology().entity(_ENTITY)
    proof = [
        f"Entity: {entity.fqn}",
        f"Backend: {ctx.settings.data_backend}",
        *metrics_proof,
        *ports_proof,
        *_phase_proof(
            {
                "contextualize": contextualize_result.failed,
                "research": research_result.failed,
                "strategize": strategize_result.failed,
            }
        ),
        f"Transport: {_transport_name(ctx)}",
    ]

    artifacts = []
    if ctx.artifacts is not None:
        markdown_body = _render_markdown(args.customer, parts)
        ref, pdf_proof = build_brief_pdf(
            ctx.artifacts,
            title=f"{args.customer} Brief",
            markdown_body=markdown_body,
            key_prefix=f"existing-customer-brief/{anchor.isoformat()}",
        )
        artifacts.append(ref)
        proof.extend(pdf_proof)
    else:
        proof.append(_ARTIFACT_SKIP_PROOF)

    return SkillResult(ok=True, parts=parts, proof=proof, artifacts=artifacts)
