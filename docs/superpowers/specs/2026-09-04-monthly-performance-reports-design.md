# Monthly Performance Reports and Report-Grounded Chat — Design

Status: Draft for owner review · Date: 2026-09-04 · Owner decisions recorded inline
Visual target: `docs/reference/mockups/2026-09-04-monthly-report-mockup.html` (three tabs: report view,
email, admin dialogs; synthetic figures that reconcile across every table)

## 1. What this adds

Poseidon gains a second deliverable next to chat: an **office-level monthly performance report**
that a report admin generates on demand, previews inside Poseidon, and emails to a recipient list.
Every report opens in a **split view** beside a conversation that is grounded in that report, so the
team can ask follow-up questions and drill into the numbers with the same certified skills the chat
already uses.

The report reproduces what the mom-comparison app does today (KPIs with deltas, top movers with a
volume-versus-margin driver, new and lost accounts, a narrative written over pre-computed numbers)
and adds two things: the five ranked tables the owner asked for, and **account context** — for each
top mover, where that customer's business went across the whole book.

Platform work rides along because the audience runs on Snowflake: the Cortex LLM provider is built
now, a Snowflake data client arrives, the SPCS app database becomes Snowflake Postgres, and a
Snowflake-side build handoff is written in the Triton stage-gate format.

### Non-goals (v1)

- No scheduled generation. Reports are produced on demand only (owner decision).
- No per-person scoping. A report is one office slice sent to a list.
- No comparison basis other than previous month (the basis is an extension point, §4.1).
- No chat-triggered generation. The report skill is not router-visible in v1 (§5.1).
- No self-healing of narratives. A failed grounding check falls back to the template narrative.

## 2. Owner decisions (2026-09-04)

| # | Decision |
|---|----------|
| D34 | A report is deterministic skill output: Python queries, computes and renders every figure; the synthesis model only writes prose over the computed JSON, and a grounding check verifies every number it writes. |
| D35 | Report scope is an **office slice**: `CUSTOMER_TEAM_NAME` (Customer Broker Office) or `PRIMARY_SUPPLY_TEAM_OFFICE` (Supply Team Office). A report definition = office column + office value + recipient list. Generation is on demand; the flow is generate → preview → send. |
| D36 | Comparison basis is an enum with one implemented value, `prior_month`. Same-month-prior-year and YTD are declared values that fail validation until built. |
| D37 | Report definitions, runs and sends are **shared org documents**: granted to the app role, no per-user row policies. Writes are gated by a new role `Poseidon:ReportAdmin`; reads by `Poseidon:Sales`. |
| D38 | Email goes through one `Mailer` interface with SMTP, Microsoft Graph and stub implementations. The mail relay is a new egress processor: report content to internal recipients, never conversation content. |
| D39 | On SPCS the app database is **Snowflake Postgres** (revises D20 for Postgres; MinIO stays as an in-service container for artifacts). |
| D40 | `CortexProvider` is built now with its parity test (revises D33's timing, not its interface). |
| D41 | Report chat carries the office filter and the month pair by default; widening is per turn; share-of-total is computed in Python; the report's own figures reach the model only through the `reporting.report_lookup` tool result. |
| D42 | The EC2 deployment (Phase 14) is paused after its completed offline half; this work sequences first. |
| D43 | The report content and math follow mom-comparison exactly where it already works (two single-month aggregations merged in Python, its KPI, top-mover, driver and new/lost rules, its narrative templates). |

## 3. Report definition

```
report_definition
  id uuid pk (uuid7)
  name text not null unique
  slice_column text not null check (slice_column in ('CUSTOMER_TEAM_NAME','PRIMARY_SUPPLY_TEAM_OFFICE'))
  slice_value text not null                -- a certified dimension value (validated on write)
  comparison_basis text not null default 'prior_month' check (comparison_basis in ('prior_month'))
  recipients jsonb not null default '[]'   -- array of email strings, validated on write
  enabled boolean not null default true
  created_by text not null, created_at timestamptz, updated_at timestamptz
```

`slice_value` is validated against `DataClient.list_dimension_values(entity, slice_column)` on
create and update; an unknown value is a 422. The office picker in the UI is a type-ahead over the
same call, so nothing that is not in the data can be chosen.

## 4. Report content contract

### 4.1 Periods

`resolve_periods(basis, period_b: date) -> (PeriodWindow a, PeriodWindow b)` is the only function
that knows what a basis means. `prior_month`: B = the report month, A = the calendar month before
it. Both are half-open `PeriodWindow`s. A future basis adds one branch here and nothing elsewhere.

### 4.2 Queries (mom-comparison's shape)

One aggregation **per month**, merged in Python; never a pivoted two-month query. For the office
slice the job runs three breakdown pairs through the existing certified query builder, each with the
filter `{slice_column: (slice_value,)}` and both windows:

| Breakdown | `group_by` | Metrics | Rows |
|-----------|-----------|---------|------|
| customers | `CUST_NM` | GP, VOLUME, MARGIN, NUM_WON, NUM_INQUIRIES, NUM_LOST | all (`top_n` = `REPORT_ROW_CAP`, a constant of 5000; a slice that hits it fails the run loudly) |
| ports | `LOC_NM` | same | all |
| deal class | `DEAL_CLASSIFICATION_TRADE_CUT` | same | all |

Report tools call `DataClient` directly with `BreakdownQuerySpec`s; they do not go through the
router-facing `Args` (whose `top_n` cap of 50 is for the model, not for batch jobs). The KPI totals
are computed from the full customer frames, so they never change with the grouping.

### 4.3 Math (ported from mom-comparison, one shared module)

A new package `backend/poseidon/core/analytics/` holds the month-over-month math used by both the
report tools and the chat skill (§7.4), ported from `mom-comparison/app/agent.py` and the
parameterized `wfs_core.analytics`, with the same expectations as tests:

- `compute_kpis(frame_a, frame_b)`: VOLUME, GP, MARGIN, NUM_WON, NUM_INQUIRIES, NUM_LOST, each
  `{a, b, change, pct_change}`; MARGIN recomputed as GP over VOLUME, never averaged; WIN_RATE =
  NUM_WON over NUM_INQUIRIES.
- `merge_breakdowns(rows_a, rows_b)`: outer join on the dimension key, zero-filled, with
  `{a, b, change, pct}` per metric (pct against `abs(a)`, 0.0 when a is 0).
- `top_movers(merged, n=5)`: top n increases and top n decreases by absolute GP change, each with GP,
  VOLUME and MARGIN deltas and a `driver`, using mom-comparison's rule verbatim: volume effect =
  Δvolume × old margin; margin effect = Δmargin × new volume; share of volume effect > 0.65 →
  `volume-driven`, < 0.35 → `margin-driven`, else `both`; both effects zero → `unchanged`.
- `new_lost(merged)`: keys present in B only / A only, with GP, VOLUME, MARGIN, GP-sorted.
- `ranked(merged, by, n=10)`: rows sorted by the B value of `by`.
- `share(numerator_values, denominator_values)`: per-metric share, used by §7.4.

Rounding for display (mom-comparison's rules): money and tons whole, percentages one decimal,
margin two decimals, "flat" when a change is zero.

### 4.4 Tables in the report

Five ranked tables, ranked by month B and showing A, B, change and percent for GP, VOLUME and
MARGIN on every row: customers by GP (10), customers by volume (10), ports by GP (10), ports by
volume (10), deal class (all rows, plus a total row). A total row also closes the port tables.

### 4.5 Top movers and account context

Five increases and five decreases by GP (§4.3). For **each mover**, the `account_context` tool
pulls that customer's whole-book numbers (no office filter): totals for both windows, and three
breakdown pairs — by `LOC_NM`, `CUSTOMER_TEAM_NAME`, `PRIMARY_SUPPLY_TEAM_OFFICE`. From those it
computes:

- `book`: the customer's whole-book GP/VOLUME/MARGIN for A and B with change and pct;
- `share_of_book`: the office's share of the customer's GP in A and in B;
- `offsets`: groups whose GP change has the opposite sign to the office-level change, sorted by
  magnitude, up to five per dimension, each `{dimension, value, change}`.

Budget: 10 movers × (1 total + 3 breakdowns) × 2 windows = 80 aggregate queries per report. Each
is recorded in the proof block. A mover with no offsets simply has an empty list.

### 4.6 New and lost customers

Within the office slice (§4.3 `new_lost` on the customer breakdown).

### 4.7 Narrative — two layers

1. **Template narrative** (always present, zero model calls): Python fills mom-comparison's
   `mom-patterns.md` templates — Overall Summary, Key Drivers (top increases/decreases with
   volume, margin, driver), New/Lost — from the computed JSON.
2. **Model narrative**: the `synthesis` role receives only the pre-computed block (KPIs JSON, top
   movers with drivers, new/lost capped at 10, account context) plus the analysis instructions
   ported from mom-comparison's analysis prompt and playbook. It returns JSON:
   `{narrative_md, insights: [3-5 strings], account_context_notes: [strings],
   suggested_questions: [3 strings]}`. `narrative_md` follows the same section order as the
   template narrative and adds an Account context section ("Meridian's GP fell 56,500 at
   Gibraltar but rose 31,200 at Algeciras under the Spain Office").
3. **Grounding check** (deterministic): every number in the model's prose and bullets, extracted
   with separators, decimals and percent signs normalized, must exist in the set of displayed
   figures derived from the payload (values at their display rounding, plus win rates and shares
   at one decimal). Years, month names and ranks 1–10 are exempt. One violation → one retry with
   the violating figures listed; a second failure → the template narrative is used,
   `narrative.grounded=false`, and the UI shows "AI narrative withheld: N unverified figures".
   This is the 2026-08-05 live-synthesis lesson applied by construction.

### 4.8 Suggested questions

The three `suggested_questions` are rendered as chips in the report panel and in the grounded
conversation's opener; each chip's `send_text` is the question verbatim. They must name only
entities present in the payload (checked like numbers; a bad chip is dropped, not rewritten).

### 4.9 Charts

Server-side SVG, rendered by Python from the payload so the panel and the PDF show the same image
and snapshot tests pin the output: grouped horizontal bars (A vs B) and a change waterfall for
customers and for ports, and grouped bars for deal class. Marks follow the dataviz rules already
used in the mockup (≤24 px thick, 2 px surface gap, direct labels on the B bar and on every
change bar, a legend for the two series). The panel shows all five figures; the PDF embeds the
customer and deal class figures only (three), to keep it to a few pages. Charts are not embedded
in the email.

### 4.10 Payload (versioned, `payload_version: 1`)

```
{ definition: {id, name, slice_column, slice_value, basis},
  periods: {a: {start,end}, b: {start,end}, label_a, label_b},
  kpis: {VOLUME|GP|MARGIN|NUM_WON|NUM_INQUIRIES|NUM_LOST|WIN_RATE: {a,b,change,pct_change}},
  tables: {customers_by_gp, customers_by_volume, ports_by_gp, ports_by_volume, deal_class:
           {rows: [{key, a:{GP,VOLUME,MARGIN}, b:{...}, change:{...}, pct:{...}}], total?}},
  movers: {increases: [...], decreases: [...]},           // §4.3 entries + account_context ref
  new_lost: {new: [...], lost: [...]},
  account_context: {<customer>: {book, share_of_book, offsets}},
  narrative: {template_md, llm_md|null, grounded, flags: [str]},
  suggested_questions: [str, str, str],
  charts: {customers_grouped, customers_change, ports_grouped, ports_change, deal_class_grouped},  // svg strings
  proof: [str], generated_at, source: "synthetic"|"snowflake", generated_by }
```

The same payload feeds the report panel, the email template and the PDF, so the three surfaces
cannot disagree.

## 5. Generation pipeline

### 5.1 Code layout

```
backend/poseidon/tasks/reporting/
  task.yml
  skills/
    monthly_performance/          # NOT router-visible in v1 (task.yml lists it as internal)
      skill.py                    # run(ctx, args) -> SkillResult (parts = none; result carries payload + artifacts)
      schema.py                   # Args(definition_id, period_b)
      prompts/narrate.md          # v1, embeds the mom-patterns templates + analysis instructions
      tools/
        resolve_periods.py        # §4.1
        fetch_slice.py            # the three breakdown pairs (§4.2)
        account_context.py        # §4.5
        assemble_payload.py       # math (§4.3) -> payload (§4.10)
        template_narrative.py     # §4.7 layer 1
        grounding_check.py        # §4.7 layer 3
        render_charts.py          # §4.9
        render_html.py            # report.html.j2 -> HTML string
        render_pdf.py             # HTML -> PDF bytes (WeasyPrint, lazy import as build_brief_pdf does)
      subskills/narrate/subskill.py   # §4.7 layer 2, via ctx.llm role "synthesis"
      tests/
    report_lookup/                # router-visible (§7.5)
backend/poseidon/core/analytics/mom.py          # §4.3, shared with data_qa
backend/poseidon/core/mail/{base,smtp,graph,stub}.py   # §5.6
backend/poseidon/reports/{store,jobs,service}.py       # definitions/runs/sends store, the job runner, send orchestration
backend/poseidon/api/reports.py                        # §5.5
backend/poseidon/scripts/worker.py                     # §5.2
backend/poseidon/config/templates/report.html.j2, email/report.html.j2, email/report.txt.j2
```

The registry gains an `internal: true` flag on a skill's `SKILL_META`: discovered, validated and
dispatchable in code, excluded from `TOOL_SCHEMAS`. Exposing the skill to the router later is a
one-line change.

### 5.2 Durable job

```
report_run
  id uuid pk (uuid7), definition_id uuid fk, period_a date, period_b date, basis text,
  status text check (status in ('pending','running','ok','error','superseded')),
  requested_by text not null, attempts int default 0,
  payload jsonb, html_key text, pdf_key text, turn_run_id uuid, error jsonb,
  created_at, started_at, finished_at
  index (definition_id, period_b, status)
```

`POST …/runs` inserts a `pending` row (409 if a pending/running row exists for the same definition
and month). The worker container claims it with the same pattern as memory distillation —
`SET LOCAL ROLE poseidon_worker`, `FOR UPDATE SKIP LOCKED`, `status='running'`, `attempts+1` — runs
the skill, stores the payload, uploads HTML and PDF to the artifact store under
`reports/{definition_slug}/{period_b}/{run_id}.{html,pdf}`, and marks the run `ok`. On success the
previous `ok` run for the same definition and month becomes `superseded` (its artifacts and sends
are kept). On exception the run is `error` with the problem detail; a run exceeding
`REPORT_MAX_ATTEMPTS` (default 2) stays `error`.

`poseidon.scripts.worker` replaces `memory_worker` as the container entrypoint and runs both
cycles in one loop (`memory_worker.run_once` unchanged, plus `reports.jobs.run_once`). Compose,
the EC2 compose and the SPCS spec point at the new module. Migration widens `turn_run.kind` with
`'report_run'`; each run writes one `turn_run` (kind `report_run`, `user_sub` = the requesting
admin, `question` = the definition name and month) plus the usual `llm_calls` and `tool_calls`
children.

### 5.3 Sends

```
report_send
  id uuid pk, run_id uuid fk, sent_by text, recipients jsonb, transport text,
  status text check (status in ('sent','failed')), provider_message_id text, error jsonb,
  attach_pdf boolean, created_at, sent_at
```

`POST …/send` renders the email from the payload, calls the mailer synchronously (timeout
`MAIL_TIMEOUT_SECONDS`, default 20), and records the outcome. A failed send is retryable. A run can
be sent any number of times; the panel shows the last send. Send is refused (409, with the reason)
for runs that are not `ok`, for superseded runs, and for runs whose slice returned no rows.

### 5.4 Privilege model

`report_definition`, `report_run` and `report_send` are shared org documents (D37): `GRANT SELECT,
INSERT, UPDATE` to `poseidon_app`, `GRANT SELECT, UPDATE ON report_run` to `poseidon_worker`, no row
policies. This is the first table family in the app that is deliberately not user-scoped; the
migration's docstring and doc 05 §4 say so. `conversations` gains a nullable `report_run_id` with a
unique index on `(user_sub, report_run_id)` where not null, so each user has at most one grounded
conversation per run; that table keeps its existing RLS.

Roles: `Poseidon:ReportAdmin` alongside `Poseidon:Sales`. Auth0's post-login Action adds it to the
roles claim for named users; SPCS mode reads `SPCS_REPORT_ADMINS` (Snowflake usernames, fail-closed
when empty, same shape as `SPCS_SALES_USERS`); disabled mode reads `DEV_REPORT_ADMINS` (compose sets
the dev user). `require_report_admin` is a FastAPI dependency layered on `require_sales`.
`GET /api/me` already returns roles; the UI shows admin controls only when the role is present.

### 5.5 API

All routes require `Poseidon:Sales`; rows marked admin also require `Poseidon:ReportAdmin`.

| Route | Auth | Notes |
|-------|------|-------|
| `GET /api/reports/definitions` | sales | enabled and disabled, with last run summary |
| `POST /api/reports/definitions` · `PUT …/{id}` | admin | no delete in v1: a definition is retired by `enabled=false`, which hides it from the sidebar and refuses new runs while its history stays reachable |
| `POST /api/reports/definitions/{id}/runs` `{period: "YYYY-MM"}` | admin | 202 `{run_id}`; 409 on an in-flight duplicate; 422 if the month is outside `available_periods` |
| `GET /api/reports/runs?definition_id=&status=&cursor=` | sales | cursor-paginated, newest first |
| `GET /api/reports/runs/{id}` | sales | run + payload + presigned `html_url`/`pdf_url` |
| `POST /api/reports/runs/{id}/send` `{recipients?, attach_pdf}` | admin | §5.3 |
| `GET /api/reports/runs/{id}/sends` | sales | send history |
| `POST /api/reports/runs/{id}/conversations` | sales | create-or-return the caller's grounded conversation (§7.1) |
| `DELETE /api/conversations/{id}/slots/{name}` | sales + RLS | clears one carried slot (the tag's ×); `name` in `office`, `periods`, `customer`, `port` |
| `GET /api/dimensions/values?column=&q=` | sales | generalizes the customers endpoint; `column` must be a certified dimension |
| `GET /api/reports/periods` | sales | months the data holds, from `DataClient.available_periods` |

### 5.6 Mail

```python
class OutboundMail: to: list[str]; subject: str; html: str; text: str;
                    attachments: list[tuple[str, bytes, str]]; reply_to: str | None
class MailReceipt: ok: bool; provider_message_id: str | None; error: str | None
class Mailer(Protocol): def send(self, mail: OutboundMail) -> MailReceipt
```

Implementations, selected by `MAIL_TRANSPORT`:

- `smtp` — `smtplib` to `SMTP_HOST:SMTP_PORT`, STARTTLS when `SMTP_STARTTLS=true`, login when
  `SMTP_USERNAME` is set. The relay case (no auth, port 25 or 587) is the expected production shape.
- `graph` — MSAL client-credentials token for `GRAPH_TENANT_ID`/`GRAPH_CLIENT_ID`/
  `GRAPH_CLIENT_SECRET`, `POST /v1.0/users/{GRAPH_SENDER}/sendMail` with the PDF as a file
  attachment (Graph's 3 MB inline cap is enforced; a larger PDF is sent as a link only).
- `stub` — writes the `.eml` to the artifact store under `mail/` and logs it (default locally and in
  every test).

Common settings: `MAIL_FROM`, `MAIL_REPLY_TO` (defaults to the sending admin's email),
`APP_PUBLIC_URL` (the link target `{APP_PUBLIC_URL}/reports/{run_id}`), `MAIL_TIMEOUT_SECONDS`.
Local compose adds a `mailpit` service (SMTP 1025, UI 8025) and the live override points
`MAIL_TRANSPORT=smtp` at it, so the rendered email is opened in a browser during the phase gate.

Email body: Outlook-safe HTML (tables, inline CSS, no scripts, no external CSS, no images) — the
header, the KPI table, the summary narrative, customers by GP, top ports by GP, deal class, the
"Open in Poseidon" button, a plain-text alternative, and the PDF attached. Doc 05 §7's egress table
gains the row: *Mail relay / Graph — report figures, narrative and the PDF, to internal recipients
listed on the definition — never conversation transcripts, user memory, or credentials.*

## 6. Reports UI

### 6.1 Routes and layout

The SPA adds `react-router-dom` (v7) with two routes: `/` (chat, unchanged) and `/reports/:runId`
(split view). The email link and the sidebar both navigate to the second. The shell gains a
three-column variant (`.app-shell.with-report`: 240 px sidebar · report panel · 400 px chat) and
collapses to sidebar + tabs (Report / Chat) below about 1180 px, as the mockup does. `ChatScreen`'s thread and composer are
extracted into a `ChatColumn` component used by both layouts; `ChatScreen` itself keeps its
behavior.

### 6.2 Sidebar

A Reports section above Conversations: definitions grouped by office kind, each expanding to its
recent runs with a status chip (draft = ok and never sent, sent, superseded, failed, running). Admins
see "New definition". Clicking a run opens the split view and its grounded conversation.

### 6.3 Report panel

Rendered from the payload by pure presentation components in `ui/report/` (`KpiTiles`,
`RankedTable`, `SvgFigure`, `MoverCard`, `NewLostTables`, `Narrative`, `SuggestedQuestions`,
`ProofBlock`), composed by `features/reports/ReportPanel.tsx`. Order and content match the mockup:
header (office, months, generated time, status, source), admin action bar (Send to list,
Recipients, Regenerate, Download PDF), six KPI tiles with deltas (Lost uses inverse coloring, the
Lost tile's footer shows the win rate), ranked tables under five sub-tabs, charts, movers with
account context, new/lost, narrative (with the withheld-narrative flag when `grounded=false`),
suggested-question chips, collapsed proof.

### 6.4 Admin dialogs

Definition editor (name, office kind, office value type-ahead via `/api/dimensions/values`,
comparison basis with only prior month selectable, recipients with email validation, enabled).
Generate (month picker from `/api/reports/periods`, the three "what happens" lines from the
mockup). Send (the definition's list as a checklist, one-off additions and removals for this send,
attach PDF, transport readiness line, last-send line).

### 6.5 Grounded chat column

Opening a run calls `POST …/conversations`, which creates (once per user per run) a conversation
titled `"{Month B} {year}, {office}"` with the seeded slots of §7.1 and an assistant opener: one
sentence naming the report and the default scope, plus the three suggested questions as chips.
A **tag row** above the composer shows the carried office, the month pair, and the customer or port
when carried; each tag's × calls the slot-clearing route. Doc 01 §3 describes these tags but the
current UI has none, so the row is new work in Phase 17 (built once, for all four slots).
Feedback thumbs work unchanged.

## 7. Chat extensions

### 7.1 Carried report context

`ConversationSlots` grows additively: `office_column: str | None`, `office_value: str | None`,
`report_run_id: str | None`. `SlotUpdates`/`apply_carry` gain the office pair under the same
tri-state rule (unset carries, `None` clears, a value replaces). A grounded conversation is seeded
with `office_column`, `office_value`, `report_run_id`, `period_a`/`period_b` from the run, and
`pass_through` = the report's top-10 customers (labelled `CUST_NM`) so "break that down by deal
class" can filter on exact names. `render_state_block` adds, when set:

```
Carried office filter: CUSTOMER_TEAM_NAME = GIBRALTAR OFFICE (Customer Broker Office). Apply it as a
  filter by default. Drop it for this turn only when the user asks for the whole book, the full
  account, other locations or offices, or a share of the total.
Report context: run 0198f2…, July 2026 vs August 2026, Gibraltar Office. Quote the report's own
  figures only through reporting.report_lookup.
Scope hint: book            # or "share"; present only when the scope hinter fires (§7.2)
```

Widening is **per turn**: the router omits the office filter for that call; the tag stays until the
user removes it or says so in words (owner decision).

### 7.2 Scope hinter

A new parsing stage after `skill_hinter`, lexicon-driven like it, sets `ParsedTurn.scope_hint` to
`book` on phrases such as "where else", "other ports/offices/locations", "full account", "whole
book", "overall", "across all offices", and to `share` on "how much of", "share of", "% of",
"portion of", "makes up". Advisory only; the router decides, and router-decision cases pin it.

### 7.3 `data_qa.metric_query` grows three arguments (backward compatible)

- **Breakdown with comparison.** `group_by` + `compare_period` is now legal. The skill runs the
  breakdown for both windows, merges them (§4.3 `merge_breakdowns`), and returns a `table` part with
  columns `[dimension, <metric> A, <metric> B, Change, %]` per requested metric plus `Driver` when
  GP and VOLUME are both requested, ordered by absolute change of the first metric (or by
  `order_by`/`order` below), capped at `top_n`. Pass-through captures the first column as today.
- **Ordering.** `order_by: str | None` (defaults to `metrics[0]`) and `order: "desc" | "asc"`
  (default `desc`) so "worst GP" and "lowest win rate" work (mom-comparison's diagnostic rule).
- **Share of total.** `share_of_total: list[str]` — filter columns dropped for the denominator. The
  skill runs the spec twice (all filters; filters minus those columns) and returns a `table` part
  `[Metric, Selection, Total, Share %]` with both queries in the proof. Validation: every listed
  column must be present in `filters`; `share_of_total` cannot combine with `group_by` or
  `compare_period` (422).

### 7.4 Shared math

§7.3 uses `core/analytics/mom.py`, the same module the report uses — one implementation of the
merge, the driver rule and the share.

### 7.5 `reporting.report_lookup` (router-visible)

`Args(section: Literal["kpis","customers_by_gp","customers_by_volume","ports_by_gp",
"ports_by_volume","deal_class","movers","new_lost","narrative","account_context"],
run_id: str | None)`. `run_id` defaults to the carried `report_run_id`; with neither, 422. Returns
the section as parts (`metric_grid` for KPIs, `table` for tables, `text` for narrative) with the
proof line `Report run <id>, generated <ts>, source <source>`. No queries. It exists so the
router's grounding rule ("quote only this turn's tool result") holds for report figures.

### 7.6 Router-decision cases (stub + `router_live`)

| User text (Gibraltar report carried) | Expected |
|---|---|
| why did GP drop for Meridian Bunkering? | `metric_query` filters office + `CUST_NM`, `group_by LOC_NM`, `compare_period` set, metrics GP, VOLUME, MARGIN |
| where else does Meridian lift fuel? | `metric_query` filters `CUST_NM` only (office dropped), `group_by LOC_NM` |
| give me the full account GP | `metric_query` filters `CUST_NM` only, `compare_period` set, no `group_by` |
| how much of my GP is of the total account GP? | `metric_query` filters office + `CUST_NM`, `share_of_total: [office column]` |
| what were the top ports in the report? | `report_lookup(section="ports_by_gp")` |
| compare to June instead | period carry changes A, office kept |
| break that down by deal class | `metric_query` with `CUST_NM IN (pass-through values)`, `group_by DEAL_CLASSIFICATION_TRADE_CUT` |
| slice it another way | clarification chips listing the certified dimensions (mom-comparison rule 13) |

## 8. Platform

### 8.1 Cortex provider (D40)

`core/llm/cortex.py`: `SNOWFLAKE.CORTEX.COMPLETE` through the Snowflake session, tool use emulated
with the strict-JSON prompt of doc 03 §1, normalized to the same `ToolCall`/`LLMResponse`. The
**parity test** replays recorded tool-calling scenarios through both providers and asserts identical
normalized shapes; it is offline (recorded fixtures) with a `router_live` variant. `models.yml`'s
cortex profile is already declared. Live validation is account-gated (§8.5).

### 8.2 Snowflake data client

`core/data/snowflake_client.py` implements `DataClient` over `snowflake-connector-python` using the
query builder's existing `snowflake` dialect. Session strategy keyed by `DEPLOY_MODE` (the wfs
pattern): `local` — password, PAT or key-pair from settings; `spcs` — OAuth token read fresh from
`/snowflake/session/token` on every connection, account and host injected by the platform; `ec2`
— Secrets Manager (later). SELECT/WITH-only guard on every statement. `available_periods` and
`list_dimension_values` are served from live data. `DATA_BACKEND=snowflake` selects it.

### 8.3 Snowflake Postgres on SPCS (D39)

`DATABASE_URL` is injected from a Snowflake secret; the same Alembic migrations apply. Migrations
0009/0010 (worker role, app-role membership) and `assert_boot_privileges` already assume no
superuser, which holds on Snowflake Postgres as on RDS. MinIO stays as an in-service container on a
block volume for artifacts (HTML, PDF, `.eml` from the stub mailer) with the existing
mirror-to-stage backup; S3 over an egress integration is the documented alternative.

### 8.4 SPCS service

`infra/spcs_spec.yaml`: containers `backend`, `worker` (same image, `python -m
poseidon.scripts.worker`), `minio`; one public endpoint. Environment: `DEPLOY_MODE=spcs`,
`IDENTITY_MODE=spcs_ingress`, `LLM_PROFILE=cortex`, `DATA_BACKEND=snowflake`,
`MAIL_TRANSPORT=smtp|graph`, `APP_PUBLIC_URL=<ingress url>`, `SPCS_SALES_USERS`,
`SPCS_REPORT_ADMINS`. External access integrations: Postgres egress (`<host>:5432`), the mail relay
(`<relay>:25|587`) or `graph.microsoft.com:443` + `login.microsoftonline.com:443`, and optionally
Bedrock, Perplexity and the Auth0 JWKS host. The Postgres ingress rule must include the SPCS egress
IP ranges from `SYSTEM$GET_SNOWFLAKE_EGRESS_IP_RANGES()`, which rotate; the runbook carries a dated
reminder (Triton's lesson).

### 8.5 Snowflake-side build handoff

Under `docs/snowflake/`, mirroring Triton:

- `COCO_BUILD_SPEC.md` — §0 blocking questions (below), Part A grants and objects, Part B Snowflake
  Postgres provisioning (`CREATE POSTGRES INSTANCE …`, the secret, ingress rule), Part C SPCS
  deploy (image repo, compute pool, spec, EAIs), Part D validation gates.
- `BUILD_SIGNOFF.md` — the stage-gate table: the Snowflake-side agent implements, posts evidence
  and stops; the reviewer approves. Steps: 0 questions resolved · 1 Postgres provisioned +
  migrations · 2 grants + data client parity · 3 Cortex live parity · 4 SPCS service READY +
  smoke · 5 one real report generated for one office, emailed to the owner only · 6 grounded chat
  cases live.
- `CORTEX_PROMPT.md` — the paste-ready prompt pointing at the files above and the hard constraints
  (no hand-built schema, migrations only; SELECT-only; never self-approve).

§0 blocking questions (answered on a Snowflake-connected session, recorded back into the spec and
into the ontology where they change semantics):

1. **Record type and status filters.** `MARINE_SALES_PLANNING_V` carries `RECORD_TYPE` and
   `POI_STATUS`; Triton's POI view keeps only `RECORD_TYPE='FUEL_LINE' AND POI_STATUS<>'VOID'`,
   while mom-comparison sums the view unfiltered and the certified metrics follow mom-comparison.
   Which is right for GP, volume, inquiries and won? If filtered: add the filter as a certified
   business rule, and the query builder applies it to every metric on this entity.
2. Exact distinct values (and casing) of `CUSTOMER_TEAM_NAME` and `PRIMARY_SUPPLY_TEAM_OFFICE`.
3. Confirm `LIFT_ETA_DATE` month bucketing matches the mom-comparison report the team already uses.
4. Mail: relay host/port/auth, the service mailbox, whether Graph is preferred; `APP_PUBLIC_URL`.
5. Snowflake Postgres: instance name, size, who holds `CREATE POSTGRES INSTANCE`.
6. EAI and network-rule names, the ingress-rule owner.
7. Cortex model ids available in the account's region.
8. The app's read role and warehouse; the Snowflake usernames for `SPCS_SALES_USERS` and
   `SPCS_REPORT_ADMINS`.

### 8.6 Adding a table

`docs/reference/adding-a-table.md`: the wfs lifecycle (investigate → propose → certify → compile)
run in the workspace, then vendor the certified entry into `ontology/ontology.yml`, update
`ontology/SOURCE.md`, add a synthetic profile stanza, let the loader contract test show the diff,
then reference the entity from skills. Report definitions can only slice certified dimension
columns, so a new office-like column becomes available to reports by certification alone.

## 9. Failure design

| Failure | Behavior |
|---|---|
| Data backend error during a run | run `error` with the problem detail; Regenerate creates a new pending run |
| Model narrative error or malformed JSON | template narrative, `grounded=false`, flag shown; run still `ok` |
| Grounding check fails twice | same as above with the count of unverified figures |
| Mail send fails | `report_send.status='failed'` with the error; resend allowed; run untouched |
| Office slice has no rows in either month | run `ok` with "no data" sections; Send refused with the reason |
| Duplicate generate | 409; the in-flight run is shown |
| Late-landing data | Regenerate; the new run supersedes; sent copies are kept |
| Worker down | runs stay `pending`; the panel shows "queued" with the created time; boot probe unchanged |
| Chat: `report_lookup` with no carried run | 422 turned into a plain "open a report first" reply |

## 10. Testing

- **Math**: offline pytest for `core/analytics/mom.py` with mom-comparison's expectations
  (`_compute_kpis`, `_compute_top_movers`, `_compute_new_lost` cases transcribed), property checks
  (KPIs equal the sum of breakdown rows; margin is always a ratio; the driver table at the 0.35/0.65
  boundaries; share arithmetic).
- **Report tools**: goldens against the seeded synthetic data (`pg` marker) for one office slice;
  proof-block snapshot; SVG snapshots; HTML and email template snapshots with an Outlook-safety lint
  (no `<script>`, no external stylesheet, no `position`/`flex`).
- **Grounding check**: unit tests for extraction, rounding, exemptions, retry and fallback.
- **Jobs**: claim, idempotence, supersede, attempts cap (`pg`).
- **Mail**: stub tests offline; one `mail`-marked test that sends through Mailpit and reads it back.
- **API**: admin-versus-sales matrix for every route; slice-value validation; period validation.
- **Chat**: router-decision cases of §7.6 in stub mode and under `router_live`; parser tests for the
  scope hinter; `metric_query` tests for the three new arguments; `report_lookup` tests.
- **Provider parity**: recorded-fixture parity test for Cortex; `router_live` variant.
- **Frontend**: vitest for the Reports store, sidebar section, panel components, dialogs, split
  view, tag clearing, chip → send; MSW handlers for every new route.
- **Playwright smoke**: create a definition → generate → open the split view → send → open the
  email link from Mailpit → ask "why did GP drop for X" and see a comparison table.

## 11. Docs and decision entries

- New `docs/architecture/09-reporting.md` (this design, condensed to the architecture voice).
- `00-overview.md`: D34–D43; `01-frontend.md`: routes, split view, Reports UI, tags;
  `02-backend-skills.md`: the `reporting` task, internal skills, `core/analytics`;
  `03-llm-routing.md`: narrative on `synthesis`, Cortex now, state-block additions;
  `04-data-ontology.md`: Snowflake client, the record-type question; `05-auth-identity.md`:
  ReportAdmin, shared tables, the mail egress row; `06-observability.md`: `kind='report_run'`;
  `07-infrastructure.md`: Snowflake Postgres, the SPCS spec, EAIs, Mailpit locally;
  `08-build-phases.md`: §12.
- `infra/runbooks/`: `deploy-spcs.md`, `snowflake-postgres.md`, `backup-restore-spcs.md`
  (artifacts only), `local.md` (Mailpit, live mail override).
- `docs/snowflake/*` (§8.5), `docs/reference/adding-a-table.md` (§8.6).
- `backend/.env.example`: every new variable, with a `--- SPCS ---` block.

## 12. Phases and gates

Phase 14 (EC2) is recorded as paused after its offline half (Tasks 1–6b complete; 7–9 pending).
The former Phases 15–16 (SPCS, Snowflake backend) fold into the new Phase 18; the optional
retrieval phase becomes 19.

| Phase | Deliverables | Gate (something the owner clicks or runs) |
|---|---|---|
| **15 Report engine** | `core/analytics`, the `reporting` task with all tools and the narrate subskill, grounding check, charts, HTML/PDF, migrations (definitions, runs, sends, `conversations.report_run_id`, `turn_run.kind`), `worker.py`, ReportAdmin role, the definitions/runs API | From the dev runner: create a definition for one synthetic office, generate August, open the PDF and the HTML; the payload reconciles (KPI totals = table sums); a run-log row of kind `report_run` exists |
| **16 Reports UI + email** | react-router, split-view shell, Reports sidebar, panel components, admin dialogs, mailer (smtp/graph/stub), Mailpit in compose, email templates, send API | In the browser: new definition → generate → preview → send → open the email in Mailpit → click "Open in Poseidon" → the split view opens |
| **17 Report-grounded chat** | slots + carry + state block, scope hinter, `metric_query`'s three arguments, `report_lookup`, grounded-conversation creation, tags, router-decision cases | The eight cases of §7.6 pass in stub mode; the first four pass under `router_live` on Bedrock; live in the browser: the mockup's Meridian turn |
| **18 Platform + handoff** | Cortex provider + parity test, Snowflake data client, Snowflake Postgres wiring, SPCS spec + runbooks, `docs/snowflake/*`, adding-a-table guide | Offline: parity and client tests green on recorded fixtures; the handoff docs reviewed. Account-gated: `BUILD_SIGNOFF.md` steps 0–6 approved on a Snowflake-connected session |

## 13. References

- Mockup: `docs/reference/mockups/2026-09-04-monthly-report-mockup.html`
- mom-comparison: `app/agent.py` (`_compute_kpis`, `_compute_top_movers`, `_compute_new_lost`,
  `_build_analysis_prompt`, `_build_followup_prompt`), `skills/analysis/mom-patterns.md`,
  `app/components/{kpi_cards,charts,narrative}.py`
- wfs workspace: `core/wfs_core/analytics.py` (parameterized port), `core/wfs_core/snowflake_client.py`
  (session by deploy mode), `ontology/ontology.yml`
- Triton: `docs/snowflake/{COCO_BUILD_SPEC,BUILD_SIGNOFF,CORTEX_PROMPT}.md`, `deploy/spec.yaml`,
  `backend/app/snowflake_conn.py`
- Poseidon: docs 00–08, `core/chat/orchestrator.py` (pass-through), `core/llm/prompts.py`
  (state block), `scripts/memory_worker.py` (claim pattern), `tasks/data_qa/skills/metric_query`
