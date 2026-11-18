# Phase 17 PR B — `metric_query` extensions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a sales user slice certified metrics by several dimensions and by month/quarter/year, order them, ask for a share of the total under the C04 rule, get a deterministic "slice it another way" clarification, and have every remaining §7.6 router case pinned — all routable offline through the stub-mode dev router.

**Architecture:** `data_qa.metric_query`'s `Args` grows additively (`group_by` as a list, `order_by`/`order`, `share_of_total`). A single plain dimension keeps today's `BreakdownQuerySpec` SQL; two or more columns or any time bucket go through `FrameQuerySpec` (which gains `DATE_TRUNC` buckets) with ordering and the row cap done in Python inside the skill. Share of total runs two `MetricQuerySpec`s built in the C04 order. A lexicon-driven scope hinter and a dimension-unspecified parse issue extend the deterministic parser; the orchestrator turns the issue into chips. The dev router learns the PR B cues so the browser gate runs in stub mode (Carlos has no Bedrock access).

**Tech Stack:** Python 3.14 (`backend/.venv`), pydantic 2, SQLAlchemy-free deterministic query builder, pytest 9.1.1; no web changes (`TablePart.tsx` renders any column count).

**Spec:** `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §8.2, §11 row B, §12, §13; the reports design `docs/superpowers/specs/2026-09-04-monthly-performance-reports-design.md` §7.2, §7.3, §7.6; C04 in `docs/superpowers/specs/2026-09-05-monthly-performance-reports-codex-review.md`. Decision log through D74 in `docs/architecture/00-overview.md`; A2 rulings in `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/morning-review.md`.

**Branch:** `phase-17-metric-query-extensions`, created exactly as `git checkout -b phase-17-metric-query-extensions main` with `main` at `2f28751`. One PR.

**Model split (recorded per the owner rule):** Fable supervises only. Opus implements Tasks 2, 3, 4 and 7 (new SQL, the ordering/cap rule, the C04 order, the dev router) and reviews those four diffs plus the whole branch. Sonnet implements Tasks 1, 5, 6, 8, 9 and 10 and reviews them. The scope hinter (Task 5) is Sonnet, not Opus as the handoff suggested: it is a lexicon table and one rendered line.

---

## Global Constraints

- **Python:** always `backend/.venv/Scripts/python.exe`, never a bare `python`.
- **Offline suite:** `cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live'`. Every pytest invocation in this plan carries `env -u PERPLEXITY_API_KEY` and the full marker exclusion. **Never pipe pytest**; redirect to a file in the ledger directory and read the file.
- **pg suite** (`-m pg`, serial, compose `db` on host 5434 via `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/compose.a2.yml` because another project holds 9000): run by Tasks 6, 9 and 10 (chat text and proof text change). Never parallelise it.
- **`router_live`** cases are paid Bedrock calls; they are never run in this PR (Carlos has no credentials).
- **Never run `ruff format`.** `ruff check .` must stay clean.
- **Frozen surfaces:** ontology and metric formulas, Alembic 0001–0010, the SSE envelope and the ten part kinds, `contracts/turns`, identity subs and RLS wiring. Nothing here touches them. No new part kinds: every new shape is a `table` or `text` part.
- **Default output byte-identical.** A single plain `group_by` with default ordering renders the same SQL and the same proof as today; every existing snapshot and golden holds unless a task names it.
- **ASCII-only source** in `core/parsing/*`, `core/chat/dev_router.py`, `core/chat/orchestrator.py`, `core/llm/*` and their tests (existing byte-pin tests).
- **Commits:** before each commit run `git rev-parse --abbrev-ref HEAD` and confirm `phase-17-metric-query-extensions`; after, `git log --oneline main..HEAD` shows only this branch's work. If either is wrong, stop and report; do not improvise. Commit messages with quotes go through a scratch file and `git commit -F`. End every commit message with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **TDD and evidence:** the implementer writes the failing test first, runs it, explains why that failure is the right one, implements, runs green, and its report is the evidence. The reviewer never re-runs the suite.
- **Ledger:** `.superpowers/sdd/2026-10-18-phase-17-prB-metric-query-extensions/` (progress.md, per-task reports, red/green output files, morning-review.md). Untracked by `.superpowers/sdd/.gitignore`.

## Execution model (owner rule)

Carlos authorised on 2026-10-18: "execute when done, subagent driven". Subagent-driven development: one implementer per task with the model split above, a fresh reviewer per diff, fix rounds until clean, then a whole-branch Opus review. Push the branch and open the PR only when Carlos says so in the executing chat; never merge.

## Owner rulings (2026-10-18, binding)

1. **Owner gate runs offline.** The dev router is extended with the PR B cues (Task 7); no live Bedrock gate in this PR.
2. **Dev router reach:** gate phrases plus the slice chips: `by month/quarter/year`, `by <one of the seven cuts>`, `how much of / share of / % of / portion of / makes up` (share of total on the carried office column), and a group-by cue with no customer resolved this turn uses the carried customer. Nothing else in its behaviour table moves.
3. **Slice chips:** the seven business cuts plus "by month": Customer, Port, Supplier, Deal class, Customer office, Supplier office, Ship type, Month. Each sends `break that down by <cut>`.
4. **A2 leftovers 3, 4, 5, fixed as recommended:** compare header `Group by: CUST_NM (top 5 shown; merged over the top 50 per window)`; singular `1 row remaining`; the lookup KPI section becomes a `[KPI, Prior, Current, Change, %]` table from the payload's display strings.
5. **Row cap:** `top_n` ranks the non-time keys by the order metric summed over the period; every bucket for those keys is shown, chronologically. A plain `[MONTH]` shows every month.

## Rulings made in this plan (for the morning-review list)

1. **Path rule.** `group_by` of exactly one certified dimension builds a `BreakdownQuerySpec` (SQL unchanged, plus `ASC` when asked). Anything else (two or three columns, or any `MONTH`/`QUARTER`/`YEAR`) builds a `FrameQuerySpec`; ordering and the cap are Python.
2. **At most one time bucket** per `group_by`; a repeated column is a 422; a bucket is only ever a group-by column (a filter on `MONTH` fails the builder's own "not a dimension" check).
3. **Refusals, 422 with the skill's own text:** `WIN_RATE` on the frame path (`WIN_RATE cannot be broken down by more than one column or by a time bucket; ask for NUM_WON and NUM_INQUIRIES instead`); `compare_period` with a multi-column or bucketed `group_by` (`compare one dimension at a time: compare_period needs a single plain group_by column`); `order_by` outside `metrics`; `share_of_total` with `group_by` or `compare_period`; a `share_of_total` column absent from the effective filters.
4. **Bucket labels:** `Jan 2025`, `Q1 2025`, `2025`; column headers `Month`, `Quarter`, `Year`. The data client hands bucket keys back as ISO `YYYY-MM-DD` strings (the synthetic client stringifies `date` cells), the skill labels them.
5. **Only buckets with rows appear.** A port with no rows in March has no March row; nothing is zero-filled.
6. **Proof lines.** Frame path: `Group by: LOC_NM, MONTH (top 5 keys; every bucket)` or `Group by: LOC_NM, SUPPLIER_NM (top 5)`, then `Order: GP desc`, then `Rows: N`. Single-column path gains `Order: <metric> <dir>` only when the order is not the default, so existing proofs stay byte-identical. Share: `Selection filters: ...`, `Total filters: ...`, `Share of total: removed CUSTOMER_TEAM_NAME`, `Metrics: N values`; the office-scope line stays last.
7. **Share rendering:** one decimal, `n/a` when the total is zero or null; `Selection`/`Total` cells rounded with `format_parts._round`.
8. **Pass-through** after a frame-path breakdown captures the first non-time column of the rendered table: the table always puts the bucket first, so the column index is 1 when a bucket is present and 0 otherwise, regardless of the order the router listed `group_by`; a pure `[MONTH]` breakdown leaves pass-through untouched. (Corrected during execution: the original text keyed the index off `group_by` order, which would have carried `Jan 2025` as a filter value for `["LOC_NM", "MONTH"]`.)
9. **Scope hint precedence:** when both a `share` phrase and a `book` phrase match, `share` wins (the more specific ask).
10. **Slice clarification text:** `Which dimension should I slice by?` (owner may reword in the morning review). The issue fires only when the message has a slice phrase and no `by <word>`.
11. **"Compare to June instead" and "slice it another way" are not router decisions.** They are listed in `routing_cases.yml` with an `execution` block naming the test that pins them (`parser_case` / `orchestrator_case`); the stub and live executors skip them.
12. **D73's top-50 merge stands.** Lifting the cap would route the compare path through frames and lose `WIN_RATE`; the header reword (owner ruling 4) is the fix for the carry-forward. The "51st key reads lost/new" artefact remains and stays disclosed by the `merged over the top 50 per window` proof line.
13. **The eleven §7.6 cases.** A2's "break that down by customer" is the supplier variant; the §7.6 deal-class case over `CUST_NM` pass-through is added here, so this PR adds eight yml entries (six stub scripts, two pointer entries).

## Review Focus

Inputs the spec implies but does not spell out, each pinned to a test in the task that owns the code:

1. **A customer with no rows in one month of a bucketed breakdown** must not appear as a zero row or shift another key's rank. (Task 3 `test_frame_breakdown_omits_missing_buckets_and_ranks_by_period_total`.)
2. **`share_of_total` naming the office column when no office is carried** must 422 with the column list in the message, never divide by the same filters and print 100%. (Task 4 `test_share_of_total_column_absent_from_effective_filters_is_a_422`.)
3. **The new `Scope hint:` state-block line** must not change any dev router decision. (Task 5 `test_scope_hint_line_does_not_change_the_dev_router_decision`.)
4. **"slice it by port"** (a slice phrase that names a dimension) must not clarify. (Task 6 `test_slice_phrase_with_a_by_cue_raises_no_issue`.)
5. **"by customer office"** must map to `CUSTOMER_TEAM_NAME`, not to `CUST_NM` plus a stray word. (Task 7 `test_longer_cue_wins_over_its_prefix`.)

---

## File structure

**Modify**
- `backend/poseidon/tasks/data_qa/skills/metric_query/schema.py` — `group_by: str | list[str] | None`, `order_by`, `order`, `share_of_total`, validators.
- `backend/poseidon/tasks/data_qa/skills/metric_query/tools/build_spec.py` — path rule; `BreakdownQuerySpec.order`; `FrameQuerySpec` for the frame path.
- `backend/poseidon/tasks/data_qa/skills/metric_query/tools/format_parts.py` — optional `Order:` line; `_header_proof` grows `order` awareness.
- `backend/poseidon/tasks/data_qa/skills/metric_query/tools/compare_breakdown.py` — header reword (owner ruling 4).
- `backend/poseidon/tasks/data_qa/skills/metric_query/skill.py` — dispatch to the frame path and the share path; refusals.
- `backend/poseidon/core/data/specs.py` — `BreakdownQuerySpec.order`.
- `backend/poseidon/core/data/query_builder.py` — `TIME_BUCKETS`, bucket rendering in `build_frame_query`, `ASC` in `build_breakdown_query`.
- `backend/poseidon/core/data/synthetic_client.py` — `_frame_result` stringifies `date` keys.
- `backend/poseidon/core/parsing/lexicon.py` — `SCOPE_HINTS`.
- `backend/poseidon/core/parsing/types.py` — `ParsedTurn.scope_hint`.
- `backend/poseidon/core/parsing/pipeline.py` — scope hint stage; `dimension_unspecified` issue.
- `backend/poseidon/core/llm/prompts.py` — `Scope hint:` line.
- `backend/poseidon/core/chat/orchestrator.py` — slice clarify branch; pass-through first non-time column.
- `backend/poseidon/core/chat/dev_router.py` — cue tables, carried office, carried customer rule.
- `backend/poseidon/tasks/reporting/skills/report_lookup/tools/sections.py` — singular remaining; KPI table.
- `backend/tests/routing_cases.yml`, `backend/tests/test_llm_loop.py` — eight cases; harness.
- Tests beside each module (named per task).
- Docs: `docs/architecture/00-overview.md`, `02-backend-skills.md`, `03-llm-routing.md`, `04-data-ontology.md`, `08-build-phases.md`, the spec §8.2 addendum, `CLAUDE.md`.

**Create**
- `backend/poseidon/core/parsing/scope_hinter.py`
- `backend/poseidon/tasks/data_qa/skills/metric_query/tools/frame_breakdown.py`
- `backend/poseidon/tasks/data_qa/skills/metric_query/tools/share_of_total.py`

---

### Task 1: `Args` growth and the path rule in `build_spec` (Sonnet)

**Files:**
- Modify: `backend/poseidon/tasks/data_qa/skills/metric_query/schema.py`
- Modify: `backend/poseidon/core/data/specs.py` (add `order` to `BreakdownQuerySpec`)
- Modify: `backend/poseidon/core/data/query_builder.py` (`ASC`/`DESC` from `spec.order`; add `TIME_BUCKETS`)
- Modify: `backend/poseidon/tasks/data_qa/skills/metric_query/tools/build_spec.py`
- Test: `backend/poseidon/tasks/data_qa/skills/metric_query/tests/test_tools.py`, `backend/tests/test_query_builder_snapshots.py`

**Interfaces:**
- Produces: `Args.group_by: list[str] | None` after validation (a string input is wrapped); `Args.order_by: str | None`; `Args.order: Literal["desc","asc"] = "desc"`; `Args.share_of_total: list[str] = []`. `query_builder.TIME_BUCKETS: dict[str, str] = {"MONTH": "month", "QUARTER": "quarter", "YEAR": "year"}`. `BreakdownQuerySpec.order: str = "desc"`. `build_spec(args)` returns `MetricQuerySpec | BreakdownQuerySpec | FrameQuerySpec`; `is_frame_path(group_by: list[str] | None) -> bool`.
- Consumed by Tasks 2, 3, 4, 7, 8.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_tools.py`, in the `Args`/`build_spec` section)

```python
from poseidon.core.data.specs import FrameQuerySpec
from poseidon.core.data.query_builder import TIME_BUCKETS
from ..tools.build_spec import is_frame_path


def test_args_group_by_string_is_wrapped_into_a_list():
    args = Args(metrics=["GP"], period=_APRIL_ARG, group_by="CUST_NM")
    assert args.group_by == ["CUST_NM"]


def test_args_group_by_accepts_up_to_three_entries_and_one_bucket():
    args = Args(metrics=["GP"], period=_APRIL_ARG, group_by=["LOC_NM", "SUPPLIER_NM", "MONTH"])
    assert args.group_by == ["LOC_NM", "SUPPLIER_NM", "MONTH"]


@pytest.mark.parametrize(
    "group_by, fragment",
    [
        ([], "one to three"),
        (["A", "B", "C", "D"], "one to three"),
        (["LOC_NM", "LOC_NM"], "repeats"),
        (["MONTH", "YEAR"], "at most one time bucket"),
    ],
)
def test_args_group_by_rejects_bad_shapes(group_by, fragment):
    with pytest.raises(ValidationError) as excinfo:
        Args(metrics=["GP"], period=_APRIL_ARG, group_by=group_by)
    assert fragment in str(excinfo.value)


def test_args_order_defaults_and_accepts_asc():
    args = Args(metrics=["GP", "VOLUME"], period=_APRIL_ARG)
    assert (args.order_by, args.order) == (None, "desc")
    assert Args(metrics=["GP"], period=_APRIL_ARG, order="asc").order == "asc"


def test_args_order_by_must_be_a_requested_metric():
    with pytest.raises(ValidationError) as excinfo:
        Args(metrics=["GP"], period=_APRIL_ARG, order_by="VOLUME")
    assert "order_by" in str(excinfo.value)


@pytest.mark.parametrize("extra", [{"group_by": "CUST_NM"}, {"compare_period": _PRIOR_ARG}])
def test_args_share_of_total_cannot_combine_with_group_by_or_compare(extra):
    with pytest.raises(ValidationError) as excinfo:
        Args(metrics=["GP"], period=_APRIL_ARG, share_of_total=["CUSTOMER_TEAM_NAME"], **extra)
    assert "share_of_total" in str(excinfo.value)


def test_is_frame_path_rule():
    assert is_frame_path(None) is False
    assert is_frame_path(["CUST_NM"]) is False
    assert is_frame_path(["MONTH"]) is True
    assert is_frame_path(["LOC_NM", "MONTH"]) is True
    assert is_frame_path(["LOC_NM", "SUPPLIER_NM"]) is True


def test_build_spec_single_dimension_keeps_the_breakdown_spec_with_order():
    spec = build_spec(
        Args(metrics=["GP", "VOLUME"], period=_APRIL_ARG, group_by=["LOC_NM"], order_by="VOLUME", order="asc")
    )
    assert isinstance(spec, BreakdownQuerySpec)
    assert (spec.group_by, spec.order_by_metric, spec.order) == ("LOC_NM", "VOLUME", "asc")


def test_build_spec_frame_path_returns_a_frame_spec_in_group_by_order():
    spec = build_spec(Args(metrics=["GP"], period=_APRIL_ARG, group_by=["LOC_NM", "MONTH"]))
    assert isinstance(spec, FrameQuerySpec)
    assert spec.group_by == ("LOC_NM", "MONTH")
    assert set(TIME_BUCKETS) == {"MONTH", "QUARTER", "YEAR"}
```

Use the file's existing `_APRIL_ARG`/`_PRIOR_ARG`-style `PeriodArg` constants (the office tests define `_PRIOR_ARG`; add `_APRIL_ARG = PeriodArg(start=dt.date(2026, 4, 1), end=dt.date(2026, 5, 1))` next to it if no April constant exists). Import `ValidationError` from `pydantic`.

Builder snapshot (append to `backend/tests/test_query_builder_snapshots.py` after the breakdown tests):

```python
def test_breakdown_asc_renders_order_by_asc_postgres():
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=APRIL,
        group_by="LOC_NM",
        order_by_metric="GP",
        top_n=5,
        order="asc",
    )
    sql, params = qb.build_breakdown_query(spec, "postgres")
    assert sql.endswith('ORDER BY "GP" ASC NULLS LAST\nLIMIT %s')
    assert params[-1] == 5


def test_breakdown_default_order_is_unchanged_desc():
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V", metrics=("GP",), period=APRIL, group_by="LOC_NM", order_by_metric="GP"
    )
    sql, _ = qb.build_breakdown_query(spec, "snowflake")
    assert sql.endswith('ORDER BY "GP" DESC\nLIMIT %s')


def test_breakdown_rejects_an_unknown_order():
    spec = BreakdownQuerySpec(
        entity="MARINE_SALES_PLANNING_V", metrics=("GP",), period=APRIL, group_by="LOC_NM", order_by_metric="GP", order="sideways"
    )
    with pytest.raises(qb.SpecValidationError, match="order must be 'desc' or 'asc'"):
        qb.build_breakdown_query(spec, "postgres")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' poseidon/tasks/data_qa/skills/metric_query/tests/test_tools.py tests/test_query_builder_snapshots.py -q > ../.superpowers/sdd/2026-10-18-phase-17-prB-metric-query-extensions/t1-red.txt 2>&1`
Expected: the new tests fail with `ImportError` (`is_frame_path`, `TIME_BUCKETS`), `TypeError: unexpected keyword 'order'`, and `ValidationError` not raised. Every pre-existing test still passes.

- [ ] **Step 3: Implement**

`specs.py` — add to `BreakdownQuerySpec` after `filters`:

```python
    scope_value: str | None = None  # see MetricQuerySpec.scope_value
    order: str = "desc"  # "desc" | "asc"; validated by the builder
```

`query_builder.py` — module constant after `_MONETARY_METRIC`:

```python
# Pseudo-dimensions a FrameQuerySpec may group by (Phase 17 PR B): rendered
# as DATE_TRUNC on the entity's date column, never certified columns.
TIME_BUCKETS: dict[str, str] = {"MONTH": "month", "QUARTER": "quarter", "YEAR": "year"}
```

In `build_breakdown_query`, after the `top_n` check:

```python
    if spec.order not in ("desc", "asc"):
        raise SpecValidationError(f"order must be 'desc' or 'asc', got {spec.order!r}")
```

and replace the ORDER BY line:

```python
    clauses.append(f'ORDER BY "{spec.order_by_metric}" {spec.order.upper()}{nulls_last}')
```

`schema.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from poseidon.core.data.query_builder import TIME_BUCKETS
from poseidon.tasks._shared.fragments import DimFilter, PeriodArg


class Args(BaseModel):
    """Ask a metric question over a certified entity."""

    entity: Literal["MARINE_SALES_PLANNING_V", "W_MARINE_GL_SOURCE_AI"] = "MARINE_SALES_PLANNING_V"
    metrics: list[str] = Field(min_length=1, description="Certified metric names, e.g. GP, VOLUME")
    period: PeriodArg
    compare_period: PeriodArg | None = Field(
        default=None,
        description=(
            "Second window for side-by-side comparison. With a single-column group_by, both "
            "windows run and the result is one comparison table per metric."
        ),
    )
    filters: list[DimFilter] = Field(default_factory=list)
    group_by: str | list[str] | None = Field(
        default=None,
        description=(
            "One to three certified dimension columns to break down by (e.g. CUST_NM, LOC_NM), "
            "and/or ONE time bucket MONTH, QUARTER or YEAR. Examples: [\"MONTH\"] for a monthly "
            "series, [\"LOC_NM\"] by port, [\"LOC_NM\", \"MONTH\"] by port by month."
        ),
    )
    order_by: str | None = Field(
        default=None,
        description="Metric to rank a breakdown by; defaults to the first requested metric.",
    )
    order: Literal["desc", "asc"] = Field(
        default="desc", description='"asc" for worst/lowest first, "desc" (default) for best/highest.'
    )
    top_n: int = Field(default=5, ge=1, le=50, description="Row limit for breakdowns, 1-50.")
    scope: Literal["office", "book"] = Field(
        default="office",
        description=(
            'Office scope. "office" (default) applies the conversation\'s carried office filter '
            'when one is set; "book" runs over the whole book: use it for other offices or '
            "locations, the full account, or a share of the total."
        ),
    )
    share_of_total: list[str] = Field(
        default_factory=list,
        description=(
            "Share of total: filter columns to REMOVE for the denominator (the carried office "
            "column counts as a filter). Returns Selection, Total and Share % per metric. "
            "Cannot combine with group_by or compare_period."
        ),
    )

    @field_validator("group_by", mode="after")
    @classmethod
    def _normalise_group_by(cls, value: str | list[str] | None) -> list[str] | None:
        if value is None:
            return None
        columns = [value] if isinstance(value, str) else list(value)
        if not 1 <= len(columns) <= 3:
            raise ValueError("group_by takes one to three columns")
        if len(set(columns)) != len(columns):
            raise ValueError(f"group_by repeats a column: {columns}")
        if sum(col in TIME_BUCKETS for col in columns) > 1:
            raise ValueError("group_by takes at most one time bucket (MONTH, QUARTER or YEAR)")
        return columns

    @model_validator(mode="after")
    def _check_combinations(self) -> "Args":
        if self.order_by is not None and self.order_by not in self.metrics:
            raise ValueError(f"order_by {self.order_by!r} must be one of the requested metrics {self.metrics}")
        if self.share_of_total and (self.group_by is not None or self.compare_period is not None):
            raise ValueError("share_of_total cannot combine with group_by or compare_period")
        return self
```

Keep `SKILL_META` as is.

`build_spec.py` — replace `build_spec` and add the path helper:

```python
from poseidon.core.data.query_builder import TIME_BUCKETS
from poseidon.core.data.specs import BreakdownQuerySpec, FrameQuerySpec, MetricQuerySpec, PeriodWindow


def is_frame_path(group_by: list[str] | None) -> bool:
    """Plan ruling 1: one plain certified dimension keeps the breakdown SQL;
    two or more columns, or any time bucket, go through a frame."""
    if group_by is None:
        return False
    return len(group_by) > 1 or group_by[0] in TIME_BUCKETS


def build_spec(args: Args) -> MetricQuerySpec | BreakdownQuerySpec | FrameQuerySpec:
    period = PeriodWindow(args.period.start, args.period.end)
    metrics = tuple(args.metrics)
    filters = _filters(args)

    if args.group_by is not None and is_frame_path(args.group_by):
        return FrameQuerySpec(
            entity=args.entity, metrics=metrics, period=period, group_by=tuple(args.group_by), filters=filters
        )
    if args.group_by is not None:
        return BreakdownQuerySpec(
            entity=args.entity,
            metrics=metrics,
            period=period,
            group_by=args.group_by[0],
            order_by_metric=args.order_by or metrics[0],
            top_n=args.top_n,
            filters=filters,
            order=args.order,
        )
    return MetricQuerySpec(entity=args.entity, metrics=metrics, period=period, filters=filters)
```

Update the module docstring's "`group_by` present selects a BreakdownQuerySpec" sentence to state the path rule. In `skill.py`, the existing `args.group_by is not None and args.compare_period is not None` branch and `_compare_breakdown` now see a list: change `wide = args.model_copy(update={"top_n": WINDOW_TOP_N})` to also assert the single-column path — Task 3 owns that; for this task only make the type alias `Spec = MetricQuerySpec | BreakdownQuerySpec | FrameQuerySpec` and keep behaviour, so that a frame spec reaching `_single` raises the existing `TypeError` guard rather than silently working (Task 3 replaces it).

Fix `test_build_spec_breakdown_orders_by_first_metric` and any existing test that asserts `args.group_by == "CUST_NM"` to the list form (search the file for `group_by=="` and `.group_by ==`). The stub scripts in `routing_cases.yml` send a string; they keep working through the wrapper.

- [ ] **Step 4: Run green**

Same command as Step 2 into `t1-green.txt`. Expected: all pass. Then `ruff check .` clean.

- [ ] **Step 5: Commit**

```
feat(metric_query): group_by as a list with the frame-path rule, order_by/order, share_of_total args
```

---

### Task 2: Time buckets in the frame builder (Opus)

**Files:**
- Modify: `backend/poseidon/core/data/query_builder.py` (`build_frame_query`)
- Modify: `backend/poseidon/core/data/synthetic_client.py` (`_frame_result`)
- Test: `backend/tests/test_query_builder_snapshots.py`, `backend/tests/test_synthetic_client_pg.py` (one pg test)

**Interfaces:**
- Consumes: `TIME_BUCKETS` (Task 1).
- Produces: `build_frame_query` accepts bucket names in `spec.group_by`, renders `CAST(DATE_TRUNC('<unit>', <date_expr>) AS DATE) AS "<BUCKET>"`, refuses two buckets; `FrameRow.keys` holds ISO strings for bucket columns.

- [ ] **Step 1: Write the failing tests** (append to the frame section of `test_query_builder_snapshots.py`)

```python
def test_frame_by_port_and_month_postgres():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=PeriodWindow(dt.date(2025, 1, 1), dt.date(2026, 1, 1)),
        group_by=("LOC_NM", "MONTH"),
        filters={"CUST_NM": ("Northstar Lines",)},
    )
    sql, params = qb.build_frame_query(spec, "postgres")
    assert sql == (
        "SELECT COALESCE(LOC_NM, 'Unknown') AS LOC_NM, "
        "CAST(DATE_TRUNC('month', LIFT_ETA_DATE) AS DATE) AS \"MONTH\", "
        'SUM(GROSS_PROFIT) AS "GP"\n'
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s "
        "AND COALESCE(CUST_NM, 'Unknown') IN (%s)\n"
        "GROUP BY 1, 2"
    )
    assert params == [dt.date(2025, 1, 1), dt.date(2026, 1, 1), "Northstar Lines"]


def test_frame_by_quarter_on_the_gl_view_wraps_the_varchar_date_snowflake():
    spec = FrameQuerySpec(
        entity="W_MARINE_GL_SOURCE_AI",
        metrics=("MONETARY_TOTAL",),
        period=PeriodWindow(dt.date(2025, 1, 1), dt.date(2026, 1, 1)),
        group_by=("QUARTER",),
    )
    sql, _ = qb.build_frame_query(spec, "snowflake")
    assert sql.startswith(
        "SELECT CAST(DATE_TRUNC('quarter', TO_DATE(PERIOD_DATE)) AS DATE) AS \"QUARTER\", "
    )
    assert "GROUP BY 1" in sql


def test_frame_by_year_alone_postgres():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP", "VOLUME"),
        period=PeriodWindow(dt.date(2024, 1, 1), dt.date(2026, 1, 1)),
        group_by=("YEAR",),
    )
    sql, _ = qb.build_frame_query(spec, "postgres")
    assert sql.startswith("SELECT CAST(DATE_TRUNC('year', LIFT_ETA_DATE) AS DATE) AS \"YEAR\", ")


def test_frame_refuses_two_time_buckets():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V", metrics=("GP",), period=APRIL, group_by=("MONTH", "YEAR")
    )
    with pytest.raises(qb.SpecValidationError, match="at most one time bucket"):
        qb.build_frame_query(spec, "postgres")


def test_frame_still_refuses_a_non_dimension_that_is_not_a_bucket():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V", metrics=("GP",), period=APRIL, group_by=("GROSS_PROFIT",)
    )
    with pytest.raises(qb.SpecValidationError, match="is not a dimension"):
        qb.build_frame_query(spec, "postgres")


def test_a_bucket_name_in_filters_is_still_not_a_dimension():
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V", metrics=("GP",), period=APRIL, group_by=("LOC_NM",), filters={"MONTH": ("2025-01-01",)}
    )
    with pytest.raises(qb.SpecValidationError, match="'MONTH' is not a dimension"):
        qb.build_frame_query(spec, "postgres")
```

pg test (append to `backend/tests/test_synthetic_client_pg.py`; read that file first and build the client exactly the way its existing `run_frame_query`/`run_breakdown_query` tests do, under the same `pg` marker and skip guard; the `client` name below stands for whatever that file uses):

```python
@pytest.mark.pg
def test_frame_month_bucket_keys_come_back_as_iso_strings(client):
    spec = FrameQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("GP",),
        period=PeriodWindow(date(2026, 4, 1), date(2026, 6, 1)),
        group_by=("MONTH",),
    )
    result = client.run_frame_query(spec)
    assert sorted(row.keys[0] for row in result.rows) == ["2026-04-01", "2026-05-01"]
    assert all(isinstance(row.keys[0], str) for row in result.rows)
```

- [ ] **Step 2: Run red** into `t2-red.txt` (same offline command, the snapshot file only). Expected: the four new positive tests fail with `SpecValidationError: 'MONTH' is not a dimension ...`; `test_frame_refuses_two_time_buckets` fails because the wrong error text is raised. The pg test is deselected offline.

- [ ] **Step 3: Implement**

In `build_frame_query`, replace the loop that validates `group_by` columns:

```python
    buckets = [col for col in spec.group_by if col in TIME_BUCKETS]
    if len(buckets) > 1:
        raise SpecValidationError(
            f"group_by takes at most one time bucket, got {buckets}"
        )
    for col in spec.group_by:
        if col not in TIME_BUCKETS:
            _require_dimension(entity, col)
```

and the select list:

```python
    date_expr = _date_expr(entity, dialect)
    projections = []
    for col in spec.group_by:
        if col in TIME_BUCKETS:
            projections.append(
                f"CAST(DATE_TRUNC('{TIME_BUCKETS[col]}', {date_expr}) AS DATE) AS \"{col}\""
            )
        else:
            projections.append(f"COALESCE({col}, '{entity.null_placeholder}') AS {col}")
    select_list = ", ".join(projections + [_metric_expr(entity, m) for m in spec.metrics])
```

Update the function docstring: buckets are allowed, one at most, chronological ordering is the caller's job (no ORDER BY). Update the module docstring's validation paragraph with one sentence on `TIME_BUCKETS`.

`synthetic_client.py` `_frame_result`:

```python
def _frame_result(spec: FrameQuerySpec, rows: list[tuple]) -> FrameResult:
    n = len(spec.group_by)
    return FrameResult(
        entity=spec.entity,
        group_by=tuple(spec.group_by),
        rows=[
            FrameRow(
                keys=tuple(_key_text(cell) for cell in row[:n]),
                values={name: _as_float(cell) for name, cell in zip(spec.metrics, row[n:], strict=True)},
            )
            for row in rows
        ],
    )


def _key_text(cell: object) -> str:
    """A bucket column comes back as a ``date``; every other key is text.
    ISO form keeps the FrameRow.keys contract (strings) and sorts chronologically."""
    return cell.isoformat() if isinstance(cell, date) else cell
```

(`date` is already imported in that module for `list_months`; check and add if not.)

- [ ] **Step 4: Run green** into `t2-green.txt`; then run the pg suite serially with the compose override into `t2-pg.txt` and confirm the new pg test passes with the rest (355 passed expected, 1 skipped). `ruff check .` clean.

- [ ] **Step 5: Commit**

```
feat(query_builder): MONTH/QUARTER/YEAR time buckets in build_frame_query; frame bucket keys as ISO strings
```

---

### Task 3: The frame path in the skill — ordering, cap, labels, pass-through (Opus)

**Files:**
- Create: `backend/poseidon/tasks/data_qa/skills/metric_query/tools/frame_breakdown.py`
- Modify: `backend/poseidon/tasks/data_qa/skills/metric_query/skill.py`
- Modify: `backend/poseidon/tasks/data_qa/skills/metric_query/tools/format_parts.py` (optional `Order:` line; `_header_proof` for a `FrameQuerySpec`)
- Modify: `backend/poseidon/core/chat/orchestrator.py` (`_repopulate_pass_through`)
- Test: `tests/test_tools.py`, `backend/tests/test_chat_orchestrator.py`

**Interfaces:**
- Consumes: `is_frame_path`, `TIME_BUCKETS`, `FrameQuerySpec`, `FrameResult`.
- Produces: `frame_breakdown_parts(spec: FrameQuerySpec, result: FrameResult, settings: Settings, *, order_by: str, order: str, top_n: int) -> tuple[list[dict], list[str]]`; `bucket_label(bucket: str, iso: str) -> str`; `BUCKET_HEADERS = {"MONTH": "Month", "QUARTER": "Quarter", "YEAR": "Year"}`; `format_parts._order_proof(order_by, order, default_metric) -> list[str]` (empty when default). Orchestrator: `_pass_through_column(group_by) -> int | None`.
- Refusals (plan ruling 3) live in `skill.run` before any query.

- [ ] **Step 1: Write the failing tests**

`tests/test_tools.py` (new section "frame_breakdown_parts and skill.run on the frame path"); extend `_RecordingDataClient` with `frame_specs` and `run_frame_query` returning a configurable `FrameResult`:

```python
from poseidon.core.data.client import FrameResult, FrameRow
from poseidon.core.data.specs import FrameQuerySpec
from ..tools.frame_breakdown import BUCKET_HEADERS, bucket_label, frame_breakdown_parts

# in _RecordingDataClient.__init__: self.frame_specs = []; self._frame_rows = list(frame_rows or [])
# add:
#    def run_frame_query(self, spec: FrameQuerySpec) -> FrameResult:
#        self.frame_specs.append(spec)
#        rows = self._frame_rows.pop(0) if self._frame_rows else []
#        return FrameResult(entity=spec.entity, group_by=tuple(spec.group_by), rows=rows)

_YEAR_2025 = PeriodWindow(dt.date(2025, 1, 1), dt.date(2026, 1, 1))


def _frame(group_by, rows):
    spec = FrameQuerySpec(entity="MARINE_SALES_PLANNING_V", metrics=("GP", "VOLUME"), period=_YEAR_2025, group_by=group_by)
    return spec, FrameResult(entity=spec.entity, group_by=group_by, rows=[FrameRow(k, v) for k, v in rows])


def test_bucket_labels():
    assert bucket_label("MONTH", "2025-01-01") == "Jan 2025"
    assert bucket_label("QUARTER", "2025-04-01") == "Q2 2025"
    assert bucket_label("YEAR", "2025-01-01") == "2025"
    assert BUCKET_HEADERS == {"MONTH": "Month", "QUARTER": "Quarter", "YEAR": "Year"}


def test_frame_breakdown_omits_missing_buckets_and_ranks_by_period_total():
    spec, result = _frame(
        ("LOC_NM", "MONTH"),
        [
            (("Singapore", "2025-02-01"), {"GP": 50.0, "VOLUME": 5.0}),
            (("Rotterdam", "2025-01-01"), {"GP": 40.0, "VOLUME": 4.0}),
            (("Rotterdam", "2025-02-01"), {"GP": 30.0, "VOLUME": 3.0}),
            (("Houston", "2025-01-01"), {"GP": 10.0, "VOLUME": 1.0}),
        ],
    )
    parts, proof = frame_breakdown_parts(spec, result, SETTINGS, order_by="GP", order="desc", top_n=2)
    (table,) = parts
    assert table["payload"]["columns"] == ["Month", "Port", "Gross Profit", "Volume"]
    # Rotterdam (70 over the year) outranks Singapore (50); Houston is cut; no zero row for Singapore in January.
    assert table["payload"]["rows"] == [
        ["Jan 2025", "Rotterdam", 40, 4],
        ["Feb 2025", "Rotterdam", 30, 3],
        ["Feb 2025", "Singapore", 50, 5],
    ]
    assert proof[-3:] == ["Group by: LOC_NM, MONTH (top 2 keys; every bucket)", "Order: GP desc", "Rows: 3"]


def test_frame_breakdown_month_only_shows_every_month_chronologically():
    spec, result = _frame(
        ("MONTH",),
        [(("2025-03-01",), {"GP": 3.0, "VOLUME": 1.0}), (("2025-01-01",), {"GP": 1.0, "VOLUME": 1.0})],
    )
    parts, proof = frame_breakdown_parts(spec, result, SETTINGS, order_by="GP", order="desc", top_n=5)
    assert [row[0] for row in parts[0]["payload"]["rows"]] == ["Jan 2025", "Mar 2025"]
    assert "Group by: MONTH (top 5 keys; every bucket)" in proof


def test_frame_breakdown_two_plain_columns_orders_asc_and_caps_rows():
    spec, result = _frame(
        ("LOC_NM", "SUPPLIER_NM"),
        [
            (("Singapore", "Vitol"), {"GP": 50.0, "VOLUME": 5.0}),
            (("Rotterdam", "Shell"), {"GP": 40.0, "VOLUME": 4.0}),
            (("Houston", "BP"), {"GP": 10.0, "VOLUME": 1.0}),
        ],
    )
    parts, proof = frame_breakdown_parts(spec, result, SETTINGS, order_by="GP", order="asc", top_n=2)
    assert [row[:2] for row in parts[0]["payload"]["rows"]] == [["Houston", "BP"], ["Rotterdam", "Shell"]]
    assert "Group by: LOC_NM, SUPPLIER_NM (top 2)" in proof and "Order: GP asc" in proof


def test_frame_breakdown_empty_is_no_data():
    spec, result = _frame(("MONTH",), [])
    parts, proof = frame_breakdown_parts(spec, result, SETTINGS, order_by="GP", order="desc", top_n=5)
    assert parts == [text_part(NO_DATA_TEXT)] and proof[-1] == EMPTY_RESULT_PROOF


def test_run_frame_path_queries_a_frame_and_stamps_the_office():
    client = _RecordingDataClient(frame_rows=[[FrameRow(("Rotterdam", "2026-04-01"), {"GP": 1.0})]])
    res = run(_ctx(client, state=_ROTTERDAM), _args(metrics=["GP"], group_by=["LOC_NM", "MONTH"]))
    assert res.ok is True
    (spec,) = client.frame_specs
    assert spec.filters == {"CUSTOMER_TEAM_NAME": ("Rotterdam",)}
    assert res.proof[-1] == _STAMPED_LINE


def test_run_refuses_win_rate_on_the_frame_path_before_querying():
    client = _RecordingDataClient()
    res = run(_ctx(client), _args(metrics=["WIN_RATE"], group_by=["MONTH"]))
    assert res.ok is False and res.error["status"] == 422
    assert "WIN_RATE cannot be broken down by more than one column or by a time bucket" in res.error["detail"]
    assert client.frame_specs == []


def test_run_refuses_compare_with_a_multi_column_group_by():
    client = _RecordingDataClient()
    res = run(_ctx(client), _args(group_by=["LOC_NM", "MONTH"], compare_period=_PRIOR_ARG))
    assert res.ok is False and "compare one dimension at a time" in res.error["detail"]
    assert client.frame_specs == [] and client.breakdown_specs == []


def test_run_single_column_asc_adds_an_order_line_and_default_adds_none():
    asc = _RecordingDataClient()
    run(_ctx(asc), _args(group_by=["LOC_NM"], order="asc"))
    assert asc.breakdown_specs[0].order == "asc"
    default = _RecordingDataClient(breakdown_rows=[[BreakdownRow("Singapore", {"GP": 1.0})]])
    res = run(_ctx(default), _args(group_by=["LOC_NM"]))
    assert not any(line.startswith("Order:") for line in res.proof)
```

(`_args(...)` is the file's existing helper; confirm its keyword names and that `metrics` defaults to `["GP"]`. `SETTINGS`, `text_part`, `NO_DATA_TEXT`, `EMPTY_RESULT_PROOF`, `_ROTTERDAM`, `_STAMPED_LINE`, `BreakdownRow` already exist or are imported in that file.)

Orchestrator (append to `backend/tests/test_chat_orchestrator.py` beside `test_pass_through_capped_at_ten_rows`):

```python
def test_pass_through_column_skips_time_buckets():
    assert orchestrator._pass_through_column("CUST_NM") == 0
    assert orchestrator._pass_through_column(["CUST_NM"]) == 0
    assert orchestrator._pass_through_column(["MONTH", "LOC_NM"]) == 1
    assert orchestrator._pass_through_column(["MONTH"]) is None
    assert orchestrator._pass_through_column(None) is None


def test_pass_through_after_a_bucketed_breakdown_reads_the_first_non_time_column():
    sink = _sink()
    record = ToolRecord(tool_seq=1, skill_id=METRIC_QUERY, arguments={"group_by": ["MONTH", "LOC_NM"]}, status="ok")
    sink.tool_result_parts[1] = [{"kind": "table", "payload": {"columns": ["Month", "Port", "GP"], "rows": [["Jan 2025", "Rotterdam", 1], ["Jan 2025", "Singapore", 2]]}}]
    slots = orchestrator._repopulate_pass_through(ConversationSlots(), (record,), sink)
    assert slots.pass_through == (("Rotterdam", "Rotterdam"), ("Singapore", "Singapore"))
```

Match `ToolRecord`'s real constructor (read `core/llm/loop.py`) and the way `test_pass_through_capped_at_ten_rows` builds its record; copy that pattern.

- [ ] **Step 2: Run red** into `t3-red.txt`. Expected: `ImportError` for `frame_breakdown`, `AttributeError: _pass_through_column`, and the frame-path `run` tests raising the `TypeError` guard from `_single`.

- [ ] **Step 3: Implement**

`tools/frame_breakdown.py`:

```python
"""A frame (two or more group-by columns, or a time bucket) -> one ordered,
capped ``table`` part plus proof. Plan rulings 1, 4, 5, 6 of the PR B plan.

Cap rule (owner ruling 5): ``top_n`` ranks the NON-time keys by ``order_by``
summed over the whole period; every bucket for those keys is shown,
chronologically (ISO keys sort chronologically). No zero-filling: a key
with no rows in a bucket has no row for it. Without a bucket the rows are
the ranked keys themselves.
"""

from datetime import date

from poseidon.core.config import Settings
from poseidon.core.data.client import FrameResult
from poseidon.core.data.query_builder import TIME_BUCKETS
from poseidon.core.data.specs import FrameQuerySpec
from poseidon.core.skills.result import table_part, text_part

from .format_parts import (
    EMPTY_RESULT_PROOF,
    NO_DATA_TEXT,
    _dimension_friendly,
    _friendly_and_unit,
    _header_proof,
    _order_proof,
    _round,
)

BUCKET_HEADERS = {"MONTH": "Month", "QUARTER": "Quarter", "YEAR": "Year"}


def bucket_label(bucket: str, iso: str) -> str:
    d = date.fromisoformat(iso)
    if bucket == "MONTH":
        return d.strftime("%b %Y")
    if bucket == "QUARTER":
        return f"Q{(d.month - 1) // 3 + 1} {d.year}"
    return str(d.year)


def _value(values: dict[str, float | None], metric: str) -> float:
    v = values.get(metric)
    return 0.0 if v is None else float(v)


def frame_breakdown_parts(
    spec: FrameQuerySpec,
    result: FrameResult,
    settings: Settings,
    *,
    order_by: str,
    order: str,
    top_n: int,
) -> tuple[list[dict], list[str]]:
    proof = _header_proof(spec, settings, None)
    columns_in = list(spec.group_by)
    bucket = next((c for c in columns_in if c in TIME_BUCKETS), None)
    key_cols = [c for c in columns_in if c not in TIME_BUCKETS]
    key_idx = [columns_in.index(c) for c in key_cols]
    bucket_idx = columns_in.index(bucket) if bucket is not None else None

    cap_text = f"top {top_n} keys; every bucket" if bucket is not None else f"top {top_n}"
    proof.append(f"Group by: {', '.join(columns_in)} ({cap_text})")
    proof.extend(_order_proof(order_by, order, spec.metrics[0], force=True))

    if not result.rows:
        return [text_part(NO_DATA_TEXT)], [*proof, EMPTY_RESULT_PROOF]

    totals: dict[tuple[str, ...], float] = {}
    for row in result.rows:
        key = tuple(row.keys[i] for i in key_idx)
        totals[key] = totals.get(key, 0.0) + _value(row.values, order_by)
    sign = -1.0 if order == "desc" else 1.0
    ranked = sorted(totals, key=lambda k: (sign * totals[k], k))[:top_n]
    rank = {k: i for i, k in enumerate(ranked)}

    def sort_key(row):
        key = tuple(row.keys[i] for i in key_idx)
        bucket_key = row.keys[bucket_idx] if bucket_idx is not None else ""
        return (bucket_key, rank[key])

    kept = [r for r in result.rows if tuple(r.keys[i] for i in key_idx) in rank]
    kept.sort(key=sort_key)

    headers = []
    if bucket is not None:
        headers.append(BUCKET_HEADERS[bucket])
    headers += [_dimension_friendly(spec.entity, c) for c in key_cols]
    headers += [_friendly_and_unit(spec.entity, m)[0] for m in spec.metrics]

    rows = []
    for r in kept:
        cells: list = []
        if bucket_idx is not None:
            cells.append(bucket_label(bucket, r.keys[bucket_idx]))
        cells += [r.keys[i] for i in key_idx]
        cells += [_round(m, r.values.get(m)) for m in spec.metrics]
        rows.append(cells)

    proof.append(f"Rows: {len(rows)}")
    return [table_part(columns=headers, rows=rows)], proof
```

`format_parts.py`: make `_header_proof` accept `FrameQuerySpec` (type union; it only reads `entity`, `period`, `filters`, and appends the `Group by:` line only for a `BreakdownQuerySpec` — leave that), and add:

```python
def _order_proof(order_by: str, order: str, default_metric: str, *, force: bool = False) -> list[str]:
    """``Order: <metric> <dir>`` -- always on the frame path (``force``), and
    on the single-column path only when the order is not the default, so
    every pre-existing proof stays byte-identical (plan ruling 6)."""
    if force or order_by != default_metric or order != "desc":
        return [f"Order: {order_by} {order}"]
    return []
```

In `_breakdown_parts` (single-column), after `proof.append(f"Rows: ...")` is wrong placement; instead in `format_parts()` right after `_header_proof` and before the `BreakdownQuerySpec` branch, add `if isinstance(spec, BreakdownQuerySpec): proof.extend(_order_proof(spec.order_by_metric, spec.order, spec.metrics[0]))`.

`skill.py` — refusals and dispatch in `run`:

```python
from poseidon.core.data.specs import BreakdownQuerySpec, FrameQuerySpec, MetricQuerySpec
from .tools.build_spec import build_spec, is_frame_path
from .tools.frame_breakdown import frame_breakdown_parts

Spec = MetricQuerySpec | BreakdownQuerySpec | FrameQuerySpec

WIN_RATE_FRAME_TEXT = (
    "WIN_RATE cannot be broken down by more than one column or by a time bucket; "
    "ask for NUM_WON and NUM_INQUIRIES instead"
)
COMPARE_MULTI_TEXT = "compare one dimension at a time: compare_period needs a single plain group_by column"

# inside run(), before the try:
    frame = is_frame_path(args.group_by)
    if frame and "WIN_RATE" in args.metrics:
        return SkillResult(ok=False, error=problem(422, "invalid query", WIN_RATE_FRAME_TEXT))
    if frame and args.compare_period is not None:
        return SkillResult(ok=False, error=problem(422, "invalid query", COMPARE_MULTI_TEXT))

# inside the try, replacing the two-way branch:
        if frame:
            parts, proof = _frame(ctx, args, scoped)
        elif args.group_by is not None and args.compare_period is not None:
            parts, proof = _compare_breakdown(ctx, args, scoped)
        else:
            parts, proof = _single(ctx, args, scoped)


def _frame(ctx: SkillContext, args: Args, scoped: Callable[[Args], Spec]) -> tuple[list[dict], list[str]]:
    spec = scoped(args)
    if not isinstance(spec, FrameQuerySpec):
        raise TypeError("build_spec on the frame path did not return a FrameQuerySpec")
    return frame_breakdown_parts(
        spec, ctx.data.run_frame_query(spec), ctx.settings,
        order_by=args.order_by or args.metrics[0], order=args.order, top_n=args.top_n,
    )
```

`_stamp_office` uses `dataclasses.replace(spec, filters=...)`, which works on `FrameQuerySpec` too; widen `_S`'s bound to include it. Update `run`'s docstring with the frame path and the two refusals.

`orchestrator.py`:

```python
from poseidon.core.data.query_builder import TIME_BUCKETS


def _pass_through_column(group_by: object) -> int | None:
    """The table column pass-through reads (plan ruling 8): the first group-by
    entry that is not a time bucket; None when there is no group-by or only a bucket."""
    if not group_by:
        return None
    columns = [group_by] if isinstance(group_by, str) else list(group_by)
    for index, column in enumerate(columns):
        if column not in TIME_BUCKETS:
            return index
    return None
```

and in `_repopulate_pass_through` replace `if not record.arguments.get("group_by"): continue` with `column = _pass_through_column(record.arguments.get("group_by")); if column is None: continue` and `row[0]` with `row[column]` (both occurrences in the tuple comprehension).

- [ ] **Step 4: Run green** into `t3-green.txt` (test_tools, test_chat_orchestrator, test_llm_loop, test_live_chat_sse offline parts). `ruff check .` clean.

- [ ] **Step 5: Commit**

```
feat(metric_query): frame path -- multi-column and time-bucket breakdowns ordered and capped in Python; pass-through skips buckets
```

---

### Task 4: Share of total in the C04 order (Opus)

**Files:**
- Create: `backend/poseidon/tasks/data_qa/skills/metric_query/tools/share_of_total.py`
- Modify: `backend/poseidon/tasks/data_qa/skills/metric_query/skill.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Produces: `denominator_spec(selection: MetricQuerySpec, removed: list[str]) -> MetricQuerySpec` (raises `SpecValidationError` when a column is absent); `share_parts(selection: MetricQuerySpec, sel_result: MetricResult, total: MetricQuerySpec, tot_result: MetricResult, settings: Settings, removed: list[str]) -> tuple[list[dict], list[str]]`.

- [ ] **Step 1: Write the failing tests**

```python
from ..tools.share_of_total import denominator_spec, share_parts


class _ShareClient(_RecordingDataClient):
    """Answers the selection and the total in call order."""

    def __init__(self, values: list[dict[str, float | None]]):
        super().__init__()
        self._values = list(values)

    def run_metric_query(self, spec):
        self.metric_specs.append(spec)
        return MetricResult(entity=spec.entity, period=spec.period, values=self._values.pop(0))


def test_share_of_total_office_gp_20_of_book_100_is_20_percent_with_the_office_only_on_the_numerator():
    client = _ShareClient([{"GP": 20.0}, {"GP": 100.0}])
    args = _args(metrics=["GP"], filters=[DimFilter(column="CUST_NM", values=["Northstar Lines"])], share_of_total=["CUSTOMER_TEAM_NAME"])
    res = run(_ctx(client, state=_ROTTERDAM), args)
    assert res.ok is True
    numerator, denominator = client.metric_specs
    assert numerator.filters == {"CUST_NM": ("Northstar Lines",), "CUSTOMER_TEAM_NAME": ("Rotterdam",)}
    assert denominator.filters == {"CUST_NM": ("Northstar Lines",)}
    (table,) = res.parts
    assert table["payload"]["columns"] == ["Metric", "Selection", "Total", "Share %"]
    assert table["payload"]["rows"] == [["Gross Profit", 20, 100, 20.0]]
    assert "Selection filters: CUST_NM IN (Northstar Lines) AND CUSTOMER_TEAM_NAME IN (Rotterdam)" in res.proof
    assert "Total filters: CUST_NM IN (Northstar Lines)" in res.proof
    assert "Share of total: removed CUSTOMER_TEAM_NAME" in res.proof
    assert res.proof[-1] == _STAMPED_LINE


def test_share_of_total_column_absent_from_effective_filters_is_a_422():
    client = _ShareClient([])
    res = run(_ctx(client), _args(metrics=["GP"], share_of_total=["CUSTOMER_TEAM_NAME"]))  # no office carried
    assert res.ok is False and res.error["status"] == 422
    assert "share_of_total column 'CUSTOMER_TEAM_NAME' is not among the effective filters []" in res.error["detail"]
    assert client.metric_specs == []


def test_share_of_total_zero_total_renders_na():
    client = _ShareClient([{"GP": 5.0}, {"GP": 0.0}])
    res = run(_ctx(client, state=_ROTTERDAM), _args(metrics=["GP"], share_of_total=["CUSTOMER_TEAM_NAME"]))
    assert res.parts[0]["payload"]["rows"] == [["Gross Profit", 5, 0, "n/a"]]


def test_share_of_total_both_sides_empty_is_no_data():
    client = _ShareClient([{"GP": None}, {"GP": None}])
    res = run(_ctx(client, state=_ROTTERDAM), _args(metrics=["GP"], share_of_total=["CUSTOMER_TEAM_NAME"]))
    assert res.parts == [text_part(NO_DATA_TEXT)]


def test_share_of_total_keeps_row_scope_on_both_queries(monkeypatch):
    monkeypatch.setattr("poseidon.tasks.data_qa.skills.metric_query.skill.resolve_row_scope_value", lambda entity, user: "alice@example.com")
    client = _ShareClient([{"GP": 1.0}, {"GP": 2.0}])
    run(_ctx(client, state=_ROTTERDAM), _args(metrics=["GP"], share_of_total=["CUSTOMER_TEAM_NAME"]))
    assert [s.scope_value for s in client.metric_specs] == ["alice@example.com", "alice@example.com"]
```

(The monkeypatch target mirrors `test_run_consults_the_row_scope_resolver_with_the_caller_identity`'s pattern in the same file; reuse its exact approach.)

- [ ] **Step 2: Run red** into `t4-red.txt`. Expected: `ImportError` on `share_of_total`; the `run` tests return a plain metric table (share ignored) so the assertions on `columns` fail.

- [ ] **Step 3: Implement**

`tools/share_of_total.py`:

```python
"""Share of total in the C04 order (reports codex review C04; spec 8.2).

``skill.run`` builds the SELECTION spec through its normal path, which is
where the carried office is stamped. THIS module then derives the TOTAL
spec by REMOVING the named columns from that already-effective filter set
and never restamps -- so the office can only ever sit on the numerator.
D16 row scope rides on ``scope_value`` of the selection spec and is copied
unchanged onto the total.
"""

from dataclasses import replace

from poseidon.core.analytics import mom
from poseidon.core.config import Settings
from poseidon.core.data.client import MetricResult
from poseidon.core.data.query_builder import SpecValidationError
from poseidon.core.data.specs import MetricQuerySpec
from poseidon.core.skills.result import table_part, text_part

from .format_parts import EMPTY_RESULT_PROOF, NO_DATA_TEXT, _filters_proof, _friendly_and_unit, _header_proof, _is_empty, _round


def denominator_spec(selection: MetricQuerySpec, removed: list[str]) -> MetricQuerySpec:
    present = sorted(selection.filters)
    for column in removed:
        if column not in selection.filters:
            raise SpecValidationError(
                f"share_of_total column {column!r} is not among the effective filters {present}"
            )
    kept = {col: values for col, values in selection.filters.items() if col not in removed}
    return replace(selection, filters=kept)


def share_parts(
    selection: MetricQuerySpec,
    sel_result: MetricResult,
    total: MetricQuerySpec,
    tot_result: MetricResult,
    settings: Settings,
    removed: list[str],
) -> tuple[list[dict], list[str]]:
    proof = _header_proof(selection, settings, None)
    proof[-1] = "Selection " + proof[-1].lower().replace("filters:", "filters:", 1)  # "Filters: ..." -> "Selection filters: ..."
    proof.append("Total " + _filters_proof(total.filters).lower().replace("filters:", "filters:", 1))
    proof.append(f"Share of total: removed {', '.join(removed)}")
    if _is_empty(sel_result) and _is_empty(tot_result):
        return [text_part(NO_DATA_TEXT)], [*proof, EMPTY_RESULT_PROOF]

    rows = []
    for m in selection.metrics:
        sel = sel_result.values.get(m)
        tot = tot_result.values.get(m)
        share = mom.share(sel or 0.0, tot or 0.0)
        rows.append([
            _friendly_and_unit(selection.entity, m)[0],
            _round(m, sel),
            _round(m, tot),
            "n/a" if share is None else round(share, 1),
        ])
    proof.append(f"Metrics: {len(rows)} values")
    return [table_part(columns=["Metric", "Selection", "Total", "Share %"], rows=rows)], proof
```

Do NOT use the `.lower().replace(...)` trick above in the real code; write it plainly: build the selection line as `"Selection filters: " + _filters_proof(selection.filters)[len("Filters: "):]` and the total line the same way, so the text reads exactly `Selection filters: CUST_NM IN (...) AND ...` and `Total filters: none` when empty. (The sketch above is replaced by these two lines.)

`skill.py`: in `run`, after the frame refusals and inside the `try`, before the frame/compare/single dispatch:

```python
        if args.share_of_total:
            parts, proof = _share(ctx, args, scoped)
        elif frame: ...


def _share(ctx: SkillContext, args: Args, scoped: Callable[[Args], Spec]) -> tuple[list[dict], list[str]]:
    """C04 order: the stamped selection first, then the total derived from it; never restamped."""
    selection = scoped(args)
    if not isinstance(selection, MetricQuerySpec):
        raise TypeError("share_of_total without group_by must build a MetricQuerySpec")
    total = denominator_spec(selection, args.share_of_total)
    return share_parts(
        selection, ctx.data.run_metric_query(selection), total, ctx.data.run_metric_query(total),
        ctx.settings, args.share_of_total,
    )
```

The `SpecValidationError` from `denominator_spec` lands in the existing `except` and becomes the 422. Update `run`'s docstring (a SHARE paragraph naming C04).

- [ ] **Step 4: Run green** into `t4-green.txt`. `ruff check .` clean.

- [ ] **Step 5: Commit**

```
feat(metric_query): share_of_total in the C04 order -- stamped selection, derived total, never restamped
```

---

### Task 5: The scope hinter and its state-block line (Sonnet)

**Files:**
- Modify: `backend/poseidon/core/parsing/lexicon.py` (`SCOPE_HINTS`)
- Create: `backend/poseidon/core/parsing/scope_hinter.py`
- Modify: `backend/poseidon/core/parsing/types.py` (`ParsedTurn.scope_hint: str | None = None`, last field)
- Modify: `backend/poseidon/core/parsing/pipeline.py` (set it; docstring step 8b)
- Modify: `backend/poseidon/core/llm/prompts.py` (`Scope hint:` line after `Skill hints`)
- Test: `backend/tests/test_parsing_hinter_pipeline.py`, `backend/tests/test_llm_prompts.py`, `backend/tests/test_chat_state_devrouter.py`

**Interfaces:**
- Produces: `lexicon.SCOPE_HINTS: dict[str, str]`; `scope_hinter.scope_hint(text: str) -> str | None` returning `"book"`, `"share"` or `None`; `ParsedTurn.scope_hint`; rendered line `Scope hint: book`.

- [ ] **Step 1: Write the failing tests**

`test_parsing_hinter_pipeline.py` (new section):

```python
from poseidon.core.parsing.scope_hinter import scope_hint


@pytest.mark.parametrize(
    "text, expected",
    [
        ("where else does Northstar lift fuel", "book"),
        ("show me the other ports", "book"),
        ("give me the full account GP", "book"),
        ("whole book please", "book"),
        ("how does it look overall", "book"),
        ("across all offices", "book"),
        ("how much of my GP is of the total account GP", "share"),
        ("what share of the book is Rotterdam", "share"),
        ("what % of GP is that", "share"),
        ("what portion of volume", "share"),
        ("how much it makes up", "share"),
        ("how much of the whole book", "share"),  # share wins over book (plan ruling 9)
        ("top gp customers in april 2026", None),
        ("overalls are not a scope word", None),  # word boundary
    ],
)
def test_scope_hint_lexicon(text, expected):
    assert scope_hint(text) == expected


def test_parse_turn_sets_scope_hint(monkeypatch):
    client = FakeDataClient()  # the file's offline fake
    parsed = parse_turn("where else does Northstar Lines lift fuel", NO_SLOTS, REFERENCE_DATE, client)
    assert parsed.scope_hint == "book"
    plain = parse_turn("top gp customers in april 2026", NO_SLOTS, REFERENCE_DATE, client)
    assert plain.scope_hint is None
```

(`FakeDataClient`, `NO_SLOTS`, `REFERENCE_DATE` are the names that file already uses for `parse_turn` tests; match them. The existing `_turn(...)` helper builds `ParsedTurn` without `scope_hint`, which keeps working because the field defaults to `None`.)

`test_llm_prompts.py`:

```python
def test_render_state_block_scope_hint_golden():
    parsed = ParsedTurn(
        normalized_text="where else does northstar lines lift fuel",
        slots=ConversationSlots(),
        period_a=PeriodWindow(start=date(2026, 4, 1), end=date(2026, 5, 1)),
        period_b=None,
        customer=None,
        port=None,
        hints=(CandidateSkill(skill_id="data_qa.metric_query", score=1.0),),
        issues=(),
        scope_hint="book",
    )
    assert render_state_block(ConversationSlots(), parsed) == (
        "Mode: default\n\n"
        "Period: 2026-04-01..2026-05-01\n"
        "Skill hints: data_qa.metric_query (1.0)\n"
        "Scope hint: book"
    )
```

`test_chat_state_devrouter.py`:

```python
def test_scope_hint_line_does_not_change_the_dev_router_decision():
    router = DevDeterministicRouter()
    without = _system(_parsed(period_a=_PERIOD_A))
    with_hint = _system(replace(_parsed(period_a=_PERIOD_A), scope_hint="share"))
    assert "Scope hint: share" in with_hint
    message = [_user_message("gp this period")]
    a = router.invoke(system=without, messages=message, tools=[], model="m", params={})
    b = router.invoke(system=with_hint, messages=message, tools=[], model="m", params={})
    assert a == b
```

(`from dataclasses import replace`.)

- [ ] **Step 2: Run red** into `t5-red.txt`. Expected: `ImportError: scope_hinter`; `TypeError: unexpected keyword 'scope_hint'`.

- [ ] **Step 3: Implement**

`lexicon.py` (append; ASCII only):

```python
# Phase 17 PR B: the scope hinter's phrase table (reports design 7.2). The
# value is the ParsedTurn.scope_hint the phrase argues for. Advisory only.
SCOPE_HINTS: dict[str, str] = {
    "where else": "book",
    "other ports": "book",
    "other offices": "book",
    "other locations": "book",
    "full account": "book",
    "whole book": "book",
    "overall": "book",
    "across all offices": "book",
    "how much of": "share",
    "share of": "share",
    "% of": "share",
    "percent of": "share",
    "portion of": "share",
    "makes up": "share",
}
```

`scope_hinter.py`:

```python
"""The scope hinter (reports design 7.2, Phase 17 PR B): a lexicon stage
after ``skill_hinter`` that sets ``ParsedTurn.scope_hint`` to ``"book"`` or
``"share"``. Advisory: the router decides; the state block shows the hint.

Matching is the skill hinter's discipline (casefolded, normalised, whole
phrase) with one adjustment: a lexeme that starts or ends with a non-word
character ("% of") gets no word boundary on that side, because ``\\b``
cannot sit between a space and a ``%``. ``share`` wins over ``book`` when
both match (plan ruling 9).
"""

import re

from poseidon.core.parsing.lexicon import SCOPE_HINTS
from poseidon.core.parsing.normalize import normalize


def _pattern(lexeme: str) -> re.Pattern[str]:
    left = r"\b" if lexeme[0].isalnum() else ""
    right = r"\b" if lexeme[-1].isalnum() else ""
    return re.compile(left + re.escape(lexeme) + right)


_PATTERNS = {lexeme: _pattern(lexeme) for lexeme in SCOPE_HINTS}


def scope_hint(text: str) -> str | None:
    cleaned = normalize(text).casefold()
    matched = {SCOPE_HINTS[lexeme] for lexeme, pattern in _PATTERNS.items() if pattern.search(cleaned)}
    if "share" in matched:
        return "share"
    if "book" in matched:
        return "book"
    return None
```

`types.py`: add `scope_hint: str | None = None  # "book" | "share" | None; the scope hinter's advisory read` as the LAST field of `ParsedTurn`.

`pipeline.py`: import `scope_hint`, compute `scope = scope_hint(normalized)` right after `hints = hint(...)`, pass `scope_hint=scope` to the `ParsedTurn(...)` constructor; add step "8b. Scope hint from the UNMASKED normalized text (``scope_hinter``)" to the module docstring's ordered list.

`prompts.py` `_render_parsed`: after the hints line, `if parsed.scope_hint: lines.append(f"Scope hint: {parsed.scope_hint}")`. Add one sentence to `render_state_block`'s docstring.

Check `_parsed_to_loggable_dict` in `orchestrator.py` (it serialises `ParsedTurn` for the run log): if it enumerates fields explicitly, add `scope_hint`; if it uses `dataclasses.asdict`, nothing to do. Say which in the report.

- [ ] **Step 4: Run green** into `t5-green.txt` (the three test files plus `test_chat_orchestrator.py`). `ruff check .` clean. Confirm the ASCII byte-pin tests pass.

- [ ] **Step 5: Commit**

```
feat(parsing): scope hinter -- ParsedTurn.scope_hint from a lexicon, rendered as "Scope hint:" for the router
```

---

### Task 6: "Slice it another way" — the deterministic clarification (Sonnet)

**Files:**
- Modify: `backend/poseidon/core/parsing/pipeline.py` (`dimension_unspecified` issue)
- Modify: `backend/poseidon/core/chat/orchestrator.py` (`_select_clarify_issue`, `_finish_clarify`, `SLICE_CHIPS`, `SLICE_TEXT`)
- Test: `backend/tests/test_parsing_hinter_pipeline.py`, `backend/tests/test_chat_orchestrator.py`

**Interfaces:**
- Produces: `ParseIssue(code="dimension_unspecified", kind="dimension", message=SLICE_TEXT, phrase=<matched text>)`; `orchestrator.SLICE_TEXT = "Which dimension should I slice by?"`; `orchestrator.SLICE_CHIPS: tuple[tuple[str, str], ...]` of `(column, label)`; chips `{"id": "slice:<column>", "label": <label>, "send_text": "break that down by <label lowercased>"}`.

- [ ] **Step 1: Write the failing tests**

`test_parsing_hinter_pipeline.py`:

```python
@pytest.mark.parametrize(
    "text",
    ["slice it another way", "Slice that a different way", "cut it another way", "break it down differently", "slice it differently"],
)
def test_slice_phrase_without_a_dimension_raises_dimension_unspecified(text):
    parsed = parse_turn(text, NO_SLOTS, REFERENCE_DATE, FakeDataClient())
    issues = [i for i in parsed.issues if i.code == "dimension_unspecified"]
    assert len(issues) == 1
    assert issues[0].kind == "dimension"
    assert issues[0].message == "Which dimension should I slice by?"
    assert issues[0].phrase and issues[0].phrase.casefold() in text.casefold()


@pytest.mark.parametrize("text", ["slice it by port", "cut it another way by supplier", "top gp customers in april 2026"])
def test_slice_phrase_with_a_by_cue_raises_no_issue(text):
    parsed = parse_turn(text, NO_SLOTS, REFERENCE_DATE, FakeDataClient())
    assert not any(i.code == "dimension_unspecified" for i in parsed.issues)
```

`test_chat_orchestrator.py`:

```python
def test_slice_it_another_way_asks_with_dimension_chips(monkeypatch):
    outcome, decoded, writer, state, data = _run_turn_for(monkeypatch, "slice it another way", conversation_id="conv-slice")
    assert outcome.status == "clarify"
    kinds = [kind for _, kind, _ in decoded]
    assert kinds == ["accepted", "part", "part", "done"]
    chips = decoded[1][2]
    assert chips["kind"] == "chips"
    options = chips["payload"]["options"]
    assert [o["label"] for o in options] == [
        "Customer", "Port", "Supplier", "Deal class", "Customer office", "Supplier office", "Ship type", "Month",
    ]
    assert options[1] == {"id": "slice:LOC_NM", "label": "Port", "send_text": "break that down by port"}
    assert options[-1]["send_text"] == "break that down by month"
    assert decoded[2][2]["payload"] == {"markdown": orchestrator.SLICE_TEXT}
    assert not [c for c in data.calls if c[0].startswith("run_")]
    assert writer.finalize_calls[0]["status"] == "clarify"


def test_customer_ambiguity_still_wins_over_a_slice_issue():
    issues = (
        ParseIssue(code="dimension_unspecified", message=orchestrator.SLICE_TEXT, kind="dimension"),
        ParseIssue(code="customer_ambiguous", message="m", candidates=("A", "B")),
    )
    assert orchestrator._select_clarify_issue(issues).code == "customer_ambiguous"
```

Match `_parse_frame`'s tuple shape and the kind sequence used by `test_port_near_miss_turn_produces_port_chips_and_port_question_no_dispatch` (chips part, then text part, then done).

- [ ] **Step 2: Run red** into `t6-red.txt`. Expected: no `dimension_unspecified` issue produced; the orchestrator test runs the dev router instead of clarifying (status `ok` or capability text).

- [ ] **Step 3: Implement**

`pipeline.py` (constants near `_CLEAR_CUSTOMER_RE`; ASCII):

```python
_KIND_DIMENSION = "dimension"
_DIMENSION_UNSPECIFIED = "dimension_unspecified"
SLICE_TEXT = "Which dimension should I slice by?"
_SLICE_RE = re.compile(
    r"\b(?i:slice|cut|split|break)\s+(?:it|that|this)(?:\s+down)?\s+"
    r"(?:(?:another|a different|some other)\s+way|differently)\b"
)
_BY_CUE_RE = re.compile(r"\b(?i:by)\s+\w")


def _plan_slice(normalized: str) -> ParseIssue | None:
    """Reports design 7.6's last row: a slice phrase that names no dimension
    is a clarification, not a guess. A "by <word>" anywhere in the message
    means the user did name one, so no issue (Review Focus 4)."""
    match = _SLICE_RE.search(normalized)
    if match is None or _BY_CUE_RE.search(normalized) is not None:
        return None
    return ParseIssue(
        code=_DIMENSION_UNSPECIFIED, message=SLICE_TEXT, kind=_KIND_DIMENSION, phrase=match.group(0)
    )
```

In `parse_turn`, add `_plan_slice(normalized)` as the LAST entry of the `issues` generator (after the period issue), and document it as step 9's fourth source.

`orchestrator.py`:

```python
_DIMENSION_UNSPECIFIED = "dimension_unspecified"
_KIND_DIMENSION = "dimension"
SLICE_TEXT = "Which dimension should I slice by?"
# Owner ruling 3 (PR B plan): the seven business cuts plus month, in this order.
SLICE_CHIPS: tuple[tuple[str, str], ...] = (
    ("CUST_NM", "Customer"),
    ("LOC_NM", "Port"),
    ("SUPPLIER_NM", "Supplier"),
    ("DEAL_CLASSIFICATION_TRADE_CUT", "Deal class"),
    ("CUSTOMER_TEAM_NAME", "Customer office"),
    ("PRIMARY_SUPPLY_TEAM_OFFICE", "Supplier office"),
    ("VESSEL_DASHBOARD_SHIPTYPE_GRP", "Ship type"),
    ("MONTH", "Month"),
)
```

`_select_clarify_issue`: append `(_DIMENSION_UNSPECIFIED, _KIND_DIMENSION)` as the LAST tuple in its `for` list (customer ambiguity, then port, then unknown port, then slice). Update its docstring.

`_finish_clarify`: add a branch before the customer `else`:

```python
    elif ambiguous_issue.kind == _KIND_DIMENSION:
        message = SLICE_TEXT
        sink.push_part(
            "chips",
            {
                "options": [
                    {"id": f"slice:{column}", "label": label, "send_text": f"break that down by {label.lower()}"}
                    for column, label in SLICE_CHIPS
                ]
            },
        )
```

Update the function docstring and the module docstring's "clarify" bullet.

- [ ] **Step 4: Run green** into `t6-green.txt` (both test files, plus `test_llm_prompts.py` and `test_chat_state_devrouter.py` for the ASCII pins). Then run the **pg suite** serially into `t6-pg.txt` (chat text changed): expected 355 passed, 1 skipped. `ruff check .` clean.

- [ ] **Step 5: Commit**

```
feat(chat): "slice it another way" -- deterministic dimension_unspecified issue and dimension chips
```

---

### Task 7: Dev router cues for the PR B gate (Opus)

**Files:**
- Modify: `backend/poseidon/core/chat/dev_router.py`
- Test: `backend/tests/test_chat_state_devrouter.py`

**Interfaces:**
- Consumes: state-block lines `Carried office: <COL> = <value>. ...` (prompts.py) and `Carried customer: <value>`.
- Produces: `_ParsedState.carried_office_column: str | None`; `_group_by_cues(text) -> list[str]`; `_mentions_share(text) -> bool`; `_metric_query_call` emits `group_by` (list), `top_n`, `share_of_total`, and the carried-customer filter under the rules below.

Rules (owner ruling 2):
- `_GROUP_BY_CUES` (longest phrase first): `by customer office`→`CUSTOMER_TEAM_NAME`, `by supplier office`→`PRIMARY_SUPPLY_TEAM_OFFICE`, `by deal class`→`DEAL_CLASSIFICATION_TRADE_CUT`, `by ship type`→`VESSEL_DASHBOARD_SHIPTYPE_GRP`, `by customer`→`CUST_NM`, `by port`→`LOC_NM`, `by supplier`→`SUPPLIER_NM`, `by month`→`MONTH`, `by quarter`→`QUARTER`, `by year`→`YEAR`. All matches in text order, deduplicated, max three.
- A share cue (`how much of|share of|percent of|portion of|makes up|% of`) with a carried office column → `share_of_total: [column]`, and no `group_by`/`compare_period`/`top_n`. A share cue with no carried office → no share (plain total).
- Group-by cues → `group_by: [...]`, `top_n: 5`; `compare_period` is kept only when the list is a single plain dimension (the skill refuses otherwise). Without cues the old `top` rule stands.
- Carried customer: when a group-by or share cue is present, no customer is resolved this turn, and `carried_customer` is set → `{"column": "CUST_NM", "values": [carried]}` first in `filters`.

- [ ] **Step 1: Write the failing tests** (append; use the file's `_system`, `_parsed`, `_user_message`, `_PERIOD_A`, `_assert_valid_metric_query_args`)

```python
def _call_for(text, *, slots=None, parsed=None):
    router = DevDeterministicRouter()
    system = _system(parsed or _parsed(period_a=_PERIOD_A), slots)
    response = router.invoke(system=system, messages=[_user_message(text)], tools=[], model="m", params={})
    (call,) = response.tool_calls
    _assert_valid_metric_query_args(call)
    return call.arguments


def test_by_month_cue_emits_a_month_group_by():
    args = _call_for("give me the GP breakdown for 2025 by month")
    assert args["group_by"] == ["MONTH"] and args["top_n"] == 5


def test_longer_cue_wins_over_its_prefix():
    assert _call_for("gp by customer office")["group_by"] == ["CUSTOMER_TEAM_NAME"]
    assert _call_for("gp by customer")["group_by"] == ["CUST_NM"]


def test_by_port_by_month_emits_both_in_text_order():
    assert _call_for("and by port by month")["group_by"] == ["LOC_NM", "MONTH"]


def test_group_by_cue_uses_the_carried_customer_when_none_is_resolved_this_turn():
    args = _call_for("then by port", slots=ConversationSlots(customer="Northstar Lines"))
    assert args["filters"] == [{"column": "CUST_NM", "values": ["Northstar Lines"]}]
    assert args["group_by"] == ["LOC_NM"]


def test_without_a_cue_the_carried_customer_is_still_ignored():
    args = _call_for("gp this period", slots=ConversationSlots(customer="Northstar Lines"))
    assert "filters" not in args


def test_share_cue_with_a_carried_office_emits_share_of_total_on_that_column():
    slots = ConversationSlots(customer="Northstar Lines", office_column="CUSTOMER_TEAM_NAME", office_value="Rotterdam")
    args = _call_for("how much of my GP is of the total account GP", slots=slots)
    assert args["share_of_total"] == ["CUSTOMER_TEAM_NAME"]
    assert args["filters"] == [{"column": "CUST_NM", "values": ["Northstar Lines"]}]
    assert "group_by" not in args and "compare_period" not in args


def test_share_cue_without_a_carried_office_is_a_plain_total():
    args = _call_for("how much of my GP is that")
    assert "share_of_total" not in args


def test_multi_column_cue_drops_a_resolved_compare_period():
    parsed = _parsed(period_a=_PERIOD_A, period_b=_PERIOD_B)
    args = _call_for("by port by month", parsed=parsed)
    assert args["group_by"] == ["LOC_NM", "MONTH"] and "compare_period" not in args
    single = _call_for("by port", parsed=parsed)
    assert single["group_by"] == ["LOC_NM"] and "compare_period" in single
```

(`_PERIOD_B` exists if the compare-period tests define one; otherwise define `_PERIOD_B = PeriodWindow(date(2025, 4, 1), date(2025, 5, 1))` beside `_PERIOD_A`.)

- [ ] **Step 2: Run red** into `t7-red.txt`. Expected: `KeyError: 'group_by'`, `'share_of_total'`, `'filters'` on the new tests; every existing dev-router test still green.

- [ ] **Step 3: Implement**

Constants (ASCII) after `_BRIEF_WORD_RE`:

```python
# Phase 17 PR B (owner ruling: the stub-mode browser gate must route offline).
# Longest phrase first so "by customer office" never reads as "by customer".
_GROUP_BY_CUES: tuple[tuple[str, str], ...] = (
    ("by customer office", "CUSTOMER_TEAM_NAME"),
    ("by supplier office", "PRIMARY_SUPPLY_TEAM_OFFICE"),
    ("by deal class", "DEAL_CLASSIFICATION_TRADE_CUT"),
    ("by ship type", "VESSEL_DASHBOARD_SHIPTYPE_GRP"),
    ("by customer", "CUST_NM"),
    ("by port", "LOC_NM"),
    ("by supplier", "SUPPLIER_NM"),
    ("by month", "MONTH"),
    ("by quarter", "QUARTER"),
    ("by year", "YEAR"),
)
_GROUP_BY_CUE_RE = re.compile(
    r"\b(" + "|".join(re.escape(phrase) for phrase, _ in _GROUP_BY_CUES) + r")\b"
)
_GROUP_BY_BY_PHRASE = dict(_GROUP_BY_CUES)
_SHARE_CUE_RE = re.compile(r"\b(how much of|share of|percent of|portion of|makes up)\b|% of")
_CARRIED_OFFICE_RE = re.compile(r"^Carried office: (?P<column>\w+) = (?P<value>.+?)\. ", re.MULTILINE)
_MAX_GROUP_BY = 3
```

Helpers:

```python
def _group_by_cues(messages: list[dict]) -> list[str]:
    text = _last_user_text(messages)
    if text is None:
        return []
    columns: list[str] = []
    for match in _GROUP_BY_CUE_RE.finditer(text.casefold()):
        column = _GROUP_BY_BY_PHRASE[match.group(1)]
        if column not in columns:
            columns.append(column)
    return columns[:_MAX_GROUP_BY]


def _mentions_share(messages: list[dict]) -> bool:
    text = _last_user_text(messages)
    return text is not None and _SHARE_CUE_RE.search(text.casefold()) is not None
```

`_ParsedState` gains `carried_office_column: str | None`; `_parse_state` fills it from `_CARRIED_OFFICE_RE`.

`_metric_query_call` becomes:

```python
def _metric_query_call(parsed_state: _ParsedState, messages: list[dict]) -> LLMResponse:
    start, end = parsed_state.period
    arguments: dict[str, object] = {
        "entity": _DEFAULT_ENTITY,
        "metrics": [_DEFAULT_METRIC],
        "period": {"start": start, "end": end},
    }
    cues = _group_by_cues(messages)
    share = _mentions_share(messages) and parsed_state.carried_office_column is not None

    filters = []
    if parsed_state.customer is not None:
        filters.append({"column": _CUST_NM, "values": [parsed_state.customer]})
    elif (cues or share) and parsed_state.carried_customer is not None:
        filters.append({"column": _CUST_NM, "values": [parsed_state.carried_customer]})
    if parsed_state.port is not None:
        filters.append({"column": _LOC_NM, "values": [parsed_state.port]})
    if filters:
        arguments["filters"] = filters

    if share:
        arguments["share_of_total"] = [parsed_state.carried_office_column]
    elif cues:
        arguments["group_by"] = cues
        arguments["top_n"] = _GROUP_BY_TOP_N
        single_plain = len(cues) == 1 and cues[0] not in ("MONTH", "QUARTER", "YEAR")
        if parsed_state.compare_period is not None and single_plain:
            compare_start, compare_end = parsed_state.compare_period
            arguments["compare_period"] = {"start": compare_start, "end": compare_end}
    elif parsed_state.compare_period is not None:
        compare_start, compare_end = parsed_state.compare_period
        arguments["compare_period"] = {"start": compare_start, "end": compare_end}
    elif _mentions_top(messages):
        arguments["group_by"] = _CUST_NM
        arguments["top_n"] = _GROUP_BY_TOP_N

    call = ToolCall(id=_TOOL_CALL_ID, name=_METRIC_QUERY_SKILL_ID, arguments=arguments)
    return LLMResponse(text="", tool_calls=(call,), stop_reason="tool_use", input_tokens=0, output_tokens=0)
```

(The dev router does not import `query_builder`; the three bucket names are spelled here and cross-checked by a test that compares them with `TIME_BUCKETS`: add `test_dev_router_bucket_names_match_the_builder` asserting `set(dev_router._BUCKET_NAMES) == set(TIME_BUCKETS)`; define `_BUCKET_NAMES = ("MONTH", "QUARTER", "YEAR")` and use it in `single_plain`.)

Add a "PR B cues" section to the module docstring's behaviour table (case (b) grows the cue rules; nothing else moves).

- [ ] **Step 4: Run green** into `t7-green.txt` (dev router tests, orchestrator tests, `test_live_chat_sse.py` offline parts). `ruff check .` clean.

- [ ] **Step 5: Commit**

```
feat(dev_router): group-by, share and carried-customer cues so the PR B gate routes offline
```

---

### Task 8: The remaining §7.6 routing cases and the harness (Sonnet)

**Files:**
- Modify: `backend/tests/routing_cases.yml`
- Modify: `backend/tests/test_llm_loop.py`

**Interfaces:**
- Consumes: `Args` (Task 1), `FrameResult`/`FrameRow`.
- Produces: eight new yml entries; `_slots_for` reads `active_customer`, `period_a`/`period_a_end` (ISO strings), `office_column`, `office_value`, `report_ref`, `pass_through`; `assert_args_subset` accepts list patterns and list-valued `group_by`; `_ARGS_SUBSET_TOP_LEVEL` gains `share_of_total`; `OFFLINE_CASES` = cases with a `stub_script`; `LIVE_CASES` = `stub_script` or `live_only` cases; `FakeDataClient.run_frame_query`.

- [ ] **Step 1: Write the failing tests and the yml**

Append to `routing_cases.yml` after `report_lookup_top_ports` (keep the Rotterdam customer-report state of the A2 cases; `period` windows are September 2026 vs August 2026 like A2's, calendar 2025 where the case says so):

```yaml
# --- Phase 17 PR B: the remaining reports-design 7.6 rows -------------------

- id: report_followup_full_account_gp
  setup:
    mode: default
    state: { perspective: customer, office_column: CUSTOMER_TEAM_NAME, office_value: Rotterdam, report_ref: run-1, active_customer: "Northstar Lines" }
  user: "give me the full account GP"
  expect:
    skill: data_qa.metric_query
    args_subset: { customer: "Northstar*", scope: book, compare_period: "*" }
  execution:
    stub_script:
      - stop_reason: tool_use
        input_tokens: 1200
        output_tokens: 90
        tool_use:
          id: tool-1
          skill: data_qa.metric_query
          arguments:
            entity: MARINE_SALES_PLANNING_V
            metrics: [GP]
            period: { start: "2026-09-01", end: "2026-10-01" }
            compare_period: { start: "2026-08-01", end: "2026-09-01" }
            filters: [{ column: CUST_NM, values: [Northstar Lines] }]
            scope: book
      - stop_reason: end_turn
        input_tokens: 1450
        output_tokens: 60
        text: "Here is Northstar Lines' GP across the whole book, September against August."

- id: report_followup_share_of_total
  setup:
    mode: default
    state: { perspective: customer, office_column: CUSTOMER_TEAM_NAME, office_value: Rotterdam, report_ref: run-1, active_customer: "Northstar Lines" }
  user: "how much of my GP is of the total account GP"
  expect:
    skill: data_qa.metric_query
    args_subset: { customer: "Northstar*", scope: office, share_of_total: CUSTOMER_TEAM_NAME }
  execution:
    stub_script:
      - stop_reason: tool_use
        input_tokens: 1210
        output_tokens: 88
        tool_use:
          id: tool-1
          skill: data_qa.metric_query
          arguments:
            entity: MARINE_SALES_PLANNING_V
            metrics: [GP]
            period: { start: "2026-09-01", end: "2026-10-01" }
            filters: [{ column: CUST_NM, values: [Northstar Lines] }]
            scope: office
            share_of_total: [CUSTOMER_TEAM_NAME]
      - stop_reason: end_turn
        input_tokens: 1460
        output_tokens: 58
        text: "Rotterdam's share of Northstar Lines' GP."

- id: report_followup_gp_by_month_2025
  setup:
    mode: default
    state: { perspective: customer, office_column: CUSTOMER_TEAM_NAME, office_value: Rotterdam, report_ref: run-1 }
  user: "give me the GP breakdown for Northstar Lines for 2025 by month"
  expect:
    skill: data_qa.metric_query
    args_subset: { customer: "Northstar*", scope: office, group_by: MONTH }
  execution:
    stub_script:
      - stop_reason: tool_use
        input_tokens: 1220
        output_tokens: 95
        tool_use:
          id: tool-1
          skill: data_qa.metric_query
          arguments:
            entity: MARINE_SALES_PLANNING_V
            metrics: [GP]
            period: { start: "2025-01-01", end: "2026-01-01" }
            filters: [{ column: CUST_NM, values: [Northstar Lines] }]
            group_by: [MONTH]
            scope: office
      - stop_reason: end_turn
        input_tokens: 1480
        output_tokens: 62
        text: "Northstar Lines' GP by month for 2025."

- id: report_followup_then_by_port
  setup:
    mode: default
    state:
      perspective: customer
      office_column: CUSTOMER_TEAM_NAME
      office_value: Rotterdam
      report_ref: run-1
      active_customer: "Northstar Lines"
      period_a: "2025-01-01"
      period_a_end: "2026-01-01"
  user: "then by port"
  expect:
    skill: data_qa.metric_query
    args_subset: { customer: "Northstar*", group_by: LOC_NM }
  execution:
    stub_script:
      - stop_reason: tool_use
        input_tokens: 1190
        output_tokens: 80
        tool_use:
          id: tool-1
          skill: data_qa.metric_query
          arguments:
            entity: MARINE_SALES_PLANNING_V
            metrics: [GP]
            period: { start: "2025-01-01", end: "2026-01-01" }
            filters: [{ column: CUST_NM, values: [Northstar Lines] }]
            group_by: [LOC_NM]
      - stop_reason: end_turn
        input_tokens: 1440
        output_tokens: 55
        text: "Northstar Lines' 2025 GP by port."

- id: report_followup_by_port_by_month
  setup:
    mode: default
    state:
      perspective: customer
      office_column: CUSTOMER_TEAM_NAME
      office_value: Rotterdam
      report_ref: run-1
      active_customer: "Northstar Lines"
      period_a: "2025-01-01"
      period_a_end: "2026-01-01"
  user: "and by port by month"
  expect:
    skill: data_qa.metric_query
    args_subset: { customer: "Northstar*", group_by: [LOC_NM, MONTH] }
  execution:
    stub_script:
      - stop_reason: tool_use
        input_tokens: 1195
        output_tokens: 84
        tool_use:
          id: tool-1
          skill: data_qa.metric_query
          arguments:
            entity: MARINE_SALES_PLANNING_V
            metrics: [GP]
            period: { start: "2025-01-01", end: "2026-01-01" }
            filters: [{ column: CUST_NM, values: [Northstar Lines] }]
            group_by: [LOC_NM, MONTH]
      - stop_reason: end_turn
        input_tokens: 1450
        output_tokens: 57
        text: "Northstar Lines' 2025 GP by port by month."

- id: report_followup_compare_to_june
  setup:
    mode: default
    state: { perspective: customer, office_column: CUSTOMER_TEAM_NAME, office_value: Rotterdam, report_ref: run-1 }
  user: "compare to June instead"
  expect: { carry: "period B becomes June, period A and the carried office are kept" }
  execution:
    parser_case: "backend/tests/test_c03_period_carry.py::test_3_compare_to_another_month_swaps_only_the_comparison"
    reason: >-
      A comparison-only phrase is a deterministic period-carry decision (D62),
      not a router decision; the C03 suite pins it.

- id: report_followup_break_down_by_deal_class
  setup:
    mode: default
    state:
      perspective: customer
      office_column: CUSTOMER_TEAM_NAME
      office_value: Rotterdam
      report_ref: run-1
      pass_through: [[CUST_NM, Northstar Lines], [CUST_NM, Meridian Bunkering]]
  user: "break that down by deal class"
  expect:
    skill: data_qa.metric_query
    args_subset: { customer: "Northstar*", group_by: DEAL_CLASSIFICATION_TRADE_CUT }
  execution:
    stub_script:
      - stop_reason: tool_use
        input_tokens: 1230
        output_tokens: 96
        tool_use:
          id: tool-1
          skill: data_qa.metric_query
          arguments:
            entity: MARINE_SALES_PLANNING_V
            metrics: [GP, VOLUME]
            period: { start: "2026-09-01", end: "2026-10-01" }
            filters: [{ column: CUST_NM, values: [Northstar Lines, Meridian Bunkering] }]
            group_by: [DEAL_CLASSIFICATION_TRADE_CUT]
      - stop_reason: end_turn
        input_tokens: 1500
        output_tokens: 64
        text: "The report's top customers by deal class."

- id: slice_it_another_way
  setup:
    mode: default
    state: { perspective: customer, office_column: CUSTOMER_TEAM_NAME, office_value: Rotterdam, report_ref: run-1 }
  user: "slice it another way"
  expect: { clarify: true }
  execution:
    orchestrator_case: "backend/tests/test_chat_orchestrator.py::test_slice_it_another_way_asks_with_dimension_chips"
    reason: >-
      The parser's dimension_unspecified issue short-circuits the turn before
      any router runs (reports design 7.6, last row); the orchestrator suite pins it.
```

`test_llm_loop.py` changes to tests (write these first, red):

- `DOC_CASE_IDS` gains the eight ids in that order.
- `test_exactly_two_cases_are_live_only_and_each_names_its_owning_phase`: `len(live_only) == 2`, `len(OFFLINE_CASES) == 13`.
- `test_the_offline_cases_...`: `assert len(OFFLINE_CASES) == 13`; the followups list grows by the six stub ids; the loop asserting `METRIC_QUERY` covers `followups[:3] + followups[4:]`.
- New:

```python
def test_pointer_cases_name_an_existing_test_and_are_neither_offline_nor_live():
    pointers = [c for c in ROUTING_CASES if "parser_case" in c["execution"] or "orchestrator_case" in c["execution"]]
    assert [c["id"] for c in pointers] == ["report_followup_compare_to_june", "slice_it_another_way"]
    for case in pointers:
        target = case["execution"].get("parser_case") or case["execution"]["orchestrator_case"]
        path, name = target.split("::")
        assert (Path(__file__).resolve().parents[1] / Path(path).relative_to("backend")).exists()
        assert name in (Path(__file__).resolve().parents[1] / Path(path).relative_to("backend")).read_text(encoding="utf-8")
        assert case not in OFFLINE_CASES and case not in LIVE_CASES


def test_assert_args_subset_reads_list_group_by_and_list_patterns():
    arguments = {"group_by": ["LOC_NM", "MONTH"], "share_of_total": ["CUSTOMER_TEAM_NAME"]}
    assert_args_subset({"group_by": "MONTH"}, arguments)
    assert_args_subset({"group_by": ["LOC_NM", "MONTH"]}, arguments)
    assert_args_subset({"share_of_total": "CUSTOMER_TEAM_NAME"}, arguments)
    with pytest.raises(AssertionError):
        assert_args_subset({"group_by": ["LOC_NM", "YEAR"]}, arguments)


def test_slots_for_reads_the_pr_b_setup_vocabulary():
    slots = {case["id"]: _slots_for(case) for case in ROUTING_CASES}
    then_by_port = slots["report_followup_then_by_port"]
    assert then_by_port.customer == "Northstar Lines"
    assert (then_by_port.period_a, then_by_port.period_a_end) == (date(2025, 1, 1), date(2026, 1, 1))
    assert then_by_port.office_value == "Rotterdam"
    assert slots["report_followup_break_down_by_deal_class"].pass_through == (
        ("CUST_NM", "Northstar Lines"), ("CUST_NM", "Meridian Bunkering"),
    )
```

- [ ] **Step 2: Run red** into `t8-red.txt`. Expected: `DOC_CASE_IDS` mismatch; `KeyError: 'stub_script'` in `_script_from` for the pointer cases; `NameError: LIVE_CASES`; `assert_args_subset` failing on list patterns.

- [ ] **Step 3: Implement the harness**

```python
OFFLINE_CASES = [case for case in ROUTING_CASES if "stub_script" in case["execution"]]
LIVE_CASES = [
    case for case in ROUTING_CASES
    if "stub_script" in case["execution"] or case["execution"].get("live_only")
]

_ARGS_SUBSET_TOP_LEVEL = ("group_by", "scope", "compare_period", "section", "share_of_total")


def _candidate_values(key: str, arguments: dict) -> list[str]:
    if key == "metric":
        return [str(value) for value in arguments.get("metrics") or []]
    if key in _ARGS_SUBSET_TOP_LEVEL:
        value = arguments.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]
    ...  # unchanged


def assert_args_subset(args_subset: dict, arguments: dict) -> None:
    for key, pattern in args_subset.items():
        values = _candidate_values(key, arguments)
        patterns = pattern if isinstance(pattern, list) else [pattern]
        for one in patterns:
            assert any(_matches(str(one), value) for value in values), (
                f"expected {key}={one!r} among {values!r} (arguments: {arguments!r})"
            )


def _slots_for(case: dict) -> ConversationSlots:
    ...  # existing lines, then:
    for slot in ("period_a", "period_a_end", "period_b", "period_b_end"):
        if slot in state:
            kwargs[slot] = date.fromisoformat(state[slot])
    for slot in ("perspective", "office_column", "office_value", "report_ref"):
        if slot in state:
            kwargs[slot] = state[slot]
    return ConversationSlots(**kwargs)
```

The live executor parametrises over `LIVE_CASES` (not `ROUTING_CASES`). `FakeDataClient` gains:

```python
    def run_frame_query(self, spec) -> FrameResult:
        self.calls.append(("run_frame_query", spec))
        buckets = [c for c in spec.group_by if c in TIME_BUCKETS]
        keys_per_column = {
            "CUST_NM": list(_CUSTOMERS[:3]),
            "LOC_NM": ["Singapore", "Rotterdam"],
            "DEAL_CLASSIFICATION_TRADE_CUT": ["Spot", "Contract"],
        }
        rows = []
        plain = [c for c in spec.group_by if c not in TIME_BUCKETS]
        combos = [()]
        for column in plain:
            combos = [(*combo, key) for combo in combos for key in keys_per_column.get(column, ["Other"])]
        for combo in combos:
            for bucket_start in (["2025-01-01", "2025-02-01"] if buckets else [None]):
                keys = list(combo)
                if bucket_start is not None:
                    keys.insert(spec.group_by.index(buckets[0]), bucket_start)
                rows.append(FrameRow(keys=tuple(keys), values={m: 100.0 for m in spec.metrics}))
        return FrameResult(entity=spec.entity, group_by=tuple(spec.group_by), rows=rows)
```

Update the file's module docstring paragraph about the case count. Update `test_every_case_setup_maps_onto_real_conversation_slots` only if it enumerates ids.

- [ ] **Step 4: Run green** into `t8-green.txt` (`tests/test_llm_loop.py` whole file offline). `ruff check .` clean.

- [ ] **Step 5: Commit**

```
test(routing): the remaining reports-design 7.6 cases in stub mode; harness reads list group_by, share_of_total, carried customer and period
```

---

### Task 9: The three A2 leftovers (Sonnet)

**Files:**
- Modify: `backend/poseidon/tasks/data_qa/skills/metric_query/tools/compare_breakdown.py`
- Modify: `backend/poseidon/tasks/reporting/skills/report_lookup/tools/sections.py`
- Test: `metric_query/tests/test_tools.py`, `report_lookup/tests/test_lookup.py`

- [ ] **Step 1: Write the failing tests**

`test_tools.py`: in `test_compare_breakdown_parts_shape_and_driver` (or a new test if that one does not read the proof), assert:

```python
    assert "Group by: CUST_NM (top 5 shown; merged over the top 50 per window)" in proof
    assert not any(line == "Group by: CUST_NM (top 50)" for line in proof)
```

Use the test's actual `top_n` argument in the string.

`test_lookup.py`: replace `test_kpis_is_a_metric_grid_with_prior_as_a` with:

```python
def test_kpis_is_a_table_of_the_payloads_display_strings(registry):
    store = _FakeStore()
    result = _lookup(registry, _ctx(store=store), section="kpis")
    assert store.calls == [("dev|alice", RUN_ID)]
    assert result.proof == [HEAD, "Section: kpis"]
    (part,) = result.parts
    assert part["kind"] == "table"
    assert part["payload"]["columns"] == ["KPI", "Prior", "Current", "Change", "%"]
    by_label = {row[0]: row for row in part["payload"]["rows"]}
    gp = minimal_payload()["kpis"]["GP"]["display"]
    assert by_label["Gross Profit (USD)"] == ["Gross Profit (USD)", gp["prior"], gp["current"], gp["change"], gp["pct"]]
    assert "Win Rate" in by_label
    assert all(isinstance(cell, str) for row in part["payload"]["rows"] for cell in row)
```

and add:

```python
def test_one_row_remaining_is_singular(registry):
    first = _lookup(registry, _ctx(), section="entities_by_gp", limit=2)
    _, remaining = first.parts
    assert remaining["payload"]["markdown"] == "1 row remaining; ask again with offset=2."
```

(`minimal_payload` is already imported in that test module or its fixtures; the fixture has three `entities_by_gp` rows, so `limit=2` leaves one.)

- [ ] **Step 2: Run red** into `t9-red.txt`.

- [ ] **Step 3: Implement**

`compare_breakdown.py`: after `proof = _header_proof(spec_current, settings, spec_prior.period)` add

```python
    proof[-1] = (
        f"Group by: {spec_current.group_by} (top {top_n} shown; "
        f"merged over the top {spec_current.top_n} per window)"
    )
```

and update the module docstring's proof paragraph.

`sections.py`:

```python
def _kpis(payload: dict[str, Any]) -> dict:
    rows = []
    for name, kpi in payload["kpis"].items():
        friendly, unit = KPI_FRIENDLY.get(name, (name.replace("_", " ").title(), None))
        label = f"{friendly} ({unit})" if unit else friendly
        d = kpi["display"]
        rows.append([label, d["prior"], d["current"], d["change"], d["pct"]])
    return table_part(columns=["KPI", "Prior", "Current", "Change", "%"], rows=rows)
```

Remove the now-unused `metric_grid_part` import if nothing else uses it. In `_paged`:

```python
    if end < total:
        remaining = total - end
        noun = "row" if remaining == 1 else "rows"
        parts.append(text_part(f"{remaining} {noun} remaining; ask again with offset={end}."))
```

Grep `backend/tests` and `contracts/turns` for `metric_grid` assertions tied to the lookup's `kpis` section and for `rows remaining` (the pg flow test and `test_live_chat_sse.py`); update any pinned text and name each in the report.

- [ ] **Step 4: Run green** into `t9-green.txt`; run the **pg suite** into `t9-pg.txt` (expected 355 passed, 1 skipped); `ruff check .` clean. If `contracts/turns` changed (it should not: no contract scenario reads the `kpis` section; confirm by grep), stop and report.

- [ ] **Step 5: Commit**

```
fix(report): compare header names the shown cap; singular "1 row remaining"; lookup KPIs as a display-string table
```

---

### Task 10: Docs, decision log, spec addendum, verified figures (Sonnet)

**Files:**
- Modify: `docs/architecture/00-overview.md` (D75–D78; D73 annotation)
- Modify: `docs/architecture/02-backend-skills.md` (PR B paragraph after the A2 one)
- Modify: `docs/architecture/03-llm-routing.md` (§6 paragraph; state block `Scope hint:`; dev router cues)
- Modify: `docs/architecture/04-data-ontology.md` (`FrameQuerySpec` bullet: time buckets)
- Modify: `docs/architecture/08-build-phases.md` (PR B shipped bullet)
- Modify: `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` (§8.2 addendum)
- Modify: `CLAUDE.md` (Commands figures)

- [ ] **Step 1: Decision rows** (append after D74; the `Revises` column as shown)

| # | Decision | Revises |
|---|---|---|
| D75 | `metric_query.group_by` is one to three columns with at most one time bucket (`MONTH`/`QUARTER`/`YEAR`). One plain dimension keeps the breakdown SQL (now with `order_by`/`order`); anything else runs a `FrameQuerySpec` with `DATE_TRUNC` buckets, ranked and capped in Python: `top_n` caps the non-time keys by the order metric over the period, every bucket for those keys is shown chronologically, nothing is zero-filled. `WIN_RATE` and `compare_period` are refused on the frame path (422). Pass-through reads the first non-time column. | spec §8.2 (cap rule and refusals made explicit) |
| D76 | Share of total follows the C04 order: the stamped selection spec is built first, the total is derived by removing the named columns from its effective filters and is never restamped; D16 scope rides on both. A named column absent from the effective filters is a 422. Output `[Metric, Selection, Total, Share %]`, both filter sets in the proof. | — |
| D77 | A lexicon scope hinter sets `ParsedTurn.scope_hint` (`book`/`share`, `share` wins) and the state block shows `Scope hint:`; advisory. "Slice it another way" with no `by <dimension>` is the parser issue `dimension_unspecified`, answered deterministically with eight chips (seven business cuts plus Month) that send `break that down by <cut>`. | — |
| D78 | The stub-mode dev router carries the PR B cues (group-by phrases, share phrases on the carried office column, carried customer under a cue) so the browser gate runs without Bedrock. Its drift from the live router is reconciled in PR C. | A2 plan ruling 19 (dev router parity parked) |

Annotate D73: append `(PR B keeps the cap; the compare header now names the shown rows)`.

- [ ] **Step 2: Doc paragraphs**

`02-backend-skills.md`, after the A2 `metric_query` paragraph:

> **`data_qa.metric_query` extensions (Phase 17 PR B, D75–D76).** `group_by` is a list of one to three certified dimensions plus at most one of `MONTH`, `QUARTER`, `YEAR`; `order_by` (default the first metric) and `order` (`desc`/`asc`) rank it. One plain dimension keeps the breakdown query; two or more columns or a time bucket run a frame and are ranked and capped in Python (`top_n` keys, every bucket, bucket column first, labels `Jan 2025`/`Q1 2025`/`2025`). `share_of_total: [columns]` runs the stamped selection and a total with those filter columns removed, never restamped, and answers `[Metric, Selection, Total, Share %]`. Refused with a 422: `WIN_RATE` or `compare_period` on the frame path, `share_of_total` with `group_by` or `compare_period`, a share column absent from the effective filters.

Also, in the parsing section (§5), one sentence: the scope hinter (D77) and the `dimension_unspecified` clarification.

`03-llm-routing.md`: in §3's state-block list add `Scope hint: book|share` after `Skill hints`; in §6 replace the A2 paragraph's closing with: "Phase 17 PR B adds the remaining reports-design §7.6 rows: six stub-run cases (`report_followup_full_account_gp`, `report_followup_share_of_total`, `report_followup_gp_by_month_2025`, `report_followup_then_by_port`, `report_followup_by_port_by_month`, `report_followup_break_down_by_deal_class`) and two pointer entries whose decision is deterministic (`report_followup_compare_to_june` → the C03 suite; `slice_it_another_way` → the orchestrator suite). Pointer entries carry `parser_case`/`orchestrator_case` instead of a script and run in neither executor." Add a sentence on the dev router's PR B cues (D78).

`04-data-ontology.md`: extend the `FrameQuerySpec` bullet: "`group_by` may include one of `MONTH`/`QUARTER`/`YEAR`, rendered `CAST(DATE_TRUNC('<unit>', <date column>) AS DATE)` with the Snowflake `TO_DATE` wrap for VARCHAR date columns; bucket keys come back as ISO strings."

`08-build-phases.md`: replace the `PR B (next)` bullet with a shipped bullet naming D75–D78, the eight routing cases, the dev router cues and the three A2 leftovers.

Spec §8.2 addendum (one paragraph at the end of §8.2): "**Realised in PR B (2026-10-18):** cap rule (top_n keys, every bucket), the two frame-path refusals, D73's merge kept with a reworded header, the eight curated slice chips, and the offline dev-router gate in place of the live Bedrock gate (owner has no credentials). See D75–D78."

- [ ] **Step 3: Final gates, run once each, each to its own file, never piped**

1. Offline suite → `final-offline.txt`. Record passed/skipped/deselected.
2. `ruff check .` → `final-ruff.txt`.
3. `tests/contracts` → `final-contracts.txt`; `git status --short contracts/` must be empty (no contract changed; if it did, stop and report).
4. pg suite, serial, compose `db` on 5434 → `final-pg.txt`.
5. `cd web && npm test` → `final-web.txt`; `npx tsc --noEmit` → `final-tsc.txt` (untouched; figures refreshed only).

Update `CLAUDE.md`'s Commands block: date `2026-10-18 (Phase 17 PR B)`, the new offline figure, the pg figure, and the verified-figures paragraph. Add one Gotcha line: "`group_by` is a list now; the dev router and the stub scripts may still send a string, which `Args` wraps."

- [ ] **Step 4: Commit**

```
docs(phase-17): D75-D78, PR B in docs 02/03/04/08, spec 8.2 addendum, verified figures
```

---

## Owner gate (Carlos, in the browser, stub mode, after Task 10)

Compose with the A2 override (db on 5434, MinIO unexposed): `docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/compose.a2.yml up`. Open the Next.js chat.

1. Click **Monthly report** → Customer office → Rotterdam → June 2026. The report renders (A2 behaviour, sanity only).
2. Type `give me the GP breakdown for Northstar Lines for 2025 by month`. Expect a table with `Month` first (`Jan 2025` … `Jun 2025`, only months with rows), `Gross Profit` second; the proof ends `Group by: MONTH (top 5 keys; every bucket)`, `Order: GP desc`, `Rows: N`, then the office-scope line.
3. Type `then by port`. Expect `Port, Gross Profit` rows for Northstar Lines over 2025 (carried customer and year), the proof `Group by: LOC_NM (top 5)`.
4. Type `and by port by month`. Expect `Month, Port, Gross Profit` rows; up to five ports, each month they have rows, months in order.
5. Type `how much of my GP is of the total account GP`. Expect `[Metric, Selection, Total, Share %]` with one row; the proof shows `Selection filters:` carrying `CUSTOMER_TEAM_NAME IN (Rotterdam)` and `Total filters:` without it.
6. Type `slice it another way`. Expect the question `Which dimension should I slice by?` and eight chips; click **Supplier** and expect a `Supplier, Gross Profit` table.
7. Reload the conversation; every table replays.

Gate notes: the dev router answers with the fixed close `Certified answer for ...`; the figures are what matters. Anything that 422s or shows `No data for this selection.` is a finding for the morning review.

## Self-review (writing-plans checklist, run 2026-10-18)

**1. Spec coverage, §8.2.** Multi-column and time-bucket group-by: Tasks 1–3 (schema, builder, frame path; chronological buckets, bucket first). Ordering: Tasks 1 and 3 (`order_by`/`order`, `ASC` in the builder, Python ordering on the frame path, `Order:` proof line). Share of total with the C04 order, no restamp, D16 on both, 422 combinations, 20% acceptance: Task 4. Scope hinter (lexicon, after `skill_hinter`, `Scope hint:` line): Task 5. "Slice it another way" (deterministic chips from the parser's ambiguity path): Task 6. Remaining §7.6 cases in stub mode: Task 8 (the `router_live` run is Carlos's call and is not possible today; the cases remain runnable through `LIVE_CASES`). §11 row B: share acceptance (Task 4), time-bucket SQL snapshots both dialects (Task 2), hinter lexicon tests (Task 5), all eleven cases visible in one file (Task 8), the browser gate (Owner gate). §12 docs and §13: Task 10. The A2 close-out items 3, 4, 5: Task 9 (owner ruling 4). Carry-forwards weighed: compare header (Task 9), 51st-key artefact (plan ruling 12, disclosed), `office_chips` and D16 (dormant, left), the duplicated dispatch block (left; no third flow lands here).

**2. Placeholders.** None: every step carries its code or the exact text to write. Task 4's proof-line sketch is replaced inline by the plain string construction the step names.

**3. Type and name consistency.** `Args.group_by` is `list[str] | None` after validation everywhere (`build_spec`, `skill.run`, `_pass_through_column`, the harness); `is_frame_path` (Task 1) is what `skill.run` (Task 3) calls; `TIME_BUCKETS` (Task 1) is read by the builder (Task 2), `frame_breakdown` and the orchestrator (Task 3), the harness fake (Task 8), and cross-checked against the dev router's `_BUCKET_NAMES` (Task 7). `FrameRow.keys` are strings (Task 2) and `bucket_label` parses ISO (Task 3). `_order_proof` (Task 3) is defined in `format_parts` and imported by `frame_breakdown`. `denominator_spec`/`share_parts` (Task 4) match `_share` in `skill.py`. `ParsedTurn.scope_hint` defaults to `None` so every existing constructor (pipeline, four test files) keeps working. `SLICE_TEXT` is the same literal in `pipeline.py` and `orchestrator.py` (the orchestrator test asserts the rendered text; Task 6's pipeline test asserts the issue message). The yml ids in Task 8 match `DOC_CASE_IDS`, `LIVE_CASES` is what the live executor parametrises over, and `OFFLINE_CASES` keeps its order-dependent indexing (`[0]`, `[1]`, `[2]`) because the six new stub cases are appended after `report_lookup_top_ports`.

**4. Review Focus.** Each line has its test in the owning task (3, 4, 5, 6, 7).

---

## Paste this into the PR B execution chat (if a fresh chat is used)

> You are Fable, executing Phase 17 PR B (`metric_query` extensions) of Poseidon in C:\Users\carlo\github\latest_poseidon. The approved plan is `docs/superpowers/plans/2026-10-18-phase-17-prB-metric-query-extensions.md`; read it first, then the spec `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §8.2, `CLAUDE.md`, and `.claude/CLAUDE.md` "Model delegation" and "Git & PRs". Main is at `2f28751` (PR #9 merged). Create the branch exactly as the plan's Branch line says. Use superpowers:subagent-driven-development: one implementer per task with the plan's model split (Opus for 2, 3, 4, 7; Sonnet for 1, 5, 6, 8, 9, 10), a fresh reviewer per diff (Opus for 2, 3, 4, 7 and the whole branch), fix rounds until clean. The implementer runs the tests and its report is the evidence; the reviewer never re-runs the suite. Every pytest command carries `env -u PERPLEXITY_API_KEY` and the full marker exclusion; never pipe pytest; never run `router_live`. Keep the ledger in `.superpowers/sdd/2026-10-18-phase-17-prB-metric-query-extensions/`. At the end: write the PR description (what changed, why, how verified, with the final figures) and the morning-review list; then STOP. Do not run `gh pr create` and do not push unless Carlos says so in that chat.

---

## Execution record (2026-10-19)

Branch `phase-17-metric-query-extensions`, 15 commits over `main` @ 2f28751, HEAD 3277d9f. Not pushed; no PR open (both wait for Carlos). Ledger and morning review: `.superpowers/sdd/2026-10-18-phase-17-prB-metric-query-extensions/` (`progress.md`, `morning-review.md`, per-task briefs and reports, gate output files).

## PR description (ready to paste)

## Phase 17 PR B - `metric_query` extensions

Spec `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §8.2; decisions D75-D78 in `docs/architecture/00-overview.md`.

### What changed

- **Multi-column and time-bucket breakdowns.** `data_qa.metric_query`'s `group_by` is a list of one to three certified dimensions plus at most one of `MONTH`/`QUARTER`/`YEAR`. One plain dimension keeps today's breakdown SQL; anything else runs a `FrameQuerySpec` (the query builder gained `DATE_TRUNC` buckets, both dialects snapshot-pinned) and is ranked and capped in Python: `top_n` ranks the non-time keys over the whole period, every bucket for those keys is shown chronologically, bucket column first (`Jan 2025`, `Q1 2025`, `2025`). A ratio metric (`MARGIN`) ranks by the recomputed period ratio. Refused with a clear 422: `WIN_RATE` on the frame path and `compare_period` with a multi-column or bucketed group-by.
- **Ordering.** `order_by` (default the first metric) and `order` (`desc`/`asc`); the proof gains `Order: GP asc` only when the order is not the default, so every existing proof is byte-identical. The comparison path's 50-row windows always fetch the top 50 regardless of `order`.
- **Share of total** in the C04 order: the stamped selection is built first, the total is derived by removing the named filter columns and is never restamped, D16 row scope rides on both. Output `[Metric, Selection, Total, Share %]`, both filter sets in the proof; `n/a` for ratio metrics. Acceptance: office GP 20 of book 100 -> 20.0 with the office predicate only on the numerator.
- **Scope hinter.** A lexicon stage after the skill hinter sets `ParsedTurn.scope_hint` (`book`/`share`), rendered as `Scope hint:` in the state block; advisory.
- **"Slice it another way."** A slice phrase with no `by <dimension>` is the deterministic parse issue `dimension_unspecified`, answered with eight chips (Customer, Port, Supplier, Deal class, Customer office, Supplier office, Ship type, Month) that send `break that down by <cut>`. No model call.
- **Dev router cues** (stub mode, D78): `by <cut>` phrases, `by month/quarter/year`, share phrases on the carried office column, and the carried customer under a cue, so the browser gate runs without Bedrock. Drift from the live router is reconciled in PR C.
- **Routing cases.** The remaining reports-design §7.6 rows: six stub-run cases dispatching through the real skill, two pointer entries (`compare to June instead` -> the C03 suite; `slice it another way` -> the orchestrator suite). All eleven §7.6 rows are now in `backend/tests/routing_cases.yml`.
- **A2 leftovers.** Compare header `Group by: CUST_NM (top 5 shown; merged over the top 50 per window)`; singular `1 row remaining`; the report lookup's KPI section is a table of the payload's display strings.
- **Pass-through** after a bucketed table carries the first non-time column, deduplicated, ten at most.
- Docs 00/02/03/04/08, spec §8.2 addendum, `CLAUDE.md` figures.

### Why

Spec §8.2 (PR B of the Phase 17 split, D61). The owner rulings of 2026-10-18 (offline gate through the dev router, curated slice chips, the cap rule, the three A2 leftovers) and the execution rulings are listed in the morning review.

### How it was verified

Each implementer ran the tests and its report is the evidence; a fresh reviewer judged every diff without re-running; a whole-branch Opus review found two Important issues (compare windows inheriting `order`; ratio ranking on bucketed frames) fixed in one wave and re-reviewed clean. Final figures, each run once to its own file, never piped: offline Python 2354 passed, 1 failed (`test_auth0_bogus_kid_after_the_fetch_finished_401s_at_once_without_waiting`, a pre-existing 1.0s wall-clock check that fails under machine load, also on a clean checkout; an idle re-run should read 2355), 15 skipped, 136 deselected; `ruff check` clean; `tests/contracts` 9 passed and `contracts/` unchanged; pg suite 355 passed, 1 skipped (serial, compose db on 5434); web `npm test` 170 passed, 14 skipped, `tsc` clean (web untouched). Owner gate (browser, stub mode) listed in the morning review.

### Open product calls (morning review)

`n/a` share for ratio metrics vs a 422; period-ratio ranking of bucketed MARGIN frames vs refuse/sum; the slice question text; bucket labels; the three refusal messages.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## PR C handoff

The PR C planning-chat prompt lives in `docs/superpowers/plans/2026-10-23-phase-17-prC-handoff.md` (written after the merge; supersedes the draft that stood here).

## PR opened

https://github.com/sal1391/latest_poseidon/pull/10 (2026-10-23, 16 commits, HEAD 108e741). MERGED by Carlos on 2026-10-23; main at cd855b3. PR C handoff: `docs/superpowers/plans/2026-10-23-phase-17-prC-handoff.md`.
