# Live Anthropic browser session: findings (2026-11-16)

**Model split:** Fable drove the browser, read logs and judged; no implementation code was
written. Fixes go to fresh chats (Haiku/Sonnet implement, Opus only for a risky diff).

**Owner calls (2026-11-16):** flows = default chat + Existing Customer, no Perplexity key
(research sections expected to degrade); spend cap about $5; screenshots per step; bug list
only, first fix PR from a fresh chat. Fable ran compose and the Next dev server itself. The
four chip clicks in the first conversation were Carlos clicking in the browser window.

**Setup that worked:** `main` @ 1e47a74. Scratch override
`.superpowers/sdd/2026-11-16-live-anthropic/compose.live-anthropic.yml` (db 5434, minio
9002/9003, backend `LLM_PROFILE=anthropic LLM_MODE=live`, key from the shell env). Backend
booted clean (migrations applied, data already seeded, six skills registered, research
transport `direct (llm_mode=live)`). Next dev on :3000 with `POSEIDON_INTERNAL_TOKEN` and
`DRIZZLE_DATABASE_URL` on 5434. Models confirmed live in the run log: `claude-sonnet-5-5`
router calls; `llm:synthesis` and `llm:utility` spans in the backend log. The rotated key is
now the Windows user-level `ANTHROPIC_API_KEY` (set by Carlos); the old value in any already
open shell is rejected with `401 authentication_error`.

**Spend (recorded + estimated):** 12 router calls on Sonnet 5.5, 71.5k input / 3.5k output
tokens, about $0.18. Plus unrecorded calls (see F5): two brief synthesis calls and one utility
(title) call, estimated under $0.10. Well under the $5 cap. Everything stopped at the end
(compose down without `-v`, Next dev server killed, port 3000 free).

**Screenshots:** `00-landing.png` through `10-post-brief-pivot.png` under
`C:\Users\carlo\AppData\Local\Temp\claude\C--Users-carlo-github-latest-poseidon\f121c6b6-fa6f-440c-bfad-22a992aefbb1\scratchpad\live\shots`

## Walk log

| Conv | Turn | Asked | Routed to | Rendered | Notes |
|------|------|-------|-----------|----------|-------|
| 1 | 1-4 | Monthly report chips: Customer office, Houston, June 2026 (Carlos clicking) | report flow, three clarify turns then `reporting.monthly_performance` | report card, proof, PDF link (Next route handler, download works), three suggested-question chips | Narrative fell back to the template: the stale key was rejected (401). Not re-run after the key fix. |
| 2 | 1 | "Show me GP by customer and month for Houston in Q2 2026, top 5 customers" | `data_qa.metric_query` group_by [MONTH, CUST_NM] top 5, port = Houston | 15-row table, proof, grounded narrative | Model read Houston as a port (LOC_NM), said so. 5.9 s, two router calls. |
| 2 | 2 | "Where does Pacific Tankers lift fuel?" | metric_query by port, period carried (Q2 2026), customer carried | 4-port table (volume, GP), narrative | D62 carry works. |
| 2 | 3 | "how are we doing?" | no tool; prose clarify with four readings, names the carried period | text | Good clarify. |
| 2 | 4 | "Overall book performance, compared with Q1 2026" | metric_query, 7 metrics, compare_period | metric grid Q2 vs Q1, narrative | Grid period labels are raw ISO half-open windows; WIN RATE shows 0.56. |
| 2 | 5 | "how did we do last month" | no tool; clarify asking which month | text | Did not start the report (see F6). |
| 2 | 6 | "June 2026, whole book" | metric_query single period | 7-row METRIC/VALUE table, narrative | Promised prior-month comparison in turn 5 not delivered; raw integers in the table. |
| 3 | 1 | Existing customer chip | brief flow picker | "Which customer is this brief for?" (text, no chips) | |
| 3 | 2 | "Pacific Tankers" | `customer_insight.existing_customer_brief` | KPI grid (prior year vs YTD), ports table, Context, Narrative, Strategy, PDF link | 13.5 s wall. Three Perplexity spans fail in about 100 ms each (no key); narrative says so. Two synthesis calls (12.2 s, 6.4 s). |
| 3 | 3 | "Which of those ports grew the most this year versus last year?" | metric_query by port with compare, customer carried | two compare tables (GP, volume) with Driver column, narrative | Post-brief pivot works. |

## Findings, ranked by what Carlos would notice first

### F1. Brief PDF link is unreachable from the browser (bug, local dev)
- The brief's artifact part links to `http://minio:9000/poseidon-artifacts/...`, a presigned
  GET from `core/artifacts.py` built against `S3_ENDPOINT_URL=http://minio:9000`, the Docker
  network name. A browser on the host cannot resolve `minio`; on this machine the name
  resolves to another project's MinIO on 9000, which answers 403. Either way the click fails.
- The monthly report PDF works because D65 serves report bytes through a Next route handler.
- Options: (a) a public presign endpoint setting for local (`S3_PUBLIC_ENDPOINT_URL`, a second
  boto client used only for presigning); (b) serve brief PDFs the way reports are served
  (D65 pattern). (a) is small and local-only; (b) is consistent but touches storage.

### F2. Conversations entered through a flow chip stay titled "New chat" (bug, cosmetic daily)
- Conv 1 (report flow) and conv 3 (brief flow) still read "New chat" in the sidebar after the
  report and the brief. Conv 2 (default chat) was titled by the utility call on turn 1
  ("Houston Top 5 Customers by GP, Q2 2026"). Titling runs only on the router path; clarify
  turns and flow dispatches set none, and the finish frames carry `"title": null`. The sidebar
  history shows every past report chat as "New chat".

### F3. Number and period formatting is inconsistent across parts (cosmetic)
- Metric grid: period labels are raw half-open ISO windows (`2026-04-01 - 2026-07-01`), which
  reads as if July is included. Narratives say "Q2 2026"; the grid should too.
- Metric grid: WIN RATE renders `0.56`; the report renders `47.0%`.
- Table part: integers print raw (`16292`, `3792239`) while the grid prints `11,156,213`.
- Brief ports table has no period caption; the model hedged ("may not map to the same period").

### F4. Narrative label says "withheld" when the model call failed (cosmetic, misleading)
- Report card shows "AI narrative withheld" for a provider error; the proof line says
  `narrative: model narrative failed (provider error); template used`. Source:
  `web/src/ui/report/Narrative.tsx` line 9, `withheld = !grounded && llm_md === null`, true
  for both outcomes. The payload `flags` carry the distinction.
- Related: a failed synthesis call is invisible except in the proof line. `narrate._call`
  swallows the exception, no `llm_calls` row is written, no backend log line is emitted.

### F5. Run log records only router calls (observability, spend under-reported)
- `llm_calls` has 12 rows today, all `role=router`. The backend log shows 15 spans: the two
  brief synthesis calls and the utility title call are not recorded, and `turn_run` carries
  `input_tokens=0` for the brief turn. Spend reporting from the run log is therefore partial.
- `llm_calls.latency_ms` is NULL on every Anthropic-provider row (the Messages API path does
  not fill it; stub rows do).

### F6. Routing observations against the dev-router divergence list (judgment calls)
- "how did we do last month" cannot start the report: `reporting.monthly_performance` is
  internal (D70), so the router has no tool for it. The live model asked which month, citing
  the two carried quarters. Only the chip or the entry phrase starts a report. Product call:
  is a natural-language entry wanted, or is chip-only fine for now?
- "for Houston" was read as a port, not the customer office. Houston is both a port
  (LOC_NM) and a customer office in the synthetic data. The parser's "in/for" port cue (D60)
  resolves it before the model sees it. Product call: should an office name win when a
  report context is open, or when the word "office" is present?
- The live router never mis-dispatched metric_query on a research- or brief-shaped question
  (the dev router's parked over-trigger). Clarifies were prose, no chips, which is fine.
- Narratives sometimes mention internals: "The results come from the synthetic backend, as
  noted in the proof line." A sales reader should not see that sentence.
- The brief narrative said "the data does not tell us how much of the year has elapsed":
  today's date is not in the synthesis prompt, so YTD pacing is guessed.

### Not bugs
- Research sections empty in the brief: expected without `PERPLEXITY_API_KEY`; each call
  degraded in under a second and the narrative explained it.
- No chips after the brief: the brief emits no chips part; the report emits three.
  Consistency question only.

## Open judgment calls for Carlos (recommendation first)
1. First fix PR: F2 titles for chip-entered flows (small, Sonnet). F1 needs the (a)/(b) call.
2. F1 approach: recommend (a) `S3_PUBLIC_ENDPOINT_URL` for local presigning; (b) later if
   briefs should be served like reports.
3. F6 office-vs-port and natural-language report entry: product decisions, no code until ruled.
4. F5 run-log coverage: one PR to record synthesis/utility calls and latency; medium size.

## Handoff prompt for the first fix PR (paste into a fresh chat)

> You are Fable, supervising Poseidon in C:\Users\carlo\github\latest_poseidon (design,
> dispatch, review; never implementation code). Read `CLAUDE.md` (gotchas), the decision log
> in `docs/architecture/00-overview.md` through D82, and
> `docs/superpowers/plans/2026-11-16-live-anthropic-findings.md`. State: `main` @ 1e47a74,
> nothing deployed, live Anthropic session done on 2026-11-16 with findings F1-F6.
> Job: fix F2 (conversations entered through a flow chip stay titled "New chat"). Find where
> the utility title call runs on the router path (`llm:utility` span, `title` on the finish
> frame) and make clarify turns and flow dispatches title the conversation on its first
> substantive turn (the report turn, the brief turn), never on a clarify. Write a plan with
> model split (Sonnet implements, Fable reviews), present it and STOP for Carlos's go. Then
> subagent-driven development: TDD, implementer runs the pg suite for chat output
> (`cd backend && .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg`, compose
> db up on 5434) and the offline suite with the full marker exclusion and
> `env -u PERPLEXITY_API_KEY`; reviewer never re-runs the suite. Branch
> `git checkout -b fix/flow-chat-titles main`; commit via scratch file and `git commit -F`;
> `gh pr create` only on Carlos's word; he merges in the GitHub UI. Never Bedrock; never bare
> `python`; never pipe pytest; never `ruff format`.
