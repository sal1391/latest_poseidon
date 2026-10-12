# Phase 17 — Report in chat: design

**Status:** design, owner-reviewed section by section on 2026-10-12; awaiting owner review of this
file before planning.
**Supersedes for Phase 17:** the Phase 15 and Phase 17 rows of the reports design's own phase table
(`2026-09-04-monthly-performance-reports-design.md` §12), which the migration design already
replaced with its §10 table. This document is the Phase 17 contract. Where it restates the reports
design it is the authority for what ships in this phase; where it is silent, the reports design
stands.
**Decision log entries:** D61–D69 (§2), to be copied into `docs/architecture/00-overview.md` in the
same commit as the first implementation PR.

---

## 1. What this phase delivers

A sales user opens a chat, clicks **Monthly report**, picks a perspective, an office and a month,
and gets the report in the thread: KPI tiles, five ranked tables whose footers equal the tiles,
movers with drivers and whole-book context, new and lost entities, a template narrative plus a
grounded model narrative, three suggested questions, a PDF card and the proof. Follow-up questions
answer from the carried office, perspective and month pair. The PDF is still downloadable after the
containers restart.

Everything Python computes; the model only writes prose over computed JSON and routes follow-ups
(D34). Nothing here changes the certified ontology, the metric formulas or the SSE envelope.

### In scope (four PRs, in order)

| PR | Branch | Contents |
|---|---|---|
| **0 — C03** | `fix/c03-period-carry` | Full period windows in the slots, the comparison carry rule, the orientation rule (§4). This document's first commit lives here. |
| **A — report in chat** | `phase-17-report-in-chat` | The `reporting` task (both perspectives, month to date), the frame query and shared math, the `report` part and its renderer, `report_run` and the download routes, the three-step flow, `report_lookup`, office scope stamping and breakdown-with-comparison in `metric_query`, the multi-office synthetic overlay, the `prompts/` tree with one prompt (§5–§8.1). |
| **B — metric_query extensions** | `phase-17-metric-query-extensions` | Multi-column and time-bucket group-by, ordering, share-of-total with the C04 order, the scope hinter, the "slice it another way" clarification, the remaining router-decision cases (§8.2). |
| **C — providers** | `phase-17-providers` | Direct Anthropic provider, Cortex provider built offline, one parity contract for all providers, the Coco handoff document (§9). |

Each PR is reviewable and mergeable on its own; the owner may stop after any of them.

### Out of scope (and where it goes)

- Charts (§4.9 of the reports design) — Phase 18 (D66).
- Report definitions, runs queued by a worker, sends, the mailer, the Reports screen, the split
  view, the tag row — Phase 18.
- The UI design pass for the Next.js interface — separate, owner-driven, not started here.
- Moving the brief PDFs from MinIO into Postgres — carry-forward (§13).
- Day-matched partial months in saved or emailed reports — never (D50).
- Live Cortex proof — done by the Snowflake-side owner from the handoff (§9.3).

---

## 2. Decisions taken in this design

| # | Decision | Revises |
|---|---|---|
| **D61** | Phase 17 ships as four PRs: C03 fix, report in chat, metric_query extensions, providers. All eleven router cases of the reports design §7.6 are in scope across PRs A and B. | migration design §10 row 17 (adds the split) |
| **D62** | `ConversationSlots` carries full period windows through two additive fields, `period_a_end` and `period_b_end`. The comparison (`period_b`) is **carried until replaced**: a single named period replaces A and clears B; a named comparison replaces both; a comparison-only phrase replaces B and keeps A. **Orientation:** slot A is the period being asked about, slot B is what it is compared to; a report seeds A = report month, B = prior month. | period parser's "B is never carried" contract; pipeline's "B clears every turn" rule |
| **D63** | Report **perspective** is an explicit enum `customer \| supplier \| book`. It alone decides the office column (`CUSTOMER_TEAM_NAME` / `PRIMARY_SUPPLY_TEAM_OFFICE` / none) and the primary entity (`CUST_NM` / `SUPPLIER_NM` / `CUST_NM`). Supplier figures are our certified sales GP on deals that supplier supplied. The customer breakdown of a supplier report is a chat follow-up over pass-through, not a table in the report. | completes the deferred supplier-perspective requirement |
| **D64** | **Month to date** exists only when asked for in words ("month to date", "mtd", "so far this month") or by naming the running month. Chips never offer it. It is chat-only; the payload records `window_kind: complete \| mtd` so Phase 18 refuses to save or email the latter. | refines D50 for ad-hoc chat |
| **D65** | `report_run` lands now as Drizzle migration `0003`, shaped for Phase 18 (nullable definition and superseded-by columns). It is a shared org document (no row policy, app-role grants). Report bytes are served by **Next.js route handlers** reading Postgres behind the Sales-role check, linked from a plain anchor. | resolves C07 and the downgraded C08 for this phase |
| **D66** | Charts are deferred to Phase 18. The payload has no `charts` key in `payload_version: 1`; adding it is additive. | reports design §4.9 timing |
| **D67** | The grounding check is **entity-scoped**: each numeric token in a sentence must match a display string belonging to an entity named in that sentence (or a KPI / period-level figure when the sentence names none). The suite carries a must-reject fixture. | resolves C09 |
| **D68** | Three live provider profiles: `bedrock` (unchanged), `anthropic` (new, Messages API over the existing HTTP client, no new SDK), `cortex` (REST Messages endpoint with native tool calling, built offline against recorded fixtures). One provider-parity contract test runs against all three plus the stub. Cortex's live proof is a handoff to the Snowflake-side owner. | D33's "Cortex not before SPCS"; D40's timing (was Phase 19) |
| **D69** | An ad-hoc in-chat run, including its PDF, is readable by **any `Poseidon:Sales` user**. | — (follows D37, D44, D47) |

---

## 3. Constraints carried from the repo rules

- No model-authored SQL. Every query is a `MetricQuerySpec`, `BreakdownQuerySpec` or the new
  `FrameQuerySpec`, built by the certified query builder.
- Deterministic math has one home: `backend/poseidon/core/analytics/mom.py`. Nothing is recomputed
  in TypeScript for display; the payload carries display strings.
- The SSE envelope and the existing part kinds are frozen. `report` is additive. The
  `contracts/turns` corpus gains a recorded report turn, regenerated with
  `POSEIDON_UPDATE_CONTRACTS=1` in the same commit and its diff reviewed.
- Drizzle owns the schema (D58). Python reads and writes `report_run` with plain SQL inside
  `rls_transaction`; it migrates nothing.
- Identity crosses the internal contract explicitly (D59). Row scope (D16) stays enforced on every
  query the report runs, including the whole-book context frames.
- Folder law: `backend/poseidon/tasks/reporting/skills/<skill>/` with tools, subskills and tests
  co-located. Prompts are the D46 exception (§6.6).
- Any change to chat output text runs the pg suite, not only the offline suite.

---

## 4. PR 0 — the C03 slot and carry contract

### 4.1 Slots

`ConversationSlots` gains, additively:

```
period_a_end: date | None = None     # exclusive end of the A window
period_b_end: date | None = None     # exclusive end of the B window
```

`period_a` / `period_b` keep their meaning (first day of the window). A window is
`PeriodWindow(start=period_x, end=period_x_end)`. Granularity is not stored; it is derivable from
the window and only needed for labels.

`history.slots_to_json` writes the two keys as ISO dates or `null`. `slots_from_json` reads them
and, when a key is **absent** (rows written before this PR), reconstructs the month window from the
start date — exactly the pre-PR behaviour, so no data migration and no behaviour change for old
conversations until their next turn rewrites the row.

### 4.2 Parser

`parse_periods` carries **the stored window**: when the text names no left-hand period and
`slots.period_a` is set, `period_a = PeriodWindow(slots.period_a, slots.period_a_end)` with
`a_source = "carry"`. The month-reconstruction path and the "known v1 limitation" docstring go.

`period_b` gains `b_source = "carry"`: when the text names no comparison and `slots.period_b` is
set, B is carried the same way. The comparison-only phrase ("compare to June instead", "vs June")
already splits with an empty left side; it keeps carrying A and sets B from the text.

### 4.3 Pipeline write rule

| Text names | A written | B written |
|---|---|---|
| nothing | `UNSET` (carry) | `UNSET` (carry) |
| a single period | the window | `None` (clear) |
| a comparison (both sides) | the window | the window |
| a comparison side only | `UNSET` (carry) | the window |

`period_a_end` / `period_b_end` are written whenever the matching start is written. "Clear" is the
only way B leaves the slots in this phase; the tag row that clears by click is Phase 18.

### 4.4 Orientation

In the slots, A is the period the user is asking about and B is the comparison. A report seeds
A = report month and B = the month before it. The `metric_grid` part and every table label render
month names (and the day range for month to date), never "A" or "B". The report payload uses the
keys `current` and `prior` (§5.2); the ported math keeps mom-comparison's `a`/`b` parameter names
with a docstring mapping `a = prior`, `b = current`. The mapping between slots and payload exists in
exactly one place, `resolve_periods` (§5.2).

### 4.5 State block

`render_state_block` renders windows with a label from one labeller
(`"July 2026"`, `"Q3 2026"`, `"2025"`, `"2026 year to date"`, `"October 1 to 11, 2026"`) and the
ISO bounds. Because this changes prompt text, PR 0 runs the pg suite.

### 4.6 Acceptance

A test module runs the real parser → `slots_to_json` → Postgres write → reload → next-turn parse,
for:

1. "GP for Maersk for 2025 by month" then "then by port" — the carried window is the calendar year.
2. "GP for Maersk in July 2026 vs June 2026" then "why did GP drop?" — A July, B June, both carried.
3. The same, then "compare to May instead" — A July, B May.
4. The same, then "GP for August 2026" — A August, B `None`.
5. A quarter carried across a reload keeps its quarter bounds.
6. A row without the new keys reloads as the month window.

Offline suite, ruff and the pg suite green; contracts corpus regenerated only if its diff is
non-empty, and then reviewed.

---

## 5. PR A — the report contract

### 5.1 Arguments

```
Args(perspective: Literal["customer", "supplier", "book"],
     office_value: str | None,        # required unless perspective == "book"; validated against
                                      # list_dimension_values(entity, office_column)
     month: date,                     # first day of the report month
     window_kind: Literal["complete", "mtd"] = "complete",
     basis: Literal["prior_month"] = "prior_month")
```

| perspective | office column | primary entity column | entity label |
|---|---|---|---|
| `customer` | `CUSTOMER_TEAM_NAME` | `CUST_NM` | Customer |
| `supplier` | `PRIMARY_SUPPLY_TEAM_OFFICE` | `SUPPLIER_NM` | Supplier |
| `book` | none | `CUST_NM` | Customer |

The office filter and the primary entity are chosen from this table in code. The model never sets
them.

### 5.2 Periods

`resolve_periods(basis, month, window_kind, reference_date) -> (current: PeriodWindow,
prior: PeriodWindow, label_current, label_prior)` is the only function that knows what a basis
means.

- `complete`: current = `[month, month+1)`, prior = the calendar month before. `month` must be a
  complete month present in the data; otherwise a 422 the orchestrator turns into "that month is
  not complete yet" or "no data for that month".
- `mtd`: let `n = reference_date.day − 1`. If `n == 0`, refuse ("no complete days in this month
  yet"). current = `[first of running month, first + n days)`; prior = `[first of prior month,
  first of prior month + n days)`, clamped to the prior month's length. Labels carry the day range
  and the words "month to date".

The chat flow (§6) seeds slots with A = current and B = prior.

`DataClient` gains `list_months(entity, filters) -> list[date]`: the first days of the months that
have rows. The month chips, the "complete month present" check and D50 use it; `available_periods`
(a MIN/MAX range) is not enough.

### 5.3 Queries

Six queries, one snapshot (`REPEATABLE READ` on Postgres; the Snowflake handoff must confirm or the
run retries once on drift, D54), all with D16 row scope applied:

1. and 2. **Slice frames**, one per window:
   `FrameQuerySpec(entity=MARINE_SALES_PLANNING_V, metrics=(GP, VOLUME, NUM_WON, NUM_INQUIRIES,
   NUM_LOST), period=window, group_by=(CUST_NM, SUPPLIER_NM, LOC_NM, CUSTOMER_TEAM_NAME,
   PRIMARY_SUPPLY_TEAM_OFFICE, DEAL_CLASSIFICATION_TRADE_CUT), filters={office_column:
   (office_value,)})` — no filter for `book`.
3. and 4. **Independent totals**, one `MetricQuerySpec` per window with the same filter. The frame
   roll-up must equal each total to the cent, or the run fails ("reconciliation failed") and no row
   is written with status `ok`.
5. and 6. **Context frames**, one per window, whole book, `filters={entity_column: (the ten
   movers,)}`, same group-by. Skipped for `book`.

`FrameQuerySpec` (reports design §7.4) joins `core/data/specs.py`; `build_frame_query` validates
every column with the existing dimension check, renders `COALESCE` per column and `GROUP BY 1..n`,
applies the where clause and row-scope predicate unchanged, emits no `ORDER BY` or `LIMIT`, and
refuses `WIN_RATE` and volume mode. Both dialects get byte-pinned SQL snapshots.

### 5.4 Math — `core/analytics/mom.py`

Ported from `mom-comparison/app/agent.py` (`_compute_kpis`, `_compute_top_movers`,
`_compute_new_lost`) with its expectations transcribed as tests, parameterised by the entity
column, operating on plain rows (no pandas):

- `compute_kpis(prior, current)` → VOLUME, GP, MARGIN, NUM_WON, NUM_INQUIRIES, NUM_LOST, WIN_RATE,
  each `{prior, current, change, pct_change}`; MARGIN = GP / VOLUME and WIN_RATE = NUM_WON /
  NUM_INQUIRIES recomputed from sums, never averaged.
- `merge_breakdowns(rows_prior, rows_current, key)` → outer join, zero-filled; pct against
  `abs(prior)`; **zero-base rule:** prior 0 → pct `null` rendered "new", absent-from-prior →
  `new`, absent-from-current → `lost`.
- `top_movers(merged, n=5)` → increases and decreases by absolute GP change with the driver rule
  verbatim: volume effect = Δvolume × old margin; margin effect = Δmargin × new volume; share of
  volume effect > 0.65 `volume-driven`, < 0.35 `margin-driven`, else `both`; both zero
  `unchanged`; new groups `new`.
- `new_lost(merged)`, `ranked(merged, by, n=10)` with the `Other (N <entities>)` row (D52) and the
  footer, `share(numerator, denominator)`.
- `format_display(...)`: money and tons whole, percentages one decimal, margin two decimals,
  "flat" for zero change, `en-US` separators, units `USD`, `t`, `USD/t`. Formatting happens once,
  here; every surface and the grounding check use these strings.

A property test asserts, for both perspectives and the whole book, that every ranked table's
footer equals the matching KPI value exactly.

### 5.5 Content

- **KPIs:** seven tiles (the six metrics plus win rate on the Lost tile's footer).
- **Tables:** `entities_by_gp`, `entities_by_volume` (10 rows + Other + footer), `ports_by_gp`,
  `ports_by_volume` (same), `deal_class` (all rows + footer). Every row shows prior, current, change
  and pct for GP, VOLUME and MARGIN.
- **Movers:** five increases, five decreases by GP on the primary entity, with drivers.
- **New and lost:** on the primary entity within the slice.
- **Entity context** (customer or supplier, from the context frames; absent for `book`): per mover
  `book` (whole-book GP/VOLUME/MARGIN both months with change and pct), `share_of_book` (office
  share of the entity's GP, both months), `reconciliation` (`office_change`, `other_offices_net =
  book_change − office_change`, `book_change`, always displayed), `offsets` (other values of the
  office column with opposite-sign GP change, up to five), `ports_rest_of_book`.
- **Suggested questions:** three, naming only entities present in the payload; a chip that fails
  that check is dropped, not rewritten.

For the supplier perspective the business meaning of each section is the customer one with the
entity swapped: a "new supplier" is one with GP in this office in the current month and none in the
prior; supplier context is the supplier's whole-book GP and this office's share of it. No
reliability, spend or payment claims are made or implied.

### 5.6 Narrative and grounding

1. **Template narrative**, always, zero model calls: mom-comparison's `mom-patterns.md` templates
   (Overall Summary, Key Drivers, New/Lost) filled from display strings, with the entity noun
   parameterised.
2. **Model narrative**: the `synthesis` role receives the computed block only (KPIs, movers with
   drivers, new/lost capped at 10, entity context with reconciliation lines) and returns a **forced
   tool call** `{narrative_md, insights: [3-5], context_notes: [], suggested_questions: [3]}`.
   The prompt is `prompts/reporting/narrate.xml` (§6.6) and says the frames are marginals: state
   the facts, never chain them into a causal story.
3. **Grounding check** (D67): tokenize the prose into numeric tokens (digits with separators,
   decimals, percent signs, leading sign or "minus"). For each sentence, collect the entities named
   in it (exact or casefolded match against payload entity names, ports, offices, deal classes).
   Each token must match a display string of one of those entities, or, when the sentence names no
   entity, a KPI or period-level display string. Years, month names, day numbers in the period
   label and ranks 1–10 are exempt. One violation → one retry listing the violating sentences; a
   second failure → the template narrative is used, `narrative.grounded = false`, flags list the
   count, and the renderer shows "AI narrative withheld: N unverified figures".
   Fixtures: the mockup narrative must pass; a fixture that attributes customer B's figure to
   customer A must fail.

### 5.7 Payload (`payload_version: 1`)

```
{ payload_version: 1, run_id,
  perspective, entity: {column, label},
  scope: {office_column|null, office_value|null, label},
  periods: {current: {start, end, label}, prior: {start, end, label}, window_kind},
  kpis: {<METRIC>: {prior, current, change, pct_change, display: {prior, current, change, pct}}},
  tables: {entities_by_gp, entities_by_volume, ports_by_gp, ports_by_volume, deal_class:
           {rows: [{key, prior: {...}, current: {...}, change: {...}, pct: {...}, display: {...}}],
            other: {...}|null, footer: {...}}},
  movers: {increases: [...], decreases: [...]},
  new_lost: {new: [...], lost: [...]},
  entity_context: {<entity>: {book, share_of_book, reconciliation, offsets, ports_rest_of_book}} | null,
  narrative: {template_md, llm_md|null, grounded, flags: [str]},
  suggested_questions: [str, str, str],
  proof: [str], queries: [{label, sql_hash, rows, ms}],
  generated_at, source, generated_by }
```

The same payload feeds the `report` part, the HTML, the PDF and `report_lookup`. No `charts` key.

---

## 6. PR A — the flow, the part and the lookup

### 6.1 Entry

A third opener chip, **Monthly report**, with the pinned phrase `start a monthly report`, in the
Next.js opener. The orchestrator's entry branch matches it like the two brief phrases and sets the
conversation mode to `report`. A second click starts over (perspective, office and report
reference cleared), mirroring the brief rule.

### 6.2 Subject turns (deterministic, no router)

Which step comes next is read from the slots under mode `report`:

| Slots filled | Step | Reply |
|---|---|---|
| none | perspective | text + chips `Customer office` / `Supplier office` / `Whole book` (pinned phrases `customer office report`, `supplier office report`, `whole book report`) |
| perspective, not `book`, no office | office | text + chips of `list_dimension_values(entity, office_column)`; `send_text` = the certified value |
| perspective (+ office) | month | text + chips for the last three months from `list_months` (latest marked "default"); typed text goes through the period parser, which is where month to date gets in |

A reply that resolves nothing re-asks the same step with a short clarification; typed office names
match the certified values case-insensitively and otherwise re-ask with the chips.

### 6.3 The report turn

The month step dispatches `reporting.monthly_performance` synchronously inside the turn. Tool
steps stream as today (`tool_event` frames): frames, totals, reconciliation, context, narrative,
grounding, render, store. The turn then emits, in order: the `report` part, an `artifact` part
(`name` = `<office or Whole book> <Month YYYY>.pdf`, `url` = the relative download route,
`mime` = `application/pdf`), the `proof` part, and a `chips` part with the three suggested
questions (`send_text` = the question verbatim). The slots are written in the same turn:
`period_a`/`period_a_end` = current, `period_b`/`period_b_end` = prior, `perspective`,
`office_column`, `office_value`, `report_ref` = run id, `pass_through` = the top ten primary
entities labelled with their column. Mode returns to `default` so the router sees the full
registry on the next turn (D19).

`ConversationSlots` gains, additively: `perspective`, `office_column`, `office_value`,
`report_ref`, all `str | None`, serialized by `slots_to_json`/`slots_from_json`.

### 6.4 The `report` part

Additive part kind `report` whose payload is §5.7. Renderer: `web/src/ui/report/` —
`ReportPart` (header: scope label, period labels, generated time, source, window kind),
`KpiTiles`, `RankedTables` (five sub-tabs), `MoverCards` (with the reconciliation line),
`NewLostTables`, `Narrative` (template or model, with the withheld flag), `SuggestedQuestions`
(reuses the chips callback), `ProofBlock` (collapsed). Registered in `registry.tsx`. Current
Tailwind styling, real table semantics, no second live region. The Vite app shows its fallback for
this kind and is not extended.

### 6.5 `reporting.report_lookup` (router-visible)

```
Args(section: Literal["kpis", "entities_by_gp", "entities_by_volume", "ports_by_gp",
                      "ports_by_volume", "deal_class", "movers", "new_lost", "narrative",
                      "context"],
     limit: int = <fits the 4,000-character tool-result cap>, offset: int = 0)
```

Reads `slots.report_ref` → `report_run.payload`. Returns a `metric_grid` for KPIs, `table` parts
for tables, movers, new/lost and context, `text` for the narrative, with a trailing "N rows
remaining" line when paged (D53). No queries. Without `report_ref` the orchestrator answers plainly
that a report has to be opened first.

### 6.6 Prompts (D46, first step)

A top-level `prompts/` tree is created with `prompts/reporting/narrate.xml` and a minimal
`PromptRegistry` that resolves an id to a versioned prompt. Existing skill-owned prompts migrate
later (§13).

### 6.7 State block additions

When set:

```
Carried perspective: supplier (ranks SUPPLIER_NM; office column PRIMARY_SUPPLY_TEAM_OFFICE)
Carried office: PRIMARY_SUPPLY_TEAM_OFFICE = MED SUPPLY OFFICE. data_qa.metric_query applies it
  when scope="office" (the default). Use scope="book" for the whole book, the full account, other
  locations or offices, or a share of the total.
Report context: Med Supply Office, September 2026 vs August 2026. Quote the report's own figures
  only through reporting.report_lookup.
```

### 6.8 `metric_query` changes in PR A

- `scope: Literal["office", "book"] = "office"`. With a carried office and `scope == "office"`,
  `skill.run` stamps `{office_column: (office_value,)}` onto every spec it builds, in the same
  place and with the same discipline as D16 row scope. The model cannot forget the office filter.
- `group_by` + `compare_period` becomes legal: both windows run, `merge_breakdowns` joins them, and
  the result is a `table` part `[dimension, <metric> prior, <metric> current, Change, %]` per
  requested metric plus `Driver` when GP and VOLUME are both requested, ordered by absolute change
  of the first metric, capped at `top_n`. Pass-through captures the first column.

Router-decision cases pinned in PR A (stub mode): "why did GP drop for X" (office, `CUST_NM` or
`SUPPLIER_NM` by perspective, `group_by [LOC_NM]`, `compare_period`), "where else does X lift
fuel" (book), "what were the top ports in the report" (`report_lookup(section="ports_by_gp")`),
"break that down by customer" on a supplier report (`SUPPLIER_NM IN pass-through`,
`group_by [CUST_NM]`).

### 6.9 Synthetic data

The generator gains a deterministic **multi-office overlay**: a configurable share of customers
(always including the named fixtures) has rows in two or three values of each office column, so
`share_of_book < 1`, offsets are non-empty and the context section is exercised by the pg goldens.
The reseed changes existing pg goldens pinned to row values; they are re-pinned in the same commit
with the diff reviewed, and the plan names them up front.

---

## 7. PR A — storage, download and the migration

### 7.1 `report_run` (Drizzle `0003_report_run`)

```
report_run
  id uuid pk
  definition_id uuid null                  -- Phase 18 FK
  perspective text not null check in ('customer','supplier','book')
  office_column text null check in ('CUSTOMER_TEAM_NAME','PRIMARY_SUPPLY_TEAM_OFFICE')
  office_value text null
  period_current_start date not null, period_current_end date not null
  period_prior_start date not null,   period_prior_end date not null
  window_kind text not null check in ('complete','mtd')
  basis text not null default 'prior_month'
  status text not null check in ('ok','error')      -- Phase 18 adds pending/running
  payload_version int not null
  payload jsonb not null
  html bytea null, pdf bytea null
  narrative_grounded boolean not null
  source text not null                      -- 'synthetic' | 'snowflake'
  created_by text not null                  -- sub
  conversation_id uuid null
  superseded_by uuid null                   -- Phase 18
  generated_at timestamptz not null, created_at timestamptz not null default now()
```

No `ENABLE ROW LEVEL SECURITY`; `GRANT SELECT, INSERT ON report_run TO poseidon_app` (D37, D69).
The same migration widens `ck_conversations_mode` to include `'report'`. `web/src/db/schema.ts`
and `migrations.test.ts` change with it.

Python: `backend/poseidon/reports/store.py` with `insert_run`, `get_run`, `get_bytes(run_id,
kind)` as plain SQL under `rls_transaction` (`SET LOCAL ROLE poseidon_app`), used by the skill and
`report_lookup`.

### 7.2 Download routes

`web/src/app/api/reports/[runId]/pdf/route.ts` and `.../html/route.ts`: proxy enforces
`Poseidon:Sales`, the handler repeats the check (D59), reads the bytes through Drizzle, returns
`Content-Type` and `Content-Disposition: attachment; filename="..."`. 404 for an unknown run or a
run with no bytes. The artifact card is a plain anchor to the relative URL; under SPCS ingress and
under the fixed dev identity the credential travels with a top-level navigation, which is why C08
is downgraded (migration design §9). If Auth0 is ever wired, C08 returns in full.

### 7.3 Rendering

`backend/poseidon/config/templates/report.html.j2` with autoescape on renders display strings
only. `render_pdf` imports WeasyPrint lazily with `url_fetcher` disabled, so a hostile entity name
cannot run as markup or make the renderer reach the network. HTML and PDF are stored on the run
row; HTML serves Phase 18's panel and email.

### 7.4 Left alone, deliberately

Brief PDFs stay on MinIO (carry-forward). Run retention is unbounded until folded into D29.

---

## 8. `metric_query` extensions

### 8.1 In PR A

Scope stamping and breakdown-with-comparison (§6.8).

### 8.2 In PR B

- **Multi-column group-by:** `group_by: list[str]` of one to three certified dimensions plus the
  pseudo-dimensions `MONTH`, `QUARTER`, `YEAR` (`DATE_TRUNC` on the entity's date column, ordered
  chronologically when present). A time-bucketed result renders as a `table` with the bucket first.
- **Ordering:** `order_by: str | None` (default `metrics[0]`), `order: "desc" | "asc"` (default
  `desc`).
- **Share of total** with the C04 order: build the effective filters including the carried office;
  validate that every column in `share_of_total` is present in them; derive the denominator
  filters by removing those columns; **do not restamp**; D16 row scope stays on both queries.
  Returns `[Metric, Selection, Total, Share %]` with both queries in the proof. Cannot combine with
  `group_by` or `compare_period` (422). Acceptance: office GP 20 of book GP 100 → 20%, office
  predicate on the numerator only.
- **Scope hinter:** a parsing stage after `skill_hinter`, lexicon-driven, setting
  `ParsedTurn.scope_hint` to `book` ("where else", "other ports/offices/locations", "full
  account", "whole book", "overall", "across all offices") or `share` ("how much of", "share
  of", "% of", "portion of", "makes up"). Advisory; the state block shows `Scope hint: book`.
- **"Slice it another way":** a deterministic clarification with chips listing the certified
  dimensions, produced by the parser's ambiguity path.
- The remaining router-decision cases of the reports design §7.6, pinned in stub mode; the
  widening and share cases also under `router_live`.

---

## 9. PR C — providers

### 9.1 Direct Anthropic

`core/llm/anthropic.py`: `AnthropicProvider` over the Messages API using the HTTP client already
in the environment (no new SDK dependency), `ANTHROPIC_API_KEY` from the environment, model ids
from a new `anthropic` profile in `config/models.yml`, tool-use translation to and from
`LLMResponse` mirroring Bedrock's, forced tool call supported. `Settings.llm_profile` becomes
`Literal["bedrock", "cortex", "anthropic"]`. Proven live with the owner's key.

### 9.2 Cortex

`core/llm/cortex.py`: `CortexProvider` over the Cortex REST inference endpoint with native tool
calling (D40), `SNOWFLAKE_ACCOUNT_URL` and a bearer token from the environment. Request and
response shapes are taken from Snowflake's current documentation at implementation time and pinned
as recorded fixtures; the role client's "not available until SPCS" refusal becomes an ordinary
missing-credentials error. Built and tested offline only.

### 9.3 Parity contract and the Coco handoff

`backend/tests/test_provider_parity.py`: one parametrised suite that every provider (stub,
bedrock, anthropic, cortex) must pass against recorded fixtures — plain completion, tool call,
forced tool call, malformed-tool-output handling, token accounting fields.

`docs/snowflake/CORTEX_HANDOFF.md`, written for the Snowflake-side owner: the environment variables
to set, how to mint the token, the exact commands to run (`LLM_PROFILE=cortex` with the
`router_live` marker and one report generation), what output means pass for each, what to capture
on failure, and where to record the result (a `BUILD_SIGNOFF`-style checklist in the same folder).
The live Cortex proof is complete when that checklist is returned signed.

---

## 10. Failure design (this phase)

| Failure | Behaviour |
|---|---|
| Frame roll-up ≠ independent total | turn returns an `error` part "reconciliation failed"; no `ok` run row |
| Data backend error | `error` part with the problem detail; no run row |
| Office slice has no rows in either month | `ok` run with "no data" sections, no narrative call, no suggested questions |
| Month not complete / not present | re-ask the month step with the reason; month to date offered in words |
| Model narrative error or malformed tool output | template narrative, `grounded=false`, flag shown; run `ok` |
| Grounding fails twice | same, with the unverified count |
| `report_lookup` without a report | plain "open a report first" reply |
| Download of unknown run or missing bytes | 404 |
| Caller lacks `Poseidon:Sales` | 403 at the proxy, 403 in the handler |

---

## 11. Testing and gates

**Implementers run the suites and report; reviewers judge the diff and never re-run.** Full marker
exclusion and `env -u PERPLEXITY_API_KEY` for the offline run; pg suite for any chat text change;
pytest never piped.

| PR | Automated | Owner gate |
|---|---|---|
| 0 | §4.6 cases; offline, ruff, pg | "GP for X for 2025 by month, then by port" keeps the year; "July vs June, why did GP drop" keeps the comparison; "compare to May instead" swaps it |
| A | math expectations transcribed from mom-comparison; footer = KPI property for both perspectives and book; frame SQL snapshots both dialects; grounding pass and must-reject fixtures; HTML/PDF snapshots, Outlook-safety lint, escaping test (`<img src=x>` entity, WeasyPrint fetches nothing); store round-trip (pg); route handler tests incl. 403/404; renderer vitest; contracts corpus report turn; the four router cases of §6.8 in stub mode; multi-office overlay goldens (pg) | In the browser on synthetic data: Monthly report → Supplier office → an office → month; the report renders with supplier rankings; every footer equals its tile; the three follow-ups answer correctly **live on Bedrock**; a customer office produces customer rankings; `docker compose … restart`, then the PDF downloads |
| B | all eleven §7.6 cases in stub mode; widening and share under `router_live`; share acceptance (20%); time-bucket SQL snapshots; hinter lexicon tests | "GP for X for 2025 by month, then by port" and "how much of my GP is of the total account GP" work in the browser |
| C | parity suite for four providers on recorded fixtures; settings validation | one report narrative generated with `LLM_PROFILE=anthropic`; `CORTEX_HANDOFF.md` delivered and readable without this repo's context |

Verified figures and commands in `CLAUDE.md` are refreshed in each PR's last commit.

---

## 12. Docs and decision-log updates (per PR)

- `00-overview.md`: D61–D69; D33/D40 marked revised by D68; the "still not folded into any design"
  paragraph removed (D63 folds it).
- `02-backend-skills.md`: the `reporting` task, the `internal` registry flag, `core/analytics`,
  the third flow, the slot additions.
- `03-llm-routing.md`: forced-tool-call narrative, the `anthropic` and `cortex` providers, state
  block additions, the `prompts/` tree.
- `04-data-ontology.md`: `FrameQuerySpec`, time buckets, `list_months`.
- `05-auth-identity.md`: D69, `report_run` as a shared org document.
- `01-frontend.md`: the `report` part, the download routes.
- `07-infrastructure.md`: report bytes in Postgres in every habitat; MinIO's remaining role.
- `08-build-phases.md`: this phase.
- `docs/snowflake/CORTEX_HANDOFF.md` (PR C).

---

## 13. Carry-forwards created here

- Move brief PDFs from MinIO into Postgres under D39; retire `ArtifactStore.put_pdf`.
- Migrate the remaining skill-owned prompts into `prompts/` through `PromptRegistry` (D46).
- Run retention for `report_run` under D29.
- The tag row that clears carried slots by click (Phase 18).
- Charts (Phase 18, D66).
- Phase 16's known gaps stay unscheduled: opener-chip spans, lowercase customers after "for",
  unknown-place reply losing the original question.

---

## 14. References

- `docs/superpowers/specs/2026-09-04-monthly-performance-reports-design.md` (content contract,
  D34–D54)
- `docs/superpowers/specs/2026-09-05-monthly-performance-reports-codex-review.md` (C03, C04, C07,
  C08, C09, the supplier-perspective owner decision)
- `docs/superpowers/specs/2026-09-07-codex-findings-triage.md`
- `docs/superpowers/specs/2026-09-07-nextjs-migration-design.md` (§7 internal contract, §9 Q1–Q5,
  §10 phase table)
- `docs/reference/mockups/2026-09-04-monthly-report-mockup.html`
- `C:\Users\carlo\github\mom-comparison\app\agent.py`, `skills/analysis/mom-patterns.md`
- `backend/poseidon/core/parsing/period_parser.py`, `parsing/pipeline.py`, `chat/history.py`,
  `skills/context.py`, `llm/roles.py`, `config/models.yml`
