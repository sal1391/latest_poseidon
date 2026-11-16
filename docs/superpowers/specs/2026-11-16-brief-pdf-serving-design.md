# Brief PDFs served like reports — design (F1, option b)

**Status:** draft for Carlos's approval, 2026-11-16. Supersedes the MinIO/presigned-URL path for
brief PDFs; refines D65 (report bytes served by Next.js route handlers) to cover briefs.
**Spec origin:** finding F1 in `docs/superpowers/plans/2026-11-16-live-anthropic-findings.md`.
**Model split:** Fable designs and reviews; Sonnet implements (multi-file Python + Drizzle + Next
route, with pg tests); Haiku for nothing here.

## Intent

A brief's PDF link must open in the browser, in every habitat, for as long as the chat message
exists. Today the two brief skills upload the PDF to MinIO and put a one-hour presigned URL on the
Docker-internal host `minio:9000` into the artifact part. The browser cannot resolve that host, and
the link dies after an hour anyway, so every brief PDF link in history is dead. Reports already do
this right (D65): bytes in Postgres, served by a Next.js route behind the Sales-role check, linked
by a relative path. Carlos's call: **one download path for every PDF the app produces.** Briefs
move onto the report pattern.

## Goals

- A brief turn (existing customer or new prospect) stores its rendered HTML and PDF in Postgres
  and emits an artifact part whose `url` is `/api/briefs/{runId}/pdf`.
- The Next app serves `/api/briefs/{runId}/pdf` and `/api/briefs/{runId}/html` exactly the way
  `/api/reports/{runId}/{pdf,html}` are served: Sales-role gate, bytes read through the RLS-aware
  client, `content-disposition: attachment` with a sanitised filename, `private, no-store`.
- Any `Poseidon:Sales` user can download any brief (same rule as reports, D69). Carlos decided
  this on 2026-11-16.
- Store the brief's markdown body alongside the bytes (Carlos, 2026-11-16) so a later phase can
  list or re-render briefs without parsing PDFs.
- A WeasyPrint failure (host without Pango) degrades the way the report does: the row is still
  written, the proof carries `PDF not rendered: <ExceptionName>`, no artifact part is emitted, the
  turn stays `ok`. Today that path raises `OSError` out of the skill.
- Old chat messages carrying MinIO URLs are left as they are (they were already dead).

## Non-goals

- Removing MinIO, `ArtifactStore`, the `s3_*` settings, the `minio` pytest marker and doc 07's
  MinIO paragraphs. Carlos chose a **separate follow-up PR** for that removal. This PR leaves
  `SkillContext.artifacts` and `app.state.artifact_store` in place and simply stops using them
  from the brief skills. Nothing else uses them in the production path.
- A UI for listing or reopening briefs. The stored markdown enables it later; nothing renders it
  now.
- Changing what the brief contains, its parts, or its proof lines beyond the artifact lines named
  below.
- Any change to `report_run` or the report routes except extracting shared serving code.

## Anti-goals

- Proxying bytes through the Python backend. The Next route reads Postgres directly, as reports do.
- A second visibility rule. Briefs are shared org documents like reports; no row policy.
- Breaking the SSE part contract. The artifact part keeps `{name, url, mime}`; only the `url`
  value changes shape (relative path, not presigned URL). The `contracts/turns` corpus must not
  need regeneration; if it does, stop and report.

## Constraints (repo rules that bind this work)

- Drizzle owns the schema (D58): one new migration `0004_brief_run` generated with
  `drizzle-kit generate` from `web/src/db/schema.ts`, then hand-appended with the grant, the way
  `0003_report_run.sql` was. Alembic is frozen; never touched.
- Python computes, the LLM only routes. No LLM involvement anywhere in this change.
- Folder law: skill tools stay co-located; the new store lives beside the report store
  (`backend/poseidon/briefs/store.py`, mirroring `backend/poseidon/reports/store.py`).
- This table is app state, not a certified entity, so the ontology and the five-edit table
  onboarding are untouched.
- Tests ship in the same PR. pg-marked tests run serially against the compose `db`.
- The decision log in `docs/architecture/00-overview.md` gets a new row (D83) recording this
  decision and the deferred MinIO removal, and D65's row gains "(extended to briefs by D83)".

## The design

### 1. Table `brief_run` (Drizzle migration 0004)

Shaped after `report_run`, shared org document, no RLS, `GRANT SELECT, INSERT ON brief_run TO
poseidon_app`.

| column | type | notes |
|---|---|---|
| `id` | uuid PK | uuid7 minted by the skill |
| `kind` | text NOT NULL | `existing_customer` or `new_prospect`; CHECK constraint |
| `subject` | text NOT NULL | the customer or prospect name as the skill received it |
| `title` | text NOT NULL | `"{subject} Brief"` / `"{subject} Prospect Brief"`, the H1 of the PDF |
| `anchor_date` | date NOT NULL | the brief's anchor (`anchor` in the existing-customer skill, `_today()` in the prospect skill) |
| `status` | text NOT NULL | `ok` or `error`; CHECK. Only `ok` is written this phase; the column exists for parity with `report_run` |
| `markdown` | text NOT NULL | the markdown body `_render_markdown` produced |
| `html` | bytea NULL | rendered document, UTF-8 |
| `pdf` | bytea NULL | NULL when WeasyPrint failed |
| `pdf_error` | text NULL | exception class name when `pdf` is NULL |
| `created_by` | text NOT NULL | user sub |
| `conversation_id` | uuid NULL | from `ctx.conversation_id` |
| `generated_at` | timestamptz NOT NULL | |
| `created_at` | timestamptz NOT NULL default now() | |

No `payload` jsonb: the brief's parts are already persisted on the assistant message; the markdown
is the durable content. No `superseded_by`/`definition_id`: briefs have no definitions.

### 2. Python: `BriefStore`

`backend/poseidon/briefs/__init__.py`, `backend/poseidon/briefs/store.py`. Mirror `ReportStore`:
`NewBrief` frozen dataclass, `insert_brief(user_sub, brief)`, `get_bytes(user_sub, brief_id,
kind)` for the Python-side tests, all inside `rls_transaction` with `SET LOCAL ROLE` like every
other store. Plain SQL `text(...)`.

Wired in `api/app.py` next to `report_store`: `app.state.brief_store = BriefStore(engine,
app_role=settings.database_app_role)`. Threaded through `execute_turn` and both `SkillContext`
constructions as a new additive field `brief_store: object | None = None`, exactly the way
`report_store` was added in Phase 17 A2: `orchestrator.py` threads it, `live_chat.py` and
`internal.py` pass `getattr(app_state, "brief_store", None)` beside their `report_store` lines.
`dev_runner.py` passes neither store today and gains `brief_store` only (it builds a
`SkillContext` directly; pass `getattr(request.app.state, "brief_store", None)`).

`created_by` follows the report skill's rule verbatim: `ctx.user.sub` when `ctx.user` is set,
else the anonymous sub `"dev|anonymous"` (the dev runner and the offline tests build contexts with
no user). That constant is today duplicated as `ANONYMOUS` in two reporting skills; this change
hoists it, together with `safe_filename`, into a new small shared module
`backend/poseidon/core/documents.py` (`ANONYMOUS_SUB`, `safe_filename`) and makes the three
reporting call sites import from there. Behaviour is unchanged; the report tests pin behaviour,
not location. This is the one edit to reporting files this PR makes.

`conversation_id` mirrors `ReportStore`: the orchestrator passes the real conversation id on live
chat, `/internal` and the dev runner pass `None`; the column is nullable and the store parses
with `_parse_uuid`, storing `NULL` for anything that is not a UUID.

### 3. Python: the brief skills

`build_brief_pdf` (tool) changes contract: it no longer takes a store or uploads. It becomes
`render_brief(title, markdown_body) -> RenderedBrief(html: str, pdf: bytes | None, pdf_error:
str | None, page_count: int | None)`, catching the WeasyPrint import/render exception and
recording its class name instead of raising, the way the report skill's step 7 does. `slug` and
`_render_html` stay; `slug` is no longer used for a key. The module may keep its file name; the
function is renamed because its output is bytes, not an upload.

Both skills replace the `if ctx.artifacts is not None:` block with:

1. `rendered = render_brief(title, markdown_body)`.
2. If `ctx.brief_store is not None`: mint `brief_id = uuid7()`, insert the `NewBrief` row (html
   always, pdf when rendered), and if `rendered.pdf is not None` append
   `ArtifactRef(name=<filename>, url=f"/api/briefs/{brief_id}/pdf", mime="application/pdf")`.
3. Proof lines, in this order, replacing today's `Artifact: {key}` / `Pages:` / `Bytes:` trio:
   `Brief: {brief_id}`, then `Pages: {n}` and `Bytes: {len}` when the PDF rendered, else
   `PDF not rendered: {pdf_error}`.
4. If `ctx.brief_store is None`: proof line `Brief: skipped (no brief store configured)` and no
   artifact, replacing `_ARTIFACT_SKIP_PROOF`. `ctx.artifacts` is no longer consulted.

Filename: `safe_filename(title) + ".pdf"` using the one sanitiser D74 already mandates on both
sides (`core/documents.safe_filename` in Python after the hoist above, `sanitizeFilename` in
`web/src/lib/report-files.ts`), so the artifact part's `name` and the route's
`content-disposition` filename agree. Checked 2026-11-16: the two already agree. Both replace
every character outside `[A-Za-z0-9 ._-]` with `_` first, so only plain spaces reach the collapse
step, and Python's `\s+` and the TS `/ {2,}/` then produce the same string; both trim and both fall
back to `"report"`. The builder adds one parity test on each side using the same fixture strings
(a title with a double space, one with punctuation, one with a double quote) so a later drift is
caught.

### 4. Web: routes

`web/src/app/api/briefs/[runId]/pdf/route.ts` and `.../html/route.ts`, `force-dynamic`, calling a
generalised server: lift `serveReport` into `web/src/lib/serve-document.ts` (or generalise
`serve.ts` in place) so both reports and briefs call `serveDocument(request, params, kind,
readFn, notFoundTitle)`. `web/src/lib/briefs.ts` gets `readBriefFile(sub, runId, kind)` mirroring
`readReportFile`, selecting `title` plus the bytes and returning `fileName =
sanitizeFilename(title) + "." + kind`. The report routes keep their exact responses (their
route test pins them).

`web/src/db/schema.ts` gains `briefRun` with the columns above; `web/src/db/migrations.test.ts`
gains `0004_brief_run.sql` in its expected list and a grant check like the one for 0003.

### 5. Web: renderer

`ArtifactPart.tsx` is unchanged: a relative `href` with `download={name}` already works for
reports. Its comment mentions presigned GETs; update the comment only.

### 6. Decision log

Add D83 to `docs/architecture/00-overview.md`: "Brief HTML/PDF are stored in `brief_run`
(Drizzle 0004, shared org document, D69 visibility) and served by the Next.js routes
`/api/briefs/[runId]/{pdf,html}`; the artifact part links the relative path. `ArtifactStore`/
MinIO have no production caller and are removed in a follow-up PR." Mark D65 "(extended to briefs
by D83)".

## Tests the builder must produce (minimum)

- Python offline (`test_brief_skills.py`, both skills): with a fake `brief_store`, the result
  carries one artifact whose `url` is `/api/briefs/{id}/pdf`, `name` is the sanitised title plus
  `.pdf`, and the proof carries `Brief: {id}`; the fake store received html, markdown and the
  same id. With `brief_store=None`: no artifact, proof `Brief: skipped (no brief store
  configured)`. With a `render_brief` that reports `pdf=None, pdf_error="OSError"`: row written
  with `pdf=None`, no artifact, proof `PDF not rendered: OSError`, result `ok=True`.
- Python pg (`test_brief_store_pg.py` or inside an existing pg file): insert then `get_bytes`
  round trip for pdf and html; a second user can read the first user's brief (shared document);
  malformed and unknown ids return `None`.
- Python pg e2e (`test_chat_e2e_scripted.py` brief test and `test_history_cutover.py`'s F2
  brief test). The pg suite runs on the host, where WeasyPrint cannot load, and in the container,
  where it can; the e2e must assert the same thing in both. Rule: the e2e monkeypatches
  `render_brief` at its use site in the skill module to return a fixed `RenderedBrief` (a short
  `b"%PDF-1.4 ..."` body, `page_count=1`), so the wire deterministically carries the artifact part
  with the relative URL `/api/briefs/{id}/pdf` and the proof lines `Brief: {id}`, `Pages: 1`,
  `Bytes: {n}` on every host; the proof-line index assertions (`test_chat_e2e_scripted.py:835`
  and the tuple near `:1052`) are updated to the new lines. The host-only `OSError` path is pinned
  separately by the offline skill test with `pdf_error="OSError"`. `_drop_artifact_store` is no
  longer needed by the brief tests and is removed from those call sites (the report pg test may
  keep it until the MinIO removal PR). The F2 title test in `test_history_cutover.py` only asserts
  titles; it drops its `artifact_store = None` line and needs no renderer patch, because either
  render outcome still ends the turn `ok`.
- `pdf`-marked (container only): `render_brief` produces `%PDF` magic and a positive page count.
  The existing `build_brief_pdf` tests in `existing_customer_brief/tests/test_tools.py` are
  ported: the `slug` tests stay as they are, the `pdf`-marked render half moves to `render_brief`,
  and the `minio`-marked upload half is deleted (there is no upload). `_artifact_store()` in that
  file goes with it.
- Web (`vitest`): route tests for `/api/briefs/[runId]/{pdf,html}` mirroring the report route
  test (403 without role, 404 unknown, 200 with headers and filename); `readBriefFile` db-backed
  test skipped without `DRIZZLE_DATABASE_URL` like its report sibling; migrations test updated;
  `npx tsc --noEmit` clean.
- Contracts corpus unchanged (`git status --short contracts/` empty).

## Left to the builder

- Exact column order and the Drizzle `customType` reuse for bytea (already defined once in
  `schema.ts`; reuse it).
- Whether the shared serving helper lives in `web/src/lib/` or stays under `app/api/reports/`
  with the brief routes importing across; either is acceptable, the report route tests must not
  change.
- How `render_brief` surfaces the page count when WeasyPrint rendered (today `len(document.pages)`).
- The fake store shape in the offline skill tests.
- The `anchor_date` for the prospect brief: `_today()` as the skill already computes it.

## Calls Fable made (check these while reading)

- `brief_run` is its own table, not a generalised `document` table shared with reports. The two
  have different metadata and the report table is already shipped; a union table would force a
  migration of `report_run` for no feature.
- `markdown` is `text NOT NULL`, not jsonb parts: the parts already live on the persisted message.
- `pdf_error` column rather than a flag inside a payload: briefs have no payload to flag.
- The proof line prefix changes from `Artifact:` to `Brief:`. The old lines named an object key
  that no longer exists; a reader of the proof should see the row id they can look up.
- From the builder check (2026-11-16), settled by Fable without changing what gets built:
  - e2e determinism across host and container: patch `render_brief` in the e2e (above), not a
    branch on WeasyPrint presence and not a `brief_store=None` run.
  - `created_by` without a user: the report skill's anonymous sub, hoisted with `safe_filename`
    into `core/documents.py` rather than duplicated a third time.
  - `conversation_id`: nullable, parsed like the report store.
  - Old `build_brief_pdf` tests: ported, upload half deleted.
  - Sanitiser parity: already holds; pinned by a parity test on each side.

---

## Handoff prompt for the next chat (paste into a fresh chat)

> You are Fable, supervising Poseidon in C:\Users\carlo\github\latest_poseidon (design, dispatch,
> review; never implementation code). Read `CLAUDE.md` (gotchas), then this design document
> `docs/superpowers/specs/2026-11-16-brief-pdf-serving-design.md` in full, and finding F1 in
> `docs/superpowers/plans/2026-11-16-live-anthropic-findings.md`. State: main @ 92b091c (F2 merged
> as PR #16 on 2026-11-16), nothing deployed; this design is written, builder-checked, and
> **awaiting Carlos's approval** (he has not yet said go on the document). Step 1: ask Carlos to
> confirm the design, confirming surprising answers; he already decided (b) serve briefs like
> reports, any Sales user may download, store HTML and markdown too, MinIO removal is a separate
> follow-up PR. Step 2, on his go: superpowers:writing-plans, a task-by-task plan under
> `docs/superpowers/plans/2026-11-16-brief-pdf-serving.md` with a model split (Sonnet implements,
> Sonnet reviews each diff, Fable final review), branch `git checkout -b fix/brief-pdf-serving main`,
> then present the plan and STOP for his go. Step 3, on that go: superpowers:subagent-driven-
> development, pg suite on the compose db (start it with `docker compose -f infra/docker-compose.yml
> -f .superpowers/sdd/2026-11-16-live-anthropic/compose.live-anthropic.yml up -d db migrate`, then
> `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon`), offline suite with
> `env -u PERPLEXITY_API_KEY` and the full marker exclusion, web `npm test` and `npx tsc --noEmit`,
> each to a file. Current baselines: pg 362 passed / 1 skipped, offline 2463 / 15, web 234 / 14
> skipped. Drizzle migration 0004 is generated with `npx drizzle-kit generate --name brief_run` and
> hand-appended with the grant, like 0003. Commit via scratch file and `git commit -F`; `gh pr
> create` only on Carlos's word; he merges in the GitHub UI. Never Bedrock; never bare `python`;
> never pipe pytest; never `ruff format`.
