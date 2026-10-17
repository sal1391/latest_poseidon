"""Skill ``reporting.monthly_performance`` -- the monthly performance report.

An internal skill (``SKILL_META["internal"]``): the report flow dispatches it
with chip-built arguments; the router never sees it. ``run`` is the whole
pipeline of the reports design, in this order:

1. periods -- ``resolve_periods`` against ``_today()``; a ``PeriodError`` is a 422.
2. the store -- no ``ctx.report_store`` is a structured 500.
3. scope -- D16 row scope resolved once (``resolve_row_scope_value``) and
   stamped on every spec; the perspective picks the office and entity columns.
4. one snapshot (one REPEATABLE READ connection, ruling 8) holding every query:
   the month-presence check WITHOUT the office filter (ruling 14, D50), the
   certified office check, the two frames and two totals with reconciliation
   (``tools/frames.py``), the month-over-month math (``core/analytics/mom``)
   and, for an office with movers, the two whole-book context frames.
5. outside the snapshot, the narrative decision: no rows in either month, one
   empty window, stub mode, no LLM client, or ``tools/narrate.py``.
6. the template narrative (always), suggested questions, the payload.
7. HTML, then PDF behind a guard.
8. one ``report_run`` row.
9. the ``report`` part, the proof lines and the PDF artifact.

Rulings carried here:

- 11: the KPI figures are in scope for every sentence of the grounding check;
  ``build_index`` reads them from the payload this skill assembles.
- 12: every early return is a ``SkillResult(ok=False)`` and nothing has been
  written -- PR A1 writes no ``report_run`` row on any failure; the ``error``
  status is reserved for Phase 18's queued runs.
- 13: a PDF renderer failure (no Pango on the host) is a flag, not a failed
  report; the HTML is the durable artifact and the flag says why there is no
  PDF.
- ``grounded`` is True whenever the template is used because no model was
  called (stub mode, no data in either month, one empty window); it is False
  only when a model was asked and failed or withheld, or when ``ctx.llm`` is
  None in live mode.

``run`` holds the only ``try/except`` blocks, for ``PeriodError``,
``ReconciliationError`` and the PDF guard; anything else (a data backend
error) propagates to the registry's own structured 500. ``_today`` and
``resolve_row_scope_value`` are module-level names looked up at call time so
the offline tests can replace them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast

from poseidon.core.analytics import mom
from poseidon.core.data.query_builder import resolve_row_scope_value
from poseidon.core.identity import UserContext
from poseidon.core.llm.prompt_tree import PromptTree
from poseidon.core.llm.roles import RoleClient
from poseidon.core.parsing.period_labels import label_window, month_window
from poseidon.core.skills.context import ArtifactRef, SkillContext
from poseidon.core.skills.result import SkillResult, problem, report_part
from poseidon.core.util.uuid7 import uuid7
from poseidon.reports.store import NewRun

from .schema import Args
from .tools import render
from .tools.context import build_entity_context
from .tools.frames import (
    ENTITY,
    GROUP_BY,
    ReconciliationError,
    frame_spec,
    rollup,
    run_slices,
    timed,
)
from .tools.grounding import build_index
from .tools.narrate import narrate
from .tools.narrative_template import render_template_narrative
from .tools.payload import build_payload, narrative_section, with_flag
from .tools.periods import PeriodError, resolve_periods
from .tools.questions import select_questions

__all__ = ["ENTITY", "GROUP_BY", "PERSPECTIVES", "Perspective", "run"]

ANONYMOUS = "dev|anonymous"
NARRATE_PROMPT_ID = "reporting/narrate"


@dataclass(frozen=True)
class Perspective:
    office_column: str | None
    office_label: str | None  # "Customer Broker Office" | "Supply Team Office" | None
    entity_column: str
    entity_label: str
    noun: str  # "customers" | "suppliers"


PERSPECTIVES = {
    "customer": Perspective(
        "CUSTOMER_TEAM_NAME", "Customer Broker Office", "CUST_NM", "Customer", "customers"
    ),
    "supplier": Perspective(
        "PRIMARY_SUPPLY_TEAM_OFFICE", "Supply Team Office", "SUPPLIER_NM", "Supplier", "suppliers"
    ),
    "book": Perspective(None, None, "CUST_NM", "Customer", "customers"),
}


def _today() -> date:
    """The reference date for period resolution; tests monkeypatch it."""
    return date.today()


def _now() -> datetime:
    """The run's generated_at; tests monkeypatch it."""
    return datetime.now(UTC)


def safe_filename(name: str) -> str:
    """Ruling 13: keep [A-Za-z0-9 ._-], replace the rest with _, collapse spaces."""
    cleaned = re.sub(r"[^A-Za-z0-9 ._-]", "_", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "report"


def _fail(status: int, title: str, detail: str) -> SkillResult:
    return SkillResult(ok=False, error=problem(status, title, detail))


def run(ctx: SkillContext, args: Args) -> SkillResult:
    # 1. periods
    try:
        periods = resolve_periods(args.basis, args.month, args.window_kind, _today())
    except PeriodError as exc:
        return _fail(422, "invalid report period", str(exc))

    # 2. the store
    store = ctx.report_store
    if store is None:
        return _fail(
            500,
            "report store unavailable",
            "reporting.monthly_performance needs SkillContext.report_store; "
            "the report flow (PR A2) and the demo script wire it",
        )

    # 3. scope, perspective, filters, dialect
    user = cast(UserContext | None, ctx.user)
    scope_value = resolve_row_scope_value(ENTITY, user)
    perspective = PERSPECTIVES[args.perspective]
    office_column = perspective.office_column
    entity_column = perspective.entity_column
    filters = {} if office_column is None else {office_column: (args.office_value,)}
    dialect = "snowflake" if ctx.settings.data_backend == "snowflake" else "postgres"

    # 4. every query, inside one snapshot
    with ctx.data.snapshot() as data:
        months = data.list_months(ENTITY, {}, scope_value)
        if periods.current.start not in months:
            return _fail(422, "no data for that month", f"no rows for {periods.label_current}")
        if office_column is not None:
            offices = data.list_dimension_values(ENTITY, office_column, scope_value=scope_value)
            if args.office_value not in offices:
                return _fail(
                    422,
                    "unknown office",
                    f"unknown {office_column} value {args.office_value!r}",
                )
        try:
            slices = run_slices(data, periods, filters, scope_value, dialect)
        except ReconciliationError as exc:
            return _fail(500, "reconciliation failed", str(exc))

        def merged(column: str) -> list[mom.MergedRow]:
            return mom.merge_breakdowns(
                rollup(slices.prior, column), rollup(slices.current, column)
            )

        entities = merged(entity_column)
        ports = merged("LOC_NM")
        deals = merged("DEAL_CLASSIFICATION_TRADE_CUT")
        kpis = mom.compute_kpis(
            [r.values for r in slices.prior.rows], [r.values for r in slices.current.rows]
        )
        movers = mom.top_movers(entities)
        new_lost = mom.new_lost(entities)
        tables = {
            "entities_by_gp": mom.ranked(entities, "GP", 10, perspective.noun),
            "entities_by_volume": mom.ranked(entities, "VOLUME", 10, perspective.noun),
            "ports_by_gp": mom.ranked(ports, "GP", 10, "ports"),
            "ports_by_volume": mom.ranked(ports, "VOLUME", 10, "ports"),
            "deal_class": mom.ranked(deals, "GP", len(deals) or 1, "deal classes"),
        }
        queries = list(slices.queries)
        mover_keys = [m.row.key for m in (*movers.increases, *movers.decreases)]

        entity_context: dict[str, dict] | None
        if office_column is None:
            entity_context = None
            context_line = "Context: none (whole book report)"
        elif not mover_keys:
            entity_context = {}
            context_line = "Context: none (no movers)"
        else:
            book = {}
            for side, window in (("prior", periods.prior), ("current", periods.current)):
                spec = frame_spec(window, {entity_column: tuple(mover_keys)}, scope_value)
                book[side], proof = timed(
                    f"context {side}",
                    spec,
                    dialect,
                    lambda s=spec: data.run_frame_query(s),
                    lambda frame: len(frame.rows),
                )
                queries.append(proof)
            entity_context = build_entity_context(
                book["prior"],
                book["current"],
                slices.prior,
                slices.current,
                perspective,
                cast(str, args.office_value),
                mover_keys,
            )
            context_line = f"Context: {len(mover_keys)} movers x 2 windows, whole book"

    # 5-6. the template narrative, the narrative decision, the questions
    run_id = str(uuid7())
    generated_at = _now()
    source = ctx.settings.data_backend
    generated_by = (user.name or user.email or user.sub) if user is not None else ANONYMOUS
    created_by = user.sub if user is not None else ANONYMOUS
    scope_label = (
        "Whole book"
        if office_column is None
        else f"{args.office_value} ({perspective.office_label})"
    )
    common = {
        "run_id": run_id,
        "perspective": args.perspective,
        "entity_column": entity_column,
        "entity_label": perspective.entity_label,
        "office_column": office_column,
        "office_value": args.office_value,
        "scope_label": scope_label,
        "periods": periods,
        "kpis": kpis,
        "tables": tables,
        "movers": movers,
        "new_lost": new_lost,
        "entity_context": entity_context,
        "queries": queries,
        "generated_at": generated_at,
        "source": source,
        "generated_by": generated_by,
    }
    template_md = render_template_narrative(
        build_payload(**common, narrative=None, suggested_questions=[], proof=[])
    )
    partial = build_payload(
        **common,
        narrative=narrative_section(template_md, None, False, []),
        suggested_questions=[],
        proof=[],
    )
    index = build_index(partial)

    prior_rows, current_rows = len(slices.prior.rows), len(slices.current.rows)
    no_data = prior_rows == 0 and current_rows == 0
    llm_md: str | None = None
    grounded = True
    model_questions: list[str] = []
    flags: list[str] = []
    model_call = None
    if no_data:
        flags = ["no data in either month"]
    elif prior_rows == 0 or current_rows == 0:
        empty = periods.label_prior if prior_rows == 0 else periods.label_current
        flags = [f"model narrative skipped: no rows in {empty}"]
    elif ctx.settings.llm_mode == "stub":
        flags = ["model narrative skipped: stub mode"]
    elif ctx.llm is None:
        flags = ["model narrative failed: no LLM client"]
        grounded = False
    else:
        tree = cast(PromptTree, ctx.prompt_tree or PromptTree())
        prompt = tree.resolve(NARRATE_PROMPT_ID)
        model_call = narrate(partial, cast(RoleClient, ctx.llm), prompt, index)
    if model_call is None:
        narrative_proof = f"narrative: template; {flags[0]}"
    else:
        llm_md, grounded = model_call.llm_md, model_call.grounded
        flags, model_questions = list(model_call.flags), list(model_call.model_questions)
        narrative_proof = model_call.proof_line

    suggested_questions = [] if no_data else select_questions(model_questions, partial, index)

    # the proof lines
    office_filter = (
        "whole book" if office_column is None else f"{office_column} = {args.office_value}"
    )
    window_word = "complete" if periods.window_kind == "complete" else "month to date"
    proof = [
        f"Entity: {ENTITY} - source: {source} - {office_filter}",
        f"Windows: {periods.prior.start}..{periods.prior.end} (prior) - "
        f"{periods.current.start}..{periods.current.end} (current) - "
        f"basis: prior month - window: {window_word}",
        f"Frames: six-way group-by, prior {prior_rows} rows, current {current_rows} rows; "
        "totals reconciled to the cent",
        context_line,
        narrative_proof,
        f"Run: report_run {run_id}",
    ]
    payload = build_payload(
        **common,
        narrative=narrative_section(template_md, llm_md, grounded, flags),
        suggested_questions=suggested_questions,
        proof=proof,
    )

    # 7. HTML, then PDF behind the ruling-13 guard
    html = render.render_html(payload)
    pdf: bytes | None
    try:
        pdf = render.render_pdf(html)
    except Exception as exc:  # noqa: BLE001 - ruling 13: a flag, not a failed report
        pdf = None
        payload = with_flag(payload, f"PDF not rendered: {type(exc).__name__}")
        html = render.render_html(payload)  # the stored HTML carries the notice too

    # 8. one report_run row
    store.insert_run(
        created_by,
        NewRun(
            id=run_id,
            perspective=args.perspective,
            office_column=office_column,
            office_value=args.office_value,
            period_current=periods.current,
            period_prior=periods.prior,
            window_kind=periods.window_kind,
            basis=args.basis,
            status="ok",
            payload_version=payload["payload_version"],
            payload=payload,
            html=html.encode("utf-8"),
            pdf=pdf,
            narrative_grounded=grounded,
            source=source,
            created_by=created_by,
            conversation_id=ctx.conversation_id,
            generated_at=generated_at,
        ),
    )

    # 9. the report part, the proof, the artifact
    scope_name = args.office_value or "Whole book"
    if periods.window_kind == "complete":
        period_name = label_window(periods.current)
    else:
        period_name = (
            f"{label_window(month_window(args.month.year, args.month.month))} month to date"
        )
    artifacts = (
        [
            ArtifactRef(
                name=safe_filename(f"{scope_name} {period_name}") + ".pdf",
                url=f"/api/reports/{run_id}/pdf",
                mime="application/pdf",
            )
        ]
        if pdf
        else []
    )
    return SkillResult(
        ok=True, parts=[report_part(payload)], proof=list(payload["proof"]), artifacts=artifacts
    )
