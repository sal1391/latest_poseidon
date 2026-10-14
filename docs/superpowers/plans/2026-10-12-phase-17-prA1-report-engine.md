# Phase 17 PR A1 — Report Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python report engine that, given a perspective, an office and a month, runs six certified queries in one snapshot, computes every figure once, writes a template narrative, asks the model for a grounded narrative, renders HTML and PDF, stores the run in Postgres and returns a `report` part — provable end to end from pytest and a demo script over the synthetic data, with no chat flow and no renderer yet.

**Architecture:** One new task package `backend/poseidon/tasks/reporting/` with a single internal skill `monthly_performance` whose tools own the pipeline stages (periods, frames, math, context, narrative, grounding, render, store). Shared pieces land where the spec puts them: `FrameQuerySpec` in the certified query builder, the month-over-month math in `core/analytics/mom.py`, the run table as Drizzle migration `0003`, the narrative prompt in a new `backend/prompts/` tree. The LLM layer gains a forced tool call. Nothing here touches the chat orchestrator, the renderer registry or the contracts corpus; PR A2 wires the flow, the part renderer, `report_lookup`, the download routes and the synthetic overlay.

**Tech Stack:** Python 3.14 in `backend/.venv`, pytest 9.1.1, psycopg 3, SQLAlchemy 2 plain SQL, Jinja2, WeasyPrint (lazy), Postgres 16 on port 5434 for `pg` tests, Drizzle Kit for the migration. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §5 (report contract), §6.6 (prompts), §7 (storage, rendering), §10 (failure design), §11 row A, §12, §13. Where that spec is silent the reports design `docs/superpowers/specs/2026-09-04-monthly-performance-reports-design.md` §4 and §5.7 stand. Owner rulings of 2026-10-12 (binding here): PR A ships as A1 (this plan) and A2; suggested-question chips backfill to three deterministic questions; month to date is named explicitly in the file name and header; the report turn has no lead text part.

**Branch:** `phase-17-report-engine`, created from `main` at `8980f33`. PR A2 takes the spec's branch name `phase-17-report-in-chat`. Work in this checkout, not a worktree, because the `pg` suite and the Drizzle migration need the running compose database on port 5434.

## Global Constraints

- Always `backend/.venv/Scripts/python.exe`, never a bare `python` (CLAUDE.md gotcha: a different global 3.14 without sqlalchemy).
- Never pipe pytest. Redirect to a file under `.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/` and `tail` the file.
- Do **not** put `-q` on pytest commands; `backend/pyproject.toml` already sets `addopts = "-q"` and a second one hides the summary line.
- Offline suite (from `backend/`, bash):
  `env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live'`
- pg suite (from `backend/`, bash; never parallelised):
  `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg`
- Lint: `.venv/Scripts/python.exe -m ruff check .` from `backend/`. Never `ruff format` repo-wide; you MAY run `ruff format <path>` on a file this plan creates (new files only, named in the task).
- Source files touched here stay **pure ASCII on disk** (`tests/test_period_labels.py::test_module_is_ascii_on_disk` enforces it for `period_labels.py`; keep the convention in every new module and test). Display strings use the ASCII hyphen-minus for negative numbers (`-53,500`), never U+2212.
- Frozen surfaces untouched: SSE envelope, the ten existing part kinds (the `report` kind is additive), `contracts/turns` (if `tests/contracts` shows a diff after any task, STOP and report; this PR must not regenerate it), the certified ontology and metric formulas, Alembic 0001-0010.
- The folder law: tools, subskills and tests co-located under `backend/poseidon/tasks/reporting/skills/monthly_performance/`. The one exception is the D46 prompt tree (Task 7).
- `core/chat` and `core/llm` never import `poseidon.tasks`; `core/analytics` imports nothing from `tasks` either.
- Additive dataclass growth only: `ConversationSlots`, `SkillContext`, `RegisteredSkill` grow by appending fields with defaults; never reorder.
- Every query the report runs carries D16 row scope through `resolve_row_scope_value(entity, user)` exactly as `metric_query` does (the sales entity has no row scope today, so the value is `None`; the call still happens).
- Commit after every task with the message given; every commit ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Implementers run the tests and show the failing-first output in their report; reviewers never re-run the suite.

## Execution model (owner rule, applies to every Phase 17 PR)

- **Each PR runs in a fresh chat.** This plan is PR A1. PR A2 gets its own plan and its own chat, started from a handoff prompt this chat writes at the end.
- **Fable is the supervisor only.** It dispatches one implementer per task, a fresh reviewer per diff, judges the reports, keeps the morning-review list and writes the PR description. It never writes implementation code. The one exception: an implementer stuck after two or three real attempts, in which case Fable writes the smallest unblocking fix and hands back.
- **Models per task.** Sonnet implements Tasks 1, 2, 4, 5, 6, 7, 10; Opus implements Tasks 3 (the math is the number the sales team acts on), 8 (the grounding check) and 9 (the skill). Reviews: Sonnet reviews Sonnet work; Opus reviews Tasks 1, 3, 8, 9 and the whole branch at the end.
- **The implementer runs the tests and its report is the evidence; the reviewer never re-runs the suite.**
- Ledger directory: `.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/` (self-ignored). The morning-review list lives there as `morning-review.md`.

## Rulings made in this plan (for the morning-review list)

1. **Prompt tree location.** D46 says "one canonical top-level `prompts/` tree". The dev compose build context is `../backend` and the production image copies `backend/` to `/app`, so a repo-root `prompts/` would be invisible at runtime. The tree lives at `backend/prompts/` (top level of the backend runtime, next to `poseidon/`). The loader class is `PromptTree` because `PromptRegistry` already names the Jinja loader in `core/llm/prompts.py`.
2. **Forced tool call.** `RoleClient.invoke` and `LLMProvider.invoke` gain `tool_choice: str | None = None`. The role client forwards it to the provider **only when set**, so the six existing provider doubles in the test suite keep their signatures.
3. **Stub mode and the narrative.** When `settings.llm_mode == "stub"` the model narrative is skipped (template narrative used, `grounded = true`, flag `model narrative skipped: stub mode`), mirroring the brief subskills. The pg suite therefore never calls a model.
4. **29 February.** The parser's existing year-shift rule (same calendar day, clamped to the target month's length, end floored at start + 1 day) stands; a window can be a day shorter across a leap boundary and that is the calendar's fact. Pinned by a test in Task 4. Month to date never shifts by a year; its prior window is clamped to the prior month's length (spec §5.2).
5. **Month-to-date labels.** The report never labels by shape. `resolve_periods` returns labels built from the known `window_kind`; `period_labels.label_month_to_date` is the one place the words "month to date" are appended.
6. **Driver vocabulary** adds `lost` beside `new` (an entity absent from the current month), so a lost row is never called `volume-driven`.
7. **Reconciliation tolerance** is half a cent: `abs(frame_total - independent_total) < 0.005`. Footer-equals-tile is exact equality because both come from the same `totals()` call over the same rows.
8. **Snapshot.** `DataClient.snapshot()` is a context manager yielding a client bound to one `REPEATABLE READ` connection. The synthetic client implements it; the Snowflake handoff decides later (D54).
9. **`report_run` grants.** `GRANT SELECT, INSERT ON report_run TO poseidon_app` only. No UPDATE in this phase (Phase 18 adds `superseded_by` writes with its own grant).
10. **The PDF link.** The skill returns `ArtifactRef(url="/api/reports/<run_id>/pdf")`; the route that serves it is PR A2's. Until then the card's link 404s, which the A1 demo script sidesteps by writing the PDF to disk.
11. **KPI figures are in scope for every sentence of the grounding check** (Task 8; D67's "names no entity" clause read as "entity figures need the entity named, KPI figures never do").
12. **No `report_run` row on any failure** (Task 9; `status = 'error'` is reserved for Phase 18).
13. **A PDF render failure is a flag, not a failed run** (Task 9; the host has no Pango, the container does).
14. **The month-presence check ignores the office filter** (Task 9; "no data for that month" is about the dataset, an empty office slice is an `ok` run).
15. **`created_by` without an identity is `dev|anonymous`** (Task 9; demo script and hand-built contexts only).

Rulings 11-15 are restated with their reasons at the end of the self-review.

## Review Focus

Inputs the spec implies but did not spell out, each pinned to a test in the task that owns the code:

1. **An office slice with rows in the prior month only** (the office went quiet). Expected: KPIs show current zero, every entity is `lost`, the ranked tables have a footer of zero, no reconciliation failure, no narrative call. (Task 9 `test_office_with_rows_in_the_prior_month_only_is_an_ok_run_with_lost_entities`.)
2. **An entity name containing markup or a Jinja delimiter** (`<img src=x>`, `{{ 7*7 }}`). Expected: rendered as text everywhere; WeasyPrint fetches nothing; the payload JSON carries it verbatim. (Task 6 `test_hostile_entity_name_renders_as_text_and_fetches_nothing`; Task 9 `test_payload_carries_a_hostile_name_verbatim`.)
3. **A prior-month MARGIN of zero with a non-zero current margin** (prior volume present, prior GP zero). Expected: pct `None` rendered `new`, driver `margin-driven`, no division error. (Task 3 `test_zero_prior_margin_yields_a_null_pct_and_a_margin_driver`.)
4. **A model narrative whose numbers are right but attributed to the wrong entity.** Expected: the grounding check rejects it on the first pass, the retry prompt names the sentence, the second failure falls back to the template with the count shown. (Task 8 `test_must_reject_fixture_attributes_customer_b_figure_to_customer_a`; Task 9 `test_grounding_failure_twice_uses_the_template_and_flags_the_count`.)
5. **A reference date on the first of the month** for a month-to-date request. Expected: a 422 whose detail says there are no complete days yet; no query runs. (Task 4 `test_mtd_on_the_first_of_the_month_refuses`; Task 9 `test_mtd_on_day_one_returns_422_before_any_query`.)

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `backend/poseidon/core/data/specs.py` | `FrameQuerySpec` (frozen dataclass) | 1 |
| `backend/poseidon/core/data/query_builder.py` | `build_frame_query`, `build_months_query`, `_filter_conditions` extracted from `_where_clause` | 1 |
| `backend/poseidon/core/data/__init__.py` | exports | 1 |
| `backend/tests/test_query_builder_snapshots.py` | byte-pinned SQL for both dialects, refusal cases | 1 |
| `backend/poseidon/core/data/client.py` | `FrameRow`, `FrameResult`, Protocol gains `list_months`, `run_frame_query`, `snapshot` | 2 |
| `backend/poseidon/core/data/synthetic_client.py` | the three new methods; `_SnapshotClient` bound to one REPEATABLE READ connection | 2 |
| `backend/tests/test_synthetic_client_pg.py`, `backend/tests/test_synthetic_client_snapshot.py` (new) | pg goldens recomputed from the generator; offline isolation-level test | 2 |
| `backend/poseidon/core/analytics/__init__.py`, `mom.py` (new) | the month-over-month math and display formatting, one home | 3 |
| `backend/tests/test_analytics_mom.py` (new) | hand-computed fixtures, driver boundaries, zero-base rule, footer property | 3 |
| `backend/poseidon/core/parsing/period_labels.py` | `label_month_to_date`, `days_in_month` | 4 |
| `backend/poseidon/core/parsing/period_parser.py` | dedupe: import `month_window` and `KIND_*` from `period_labels` | 4 |
| `backend/poseidon/tasks/reporting/__init__.py`, `skills/__init__.py`, `skills/monthly_performance/__init__.py`, `tools/__init__.py`, `tests/__init__.py` (new, empty) | package scaffolding (no `task.yml` until Task 9, so nothing registers early) | 4 |
| `backend/poseidon/tasks/reporting/skills/monthly_performance/tools/periods.py` (new) | `resolve_periods`, `ResolvedPeriods`, `PeriodError` | 4 |
| `.../monthly_performance/tests/test_periods.py` (new), `backend/tests/test_period_labels.py`, `backend/tests/test_parsing_periods.py` | period tests | 4 |
| `web/src/db/schema.ts`, `web/drizzle/0003_report_run.sql`, `web/drizzle/meta/0003_snapshot.json`, `web/drizzle/meta/_journal.json` | the table, the widened mode check, the grant | 5 |
| `web/src/db/migrations.test.ts`, `web/src/db/schema.test.ts` | tripwires updated | 5 |
| `backend/poseidon/reports/__init__.py`, `store.py` (new) | `ReportStore`, `NewRun`, `ReportRun` over plain SQL in `rls_transaction` | 5 |
| `backend/tests/test_report_store.py` (new) | pg round trip, missing run, bytes | 5 |
| `backend/poseidon/config/templates/report.html.j2` (new) | the HTML report, autoescaped | 6 |
| `.../monthly_performance/tools/render.py` (new) | `render_html`, `render_pdf`, `refuse_fetch` | 6 |
| `.../monthly_performance/tests/test_render.py` (new) | escaping, Outlook lint, structure, pdf-marked bytes | 6 |
| `backend/prompts/reporting/narrate.xml` (new) | the narrative system prompt, versioned | 7 |
| `backend/poseidon/core/llm/prompt_tree.py` (new) | `PromptTree`, `PromptSpec` | 7 |
| `backend/poseidon/core/llm/roles.py`, `bedrock.py`, `stub.py`, `backend/poseidon/core/chat/dev_router.py` | `tool_choice` | 7 |
| `backend/tests/test_prompt_tree.py` (new), `backend/tests/test_llm_roles.py`, `backend/tests/test_llm_bedrock.py` | prompt resolution; forced tool call plumbing | 7 |
| `.../monthly_performance/tools/narrative_template.py`, `grounding.py`, `questions.py` (new); `tests/payload_fixture.py` gains `mockup_payload()` | template narrative; entity-scoped grounding; suggested questions with backfill | 8 |
| `.../monthly_performance/tests/test_narrative_template.py`, `test_grounding.py`, `test_questions.py` (new) | mockup must-pass, must-reject, exemptions, backfill | 8 |
| `backend/poseidon/tasks/reporting/task.yml`, `.../monthly_performance/schema.py`, `skill.py`, `tools/frames.py`, `tools/context.py`, `tools/payload.py`, `tools/narrate.py` (new) | the skill | 9 |
| `backend/poseidon/core/skills/registry.py`, `context.py`, `result.py`, `backend/poseidon/core/llm/prompts.py`, `backend/poseidon/api/live_chat.py` | `internal` flag and `router_skill_ids`; `SkillContext.report_store` and `prompt_tree`; `report_part` | 9 |
| `.../monthly_performance/tests/fake_client.py` (offline `DataClient` + multi-office rows), `tests/test_skill.py` (offline), `tests/test_skill_pg.py` (pg); `backend/tests/test_skill_registry.py`, `backend/tests/test_llm_prompts.py` | skill tests; registry pins | 9 |
| `backend/poseidon/scripts/demo_report.py` (new), `backend/tests/test_demo_report.py` (new) | the thing Carlos runs | 10 |
| `docs/architecture/02-backend-skills.md`, `03-llm-routing.md`, `04-data-ontology.md`, `05-auth-identity.md`, `07-infrastructure.md`, `CLAUDE.md` | docs and verified figures | 10 |

---

### Task 1: `FrameQuerySpec`, `build_frame_query`, `build_months_query`

**Model:** Sonnet implements; Opus reviews (the certified SQL surface).

**Files:**
- Modify: `backend/poseidon/core/data/specs.py` (append after `BreakdownQuerySpec`, line 67)
- Modify: `backend/poseidon/core/data/query_builder.py` (extract `_filter_conditions` from `_where_clause` at 334-380; add two builders after `build_period_range_query`, line 489+)
- Modify: `backend/poseidon/core/data/__init__.py` (exports, lines 190-208)
- Test: `backend/tests/test_query_builder_snapshots.py` (new section after the breakdown section)

**Interfaces:**
- Consumes: `_entity`, `_require_scope_value`, `_require_certified_metrics`, `_require_dimension`, `_validate_filters`, `_metric_expr`, `_is_volume_mode`, `_table_name`, `_date_expr`, `_scope_condition` (all existing in `query_builder.py`).
- Produces:
  ```python
  @dataclass(frozen=True)
  class FrameQuerySpec:
      entity: str
      metrics: tuple[str, ...]           # certified metric names; WIN_RATE refused by the builder
      period: PeriodWindow
      group_by: tuple[str, ...]          # one or more certified dimension columns, in order
      filters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
      scope_value: str | None = None     # D16, same contract as MetricQuerySpec

  def build_frame_query(spec: FrameQuerySpec, dialect: str) -> tuple[str, list]
  def build_months_query(
      entity: str, filters: Mapping[str, tuple[str, ...]], dialect: str, scope_value: str | None = None
  ) -> tuple[str, list]
  ```
  SQL shape of `build_frame_query` (postgres, sales entity, group_by `("CUST_NM", "LOC_NM")`, metrics `("GP", "VOLUME")`, filter `CUSTOMER_TEAM_NAME = Rotterdam`):
  ```
  SELECT COALESCE(CUST_NM, 'Unknown') AS CUST_NM, COALESCE(LOC_NM, 'Unknown') AS LOC_NM, SUM(GROSS_PROFIT) AS "GP", SUM(FIXED_TONS) AS "VOLUME"
  FROM synthetic.marine_sales_planning_v
  WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s AND COALESCE(CUSTOMER_TEAM_NAME, 'Unknown') IN (%s)
  GROUP BY 1, 2
  ```
  No `ORDER BY`, no `LIMIT`, never a `HAVING` (WIN_RATE is refused before rendering). Snowflake differs only in the table name `SANDBOX.MCA.MARINE_SALES_PLANNING_V`.
  SQL shape of `build_months_query` (postgres, same filter):
  ```
  SELECT DISTINCT CAST(DATE_TRUNC('month', LIFT_ETA_DATE) AS DATE) AS "MONTH"
  FROM synthetic.marine_sales_planning_v
  WHERE COALESCE(CUSTOMER_TEAM_NAME, 'Unknown') IN (%s)
  ORDER BY 1
  ```
  With no filters and no scope the `WHERE` line is omitted. For the GL entity on snowflake the date expression is `TO_DATE(PERIOD_DATE)` through `_date_expr`, as today.
  Refusals (all `SpecValidationError`, exact text pinned): empty `group_by` → `"frame query needs at least one group_by column"`; a non-dimension column → the existing `_require_dimension` text; `WIN_RATE` in metrics → `"WIN_RATE is a ratio; a frame carries NUM_WON and NUM_INQUIRIES and the caller recomputes it"`; volume mode → `"frames are not allowed in volume mode (CLASS4 = 'Volume'); use a breakdown grouped by CLASS3"`; a duplicate column in `group_by` → `"group_by repeats 'CUST_NM'"`.

- [ ] **Step 1: Write the failing snapshot tests**

Append a new section to `backend/tests/test_query_builder_snapshots.py` (after the breakdown section, before the period-range section), importing `FrameQuerySpec` at the top next to `BreakdownQuerySpec`:

```python
# ---------------------------------------------------------------------------
# build_frame_query -- postgres / snowflake (Phase 17 PR A1, reports design 7.4)
# ---------------------------------------------------------------------------


def test_frame_by_customer_and_port_in_an_office_postgres():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP", "VOLUME"),
        period=APRIL,
        group_by=("CUST_NM", "LOC_NM"),
        filters={"CUSTOMER_TEAM_NAME": ("Rotterdam",)},
    )
    sql, params = qb.build_frame_query(spec, "postgres")
    assert sql == (
        "SELECT COALESCE(CUST_NM, 'Unknown') AS CUST_NM, "
        "COALESCE(LOC_NM, 'Unknown') AS LOC_NM, "
        'SUM(GROSS_PROFIT) AS "GP", SUM(FIXED_TONS) AS "VOLUME"\n'
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s "
        "AND COALESCE(CUSTOMER_TEAM_NAME, 'Unknown') IN (%s)\n"
        "GROUP BY 1, 2"
    )
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), "Rotterdam"]


def test_frame_by_customer_and_port_in_an_office_snowflake():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP", "VOLUME"),
        period=APRIL,
        group_by=("CUST_NM", "LOC_NM"),
        filters={"CUSTOMER_TEAM_NAME": ("Rotterdam",)},
    )
    sql, params = qb.build_frame_query(spec, "snowflake")
    assert sql == (
        "SELECT COALESCE(CUST_NM, 'Unknown') AS CUST_NM, "
        "COALESCE(LOC_NM, 'Unknown') AS LOC_NM, "
        'SUM(GROSS_PROFIT) AS "GP", SUM(FIXED_TONS) AS "VOLUME"\n'
        "FROM SANDBOX.MCA.MARINE_SALES_PLANNING_V\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s "
        "AND COALESCE(CUSTOMER_TEAM_NAME, 'Unknown') IN (%s)\n"
        "GROUP BY 1, 2"
    )
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1), "Rotterdam"]


def test_frame_with_the_report_six_way_group_by_and_no_filter_postgres():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP", "VOLUME", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST"),
        period=APRIL,
        group_by=(
            "CUST_NM",
            "SUPPLIER_NM",
            "LOC_NM",
            "CUSTOMER_TEAM_NAME",
            "PRIMARY_SUPPLY_TEAM_OFFICE",
            "DEAL_CLASSIFICATION_TRADE_CUT",
        ),
    )
    sql, params = qb.build_frame_query(spec, "postgres")
    assert sql.startswith("SELECT COALESCE(CUST_NM, 'Unknown') AS CUST_NM, ")
    assert sql.endswith("\nGROUP BY 1, 2, 3, 4, 5, 6")
    assert "ORDER BY" not in sql and "LIMIT" not in sql and "HAVING" not in sql
    assert 'SUM("#_INQUIRIES") - SUM("#_FIXTURES") AS "NUM_LOST"' in sql
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1)]


def test_frame_refuses_win_rate():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP", "WIN_RATE"),
        period=APRIL,
        group_by=("CUST_NM",),
    )
    with pytest.raises(qb.SpecValidationError, match="WIN_RATE is a ratio"):
        qb.build_frame_query(spec, "postgres")


def test_frame_refuses_an_empty_group_by():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V", metrics=("GP",), period=APRIL, group_by=()
    )
    with pytest.raises(qb.SpecValidationError, match="at least one group_by column"):
        qb.build_frame_query(spec, "postgres")


def test_frame_refuses_a_repeated_group_by_column():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        group_by=("CUST_NM", "CUST_NM"),
    )
    with pytest.raises(qb.SpecValidationError, match="group_by repeats 'CUST_NM'"):
        qb.build_frame_query(spec, "postgres")


def test_frame_refuses_a_non_dimension_column():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        group_by=("GROSS_PROFIT",),
    )
    with pytest.raises(qb.SpecValidationError, match="is not a dimension of"):
        qb.build_frame_query(spec, "postgres")


def test_frame_refuses_volume_mode():
    spec = FrameQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=APRIL,
        group_by=("CLASS3",),
        filters={"CLASS4": ("Volume",)},
    )
    with pytest.raises(qb.SpecValidationError, match="frames are not allowed in volume mode"):
        qb.build_frame_query(spec, "snowflake")


def test_frame_carries_row_scope_last_like_the_other_builders():
    """Same injected-ontology shape the row-scope tests above use: the
    scope predicate is the LAST condition and its value the LAST param."""
    # Copy the Entity/Ontology fixture construction from the existing
    # row-scope test in this file (search "RowScope(" above) and build the
    # frame against it; assert the WHERE ends with " AND REGION = %s" and
    # params end with the scope value.


# ---------------------------------------------------------------------------
# build_months_query
# ---------------------------------------------------------------------------


def test_months_query_with_an_office_filter_postgres():
    sql, params = qb.build_months_query(
        "MARINE_SALES_PLANNING_V", {"CUSTOMER_TEAM_NAME": ("Rotterdam",)}, "postgres"
    )
    assert sql == (
        'SELECT DISTINCT CAST(DATE_TRUNC(\'month\', LIFT_ETA_DATE) AS DATE) AS "MONTH"\n'
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE COALESCE(CUSTOMER_TEAM_NAME, 'Unknown') IN (%s)\n"
        "ORDER BY 1"
    )
    assert params == ["Rotterdam"]


def test_months_query_without_filters_omits_the_where_line_snowflake():
    sql, params = qb.build_months_query("MARINE_SALES_PLANNING_V", {}, "snowflake")
    assert sql == (
        'SELECT DISTINCT CAST(DATE_TRUNC(\'month\', LIFT_ETA_DATE) AS DATE) AS "MONTH"\n'
        "FROM SANDBOX.MCA.MARINE_SALES_PLANNING_V\n"
        "ORDER BY 1"
    )
    assert params == []


def test_months_query_on_gl_snowflake_wraps_the_varchar_date():
    sql, _ = qb.build_months_query("W_MARINE_GL_SOURCE_AI", {}, "snowflake")
    assert sql.startswith(
        'SELECT DISTINCT CAST(DATE_TRUNC(\'month\', TO_DATE(PERIOD_DATE)) AS DATE) AS "MONTH"\n'
    )
```

Fill the row-scope test body by copying the fixture construction from the file's existing row-scope tests (they inject an `Ontology` with a `RowScope`); the assertion is the only new content.

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
cd backend
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_query_builder_snapshots.py -k "frame or months" > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t1-red.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t1-red.txt
```

Expected: collection error `ImportError: cannot import name 'FrameQuerySpec'`. That is the right failure: the spec type does not exist.

- [ ] **Step 3: Add the spec and the builders**

In `specs.py`, append `FrameQuerySpec` exactly as in Interfaces, with a docstring that says: a frame is a grouped multi-metric slice with no ordering and no row cap, rolled up in Python; the caller recomputes ratios.

In `query_builder.py`:
1. Extract the filter loop of `_where_clause` (the `for col, values in filters.items()` block) into `_filter_conditions(entity, filters, params) -> list[str]` and call it from `_where_clause` so the existing snapshots stay byte-identical.
2. Add `build_frame_query`: validate in this order: `_entity`, `_require_scope_value`, `_require_certified_metrics`, the WIN_RATE refusal, the empty `group_by` refusal, the duplicate refusal, `_require_dimension` per column, `_validate_filters`, the volume-mode refusal. Render the select list as the COALESCE columns (same text as the breakdown builder) followed by `_metric_expr` per metric; `_where_clause` unchanged; `GROUP BY ` + `", ".join(str(i) for i in range(1, len(group_by) + 1))`. Clauses joined with `"\n"`.
3. Add `build_months_query`: validate `_entity`, `_require_scope_value`, `_validate_filters`; conditions = `_filter_conditions(...)` plus `_scope_condition(...)` when not `None`; clauses `SELECT DISTINCT CAST(DATE_TRUNC('month', {date_expr}) AS DATE) AS "MONTH"`, `FROM {table}`, `WHERE ...` only when conditions exist, `ORDER BY 1`.
4. Export both and `FrameQuerySpec` from `core/data/__init__.py` (keep `__all__` sorted).

- [ ] **Step 4: Run the snapshot module and verify it passes**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_query_builder_snapshots.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t1-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t1-green.txt
```

Expected: every test in the module passes, including the pre-existing breakdown and metric snapshots (proof the `_filter_conditions` extraction changed no bytes).

- [ ] **Step 5: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/data tests/test_query_builder_snapshots.py
cd ..
git add backend/poseidon/core/data/specs.py backend/poseidon/core/data/query_builder.py backend/poseidon/core/data/__init__.py backend/tests/test_query_builder_snapshots.py
git commit -q -F - <<'EOF'
feat(data): FrameQuerySpec and build_months_query for the monthly report

A frame is a grouped multi-metric slice with COALESCE per column, GROUP BY
1..n, the same where clause and row scope as every other builder, and no
ORDER BY, LIMIT or HAVING; WIN_RATE and volume mode are refused. The months
query lists the first days of the months that have rows under the same
filters. Both dialects are byte-pinned.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 2: `DataClient` gains `list_months`, `run_frame_query` and `snapshot`

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Modify: `backend/poseidon/core/data/client.py` (result types after `PeriodRange`, line 110; Protocol lines 122-146)
- Modify: `backend/poseidon/core/data/synthetic_client.py`
- Modify: `backend/poseidon/core/data/__init__.py` (export `FrameResult`, `FrameRow`)
- Modify: `backend/poseidon/core/ontology/models.py:88-125` (the D16 "FLIP CHECKLIST" docstring, one added line)
- Test: `backend/tests/test_synthetic_client_pg.py` (pg), `backend/tests/test_synthetic_client_snapshot.py` (new, offline)

**Interfaces:**
- Consumes: `build_frame_query`, `build_months_query`, `FrameQuerySpec` (Task 1).
- Produces:
  ```python
  @dataclass(frozen=True)
  class FrameRow:
      keys: tuple[str, ...]              # one post-COALESCE value per group_by column, in spec order
      values: dict[str, float | None]    # metric name -> value

  @dataclass(frozen=True)
  class FrameResult:
      entity: str
      group_by: tuple[str, ...]
      rows: list[FrameRow]

  class DataClient(Protocol):
      ...existing four methods...
      def list_months(
          self, entity: str, filters: Mapping[str, tuple[str, ...]], scope_value: str | None = None
      ) -> list[date]: ...
      def run_frame_query(self, spec: FrameQuerySpec) -> FrameResult: ...
      def snapshot(self) -> AbstractContextManager["DataClient"]: ...
  ```
  `SyntheticDataClient.snapshot()` opens ONE psycopg connection with `conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ` and `conn.read_only = True`, yields a `_SnapshotClient` that implements the same seven methods over that connection (its own `snapshot()` returns a context manager yielding itself), and closes the connection on exit (rollback, never commit). `_SnapshotClient` reuses the module's query-building and row-shaping code: factor the per-method "build SQL, fetch, shape rows" into module-level functions that take a `fetch: Callable[[str, Sequence, int | None], list[tuple]]` so the two clients share them.

- [ ] **Step 1: Write the failing offline test**

Create `backend/tests/test_synthetic_client_snapshot.py`:

```python
"""Offline: the snapshot client binds every query to one REPEATABLE READ,
read-only connection (D54) and the plain client still opens one per call.
A fake ``psycopg.connect`` records what the client asked for."""

import datetime as dt

import psycopg

from poseidon.core.data.specs import FrameQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.data.synthetic_client import SyntheticDataClient

APRIL = PeriodWindow(dt.date(2026, 4, 1), dt.date(2026, 5, 1))
SALES = "MARINE_SALES_PLANNING_V"


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return self._rows

    def fetchmany(self, n):
        return self._rows[:n]


class _Conn:
    opened = []

    def __init__(self):
        self.isolation_level = None
        self.read_only = None
        self.closed = False
        self.rolled_back = False
        _Conn.opened.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True
        return False

    def cursor(self):
        return _Cursor([("ACME", 10.0, 2.0)])

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _fake_connect(monkeypatch):
    _Conn.opened.clear()
    monkeypatch.setattr(psycopg, "connect", lambda dsn, connect_timeout: _Conn())


def test_snapshot_runs_every_query_on_one_repeatable_read_connection(monkeypatch):
    _fake_connect(monkeypatch)
    client = SyntheticDataClient("postgresql://u:p@h/db")
    frame = FrameQuerySpec(SALES, ("GP", "VOLUME"), APRIL, ("CUST_NM",))
    total = MetricQuerySpec(SALES, ("GP",), APRIL)

    with client.snapshot() as snap:
        rows = snap.run_frame_query(frame).rows
        snap.run_metric_query(total)
        snap.run_metric_query(total)

    assert len(_Conn.opened) == 1
    conn = _Conn.opened[0]
    assert conn.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ
    assert conn.read_only is True
    assert conn.closed is True
    assert rows[0].keys == ("ACME",) and rows[0].values == {"GP": 10.0, "VOLUME": 2.0}


def test_plain_client_still_opens_one_connection_per_call(monkeypatch):
    _fake_connect(monkeypatch)
    client = SyntheticDataClient("postgresql://u:p@h/db")
    client.run_metric_query(MetricQuerySpec(SALES, ("GP",), APRIL))
    client.run_metric_query(MetricQuerySpec(SALES, ("GP",), APRIL))
    assert len(_Conn.opened) == 2
    assert all(c.isolation_level is None for c in _Conn.opened)
```

(The metric-query fake row `("ACME", 10.0, 2.0)` has three cells against one requested metric; `_metric_result` zips with `strict=True`, so give the metric calls their own cursor rows: make `_Cursor` return `[(10.0,)]` when the SQL does not contain `GROUP BY`. Keep the test honest rather than loosening the zip.)

- [ ] **Step 2: Write the failing pg tests**

Append to `backend/tests/test_synthetic_client_pg.py` (module is already `pytestmark = pytest.mark.pg` and exposes `DATASET`, `CLIENT`, `sales_in`, `APRIL_2026`, `SALES`):

```python
def test_list_months_returns_the_eighteen_generated_months_in_order():
    months = CLIENT.list_months(SALES, {}, scope_value=None)
    assert months[0] == dt.date(2025, 1, 1)
    assert months[-1] == dt.date(2026, 6, 1)
    assert len(months) == 18
    assert months == sorted(months)


def test_list_months_under_an_office_filter_is_a_subset():
    office = sorted({r["CUSTOMER_TEAM_NAME"] for r in DATASET.sales_rows})[0]
    months = CLIENT.list_months(SALES, {"CUSTOMER_TEAM_NAME": (office,)}, scope_value=None)
    expected = sorted(
        {
            r["LIFT_ETA_DATE"].replace(day=1)
            for r in DATASET.sales_rows
            if r["CUSTOMER_TEAM_NAME"] == office
        }
    )
    assert months == expected


def test_frame_by_customer_and_port_matches_the_python_groupby():
    spec = FrameQuerySpec(
        entity=SALES,
        metrics=("GP", "VOLUME", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST"),
        period=APRIL_2026,
        group_by=("CUST_NM", "LOC_NM"),
        scope_value=None,
    )
    result = CLIENT.run_frame_query(spec)
    expected: dict[tuple[str, str], dict[str, float]] = {}
    for r in sales_in(APRIL_2026):
        key = (r["CUST_NM"], r["LOC_NM"])
        acc = expected.setdefault(
            key, {"GP": 0.0, "VOLUME": 0.0, "NUM_WON": 0.0, "NUM_INQUIRIES": 0.0}
        )
        acc["GP"] += r["GROSS_PROFIT"]
        acc["VOLUME"] += r["FIXED_TONS"]
        acc["NUM_WON"] += r["#_FIXTURES"]
        acc["NUM_INQUIRIES"] += r["#_INQUIRIES"]
    assert {row.keys for row in result.rows} == set(expected)
    for row in result.rows:
        exp = expected[row.keys]
        assert row.values["GP"] == pytest.approx(exp["GP"])
        assert row.values["VOLUME"] == pytest.approx(exp["VOLUME"])
        assert row.values["NUM_LOST"] == pytest.approx(exp["NUM_INQUIRIES"] - exp["NUM_WON"])


def test_frame_rollup_equals_the_independent_total_to_the_cent():
    frame = CLIENT.run_frame_query(
        FrameQuerySpec(SALES, ("GP", "VOLUME"), APRIL_2026, ("CUST_NM", "LOC_NM"), scope_value=None)
    )
    total = CLIENT.run_metric_query(
        MetricQuerySpec(SALES, ("GP", "VOLUME"), APRIL_2026, scope_value=None)
    )
    assert abs(sum(r.values["GP"] for r in frame.rows) - total.values["GP"]) < 0.005
    assert abs(sum(r.values["VOLUME"] for r in frame.rows) - total.values["VOLUME"]) < 0.005


def test_snapshot_client_answers_like_the_plain_client():
    spec = MetricQuerySpec(SALES, ("GP",), APRIL_2026, scope_value=None)
    with CLIENT.snapshot() as snap:
        inside = snap.run_metric_query(spec)
        months = snap.list_months(SALES, {}, scope_value=None)
    assert inside == CLIENT.run_metric_query(spec)
    assert months == CLIENT.list_months(SALES, {}, scope_value=None)
```

Add `FrameQuerySpec` to the file's `specs` import.

- [ ] **Step 3: Run both to verify they fail**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_synthetic_client_snapshot.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t2-red.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t2-red.txt
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_synthetic_client_pg.py -k "months or frame or snapshot" > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t2-red-pg.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t2-red-pg.txt
```

Expected: `AttributeError: 'SyntheticDataClient' object has no attribute 'snapshot'` / `list_months` / `run_frame_query`.

- [ ] **Step 4: Implement**

In `client.py`: add `FrameRow`, `FrameResult` after `PeriodRange`; import `AbstractContextManager` from `contextlib` and `Mapping` from `collections.abc`; extend the Protocol with the three methods and update its docstring ("the seven calls a skill makes").

In `synthetic_client.py`:
- Module-level shaping functions `_metric_result(spec, rows)`, `_breakdown_result(spec, rows)`, `_frame_result(spec, rows)`, `_months(rows)`, `_period_range(rows)`; the existing methods call them.
- `SyntheticDataClient.list_months`, `run_frame_query` through `_fetch`.
- `SyntheticDataClient.snapshot()` is a `@contextmanager` that does `conn = self._connect(); conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ; conn.read_only = True; try: yield _SnapshotClient(conn) finally: conn.rollback(); conn.close()`. Set the two attributes BEFORE the first statement (psycopg applies them to the next transaction).
- `_SnapshotClient(conn)` holds the connection and implements all seven methods through its own `_fetch` that runs `with self._conn.cursor() as cur` inside `span("db:query")`; `snapshot()` is `@contextmanager` yielding `self`.
- Keep `DIMENSION_VALUE_LIMIT` on both paths.

In `models.py` add one line to the FLIP CHECKLIST docstring: the three Phase 17 methods (`list_months`, `run_frame_query`, `snapshot`) are optional on test fakes; only the reporting skill's fakes implement them.

- [ ] **Step 5: Run both to verify they pass**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_synthetic_client_snapshot.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t2-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t2-green.txt
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_synthetic_client_pg.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t2-green-pg.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t2-green-pg.txt
```

Expected: both modules fully green (the whole pg client module, not only the new tests).

- [ ] **Step 6: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/data poseidon/core/ontology/models.py tests/test_synthetic_client_snapshot.py tests/test_synthetic_client_pg.py
cd ..
git add backend/poseidon/core/data backend/poseidon/core/ontology/models.py backend/tests/test_synthetic_client_snapshot.py backend/tests/test_synthetic_client_pg.py
git commit -q -F - <<'EOF'
feat(data): list_months, run_frame_query and a REPEATABLE READ snapshot client

The synthetic client answers the two new protocol calls and can bind a run
of queries to one read-only REPEATABLE READ connection (D54), so a report's
six queries see one state of the data. pg goldens recompute every
expectation from the generator; an offline test pins the isolation level.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 3: `core/analytics/mom.py` — the month-over-month math, one home

**Model:** Opus implements; Opus reviews. (These are the numbers the sales team acts on; mom-comparison has no tests of its own, so every expectation below is hand-computed and the implementer must re-derive each one by hand in its report before trusting the code.)

**Files:**
- Create: `backend/poseidon/core/analytics/__init__.py` (docstring only: "Deterministic analytics shared by the report and the chat skills. No I/O, no pandas, no imports from `poseidon.tasks`.")
- Create: `backend/poseidon/core/analytics/mom.py`
- Test: `backend/tests/test_analytics_mom.py` (new)

**Interfaces:**
- Consumes: nothing outside the standard library.
- Produces (every name below is used verbatim by Tasks 8 and 9):
  ```python
  SUM_METRICS = ("GP", "VOLUME", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST")
  KPI_METRICS = ("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST", "WIN_RATE")
  ROW_METRICS = ("GP", "VOLUME", "MARGIN")
  Driver = Literal["volume-driven", "margin-driven", "both", "unchanged", "new", "lost"]
  Status = Literal["both", "new", "lost"]
  Kind = Literal["money", "tons", "margin", "count", "rate"]
  KIND_BY_METRIC = {"GP": "money", "VOLUME": "tons", "MARGIN": "margin", "NUM_WON": "count",
                    "NUM_INQUIRIES": "count", "NUM_LOST": "count", "WIN_RATE": "rate"}

  @dataclass(frozen=True)
  class Delta:
      prior: float
      current: float
      change: float                 # current - prior
      pct_change: float | None      # percent units, against abs(prior); None when prior == 0

  def delta(prior: float, current: float) -> Delta
  def totals(rows: Iterable[Mapping[str, float | None]]) -> dict[str, float]
      # sums SUM_METRICS (None counts as 0); MARGIN = GP / VOLUME (0.0 when VOLUME == 0);
      # WIN_RATE = NUM_WON / NUM_INQUIRIES as a 0..1 ratio (0.0 when NUM_INQUIRIES == 0)
  def compute_kpis(prior_rows, current_rows) -> dict[str, Delta]      # keys = KPI_METRICS, in that order

  @dataclass(frozen=True)
  class MergedRow:
      key: str
      status: Status
      metrics: dict[str, Delta]     # ROW_METRICS always, plus NUM_WON/NUM_INQUIRIES/NUM_LOST when present

  def merge_breakdowns(
      prior: Mapping[str, Mapping[str, float]], current: Mapping[str, Mapping[str, float]]
  ) -> list[MergedRow]              # outer join on key, zero-filled, MARGIN recomputed per side; sorted by key
  def driver(row: MergedRow) -> Driver
  @dataclass(frozen=True) class Mover: row: MergedRow; driver: Driver
  @dataclass(frozen=True) class Movers: increases: list[Mover]; decreases: list[Mover]
  def top_movers(merged: Sequence[MergedRow], n: int = 5) -> Movers
  @dataclass(frozen=True) class NewLost: new: list[MergedRow]; lost: list[MergedRow]
  def new_lost(merged: Sequence[MergedRow]) -> NewLost
  @dataclass(frozen=True) class Ranked: rows: list[MergedRow]; other: MergedRow | None; footer: MergedRow
  def ranked(merged: Sequence[MergedRow], by: str, n: int = 10, noun: str = "customers") -> Ranked
  def share(numerator: float, denominator: float) -> float | None    # percent; None when denominator == 0
  def reconciles(a: float, b: float) -> bool                           # abs(a - b) < 0.005

  def fmt_value(x: float, kind: Kind) -> str
  def fmt_change(x: float, kind: Kind) -> str
  def fmt_pct(pct: float | None, change: float) -> str
  def display(d: Delta, kind: Kind) -> dict[str, str]                 # {"prior", "current", "change", "pct"}
  ```
  Rules, all verbatim from the spec and mom-comparison:
  - `delta`: `change = current - prior`; `pct_change = change / abs(prior) * 100` when `prior != 0`, else `None`.
  - `merge_breakdowns`: a key in both inputs is `both`; current-only is `new`; prior-only is `lost`. Missing sides are zero-filled for GP, VOLUME and any NUM_* present in either input; MARGIN per side is `GP / VOLUME` or `0.0`. Rows are returned sorted by key so output is deterministic.
  - `driver`: `new` when `status == "new"`, `lost` when `status == "lost"`; otherwise with `m_a = prior MARGIN`, `m_b = current MARGIN`, `vol_effect = (vol_b - vol_a) * m_a` (0 when `m_a == 0`), `margin_effect = (m_b - m_a) * vol_b` (0 when `vol_b == 0`); both zero → `unchanged`; `ratio = |vol| / (|vol| + |margin|)`; `> 0.65` → `volume-driven`; `< 0.35` → `margin-driven`; else `both`.
  - `top_movers`: increases are rows with GP change `> 0` sorted by change descending (ties by key ascending), first `n`; decreases are rows with GP change `< 0` sorted ascending, first `n`. `new` and `lost` rows take part.
  - `new_lost`: `new` rows sorted by `abs(current GP)` descending, `lost` by `abs(prior GP)` descending; ties by key.
  - `ranked`: sort by `metrics[by].current` descending (ties by key ascending); `rows` = first `n`; `other` = a `MergedRow` with key `f"Other ({k} {noun})"` summing the remaining `k` rows (status `both`; MARGIN recomputed from the summed GP and VOLUME; `None` when `k == 0`); `footer` = key `"Total"`, every metric summed over ALL rows with MARGIN recomputed, built by the same summing helper as `other`. Raise `ValueError` if `by` is not in `ROW_METRICS`.
  - `share(n, d)` = `n / d * 100` or `None`.
  - Formatting (mom-comparison's rules, Python's own `format`): money, tons and count → `f"{x:,.0f}"`; margin → `f"{x:.2f}"`; rate → `f"{x * 100:.1f}%"`. `fmt_change`: `"flat"` when `x == 0`; money/tons/count `f"{x:+,.0f}"`; margin `f"{x:+.2f}"`; rate `f"{x * 100:+.1f} pts"`. `fmt_pct`: `"flat"` when `change == 0` (checked first, so a zero-to-zero delta reads flat, not new); `"new"` when `pct is None`; else `f"{pct:+.1f}%"`. The minus sign is Python's ASCII hyphen.
  - The module has no I/O and no `datetime`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_analytics_mom.py`:

```python
"""Hand-computed expectations for the month-over-month math (reports
design 4.3, Phase 17 spec 5.4). mom-comparison ships no tests, so every
number here was derived by hand from the fixture below; the derivations
are in the comments so a reviewer can check them without running code."""

from random import Random

import pytest

from poseidon.core.analytics import mom

# Prior month (A) and current month (B), rows already rolled up per customer.
PRIOR = {
    "Alpha": {"GP": 100_000.0, "VOLUME": 4_000.0, "NUM_WON": 10.0, "NUM_INQUIRIES": 20.0, "NUM_LOST": 10.0},
    "Beta": {"GP": 50_000.0, "VOLUME": 2_500.0, "NUM_WON": 5.0, "NUM_INQUIRIES": 10.0, "NUM_LOST": 5.0},
    "Gamma": {"GP": 20_000.0, "VOLUME": 1_000.0, "NUM_WON": 2.0, "NUM_INQUIRIES": 4.0, "NUM_LOST": 2.0},
}
CURRENT = {
    "Alpha": {"GP": 90_000.0, "VOLUME": 4_500.0, "NUM_WON": 12.0, "NUM_INQUIRIES": 20.0, "NUM_LOST": 8.0},
    "Beta": {"GP": 65_000.0, "VOLUME": 2_500.0, "NUM_WON": 5.0, "NUM_INQUIRIES": 12.0, "NUM_LOST": 7.0},
    "Delta": {"GP": 10_000.0, "VOLUME": 500.0, "NUM_WON": 1.0, "NUM_INQUIRIES": 2.0, "NUM_LOST": 1.0},
}


def test_totals_recompute_margin_and_win_rate_from_sums():
    t = mom.totals(PRIOR.values())
    assert t["GP"] == 170_000.0 and t["VOLUME"] == 7_500.0
    assert t["MARGIN"] == pytest.approx(170_000 / 7_500)  # 22.666..., never an average of margins
    assert t["NUM_WON"] == 17.0 and t["NUM_INQUIRIES"] == 34.0 and t["NUM_LOST"] == 17.0
    assert t["WIN_RATE"] == 0.5


def test_totals_treat_none_cells_as_zero_and_empty_volume_as_zero_margin():
    t = mom.totals([{"GP": None, "VOLUME": None}])
    assert t["GP"] == 0.0 and t["MARGIN"] == 0.0 and t["WIN_RATE"] == 0.0


def test_compute_kpis_matches_the_hand_derivation():
    k = mom.compute_kpis(PRIOR.values(), CURRENT.values())
    assert list(k) == list(mom.KPI_METRICS)
    # VOLUME 7,500 -> 7,500: flat.
    assert k["VOLUME"] == mom.Delta(7_500.0, 7_500.0, 0.0, 0.0)
    # GP 170,000 -> 165,000: -5,000, -5,000/170,000 = -2.94...%.
    assert k["GP"].change == -5_000.0
    assert k["GP"].pct_change == pytest.approx(-2.9411764705)
    # MARGIN 22.67 -> 22.00.
    assert k["MARGIN"].prior == pytest.approx(22.6666666667)
    assert k["MARGIN"].current == 22.0
    # NUM_WON 17 -> 18 (+1, +5.88%); NUM_INQUIRIES 34 -> 34 (flat); NUM_LOST 17 -> 16.
    assert k["NUM_WON"].change == 1.0 and k["NUM_WON"].pct_change == pytest.approx(5.8823529412)
    assert k["NUM_INQUIRIES"].change == 0.0
    assert k["NUM_LOST"].change == -1.0
    # WIN_RATE 17/34 = 0.5 -> 18/34 = 0.5294...
    assert k["WIN_RATE"].prior == 0.5
    assert k["WIN_RATE"].current == pytest.approx(0.5294117647)


def test_merge_breakdowns_outer_joins_zero_fills_and_sorts_by_key():
    merged = {row.key: row for row in mom.merge_breakdowns(PRIOR, CURRENT)}
    assert [r.key for r in mom.merge_breakdowns(PRIOR, CURRENT)] == ["Alpha", "Beta", "Delta", "Gamma"]
    alpha = merged["Alpha"]
    assert alpha.status == "both"
    assert alpha.metrics["GP"] == mom.Delta(100_000.0, 90_000.0, -10_000.0, -10.0)
    assert alpha.metrics["VOLUME"] == mom.Delta(4_000.0, 4_500.0, 500.0, 12.5)
    assert alpha.metrics["MARGIN"] == mom.Delta(25.0, 20.0, -5.0, -20.0)
    assert alpha.metrics["NUM_WON"] == mom.Delta(10.0, 12.0, 2.0, 20.0)
    gamma = merged["Gamma"]
    assert gamma.status == "lost"
    assert gamma.metrics["GP"] == mom.Delta(20_000.0, 0.0, -20_000.0, -100.0)
    assert gamma.metrics["MARGIN"].current == 0.0
    delta = merged["Delta"]
    assert delta.status == "new"
    assert delta.metrics["GP"] == mom.Delta(0.0, 10_000.0, 10_000.0, None)  # zero-base rule
    assert delta.metrics["MARGIN"] == mom.Delta(0.0, 20.0, 20.0, None)


def test_driver_rule_verbatim_from_mom_comparison():
    merged = {row.key: row for row in mom.merge_breakdowns(PRIOR, CURRENT)}
    # Alpha: vol_effect = 500 * 25 = 12,500; margin_effect = -5 * 4,500 = -22,500;
    # ratio = 12,500 / 35,000 = 0.357 -> both.
    assert mom.driver(merged["Alpha"]) == "both"
    # Beta: volume unchanged -> vol_effect 0; margin_effect = 6 * 2,500 -> margin-driven.
    assert mom.driver(merged["Beta"]) == "margin-driven"
    assert mom.driver(merged["Gamma"]) == "lost"
    assert mom.driver(merged["Delta"]) == "new"


def _row(gp_a, vol_a, gp_b, vol_b):
    return mom.merge_breakdowns({"X": {"GP": gp_a, "VOLUME": vol_a}}, {"X": {"GP": gp_b, "VOLUME": vol_b}})[0]


def test_driver_boundaries_at_0_65_and_0_35_are_both():
    # m_a = 1 in every case so vol_effect = dvol; margin_effect = dmargin * vol_b.
    assert mom.driver(_row(35, 35, 135, 100)) == "both"             # 65 vs 35 -> ratio 0.65
    assert mom.driver(_row(34, 34, 134, 100)) == "volume-driven"    # 66 vs 34 -> 0.66
    assert mom.driver(_row(65, 65, 165, 100)) == "both"             # 35 vs 65 -> 0.35
    assert mom.driver(_row(66, 66, 166, 100)) == "margin-driven"    # 34 vs 66 -> 0.34
    assert mom.driver(_row(50, 10, 50, 10)) == "unchanged"


def test_zero_prior_margin_yields_a_null_pct_and_a_margin_driver():
    """Review Focus 3: prior volume present, prior GP zero."""
    row = _row(0.0, 100.0, 50.0, 100.0)
    assert row.status == "both"
    assert row.metrics["GP"].pct_change is None
    assert row.metrics["MARGIN"] == mom.Delta(0.0, 0.5, 0.5, None)
    assert mom.driver(row) == "margin-driven"
    assert mom.display(row.metrics["GP"], "money")["pct"] == "new"


def test_top_movers_by_gp_change_with_new_and_lost_taking_part():
    movers = mom.top_movers(mom.merge_breakdowns(PRIOR, CURRENT), n=5)
    assert [(m.row.key, m.driver) for m in movers.increases] == [("Beta", "margin-driven"), ("Delta", "new")]
    assert [(m.row.key, m.driver) for m in movers.decreases] == [("Gamma", "lost"), ("Alpha", "both")]
    assert [m.row.key for m in mom.top_movers(mom.merge_breakdowns(PRIOR, CURRENT), n=1).decreases] == ["Gamma"]


def test_new_lost_sorted_by_absolute_gp():
    nl = mom.new_lost(mom.merge_breakdowns(PRIOR, CURRENT))
    assert [r.key for r in nl.new] == ["Delta"]
    assert [r.key for r in nl.lost] == ["Gamma"]


def test_ranked_carries_an_other_row_and_a_footer_equal_to_the_kpi():
    merged = mom.merge_breakdowns(PRIOR, CURRENT)
    r = mom.ranked(merged, by="GP", n=2, noun="customers")
    assert [row.key for row in r.rows] == ["Alpha", "Beta"]
    assert r.other is not None and r.other.key == "Other (2 customers)"
    assert r.other.metrics["GP"] == mom.Delta(20_000.0, 10_000.0, -10_000.0, -50.0)
    assert r.other.metrics["VOLUME"] == mom.Delta(1_000.0, 500.0, -500.0, -50.0)
    assert r.other.metrics["MARGIN"] == mom.Delta(20.0, 20.0, 0.0, 0.0)
    assert r.footer.key == "Total"
    kpis = mom.compute_kpis(PRIOR.values(), CURRENT.values())
    assert r.footer.metrics["GP"] == kpis["GP"]
    assert r.footer.metrics["VOLUME"] == kpis["VOLUME"]
    assert r.footer.metrics["MARGIN"] == kpis["MARGIN"]


def test_ranked_with_everything_shown_has_no_other_row():
    r = mom.ranked(mom.merge_breakdowns(PRIOR, CURRENT), by="VOLUME", n=10)
    assert r.other is None
    assert [row.key for row in r.rows] == ["Alpha", "Beta", "Delta", "Gamma"]


def test_ranked_rejects_an_unknown_metric():
    with pytest.raises(ValueError, match="by must be one of"):
        mom.ranked(mom.merge_breakdowns(PRIOR, CURRENT), by="NUM_WON")


def test_footer_equals_kpi_exactly_for_random_books():
    """Property: for any book, every ranked table's footer equals the KPI
    built from the same rows, by exact equality (same totals() call)."""
    rng = Random(1391)
    for _ in range(50):
        keys = [f"E{i}" for i in range(rng.randint(1, 12))]
        prior = {k: {"GP": rng.choice([0.0, rng.uniform(-5e4, 2e5)]), "VOLUME": rng.choice([0.0, rng.uniform(0, 9e3)])} for k in keys if rng.random() > 0.2}
        current = {k: {"GP": rng.choice([0.0, rng.uniform(-5e4, 2e5)]), "VOLUME": rng.choice([0.0, rng.uniform(0, 9e3)])} for k in keys if rng.random() > 0.2}
        merged = mom.merge_breakdowns(prior, current)
        kpis = mom.compute_kpis(prior.values(), current.values())
        for by in ("GP", "VOLUME"):
            r = mom.ranked(merged, by=by, n=3)
            assert r.footer.metrics["GP"] == kpis["GP"]
            assert r.footer.metrics["VOLUME"] == kpis["VOLUME"]
            shown = sum(row.metrics["GP"].current for row in r.rows)
            other = r.other.metrics["GP"].current if r.other else 0.0
            assert mom.reconciles(shown + other, r.footer.metrics["GP"].current)


def test_share_and_reconciles():
    assert mom.share(20.0, 100.0) == 20.0
    assert mom.share(5.0, 0.0) is None
    assert mom.reconciles(100.004, 100.0) and not mom.reconciles(100.005, 100.0)


def test_display_strings_follow_the_mom_comparison_rounding_rules():
    assert mom.fmt_value(1_182_900.4, "money") == "1,182,900"
    assert mom.fmt_value(44_210.0, "tons") == "44,210"
    assert mom.fmt_value(26.756, "margin") == "26.76"
    assert mom.fmt_value(298.0, "count") == "298"
    assert mom.fmt_value(0.5612, "rate") == "56.1%"
    assert mom.fmt_change(2_350.0, "tons") == "+2,350"
    assert mom.fmt_change(-53_500.0, "money") == "-53,500"
    assert mom.fmt_change(0.0, "money") == "flat"
    assert mom.fmt_change(-2.78, "margin") == "-2.78"
    assert mom.fmt_change(-0.053, "rate") == "-5.3 pts"
    assert mom.fmt_pct(5.6, 2_350.0) == "+5.6%"
    assert mom.fmt_pct(-4.3, -53_500.0) == "-4.3%"
    assert mom.fmt_pct(0.0, 0.0) == "flat"
    assert mom.fmt_pct(None, 10_000.0) == "new"
    assert mom.fmt_pct(None, 0.0) == "flat"  # zero to zero is flat, never "new"
    assert mom.display(mom.Delta(1_236_400.0, 1_182_900.0, -53_500.0, -4.3273), "money") == {
        "prior": "1,236,400",
        "current": "1,182,900",
        "change": "-53,500",
        "pct": "-4.3%",
    }


def test_module_is_ascii_on_disk():
    from pathlib import Path

    import poseidon.core.analytics.mom as module

    Path(module.__file__).read_bytes().decode("ascii")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_analytics_mom.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t3-red.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t3-red.txt
```

Expected: `ModuleNotFoundError: No module named 'poseidon.core.analytics'`.

- [ ] **Step 3: Implement `mom.py`**

Write the module to the Interfaces contract. Implementation notes:
- A private `_sum_rows(rows, keys) -> dict[str, float]` and a private `_derived(sums) -> dict` (MARGIN, WIN_RATE) feed `totals`, `other` and `footer` so the same arithmetic produces the KPI and the footer. `_sum_rows` uses `math.fsum`, never a running `+`: the footer sums merged rows in key order while the KPI sums the same rows in input order, and only an order-independent (correctly rounded) sum makes `test_footer_equals_kpi_exactly_for_random_books` exact rather than flaky.
- `merge_breakdowns` builds per-side sums with the union of `SUM_METRICS` keys present in either input (always GP and VOLUME, zero-filled), then `Delta` per metric via `delta()`.
- The module docstring maps the spec's `current`/`prior` vocabulary to mom-comparison's `a = prior`, `b = current`, and says why pct is `None` at a zero base.
- Run `ruff format poseidon/core/analytics` (new files only).

- [ ] **Step 4: Run to verify it passes**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_analytics_mom.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t3-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t3-green.txt
```

Expected: all 17 tests pass. In the task report, show the hand derivation for Alpha's driver ratio and the `Other (2 customers)` row.

- [ ] **Step 5: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/analytics tests/test_analytics_mom.py
cd ..
git add backend/poseidon/core/analytics backend/tests/test_analytics_mom.py
git commit -q -F - <<'EOF'
feat(analytics): month-over-month math ported from mom-comparison, one home

compute_kpis, merge_breakdowns, driver, top_movers, new_lost, ranked (with
the D52 Other row and a footer built by the same totals() as the KPI),
share and the display formatting. Zero-base rule: a zero prior gives a null
pct rendered "new"; lost rows are "lost", never volume-driven. Hand-derived
expectations and a 50-book footer-equals-tile property.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 4: Periods — dedupe the parser, month-to-date labels, `resolve_periods`

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Modify: `backend/poseidon/core/parsing/period_labels.py` (add `days_in_month`, `label_month_to_date`; export)
- Modify: `backend/poseidon/core/parsing/period_parser.py:195-199, 247-251` (delete `_KIND_MONTH/_QUARTER/_YEAR/_YTD` and `_month_window`; import `KIND_MONTH, KIND_QUARTER, KIND_YEAR, KIND_YTD, KIND_RANGE, month_window` from `period_labels` and rename uses; `_KIND_RANGE` goes too)
- Create (empty): `backend/poseidon/tasks/reporting/__init__.py`, `backend/poseidon/tasks/reporting/skills/__init__.py`, `.../skills/monthly_performance/__init__.py`, `.../monthly_performance/tools/__init__.py`, `.../monthly_performance/tests/__init__.py`. **No `task.yml` yet** (Task 9 adds it) so the registry does not see the package.
- Create: `backend/poseidon/tasks/reporting/skills/monthly_performance/tools/periods.py`
- Test: `backend/tests/test_period_labels.py`, `backend/tests/test_parsing_periods.py`, `.../monthly_performance/tests/test_periods.py` (new)

**Interfaces:**
- Consumes: `PeriodWindow`, `period_labels.month_window`, `label_window`, `_add_months`.
- Produces:
  ```python
  # period_labels.py
  def days_in_month(year: int, month: int) -> int
  def label_month_to_date(window: PeriodWindow) -> str      # f"{label_window(window)} (month to date)"

  # tools/periods.py
  Basis = Literal["prior_month"]
  WindowKind = Literal["complete", "mtd"]

  class PeriodError(ValueError):
      """Carries the 422 detail the orchestrator shows the user."""

  @dataclass(frozen=True)
  class ResolvedPeriods:
      current: PeriodWindow
      prior: PeriodWindow
      label_current: str
      label_prior: str
      window_kind: WindowKind

  def resolve_periods(
      basis: Basis, month: date, window_kind: WindowKind, reference_date: date
  ) -> ResolvedPeriods
  ```
  Rules (spec §5.2, pure, no data access; the "present in the data" check is the skill's, Task 9):
  - `month.day != 1` → `PeriodError("month must be the first day of a month")`.
  - `basis != "prior_month"` → `PeriodError(f"unknown basis {basis!r}")`.
  - `complete`: `current = month_window(month.year, month.month)`; if `current.end > reference_date` → `PeriodError("that month is not complete yet")`; `prior` = the calendar month before; labels `label_window(current)`, `label_window(prior)` (so "September 2026" and "August 2026").
  - `mtd`: if `month != reference_date.replace(day=1)` → `PeriodError("month to date applies to the running month only")`; `n = reference_date.day - 1`; `n == 0` → `PeriodError("no complete days in this month yet")`; `current = [month, month + n days)`; `prior_first` = first of the prior month; `prior = [prior_first, min(prior_first + n days, month))`; labels `label_month_to_date(current)` and `label_window(prior)` (so "October 1 to 11, 2026 (month to date)" and "September 1 to 11, 2026").

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_period_labels.py`:

```python
def test_label_month_to_date_appends_the_words_once():
    window = PeriodWindow(date(2026, 10, 1), date(2026, 10, 12))
    assert label_month_to_date(window) == "October 1 to 11, 2026 (month to date)"


def test_days_in_month_knows_leap_years():
    assert days_in_month(2028, 2) == 29 and days_in_month(2027, 2) == 28
```

Append to `backend/tests/test_parsing_periods.py` (next to the existing year-shift rows; use that file's row style and helpers):

```python
def test_period_parser_reuses_period_labels_instead_of_private_copies():
    import poseidon.core.parsing.period_parser as parser

    assert not hasattr(parser, "_month_window")
    assert not hasattr(parser, "_KIND_MONTH")
    assert parser.month_window is month_window  # the one from period_labels
```

And one more row in the table-driven year-shift tests: carried A = `PeriodWindow(date(2028, 2, 29), date(2028, 3, 5))`, text `"vs last year"`, expected B = `PeriodWindow(date(2027, 2, 28), date(2027, 3, 5))` (the clamp rule stands; ruling 4). Name it `vs_last_year_on_a_range_starting_29_feb_clamps_the_start`.

Create `backend/poseidon/tasks/reporting/skills/monthly_performance/tests/test_periods.py`:

```python
from datetime import date

import pytest

from poseidon.core.data.specs import PeriodWindow
from poseidon.tasks.reporting.skills.monthly_performance.tools.periods import (
    PeriodError,
    resolve_periods,
)

OCT_12 = date(2026, 10, 12)


def test_complete_month_labels_both_months_by_name():
    r = resolve_periods("prior_month", date(2026, 9, 1), "complete", OCT_12)
    assert r.current == PeriodWindow(date(2026, 9, 1), date(2026, 10, 1))
    assert r.prior == PeriodWindow(date(2026, 8, 1), date(2026, 9, 1))
    assert (r.label_current, r.label_prior) == ("September 2026", "August 2026")
    assert r.window_kind == "complete"


def test_january_prior_is_december_of_the_year_before():
    r = resolve_periods("prior_month", date(2026, 1, 1), "complete", OCT_12)
    assert r.prior == PeriodWindow(date(2025, 12, 1), date(2026, 1, 1))
    assert r.label_prior == "December 2025"


def test_running_month_as_complete_is_refused():
    with pytest.raises(PeriodError, match="not complete yet"):
        resolve_periods("prior_month", date(2026, 10, 1), "complete", OCT_12)


def test_mtd_builds_day_matched_windows_and_names_them():
    r = resolve_periods("prior_month", date(2026, 10, 1), "mtd", OCT_12)
    assert r.current == PeriodWindow(date(2026, 10, 1), date(2026, 10, 12))
    assert r.prior == PeriodWindow(date(2026, 9, 1), date(2026, 9, 12))
    assert r.label_current == "October 1 to 11, 2026 (month to date)"
    assert r.label_prior == "September 1 to 11, 2026"
    assert r.window_kind == "mtd"


def test_mtd_prior_is_clamped_to_the_prior_month_length():
    r = resolve_periods("prior_month", date(2026, 3, 1), "mtd", date(2026, 3, 31))
    assert r.current == PeriodWindow(date(2026, 3, 1), date(2026, 3, 31))
    assert r.prior == PeriodWindow(date(2026, 2, 1), date(2026, 3, 1))
    assert r.label_prior == "February 1 to 28, 2026"


def test_mtd_on_the_first_of_the_month_refuses():
    with pytest.raises(PeriodError, match="no complete days"):
        resolve_periods("prior_month", date(2026, 10, 1), "mtd", date(2026, 10, 1))


def test_mtd_for_a_month_that_is_not_running_is_refused():
    with pytest.raises(PeriodError, match="running month only"):
        resolve_periods("prior_month", date(2026, 9, 1), "mtd", OCT_12)


def test_month_must_be_a_first_day():
    with pytest.raises(PeriodError, match="first day of a month"):
        resolve_periods("prior_month", date(2026, 9, 15), "complete", OCT_12)
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_period_labels.py tests/test_parsing_periods.py poseidon/tasks/reporting > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t4-red.txt 2>&1; tail -8 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t4-red.txt
```

Expected: `ImportError` on `label_month_to_date`, the parser dedupe test fails on `hasattr`, and the periods module import fails.

- [ ] **Step 3: Implement**

`period_labels.py`: add the two functions (use `calendar.monthrange`), keep ASCII, extend `__all__`.
`period_parser.py`: replace the private copies with imports; every `_KIND_X` use becomes `KIND_X`; `_month_window(...)` becomes `month_window(...)`; the `_Resolved.kind` values are unchanged strings. Re-run the whole parser module to prove no behaviour moved.
`tools/periods.py`: implement to the rules above with `timedelta(days=n)` arithmetic and `period_labels._add_months` for the prior month's first day (import it as a public helper: add `add_months` to `period_labels.__all__` as a thin public alias and use that).

- [ ] **Step 4: Run to verify they pass**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_period_labels.py tests/test_parsing_periods.py tests/test_c03_period_carry.py tests/test_llm_prompts.py poseidon/tasks/reporting > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t4-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t4-green.txt
```

Expected: all green, including the untouched C03 acceptance and prompt goldens.

- [ ] **Step 5: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/parsing poseidon/tasks/reporting tests/test_period_labels.py tests/test_parsing_periods.py
cd ..
git add backend/poseidon/core/parsing backend/poseidon/tasks/reporting backend/tests/test_period_labels.py backend/tests/test_parsing_periods.py
git commit -q -F - <<'EOF'
feat(periods): resolve_periods for the report; parser reuses period_labels

The period parser drops its private month_window and KIND_* copies (PR 0
carry-forward). period_labels gains label_month_to_date, the one place
"month to date" is appended. resolve_periods is the only function that
knows what a basis means: complete months label by name, month to date
builds day-matched windows with the prior clamped to its month length and
refuses day one of the month (D64).

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 5: `report_run` — Drizzle migration `0003` and the Python store

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Modify: `web/src/db/schema.ts` (new `reportRun` table after `conversations`; widen the `ck_conversations_mode` check at line 128 to include `'report'::text`)
- Create (generated, then hand-appended): `web/drizzle/0003_report_run.sql`, `web/drizzle/meta/0003_snapshot.json`; `web/drizzle/meta/_journal.json` gains entry 3
- Modify: `web/src/db/migrations.test.ts` (four filenames; new assertions), `web/src/db/schema.test.ts` (report_run is not an RLS table)
- Create: `backend/poseidon/reports/__init__.py`, `backend/poseidon/reports/store.py`
- Test: `backend/tests/test_report_store.py` (new, pg)

**Interfaces:**
- Consumes: `rls_transaction`, `build_engine`, `PeriodWindow`.
- Produces:
  ```python
  @dataclass(frozen=True)
  class NewRun:
      id: str                       # uuid7 string, minted by the skill
      perspective: str              # customer | supplier | book
      office_column: str | None
      office_value: str | None
      period_current: PeriodWindow
      period_prior: PeriodWindow
      window_kind: str              # complete | mtd
      basis: str                    # prior_month
      status: str                   # ok | error
      payload_version: int
      payload: dict
      html: bytes | None
      pdf: bytes | None
      narrative_grounded: bool
      source: str                   # synthetic | snowflake
      created_by: str               # user sub
      conversation_id: str | None
      generated_at: datetime        # tz-aware

  @dataclass(frozen=True)
  class ReportRun:                  # what get_run returns; no bytes
      id, definition_id, perspective, office_column, office_value, period_current, period_prior,
      window_kind, basis, status, payload_version, payload, narrative_grounded, source,
      created_by, conversation_id, superseded_by, generated_at, created_at

  class ReportStore:
      def __init__(self, engine: Engine, app_role: str | None = None) -> None
      def insert_run(self, user_sub: str, run: NewRun) -> None
      def get_run(self, user_sub: str, run_id: str) -> ReportRun | None
      def get_bytes(self, user_sub: str, run_id: str, kind: Literal["html", "pdf"]) -> bytes | None
  ```
  Every method runs inside `rls_transaction(engine, user_sub, app_role=app_role)` (the identity is set even though the table has no row policy, so the audit path and `SET LOCAL ROLE` behave like every other store). A non-UUID `run_id` returns `None` without a query (copy `history._parse_uuid`). `payload` is written with `CAST(:payload AS jsonb)` from `json.dumps`. `html`/`pdf` bind as `bytea` through psycopg with no cast.

  The SQL table is exactly spec §7.1. In `schema.ts`:
  ```ts
  const bytea = customType<{ data: Buffer }>({ dataType() { return "bytea"; } });

  export const reportRun = pgTable("report_run", {
  	id: uuid().primaryKey().notNull(),
  	definitionId: uuid("definition_id"),
  	perspective: text().notNull(),
  	officeColumn: text("office_column"),
  	officeValue: text("office_value"),
  	periodCurrentStart: date("period_current_start").notNull(),
  	periodCurrentEnd: date("period_current_end").notNull(),
  	periodPriorStart: date("period_prior_start").notNull(),
  	periodPriorEnd: date("period_prior_end").notNull(),
  	windowKind: text("window_kind").notNull(),
  	basis: text().default('prior_month').notNull(),
  	status: text().notNull(),
  	payloadVersion: integer("payload_version").notNull(),
  	payload: jsonb().notNull(),
  	html: bytea(),
  	pdf: bytea(),
  	narrativeGrounded: boolean("narrative_grounded").notNull(),
  	source: text().notNull(),
  	createdBy: text("created_by").notNull(),
  	conversationId: uuid("conversation_id"),
  	supersededBy: uuid("superseded_by"),
  	generatedAt: timestamp("generated_at", { withTimezone: true, mode: 'string' }).notNull(),
  	createdAt: timestamp("created_at", { withTimezone: true, mode: 'string' }).defaultNow().notNull(),
  }, (table) => [
  	check("ck_report_run_perspective", sql`perspective = ANY (ARRAY['customer'::text, 'supplier'::text, 'book'::text])`),
  	check("ck_report_run_office_column", sql`office_column IS NULL OR office_column = ANY (ARRAY['CUSTOMER_TEAM_NAME'::text, 'PRIMARY_SUPPLY_TEAM_OFFICE'::text])`),
  	check("ck_report_run_window_kind", sql`window_kind = ANY (ARRAY['complete'::text, 'mtd'::text])`),
  	check("ck_report_run_status", sql`status = ANY (ARRAY['ok'::text, 'error'::text])`),
  ]);
  ```
  No `.enableRLS()`, no policy (D37, D69). Import `date` and `customType` from `drizzle-orm/pg-core`. `definition_id` and `superseded_by` are plain nullable uuids this phase (Phase 18 adds the FKs).

- [ ] **Step 1: Write the failing web tests**

In `web/src/db/migrations.test.ts`, change the first test's expected list to `["0000_roles.sql", "0001_baseline.sql", "0002_grants_force_rls.sql", "0003_report_run.sql"]` and rename it "contains the four expected migrations and a journal". Add:

```ts
  it("0003 creates report_run as a shared org document and widens the mode check", () => {
    const sql = readFileSync(join(drizzleDir, "0003_report_run.sql"), "utf8");
    expect(sql).toContain('CREATE TABLE "report_run"');
    expect(sql).toContain("GRANT SELECT, INSERT ON report_run TO poseidon_app;");
    expect(sql).not.toMatch(/report_run[\s\S]*ENABLE ROW LEVEL SECURITY/);
    expect(sql).not.toContain("CREATE POLICY");
    expect(sql).toMatch(/ck_conversations_mode[\s\S]*'report'::text/);
  });
```

In `web/src/db/schema.test.ts` add:

```ts
  it("report_run is a shared org document: no RLS, no policies, four checks", () => {
    const table = findTable("report_run");
    expect(table).toBeDefined();
    const cfg = getTableConfig(table as never);
    expect(cfg.enableRLS).toBe(false);
    expect(cfg.policies).toHaveLength(0);
    expect(cfg.checks.map((c) => c.name).sort()).toEqual([
      "ck_report_run_office_column",
      "ck_report_run_perspective",
      "ck_report_run_status",
      "ck_report_run_window_kind",
    ]);
  });
```

(Keep `RLS_TABLES` at nine; `report_run` is deliberately not in it.)

- [ ] **Step 2: Run the web tests to verify they fail**

```bash
cd ../web
npm test -- --run src/db > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-red-web.txt 2>&1; tail -8 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-red-web.txt
```

Expected: the filename list and the two new tests fail (no 0003, no `report_run` table).

- [ ] **Step 3: Edit `schema.ts`, generate the migration, append the grant, apply it**

1. Add `reportRun` and widen the conversations check to `mode = ANY (ARRAY['existing'::text, 'prospect'::text, 'default'::text, 'report'::text])`.
2. Generate (bash):
   ```bash
   DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon npx drizzle-kit generate --name report_run > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-generate.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-generate.txt
   ```
   Expected: `web/drizzle/0003_report_run.sql`, `meta/0003_snapshot.json`, and a new `_journal.json` entry. Open the SQL. It must contain `CREATE TABLE "report_run"` and the dropped-and-re-added `ck_conversations_mode`. If drizzle-kit did not emit the check change, append it by hand after the table:
   ```sql
   --> statement-breakpoint
   ALTER TABLE "conversations" DROP CONSTRAINT "ck_conversations_mode";
   --> statement-breakpoint
   ALTER TABLE "conversations" ADD CONSTRAINT "ck_conversations_mode" CHECK (mode = ANY (ARRAY['existing'::text, 'prospect'::text, 'default'::text, 'report'::text]));
   ```
3. Append to the end of `0003_report_run.sql`:
   ```sql
   --> statement-breakpoint
   -- Phase 17 D37/D65/D69: a shared org document. No ENABLE/FORCE ROW LEVEL SECURITY, no policy.
   -- Reads by any Poseidon:Sales user; writes by the app role on behalf of the generating user.
   -- No UPDATE this phase (Phase 18 adds superseded_by writes with its own grant).
   GRANT SELECT, INSERT ON report_run TO poseidon_app;
   ```
4. Apply to the compose database:
   ```bash
   DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon npx drizzle-kit migrate > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-migrate.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-migrate.txt
   ```
   Expected: one migration applied. Verify with `docker exec infra-db-1 psql -U poseidon -d poseidon -c '\d report_run'` and confirm the grant with `-c "SELECT has_table_privilege('poseidon_app', 'report_run', 'INSERT')"` (expects `t`).

- [ ] **Step 4: Run the web tests to verify they pass**

```bash
npm test -- --run src/db > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-green-web.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-green-web.txt
npx tsc --noEmit > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-tsc.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-tsc.txt
```

Expected: `src/db` tests green including "drizzle-kit generate against the committed schema produces nothing new"; tsc clean.

- [ ] **Step 5: Write the failing Python store tests**

Create `backend/tests/test_report_store.py` (copy the `pg_engine` and `effective_app_role` fixtures verbatim from `tests/test_history_store.py:450-500`, changing the `to_regclass` probe to `'public.report_run'` and the hint to "migrate it with `cd web && npx drizzle-kit migrate` (0003)"):

```python
import uuid
from datetime import UTC, date, datetime

import pytest

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.util.uuid7 import uuid7
from poseidon.reports.store import NewRun, ReportStore

# ... fixtures copied from test_history_store.py ...


def _run(**overrides) -> NewRun:
    base = dict(
        id=str(uuid7()),
        perspective="customer",
        office_column="CUSTOMER_TEAM_NAME",
        office_value="Rotterdam",
        period_current=PeriodWindow(date(2026, 6, 1), date(2026, 7, 1)),
        period_prior=PeriodWindow(date(2026, 5, 1), date(2026, 6, 1)),
        window_kind="complete",
        basis="prior_month",
        status="ok",
        payload_version=1,
        payload={"payload_version": 1, "kpis": {"GP": {"current": 1.5}}},
        html=b"<html>x</html>",
        pdf=b"%PDF-1.7 fake",
        narrative_grounded=True,
        source="synthetic",
        created_by="test|alice",
        conversation_id=None,
        generated_at=datetime(2026, 10, 12, 8, 14, tzinfo=UTC),
    )
    base.update(overrides)
    return NewRun(**base)


@pytest.mark.pg
def test_insert_then_get_round_trips_every_column(pg_engine, effective_app_role):
    store = ReportStore(pg_engine, app_role=effective_app_role)
    run = _run()
    store.insert_run("test|alice", run)

    got = store.get_run("test|alice", run.id)

    assert got is not None
    assert got.id == run.id and got.perspective == "customer"
    assert got.office_column == "CUSTOMER_TEAM_NAME" and got.office_value == "Rotterdam"
    assert got.period_current == run.period_current and got.period_prior == run.period_prior
    assert got.window_kind == "complete" and got.basis == "prior_month" and got.status == "ok"
    assert got.payload == run.payload and got.payload_version == 1
    assert got.narrative_grounded is True and got.source == "synthetic"
    assert got.created_by == "test|alice" and got.conversation_id is None
    assert got.superseded_by is None and got.definition_id is None
    assert got.generated_at == run.generated_at
    assert got.created_at is not None


@pytest.mark.pg
def test_bytes_come_back_by_kind_and_are_absent_from_get_run(pg_engine, effective_app_role):
    store = ReportStore(pg_engine, app_role=effective_app_role)
    run = _run()
    store.insert_run("test|alice", run)

    assert store.get_bytes("test|alice", run.id, "html") == b"<html>x</html>"
    assert store.get_bytes("test|alice", run.id, "pdf") == b"%PDF-1.7 fake"
    assert not hasattr(store.get_run("test|alice", run.id), "pdf")


@pytest.mark.pg
def test_a_run_is_readable_by_another_sales_user(pg_engine, effective_app_role):
    """D69: shared org document, no row policy."""
    store = ReportStore(pg_engine, app_role=effective_app_role)
    run = _run()
    store.insert_run("test|alice", run)

    assert store.get_run(f"test|{uuid.uuid4().hex}", run.id) is not None


@pytest.mark.pg
def test_unknown_or_malformed_ids_return_none(pg_engine, effective_app_role):
    store = ReportStore(pg_engine, app_role=effective_app_role)
    assert store.get_run("test|alice", str(uuid.uuid4())) is None
    assert store.get_run("test|alice", "not-a-uuid") is None
    assert store.get_bytes("test|alice", str(uuid.uuid4()), "pdf") is None


@pytest.mark.pg
def test_a_run_with_no_bytes_stores_nulls(pg_engine, effective_app_role):
    store = ReportStore(pg_engine, app_role=effective_app_role)
    run = _run(html=None, pdf=None, status="error", narrative_grounded=False)
    store.insert_run("test|alice", run)
    assert store.get_bytes("test|alice", run.id, "pdf") is None
    assert store.get_run("test|alice", run.id).status == "error"
```

- [ ] **Step 6: Run to verify they fail, implement, run to verify they pass**

```bash
cd ../backend
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_report_store.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-red.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-red.txt
```

Expected: `ModuleNotFoundError: No module named 'poseidon.reports'`.

Implement `store.py` with three `text(...)` constants (`_INSERT_RUN_SQL`, `_SELECT_RUN_SQL` listing every non-bytes column, `_SELECT_BYTES_SQL` built per kind from a fixed `{"html": "html", "pdf": "pdf"}` map, never from the caller's string), the two dataclasses, `_parse_uuid`, and the row-to-`ReportRun` mapping (dates come back as `date`, payload as `dict`, timestamps tz-aware). `__init__.py` docstring: "Report storage (Phase 17 D65). Drizzle owns the schema; this package only reads and writes." Run `ruff format poseidon/reports` (new files).

```bash
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_report_store.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t5-green.txt
```

Expected: 5 passed.

- [ ] **Step 7: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/reports tests/test_report_store.py
cd ..
git add web/src/db/schema.ts web/drizzle/0003_report_run.sql web/drizzle/meta/0003_snapshot.json web/drizzle/meta/_journal.json web/src/db/migrations.test.ts web/src/db/schema.test.ts backend/poseidon/reports backend/tests/test_report_store.py
git commit -q -F - <<'EOF'
feat(db): report_run as Drizzle 0003, shared org document; Python ReportStore

The table is shaped for Phase 18 (nullable definition_id and superseded_by),
has no row policy (D37, D69) and grants SELECT, INSERT to poseidon_app. The
same migration widens ck_conversations_mode to allow 'report'. Python reads
and writes it with plain SQL under rls_transaction and migrates nothing
(D58). pg tests pin the round trip, the cross-user read and the null bytes.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 6: Rendering — `report.html.j2`, `render_html`, `render_pdf`

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Create: `backend/poseidon/config/templates/report.html.j2`
- Create: `backend/poseidon/tasks/reporting/skills/monthly_performance/tools/render.py`
- Create: `.../monthly_performance/tests/payload_fixture.py` (the `minimal_payload()` builder every later test reuses)
- Test: `.../monthly_performance/tests/test_render.py` (new)

**Interfaces:**
- Consumes: the `markdown` and `jinja2` libraries already in `pyproject.toml`; WeasyPrint lazily.
- Produces:
  ```python
  TEMPLATES_DIR = Path(poseidon.__file__).resolve().parent / "config" / "templates"
  def render_html(payload: Mapping[str, Any]) -> str
  def refuse_fetch(url: str, *args: object, **kwargs: object) -> NoReturn   # raises RuntimeError(f"report renderer refuses to fetch {url}")
  def render_pdf(html: str) -> bytes                                        # lazy `from weasyprint import HTML`; HTML(string=html, url_fetcher=refuse_fetch).write_pdf()
  def narrative_html(markdown_text: str) -> Markup                          # Markup(markdown.markdown(html.escape(markdown_text), output_format="html5"))
  ```
  The Jinja environment: `FileSystemLoader(TEMPLATES_DIR)`, `autoescape=True`, `undefined=StrictUndefined`, `auto_reload=False`, filter `narrative_html` registered. Module-level, built once.

  **The payload v1 contract** (spec §5.7 made exact; Task 9 produces it, Task 8 reads it, this template renders it):
  ```
  payload_version: 1
  run_id: str
  perspective: "customer" | "supplier" | "book"
  entity: {column: "CUST_NM" | "SUPPLIER_NM", label: "Customer" | "Supplier"}
  scope: {office_column: str | null, office_value: str | null, label: str}
         # label "Rotterdam (Customer Broker Office)" | "Rotterdam (Supply Team Office)" | "Whole book"
  periods: {current: {start, end, label}, prior: {start, end, label}, window_kind: "complete" | "mtd"}
  kpis: {<METRIC in KPI_METRICS>: DELTA}
         # DELTA = {prior: f, current: f, change: f, pct_change: f | null, display: {prior, current, change, pct}}
  tables: {entities_by_gp | entities_by_volume | ports_by_gp | ports_by_volume | deal_class:
           {rows: [ROW], other: ROW | null, footer: ROW}}
         # ROW = {key: str, status: "both"|"new"|"lost",
         #        prior: {GP, VOLUME, MARGIN}, current: {...}, change: {...}, pct: {... | null},
         #        display: {GP: {prior, current, change, pct}, VOLUME: {...}, MARGIN: {...}}}
  movers: {increases: [MOVER], decreases: [MOVER]}          # MOVER = ROW + {driver: str}
  new_lost: {new: [ROW], lost: [ROW]}
  entity_context: null | {<entity name>: {          # null for book; {} for an office with no movers
      book: {GP: DELTA, VOLUME: DELTA, MARGIN: DELTA},
      share_of_book: {prior: f | null, current: f | null, display: {prior, current}},   # "53.4%"; "n/a" for a null side
      reconciliation: {office_change: f, other_offices_net: f, book_change: f,
                       display: {office_change, other_offices_net, book_change}},      # signed money strings
      offsets: [{key: str, change: f, display: str}],                                   # up to 5
      ports_rest_of_book: [{key: str, change: f, display: str}]}}                       # up to 5
  narrative: {template_md: str, llm_md: str | null, grounded: bool, flags: [str]}
  suggested_questions: [str, str, str]               # [] only for a no-data run (spec section 10)
  proof: [str]
  queries: [{label: str, sql_hash: str, rows: int, ms: int}]
  generated_at: iso datetime, source: "synthetic" | "snowflake", generated_by: str
  ```
  `payload_fixture.minimal_payload(**overrides) -> dict` builds a complete, internally consistent payload with two customers (`Northstar Lines`, `Meridian Bunkering`), two ports, two deal classes, one mover each way, one new and one lost entity, an `entity_context` entry for `Meridian Bunkering`, a template narrative and three questions; overrides replace top-level keys.

  **Template rules:** every data string passes through autoescape; the narrative goes through `narrative_html` (escape first, then markdown, so a hostile name can never become markup and `**bold**` still renders). Sections in order: header (`<h1>Monthly performance report</h1>`, scope label, `{{ periods.current.label }} vs {{ periods.prior.label }}`, a `Month to date` badge when `window_kind == "mtd"`, generated time, source), KPIs as a `<table class="kpis">` with one row per metric in `KPI_METRICS` order (friendly labels: Volume (tons), Gross profit, Margin (per ton), # Won, # Inquiries, # Lost, Win rate) showing prior, current, change, pct, five ranked `<table>`s each with `<thead>`, `<tbody>` rows (plus the `other` row when present) and a `<tfoot>` footer row, movers (increases then decreases; each with GP/VOLUME/MARGIN display strings, the driver word, and when `entity_context[key]` exists a context block: book line, share line, the reconciliation line `office {office_change}; other offices net {other_offices_net}; book {book_change}`, offsets and rest-of-book ports), new and lost tables, the narrative (`llm_md` when `grounded and llm_md`, else `template_md`; then one `<p class="notice">` per flag), suggested questions as `<ul>`, proof as `<details>`. One `<style>` block, tables only, no `<link>`, no `<script>`, no `position`, no `flex`, no `grid` (the HTML serves Phase 18's email).

- [ ] **Step 1: Write the fixture and the failing tests**

Create `payload_fixture.py` per the contract (a plain function building dicts; no imports from the tools package, so Task 8 and 9 tests can import it before those modules exist).

Create `test_render.py`:

```python
import re

import pytest

from poseidon.tasks.reporting.skills.monthly_performance.tools import render
from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import minimal_payload

try:
    import weasyprint  # noqa: F401 - existence probe only
except Exception:  # noqa: BLE001 - missing native libs raise OSError, not ImportError
    _HAS_WEASYPRINT = False
else:
    _HAS_WEASYPRINT = True


def test_html_carries_every_section_in_order():
    html = render.render_html(minimal_payload())
    order = [
        "Monthly performance report",
        'class="kpis"',
        "Customers by GP",
        "Customers by volume",
        "Ports by GP",
        "Ports by volume",
        "Deal class",
        "Top increases",
        "Top decreases",
        "New in",
        "Lost from",
        "Summary of operations",
        "Suggested questions",
        "Proof",
    ]
    positions = [html.index(marker) for marker in order]
    assert positions == sorted(positions)


def test_every_table_footer_shows_the_kpi_display_strings():
    payload = minimal_payload()
    html = render.render_html(payload)
    gp = payload["kpis"]["GP"]["display"]
    assert html.count(f"<tfoot>") == 5
    assert gp["current"] in html and gp["prior"] in html


def test_month_to_date_is_named_in_the_header():
    payload = minimal_payload()
    payload["periods"]["window_kind"] = "mtd"
    payload["periods"]["current"]["label"] = "October 1 to 11, 2026 (month to date)"
    html = render.render_html(payload)
    assert "October 1 to 11, 2026 (month to date)" in html
    assert "Month to date" in html
    assert "Month to date" not in render.render_html(minimal_payload())


def test_hostile_entity_name_renders_as_text_and_fetches_nothing(monkeypatch):
    """Review Focus 2 and reports design 5.7."""
    payload = minimal_payload()
    hostile = '<img src="http://evil.example/x.png"> {{ 7*7 }}'
    payload["tables"]["entities_by_gp"]["rows"][0]["key"] = hostile
    payload["narrative"]["template_md"] = f"**{hostile}** led the month with 1,000."
    html = render.render_html(payload)
    assert "<img" not in html
    assert "&lt;img" in html
    assert "49" not in html and "{{" not in html

    captured = {}

    class _FakeHTML:
        def __init__(self, *, string, url_fetcher):
            captured["url_fetcher"] = url_fetcher

        def write_pdf(self):
            return b"%PDF-fake"

    import sys
    import types

    monkeypatch.setitem(sys.modules, "weasyprint", types.SimpleNamespace(HTML=_FakeHTML))
    assert render.render_pdf(html) == b"%PDF-fake"
    with pytest.raises(RuntimeError, match="refuses to fetch"):
        captured["url_fetcher"]("http://evil.example/x.png")


def test_outlook_safety_lint():
    html = render.render_html(minimal_payload())
    assert "<script" not in html
    assert "<link" not in html
    assert not re.search(r"position\s*:", html)
    assert not re.search(r"display\s*:\s*(flex|grid)", html)
    assert html.count("<style>") == 1


def test_withheld_flag_and_template_fallback_are_shown():
    payload = minimal_payload()
    payload["narrative"]["llm_md"] = "Model prose that must not show."
    payload["narrative"]["grounded"] = False
    payload["narrative"]["flags"] = ["AI narrative withheld: 3 unverified figures"]
    html = render.render_html(payload)
    assert "Model prose that must not show." not in html
    assert "AI narrative withheld: 3 unverified figures" in html
    assert payload["narrative"]["template_md"].split("\n")[0].strip("*# ") in html


def test_strict_undefined_rejects_a_payload_missing_a_section():
    payload = minimal_payload()
    del payload["suggested_questions"]
    with pytest.raises(Exception, match="suggested_questions"):
        render.render_html(payload)


@pytest.mark.pdf
@pytest.mark.skipif(not _HAS_WEASYPRINT, reason="weasyprint needs Pango/Cairo - run in the container")
def test_real_pdf_bytes():
    pdf = render.render_pdf(render.render_html(minimal_payload()))
    assert pdf.startswith(b"%PDF")
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider poseidon/tasks/reporting -k render > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t6-red.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t6-red.txt
```

Expected: `ImportError` on `render`.

- [ ] **Step 3: Implement the template and `render.py`**

Write the template to the rules above. Section headings are literal strings the tests index: "Customers by GP" etc. use `{{ entity.label }}s by GP` so a supplier report reads "Suppliers by GP" (the test fixture is a customer report). "New in {{ periods.current.label }}", "Lost from {{ periods.prior.label }}", "Summary of operations", "Suggested questions", "Proof". Run `ruff format` on the new Python files.

- [ ] **Step 4: Run to verify they pass**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider poseidon/tasks/reporting -k render > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t6-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t6-green.txt
```

Expected: 7 passed, 1 skipped (the pdf-marked test skips on the host without Pango; if WeasyPrint is importable on this host it runs and must pass).

- [ ] **Step 5: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/tasks/reporting
cd ..
git add backend/poseidon/config/templates/report.html.j2 backend/poseidon/tasks/reporting
git commit -q -F - <<'EOF'
feat(reporting): autoescaped HTML report and a fetch-refusing PDF renderer

report.html.j2 renders display strings only; the narrative is escaped
before markdown so a hostile entity name is text everywhere. WeasyPrint is
imported lazily with a url_fetcher that raises, so the renderer can never
reach the network. An Outlook-safety lint pins the HTML for Phase 18's
email. payload_fixture.minimal_payload is the v1 payload contract in code.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 7: The prompt tree and the forced tool call

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Create: `backend/prompts/reporting/narrate.xml`
- Create: `backend/poseidon/core/llm/prompt_tree.py`
- Modify: `backend/poseidon/core/llm/roles.py` (`LLMProvider.invoke` protocol and `RoleClient.invoke`, lines 64-87 and 191-240)
- Modify: `backend/poseidon/core/llm/bedrock.py` (`invoke` line 129, `_build_request` line 209, `_build_tool_config` line 280)
- Modify: `backend/poseidon/core/llm/stub.py`, `backend/poseidon/core/chat/dev_router.py:619`
- Test: `backend/tests/test_prompt_tree.py` (new), `backend/tests/test_llm_types_roles.py`, `backend/tests/test_llm_bedrock.py`

**Interfaces:**
- Produces:
  ```python
  # prompt_tree.py
  DEFAULT_PROMPT_TREE = Path(__file__).resolve().parents[3] / "prompts"     # backend/prompts

  class PromptTreeError(KeyError): ...

  @dataclass(frozen=True)
  class PromptSpec:
      id: str          # "reporting/narrate"
      version: str     # "1"
      system: str      # the <system> text, stripped

  class PromptTree:
      def __init__(self, root: Path = DEFAULT_PROMPT_TREE) -> None
      def resolve(self, prompt_id: str) -> PromptSpec     # reads <root>/<id>.xml, parses <prompt id= version=><system>

  # roles.py
  class LLMProvider(Protocol):
      def invoke(self, *, system, messages, tools, model, params, tool_choice: str | None = None) -> LLMResponse: ...
  class RoleClient:
      def invoke(self, role, *, system, messages, tools=[], tool_choice: str | None = None) -> LLMResponse
          # forwards tool_choice to the provider ONLY when it is not None
  ```
  Bedrock: when `tool_choice` is set, `request["toolConfig"]["toolChoice"] = {"tool": {"name": _to_bedrock_tool_name(tool_choice)}}`; `tool_choice` with empty `tools` raises `ValueError("tool_choice requires at least one tool")`. Stub: records `tool_choice` in each `calls` entry. `DevDeterministicRouter.invoke` accepts and ignores it.

  `narrate.xml` (the system prompt; the user message and the tool schema are Task 9's):
  ```xml
  <?xml version="1.0" encoding="utf-8"?>
  <prompt id="reporting/narrate" version="1">
    <system>
  You write the narrative of a monthly performance report for a marine fuel sales team.

  You receive computed figures only: KPIs for the current month and the month before, the top movers with a driver word, new and lost entities, and for each mover its whole-book context with a reconciliation line. Every number you may use is given as a display string. Use those strings exactly as written; never recompute, round differently, or invent a figure.

  The frames are marginals. State the facts they support and never chain them into a causal story: an office's loss and another office's gain at a port are two facts, not one event.

  Write markdown with these sections, in this order: "Overall summary" (one paragraph), "Key drivers" (one paragraph on the movers, naming each entity exactly as given), "{entity_label} context" (one paragraph using the reconciliation lines; omit this section for a whole-book report), "Insights" (three to five bullets, each with a number). No dollar signs anywhere; write amounts as plain numbers. Name entities, ports, offices and deal classes exactly as they appear in the data.

  Then suggest three follow-up questions a salesperson would ask next. Each must name only entities present in the data.

  Respond only by calling the narrate tool.
    </system>
  </prompt>
  ```

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_prompt_tree.py`:

```python
from pathlib import Path

import pytest

from poseidon.core.llm.prompt_tree import DEFAULT_PROMPT_TREE, PromptSpec, PromptTree, PromptTreeError


def test_default_tree_is_backend_prompts():
    assert DEFAULT_PROMPT_TREE.name == "prompts"
    assert DEFAULT_PROMPT_TREE.parent.name == "backend"
    assert (DEFAULT_PROMPT_TREE / "reporting" / "narrate.xml").is_file()


def test_resolve_narrate_returns_version_and_system_text():
    spec = PromptTree().resolve("reporting/narrate")
    assert spec == PromptSpec(id="reporting/narrate", version="1", system=spec.system)
    assert "marginals" in spec.system
    assert "Respond only by calling the narrate tool." in spec.system
    assert spec.system == spec.system.strip()


def test_unknown_id_raises():
    with pytest.raises(PromptTreeError, match="reporting/missing"):
        PromptTree().resolve("reporting/missing")


def test_id_mismatch_raises(tmp_path: Path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "y.xml").write_text(
        '<prompt id="x/other" version="3"><system>hi</system></prompt>', encoding="utf-8"
    )
    with pytest.raises(PromptTreeError, match="x/other"):
        PromptTree(tmp_path).resolve("x/y")


def test_ids_cannot_escape_the_tree():
    with pytest.raises(PromptTreeError):
        PromptTree().resolve("../pyproject")
```

Append to `backend/tests/test_llm_types_roles.py` (use that file's existing `Settings` and provider-double conventions):

```python
def test_tool_choice_is_forwarded_only_when_set():
    seen = []

    class _Provider:
        def invoke(self, *, system, messages, tools, model, params, tool_choice=None):
            seen.append(tool_choice)
            return LLMResponse(text="", tool_calls=(), stop_reason="end_turn", input_tokens=0, output_tokens=0)

    class _LegacyProvider:  # the six existing doubles have no tool_choice parameter
        def invoke(self, *, system, messages, tools, model, params):
            seen.append("legacy")
            return LLMResponse(text="", tool_calls=(), stop_reason="end_turn", input_tokens=0, output_tokens=0)

    client = RoleClient(_stub_settings(), providers={"stub": _Provider()})
    client.invoke("synthesis", system="s", messages=[], tools=[{"name": "t", "description": "d", "input_schema": {}}], tool_choice="t")
    client.invoke("synthesis", system="s", messages=[], tools=[])
    legacy = RoleClient(_stub_settings(), providers={"stub": _LegacyProvider()})
    legacy.invoke("synthesis", system="s", messages=[], tools=[])
    assert seen == ["t", None, "legacy"]
```

(`_stub_settings()` is whatever helper that file already uses to build stub-mode settings; name it accordingly.)

Append to `backend/tests/test_llm_bedrock.py`:

```python
def test_forced_tool_choice_is_translated_to_the_bedrock_name():
    request = bedrock._build_request(
        system="s",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "reporting.narrate", "description": "d", "input_schema": {"type": "object"}}],
        model="m",
        params={},
        tool_choice="reporting.narrate",
    )
    assert request["toolConfig"]["toolChoice"] == {"tool": {"name": "reporting__narrate"}}


def test_forced_tool_choice_without_tools_is_an_error():
    with pytest.raises(ValueError, match="tool_choice requires at least one tool"):
        bedrock._build_request(system="s", messages=[], tools=[], model="m", params={}, tool_choice="x")
```

- [ ] **Step 2: Run to verify they fail**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_prompt_tree.py tests/test_llm_types_roles.py tests/test_llm_bedrock.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t7-red.txt 2>&1; tail -6 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t7-red.txt
```

Expected: `ModuleNotFoundError` for `prompt_tree`; `TypeError: ... unexpected keyword argument 'tool_choice'` for the other two.

- [ ] **Step 3: Implement**

`prompt_tree.py`: `resolve` rejects ids containing `..` or starting with `/` (`PromptTreeError`), builds `root / f"{prompt_id}.xml"`, parses with `xml.etree.ElementTree`, requires the root tag `prompt` with `id == prompt_id` and a `version` attribute, and returns the stripped `<system>` text. Module docstring cites D46 and ruling 1 of this plan (why `backend/prompts`, why not `PromptRegistry`).
`roles.py`: add the keyword to the protocol and to `RoleClient.invoke`; build `extra = {} if tool_choice is None else {"tool_choice": tool_choice}` and call `provider.invoke(..., **extra)`.
`bedrock.py`: thread `tool_choice` from `invoke` into `_build_request`; add the `toolChoice` key after `_build_tool_config`.
`stub.py`, `dev_router.py`: accept the keyword; the stub records it.

- [ ] **Step 4: Run to verify they pass, plus the LLM and chat modules that use the providers**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_prompt_tree.py tests/test_llm_types_roles.py tests/test_llm_bedrock.py tests/test_llm_loop.py tests/test_chat_orchestrator.py tests/test_brief_subskills.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t7-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t7-green.txt
```

Expected: all green (the legacy doubles in the loop and orchestrator tests prove ruling 2).

- [ ] **Step 5: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/llm poseidon/core/chat/dev_router.py tests/test_prompt_tree.py tests/test_llm_types_roles.py tests/test_llm_bedrock.py
cd ..
git add backend/prompts backend/poseidon/core/llm backend/poseidon/core/chat/dev_router.py backend/tests/test_prompt_tree.py backend/tests/test_llm_types_roles.py backend/tests/test_llm_bedrock.py
git commit -q -F - <<'EOF'
feat(llm): backend/prompts tree with PromptTree (D46, first step); forced tool call

prompts/reporting/narrate.xml is the first versioned XML system prompt;
PromptTree resolves an id to it. RoleClient.invoke and the provider
protocol gain tool_choice, forwarded only when set so existing doubles keep
their signatures; Bedrock maps it to Converse toolChoice with the
Bedrock-safe name. The tree lives under backend/ because that is the
runtime's root in every habitat.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 8: Template narrative, entity-scoped grounding, suggested questions

**Model:** Opus implements; Opus reviews. (The grounding check is the one thing standing between a model's prose and a wrong number reaching a salesperson. The reviewer must re-derive the 45-token count of the must-pass fixture by hand from the narrative text, not from the test output.)

**Files:**
- Create: `backend/poseidon/tasks/reporting/skills/monthly_performance/tools/narrative_template.py`
- Create: `.../monthly_performance/tools/grounding.py`
- Create: `.../monthly_performance/tools/questions.py`
- Modify: `.../monthly_performance/tests/payload_fixture.py` (add `mockup_payload()`; it may now import `poseidon.core.analytics.mom`, which is core, not the tools package)
- Test: `.../monthly_performance/tests/test_narrative_template.py`, `test_grounding.py`, `test_questions.py` (all new)

**Interfaces:**
- Consumes: the payload v1 contract (Task 6), `mom.display`, `mom.delta`, `mom.fmt_value`, `mom.fmt_change`, `mom.KIND_BY_METRIC` (Task 3).
- Produces (every name below is used verbatim by Task 9):
  ```python
  # grounding.py
  @dataclass(frozen=True)
  class GroundingIndex:
      kpi: frozenset[str]                       # display strings of the KPI tiles and every table footer
      by_entity: Mapping[str, frozenset[str]]   # canonical name -> display strings that belong to it
      aliases: Mapping[str, str]                # casefolded alias -> canonical name
      exempt: frozenset[str]                    # the integers that appear in the two period labels

  def build_index(payload: Mapping[str, Any]) -> GroundingIndex
  def numeric_tokens(sentence: str) -> list[str]          # normalised: U+2212 -> "-", "minus 7,700" -> "-7,700"
  def find_entities(sentence: str, index: GroundingIndex) -> list[str]
  def split_paragraphs(text: str) -> list[list[str]]      # paragraphs, each a list of sentences

  @dataclass(frozen=True)
  class Violation:
      sentence: str
      tokens: tuple[str, ...]

  @dataclass(frozen=True)
  class GroundingReport:
      ok: bool
      violations: tuple[Violation, ...]
      checked: int                                        # non-exempt tokens examined
      @property
      def unverified(self) -> int: ...                    # sum(len(v.tokens) for v in violations)

  def check(text: str, index: GroundingIndex) -> GroundingReport

  # narrative_template.py
  def render_template_narrative(payload: Mapping[str, Any]) -> str

  # questions.py
  def mention_resolves(question: str, index: GroundingIndex, payload: Mapping[str, Any]) -> bool
  def backfill_questions(payload: Mapping[str, Any]) -> list[str]
  def select_questions(
      candidates: Sequence[str], payload: Mapping[str, Any], index: GroundingIndex
  ) -> list[str]                                          # exactly three, model questions first
  ```

  **Grounding rules (D67 made exact; the tests below are the contract, the prose here is the reason):**
  - **Tokens.** A numeric token is a run of digits with optional thousands separators and an optional decimal part, an optional trailing `%`, and an optional sign immediately before the first digit (`+`, `-`, U+2212, or the word `minus` followed by whitespace). Reference regex, which the implementer may tighten as long as the tokenisation tests pass:
    `(?<![\w.])(?:minus\s+)?[-+−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|(?<![\w.])(?:minus\s+)?[-+−]?\d+(?:\.\d+)?%?`
    Normalisation: U+2212 becomes `-`; `minus ` becomes `-`. `30.92/ton` yields `30.92`; `(27.58 -> 30.03/ton)` yields `27.58` and `30.03` (the `-` of `->` is not followed by a digit, so it is not a sign).
  - **Exempt tokens** are never checked and never counted: an unsigned four-digit integer from 1900 to 2099 (a year); an unsigned integer from 0 to 10 with no separator, decimal or `%` (a rank or a count in words' place); any unsigned integer that appears in either period label (`"October 1 to 11, 2026"` exempts `1`, `11`, `2026`). Month names are words, not tokens.
  - **Display variants.** A display string `d` admits these token spellings: `d` itself; `d` without its leading sign (an unsigned token may match a figure of either sign, because prose says "fell 56,500" against a stored `-56,500`); and, when `d` ends in `%` or ` pts`, the same two forms with that suffix removed (prose says "down 5.3 points" against `-5.3 pts`). A **signed** token must match the sign: `-30.0%` does not match `+30.0%`. The non-numeric displays `flat`, `new` and `n/a` are never indexed.
  - **Index.** `kpi` holds the four display strings of every KPI metric plus the display strings of every table footer. `by_entity[name]` holds, for every row of every table (`rows` and `other` included), every mover, and every new/lost row: the twelve display strings of GP, VOLUME and MARGIN; and for every `entity_context[name]`: the twelve `book` strings, the two `share_of_book` strings, the three `reconciliation` strings, and each `offsets[*].display` and `ports_rest_of_book[*].display`. Each offset and rest-of-book figure is indexed **twice**: under the mover and under the named office or port (`by_entity["Spain Office"]` gains `+41,000`), so "the Spain Office (+41,000)" verifies whether or not the mover is named in that sentence.
  - **Aliases.** Every canonical name maps from its casefolded full name. It also maps from its casefolded first word when that word is four or more letters and no other canonical name shares it (`Northstar Lines` -> `northstar`; `Med Supply Team` gets no alias; `Vega Bulk` and `Vega Carriers` give `vega` to neither). Matching is a whole-word regex (`\b...\b`) over the casefolded sentence, so `Northstar's` and `**Meridian Bunkering**` both match.
  - **Scope.** A paragraph is a block separated by a blank line, a markdown heading line, or a list item line (each `- ` / `* ` / `1. ` line is its own paragraph). Sentences split on `.`, `!` or `?` followed by whitespace. The entities in scope for a sentence are those named in it **plus those named in earlier sentences of the same paragraph** (so "Meridian Bunkering did not leave. Its whole-book GP is down only 12,400" verifies). The KPI set is in scope for **every** sentence: the mockup's own summary names the office beside the office totals, and a sentence naming a port must still be allowed to quote the office total. (Ruling 11 for the morning-review list: D67's "when the sentence names no entity" clause is read as "entity figures need the entity named; KPI figures never do".)
  - **Verdict.** Every non-exempt token must equal a variant of a display string in scope. A sentence with one or more unverified tokens is one `Violation`. `check` never raises on odd input (empty text, no sentences, a heading-only narrative) and returns `ok=True, checked=0` for text with no tokens.

  **Template narrative** (`render_template_narrative`, zero model calls, mom-patterns' Overall Summary, Key Drivers and New/Lost templates with the entity noun parameterised). Exact wording, because Task 9 tests and the grounding check both read it:
  ```
  **{prior.label} to {current.label}, {scope.label}.** Overall volume {CHANGE(VOLUME)} ({VOLUME.change} tons), while gross profit {CHANGE(GP)} ({GP.change}). Margin moved from {MARGIN.prior}/ton to {MARGIN.current}/ton ({MARGIN.change}/ton). The {office|book} won {NUM_WON.current} transactions in {current.label} vs {NUM_WON.prior} in {prior.label} ({COUNT(NUM_WON)}), with inquiries {UPDOWN(NUM_INQUIRIES)} ({NUM_INQUIRIES.current} vs {NUM_INQUIRIES.prior}). The win rate {RATE(WIN_RATE)} from {WIN_RATE.prior} to {WIN_RATE.current}.

  ## Key drivers

  *Top increases by GP:*
  1. **{key}**: GP {GP.change} ({GP.pct}), {driver}
     - Volume: {VOLUME.prior} -> {VOLUME.current} tons ({VOLUME.change}, {VOLUME.pct})
     - Margin: {MARGIN.prior} -> {MARGIN.current}/ton ({MARGIN.change})
  *Top decreases by GP:*
  1. ...

  ## New and lost {entity_label_plural}

  **New in {current.label}:** {key} (GP {GP.current}, {VOLUME.current} tons, margin {MARGIN.current}); {next}; ... and {N} more
  **Lost from {prior.label}:** {key} (GP {GP.prior}, {VOLUME.prior} tons, margin {MARGIN.prior})
  ```
  where every `{X.y}` is the payload's display string; `CHANGE(m)` is `increased by {pct without sign}` / `decreased by {pct without sign}` / `was flat` (and then the parenthetical is omitted) / `rose from a zero base to {current}` when pct is `new`; `COUNT(NUM_WON)` is `{abs change} more` / `{abs change} fewer` / `unchanged`; `UPDOWN` is `up {pct}` / `down {pct}` / `flat` (the `(current vs prior)` parenthetical always follows); `RATE` is `improved` / `declined` / `held`; a flat MARGIN change renders `(unchanged)`; an empty movers list or new/lost list renders the word `none`; new/lost are capped at ten entries with `and N more`; `entity_label_plural` is `payload["entity"]["label"].lower() + "s"`; `{office|book}` is `office` when `scope.office_column` is set, else `book`. Because every figure is a display string, the template always passes `check` against its own payload, and a test pins that.

  **Questions.** `mention_resolves` strips the question's first word, then examines every maximal run of capitalised words (a word whose first character is uppercase; a trailing `'s`, `?`, `,` or `.` is stripped; `&` joins a run). Walking a run left to right: the longest entity name (casefolded, matched word by word) starting at the position consumes its words; otherwise a single word that is an alias, or whose casefold is in `KNOWN_WORDS`, consumes one word; anything else is an unresolved mention and the question is dropped, never rewritten. `KNOWN_WORDS` = the metric words `gp volume margin inquiries inquiry won lost win rate fixtures`, the twelve month names, `office port ports customer customers supplier suppliers book whole deal class report month total change`, and every word of `scope.label`, `entity.label` and the two period labels. `backfill_questions` returns, in order: `Why did GP change for {top}?`; then `Where else does {top} lift fuel?` for the customer and book perspectives or `Which customers did {top} supply in {scope.office_value}?` for the supplier perspective; `What were the top ports in the report?`; `Which deal class drove the margin change?`; `Which {entity_label_plural} drove GP in {current.label}?`. `{top}` is the mover with the largest absolute GP change across increases and decreases (a tie goes to the decrease); when there are no movers the two `{top}` questions are skipped. `select_questions` keeps the candidates that resolve, in order, dropping casefolded duplicates, then appends backfill questions not already present until the list has three, and returns exactly three.

- [ ] **Step 1: Add `mockup_payload()` to the fixture module**

Append to `payload_fixture.py` a builder that carries the figures of `docs/reference/mockups/2026-09-04-monthly-report-mockup.html` lines 422-622, built **through `mom`** from raw numbers so the display strings are the engine's own, not hand-typed. Raw figures (prior July 2026, current August 2026, scope label `Gibraltar Office (Customer Broker Office)`, entity Customer, `window_kind` complete):

| KPI | prior | current |
|---|---|---|
| VOLUME | 41,860 | 44,210 |
| GP | 1,236,400 | 1,182,900 |
| NUM_WON | 312 | 298 |
| NUM_INQUIRIES | 508 | 531 |
| NUM_LOST | 196 | 233 |

MARGIN and WIN_RATE are derived by `mom.totals` (so they read `29.54` / `26.76` and `61.4%` / `56.1%`, exactly the mockup).

`entities_by_gp` and `entities_by_volume` rows (customer: GP prior, GP current, VOLUME prior, VOLUME current): Northstar Lines 142,600 / 171,900 / 4,810 / 5,960; Meridian Bunkering 214,800 / 158,300 / 7,270 / 5,120; Pacific Crest Shipping 121,400 / 128,700 / 4,120 / 4,390; Atlas Tankers 98,200 / 104,500 / 3,560 / 3,480; Bluewater Carriers 87,900 / 92,100 / 2,980 / 3,210; Helix Container Line 76,300 / 79,800 / 2,610 / 2,790; Corsair Maritime 71,500 / 68,200 / 2,450 / 2,510; Solent Ferries 54,100 / 58,900 / 1,890 / 2,140; Vega Bulk 49,800 / 47,300 / 1,720 / 1,690; Harbor & Sons 38,200 / 41,600 / 1,310 / 1,480. `other` is `None`; the footer is a row built from the KPI raw totals (the fixture is a payload-shape fixture, not a roll-up).

`ports_by_gp` and `ports_by_volume` (GP prior / current, VOLUME prior / current): Gibraltar 655,100 / 612,400 / 21,400 / 22,900; Algeciras 221,300 / 238,900 / 7,900 / 9,100; Ceuta 118,800 / 121,300 / 4,400 / 4,600; Tangier 104,200 / 98,700 / 3,900 / 3,700; Las Palmas 70,600 / 62,100 / 2,600 / 2,300; Malta 31,900 / 28,400 / 1,050 / 950; Barcelona 19,800 / 12,600 / 380 / 420; Valencia 14,700 / 8,500 / 230 / 240. `deal_class`: TRADED 742,100 / 688,400 / 24,900 / 25,700; INVENTORY 351,600 / 356,900 / 12,400 / 13,100; CONTRACTED 142,700 / 137,600 / 4,560 / 5,410.

Movers: increases Northstar Lines (volume-driven), Pacific Crest Shipping (volume-driven), Atlas Tankers (margin-driven); decreases Meridian Bunkering (volume-driven), Corsair Maritime (margin-driven), Vega Bulk (both). New: Orion Offshore (GP 12,400, 480 t); lost: Kestrel Lines (GP 18,900, 610 t). `entity_context`: Meridian Bunkering book GP 402,100 -> 389,700, VOLUME 13,600 -> 12,600; share from office GP over book GP (`53.4%` -> `40.6%`); reconciliation office_change -56,500, book_change -12,400, other_offices_net +44,100; offsets `Spain Office` +41,000 and `Med Supply Team` +28,400; ports_rest_of_book `Algeciras` +31,200. Northstar Lines book GP 301,200 -> 322,800, VOLUME 10,200 -> 11,200; share `47.3%` -> `53.3%`; reconciliation +29,300 / +21,600 / -7,700; offsets `Rotterdam` -7,700; no rest-of-book ports. `narrative.template_md` is any non-empty string (Task 9 fills it for real); `suggested_questions` are the mockup's three chips; `proof` and `queries` as in `minimal_payload`.

Write a small local helper `_row(key, gp_prior, vol_prior, gp_current, vol_current, status="both")` that builds a ROW from `mom.delta` and `mom.display` (MARGIN per side is `gp / vol` or `0.0`), and `_delta(prior, current, kind)` for KPI and book DELTAs; `share_of_book.display` renders `mom.share(...)` as `f"{share:.1f}%"` or `n/a`.

- [ ] **Step 2: Write the failing grounding tests**

Create `test_grounding.py`:

```python
"""D67: the entity-scoped grounding check. The mockup narrative is the
must-pass fixture; a paragraph that hands one customer's figure to another
is the must-reject fixture. Every token count below was derived by hand
from the fixture text (see the comment on each)."""

from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    minimal_payload,
    mockup_payload,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import grounding

# The mockup's "Summary of operations" (docs/reference/mockups/..., lines
# 611-622) with its two non-ASCII glyphs spelled in ASCII: the arrow is
# "->" and the minus sign is "-". 45 non-exempt numeric tokens:
# summary 15, key drivers 11, account context 9, insights 10.
MOCKUP_NARRATIVE = """\
**July 2026 to August 2026, Gibraltar Office.** Overall volume increased by 5.6% (+2,350 tons), while gross profit decreased by 4.3% (-53,500). Margin moved from 29.54/ton to 26.76/ton (-2.78/ton). The office won 298 transactions in August vs 312 in July (14 fewer), with inquiries up 4.5% (531 vs 508). The win rate declined from 61.4% to 56.1%.

## Key drivers

The GP decline is concentrated in one account. Meridian Bunkering's GP fell 56,500 (-26.3%) on volume, down 2,150 tons, while its margin actually improved to 30.92/ton. Northstar Lines offset a third of that with +29,300 (+20.5%), also volume-driven, on 1,150 more tons. Atlas Tankers added 6,300 purely on margin (27.58 -> 30.03/ton). Corsair Maritime lost 3,300 on margin compression despite more volume.

## Account context

Meridian Bunkering did not leave. Its whole-book GP is down only 12,400 (-3.1%): the volume that left Gibraltar reappeared under the Spain Office (+41,000), mostly at Algeciras (+31,200), supplied by the Med Supply Team (+28,400). Gibraltar's share of the account dropped from 53.4% to 40.6%. Northstar's gain is real growth for the account (+21,600 overall), with a small -7,700 offset at Rotterdam.

## Insights

- Margin compression is broad, not one deal: all three deal classes lost margin, CONTRACTED most sharply (31.29 -> 25.43/ton).
- TRADED business gained 800 tons but lost 53,700 of GP, so August's extra volume was priced thinner than July's.
- Inquiries rose 4.5% while wins fell 4.5%; 37 more lost inquiries pushed the win rate down 5.3 points.
- One account came in (Orion Offshore, 12,400 GP) and one went quiet (Kestrel Lines, 18,900 in July).
"""


def test_mockup_narrative_passes_with_every_token_checked():
    report = grounding.check(MOCKUP_NARRATIVE, grounding.build_index(mockup_payload()))
    assert report.ok, report.violations
    assert report.unverified == 0
    assert report.checked == 45


def test_must_reject_fixture_attributes_customer_b_figure_to_customer_a():
    """Review Focus 4 / spec 5.6: Meridian's -56,500 and -26.3% handed to
    Northstar. Both tokens are unverified: 56,500 is nobody else's figure
    and -26.3% carries a sign Northstar's own +20.5% cannot match."""
    text = "Northstar Lines' GP fell 56,500 (-26.3%) on lower volume."
    report = grounding.check(text, grounding.build_index(mockup_payload()))
    assert not report.ok
    assert len(report.violations) == 1
    assert report.violations[0].sentence == text
    assert report.violations[0].tokens == ("56,500", "-26.3%")
    assert report.unverified == 2


def test_tokens_normalise_the_unicode_minus_and_the_word_minus():
    text = "GP fell minus 7,700 and −2.78/ton to 56.1%; margin was 27.58 -> 30.03/ton."
    assert grounding.numeric_tokens(text) == ["-7,700", "-2.78", "56.1%", "27.58", "30.03"]


def test_years_small_integers_and_period_label_days_are_exempt():
    payload = mockup_payload()
    payload["periods"]["current"]["label"] = "October 1 to 11, 2026 (month to date)"
    payload["periods"]["prior"]["label"] = "September 1 to 11, 2026"
    index = grounding.build_index(payload)
    report = grounding.check("In 2026 the top 3 ports held from day 1 to 11.", index)
    assert report.ok and report.checked == 0


def test_an_unsigned_token_matches_either_sign_but_a_signed_token_must_match():
    index = grounding.build_index(mockup_payload())
    assert grounding.check("Northstar Lines rose 29,300.", index).ok
    assert grounding.check("Northstar Lines rose +29,300.", index).ok
    assert not grounding.check("Northstar Lines fell -29,300.", index).ok


def test_entity_scope_accumulates_within_a_paragraph_and_resets_at_a_blank_line():
    index = grounding.build_index(mockup_payload())
    same = "Meridian Bunkering fell 56,500. Its margin improved to 30.92/ton."
    split = "Meridian Bunkering fell 56,500.\n\nIts margin improved to 30.92/ton."
    assert grounding.check(same, index).ok
    report = grounding.check(split, index)
    assert not report.ok and report.violations[0].tokens == ("30.92",)


def test_a_list_item_is_its_own_paragraph():
    index = grounding.build_index(mockup_payload())
    text = "Meridian Bunkering fell 56,500.\n- Its margin improved to 30.92/ton."
    assert not grounding.check(text, index).ok


def test_offsets_are_indexed_under_the_mover_and_the_named_office():
    index = grounding.build_index(mockup_payload())
    assert grounding.check("The Spain Office gained +41,000.", index).ok
    assert grounding.check("Meridian Bunkering shifted +41,000 elsewhere.", index).ok
    assert grounding.check("Algeciras took +31,200.", index).ok


def test_kpi_figures_are_allowed_in_every_sentence():
    index = grounding.build_index(mockup_payload())
    assert grounding.check("Gibraltar led while the office made 1,182,900 overall.", index).ok


def test_first_word_alias_needs_four_letters_and_uniqueness():
    payload = minimal_payload()
    rows = payload["tables"]["entities_by_gp"]["rows"]
    rows[0]["key"] = "Vega Bulk"
    rows[1]["key"] = "Vega Carriers"
    payload["entity_context"] = {
        "Med Supply Team": payload["entity_context"]["Meridian Bunkering"],
    }
    index = grounding.build_index(payload)
    assert "vega" not in index.aliases
    assert "med" not in index.aliases
    assert index.aliases["vega bulk"] == "Vega Bulk"
    mockup = grounding.build_index(mockup_payload())
    assert mockup.aliases["northstar"] == "Northstar Lines"
    assert mockup.aliases["meridian"] == "Meridian Bunkering"


def test_possessives_and_markdown_emphasis_still_name_the_entity():
    index = grounding.build_index(mockup_payload())
    assert grounding.find_entities("**Northstar's** gain was +21,600.", index) == ["Northstar Lines"]


def test_text_without_tokens_is_ok_and_never_raises():
    index = grounding.build_index(mockup_payload())
    for text in ("", "## Key drivers", "Nothing moved.", "\n\n"):
        report = grounding.check(text, index)
        assert report.ok and report.checked == 0 and report.violations == ()


def test_modules_are_ascii_on_disk():
    from pathlib import Path

    from poseidon.tasks.reporting.skills.monthly_performance.tools import (
        narrative_template,
        questions,
    )

    for module in (grounding, narrative_template, questions):
        Path(module.__file__).read_bytes().decode("ascii")
```

- [ ] **Step 3: Write the failing template and question tests**

Create `test_narrative_template.py`:

```python
from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    minimal_payload,
    mockup_payload,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import grounding
from poseidon.tasks.reporting.skills.monthly_performance.tools.narrative_template import (
    render_template_narrative,
)


def test_overall_summary_uses_the_display_strings_verbatim():
    text = render_template_narrative(mockup_payload())
    assert text.startswith("**July 2026 to August 2026, Gibraltar Office (Customer Broker Office).**")
    assert "Overall volume increased by 5.6% (+2,350 tons), while gross profit decreased by 4.3% (-53,500)." in text
    assert "Margin moved from 29.54/ton to 26.76/ton (-2.78/ton)." in text
    assert "The office won 298 transactions in August 2026 vs 312 in July 2026 (14 fewer), with inquiries up 4.5% (531 vs 508)." in text
    assert "The win rate declined from 61.4% to 56.1%." in text


def test_key_drivers_list_movers_with_their_driver_word():
    text = render_template_narrative(mockup_payload())
    assert "## Key drivers" in text
    assert "*Top increases by GP:*\n1. **Northstar Lines**: GP +29,300 (+20.5%), volume-driven" in text
    assert "   - Volume: 4,810 -> 5,960 tons (+1,150, +23.9%)" in text
    assert "*Top decreases by GP:*\n1. **Meridian Bunkering**: GP -56,500 (-26.3%), volume-driven" in text


def test_new_and_lost_sections_name_entities_or_say_none():
    text = render_template_narrative(mockup_payload())
    assert "## New and lost customers" in text
    assert "**New in August 2026:** Orion Offshore (GP 12,400, 480 tons, margin 25.83)" in text
    assert "**Lost from July 2026:** Kestrel Lines (GP 18,900, 610 tons, margin 30.98)" in text
    payload = mockup_payload()
    payload["new_lost"] = {"new": [], "lost": []}
    payload["movers"] = {"increases": [], "decreases": []}
    text = render_template_narrative(payload)
    assert "**New in August 2026:** none" in text
    assert "*Top increases by GP:*\nnone" in text


def test_flat_and_zero_base_wording():
    payload = mockup_payload()
    for metric in ("VOLUME", "NUM_INQUIRIES"):
        payload["kpis"][metric]["display"].update({"change": "flat", "pct": "flat"})
    payload["kpis"]["GP"]["display"].update({"change": "+1,000", "pct": "new"})
    payload["kpis"]["MARGIN"]["display"]["change"] = "flat"
    text = render_template_narrative(payload)
    assert "Overall volume was flat, while gross profit rose from a zero base to 1,182,900." in text
    assert "(unchanged)" in text
    assert "with inquiries flat (531 vs 508)" in text


def test_entity_noun_is_parameterised_for_suppliers():
    payload = mockup_payload()
    payload["entity"] = {"column": "SUPPLIER_NM", "label": "Supplier"}
    assert "## New and lost suppliers" in render_template_narrative(payload)


def test_book_perspective_says_book_not_office():
    payload = mockup_payload()
    payload["scope"] = {"office_column": None, "office_value": None, "label": "Whole book"}
    assert "The book won 298 transactions" in render_template_narrative(payload)


def test_template_narrative_passes_the_grounding_check():
    for payload in (mockup_payload(), minimal_payload()):
        report = grounding.check(render_template_narrative(payload), grounding.build_index(payload))
        assert report.ok, report.violations
```

Create `test_questions.py`:

```python
from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    minimal_payload,
    mockup_payload,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import grounding, questions

MODEL = [
    "Why did GP drop for Meridian Bunkering?",
    "Where else does Meridian lift fuel?",
    "Which deal class drove the margin compression?",
]


def _select(candidates, payload=None):
    payload = payload or mockup_payload()
    return questions.select_questions(candidates, payload, grounding.build_index(payload))


def test_model_questions_naming_payload_entities_are_kept_in_order():
    assert _select(MODEL) == MODEL


def test_a_question_naming_an_unknown_entity_is_dropped_not_rewritten_and_backfilled():
    picked = _select(["Why did Poseidon Shipping leave?", MODEL[0]])
    assert picked[0] == MODEL[0]
    assert len(picked) == 3
    assert not any("Poseidon" in q for q in picked)


def test_backfill_is_deterministic_and_names_the_largest_mover():
    assert _select([]) == [
        "Why did GP change for Meridian Bunkering?",
        "Where else does Meridian Bunkering lift fuel?",
        "What were the top ports in the report?",
    ]


def test_supplier_backfill_asks_which_customers_the_supplier_supplied():
    payload = mockup_payload()
    payload["perspective"] = "supplier"
    payload["entity"] = {"column": "SUPPLIER_NM", "label": "Supplier"}
    payload["scope"] = {
        "office_column": "PRIMARY_SUPPLY_TEAM_OFFICE",
        "office_value": "Med Supply Team",
        "label": "Med Supply Team (Supply Team Office)",
    }
    assert _select([], payload)[1] == "Which customers did Meridian Bunkering supply in Med Supply Team?"


def test_no_movers_skips_the_mover_questions():
    payload = mockup_payload()
    payload["movers"] = {"increases": [], "decreases": []}
    assert _select([], payload) == [
        "What were the top ports in the report?",
        "Which deal class drove the margin change?",
        "Which customers drove GP in August 2026?",
    ]


def test_duplicates_of_a_kept_question_are_not_backfilled_twice():
    picked = _select(["What were the top ports in the report?", "what were the top ports in the report?"])
    assert picked.count("What were the top ports in the report?") == 1
    assert len(picked) == 3


def test_metric_words_offices_and_the_first_word_are_not_entity_mentions():
    index = grounding.build_index(mockup_payload())
    payload = mockup_payload()
    assert questions.mention_resolves("Did GP or Volume move more at Gibraltar Office?", index, payload)
    assert questions.mention_resolves("Which deal class drove the margin change in August 2026?", index, payload)
    assert questions.mention_resolves("How did Harbor & Sons do?", index, payload)
    assert not questions.mention_resolves("How did Harbor & Daughters do?", index, payload)


def test_minimal_payload_always_yields_three():
    assert len(_select([], minimal_payload())) == 3
```

- [ ] **Step 4: Run to verify they fail**

```bash
cd backend
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider poseidon/tasks/reporting -k "grounding or narrative_template or questions" > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t8-red.txt 2>&1; tail -6 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t8-red.txt
```

Expected: `ImportError: cannot import name 'mockup_payload'` (collection fails before any test runs). That is the right failure: the fixture and the three modules do not exist.

- [ ] **Step 5: Implement the fixture and the three modules**

`payload_fixture.mockup_payload()` per Step 1. `grounding.py`, `narrative_template.py`, `questions.py` to the rules in Interfaces. Implementation notes:
- `build_index` walks the payload once; a private `_variants(display) -> frozenset[str]` is applied at **check** time to the strings in scope, not at index time, so the index stays small and readable in a debugger.
- `find_entities` returns canonical names in first-appearance order and never the same name twice.
- `check` collects the scope as it walks a paragraph's sentences (`scope |= find_entities(sentence)`), then tests each token against `kpi | union(by_entity[name] for name in scope)`.
- `narrative_template` has one private `_section` per mom-patterns template and joins them with blank lines; nothing in it computes a number.
- `questions.KNOWN_WORDS` is a module-level frozenset; the payload-derived words are added per call.
- Run `ruff format` on the three new modules and the three new test files (new files only).

- [ ] **Step 6: Run to verify they pass**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider poseidon/tasks/reporting > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t8-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t8-green.txt
```

Expected: every test under `poseidon/tasks/reporting` passes (Tasks 4, 6 and 8), with the one `pdf`-marked test skipped on the host. In the task report, show the hand count of the 45 tokens (15 + 11 + 9 + 10) sentence by sentence.

- [ ] **Step 7: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/tasks/reporting
cd ..
git add backend/poseidon/tasks/reporting
git commit -q -F - <<'EOF'
feat(reporting): template narrative, entity-scoped grounding check, suggested questions

The template narrative fills mom-patterns' three templates from display
strings only. The grounding check (D67) tokenises prose into numeric
tokens, scopes each sentence to the entities named in it and earlier in
its paragraph, lets KPI figures through everywhere, and reports the
unverified tokens per sentence; the mockup narrative passes with all 45
figures checked and a misattributed figure fails. Suggested questions keep
model chips whose entity mentions resolve and backfill deterministically
to three.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 9: The skill — `reporting.monthly_performance`, the `internal` flag, the pipeline

**Model:** Opus implements; Opus reviews.

**Files:**
- Create: `backend/poseidon/tasks/reporting/task.yml`
- Create: `.../monthly_performance/schema.py`, `skill.py`, `tools/frames.py`, `tools/context.py`, `tools/payload.py`, `tools/narrate.py`
- Create: `.../monthly_performance/tests/fake_client.py` (the offline `DataClient` and the multi-office fixture rows), `tests/test_skill.py` (offline), `tests/test_skill_pg.py` (pg)
- Modify: `backend/poseidon/core/skills/registry.py` (`RegisteredSkill.internal`, `_register`, `router_skill_ids`, `tool_schemas`)
- Modify: `backend/poseidon/core/skills/context.py` (append `report_store`, `prompt_tree`)
- Modify: `backend/poseidon/core/skills/result.py` (add `report_part`)
- Modify: `backend/poseidon/core/llm/prompts.py:223-235` (`skill_lines_block` over `router_skill_ids`)
- Modify: `backend/poseidon/api/live_chat.py:741-758` (`list_skills` over `router_skill_ids`)
- Test: `backend/tests/test_skill_registry.py` (the pinned id list and parity test; two new throwaway tests), `backend/tests/test_llm_prompts.py:173-184, 358-367`

**Interfaces:**
- Consumes: everything Tasks 1-8 produce, by the exact names in their Interfaces blocks: `FrameQuerySpec`, `MetricQuerySpec`, `FrameResult`, `FrameRow`, `DataClient.snapshot/list_months/run_frame_query/list_dimension_values`, `mom.*`, `resolve_periods`/`ResolvedPeriods`/`PeriodError`, `ReportStore`/`NewRun`, `render_html`/`render_pdf`, `PromptTree`, `RoleClient.invoke(..., tools=, tool_choice=)`, `render_template_narrative`, `grounding.build_index/check`, `questions.select_questions`, `resolve_row_scope_value`, `uuid7`.
- Produces:
  ```python
  # core/skills/registry.py
  @dataclass(frozen=True)
  class RegisteredSkill:
      ...existing five fields...
      internal: bool = False        # appended; SKILL_META["internal"], default False

  class SkillRegistry:
      skill_ids -> list[str]                # every registered id, internal ones included (unchanged)
      router_skill_ids -> list[str]         # NEW: ids with internal == False, same order
      tool_schemas -> list[dict]            # CHANGED: router-visible skills only
      get / dispatch                        # unchanged: internal skills resolve and dispatch
  # _register: meta.get("internal", False) must be a bool, else SkillDefinitionError
  # naming the skill; the Bedrock-safe name check still covers internal ids.

  # core/skills/context.py (appended after `user`, both typed object for the documented reason)
  report_store: object | None = None        # a poseidon.reports.store.ReportStore
  prompt_tree: object | None = None         # a poseidon.core.llm.prompt_tree.PromptTree

  # core/skills/result.py
  def report_part(payload: dict) -> dict    # {"kind": "report", "payload": dict(payload)}

  # schema.py
  class Args(BaseModel):
      perspective: Literal["customer", "supplier", "book"]
      office_value: str | None = None       # required unless perspective == "book" (model_validator)
      month: date                           # first day of the report month (field_validator: day == 1)
      window_kind: Literal["complete", "mtd"] = "complete"
      basis: Literal["prior_month"] = "prior_month"
  SKILL_META = {"description": "Internal: builds the monthly performance report for an office or the whole book. Dispatched by the report flow, never chosen by the router.", "examples": [], "internal": True}

  # skill.py
  ENTITY = "MARINE_SALES_PLANNING_V"
  GROUP_BY = ("CUST_NM", "SUPPLIER_NM", "LOC_NM", "CUSTOMER_TEAM_NAME", "PRIMARY_SUPPLY_TEAM_OFFICE", "DEAL_CLASSIFICATION_TRADE_CUT")
  @dataclass(frozen=True)
  class Perspective:
      office_column: str | None
      office_label: str | None      # "Customer Broker Office" | "Supply Team Office" | None
      entity_column: str
      entity_label: str
      noun: str                     # "customers" | "suppliers"
  PERSPECTIVES = {
      "customer": Perspective("CUSTOMER_TEAM_NAME", "Customer Broker Office", "CUST_NM", "Customer", "customers"),
      "supplier": Perspective("PRIMARY_SUPPLY_TEAM_OFFICE", "Supply Team Office", "SUPPLIER_NM", "Supplier", "suppliers"),
      "book": Perspective(None, None, "CUST_NM", "Customer", "customers"),
  }
  def _today() -> date                                   # module-level hook; tests monkeypatch it
  def run(ctx: SkillContext, args: Args) -> SkillResult

  # tools/frames.py
  @dataclass(frozen=True)
  class Timed:                                           # one query's proof line
      label: str; sql_hash: str; rows: int; ms: int
  @dataclass(frozen=True)
  class Slices:
      prior: FrameResult; current: FrameResult
      prior_total: dict[str, float]; current_total: dict[str, float]   # from mom.totals over the frame rows
      queries: list[Timed]
  class ReconciliationError(Exception): ...
  def frame_spec(window, filters, scope_value) -> FrameQuerySpec       # ENTITY, SUM_METRICS, GROUP_BY
  def run_slices(data, periods, filters, scope_value, dialect) -> Slices
      # two frames, two MetricQuerySpec totals (GP, VOLUME), reconcile each window with mom.reconciles
      # on GP and VOLUME (a None total counts as 0.0); raise ReconciliationError("reconciliation
      # failed: <window label> GP frame <x> vs total <y>") on the first mismatch
  def rollup(frame: FrameResult, column: str) -> dict[str, dict[str, float]]
      # group FrameRow by the value at frame.group_by.index(column); fsum each SUM_METRIC (None -> 0)
  def sql_hash(spec, dialect) -> str                     # sha256 of the builder's SQL text, first 12 hex chars

  # tools/context.py
  def build_entity_context(
      book_prior: FrameResult, book_current: FrameResult, office_prior: FrameResult,
      office_current: FrameResult, perspective: Perspective, office_value: str, movers: Sequence[str]
  ) -> dict[str, dict]                                   # the entity_context payload section
  # per mover: book = GP/VOLUME/MARGIN DELTAs over the mover's whole-book rows; share_of_book =
  # mom.share(office GP, book GP) per side (None -> display "n/a", else f"{x:.1f}%");
  # reconciliation office_change / other_offices_net = book_change - office_change / book_change
  # with mom.fmt_change(..., "money") displays; offsets = the other values of perspective.office_column
  # in the mover's book rows whose GP change has the opposite sign to office_change (none when
  # office_change == 0), by abs(change) desc, top 5; ports_rest_of_book = LOC_NM values over the
  # mover's book rows whose office column != office_value, by abs(GP change) desc, top 5.

  # tools/payload.py
  def build_payload(...) -> dict                         # assembles payload_version 1 per the Task 6 contract
  def row_from_merged(row: MergedRow) -> dict            # ROW with display strings
  def kpi_from_delta(d: Delta, metric: str) -> dict      # DELTA with display strings

  # tools/narrate.py
  NARRATE_TOOL = {
      "name": "reporting.narrate",
      "description": "Return the report narrative, three to five insights, any context notes and exactly three suggested questions.",
      "input_schema": {
          "type": "object",
          "properties": {
              "narrative_md": {"type": "string"},
              "insights": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 5},
              "context_notes": {"type": "array", "items": {"type": "string"}},
              "suggested_questions": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 3},
          },
          "required": ["narrative_md", "insights", "context_notes", "suggested_questions"],
      },
  }
  @dataclass(frozen=True)
  class Narrative:
      llm_md: str | None
      grounded: bool
      flags: list[str]
      model_questions: list[str]
      proof_line: str
  def build_computed_block(payload: Mapping) -> dict     # display strings only: scope, periods, entity label,
                                                         # kpis, movers (key, driver, GP/VOLUME/MARGIN), new/lost
                                                         # capped at 10, entity_context (book, share, reconciliation,
                                                         # offsets, ports) -- never a raw float
  def narrate(payload: Mapping, llm: RoleClient, prompt: PromptSpec, index: GroundingIndex) -> Narrative
  ```
  **`narrate` contract.** One `llm.invoke("synthesis", system=prompt.system, messages=[{"role": "user", "content": "Computed figures (JSON):\n" + json.dumps(block, indent=1) + "\n\nWrite the narrative now."}], tools=[NARRATE_TOOL], tool_choice="reporting.narrate")`. A response with `stop_reason == "error"`, no tool call, a tool call not named `reporting.narrate`, or arguments missing any required key or of the wrong type is **malformed**: `Narrative(None, False, ["model narrative failed: <reason>"], [], "narrative: model narrative failed (<reason>); template used")`, where reason is `provider error` or `malformed tool output`. Otherwise `llm_md = narrative_md`, with `"\n\n## Notes\n" + bullets` appended when `context_notes` is non-empty; the grounding text is `llm_md` plus each insight as its own paragraph. If `check` passes: `Narrative(llm_md, True, [], suggested_questions, f"narrative: synthesis role, prompt {prompt.id} v{prompt.version}, grounded ({checked} of {checked} figures)")`. If it fails: **one** retry with the same system prompt and a single user message = the original content + `"\n\nYour previous narrative failed the figure check. These sentences contain figures that are not in the data:\n"` + one `- <sentence>` line per violation + `"\nRewrite the narrative using only the display strings given."`. A second failure (or a malformed retry) gives `Narrative(None, False, [f"AI narrative withheld: {unverified} unverified figures"], model_questions_from_the_last_well_formed_reply, "narrative: AI narrative withheld (<n> unverified figures); template used")`. The model's questions are kept even when the narrative is withheld; `select_questions` filters them.

  **`run` contract (the pipeline, in this order; every early return is a `SkillResult(ok=False, error=problem(...))` and nothing has been written):**
  1. `resolve_periods(args.basis, args.month, args.window_kind, _today())`; `PeriodError` -> 422 `"invalid report period"` with the error's text as detail.
  2. `ctx.report_store is None` -> 500 `"report store unavailable"`, detail `"reporting.monthly_performance needs SkillContext.report_store; the report flow (PR A2) and the demo script wire it"`.
  3. `scope_value = resolve_row_scope_value(ENTITY, user)`; `perspective = PERSPECTIVES[args.perspective]`; `filters = {} if office_column is None else {office_column: (args.office_value,)}`; `dialect = "snowflake" if ctx.settings.data_backend == "snowflake" else "postgres"`.
  4. Inside `with ctx.data.snapshot() as data:`
     - `data.list_months(ENTITY, {}, scope_value)` **without the office filter** (is the month in the data at all, D50): `periods.current.start not in months` -> 422 `"no data for that month"`, detail `f"no rows for {periods.label_current}"`. For a month-to-date run the running month's first day is what must be present.
     - when `office_column` is set: `args.office_value not in data.list_dimension_values(ENTITY, office_column, scope_value=scope_value)` -> 422 `"unknown office"`, detail `f"unknown {office_column} value {args.office_value!r}"`.
     - `slices = run_slices(...)`; `ReconciliationError` -> 500 `"reconciliation failed"` with the error text; **no row is written** (ruling 12: PR A1 writes no `report_run` row on any failure; the `error` status is reserved for Phase 18's queued runs).
     - `entities = mom.merge_breakdowns(rollup(prior, entity_column), rollup(current, entity_column))`, likewise `ports` on `LOC_NM` and `deals` on `DEAL_CLASSIFICATION_TRADE_CUT`; `kpis = mom.compute_kpis(prior frame row values, current frame row values)`; `movers = mom.top_movers(entities)`; `new_lost = mom.new_lost(entities)`; tables `entities_by_gp = mom.ranked(entities, "GP", 10, noun)`, `entities_by_volume = mom.ranked(entities, "VOLUME", 10, noun)`, `ports_by_gp` / `ports_by_volume` (noun `"ports"`), `deal_class = mom.ranked(deals, "GP", len(deals) or 1, "deal classes")`.
     - context frames (perspective != book and movers non-empty): `FrameQuerySpec` whole book, `filters={entity_column: tuple(mover keys)}`, both windows, same `GROUP_BY`, timed like the others; `entity_context = build_entity_context(...)`. For `book` it is `None`; for an office with no movers it is `{}`.
  5. Outside the snapshot: sparse detection. `prior_rows = len(prior.rows)`, `current_rows = len(current.rows)`. Both zero -> flags `["no data in either month"]`, no model call, `suggested_questions = []`. Exactly one zero -> flag `f"model narrative skipped: no rows in {label of the empty window}"`, no model call, questions backfilled. Otherwise, `ctx.settings.llm_mode == "stub"` -> flag `"model narrative skipped: stub mode"`, `grounded = True`, `llm_md = None`; `ctx.llm is None` -> flag `"model narrative failed: no LLM client"`, `grounded = False`; else `narrate(...)` with `prompt = (ctx.prompt_tree or PromptTree()).resolve("reporting/narrate")`.
  6. `template_md = render_template_narrative(partial payload)`; `suggested_questions = select_questions(model_questions, payload, index)` unless the no-data case; assemble the payload (`run_id = str(uuid7())`, `generated_at = datetime.now(UTC)`, `source = ctx.settings.data_backend`, `generated_by = user.name or user.email or user.sub` or `"dev|anonymous"` without a user, `proof` lines below, `queries` from `Timed`).
  7. `html = render_html(payload)`; `pdf = render_pdf(html)` inside `try/except Exception as exc` -> `pdf = None` and flag `f"PDF not rendered: {type(exc).__name__}"` (ruling 13: a renderer without Pango on the host is not a failed report; the HTML is the durable artifact and the flag says why there is no PDF).
  8. `store.insert_run(created_by, NewRun(...))` with `status="ok"`, `html=html.encode("utf-8")`, `narrative_grounded=grounded`, `conversation_id=None` (A2 threads it through), `created_by = user.sub if user else "dev|anonymous"`.
  9. Return `SkillResult(ok=True, parts=[report_part(payload)], proof=payload["proof"], artifacts=[ArtifactRef(name=f"{scope_name} {period_name}.pdf", url=f"/api/reports/{run_id}/pdf", mime="application/pdf")] if pdf else [])` where `scope_name` is `args.office_value` or `"Whole book"` and `period_name` is `label_window(current)` for a complete month or `f"{Month YYYY of the running month} month to date"` for mtd (so `"Rotterdam June 2026.pdf"`, `"Whole book October 2026 month to date.pdf"`).

  **Proof lines** (ASCII, one string each): `Entity: MARINE_SALES_PLANNING_V - source: {source} - {office filter: COL = value | whole book}`; `Windows: {prior.start}..{prior.end} (prior) - {current.start}..{current.end} (current) - basis: prior month - window: {complete|month to date}`; `Frames: six-way group-by, prior {n} rows, current {m} rows; totals reconciled to the cent`; `Context: {k} movers x 2 windows, whole book` or `Context: none (whole book report)` or `Context: none (no movers)`; the narrative proof line from step 5 (for the skipped cases: `narrative: template; model narrative skipped: stub mode`); `Run: report_run {run_id}`.

  **Scope label:** `f"{office_value} ({office_label})"` or `"Whole book"`.

- [ ] **Step 1: Write the failing registry, prompt and context tests**

In `backend/tests/test_skill_registry.py`:
1. Rename `test_discovery_finds_all_four_registered_skills` to `test_discovery_finds_all_five_registered_skills_and_four_router_visible` and make its body:
   ```python
   reg = SkillRegistry.discover()
   assert reg.skill_ids == [
       "customer_insight.existing_customer_brief",
       "customer_insight.new_prospect_brief",
       "data_qa.metric_query",
       "reporting.monthly_performance",
       "research.web_research",
   ]
   assert reg.router_skill_ids == [
       "customer_insight.existing_customer_brief",
       "customer_insight.new_prospect_brief",
       "data_qa.metric_query",
       "research.web_research",
   ]
   assert reg.get("reporting.monthly_performance").internal is True
   ```
   Update its docstring to say Phase 17 PR A1 adds the first `internal` skill, which `skill_ids`, `get` and `dispatch` see and `tool_schemas` does not.
2. In `test_schema_dispatch_parity`, replace the first assertion with `assert schema_names == set(reg.router_skill_ids)` and add `assert set(reg.skill_ids) - schema_names == {"reporting.monthly_performance"}`.
3. Add two throwaway-package tests after `test_disabled_task_is_never_imported`:
   ```python
   def test_internal_flag_hides_a_skill_from_the_router_but_not_from_dispatch(tasks_package):
       internal_schema = _GOOD_SCHEMA.replace(
           '"examples": ["say hello"],', '"examples": ["say hello"], "internal": True,'
       )
       files = {
           **_files("demo_task", "hidden", schema_py=internal_schema),
           **_files("demo_task", "shown"),
       }
       pkg = tasks_package("internal_tasks", files)
       reg = SkillRegistry.discover(pkg)

       assert reg.skill_ids == ["demo_task.hidden", "demo_task.shown"]
       assert reg.router_skill_ids == ["demo_task.shown"]
       assert [s["name"] for s in reg.tool_schemas] == ["demo_task.shown"]
       assert reg.get("demo_task.hidden").internal is True
       res = reg.dispatch("demo_task.hidden", {"q": "hi"}, _ctx())
       assert res.ok is True and res.parts == [{"kind": "text", "payload": {"markdown": "hi"}}]


   def test_internal_must_be_a_bool(tasks_package):
       bad_schema = _GOOD_SCHEMA.replace(
           '"examples": ["say hello"],', '"examples": ["say hello"], "internal": "yes",'
       )
       pkg = tasks_package("badinternal_tasks", _files("demo_task", "bad", schema_py=bad_schema))
       with pytest.raises(SkillDefinitionError) as err:
           SkillRegistry.discover(pkg)
       assert "demo_task.bad" in str(err.value) and "internal" in str(err.value)
   ```

In `backend/tests/test_llm_prompts.py`: `test_router_prompt_names_every_skill_the_registry_discovers` iterates `skills.router_skill_ids` and gains `assert "reporting.monthly_performance" not in rendered` with a docstring line saying an internal skill must never reach the router prompt; `test_skill_lines_block_one_line_per_registered_skill` compares against `registry.router_skill_ids` (both the count and the per-line loop).

`tests/test_live_chat_sse.py::test_get_skills_returns_registry_backed_shape` is **not edited**: its four-entry pinned list staying green is the proof that `/api/skills` hides the internal skill.

- [ ] **Step 2: Run them to verify they fail**

```bash
cd backend
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_skill_registry.py tests/test_llm_prompts.py -k "five or parity or internal or names_every_skill or skill_lines_block" > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-red-registry.txt 2>&1; tail -8 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-red-registry.txt
```

Expected: `AttributeError: 'SkillRegistry' object has no attribute 'router_skill_ids'` and the five-id assertion failing on four ids.

- [ ] **Step 3: Registry, context, result and the two call sites**

`registry.py`: append `internal: bool = False` to `RegisteredSkill`; in `_register` read `internal = meta.get("internal", False)` and raise `SkillDefinitionError(f"skill '{skill_id}': SKILL_META['internal'] must be true or false (got {internal!r})")` when not a bool; add the `router_skill_ids` property; make `tool_schemas` iterate the non-internal skills. Extend the module docstring: an internal skill is dispatched by code (a deterministic flow) and must never be offered to the model, so it is absent from `tool_schemas`, the router prompt and `/api/skills`, while `skill_ids`, `get` and `dispatch` still see it; the Bedrock-safe name check still covers it because a flow dispatch goes through the same loop plumbing.
`context.py`: append the two fields with a docstring paragraph each (typed `object` for the same one-way-dependency reason as `tools`/`llm`; `None` is a real state: every call site that cannot produce a report leaves them unset, and the reporting skill answers a structured 500 when `report_store` is missing and falls back to the default `PromptTree()` when `prompt_tree` is).
`result.py`: add `report_part` next to `phase_section_part` with a docstring naming the Phase 17 `report` kind as additive to the ten frozen kinds and pointing at Task 6's payload contract.
`prompts.py` and `live_chat.py`: `router_skill_ids` in place of `skill_ids`, docstrings updated in one sentence each.

- [ ] **Step 4: Run the registry and prompt modules to verify they pass**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_skill_registry.py tests/test_llm_prompts.py tests/test_live_chat_sse.py tests/test_api_auth.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-green-registry.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-green-registry.txt
```

Expected: the five-id test still FAILS (the skill package has no `task.yml` yet) and everything else passes. That one red test is the handover into the next step.

- [ ] **Step 5: Write the fake client, the fixture rows and the failing offline skill tests**

Create `tests/fake_client.py`:

```python
"""An in-memory DataClient for the reporting skill's offline tests, plus
the multi-office fixture rows every test below shares. Mirrors the real
client's contract (one post-COALESCE key per group_by column, None for a
NULLIF-guarded MARGIN, a half-open window) over plain row dicts, so the
skill under test runs the real pipeline against hand-built numbers whose
expectations are derived by hand in test_skill.py."""

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from math import fsum

from poseidon.core.data.client import FrameResult, FrameRow, MetricResult, PeriodRange
from poseidon.core.data.specs import FrameQuerySpec, MetricQuerySpec

DIMS = (
    "CUST_NM",
    "SUPPLIER_NM",
    "LOC_NM",
    "CUSTOMER_TEAM_NAME",
    "PRIMARY_SUPPLY_TEAM_OFFICE",
    "DEAL_CLASSIFICATION_TRADE_CUT",
)
SUPPLY_OFFICE = {"Shell": "Med Supply Team", "BP": "NW Europe Supply"}


def _row(day, cust, supplier, port, office, deal, gp, vol, won, inq):
    return {
        "LIFT_ETA_DATE": day,
        "CUST_NM": cust,
        "SUPPLIER_NM": supplier,
        "LOC_NM": port,
        "CUSTOMER_TEAM_NAME": office,
        "PRIMARY_SUPPLY_TEAM_OFFICE": SUPPLY_OFFICE[supplier],
        "DEAL_CLASSIFICATION_TRADE_CUT": deal,
        "GP": float(gp),
        "VOLUME": float(vol),
        "NUM_WON": float(won),
        "NUM_INQUIRIES": float(inq),
    }


MAY, JUNE, APRIL = date(2026, 5, 10), date(2026, 6, 10), date(2026, 4, 10)


def multi_office_rows() -> list[dict]:
    """May and June 2026 for Gibraltar Office (the office under test), with
    Northstar and Meridian also trading under Spain Office so share_of_book
    is below 100% and the offsets have both signs; Atlas trades in one
    office only (share 100%, no offsets); Vega has zero GP in May on real
    volume (zero-base rule); Orion is new, Kestrel is lost; Malta Office
    has rows in May only (Review Focus 1); Valencia Office exists only in
    April (the no-data-in-either-month case)."""
    return [
        # May 2026 (prior)
        _row(MAY, "Northstar Lines", "Shell", "Gibraltar", "Gibraltar Office", "TRADED", 100_000, 4_000, 10, 20),
        _row(MAY, "Northstar Lines", "BP", "Algeciras", "Spain Office", "TRADED", 50_000, 2_000, 5, 10),
        _row(MAY, "Meridian Bunkering", "Shell", "Gibraltar", "Gibraltar Office", "INVENTORY", 200_000, 7_000, 20, 30),
        _row(MAY, "Meridian Bunkering", "BP", "Algeciras", "Spain Office", "INVENTORY", 100_000, 3_500, 10, 15),
        _row(MAY, "Atlas Tankers", "Shell", "Ceuta", "Gibraltar Office", "TRADED", 60_000, 2_000, 6, 10),
        _row(MAY, "Kestrel Lines", "BP", "Gibraltar", "Gibraltar Office", "TRADED", 20_000, 600, 2, 4),
        _row(MAY, "Vega Bulk", "Shell", "Ceuta", "Gibraltar Office", "INVENTORY", 0, 100, 1, 2),
        _row(MAY, "Solent Ferries", "BP", "Malta", "Malta Office", "TRADED", 30_000, 1_000, 3, 6),
        # June 2026 (current)
        _row(JUNE, "Northstar Lines", "Shell", "Gibraltar", "Gibraltar Office", "TRADED", 130_000, 5_000, 12, 20),
        _row(JUNE, "Northstar Lines", "BP", "Algeciras", "Spain Office", "TRADED", 40_000, 1_600, 4, 10),
        _row(JUNE, "Meridian Bunkering", "Shell", "Gibraltar", "Gibraltar Office", "INVENTORY", 140_000, 5_000, 15, 30),
        _row(JUNE, "Meridian Bunkering", "BP", "Algeciras", "Spain Office", "INVENTORY", 150_000, 5_000, 14, 20),
        _row(JUNE, "Atlas Tankers", "Shell", "Ceuta", "Gibraltar Office", "TRADED", 66_000, 2_000, 6, 10),
        _row(JUNE, "Orion Offshore", "BP", "Gibraltar", "Gibraltar Office", "TRADED", 12_000, 500, 1, 2),
        _row(JUNE, "Vega Bulk", "Shell", "Ceuta", "Gibraltar Office", "INVENTORY", 5_000, 100, 1, 2),
        # April 2026 only
        _row(APRIL, "Harbor & Sons", "Shell", "Valencia", "Valencia Office", "TRADED", 10_000, 400, 1, 2),
    ]


def _metric(rows, name):
    if name == "GP":
        return fsum(r["GP"] for r in rows)
    if name == "VOLUME":
        return fsum(r["VOLUME"] for r in rows)
    if name == "NUM_WON":
        return fsum(r["NUM_WON"] for r in rows)
    if name == "NUM_INQUIRIES":
        return fsum(r["NUM_INQUIRIES"] for r in rows)
    if name == "NUM_LOST":
        return fsum(r["NUM_INQUIRIES"] - r["NUM_WON"] for r in rows)
    if name == "MARGIN":
        vol = fsum(r["VOLUME"] for r in rows)
        return None if vol == 0 else fsum(r["GP"] for r in rows) / vol
    raise KeyError(name)


@dataclass
class FakeDataClient:
    rows: list[dict] = field(default_factory=multi_office_rows)
    tamper_total: float = 0.0            # added to every metric-query GP, to force a reconciliation failure
    fail_frames_with: Exception | None = None
    calls: list[str] = field(default_factory=list)

    def _select(self, window, filters: Mapping[str, tuple[str, ...]]):
        return [
            r
            for r in self.rows
            if window.start <= r["LIFT_ETA_DATE"] < window.end
            and all(r[col] in values for col, values in filters.items())
        ]

    def list_dimension_values(self, entity, column, search=None, scope_value=None):
        self.calls.append("list_dimension_values")
        return sorted({r[column] for r in self.rows})

    def available_periods(self, entity, scope_value=None):
        self.calls.append("available_periods")
        days = [r["LIFT_ETA_DATE"] for r in self.rows]
        return PeriodRange(start=min(days), end=max(days))

    def list_months(self, entity, filters, scope_value=None):
        self.calls.append("list_months")
        return sorted(
            {
                r["LIFT_ETA_DATE"].replace(day=1)
                for r in self.rows
                if all(r[col] in values for col, values in filters.items())
            }
        )

    def run_metric_query(self, spec: MetricQuerySpec) -> MetricResult:
        self.calls.append("run_metric_query")
        rows = self._select(spec.period, spec.filters)
        values = {m: _metric(rows, m) for m in spec.metrics}
        if "GP" in values and values["GP"] is not None:
            values["GP"] += self.tamper_total
        return MetricResult(entity=spec.entity, period=spec.period, values=values)

    def run_breakdown_query(self, spec):
        raise NotImplementedError("the reporting skill never runs a breakdown")

    def run_frame_query(self, spec: FrameQuerySpec) -> FrameResult:
        self.calls.append("run_frame_query")
        if self.fail_frames_with is not None:
            raise self.fail_frames_with
        groups: dict[tuple[str, ...], list[dict]] = {}
        for r in self._select(spec.period, spec.filters):
            groups.setdefault(tuple(r[c] for c in spec.group_by), []).append(r)
        return FrameResult(
            entity=spec.entity,
            group_by=tuple(spec.group_by),
            rows=[
                FrameRow(keys=keys, values={m: _metric(rs, m) for m in spec.metrics})
                for keys, rs in sorted(groups.items())
            ],
        )

    @contextmanager
    def snapshot(self):
        self.calls.append("snapshot")
        yield self
```

(`PeriodRange` has exactly two fields, `start` and `end`, both `date | None`; `end` is inclusive.)

Create `tests/test_skill.py`:

```python
"""Offline, through the real registry, against FakeDataClient's hand-built
multi-office rows. Every number asserted below is derived by hand from
fake_client.multi_office_rows() -- the derivations are in the comments."""

from datetime import date

import pytest

from poseidon.core.config import Settings
from poseidon.core.identity import UserContext
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.stub import StubProvider
from poseidon.core.llm.types import LLMResponse, ToolCall
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.tasks.reporting.skills.monthly_performance import skill as skill_module
from poseidon.tasks.reporting.skills.monthly_performance.tests.fake_client import (
    FakeDataClient,
    multi_office_rows,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import render
from poseidon.tasks.reporting.skills.monthly_performance.tools.narrate import NARRATE_TOOL

SKILL = "reporting.monthly_performance"
DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"
USER = UserContext(sub="dev|alice", email="alice@example.com", name="Alice", roles=("Poseidon:Sales",))
JUNE = date(2026, 6, 1)


class _FakeStore:
    def __init__(self):
        self.inserted = []

    def insert_run(self, user_sub, run):
        self.inserted.append((user_sub, run))


class _ExplodingLLM:
    def invoke(self, *args, **kwargs):
        raise AssertionError("the model must not be called in this scenario")


@pytest.fixture(autouse=True)
def _pdf_and_today(monkeypatch):
    monkeypatch.setattr(render, "render_pdf", lambda html: b"%PDF-fake")
    monkeypatch.setattr(skill_module, "_today", lambda: date(2026, 10, 14))


@pytest.fixture(scope="module")
def registry():
    return SkillRegistry.discover()


def _ctx(data=None, store=None, llm=None, llm_mode="stub", user=USER):
    settings = Settings(_env_file=None, database_url=DSN, s3_bucket="poseidon-artifacts", llm_mode=llm_mode)
    return SkillContext(
        data=data or FakeDataClient(),
        artifacts=None,
        settings=settings,
        state=ConversationSlots(),
        llm=llm,
        user=user,
        report_store=_FakeStore() if store is None else store,
    )


def _run(registry, ctx, **args):
    base = {"perspective": "customer", "office_value": "Gibraltar Office", "month": "2026-06-01"}
    base.update(args)
    return registry.dispatch(SKILL, base, ctx)


def _payload(result):
    assert result.ok, result.error
    assert [p["kind"] for p in result.parts] == ["report"]
    return result.parts[0]["payload"]


def _tool_reply(narrative_md, questions, insights=None):
    return LLMResponse(
        text="",
        tool_calls=(
            ToolCall(
                id="t1",
                name="reporting.narrate",
                arguments={
                    "narrative_md": narrative_md,
                    "insights": insights
                    or [
                        "Northstar Lines added 1,000 tons.",
                        "Meridian Bunkering lost 2,000 tons.",
                        "The office margin moved to 28.02/ton.",
                    ],
                    "context_notes": [],
                    "suggested_questions": questions,
                },
            ),
        ),
        stop_reason="tool_use",
        input_tokens=1,
        output_tokens=1,
    )


GOOD_NARRATIVE = "Northstar Lines grew GP +30,000 (+30.0%) on volume. Meridian Bunkering fell -60,000 (-30.0%)."
GOOD_QUESTIONS = [
    "Why did GP drop for Meridian Bunkering?",
    "Where else does Northstar Lines lift fuel?",
    "Which deal class drove the margin change?",
]
# Meridian's -60,000 and -30.0% handed to Northstar: two unverified tokens
# (Northstar's own strings are +30,000 and +30.0%; the sign must match).
BAD_NARRATIVE = "Northstar Lines' GP fell 60,000 (-30.0%) on lower volume."


def _live_ctx(script, store=None):
    provider = StubProvider(script)
    settings = Settings(_env_file=None, database_url=DSN, s3_bucket="poseidon-artifacts", llm_mode="live")
    llm = RoleClient(settings, providers={"bedrock": provider})
    return _ctx(store=store, llm=llm, llm_mode="live"), provider


# ---------------------------------------------------------------------------
# the customer office run
# ---------------------------------------------------------------------------


def test_customer_office_run_is_ok_and_carries_the_hand_derived_figures(registry):
    """Gibraltar Office, May -> June 2026. Prior GP 380,000 (100+200+60+20+0 k),
    current 353,000 (130+140+66+12+5 k): -27,000 = -7.1%. Volume 13,700 ->
    12,600: -1,100 = -8.0%. Margin 27.74 -> 28.02 (+0.28). Won 39 -> 35,
    inquiries 66 -> 64, lost 27 -> 29. Win rate 59.1% -> 54.7%."""
    ctx = _ctx()
    payload = _payload(_run(registry, ctx))

    assert payload["payload_version"] == 1 and payload["perspective"] == "customer"
    assert payload["entity"] == {"column": "CUST_NM", "label": "Customer"}
    assert payload["scope"] == {
        "office_column": "CUSTOMER_TEAM_NAME",
        "office_value": "Gibraltar Office",
        "label": "Gibraltar Office (Customer Broker Office)",
    }
    assert payload["periods"]["current"]["label"] == "June 2026"
    assert payload["periods"]["prior"]["label"] == "May 2026"
    assert payload["periods"]["window_kind"] == "complete"
    gp = payload["kpis"]["GP"]["display"]
    assert gp == {"prior": "380,000", "current": "353,000", "change": "-27,000", "pct": "-7.1%"}
    assert payload["kpis"]["VOLUME"]["display"]["change"] == "-1,100"
    assert payload["kpis"]["MARGIN"]["display"] == {"prior": "27.74", "current": "28.02", "change": "+0.28", "pct": "+1.0%"}
    assert payload["kpis"]["WIN_RATE"]["display"]["prior"] == "59.1%"
    assert payload["kpis"]["NUM_LOST"]["display"]["change"] == "+2"
    assert [r["key"] for r in payload["tables"]["entities_by_gp"]["rows"]] == [
        "Meridian Bunkering", "Northstar Lines", "Atlas Tankers", "Orion Offshore", "Vega Bulk", "Kestrel Lines",
    ]
    assert payload["tables"]["entities_by_gp"]["other"] is None
    assert [(m["key"], m["driver"]) for m in payload["movers"]["increases"]] == [
        ("Northstar Lines", "volume-driven"),
        ("Orion Offshore", "new"),
        ("Atlas Tankers", "margin-driven"),
        ("Vega Bulk", "margin-driven"),
    ]
    assert [(m["key"], m["driver"]) for m in payload["movers"]["decreases"]] == [
        ("Meridian Bunkering", "volume-driven"),
        ("Kestrel Lines", "lost"),
    ]
    assert [r["key"] for r in payload["new_lost"]["new"]] == ["Orion Offshore"]
    assert [r["key"] for r in payload["new_lost"]["lost"]] == ["Kestrel Lines"]
    assert payload["tables"]["ports_by_gp"]["rows"][0]["key"] == "Gibraltar"
    # TRADED June = 130,000 + 66,000 + 12,000 = 208,000; INVENTORY = 140,000 + 5,000 = 145,000.
    assert [r["key"] for r in payload["tables"]["deal_class"]["rows"]] == ["TRADED", "INVENTORY"]
    assert payload["narrative"]["llm_md"] is None and payload["narrative"]["grounded"] is True
    assert payload["narrative"]["flags"] == ["model narrative skipped: stub mode"]
    assert payload["narrative"]["template_md"].startswith("**May 2026 to June 2026, Gibraltar Office (Customer Broker Office).**")
    assert payload["suggested_questions"] == [
        "Why did GP change for Meridian Bunkering?",
        "Where else does Meridian Bunkering lift fuel?",
        "What were the top ports in the report?",
    ]
    assert payload["source"] == "synthetic" and payload["generated_by"] == "Alice"
    assert [q["label"] for q in payload["queries"]] == [
        "frame prior", "frame current", "total prior", "total current", "context prior", "context current",
    ]
    assert any(line.startswith("Run: report_run ") for line in payload["proof"])


def test_entity_context_share_offsets_and_reconciliation(registry):
    """Meridian: office 200,000 -> 140,000 (-60,000); book 300,000 -> 290,000
    (-10,000, -3.3%); share 66.7% -> 48.3%; other offices net +50,000; Spain
    Office +50,000 is the one opposite-sign offset; Algeciras +50,000 is the
    rest-of-book port. Northstar: book +20,000, Spain Office -10,000. Atlas:
    one office, share 100.0%, no offsets. Vega: zero prior book GP -> n/a."""
    payload = _payload(_run(registry, _ctx()))
    context = payload["entity_context"]
    meridian = context["Meridian Bunkering"]
    assert meridian["book"]["GP"]["display"] == {"prior": "300,000", "current": "290,000", "change": "-10,000", "pct": "-3.3%"}
    assert meridian["share_of_book"]["display"] == {"prior": "66.7%", "current": "48.3%"}
    assert meridian["reconciliation"]["display"] == {
        "office_change": "-60,000", "other_offices_net": "+50,000", "book_change": "-10,000",
    }
    assert [(o["key"], o["display"]) for o in meridian["offsets"]] == [("Spain Office", "+50,000")]
    assert [(p["key"], p["display"]) for p in meridian["ports_rest_of_book"]] == [("Algeciras", "+50,000")]
    northstar = context["Northstar Lines"]
    assert northstar["book"]["GP"]["display"]["change"] == "+20,000"
    assert [(o["key"], o["display"]) for o in northstar["offsets"]] == [("Spain Office", "-10,000")]
    atlas = context["Atlas Tankers"]
    assert atlas["share_of_book"]["display"] == {"prior": "100.0%", "current": "100.0%"}
    assert atlas["offsets"] == [] and atlas["ports_rest_of_book"] == []
    assert atlas["reconciliation"]["display"]["other_offices_net"] == "flat"
    assert context["Vega Bulk"]["share_of_book"]["display"]["prior"] == "n/a"
    assert context["Vega Bulk"]["share_of_book"]["prior"] is None
    assert context["Kestrel Lines"]["share_of_book"]["display"]["current"] == "n/a"
    assert set(context) == {
        "Northstar Lines", "Orion Offshore", "Atlas Tankers", "Vega Bulk", "Meridian Bunkering", "Kestrel Lines",
    }


def test_every_footer_display_equals_its_kpi_tile(registry):
    payload = _payload(_run(registry, _ctx()))
    kpis = payload["kpis"]
    for name, table in payload["tables"].items():
        footer = table["footer"]
        assert footer["key"] == "Total", name
        for metric in ("GP", "VOLUME", "MARGIN"):
            assert footer["display"][metric] == kpis[metric]["display"], (name, metric)


def test_payload_carries_a_hostile_name_verbatim(registry):
    """Review Focus 2: the engine stores the name as data; the renderer
    (Task 6) is what escapes it."""
    hostile = '<img src="http://evil.example/x.png"> {{ 7*7 }}'
    rows = [dict(r, CUST_NM=hostile) if r["CUST_NM"] == "Atlas Tankers" else r for r in multi_office_rows()]
    store = _FakeStore()
    payload = _payload(_run(registry, _ctx(data=FakeDataClient(rows=rows), store=store)))
    assert any(r["key"] == hostile for r in payload["tables"]["entities_by_gp"]["rows"])
    assert hostile in payload["entity_context"]
    html = store.inserted[0][1].html.decode("utf-8")
    assert "<img" not in html and "&lt;img" in html


def test_run_row_and_artifact(registry):
    store = _FakeStore()
    result = _run(registry, _ctx(store=store))
    payload = _payload(result)
    assert len(store.inserted) == 1
    sub, run = store.inserted[0]
    assert sub == "dev|alice" and run.created_by == "dev|alice"
    assert run.id == payload["run_id"] and run.status == "ok" and run.payload_version == 1
    assert run.perspective == "customer" and run.office_column == "CUSTOMER_TEAM_NAME"
    assert run.office_value == "Gibraltar Office" and run.window_kind == "complete"
    assert run.period_current.start == JUNE and run.period_prior.start == date(2026, 5, 1)
    assert run.pdf == b"%PDF-fake"
    assert run.html.lstrip().startswith((b"<!", b"<html"))
    assert run.narrative_grounded is True and run.source == "synthetic"
    assert [(a.name, a.url, a.mime) for a in result.artifacts] == [
        ("Gibraltar Office June 2026.pdf", f"/api/reports/{payload['run_id']}/pdf", "application/pdf")
    ]
    assert result.proof == payload["proof"]


def test_pdf_failure_is_a_flag_not_a_failed_run(registry, monkeypatch):
    def _boom(html):
        raise OSError("cannot load library 'gobject-2.0-0'")

    monkeypatch.setattr(render, "render_pdf", _boom)
    store = _FakeStore()
    result = _run(registry, _ctx(store=store))
    payload = _payload(result)
    assert "PDF not rendered: OSError" in payload["narrative"]["flags"]
    assert store.inserted[0][1].pdf is None and result.artifacts == []


# ---------------------------------------------------------------------------
# the other perspectives
# ---------------------------------------------------------------------------


def test_supplier_perspective_ranks_suppliers_in_the_supply_office(registry):
    """Med Supply Team is every Shell row: May 360,000 (100+200+60+0 k), June
    341,000 (130+140+66+5 k). One supplier, so the context says share 100%."""
    payload = _payload(_run(registry, _ctx(), perspective="supplier", office_value="Med Supply Team"))
    assert payload["entity"] == {"column": "SUPPLIER_NM", "label": "Supplier"}
    assert payload["scope"]["label"] == "Med Supply Team (Supply Team Office)"
    assert payload["kpis"]["GP"]["display"] == {"prior": "360,000", "current": "341,000", "change": "-19,000", "pct": "-5.3%"}
    assert [r["key"] for r in payload["tables"]["entities_by_gp"]["rows"]] == ["Shell"]
    assert payload["entity_context"]["Shell"]["share_of_book"]["display"] == {"prior": "100.0%", "current": "100.0%"}
    assert payload["suggested_questions"][1] == "Which customers did Shell supply in Med Supply Team?"
    assert "## New and lost suppliers" in payload["narrative"]["template_md"]


def test_book_perspective_has_no_office_and_no_context(registry):
    """Whole book June: 130+40+140+150+66+12+5 = 543,000; May: 100+50+200+100+60+20+0+30 = 560,000."""
    store = _FakeStore()
    result = _run(registry, _ctx(store=store), perspective="book", office_value=None)
    payload = _payload(result)
    assert payload["scope"] == {"office_column": None, "office_value": None, "label": "Whole book"}
    assert payload["entity_context"] is None
    assert payload["kpis"]["GP"]["display"]["current"] == "543,000"
    assert payload["kpis"]["GP"]["display"]["prior"] == "560,000"
    assert [q["label"] for q in payload["queries"]] == ["frame prior", "frame current", "total prior", "total current"]
    assert "Context: none (whole book report)" in payload["proof"]
    assert result.artifacts[0].name == "Whole book June 2026.pdf"
    assert store.inserted[0][1].office_column is None


def test_book_perspective_rejects_an_office_value(registry):
    result = _run(registry, _ctx(), perspective="book", office_value="Gibraltar Office")
    assert result.ok is False and result.error["status"] == 422
    assert "office_value" in result.error["detail"]


def test_customer_perspective_requires_an_office_value(registry):
    result = _run(registry, _ctx(), office_value=None)
    assert result.ok is False and result.error["status"] == 422
    assert "office_value" in result.error["detail"]


# ---------------------------------------------------------------------------
# sparse and empty slices (spec section 10, Review Focus 1)
# ---------------------------------------------------------------------------


def test_office_with_rows_in_the_prior_month_only_is_an_ok_run_with_lost_entities(registry):
    """Malta Office: Solent Ferries in May only. Current is all zero, Solent
    is lost, the footers are zero, no narrative call, questions backfilled."""
    store = _FakeStore()
    ctx = _ctx(store=store, llm=_ExplodingLLM(), llm_mode="live")
    payload = _payload(_run(registry, ctx, office_value="Malta Office"))
    assert payload["kpis"]["GP"]["display"] == {"prior": "30,000", "current": "0", "change": "-30,000", "pct": "-100.0%"}
    rows = payload["tables"]["entities_by_gp"]["rows"]
    assert [(r["key"], r["status"]) for r in rows] == [("Solent Ferries", "lost")]
    assert payload["tables"]["ports_by_gp"]["footer"]["display"]["GP"]["current"] == "0"
    assert payload["movers"]["increases"] == []
    assert [(m["key"], m["driver"]) for m in payload["movers"]["decreases"]] == [("Solent Ferries", "lost")]
    assert payload["narrative"]["flags"] == ["model narrative skipped: no rows in June 2026"]
    assert payload["narrative"]["llm_md"] is None
    assert payload["entity_context"]["Solent Ferries"]["share_of_book"]["display"] == {"prior": "100.0%", "current": "n/a"}
    assert payload["suggested_questions"] == [
        "Why did GP change for Solent Ferries?",
        "Where else does Solent Ferries lift fuel?",
        "What were the top ports in the report?",
    ]
    assert store.inserted[0][1].status == "ok"


def test_no_rows_in_either_month_is_an_ok_run_with_no_data_sections(registry):
    """Valencia Office exists (April rows) but has nothing in May or June."""
    store = _FakeStore()
    ctx = _ctx(store=store, llm=_ExplodingLLM(), llm_mode="live")
    payload = _payload(_run(registry, ctx, office_value="Valencia Office"))
    assert payload["narrative"]["flags"] == ["no data in either month"]
    assert payload["suggested_questions"] == []
    assert all(table["rows"] == [] for table in payload["tables"].values())
    assert payload["movers"] == {"increases": [], "decreases": []}
    assert payload["new_lost"] == {"new": [], "lost": []}
    assert payload["entity_context"] == {}
    assert payload["kpis"]["GP"]["display"] == {"prior": "0", "current": "0", "change": "flat", "pct": "flat"}
    assert "Overall volume was flat, while gross profit was flat." in payload["narrative"]["template_md"]
    assert "Context: none (no movers)" in payload["proof"]
    assert store.inserted[0][1].status == "ok"


# ---------------------------------------------------------------------------
# refusals before any query (spec sections 5.2, 10; Review Focus 5)
# ---------------------------------------------------------------------------


def test_month_absent_from_the_data_is_a_422_and_writes_nothing(registry):
    store = _FakeStore()
    result = _run(registry, _ctx(store=store), month="2026-03-01")
    assert result.ok is False and result.error["status"] == 422
    assert result.error["title"] == "no data for that month"
    assert "March 2026" in result.error["detail"]
    assert store.inserted == []


def test_running_month_as_complete_is_a_422(registry, monkeypatch):
    monkeypatch.setattr(skill_module, "_today", lambda: date(2026, 6, 15))
    result = _run(registry, _ctx())
    assert result.ok is False and result.error["status"] == 422
    assert "not complete yet" in result.error["detail"]


def test_mtd_on_day_one_returns_422_before_any_query(registry, monkeypatch):
    monkeypatch.setattr(skill_module, "_today", lambda: date(2026, 6, 1))
    data = FakeDataClient()
    result = _run(registry, _ctx(data=data), window_kind="mtd")
    assert result.ok is False and result.error["status"] == 422
    assert "no complete days" in result.error["detail"]
    assert data.calls == []


def test_mtd_run_names_month_to_date_in_labels_and_the_file(registry, monkeypatch):
    """June 1-14 vs May 1-14: Northstar, Meridian, Atlas, Orion and Vega all
    lift on the 10th, so the day-matched slices carry the same June rows
    and May rows as the complete run."""
    monkeypatch.setattr(skill_module, "_today", lambda: date(2026, 6, 15))
    result = _run(registry, _ctx(), window_kind="mtd")
    payload = _payload(result)
    assert payload["periods"]["current"]["label"] == "June 1 to 14, 2026 (month to date)"
    assert payload["periods"]["prior"]["label"] == "May 1 to 14, 2026"
    assert payload["periods"]["window_kind"] == "mtd"
    assert payload["kpis"]["GP"]["display"]["current"] == "353,000"
    assert result.artifacts[0].name == "Gibraltar Office June 2026 month to date.pdf"
    assert "window: month to date" in payload["proof"][1]


def test_unknown_office_is_a_422(registry):
    result = _run(registry, _ctx(), office_value="Atlantis Office")
    assert result.ok is False and result.error["status"] == 422
    assert result.error["detail"] == "unknown CUSTOMER_TEAM_NAME value 'Atlantis Office'"


def test_month_not_on_day_one_is_a_422(registry):
    result = _run(registry, _ctx(), month="2026-06-15")
    assert result.ok is False and result.error["status"] == 422


def test_missing_report_store_is_a_structured_500(registry):
    ctx = SkillContext(
        data=FakeDataClient(),
        artifacts=None,
        settings=Settings(_env_file=None, database_url=DSN, s3_bucket="poseidon-artifacts"),
        state=ConversationSlots(),
        user=USER,
    )
    result = _run(registry, ctx)
    assert result.ok is False and result.error["status"] == 500
    assert result.error["title"] == "report store unavailable"


# ---------------------------------------------------------------------------
# failures after the queries (spec section 10)
# ---------------------------------------------------------------------------


def test_reconciliation_failure_is_an_error_and_writes_no_row(registry):
    store = _FakeStore()
    result = _run(registry, _ctx(data=FakeDataClient(tamper_total=1.0), store=store))
    assert result.ok is False and result.error["status"] == 500
    assert result.error["title"] == "reconciliation failed"
    assert "May 2026" in result.error["detail"]
    assert store.inserted == []


def test_reconciliation_tolerates_half_a_cent(registry):
    assert _run(registry, _ctx(data=FakeDataClient(tamper_total=0.004))).ok is True


def test_data_backend_error_is_a_500_and_writes_no_row(registry):
    store = _FakeStore()
    data = FakeDataClient(fail_frames_with=RuntimeError("connection reset"))
    result = _run(registry, _ctx(data=data, store=store))
    assert result.ok is False and result.error["status"] == 500
    assert "connection reset" in result.error["detail"]
    assert store.inserted == []


def test_row_scope_is_resolved_once_and_stamped_on_every_spec(registry, monkeypatch):
    """D16: every spec the skill builds carries the resolved scope value."""
    seen = []
    monkeypatch.setattr(skill_module, "resolve_row_scope_value", lambda entity, user: "SCOPED")

    class _Spy(FakeDataClient):
        def run_frame_query(self, spec):
            seen.append(spec.scope_value)
            return super().run_frame_query(spec)

        def run_metric_query(self, spec):
            seen.append(spec.scope_value)
            return super().run_metric_query(spec)

    assert _run(registry, _ctx(data=_Spy())).ok is True
    assert seen and set(seen) == {"SCOPED"}


# ---------------------------------------------------------------------------
# the model narrative (live mode with a scripted provider)
# ---------------------------------------------------------------------------


def test_live_mode_uses_the_model_narrative_when_it_grounds(registry):
    ctx, provider = _live_ctx([_tool_reply(GOOD_NARRATIVE, GOOD_QUESTIONS)])
    payload = _payload(_run(registry, ctx))
    assert payload["narrative"]["llm_md"] == GOOD_NARRATIVE
    assert payload["narrative"]["grounded"] is True and payload["narrative"]["flags"] == []
    assert payload["suggested_questions"] == GOOD_QUESTIONS
    assert len(provider.calls) == 1
    assert provider.calls[0]["tool_choice"] == "reporting.narrate"
    assert provider.calls[0]["tools"] == [NARRATE_TOOL]
    assert "marginals" in provider.calls[0]["system"]
    content = provider.calls[0]["messages"][0]["content"]
    assert '"353,000"' in content and "353000" not in content
    assert any("grounded (" in line and "of" in line for line in payload["proof"])


def test_grounding_failure_twice_uses_the_template_and_flags_the_count(registry):
    """Review Focus 4: the retry names the offending sentence; the second
    failure withholds the model prose and shows the count."""
    store = _FakeStore()
    ctx, provider = _live_ctx(
        [_tool_reply(BAD_NARRATIVE, GOOD_QUESTIONS), _tool_reply(BAD_NARRATIVE, GOOD_QUESTIONS)], store=store
    )
    payload = _payload(_run(registry, ctx))
    assert len(provider.calls) == 2
    assert BAD_NARRATIVE in provider.calls[1]["messages"][0]["content"]
    assert "failed the figure check" in provider.calls[1]["messages"][0]["content"]
    assert payload["narrative"]["llm_md"] is None
    assert payload["narrative"]["grounded"] is False
    assert payload["narrative"]["flags"] == ["AI narrative withheld: 2 unverified figures"]
    assert payload["suggested_questions"] == GOOD_QUESTIONS
    assert store.inserted[0][1].narrative_grounded is False


def test_grounding_failure_then_success_keeps_the_retry(registry):
    ctx, provider = _live_ctx([_tool_reply(BAD_NARRATIVE, GOOD_QUESTIONS), _tool_reply(GOOD_NARRATIVE, GOOD_QUESTIONS)])
    payload = _payload(_run(registry, ctx))
    assert len(provider.calls) == 2
    assert payload["narrative"]["llm_md"] == GOOD_NARRATIVE and payload["narrative"]["grounded"] is True


def test_malformed_tool_output_uses_the_template_and_flags_it(registry):
    plain = LLMResponse(text="Here is prose instead of a tool call.", tool_calls=(), stop_reason="end_turn", input_tokens=1, output_tokens=1)
    ctx, provider = _live_ctx([plain])
    payload = _payload(_run(registry, ctx))
    assert payload["narrative"]["llm_md"] is None and payload["narrative"]["grounded"] is False
    assert payload["narrative"]["flags"] == ["model narrative failed: malformed tool output"]
    assert len(provider.calls) == 1
    assert len(payload["suggested_questions"]) == 3


def test_provider_error_uses_the_template_and_flags_it(registry):
    error = LLMResponse(text="", tool_calls=(), stop_reason="error", input_tokens=0, output_tokens=0)
    ctx, _ = _live_ctx([error])
    payload = _payload(_run(registry, ctx))
    assert payload["narrative"]["flags"] == ["model narrative failed: provider error"]
    assert payload["narrative"]["grounded"] is False


def test_model_questions_naming_unknown_entities_are_dropped_and_backfilled(registry):
    questions = ["Why did Poseidon Shipping leave?", "Why did GP drop for Meridian Bunkering?", "Any news on Atlantis Office?"]
    ctx, _ = _live_ctx([_tool_reply(GOOD_NARRATIVE, questions)])
    payload = _payload(_run(registry, ctx))
    assert payload["suggested_questions"][0] == "Why did GP drop for Meridian Bunkering?"
    assert len(payload["suggested_questions"]) == 3
    assert not any("Poseidon" in q or "Atlantis" in q for q in payload["suggested_questions"])


def test_context_notes_are_appended_and_grounded(registry):
    reply = _tool_reply(GOOD_NARRATIVE, GOOD_QUESTIONS)
    args = dict(reply.tool_calls[0].arguments, context_notes=["Spain Office took +50,000 of Meridian Bunkering's volume."])
    reply = LLMResponse(text="", tool_calls=(ToolCall(id="t1", name="reporting.narrate", arguments=args),), stop_reason="tool_use", input_tokens=1, output_tokens=1)
    ctx, _ = _live_ctx([reply])
    payload = _payload(_run(registry, ctx))
    assert payload["narrative"]["grounded"] is True
    assert payload["narrative"]["llm_md"].endswith("## Notes\n- Spain Office took +50,000 of Meridian Bunkering's volume.")


def test_no_llm_client_in_live_mode_is_a_flag(registry):
    payload = _payload(_run(registry, _ctx(llm=None, llm_mode="live")))
    assert payload["narrative"]["flags"] == ["model narrative failed: no LLM client"]
    assert payload["narrative"]["grounded"] is False


def test_anonymous_caller_is_recorded_as_dev_anonymous(registry):
    store = _FakeStore()
    payload = _payload(_run(registry, _ctx(store=store, user=None)))
    assert store.inserted[0][0] == "dev|anonymous" and payload["generated_by"] == "dev|anonymous"
```

- [ ] **Step 6: Run the offline skill tests to verify they fail**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider poseidon/tasks/reporting -k test_skill > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-red-skill.txt 2>&1; tail -6 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-red-skill.txt
```

Expected: collection error `ModuleNotFoundError` / `ImportError` on `monthly_performance.skill` (and `FrameResult` if Task 2 is not merged yet, which it is on this branch).

- [ ] **Step 7: Implement the skill**

`task.yml`:
```yaml
id: reporting
title: Reporting
description: >-
  Monthly performance reports over the certified sales view: an office or
  the whole book, this month against the month before, from one snapshot.
enabled: true
```
`schema.py`, `skill.py`, `tools/frames.py`, `tools/context.py`, `tools/payload.py`, `tools/narrate.py` to the Interfaces contract. Implementation notes:
- `skill.run` holds the only `try/except` for `PeriodError`, `ReconciliationError` and the `render_pdf` guard; anything else propagates to the registry's own 500 (the data-backend-error row of spec section 10).
- `tools/frames.run_slices` times each call with `time.perf_counter()` and builds `Timed` with `sql_hash(spec, dialect)`; it never imports `psycopg` or any client, only `core.data`.
- The KPI totals, every `ranked` footer and every `rollup` go through `mom` or `fsum`; nothing in the skill adds two floats with `+` in a loop.
- `tools/payload.build_payload` is the one place payload keys are spelled; `row_from_merged` and `kpi_from_delta` are its two row shapes, and `tools/context.py` reuses `kpi_from_delta` for the `book` deltas.
- `tools/narrate.narrate` builds the computed block from the payload **after** the template narrative has been rendered, so the model sees the same display strings the grounding index holds. The block never contains a raw float: a test asserts `"353000" not in content`.
- The `report` part is the payload dict itself, through `report_part`.
- Module docstrings: `skill.py` maps the spec's pipeline order to the step numbers above and records rulings 11-13; `schema.py` says why `office_value` is validated against the certified values in `run`, not in `Args` (the model never authors it; the flow does, from chips).
- Run `ruff format` on every new file in the package (new files only).

- [ ] **Step 8: Run the offline tests and the whole offline suite to verify they pass**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider poseidon/tasks/reporting tests/test_skill_registry.py tests/test_llm_prompts.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-green-skill.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-green-skill.txt
env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-offline.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-offline.txt
```

Expected: both green. In the task report show the hand derivations for Gibraltar Office's GP (-27,000, -7.1%), Meridian's share (66.7% -> 48.3%) and the two-token count of `BAD_NARRATIVE`.

- [ ] **Step 9: Write the pg goldens**

Create `tests/test_skill_pg.py` (module-level skip identical in shape to `poseidon/tasks/data_qa/skills/metric_query/tests/test_skill.py`'s, probing `synthetic.marine_sales_planning_v` and `public.report_run` and hinting `cd web && npx drizzle-kit migrate` for the latter):

```python
"""Ground truth for reporting.monthly_performance through the real registry
against the seeded Postgres. Expectations are recomputed from
generate(seed=SEED), never hand-copied. The generator gives every customer
exactly one office today, so share_of_book is 100% and offsets are empty;
PR A2's multi-office overlay re-pins those two assertions."""

import datetime as dt
import os
from math import fsum

import psycopg
import pytest
from sqlalchemy import create_engine, text

from poseidon.core.analytics import mom
from poseidon.core.config import Settings
from poseidon.core.data.synthetic_client import SyntheticDataClient, normalize_dsn
from poseidon.core.identity import UserContext
from poseidon.core.llm.prompt_tree import PromptTree
from poseidon.core.parsing.period_labels import month_window
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.reports.store import ReportStore
from poseidon.scripts.generate_synthetic import generate

pytestmark = pytest.mark.pg

SEED = 1391
SALES = "MARINE_SALES_PLANNING_V"
SKILL = "reporting.monthly_performance"
_SETTINGS_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"
_DSN = os.environ.get("DATABASE_URL", "")

# ... module-level skip block copied from metric_query's test_skill.py, extended
# with a `SELECT to_regclass('public.report_run')` probe ...

DATASET = generate(seed=SEED)
CLIENT = SyntheticDataClient(_DSN)
USER = UserContext(sub="dev|report-pg", email=None, name="Report PG", roles=("Poseidon:Sales",))


def _engine_and_role():
    engine = create_engine(_DSN)
    with engine.connect() as conn:
        is_superuser = conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar_one()
    return engine, ("poseidon_app" if is_superuser else None)


@pytest.fixture(scope="module")
def registry():
    return SkillRegistry.discover()


@pytest.fixture(scope="module")
def store():
    engine, role = _engine_and_role()
    yield ReportStore(engine, app_role=role)
    engine.dispose()


def _ctx(store):
    settings = Settings(_env_file=None, database_url=_SETTINGS_DSN, s3_bucket="poseidon-artifacts", llm_mode="stub")
    return SkillContext(
        data=CLIENT, artifacts=None, settings=settings, state=ConversationSlots(), user=USER,
        report_store=store, prompt_tree=PromptTree(),
    )


def _latest_complete_month() -> dt.date:
    today = dt.date.today()
    return max(m for m in CLIENT.list_months(SALES, {}) if month_window(m.year, m.month).end <= today)


def _rows(window, column=None, value=None):
    return [
        r for r in DATASET.sales_rows
        if window.start <= r["LIFT_ETA_DATE"] < window.end and (column is None or r[column] == value)
    ]


def _by(rows, column):
    out = {}
    for r in rows:
        acc = out.setdefault(r[column], {"GP": 0.0, "VOLUME": 0.0})
        acc["GP"] += r["GROSS_PROFIT"]
        acc["VOLUME"] += r["FIXED_TONS"]
    return out


def _run(registry, store, **args):
    result = registry.dispatch(SKILL, args, _ctx(store))
    assert result.ok, result.error
    return result, result.parts[0]["payload"]


def _assert_footers_equal_tiles(payload):
    for name, table in payload["tables"].items():
        for metric in ("GP", "VOLUME", "MARGIN"):
            assert table["footer"]["display"][metric] == payload["kpis"][metric]["display"], (name, metric)
            for side in ("prior", "current"):
                assert mom.reconciles(table["footer"][side][metric] if metric != "MARGIN" else 0.0, payload["kpis"][metric][side] if metric != "MARGIN" else 0.0), (name, metric, side)


def test_customer_office_golden(registry, store):
    month = _latest_complete_month()
    office = sorted({r["CUSTOMER_TEAM_NAME"] for r in DATASET.sales_rows})[0]
    result, payload = _run(registry, store, perspective="customer", office_value=office, month=month.isoformat())

    current = month_window(month.year, month.month)
    prior = month_window(*((month.year, month.month - 1) if month.month > 1 else (month.year - 1, 12)))
    cur_rows, pri_rows = _rows(current, "CUSTOMER_TEAM_NAME", office), _rows(prior, "CUSTOMER_TEAM_NAME", office)
    assert payload["kpis"]["GP"]["display"]["current"] == mom.fmt_value(fsum(r["GROSS_PROFIT"] for r in cur_rows), "money")
    assert payload["kpis"]["GP"]["display"]["prior"] == mom.fmt_value(fsum(r["GROSS_PROFIT"] for r in pri_rows), "money")
    assert payload["kpis"]["VOLUME"]["display"]["current"] == mom.fmt_value(fsum(r["FIXED_TONS"] for r in cur_rows), "tons")
    top = max(_by(cur_rows, "CUST_NM").items(), key=lambda kv: kv[1]["GP"])[0]
    assert payload["tables"]["entities_by_gp"]["rows"][0]["key"] == top
    assert len(payload["tables"]["entities_by_gp"]["rows"]) <= 10
    _assert_footers_equal_tiles(payload)
    for name, context in payload["entity_context"].items():
        assert context["offsets"] == [], name  # one office per customer today (A2 overlay changes this)
        for side in ("prior", "current"):
            assert context["share_of_book"]["display"][side] in ("100.0%", "n/a"), (name, side)
    assert payload["narrative"]["flags"][0] == "model narrative skipped: stub mode"
    assert len(payload["suggested_questions"]) == 3

    run = store.get_run(USER.sub, payload["run_id"])
    assert run is not None and run.status == "ok" and run.payload["run_id"] == payload["run_id"]
    assert store.get_bytes(USER.sub, payload["run_id"], "html") is not None
    pdf = store.get_bytes(USER.sub, payload["run_id"], "pdf")
    if pdf is None:
        assert any(f.startswith("PDF not rendered: ") for f in payload["narrative"]["flags"])
        assert result.artifacts == []
    else:
        assert pdf.startswith(b"%PDF") and result.artifacts[0].url == f"/api/reports/{payload['run_id']}/pdf"


def test_supplier_office_golden(registry, store):
    month = _latest_complete_month()
    office = sorted({r["PRIMARY_SUPPLY_TEAM_OFFICE"] for r in DATASET.sales_rows})[0]
    _, payload = _run(registry, store, perspective="supplier", office_value=office, month=month.isoformat())
    current = month_window(month.year, month.month)
    cur_rows = _rows(current, "PRIMARY_SUPPLY_TEAM_OFFICE", office)
    assert payload["entity"]["label"] == "Supplier"
    top = max(_by(cur_rows, "SUPPLIER_NM").items(), key=lambda kv: kv[1]["GP"])[0]
    assert payload["tables"]["entities_by_gp"]["rows"][0]["key"] == top
    assert payload["scope"]["label"] == f"{office} (Supply Team Office)"
    _assert_footers_equal_tiles(payload)


def test_whole_book_golden(registry, store):
    month = _latest_complete_month()
    _, payload = _run(registry, store, perspective="book", month=month.isoformat())
    current = month_window(month.year, month.month)
    assert payload["entity_context"] is None
    assert payload["kpis"]["GP"]["display"]["current"] == mom.fmt_value(fsum(r["GROSS_PROFIT"] for r in _rows(current)), "money")
    assert len(payload["queries"]) == 4
    _assert_footers_equal_tiles(payload)


def test_month_outside_the_data_is_a_422(registry, store):
    result = registry.dispatch(SKILL, {"perspective": "book", "month": "2019-01-01"}, _ctx(store))
    assert result.ok is False and result.error["status"] == 422
```

Simplify `_assert_footers_equal_tiles` to the display-equality loop if the raw-value line reads awkwardly; the display equality is the pinned property (ruling: the raw sums of different groupings agree to the cent, the display strings agree exactly).

- [ ] **Step 10: Run the pg module, then the whole pg suite**

```bash
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider poseidon/tasks/reporting/skills/monthly_performance/tests/test_skill_pg.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-green-pg.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-green-pg.txt
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-pg-suite.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t9-pg-suite.txt
```

Expected: 4 passed in the module; the whole pg suite green (the Task 5 store tests and Task 2 client tests included). Report the pass/skip counts.

- [ ] **Step 11: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/tasks/reporting poseidon/core/skills poseidon/core/llm/prompts.py poseidon/api/live_chat.py tests/test_skill_registry.py tests/test_llm_prompts.py
cd ..
git add backend/poseidon/tasks/reporting backend/poseidon/core/skills backend/poseidon/core/llm/prompts.py backend/poseidon/api/live_chat.py backend/tests/test_skill_registry.py backend/tests/test_llm_prompts.py
git commit -q -F - <<'EOF'
feat(reporting): the monthly_performance skill, internal to the registry

One snapshot, six certified queries, every figure computed once in
core/analytics, template narrative always, model narrative through a
forced tool call and the entity-scoped grounding check with one retry,
suggested questions backfilled to three, HTML and PDF rendered, the run
stored, a report part returned. SKILL_META["internal"] hides the skill
from the router prompt, tool schemas and /api/skills; dispatch still sees
it. Offline tests run the real pipeline over hand-built multi-office rows;
pg goldens run it through the real registry against the seeded database.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 10: Demo script, docs, verified figures, final gates

**Model:** Sonnet implements; Opus reviews the whole branch after this task (the final review stays on the most capable model).

**Files:**
- Create: `backend/poseidon/scripts/demo_report.py`
- Test: `backend/tests/test_demo_report.py` (new, offline)
- Modify: `docs/architecture/02-backend-skills.md` (sections 1, 2, 3), `03-llm-routing.md` (sections 1, 4), `04-data-ontology.md` (section 3), `05-auth-identity.md` (section 4), `07-infrastructure.md` (section 2), `CLAUDE.md` (Commands header, figures, one Conventions bullet, one Gotcha)

**Interfaces:**
- Consumes: `SkillRegistry.discover().dispatch`, `SyntheticDataClient`, `ReportStore`, `build_engine`, `PromptTree`, `Settings`.
- Produces:
  ```python
  # demo_report.py
  def parse_args(argv: list[str] | None) -> argparse.Namespace
      # --perspective {customer,supplier,book} (required) --office TEXT --month YYYY-MM (required)
      # --out PATH (required; the PDF, or the HTML with .html when no PDF was rendered)
  def render_summary(payload: Mapping[str, Any], artifacts: Sequence[ArtifactRef]) -> str
      # ASCII text: header (scope, periods, source, run id), a KPI table (metric, prior, current,
      # change, pct), one line per ranked table "footer == tile: yes/no" for GP/VOLUME/MARGIN,
      # movers with drivers, new/lost, flags, suggested questions, proof lines
  def main(argv: list[str] | None = None) -> int
      # 2 when DATABASE_URL is missing or --month malformed; 1 with the problem printed when the
      # dispatch returns ok=False; 0 after writing the file and printing the summary
  ```
  `main` reads `DATABASE_URL` first (missing -> exit 2 with a message on stderr, before anything else is built), then builds `Settings(_env_file=None, database_url=url, s3_bucket="poseidon-artifacts", data_backend="synthetic", llm_mode="stub")` explicitly (no dotenv, no other environment: stub mode is forced so the demo never spends a model call), `SyntheticDataClient(settings.database_url)`, `ReportStore(build_engine(settings.database_url), app_role=settings.database_app_role)`, `PromptTree()`, a `UserContext(sub="dev|demo", email=None, name="Demo", roles=("Poseidon:Sales",))`, and dispatches through `SkillRegistry.discover()`. Formatting is plain aligned ASCII like `demo_query.py` (reuse its `_render_table` by importing it; do not copy it).

- [ ] **Step 1: Write the failing offline test**

Create `backend/tests/test_demo_report.py`:

```python
import pytest

from poseidon.core.skills.context import ArtifactRef
from poseidon.scripts import demo_report
from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import minimal_payload


def test_parse_args_requires_a_yyyy_mm_month():
    args = demo_report.parse_args(["--perspective", "book", "--month", "2026-06", "--out", "x.pdf"])
    assert args.month == "2026-06" and args.office is None
    with pytest.raises(SystemExit):
        demo_report.parse_args(["--perspective", "book", "--month", "2026-6", "--out", "x.pdf"])


def test_render_summary_shows_tiles_footers_movers_and_flags():
    payload = minimal_payload()
    text = demo_report.render_summary(payload, [ArtifactRef("x.pdf", "/api/reports/1/pdf", "application/pdf")])
    assert payload["scope"]["label"] in text
    assert payload["kpis"]["GP"]["display"]["current"] in text
    assert "entities_by_gp" in text and "footer == tile: yes" in text
    for mover in payload["movers"]["increases"]:
        assert mover["key"] in text and mover["driver"] in text
    for question in payload["suggested_questions"]:
        assert question in text
    assert text.isascii()


def test_main_without_database_url_exits_2(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert demo_report.main(["--perspective", "book", "--month", "2026-06", "--out", "x.pdf"]) == 2
    assert "DATABASE_URL" in capsys.readouterr().err
```

- [ ] **Step 2: Run to verify it fails, implement, run to verify it passes**

```bash
cd backend
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_demo_report.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t10-red.txt 2>&1; tail -4 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t10-red.txt
```

Expected: `ModuleNotFoundError: No module named 'poseidon.scripts.demo_report'`.

Implement `demo_report.py` to the Interfaces contract (module docstring in `demo_query.py`'s style, with the usage line). Run `ruff format` on it.

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_demo_report.py > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t10-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/t10-green.txt
```

Expected: 3 passed.

- [ ] **Step 3: Run the demo against the compose database, three ways**

```bash
mkdir -p ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -m poseidon.scripts.demo_report --perspective customer --office Rotterdam --month 2026-06 --out ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo/rotterdam-2026-06.pdf > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo/customer.txt 2>&1; tail -40 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo/customer.txt
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -m poseidon.scripts.demo_report --perspective supplier --office Singapore --month 2026-06 --out ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo/singapore-supply-2026-06.pdf > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo/supplier.txt 2>&1; tail -20 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo/supplier.txt
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -m poseidon.scripts.demo_report --perspective book --month 2026-06 --out ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo/book-2026-06.pdf > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo/book.txt 2>&1; tail -20 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/demo/book.txt
```

Expected: exit 0 each time, every `footer == tile` line says `yes`, and either three PDFs exist or the output names the `PDF not rendered` flag and three `.html` files exist instead (the host has no Pango; the container does). Paste the customer summary into the task report in full. The reviewer opens the written HTML (or PDF) and checks that the KPI tiles match the printed summary rather than trusting the summary text.

- [ ] **Step 4: Docs**

Each edit is a short paragraph or a few lines in the named section, in the document's existing voice; nothing is restructured:

- `02-backend-skills.md` section 1: add `reporting/` to the tree with one skill `monthly_performance/` (tools `periods.py frames.py context.py narrative_template.py grounding.py questions.py narrate.py payload.py render.py`, tests), marked "internal: dispatched by the report flow, never by the router"; under "Visibility rules" add: a skill whose `SKILL_META["internal"]` is true is registered but withheld from `tool_schemas`, the router prompt and `/api/skills` (`SkillRegistry.router_skill_ids`); `dispatch` and `get` still see it (Phase 17 PR A1). Section 2 step 3: add "`internal` is a bool". Section 3 `SkillContext`: add `report_store (ReportStore over report_run, doc 05 section 4) · prompt_tree (PromptTree over backend/prompts/, doc 03 section 4)`.
- `03-llm-routing.md` section 1 tree: add `prompt_tree.py  # PromptTree: resolve("reporting/narrate") -> versioned XML system prompt (D46, first step)`; add one bullet after D33: `tool_choice` on `RoleClient.invoke`/`LLMProvider.invoke` forces a named tool (Bedrock `toolConfig.toolChoice`); the report narrative is the first forced call. Section 4: add the `backend/prompts/<area>/<name>.xml` line to the tree, say the tree lives under `backend/` because that is the runtime root in every habitat (ruling 1), and that skill-owned `prompts/*.md` migrate later (spec section 13).
- `04-data-ontology.md` section 3: extend the Protocol listing with `list_months(entity, filters, scope_value=None) -> list[date]`, `run_frame_query(spec: FrameQuerySpec) -> FrameResult`, `snapshot() -> ContextManager[DataClient]`; one sentence on `FrameQuerySpec` (grouped multi-metric slice, no ORDER BY/LIMIT, rolled up in Python, WIN_RATE refused) and one on the snapshot (REPEATABLE READ, read-only, D54).
- `05-auth-identity.md` section 4: a paragraph after the D28 decision: **Shared org documents (D65, D69).** `report_run` carries no `user_sub` policy; any `Poseidon:Sales` user reads any run; writes go through `rls_transaction` as the generating user under `poseidon_app` with `SELECT, INSERT` only. The `created_by` column is attribution, not an access boundary.
- `07-infrastructure.md` section 2: one sentence that report HTML and PDF bytes live in Postgres (`report_run`) in every habitat, so the image needs no object store for reports; MinIO/S3 keeps the brief PDFs until the carry-forward in the Phase 17 spec section 13 moves them.
- `CLAUDE.md`: the Commands header date and label become `verified <date> (Phase 17 PR A1)`; every figure in that block and in the two verification paragraphs is replaced by the numbers from Step 5's runs (never estimated); add under Conventions: "**Internal skills.** `SKILL_META['internal'] = True` registers a skill the router never sees (`router_skill_ids`, `tool_schemas`, `/api/skills` all omit it) while `dispatch` still runs it. `reporting.monthly_performance` is the first."; add under Gotchas: "**WeasyPrint on the host.** `render_pdf` raises `OSError` without Pango/Cairo; the reporting skill records `PDF not rendered: OSError` and stores HTML only. Run the `pdf`-marked tests and the real PDF check in the container."

- [ ] **Step 5: Final gates**

```bash
env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-offline.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-offline.txt
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-pg.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-pg.txt
.venv/Scripts/python.exe -m ruff check . > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-ruff.txt 2>&1; tail -2 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-ruff.txt
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/contracts > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-contracts.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-contracts.txt
cd ../web
npm test > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-web.txt 2>&1; tail -6 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-web.txt
npx tsc --noEmit > ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-tsc.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/final-tsc.txt
cd ..
git status --short contracts/
```

Expected: offline green with the new counts; pg green; ruff clean; `tests/contracts` green and `git status --short contracts/` empty (this PR regenerates nothing); web vitest and tsc clean. Copy the exact pass/skip/deselect numbers into `CLAUDE.md` before committing. If `tests/contracts` shows a diff, STOP and report; do not regenerate.

- [ ] **Step 6: Commit**

```bash
git add backend/poseidon/scripts/demo_report.py backend/tests/test_demo_report.py docs/architecture/02-backend-skills.md docs/architecture/03-llm-routing.md docs/architecture/04-data-ontology.md docs/architecture/05-auth-identity.md docs/architecture/07-infrastructure.md CLAUDE.md
git commit -q -F - <<'EOF'
docs(phase-17): demo_report script, architecture docs for PR A1, verified figures

demo_report runs the report engine through the registry against the
compose database in stub mode and writes the PDF (or the HTML when the
host has no Pango). Docs 02, 03, 04, 05 and 07 record the reporting task,
the internal flag, the prompt tree and forced tool call, the frame query
and snapshot, report_run as a shared org document, and report bytes in
Postgres. CLAUDE.md carries the figures from the final gates.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

## Self-review (writing-plans checklist, run 2026-10-14)

**1. Spec coverage, PR A1 scope.** Spec section 5.1 Args: Task 9 `schema.py`. 5.2 periods and `list_months`: Tasks 2, 4, 9 (the D50 presence check is on the whole entity, so an office that went quiet is still an `ok` run, Review Focus 1). 5.3 six queries in one snapshot with row scope: Tasks 1, 2, 9 (`test_row_scope_is_resolved_once_and_stamped_on_every_spec`). 5.4 math and the footer-equals-KPI property: Tasks 3, 9. 5.5 content: Task 9 (`build_entity_context`, `ranked` tables, movers, new/lost, three questions). 5.6 template narrative, forced tool call, grounding with retry and the two fixtures: Tasks 7, 8, 9. 5.7 payload v1: Task 6 contract, Task 9 builder. 6.6 prompts tree: Task 7. 7.1 `report_run` and the store: Task 5. 7.3 rendering with escaping and no fetches: Task 6. Section 10 failure rows that belong to the engine: reconciliation, data backend error, empty office slice, month not present, model error or malformed output, grounding twice (Task 9 tests, one each). Section 11 row A automated items in A1: math transcription, footer property, SQL snapshots both dialects, grounding fixtures, HTML/PDF tests, store round trip. Section 12 docs for what A1 touches: Task 10. **Deferred to PR A2 by the owner ruling:** 6.1-6.5, 6.7, 6.8, 6.9, 7.2, the contracts corpus report turn, the `report` renderer, docs 01 and 08, the `conversation_id` on the run row.

**2. Placeholder scan.** No TBD/TODO. Every test is written out. The two "copy from the existing file" instructions (Task 1 row-scope fixture, Task 5 pg fixtures) name the exact source lines and the single new assertion. Task 9's pg module says what to copy for the skip block and names the extra probe.

**3. Type and name consistency, fixed inline during this review.** `mom.fmt_pct` now checks `change == 0` before `pct is None`, so a zero-to-zero KPI on a no-data run reads `flat`, not `new` (one assertion added to Task 3's formatting test). `mom._sum_rows` now says `math.fsum` so a footer summed in key order equals a KPI summed in frame order (Task 3's own property test depends on it). The Task 6 contract now says `suggested_questions` is `[]` only for a no-data run, `share_of_book.display` renders `n/a` for a null side, and `entity_context` is `{}` for an office with no movers. Task 9 uses `FrameResult.group_by` and `FrameRow.keys/values` exactly as Task 2 defines them, `mom.ranked(..., noun=...)` as Task 3 defines it, `ResolvedPeriods.label_current/label_prior/window_kind` as Task 4 defines them, `NewRun` field names as Task 5 defines them, `render.render_html/render_pdf` as Task 6, `PromptTree().resolve("reporting/narrate").system/.id/.version` and `tool_choice=` as Task 7, and the Task 8 names verbatim. `StubProvider` records `tool_choice` per Task 7, which `test_live_mode_uses_the_model_narrative_when_it_grounds` reads.

**4. Review Focus.** All five lines are pinned to named tests that exist in the plan: 1 -> Task 9 `test_office_with_rows_in_the_prior_month_only_is_an_ok_run_with_lost_entities`; 2 -> Task 6 `test_hostile_entity_name_renders_as_text_and_fetches_nothing` and Task 9 `test_payload_carries_a_hostile_name_verbatim`; 3 -> Task 3 `test_zero_prior_margin_yields_a_null_pct_and_a_margin_driver`; 4 -> Task 8 `test_must_reject_fixture_attributes_customer_b_figure_to_customer_a` and Task 9 `test_grounding_failure_twice_uses_the_template_and_flags_the_count`; 5 -> Task 4 `test_mtd_on_the_first_of_the_month_refuses` and Task 9 `test_mtd_on_day_one_returns_422_before_any_query`.

**Rulings added by Tasks 8-9 (append to "Rulings made in this plan" for the morning-review list):**

11. **KPI figures are in scope for every sentence.** D67's "when the sentence names no entity" clause is read as: entity figures need the entity named in the sentence or earlier in its paragraph; KPI and footer figures never do. The mockup's own summary names the office and a port beside the office totals and must pass.
12. **No `report_run` row on any failure in A1.** Reconciliation and data errors write nothing; `status = 'error'` is reserved for Phase 18's queued runs.
13. **A PDF render failure is a flag, not a failed run.** The HTML is stored, `pdf` is null, the flag reads `PDF not rendered: <ExceptionType>`, no artifact is returned. The host has no Pango; the container does.
14. **The month-presence check ignores the office filter.** "No data for that month" means the dataset has no rows in that month at all (D50). An office with no rows in one or both months is an `ok` run with zero sections (spec section 10, Review Focus 1).
15. **`created_by` without an identity is `dev|anonymous`.** Only the demo script and hand-built test contexts hit this; the chat flow always has a user.
