# Monthly Performance Reports and Report-Grounded Chat — Design

Status: Revised draft for owner review · Date: 2026-09-04 (revised the same day after the adversarial
design review recorded in `2026-09-04-monthly-performance-reports-review.md`) · Owner decisions
recorded inline
Visual target: `docs/reference/mockups/2026-09-04-monthly-report-mockup.html` (three tabs: report view,
email, admin dialogs; synthetic figures that reconcile across every table)

## 1. What this adds

Poseidon gains a second deliverable next to chat: an **office-level monthly performance report**
that a report admin generates on demand, previews inside Poseidon, and emails to a recipient list.
Every report opens beside a conversation that is grounded in that report, so the team can ask
follow-up questions and drill into the numbers with the same certified skills the chat already
uses. The report is the fixed "current view"; the flexibility lives in the chat, where a user can
ask for a customer's GP for 2025 by month, then by port, and keep drilling.

The report reproduces what the mom-comparison app does today (KPIs with deltas, top movers with a
volume-versus-margin driver, new and lost accounts, a narrative written over pre-computed numbers)
and adds two things: the five ranked tables the owner asked for, and **account context** — for each
top mover, how that customer's whole-book business moved and which other offices absorbed it.

Platform work rides along because the audience runs on Snowflake: the Cortex LLM provider is built
now over Snowflake's native tool-calling API, a Snowflake data client arrives, the SPCS app database
becomes Snowflake Postgres, and a Snowflake-side build handoff is written in the Triton stage-gate
format. The first phase already ends with a report a user can open and question in the chat.

### Non-goals (v1)

- No scheduled generation. Reports are produced on demand only (owner decision).
- No per-person scoping. A report is one office slice sent to a list; every Sales user can open
  every report (D44).
- No comparison basis other than previous month (the basis is an extension point, §4.1).
- No self-healing of narratives. A failed grounding check falls back to the template narrative.

## 2. Owner decisions (2026-09-04)

| # | Decision |
|---|----------|
| D34 | A report is deterministic skill output: Python queries, computes and renders every figure; the synthesis model only writes prose over the computed JSON, and a grounding check verifies every number it writes. |
| D35 | Report scope is an **office slice**: `CUSTOMER_TEAM_NAME` (Customer Broker Office) or `PRIMARY_SUPPLY_TEAM_OFFICE` (Supply Team Office), or the whole book. A report definition = office column + office value + recipient list. Generation is on demand; the flow is generate → preview → send. |
| D36 | Comparison basis is an enum with one implemented value, `prior_month`. Same-month-prior-year and YTD are declared values that fail validation until built. Exploration of other month pairs and other dimensions happens in the chat, not in definitions. |
| D37 | Report definitions, runs and sends are **shared org documents**: granted to the app role, no per-user row policies. Writes are gated by a new role `Poseidon:ReportAdmin`; reads by `Poseidon:Sales`. |
| D38 | Email goes through one `Mailer` interface. **Microsoft Graph is the production transport on SPCS** (SPCS egress only allows ports 22, 80, 443 and 1024+); SMTP serves local Mailpit and EC2; a stub serves tests. The mail path is a new egress processor: report content to allow-listed internal domains, never conversation content. |
| D39 | On SPCS the app database is **Snowflake Postgres** (revises D20). Report HTML and PDF are stored as bytes in Postgres and served through the API in every habitat; MinIO is not part of the SPCS service. |
| D40 | `CortexProvider` is built now (revises D33's timing, not its interface) over the **Cortex REST Messages endpoint with native tool calling**, not the SQL `COMPLETE` function with emulated tools. |
| D41 | Report chat carries the office filter and the month pair by default. Scope is an **explicit, validated skill argument** (`office` or `book`) that the skill applies deterministically from carried state; the model chooses the value, code applies the filter. Share-of-total is computed in Python. The report's own figures reach the model only through the `reporting.report_lookup` tool result. |
| D42 | The EC2 deployment (Phase 14) is paused after its completed offline half; this work sequences first. |
| D43 | The report content and math follow mom-comparison exactly where it already works: two single-month frames merged in Python, its KPI, top-mover, driver and new/lost rules, its narrative templates. |
| D44 | **Visibility:** the whole book is visible to every Sales user, including other offices' customer-level GP and margin, as in mom-comparison today. Recorded so a later per-office restriction is a conscious change, not drift. |
| D45 | The query builder gains a **multi-column frame query** (group by several certified columns, plus a month/quarter/year time bucket, no row limit). A report is four to six queries, and the chat can answer "by port by month". |

## 3. Report definition

```
report_definition
  id uuid pk (uuid7)
  name text not null unique
  slice_column text not null check (slice_column in ('CUSTOMER_TEAM_NAME','PRIMARY_SUPPLY_TEAM_OFFICE'))
  slice_value text null                    -- a certified dimension value, or NULL = whole book
  comparison_basis text not null default 'prior_month' check (comparison_basis in ('prior_month'))
  recipients jsonb not null default '[]'   -- array of email strings, validated on write (§5.6)
  enabled boolean not null default true
  version int not null default 1           -- optimistic concurrency on PUT
  created_by text not null, created_at timestamptz, updated_at timestamptz
```

`slice_value` is validated against `DataClient.list_dimension_values(entity, slice_column)` on
create and update; an unknown value is a 422. `NULL` means the whole book, used for company-level
reports and as the reconciliation oracle against mom-comparison (§8.5). The office picker in the UI
is a type-ahead over the same call. `PUT` carries `version`; a stale version is a 409.

## 4. Report content contract

### 4.1 Periods

`resolve_periods(basis, period_b: date) -> (PeriodWindow a, PeriodWindow b)` is the only function
that knows what a basis means. `prior_month`: B = the report month, A = the calendar month before
it. Both are half-open `PeriodWindow`s. A future basis adds one branch here and nothing elsewhere.

### 4.2 Queries: two wide frames per month (D45)

One frame **per month**, merged in Python; never a pivoted two-month query. The report runs the new
`FrameQuerySpec` (§7.4) once per window, filtered to the office when `slice_value` is set:

```
FrameQuerySpec(entity=MARINE_SALES_PLANNING_V,
               metrics=(GP, VOLUME, NUM_WON, NUM_INQUIRIES, NUM_LOST),
               period=window,
               group_by=(CUST_NM, LOC_NM, CUSTOMER_TEAM_NAME, PRIMARY_SUPPLY_TEAM_OFFICE,
                         DEAL_CLASSIFICATION_TRADE_CUT),
               filters={slice_column: (slice_value,)})
```

Every table, KPI, mover and new/lost row rolls up from these two frames in Python (sums are exact
under roll-up; MARGIN and WIN_RATE are recomputed from the rolled-up sums, never averaged). Two
`MetricQuerySpec` totals per window run as an independent control: the frame's roll-up must equal
the total to the cent, or the run fails loudly. Account context (§4.5) adds two whole-book frames.
Six queries per report; no row cap and no `LIMIT`.

### 4.3 Math (ported from mom-comparison, one shared module)

`backend/poseidon/core/analytics/mom.py` holds the month-over-month math used by both the report
tools and the chat skill, ported from `mom-comparison/app/agent.py` and the parameterized
`wfs_core.analytics`, with the same expectations as tests:

- `compute_kpis(frame_a, frame_b)`: VOLUME, GP, MARGIN, NUM_WON, NUM_INQUIRIES, NUM_LOST, each
  `{a, b, change, pct_change}`; MARGIN = GP over VOLUME; WIN_RATE = NUM_WON over NUM_INQUIRIES.
- `merge_breakdowns(rows_a, rows_b, key)`: outer join on the key, zero-filled, `{a, b, change,
  pct}` per metric; pct against `abs(a)`. **Zero-base rule:** when `a` is 0 the pct is `null`
  (rendered "new"), not `+0.0%`, and a group absent from A is labelled `new`, never
  `margin-driven`; a group absent from B is `lost`.
- `top_movers(merged, n=5)`: top n increases and top n decreases by absolute GP change, each with GP,
  VOLUME and MARGIN deltas and a `driver`, using mom-comparison's rule verbatim: volume effect =
  Δvolume × old margin; margin effect = Δmargin × new volume; share of volume effect > 0.65 →
  `volume-driven`, < 0.35 → `margin-driven`, else `both`; both effects zero → `unchanged`; new
  groups → `new`.
- `new_lost(merged)`: keys present in B only / A only, with GP, VOLUME, MARGIN, GP-sorted.
- `ranked(merged, by, n=10)`: rows sorted by the B value of `by`.
- `share(numerator_values, denominator_values)`: per-metric share, used by §7.3.

Display rounding (mom-comparison's rules): money and tons whole, percentages one decimal, margin two
decimals, "flat" when a change is zero. **Formatting happens once, in Python:** the payload
carries both raw numbers and display strings (`en-US` separators, unit labels `USD`, `t`,
`USD/t`); the panel, the email and the PDF render the display strings, so the three surfaces and
the grounding check share one formatting.

### 4.4 Tables in the report

Five ranked tables, ranked by month B and showing A, B, change and percent for GP, VOLUME and
MARGIN on every row: customers by GP (10), customers by volume (10), ports by GP (10), ports by
volume (10), deal class (all rows). Every table closes with a total row that equals the KPI tile.

### 4.5 Top movers and account context

Five increases and five decreases by GP (§4.3). For the ten movers together, two whole-book frames
(no office filter, `filters={CUST_NM: (the ten,)}`, same group-by as §4.2) give, per mover:

- `book`: the customer's whole-book GP/VOLUME/MARGIN for A and B with change and pct;
- `share_of_book`: the office's share of the customer's GP in A and in B;
- `reconciliation`: `{office_change, other_offices_net = book_change − office_change,
  book_change}`, always displayed ("office −56,500; other offices net +44,100; book −12,400");
- `offsets`: computed **only on the dimension disjoint from the slice** — other values of
  `slice_column` — so every group is outside this office by construction; the groups whose GP
  change has the opposite sign to the office's, sorted by magnitude, up to five;
- `ports_rest_of_book`: the customer's port breakdown over the rest of the book (whole-book port
  totals minus this office's port totals), so "moved to Algeciras" is never contaminated by the
  office's own Algeciras lifts.

The narrative may state these facts and may not chain them into a causal story the data cannot
support (no "the Spain Office gain happened at Algeciras supplied by the Med team"; the frames are
marginals, and the prompt says so). A whole-book report has no account context section.

### 4.6 New and lost customers

Within the office slice (§4.3 `new_lost` on the customer roll-up).

### 4.7 Narrative — two layers

1. **Template narrative** (always present, zero model calls): Python fills mom-comparison's
   `mom-patterns.md` templates — Overall Summary, Key Drivers (top increases/decreases with volume,
   margin, driver), New/Lost — from the computed JSON, using the display strings of §4.3.
2. **Model narrative**: the `synthesis` role receives only the pre-computed block (KPIs, top movers
   with drivers, new/lost capped at 10, account context with its reconciliation lines) plus the
   analysis instructions ported from mom-comparison's analysis prompt and playbook. The output is
   requested as a **forced tool call** with a fixed schema (the provider-native way to get JSON on
   both Bedrock and Cortex): `{narrative_md, insights: [3-5], account_context_notes: [],
   suggested_questions: [3]}`. `narrative_md` follows the template's section order and adds an
   Account context section.
3. **Grounding check** (deterministic): the prose is tokenized into numeric tokens (digits with
   separators, decimals, percent signs, a leading sign or "minus"); each token, normalized, must
   match a display string derived from the payload (values at their display rounding, win rates
   and shares at one decimal, reconciliation figures included). Years, month names and ranks 1–10
   are exempt. The check is pinned against the mockup narrative as a fixture: it must pass every
   figure the mockup states. One violation → one retry with the violating figures listed; a second
   failure → the template narrative is used, `narrative.grounded=false`, and the UI shows "AI
   narrative withheld: N unverified figures".

### 4.8 Suggested questions

The three `suggested_questions` are rendered as chips in the report and in the grounded
conversation's opener; each chip's `send_text` is the question verbatim. They must name only
entities present in the payload (checked like numbers; a bad chip is dropped, not rewritten).

### 4.9 Charts

Server-side SVG rendered by Python from the payload, so every surface shows the same image and
snapshot tests pin the output: grouped horizontal bars (A vs B) and a change waterfall for
customers and for ports, and grouped bars for deal class. Marks follow the dataviz rules used in the
mockup (≤24 px thick, 2 px surface gap, direct labels on the B bar and on every change bar, a legend
for the two series, `role="img"` with an accessible name, colour never the only carrier of
sign). The panel shows all five figures; the PDF embeds the customer and deal class figures only.
Charts are not embedded in the email. Every data-sourced string in an SVG is escaped (§5.7).

### 4.10 Payload (versioned, `payload_version: 1`)

```
{ definition: {id, name, slice_column, slice_value|null, basis},
  periods: {a: {start,end}, b: {start,end}, label_a, label_b},
  kpis: {VOLUME|GP|MARGIN|NUM_WON|NUM_INQUIRIES|NUM_LOST|WIN_RATE: {a,b,change,pct_change, display:{a,b,change,pct}}},
  tables: {customers_by_gp, customers_by_volume, ports_by_gp, ports_by_volume, deal_class:
           {rows: [{key, a:{...}, b:{...}, change:{...}, pct:{...}, display:{...}}], total}},
  movers: {increases: [...], decreases: [...]},           // §4.3 entries
  new_lost: {new: [...], lost: [...]},
  account_context: {<customer>: {book, share_of_book, reconciliation, offsets, ports_rest_of_book}},
  narrative: {template_md, llm_md|null, grounded, flags: [str]},
  suggested_questions: [str, str, str],
  charts: {customers_grouped, customers_change, ports_grouped, ports_change, deal_class_grouped},  // svg strings
  proof: [str], queries: [{label, sql_hash, rows, ms}], generated_at, source, generated_by }
```

The same payload feeds the in-chat report part, the report panel, the email template and the PDF.

## 5. Generation pipeline

### 5.1 Code layout

```
backend/poseidon/tasks/reporting/
  task.yml
  skills/
    monthly_performance/          # router-visible only through the flow entry (§6.5); SKILL_META internal=true
      skill.py                    # run(ctx, args) -> SkillResult: one `report` part + PDF ArtifactRef + proof
      schema.py                   # Args(slice_column, slice_value|None, period_b)
      prompts/narrate.md          # v1, embeds the mom-patterns templates + analysis instructions
      tools/
        resolve_periods.py        # §4.1
        fetch_frames.py           # the two slice frames + two totals (§4.2)
        account_context.py        # §4.5 (two whole-book frames)
        assemble_payload.py       # math (§4.3) -> payload (§4.10), display strings included
        template_narrative.py     # §4.7 layer 1
        grounding_check.py        # §4.7 layer 3
        render_charts.py          # §4.9
        render_html.py            # report.html.j2 -> HTML string (autoescape on)
        render_pdf.py             # HTML -> PDF bytes (WeasyPrint, lazy import, url_fetcher disabled)
      subskills/narrate/subskill.py   # §4.7 layer 2, via ctx.llm role "synthesis", forced tool call
      tests/
    report_lookup/                # router-visible (§7.5)
backend/poseidon/core/analytics/mom.py          # §4.3, shared with data_qa
backend/poseidon/core/data/specs.py             # + FrameQuerySpec (§7.4)
backend/poseidon/core/mail/{base,smtp,graph,stub}.py   # §5.6
backend/poseidon/reports/{store,jobs,service}.py       # definitions/runs/sends store, the job runner, send orchestration
backend/poseidon/api/reports.py                        # §5.5
backend/poseidon/scripts/worker.py                     # §5.2
backend/poseidon/config/templates/report.html.j2, email/report.html.j2, email/report.txt.j2
```

Framework seams the design needs, added in Phase 15: `SkillResult.payload: dict | None` (a
structured result a caller keeps beside the parts), `ArtifactStore.put(key, bytes, mime)` and
`ArtifactStore.get(key)`, and a registry flag `internal: true` on `SKILL_META` (discovered,
validated and dispatchable in code; excluded from `TOOL_SCHEMAS`).

### 5.2 Durable job (Phase 16)

Phase 15 runs the skill synchronously inside a chat turn (§6.5); the job below arrives with the
Reports screen.

```
report_run
  id uuid pk (uuid7), definition_id uuid fk, period_a date, period_b date, basis text,
  status text check (status in ('pending','ok','error','superseded')),
  requested_by text not null, attempts int default 0,
  payload jsonb, html bytea, pdf bytea, turn_run_id uuid, error jsonb,
  created_at, started_at, finished_at
  unique index (definition_id, period_b) where status = 'pending'   -- one in-flight run per month
```

`POST …/runs` inserts a `pending` row; the partial unique index makes a duplicate a 409 without a
read-then-insert race. The worker claims exactly as memory distillation does: `SET LOCAL ROLE
poseidon_worker`, `SELECT … WHERE status='pending' AND attempts < :max FOR UPDATE SKIP LOCKED`,
`attempts+1`, and **holds the row lock for the duration of the job** — there is no `running`
state, so a worker crash releases the row and the next cycle retries it, and the attempts cap
(`REPORT_MAX_ATTEMPTS`, default 2) turns a repeat failure into `error`. On success the previous `ok`
run for the same definition and month becomes `superseded` (its bytes and sends are kept). Every
frame query carries a Snowflake `QUERY_TAG` of `poseidon:report_run:<id>` and its duration is
recorded in `payload.queries`.

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
  attach_pdf boolean, idempotency_key uuid unique, created_at, sent_at
```

`POST …/send` renders the email from the payload, calls the mailer synchronously (timeout
`MAIL_TIMEOUT_SECONDS`, default 20), and records the outcome; the client-generated
`idempotency_key` makes a retried click a no-op. A failed send is retryable. A run can be sent any
number of times; the panel shows the last send. Send is refused (409, with the reason) for runs
that are not `ok`, for superseded runs, and for runs whose slice returned no rows.

### 5.4 Privilege model

`report_definition`, `report_run` and `report_send` are shared org documents (D37, D44): `GRANT
SELECT, INSERT, UPDATE` to `poseidon_app`, `GRANT SELECT, UPDATE ON report_run` to
`poseidon_worker` (the claim runs under the worker role for uniformity with the memory claim; the
grant is what makes it legal, no row policy is involved), no row policies. This is the first table
family in the app that is deliberately not user-scoped; the migration's docstring and doc 05 §4 say
so. `conversations` gains a nullable `report_definition_id` and `report_period_b` with a unique
index on `(user_sub, report_definition_id, report_period_b)` where not null, so each user has at
most one grounded conversation per report month, and that conversation follows the **current** `ok`
run when a regenerate supersedes the old one (§7.5); `conversations` keeps its RLS.

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
| `POST /api/reports/definitions` · `PUT …/{id}` | admin | no delete in v1: retire with `enabled=false`; `PUT` carries `version` (409 on stale) |
| `POST /api/reports/definitions/{id}/runs` `{period: "YYYY-MM"}` | admin | 202 `{run_id}`; 409 on an in-flight duplicate (partial unique index); 422 if the month is outside `available_periods` |
| `GET /api/reports/runs?definition_id=&status=&cursor=` | sales | cursor-paginated, newest first |
| `GET /api/reports/runs/{id}` | sales | run + payload |
| `GET /api/reports/runs/{id}/html` · `…/pdf` | sales | bytes streamed from Postgres with `Content-Disposition`; the only delivery path in every habitat |
| `POST /api/reports/runs/{id}/send` `{recipients?, attach_pdf, idempotency_key}` | admin | §5.3 |
| `GET /api/reports/runs/{id}/sends` | sales | send history |
| `POST /api/reports/runs/{id}/conversations` | sales | create-or-return the caller's grounded conversation for that definition and month |
| `DELETE /api/conversations/{id}/slots/{name}` | sales + RLS | clears one carried slot (the tag's ×); `name` in `office`, `periods`, `customer`, `port` |
| `GET /api/dimensions/values?column=&q=` | sales | generalizes the customers endpoint; `column` must be a certified dimension |
| `GET /api/reports/periods` | sales | months the data holds, from `DataClient.available_periods` |

### 5.6 Mail

```python
class OutboundMail: to: list[str]; subject: str; html: str; text: str;
                    attachments: list[tuple[str, bytes, str]]; reply_to: str
class MailReceipt: ok: bool; provider_message_id: str | None; error: str | None
class Mailer(Protocol): def send(self, mail: OutboundMail) -> MailReceipt
```

Implementations, selected by `MAIL_TRANSPORT`:

- `graph` — **the SPCS production transport.** MSAL client-credentials token for
  `GRAPH_TENANT_ID`/`GRAPH_CLIENT_ID`/`GRAPH_CLIENT_SECRET` (the secret injected as a Snowflake
  secret on SPCS), `POST /v1.0/users/{GRAPH_SENDER}/sendMail` with the PDF as a file attachment
  (Graph's 3 MB inline cap is enforced; a larger PDF is sent as a link only). Egress:
  `graph.microsoft.com:443` and `login.microsoftonline.com:443`. The Entra app registration with
  `Mail.Send` scoped to one service mailbox is the primary IT ask (§8.5 Q4).
- `smtp` — `smtplib` to `SMTP_HOST:SMTP_PORT`, STARTTLS when `SMTP_STARTTLS=true`, login when
  `SMTP_USERNAME` is set. Serves local Mailpit (port 1025) and the EC2 habitat. **Not deployable
  on SPCS** unless the relay listens on a port ≥ 1024 or the account team grants a port exception.
- `stub` — stores the `.eml` bytes on the send row and logs it (default in every test).

Common settings: `MAIL_FROM`, `MAIL_REPLY_TO` (required; the sending identity on SPCS has no email
address), `MAIL_ALLOWED_DOMAINS` (every recipient must match; validated on definition write and on
send, fail-closed when empty), `APP_PUBLIC_URL` (the link target `{APP_PUBLIC_URL}/reports/{run_id}`),
`MAIL_TIMEOUT_SECONDS`. Local compose adds a `mailpit` service (SMTP 1025, UI 8025) and the live
override points `MAIL_TRANSPORT=smtp` at it, so the rendered email is opened in a browser during
the phase gate.

Email body: Outlook-safe HTML (tables, inline CSS, no scripts, no external CSS, no images) — the
header, the KPI table, the summary narrative, customers by GP, top ports by GP, deal class, the
"Open in Poseidon" button, a plain-text alternative, and the PDF attached. Doc 05 §7's egress table
gains the row: *Mail (Graph / SMTP) — report figures, narrative and the PDF, to recipients on
allow-listed internal domains — never conversation transcripts, user memory, or credentials.*

### 5.7 Rendering hygiene

Customer and port names are free text from an upstream CRM. Every template that receives them
(`report.html.j2`, the two email templates, the SVG renderer) runs with autoescaping on; the
existing unescaped Jinja pattern used for briefs is not copied. WeasyPrint renders with a
`url_fetcher` that refuses every URL, so a name containing markup can never make the worker fetch
anything. The SPA injects SVG strings only after the same server-side escaping, and never
interpolates payload strings into markup itself.

## 6. Reports UI

### 6.1 Routes and layout

The SPA adds `react-router-dom` (v7) with two routes: `/` (chat, unchanged) and `/reports/:runId`
(split view). The email link and the sidebar both navigate to the second. The shell gains a
three-column variant (`.app-shell.with-report`: 240 px sidebar · report panel · 400 px chat) and
collapses to sidebar + tabs (Report / Chat) below about 1180 px, as the mockup does. `ChatScreen`'s
thread and composer are extracted into a `ChatColumn` component used by both layouts.

**Server-side history fallback (required by the router, Phase 16):** the production image serves
the SPA through Starlette's `StaticFiles`, which 404s any path that is not a file. `create_app`
replaces the mount with a small subclass that returns `index.html` for `GET`/`HEAD` requests that
miss a file, are not under `/api` or `/health`, and accept `text/html`; unknown `/api` paths keep
their JSON 404. Pinned in `test_static_serving.py` (`GET /reports/<uuid>` → 200, `index.html`).
The Auth0 boundary passes `appState.returnTo = location.pathname + search` into the login redirect
and navigates back to it after the callback, so an emailed link survives a login.

### 6.2 Sidebar

A Reports section above Conversations: definitions grouped by office kind, each expanding to its
recent runs with a status chip (draft = ok and never sent, sent, superseded, failed, queued).
Admins see "New definition". Clicking a run opens the split view and its grounded conversation.

### 6.3 Report panel and the in-chat report part

One presentation component tree in `ui/report/` (`KpiTiles`, `RankedTable`, `SvgFigure`,
`MoverCard`, `NewLostTables`, `Narrative`, `SuggestedQuestions`, `ProofBlock`) renders a payload.
It is used twice: as the renderer of the new `report` message part (Phase 15, inside a
conversation) and as the body of `features/reports/ReportPanel.tsx` (Phase 16, the split view).
Order and content match the mockup: header (office, months, generated time, status, source), the
admin action bar in the panel only (Send to list, Recipients, Regenerate, Download PDF), six KPI
tiles with deltas (Lost inverse-coloured, with a sign glyph as well; the Lost tile's footer shows
the win rate), ranked tables under five sub-tabs, charts, movers with account context and the
reconciliation line, new/lost, narrative (with the withheld-narrative flag when
`grounded=false`), suggested-question chips, collapsed proof. Tables use real table semantics; the
panel does not add a second live region beside the chat's.

### 6.4 Admin dialogs (Phase 16)

Definition editor (name, office kind, office value type-ahead or "whole book", comparison basis
with only prior month selectable, recipients with email and domain validation, enabled). Generate
(month picker from `/api/reports/periods`, the three "what happens" lines from the mockup). Send
(the definition's list as a checklist, one-off additions and removals for this send within the
allowed domains, attach PDF, transport readiness line, last-send line).

### 6.5 Flow entry and the grounded conversation

**Phase 15 (thin, user-visible):** a third opener chip, "Monthly report", under the existing D19
entry rule. The subject turn offers office chips built from the certified values of the two office
columns (plus "whole book"), then a month (default: the last complete month, chips to change). The
skill runs synchronously inside that turn, streaming tool steps as it goes, and returns one
`report` part (rendered by §6.3's tree), the PDF as an artifact card, and the proof block. The
conversation's slots carry the office and the month pair, the report's top-10 customers as
pass-through, and the payload reference, so the follow-up questions of §7 work from the next
message. This is the report-in-chat plus follow-ups the team asked for, on plumbing the brief flows
already exercise.

**Phase 16:** opening a run from the sidebar calls `POST …/conversations`, which creates (once per
user per definition and month) a conversation titled `"{Month B} {year}, {office}"` seeded the
same way, with an assistant opener naming the report and the default scope plus the three suggested
questions as chips. A **tag row** above the composer shows the carried office, the month pair, and
the customer or port when carried; each tag's × calls the slot-clearing route. Doc 01 §3 describes
these tags but the current UI has none, so the row is new work, built once for all four slots.
Feedback thumbs work unchanged.

## 7. Chat extensions

### 7.1 Carried report context

`ConversationSlots` grows additively: `office_column`, `office_value`, `report_ref` (definition id
and month in Phase 16; the in-turn payload id in Phase 15). `SlotUpdates`/`apply_carry` gain the
office pair under the tri-state rule (unset carries, `None` clears, a value replaces). A grounded
conversation is seeded with the office pair, `period_a`/`period_b`, `pass_through` = the report's
top-10 customers (labelled `CUST_NM`), and `report_ref`. `render_state_block` adds, when set:

```
Carried office: CUSTOMER_TEAM_NAME = GIBRALTAR OFFICE (Customer Broker Office). data_qa.metric_query
  applies it when scope="office" (the default). Use scope="book" when the user asks for the whole
  book, the full account, other locations or offices, or a share of the total.
Report context: Gibraltar Office, July 2026 vs August 2026. Quote the report's own figures only
  through reporting.report_lookup.
Scope hint: book            # or "share"; present only when the scope hinter fires (§7.2)
```

Widening is **per turn**: the model picks `scope="book"` for that call; the tag stays until the
user removes it or says so in words (owner decision).

### 7.2 Scope hinter

A new parsing stage after `skill_hinter`, lexicon-driven like it, sets `ParsedTurn.scope_hint` to
`book` on phrases such as "where else", "other ports/offices/locations", "full account", "whole
book", "overall", "across all offices", and to `share` on "how much of", "share of", "% of",
"portion of", "makes up". Advisory only; the router decides, and router-decision cases pin it.

### 7.3 `data_qa.metric_query` grows (backward compatible)

- **Scope (D41).** `scope: Literal["office","book"] = "office"`. When the carried office is set and
  `scope == "office"`, `skill.run` stamps `{office_column: (office_value,)}` onto every spec it
  builds (both windows of a comparison, both queries of a share), the same place and discipline as
  D16's row scope. The model never authors the office filter and cannot forget it.
- **Breakdown with comparison.** `group_by` + `compare_period` is now legal: both windows run,
  `merge_breakdowns` (§4.3) joins them, and the result is a `table` part with `[dimension(s),
  <metric> A, <metric> B, Change, %]` per requested metric plus `Driver` when GP and VOLUME are
  both requested, ordered by absolute change of the first metric (or by `order_by`/`order`),
  capped at `top_n`. Pass-through captures the first column as today.
- **Multi-column and time-bucket group-by (D45).** `group_by: list[str]` (one to three entries)
  over certified dimensions plus the pseudo-dimensions `MONTH`, `QUARTER`, `YEAR` (rendered as
  `DATE_TRUNC` on the entity's date column, ordered chronologically when present). "GP for Maersk
  for 2025 by month" is `filters CUST_NM`, `period` = calendar 2025, `group_by [MONTH]`; "then by
  port" is `group_by [LOC_NM]` with the carried customer and period; "by port by month" is
  `group_by [LOC_NM, MONTH]`. A time-bucketed result renders as a `table` part with the bucket
  first; a single-series monthly result also gets a small line chart part (`chart`, SVG).
- **Ordering.** `order_by: str | None` (defaults to `metrics[0]`) and `order: "desc" | "asc"`
  (default `desc`) so "worst GP" and "lowest win rate" work (mom-comparison's diagnostic rule).
- **Share of total.** `share_of_total: list[str]` — filter columns dropped for the denominator
  (the carried office counts as a filter here). The skill runs the spec twice and returns a `table`
  part `[Metric, Selection, Total, Share %]` with both queries in the proof. Validation: every
  listed column must be present in the effective filters; `share_of_total` cannot combine with
  `group_by` or `compare_period` (422).

### 7.4 Frame query and shared math

`FrameQuerySpec(entity, metrics, period, group_by: tuple[str, ...], filters, scope_value)` joins
the existing specs; `build_frame_query` validates each column with `_require_dimension` (or the
time-bucket rule), renders `COALESCE` per column and `GROUP BY 1..n`, applies `_where_clause` and
the row-scope predicate unchanged, emits no `ORDER BY`/`LIMIT`, refuses volume mode and refuses
`WIN_RATE` (a ratio the caller recomputes). Both dialects get byte-pinned snapshots. §7.3 and the
report share `core/analytics/mom.py`, so there is one implementation of the merge, the driver rule
and the share.

### 7.5 `reporting.report_lookup` (router-visible)

`Args(section: Literal["kpis","customers_by_gp","customers_by_volume","ports_by_gp",
"ports_by_volume","deal_class","movers","new_lost","narrative","account_context"])`. The section
comes from the conversation's `report_ref`: the in-turn payload in Phase 15, or the **current `ok`
run** for the carried definition and month in Phase 16, so a regenerate never strands a
conversation on superseded numbers; the proof line names the run and its generated time, and says
"regenerated since you opened this report" when the run differs from the one the conversation was
opened on. With no report context, 422 turned into a plain "open a report first" reply. Returns
parts (`metric_grid` for KPIs, `table` for tables, `text` for narrative) sized within the loop's
tool-result cap (long sections page by `top_n`). No queries.

### 7.6 Router-decision cases (stub + `router_live`)

| User text (Gibraltar report carried) | Expected |
|---|---|
| why did GP drop for Meridian Bunkering? | `metric_query` scope office, filters `CUST_NM`, `group_by [LOC_NM]`, `compare_period` set, metrics GP, VOLUME, MARGIN |
| where else does Meridian lift fuel? | `metric_query` scope **book**, filters `CUST_NM`, `group_by [LOC_NM]` |
| give me the full account GP | `metric_query` scope book, filters `CUST_NM`, `compare_period` set, no `group_by` |
| how much of my GP is of the total account GP? | `metric_query` scope office, filters `CUST_NM`, `share_of_total: [office column]` |
| give me the GP breakdown for Meridian for 2025 by month | `metric_query` scope office (the default), filters `CUST_NM`, period = calendar 2025, `group_by [MONTH]` |
| then by port | `metric_query` same filters and period, `group_by [LOC_NM]` |
| and by port by month | `group_by [LOC_NM, MONTH]` |
| what were the top ports in the report? | `report_lookup(section="ports_by_gp")` |
| compare to June instead | period carry changes A, office kept |
| break that down by deal class | `metric_query` with `CUST_NM IN (pass-through values)`, `group_by [DEAL_CLASSIFICATION_TRADE_CUT]` |
| slice it another way | a deterministic clarification: chips listing the certified dimensions (mom-comparison rule 13), produced by the parser's ambiguity path, not by the model |

## 8. Platform

### 8.1 Cortex provider (D40)

`core/llm/cortex.py`: an `httpx` client over `POST https://{SNOWFLAKE_HOST}/api/v2/cortex/v1/messages`
(Anthropic Messages format) with native `tools`, `tool_choice`, `tool_use` and `tool_result`.
Translation happens at the provider boundary only: the loop's Converse-shaped `toolUse` →
`tool_use {id, name, input}`, `toolResult {toolUseId}` → `tool_result {tool_use_id}`, the system
string → `system`; skill ids translated `.` ↔ `__` exactly as `bedrock.py` does; token usage read
from the response. Auth: the SPCS OAuth token file read fresh per call (the same file the data
client reads), a PAT or key-pair locally. Models: the `cortex` profile in `models.yml` (Claude
models served inside the Snowflake account; nothing leaves the boundary and no other vendor is
involved). The **parity test** replays recorded tool-calling scenarios through both providers and
asserts identical normalized `ToolCall`/`LLMResponse` shapes; it is offline (recorded fixtures) with
a `router_live` variant. Live validation is account-gated (§8.5). The strict-JSON emulation of doc
03 §1 is retired from the design.

### 8.2 Snowflake data client

`core/data/snowflake_client.py` implements `DataClient` (including `run_frame_query`) over
`snowflake-connector-python` using the query builder's existing `snowflake` dialect. Session
strategy keyed by `DEPLOY_MODE` (the wfs pattern): `local` — password, PAT or key-pair from
settings; `spcs` — OAuth token read fresh from `/snowflake/session/token` on every connection,
account and host injected by the platform; `ec2` — Secrets Manager (later). SELECT/WITH-only guard
on every statement; `QUERY_TAG` set per request. `available_periods` and `list_dimension_values`
are served from live data. `DATA_BACKEND=snowflake` selects it. Every result cell is coerced to
`float`/`date` so Snowflake `Decimal`s never reach the math.

### 8.3 Row filter mechanism (Phase 15) and the record-type policy (§8.5 Q1)

The ontology gains an optional entity-level `row_filter` (a static SQL predicate string, certified
like a business rule), parsed explicitly by the loader and rendered by every builder inside
`_where_clause`, the way D16's row scope is threaded. No entity declares it yet. The **policy** —
whether `MARINE_SALES_PLANNING_V` metrics must exclude non-fuel record types or void rows — is
decided by the question-only Snowflake-side handoff (§8.5 Q1) and lands as one ontology line, so
Phase 15's goldens are pinned on synthetic data and the real-data definition is set before the
first real report.

### 8.4 Snowflake Postgres on SPCS (D39)

`DATABASE_URL` is injected from a Snowflake secret; the same Alembic migrations apply. Migrations
0009/0010 (worker role, app-role membership) and `assert_boot_privileges` already assume no
superuser, which holds on Snowflake Postgres as on RDS; the handoff verifies `SET ROLE` and the
`pgvector` extension there (Q5). Report artifacts live in Postgres (§5.2), so the SPCS service has
no object store; brief PDFs keep using MinIO/S3 where those exist and are outside this design.

### 8.5 SPCS service

`infra/spcs_spec.yaml`: containers `backend` and `worker` (same image, `python -m
poseidon.scripts.worker`); one public endpoint. Environment: `DEPLOY_MODE=spcs`,
`IDENTITY_MODE=spcs_ingress`, `LLM_PROFILE=cortex`, `DATA_BACKEND=snowflake`,
`MAIL_TRANSPORT=graph`, `APP_PUBLIC_URL=<ingress url>`, `MAIL_ALLOWED_DOMAINS`, `SPCS_SALES_USERS`,
`SPCS_REPORT_ADMINS`. External access integrations: Postgres egress (`<host>:5432`),
`graph.microsoft.com:443` + `login.microsoftonline.com:443`, and optionally Bedrock, Perplexity and
the Auth0 JWKS host — all on ports SPCS allows. The Postgres ingress rule must include the SPCS
egress IP ranges from `SYSTEM$GET_SNOWFLAKE_EGRESS_IP_RANGES()`, which rotate; the runbook carries a
dated reminder (Triton's lesson).

### 8.6 Snowflake-side build handoff

Under `docs/snowflake/`, mirroring Triton:

- `COCO_BUILD_SPEC.md` — §0 blocking questions (below), Part A grants and objects, Part B Snowflake
  Postgres provisioning (`CREATE POSTGRES INSTANCE …`, the secret, ingress rule), Part C SPCS
  deploy (image repo, compute pool, spec, EAIs), Part D validation gates.
- `BUILD_SIGNOFF.md` — the stage-gate table: the Snowflake-side agent implements, posts evidence
  and stops; the reviewer approves. Steps: **0 questions Q1–Q3 and Q8 resolved (dispatchable at the
  start of Phase 15; SQL-only, no app code)** · 1 Postgres provisioned + migrations + `SET ROLE` and
  `pgvector` verified · 2 grants + data client parity: the `snowflake_live`-marked certified-metric
  suite runs against `MARINE_SALES_PLANNING_V` and returns floats with a `snowflake`-sourced proof
  block · 3 Cortex live parity · 4 SPCS service READY + smoke · 5 **differential reconciliation:**
  one whole-book report and one office report for one month pair, KPIs diffed to the cent and the
  top-10 customer and port rows and driver labels diffed against mom-comparison's output for the
  same months, the diff table pasted as evidence; email to the owner only · 6 grounded chat cases
  live.
- `CORTEX_PROMPT.md` — the paste-ready prompt pointing at the files above and the hard constraints
  (no hand-built schema, migrations only; SELECT-only; never self-approve).

§0 blocking questions (answered on a Snowflake-connected session, recorded back into the spec and
into the ontology where they change semantics):

1. **Record type and status filters.** `MARINE_SALES_PLANNING_V` carries `RECORD_TYPE` and
   `POI_STATUS`. Three candidates: unfiltered (mom-comparison, and the certified metrics today);
   `RECORD_TYPE='FUEL_LINE' AND POI_STATUS<>'VOID'` (Triton's fuel grain); all record types with
   `POI_STATUS<>'VOID'`. Run, verbatim: `SELECT RECORD_TYPE, POI_STATUS, COUNT(*),
   COUNT(DISTINCT POI_ID), SUM("#_INQUIRIES"), SUM("#_FIXTURES"), SUM(FIXED_TONS), SUM(GROSS_PROFIT)
   FROM SANDBOX.MCA.MARINE_SALES_PLANNING_V GROUP BY 1,2 ORDER BY 1,2`, reconcile one recent month
   against mom-comparison's totals and against finance, and record the chosen predicate as the
   entity's `row_filter` (§8.3) or record that none applies.
2. Exact distinct values (and casing) of `CUSTOMER_TEAM_NAME` and `PRIMARY_SUPPLY_TEAM_OFFICE`.
3. Confirm `LIFT_ETA_DATE` month bucketing matches the mom-comparison report the team already uses.
4. Mail: the Entra app registration for Graph (`Mail.Send`, one service mailbox), the reply-to
   address, the allowed recipient domains, `APP_PUBLIC_URL`.
5. Snowflake Postgres: instance name, size, who holds `CREATE POSTGRES INSTANCE`; `SET ROLE` and
   `pgvector` availability.
6. EAI and network-rule names, the ingress-rule owner.
7. Cortex REST endpoint host and the Claude model ids available in the account's region.
8. The app's read role and warehouse; the Snowflake usernames for `SPCS_SALES_USERS` and
   `SPCS_REPORT_ADMINS`.

### 8.7 Adding a table

`docs/reference/adding-a-table.md`: the wfs lifecycle (investigate → propose → certify → compile)
run in the workspace, then vendor the certified entry into `ontology/ontology.yml`, update
`ontology/SOURCE.md`, add a synthetic profile stanza, let the loader contract test show the diff,
then reference the entity from skills. Report definitions can only slice certified dimension
columns, so a new office-like column becomes available to reports by certification alone.

## 9. Failure design

| Failure | Behavior |
|---|---|
| Data backend error during a run | run `error` with the problem detail after the attempts cap; Regenerate creates a new pending run |
| Frame roll-up disagrees with the independent total | run `error` ("reconciliation failed"), never a report with numbers that do not add up |
| Model narrative error or malformed output | template narrative, `grounded=false`, flag shown; run still `ok` |
| Grounding check fails twice | same as above with the count of unverified figures |
| Mail send fails | `report_send.status='failed'` with the error; resend allowed; run untouched |
| Office slice has no rows in either month | run `ok` with "no data" sections; Send refused with the reason |
| Duplicate generate | 409 from the partial unique index; the in-flight run is shown |
| Worker crash mid-run | the row lock releases; the next cycle retries; the attempts cap bounds it |
| Late-landing data | Regenerate; the new run supersedes; grounded conversations follow the current run (§7.5) |
| Worker down | runs stay `pending`; the panel shows "queued" with the created time |
| Recipient outside the allowed domains | 422 on write, 409 on send, with the address named |
| Chat: `report_lookup` with no report context | plain "open a report first" reply |

## 10. Testing

- **Math**: offline pytest for `core/analytics/mom.py` with mom-comparison's expectations
  (`_compute_kpis`, `_compute_top_movers`, `_compute_new_lost` cases transcribed), property checks
  (frame roll-up equals the independent total; margin is always a ratio; the driver table at the
  0.35/0.65 boundaries; the zero-base rule; share arithmetic).
- **Frame query**: byte-pinned SQL snapshots for both dialects, including time buckets; `pg`
  goldens against the seeded synthetic data.
- **Account context**: offline unit tests through a fake `DataClient` with a hand-built
  multi-office fixture (a customer with GP in two office values across A and B) asserting
  `share_of_book < 1`, opposite-sign office offsets, the empty-offsets case, the zero-book-GP share
  case; plus the synthetic generator gains a **multi-office overlay** (a configurable share of
  customers, always including the named ones, spread across two or three office values per row)
  so the `pg` goldens exercise a non-degenerate case.
- **Report tools**: goldens for one office slice and the whole book; proof-block snapshot; SVG
  snapshots; HTML and email template snapshots with an Outlook-safety lint (no `<script>`, no
  external stylesheet, no `position`/`flex`) and an escaping test (a customer named
  `<img src=x>` renders as text everywhere and WeasyPrint fetches nothing).
- **Grounding check**: unit tests for tokenization, rounding, exemptions, retry and fallback, and
  the mockup narrative as a must-pass fixture.
- **Jobs**: claim under lock, crash-release, idempotence, supersede, attempts cap, the partial
  unique index (`pg`).
- **Mail**: stub tests offline; domain allow-list; one `mail`-marked test that sends through Mailpit
  and reads it back.
- **API**: admin-versus-sales matrix for every route; slice-value validation; period validation;
  `PUT` version conflicts; the HTML/PDF byte routes.
- **Chat**: router-decision cases of §7.6 in stub mode and under `router_live`; parser tests for the
  scope hinter and the bare-year period; `metric_query` tests for scope stamping, multi-column and
  time-bucket group-by, ordering and share; `report_lookup` tests including the regenerate case.
- **Provider parity**: recorded-fixture parity test for Cortex over the Messages format; a
  `router_live` variant.
- **Frontend**: vitest for the `report` part renderer, the Reports store, sidebar section, panel,
  dialogs, split view, tag clearing, chip → send, the router fallback; MSW handlers for every new
  route.
- **Playwright smoke**: Phase 15: pick the Monthly report chip → office → month → the report renders
  in the thread → ask "why did GP drop for X" and see a comparison table. Phase 16: create a
  definition → generate → open the split view → send → open the email link from Mailpit → the
  split view opens after login.
- **Snowflake-side**: `snowflake_live` parity suite and the differential reconciliation of §8.6.

## 11. Docs and decision entries

- New `docs/architecture/09-reporting.md` (this design, condensed to the architecture voice).
- `00-overview.md`: D34–D45; `01-frontend.md`: routes, the history fallback, split view, the
  `report` part, tags; `02-backend-skills.md`: the `reporting` task, internal skills,
  `core/analytics`, the third flow; `03-llm-routing.md`: narrative on `synthesis` via forced tool
  call, Cortex over the Messages endpoint, state-block additions, the retirement of the
  strict-JSON emulation; `04-data-ontology.md`: `FrameQuerySpec`, time buckets, `row_filter`, the
  Snowflake client; `05-auth-identity.md`: ReportAdmin, D44, shared tables, the mail egress row;
  `06-observability.md`: `kind='report_run'`, `QUERY_TAG`; `07-infrastructure.md`: Snowflake
  Postgres, the SPCS spec without MinIO, EAIs and the port rule, Graph, Mailpit locally;
  `08-build-phases.md`: §12.
- `infra/runbooks/`: `deploy-spcs.md`, `snowflake-postgres.md`, `local.md` (Mailpit, live mail
  override).
- `docs/snowflake/*` (§8.6), `docs/reference/adding-a-table.md` (§8.7).
- `backend/.env.example`: every new variable, with a `--- SPCS ---` block.

## 12. Phases and gates

Phase 14 (EC2) is recorded as paused after its offline half (Tasks 1–6b complete; 7–9 pending).
The former Phases 15–16 (SPCS, Snowflake backend) fold into the new Phase 18; the optional
retrieval phase becomes 19. The question-only handoff (§8.6 step 0) is issued at the start of
Phase 15 and runs in parallel with it.

| Phase | Deliverables | Gate (something the owner clicks or runs) |
|---|---|---|
| **15 Report in chat** | `FrameQuerySpec` + time buckets + `row_filter` mechanism; `core/analytics`; the `reporting` task with all tools, the narrate subskill, grounding check, charts, HTML/PDF; `SkillResult.payload`, `ArtifactStore.put/get`, the `internal` flag; the third flow chip and office/month subject steps; the `report` message part and the `ui/report/` tree; `metric_query` scope stamping + comparison + multi-column/time-bucket group-by + ordering + share; `report_lookup` over the in-turn payload; the multi-office synthetic overlay; question-only handoff issued | In the browser on synthetic data: Monthly report chip → office → month → the report renders in the thread with the PDF; every table total equals its KPI tile; "why did GP drop for X", "where else does X lift fuel", "GP for X for 2025 by month, then by port" answer correctly; a run-log row per turn |
| **16 Reports screen + email** | definitions/runs/sends tables and API, the durable job and `worker.py`, ReportAdmin role, react-router with the history fallback and Auth0 returnTo, split-view shell, Reports sidebar, panel and admin dialogs, tag row, mailer (graph/smtp/stub) with the domain allow-list, Mailpit in compose, email templates, byte routes | New definition → generate → preview → send → open the email in Mailpit → click "Open in Poseidon" → login → the split view opens with the grounded conversation |
| **17 Grounded chat, complete** | slot seeding from runs, `report_lookup` following the current run, the scope hinter, the remaining router-decision cases live, the deterministic "slice it another way" clarification, feedback on report answers | All cases of §7.6 pass in stub mode; the widening and share cases pass under `router_live` on Bedrock; live in the browser: the mockup's Meridian turn |
| **18 Platform + handoff** | Cortex provider over the Messages endpoint + parity test, Snowflake data client, Snowflake Postgres wiring, SPCS spec + runbooks, `docs/snowflake/*` (Parts A–D), adding-a-table guide | Offline: parity and client tests green on recorded fixtures; the handoff docs reviewed. Account-gated: `BUILD_SIGNOFF.md` steps 1–6 approved on a Snowflake-connected session, including the differential reconciliation against mom-comparison |

## 13. References

- Review record: `docs/superpowers/specs/2026-09-04-monthly-performance-reports-review.md`
- Mockup: `docs/reference/mockups/2026-09-04-monthly-report-mockup.html`
- mom-comparison: `app/agent.py` (`_compute_kpis`, `_compute_top_movers`, `_compute_new_lost`,
  `_build_analysis_prompt`, `_build_followup_prompt`), `skills/analysis/mom-patterns.md`,
  `app/components/{kpi_cards,charts,narrative}.py`
- wfs workspace: `core/wfs_core/analytics.py` (parameterized port), `core/wfs_core/snowflake_client.py`
  (session by deploy mode), `ontology/ontology.yml`
- Triton: `docs/snowflake/{COCO_BUILD_SPEC,BUILD_SIGNOFF,CORTEX_PROMPT}.md`, `deploy/spec.yaml`,
  `backend/app/snowflake_conn.py`
- Poseidon: docs 00–08, `core/chat/orchestrator.py` (pass-through), `core/llm/prompts.py`
  (state block), `scripts/memory_worker.py` (claim pattern), `tasks/data_qa/skills/metric_query`,
  `api/app.py` (static serving), `core/artifacts.py`
