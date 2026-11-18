# Phase 17 PR A1 — execution handoff (written 2026-10-14, plan approved by Carlos)

Repo state when this was written: `main` at `8980f33`, working tree clean apart from untracked
handoff and plan files under `docs/superpowers/plans/`. The plan is untracked; commit it as the
first commit on the branch so the executing chat and the reviewers read the same text.

## Paste this into a fresh chat

> You are Fable, supervising the execution of Phase 17 PR A1 (the report engine) of Poseidon in
> C:\Users\carlo\github\latest_poseidon. The plan is approved and final:
> `docs/superpowers/plans/2026-10-12-phase-17-prA1-report-engine.md` (ten tasks, self-reviewed,
> rulings 1-15). Read it in full before anything else, then read CLAUDE.md (repo rules, gotchas,
> frozen surfaces) and `.claude/CLAUDE.md`'s "Model delegation" and "Git & PRs" sections.
>
> Use superpowers:subagent-driven-development. Execution model (owner rule): you never write
> implementation code; one implementer per task, a fresh reviewer per diff, fix rounds until
> clean, then one whole-branch review on Opus after Task 10. Model split from the plan header:
> Sonnet implements Tasks 1, 2, 4, 5, 6, 7, 10; Opus implements Tasks 3, 8, 9; Opus reviews
> Tasks 1, 3, 8, 9 and the whole branch; Sonnet reviews the rest. The implementer runs the tests
> and its report is the evidence (failing-first output shown, hand derivations where the plan
> asks); the reviewer is forbidden from re-running the suite and judges the diff against the
> implementer's claims. Brief reviewers to prove guards by breaking them (revert, watch the test
> fail, restore) and to re-derive arithmetic themselves.
>
> First actions, in order: (1) `git rev-parse --abbrev-ref HEAD` and `git status --short`; stop
> and report if the tree has uncommitted tracked changes. (2) `git checkout -b
> phase-17-report-engine main` (explicitly from main; expect main at 8980f33, report if not).
> (3) Commit the plan file alone: `docs(plan): Phase 17 PR A1 report engine plan` with the
> Co-Authored-By trailer. (4) `mkdir -p .superpowers/sdd/2026-10-12-phase-17-prA1-report-engine`
> and start `progress.md` and `morning-review.md` there (self-ignored). (5) Confirm the compose
> `db` service is up on port 5434 (`docker compose -f infra/docker-compose.yml ps`); Tasks 2, 5,
> 9 and 10 need it. Then dispatch Task 1.
>
> Every implementer dispatch carries: the task text verbatim (not a summary), the Global
> Constraints block, the branch name, "run `git rev-parse --abbrev-ref HEAD` before committing
> and `git log --oneline main..HEAD` after; stop and report rather than improvise if either
> surprises you", the exact pytest commands from the task (never piped, never `-q`, always
> `.venv/Scripts/python.exe`), and "do not run builds while another agent shares the tree".
> Never regenerate `contracts/turns`; if `tests/contracts` shows a diff, stop and report.
>
> Settled, do not reopen: the rulings in the plan (1-15) and the owner decisions in its Spec
> paragraph (A1/A2 split, chips backfill to three, month to date named explicitly, no lead text
> part). PR A2 wires the flow, renderer, lookup, routes and overlay; nothing in A1 touches
> `core/chat`, the renderer registry or the contracts corpus.
>
> When Task 10's final gates are green and the whole-branch review is clean: write the PR
> description (what changed, why, how verified, with the exact gate figures), the morning-review
> list (rulings, deviations, deferred minors), and a handoff prompt for the PR A2 planning chat
> at the bottom of this file; then STOP. Do not run `gh pr create` and do not push unless Carlos
> says so in that chat. If Carlos has said "keep running until you're done", push the branch and
> open the PR with `gh pr create`, still never merging.
>
> Known host facts: WeasyPrint does not import on this Windows host (no Pango), so `pdf`-marked
> tests skip and the skill records `PDF not rendered: OSError`; the synthetic generator gives
> every customer one office, so the pg goldens pin share 100% and empty offsets by design.

## What the next chat should leave behind

- Branch `phase-17-report-engine` with eleven commits (plan + ten tasks), each ending in the
  Co-Authored-By trailer.
- `.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/progress.md`, `morning-review.md`,
  the per-task red/green logs, the three demo outputs.
- A PR description and the PR A2 handoff prompt appended below this line.

---

# Execution result (written 2026-10-14 by the executing chat)

Branch `phase-17-report-engine`, 18 commits above `main` (8980f33), head `d3022f9`. Not pushed, no PR opened. Ledger and morning-review list: `.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/` (`progress.md`, `morning-review.md`, per-task reports, red/green logs, `demo/` outputs).

## PR description (paste into `gh pr create` / the GitHub UI)

**Title:** Phase 17 PR A1: the monthly performance report engine

**What changed**
- `core/data`: `FrameQuerySpec`, `build_frame_query`, `build_months_query` (byte-pinned SQL, both dialects, D16 scope last); `DataClient` gains `list_months`, `run_frame_query`, `snapshot()` (one REPEATABLE READ read-only connection, D54).
- `core/analytics/mom.py`: the month-over-month math in one home (KPIs, outer-join merge, driver rule, movers, new/lost, ranked tables with the Other row and a footer built by the same `fsum` helper as the KPI, display formatting).
- Periods: `resolve_periods` (complete month or month to date, D64), `label_day_range`, `label_month_to_date`; the period parser reuses `period_labels` (PR 0 carry-forward).
- `report_run`: Drizzle migration `0003` (shared org document, no RLS, `GRANT SELECT, INSERT` to `poseidon_app`, `ck_conversations_mode` widened to `report`), Python `ReportStore` over `rls_transaction`.
- Rendering: `report.html.j2` (autoescaped, Outlook-safe, tables only), `render_html`, `render_pdf` with a fetch-refusing WeasyPrint; escape-then-markdown with link/image patterns disabled.
- LLM: `backend/prompts/` tree with `PromptTree` (D46, first step), `tool_choice` forced tool call through `RoleClient`/Bedrock, recorded by the stub.
- Narrative: template narrative from display strings; entity-scoped grounding check (D67) with one retry; suggested questions backfilled to three, entity- and figure-checked.
- The skill `reporting.monthly_performance`, registered `internal` (hidden from the router prompt, tool schemas, `/api/skills` and the unknown-skill error text; `dispatch` sees it; D70). One snapshot, six certified queries, every figure computed once, HTML+PDF, run stored, `report` part returned. No `report_run` row on any failure.
- `demo_report.py` runs it against the compose database in stub mode; docs 02/03/04/05/07, `00-overview` (D46/D67 refinements, D70), CLAUDE.md figures.

**Why**: PR A1 of the Phase 17 report-in-chat feature (spec `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md`); PR A2 wires the flow, renderer, lookup, routes and the synthetic multi-office overlay. Nothing here touches `core/chat`, the renderer registry or `contracts/turns`.

**How verified** (all from `backend/` unless noted; outputs in the ledger directory)
- Offline Python suite: 2178 passed, 14 skipped, 126 deselected.
- pg suite (serial, compose db on 5434, Drizzle 0003 applied): 352 passed, 1 skipped.
- `tests/contracts`: 7 passed; `git status --short contracts/` empty (nothing regenerated).
- `ruff check .`: clean. Web (`cd web`): `npm test` 151 passed, 13 skipped; `npx tsc --noEmit` clean.
- Demo: Rotterdam (customer), Singapore (supplier), whole book, June 2026; exit 0; every `footer == tile: yes`; HTML written (no Pango on the host, `PDF not rendered: OSError` flagged as designed).
- Every task: implementer ran red-then-green, guards proven by reverting; a fresh reviewer per diff re-derived the arithmetic by hand (Task 3 fixture, the 45-token mockup count, the Task 9 fake rows, the Rotterdam demo tiles against the rendered HTML); one Opus whole-branch review, one fix wave, one scoped re-review.

**Known and deferred**: see `morning-review.md` (YOUR CALL items 1-6; A2 carry-forwards). One pre-existing `router_live` test failed on live model output when the plan's Task 7 command ran without the marker exclusion (one paid call).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

## Paste this into the PR A2 planning chat

> You are Fable, planning Phase 17 PR A2 (report in chat) of Poseidon in C:\Users\carlo\github\latest_poseidon. PR A1 (the report engine) MERGED as PR #8 (https://github.com/sal1391/latest_poseidon/pull/8) on 2026-10-16; main is at merge commit `1b2ae40`. Branch `phase-17-report-in-chat` from main explicitly (`git checkout -b phase-17-report-in-chat main`), never a bare checkout; confirm with `git rev-parse --abbrev-ref HEAD` before any commit. Read first: the spec `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` (sections 6.1-6.5, 6.7-6.9, 7.2, the contracts corpus report turn, the `report` renderer, docs 01 and 08, `conversation_id` on the run row); the A1 plan `docs/superpowers/plans/2026-10-12-phase-17-prA1-report-engine.md` (its Interfaces blocks are the contracts A2 consumes); `docs/superpowers/plans/2026-10-14-phase-17-prA1-execution-handoff.md` (bottom: this result); `.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/morning-review.md` (rulings, YOUR CALL items, A2 carry-forwards); CLAUDE.md; `.claude/CLAUDE.md` "Model delegation" and "Git & PRs".
>
> Use superpowers:brainstorming, then superpowers:writing-plans; present the plan and STOP for Carlos's go. Owner decisions already made: chips backfill to three; month to date is named explicitly; the report turn has no lead text part; the PDF link is `/api/reports/<run_id>/pdf` (A1 returns it; A2 serves it). Carlos ruled on the six A1 YOUR CALL items on 2026-10-16: (1) A2 renders the model `insights` array as an `## Insights` section and drops the Insights section from the narrate prompt (bump the prompt version); (2) the new/lost overflow keeps "; and others" with no count; (3) the office-run proof line keeps "whole book"; (4) zero volume keeps `0.00` margin and `-100.0%` lost GP; (5) no cleanup path for `report_run` rows in dev; (6) the live router drift is parked for PR C.
>
> A1 facts A2 builds on: skill id `reporting.monthly_performance` (internal; dispatch via `SkillRegistry.dispatch` with `SkillContext.report_store` and `prompt_tree` set); `Args(perspective, office_value, month, window_kind, basis)`; the `report` part is the payload v1 dict (`tests/payload_fixture.minimal_payload()` is the contract in code; `periods.*.end` is half-open; money strings carry no currency sign; `narrative.flags` carries `PDF not rendered: <Type>` when `pdf` is null); `ReportStore.get_run/get_bytes(user_sub, run_id, "html"|"pdf")`; `report_run` has `conversation_id` (A1 writes None); proof lines are ASCII strings; `narrate` keeps model insights in the tool contract but does not render them (A2 renders them, ruling 1 above).
>
> A2 carry-forwards from the A1 reviews (plan them explicitly): the agent-loop guard so a stub/dev router can never dispatch an internal skill and a report result never reaches `loop._part_content`'s JSON dump; Content-Disposition filename sanitising on the PDF route (the artifact name is built from the raw office value); `get_bytes` kind validation (bare KeyError today); the stored HTML never says why there is no PDF; the renderer must reuse the escape-first, no-links markdown rule; the GL months query applies no dual-purpose exclusion (A1 is sales-only); visible-text polish ("-0", "+0.0%", "Other (1 customers)"); `ports_rest_of_book` can list a flat port; a template drift test against `KPI_METRICS`; the template's header label is sniffed from the table title; the synthetic multi-office overlay must re-pin the pg goldens' share/offsets assertions (today every customer has one office); the `report_run` rows accumulating in the dev DB (no DELETE grant). Plan-level: the plan's own pytest commands must carry `env -u PERPLEXITY_API_KEY` and the full marker exclusion on every path-targeted run (A1's Task 7 command fired one paid Bedrock call).
