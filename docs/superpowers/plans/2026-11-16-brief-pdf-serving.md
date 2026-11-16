# Brief PDFs Served Like Reports (F1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A brief's PDF link opens in the browser for as long as the chat message exists, by storing brief HTML/PDF/markdown in Postgres and serving them from Next.js routes exactly like reports.

**Architecture:** A new `brief_run` table (Drizzle 0004, shared org document, no RLS policy) written by a Python `BriefStore` that mirrors `ReportStore`. Both brief skills call one shared tool, `persist_brief`, which renders, stores and returns the artifact reference plus proof lines. The Next app serves `/api/briefs/[runId]/{pdf,html}` through a serving helper shared with the report routes.

**Tech Stack:** Python 3.14 (`backend/.venv/Scripts/python.exe`), SQLAlchemy `text()`, pytest; Drizzle + Next.js route handlers, vitest, tsc.

**Spec:** `docs/superpowers/specs/2026-11-16-brief-pdf-serving-design.md` (approved by Carlos 2026-11-16). Executors read both.

**Model split:** Fable supervises, dispatches and does the final whole-branch review; never writes implementation code. Sonnet implements every task. A fresh Sonnet reviews each task's diff. Haiku: nothing. Escalate a task to Opus only after 2-3 real failed attempts.

**Execution approach:** subagent-driven, task by task, in a fresh chat started from the handoff prompt at the bottom of this file.

## Global Constraints

- Branch: `git checkout -b fix/brief-pdf-serving main`. Before every commit run `git rev-parse --abbrev-ref HEAD` and confirm `fix/brief-pdf-serving`; after it, `git log --oneline main..HEAD` must show only this plan's commits. On any mismatch: stop and report, do not improvise.
- Commit messages go in a scratch file and are committed with `git commit -F <path>`. Never `--no-verify`. Never push to `main`. `gh pr create` only on Carlos's word.
- Always `.venv/Scripts/python.exe`, never a bare `python`. Never pipe pytest: redirect to a file, then read the file. Never `ruff format`. `ruff check .` must stay clean.
- Offline suite always with `env -u PERPLEXITY_API_KEY` and `-m 'not pg and not minio and not pdf and not router_live and not research_live'`. Never a bare `-m router_live`. Never Bedrock.
- The pg suite runs serially, never in parallel, against the compose db: `docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-11-16-live-anthropic/compose.live-anthropic.yml up -d db migrate`, then `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon`. After Task 1 lands, re-run the `migrate` service before any pg run.
- Drizzle owns the schema. Alembic migrations 0001-0010 are never touched.
- Frozen: the SSE envelope and the artifact part shape `{name, url, mime}`; only the `url` value changes. `git status --short contracts/` must stay empty. If the contracts corpus wants regeneration: stop and report.
- No LLM involvement anywhere in this change. Ontology untouched.
- Out of scope (separate follow-up PR): removing MinIO, `ArtifactStore`, the `s3_*` settings, the `minio` marker, `SkillContext.artifacts`, `app.state.artifact_store`. They stay; the brief skills simply stop reading them.
- TDD: each implementer shows the test failing first and says why that failure is the right one. Prove a guard by breaking the code (revert the line, watch the named test fail, restore), not by reasoning. Reviewers do not re-run suites; they check the implementer's evidence against the diff.
- Baselines before this work: offline 2463 passed / 15 skipped; pg 362 passed / 1 skipped; web 234 passed / 14 skipped; `npx tsc --noEmit` clean.

**Sanctioned files (nothing outside this list is modified):**

- Create: `backend/poseidon/core/documents.py`, `backend/poseidon/briefs/__init__.py`, `backend/poseidon/briefs/store.py`, `backend/poseidon/tasks/customer_insight/skills/existing_customer_brief/tools/persist_brief.py`, `backend/tests/test_documents.py`, `backend/tests/test_brief_store_pg.py`, `backend/tests/test_brief_wiring.py`, `web/drizzle/0004_brief_run.sql`, `web/drizzle/meta/0004_snapshot.json`, `web/src/lib/briefs.ts`, `web/src/lib/serve-document.ts`, `web/src/app/api/briefs/[runId]/pdf/route.ts`, `web/src/app/api/briefs/[runId]/html/route.ts`, `web/src/app/api/briefs/[runId]/route.test.ts`
- Modify (backend): `poseidon/tasks/reporting/skills/monthly_performance/skill.py`, `poseidon/tasks/reporting/skills/report_lookup/skill.py`, `poseidon/api/app.py`, `poseidon/api/live_chat.py`, `poseidon/api/internal.py`, `poseidon/api/dev_runner.py`, `poseidon/core/chat/orchestrator.py`, `poseidon/core/skills/context.py`, `poseidon/tasks/customer_insight/skills/existing_customer_brief/skill.py`, `.../existing_customer_brief/tools/build_brief_pdf.py`, `.../existing_customer_brief/tests/test_tools.py`, `poseidon/tasks/customer_insight/skills/new_prospect_brief/skill.py`, `tests/test_brief_skills.py`, `tests/test_entry_orchestration.py` (amendment 1: the one `Artifact:` proof assertion at :489), `tests/test_chat_e2e_scripted.py`, `tests/test_history_cutover.py`, `tests/test_live_chat_sse.py` (only if Task 2's stop-and-report clause is ruled that way)
- Modify (web): `web/src/db/schema.ts`, `web/src/db/migrations.test.ts`, `web/drizzle/meta/_journal.json`, `web/src/app/api/reports/[runId]/serve.ts`, `web/src/lib/report-files.test.ts`, `web/src/ui/message-parts/ArtifactPart.tsx` (comment only)
- Modify (docs): `docs/architecture/00-overview.md`, `CLAUDE.md`, `infra/runbooks/local.md`, `web/README.md` (amendment 2: a "Brief downloads" paragraph); add to the commit: this plan and the spec.

## Review Focus

1. **A stored brief whose PDF did not render, requested at `/pdf`.** Expected: 404 problem detail, not a 500 or an empty download. Pinned in Task 4 (`readBriefFile` returns null on null bytes) and Task 1's live test.
2. **A title with a double quote, slash or double space** (customer names are free text). Expected: the link's `name` and the route's `content-disposition` filename are the same safe string. Pinned by the parity tests in Task 1 (Python) and Task 4 (TS).
3. **The database write fails after the brief's sections are already on screen.** Expected: the brief still completes `ok`, with no PDF link and a proof line `Brief: not stored: <ExceptionName>`. Pinned in Task 3. (Carlos's call C1 below.)
4. **A conversation id that is not a UUID** (`/internal`, dev runner, offline contexts pass `None` or strings like `conv-x`). Expected: stored as NULL, no crash. Pinned in Task 2's pg test.
5. **A context with no user** (dev runner, offline). Expected: `created_by = "dev|anonymous"`. Pinned in Task 3.

Known and accepted, not fixed here: the legacy Vite app at :5173 has no `/api/briefs` route, exactly as it has no `/api/reports` route today. Old messages keep their dead MinIO URLs.

## Rulings (Carlos, 2026-11-16: "I agree with both")

- **C1 (behaviour), RULED: degrade.** If saving the brief row fails, the brief is still shown, with no link and a proof line saying why, instead of failing the whole turn. The report skill fails the turn in the same spot; briefs differ because their content is already streamed.
- **C2 (small design deviation), RULED: accepted.** The two skills share one new tool `persist_brief` instead of each carrying a copy of the render-store-prove block, and when no brief store is configured the PDF is not rendered at all (nothing would consume it). Tests therefore patch `persist_brief`'s module, one site, not each skill.

---

### Task 1: `brief_run` table (Drizzle 0004) and the shared filename helpers

**Files:**
- Modify: `web/src/db/schema.ts` (after `reportRun`, line ~162), `web/src/db/migrations.test.ts`, `web/drizzle/meta/_journal.json` (generated)
- Create: `web/drizzle/0004_brief_run.sql`, `web/drizzle/meta/0004_snapshot.json` (generated), `backend/poseidon/core/documents.py`, `backend/tests/test_documents.py`
- Modify: `backend/poseidon/tasks/reporting/skills/monthly_performance/skill.py:84,118-122,230-231,366`, `backend/poseidon/tasks/reporting/skills/report_lookup/skill.py:23,43`

**Interfaces:**
- Produces (TS): `export const briefRun = pgTable("brief_run", …)` with columns `id` uuid PK, `kind` text NN, `subject` text NN, `title` text NN, `anchorDate` date("anchor_date") NN, `status` text NN, `markdown` text NN, `html` bytea, `pdf` bytea, `pdfError` text("pdf_error"), `createdBy` text("created_by") NN, `conversationId` uuid("conversation_id"), `generatedAt` timestamptz NN (`mode: 'string'`), `createdAt` timestamptz default now NN. Checks: `ck_brief_run_kind` (`existing_customer` | `new_prospect`), `ck_brief_run_status` (`ok` | `error`). Reuse the existing `bytea` customType. No `.enableRLS()`.
- Produces (Python): `poseidon.core.documents.ANONYMOUS_SUB = "dev|anonymous"` and `safe_filename(name: str) -> str` (body moved verbatim from `monthly_performance/skill.py`).

- [ ] **Step 1: Failing migrations test.** In `migrations.test.ts`: expected list gains `"0004_brief_run.sql"` (rename the test title to "five"); add a test "0004 creates brief_run as a shared org document":

```ts
const sql = readFileSync(join(drizzleDir, "0004_brief_run.sql"), "utf8");
expect(sql).toContain('CREATE TABLE "brief_run"');
expect(sql).toContain("GRANT SELECT, INSERT ON brief_run TO poseidon_app;");
expect(sql).not.toContain("ENABLE ROW LEVEL SECURITY");
expect(sql).not.toContain("CREATE POLICY");
expect(sql).toMatch(/ck_brief_run_kind[\s\S]*'existing_customer'::text[\s\S]*'new_prospect'::text/);
```

- [ ] **Step 2:** `cd web && npm test -- src/db/migrations.test.ts > <scratch>/t1-red.txt 2>&1`. Expected: FAIL, file list mismatch and ENOENT on 0004.
- [ ] **Step 3:** Add `briefRun` to `schema.ts`. Generate: `cd web && DRIZZLE_DATABASE_URL=postgresql://unused:unused@127.0.0.1:1/unused npx drizzle-kit generate --name brief_run`. The generated SQL must contain only the `CREATE TABLE "brief_run"`; if it touches any other table, stop and report. Hand-append, after a `--> statement-breakpoint` line:

```sql
-- D83: a shared org document like report_run (D65/D69). No ENABLE/FORCE ROW LEVEL SECURITY, no policy.
-- Reads by any Poseidon:Sales user; writes by the app role on behalf of the generating user.
GRANT SELECT, INSERT ON brief_run TO poseidon_app;
```

- [ ] **Step 4:** Re-run the migrations test to a file. Expected: all pass, including the existing "generate produces nothing new" tripwire. `npx tsc --noEmit` clean.
- [ ] **Step 5: Failing Python test** `backend/tests/test_documents.py`:

```python
from poseidon.core.documents import ANONYMOUS_SUB, safe_filename

PARITY = [  # the same fixtures as web/src/lib/report-files.test.ts (Task 4)
    ("Acme  Marine Brief", "Acme Marine Brief"),
    ("A/S Rederi: Q3 Brief!", "A_S Rederi_ Q3 Brief_"),
    ('Rotterdam/"x" Office', "Rotterdam__x_ Office"),
    ("   ", "report"),
]

def test_safe_filename_parity_fixtures():
    for raw, expected in PARITY:
        assert safe_filename(raw) == expected

def test_anonymous_sub():
    assert ANONYMOUS_SUB == "dev|anonymous"

def test_reporting_skills_use_the_shared_helpers():
    from poseidon.tasks.reporting.skills.monthly_performance import skill as monthly
    from poseidon.tasks.reporting.skills.report_lookup import skill as lookup
    assert monthly.safe_filename is safe_filename
    assert not hasattr(lookup, "ANONYMOUS") and not hasattr(monthly, "ANONYMOUS")
```

- [ ] **Step 6:** Run it to a file. Expected: FAIL with `ModuleNotFoundError: poseidon.core.documents`.
- [ ] **Step 7:** Create `core/documents.py`; in the two reporting skills delete the local `ANONYMOUS` and `safe_filename` and import `ANONYMOUS_SUB`, `safe_filename` from `poseidon.core.documents`, replacing each use. Drop `re` from `monthly_performance/skill.py` only if nothing else there uses it.
- [ ] **Step 8:** Run `test_documents.py` plus `poseidon/tasks/reporting` tests (offline marker exclusion) to a file. Expected: all pass. `ruff check .` clean.
- [ ] **Step 9:** Commit: `feat(db): brief_run table (Drizzle 0004); hoist safe_filename and the anonymous sub`.

### Task 2: `BriefStore` and its wiring

**Files:**
- Create: `backend/poseidon/briefs/__init__.py` (docstring only), `backend/poseidon/briefs/store.py`, `backend/tests/test_brief_store_pg.py`, `backend/tests/test_brief_wiring.py`
- Modify: `backend/poseidon/core/skills/context.py:186` (field + docstring entry), `backend/poseidon/core/chat/orchestrator.py` (every site that carries `report_store`: lines ~505, 660, 781, 1616, 1701, 1764, 1793), `backend/poseidon/api/app.py:586`, `backend/poseidon/api/live_chat.py:1061`, `backend/poseidon/api/internal.py:181`, `backend/poseidon/api/dev_runner.py:89`

**Interfaces:**
- Consumes: the `brief_run` table from Task 1 (re-run compose `migrate` first).
- Produces:

```python
@dataclass(frozen=True)
class NewBrief:
    id: str                      # uuid7 string
    kind: str                    # existing_customer | new_prospect
    subject: str
    title: str
    anchor_date: date
    status: str                  # ok
    markdown: str
    html: bytes | None
    pdf: bytes | None
    pdf_error: str | None
    created_by: str
    conversation_id: str | None
    generated_at: datetime       # tz-aware

class BriefStore:
    def __init__(self, engine: Engine, app_role: str | None = None) -> None
    def insert_brief(self, user_sub: str, brief: NewBrief) -> None
    def get_bytes(self, user_sub: str, brief_id: str, kind: Literal["html", "pdf"]) -> bytes | None
```

  Mirror `backend/poseidon/reports/store.py`: plain `text()` SQL, every method inside `rls_transaction(self._engine, user_sub, app_role=self._app_role)`, bytes column chosen from a fixed map, `get_bytes` raises `ValueError` on any other kind and returns `None` for a malformed or unknown id. One difference from `ReportStore`: `insert_brief` passes `conversation_id` through a local `_parse_uuid` and stores NULL when it is not a UUID.
- Produces: `SkillContext.brief_store: object | None = None` (added after `prompt_tree`, before `conversation_id`); `execute_turn(..., brief_store: object | None = None)` threaded to every `SkillContext` it builds; `app.state.brief_store`.

- [ ] **Step 1: Failing pg test** `test_brief_store_pg.py` (`pytest.mark.pg`, using the `pg_database_url` fixture and engine construction the way `backend/tests/test_report_store.py` does). Tests, each with a fresh `str(uuid7())` id:
  - `test_insert_then_get_bytes_round_trip`: insert with `html=b"<p>h</p>"`, `pdf=b"%PDF-x"`; `get_bytes(..., "pdf") == b"%PDF-x"`, `get_bytes(..., "html") == b"<p>h</p>"`.
  - `test_a_second_user_reads_the_first_users_brief`: insert as `dev|alice`, read as `dev|bob`, equal bytes.
  - `test_pdf_null_reads_none`: insert with `pdf=None, pdf_error="OSError"`; `get_bytes(..., "pdf") is None`, html still readable.
  - `test_malformed_and_unknown_ids_return_none`: `"not-a-uuid"` and a fresh uuid7 both give `None`.
  - `test_non_uuid_conversation_id_is_stored_null`: insert with `conversation_id="conv-x"` succeeds; a direct `SELECT conversation_id` returns NULL. A second insert with a real uuid stores it.
  - `test_unknown_bytes_kind_raises`: `pytest.raises(ValueError)` for `kind="payload"`.
- [ ] **Step 2:** Run `-m pg tests/test_brief_store_pg.py` to a file. Expected: FAIL at import (`poseidon.briefs` missing).
- [ ] **Step 3:** Implement `briefs/store.py`.
- [ ] **Step 4:** Re-run to a file. Expected: 6 passed.
- [ ] **Step 5: Failing offline wiring test** `test_brief_wiring.py`, modelled on `tests/test_live_chat_sse.py:318-414` (import its `_live_app`, `_settings` and fakes the same way that test does):
  - `test_create_app_wires_a_brief_store`: `isinstance(_live_app().state.brief_store, BriefStore)`.
  - `test_execute_turn_threads_brief_store_to_both_context_sites`: a `SkillRegistry.dispatch` spy returning a canned `SkillResult` for `customer_insight.*`; call `execute_turn(..., brief_store=sentinel)` once for a routed turn and once for a `mode="new_prospect"` subject turn; assert `[ctx.brief_store for _id, ctx in seen] == [sentinel] * len(seen)` and `len(seen) >= 2`.
- [ ] **Step 6:** Run it offline to a file. Expected: FAIL (`brief_store` unexpected keyword / missing attribute).
- [ ] **Step 7:** Wire it: the `SkillContext` field and docstring entry; `execute_turn` and its inner helpers pass `brief_store` everywhere they pass `report_store`; `app.py` adds `app.state.brief_store = BriefStore(engine, app_role=settings.database_app_role)` beside the report store; `live_chat.py` and `internal.py` pass `brief_store=getattr(app_state_or_request_app_state, "brief_store", None)` beside their `report_store` lines; `dev_runner.py` passes `brief_store=getattr(request.app.state, "brief_store", None)`.
- [ ] **Step 8:** Run the wiring test, then the whole offline suite, each to a file. Expected: offline 2463 + the new offline tests from Tasks 1-2, 15 skipped, no failures. Prove the threading guard: remove `brief_store=brief_store` at one orchestrator site, confirm the wiring test fails, restore.
- [ ] **Step 9:** Commit: `feat(briefs): BriefStore over brief_run, threaded into every SkillContext`.

### Task 3: `render_brief`, `persist_brief`, both skills, and every test that pins brief proof lines

**Files:**
- Modify: `.../existing_customer_brief/tools/build_brief_pdf.py`, `.../existing_customer_brief/skill.py:24,171,209,521-533`, `.../new_prospect_brief/skill.py:93,101,108-113,232-244` (and its docstring mentions), `backend/poseidon/core/skills/context.py:167` (`ArtifactRef` docstring: the url is a presigned GET or an app-relative path), `.../existing_customer_brief/tests/test_tools.py`, `backend/tests/test_brief_skills.py`, `backend/tests/test_chat_e2e_scripted.py`, `backend/tests/test_history_cutover.py:394-399`
- Create: `.../existing_customer_brief/tools/persist_brief.py`

**Interfaces:**
- Consumes: `NewBrief`, `BriefStore.insert_brief` (Task 2); `ANONYMOUS_SUB`, `safe_filename` (Task 1); `ctx.brief_store`, `ctx.user`, `ctx.conversation_id`.
- Produces, in `build_brief_pdf.py` (file name kept; `build_brief_pdf` the function, its `ArtifactStore`/`ArtifactRef` imports and the upload are deleted; `slug` and `_render_html` stay):

```python
@dataclass(frozen=True)
class RenderedBrief:
    html: str
    pdf: bytes | None
    pdf_error: str | None      # exception class name when pdf is None
    page_count: int | None

def render_brief(title: str, markdown_body: str) -> RenderedBrief
```

  `render_brief` builds the HTML with `_render_html`, then does the lazy WeasyPrint import and render inside one `try/except Exception` (`# noqa: BLE001`), returning `pdf=None, pdf_error=type(exc).__name__, page_count=None` on any failure. It never raises.
- Produces, in `persist_brief.py`:

```python
BRIEF_SKIP_PROOF = "Brief: skipped (no brief store configured)"

def _now() -> datetime            # datetime.now(UTC); tests monkeypatch it

def persist_brief(
    ctx: SkillContext, *, kind: str, subject: str, title: str,
    anchor_date: date, markdown_body: str,
) -> tuple[list[ArtifactRef], list[str]]   # (artifacts, proof lines)
```

  Behaviour, in order:
  1. `ctx.brief_store is None` -> `([], [BRIEF_SKIP_PROOF])`, without rendering.
  2. `rendered = render_brief(title, markdown_body)`; `brief_id = str(uuid7())` (module-level import so tests can patch `persist_brief.uuid7`).
  3. `created_by = ctx.user.sub` when `ctx.user` is set (cast to `UserContext` as `monthly_performance/skill.py:147` does), else `ANONYMOUS_SUB`.
  4. `ctx.brief_store.insert_brief(created_by, NewBrief(id=brief_id, kind=kind, subject=subject, title=title, anchor_date=anchor_date, status="ok", markdown=markdown_body, html=rendered.html.encode("utf-8"), pdf=rendered.pdf, pdf_error=rendered.pdf_error, created_by=created_by, conversation_id=ctx.conversation_id, generated_at=_now()))`. If the insert raises (C1): log at error level and return `([], [f"Brief: not stored: {type(exc).__name__}"])`.
  5. Proof: `f"Brief: {brief_id}"`, then `f"Pages: {page_count}"` and `f"Bytes: {len(pdf)}"` when the PDF rendered, else `f"PDF not rendered: {pdf_error}"`.
  6. Artifact, only when the PDF rendered: `ArtifactRef(name=safe_filename(title) + ".pdf", url=f"/api/briefs/{brief_id}/pdf", mime="application/pdf")`.
- Skills: each replaces its `if ctx.artifacts is not None: … else: …` block with

```python
artifacts, brief_proof = persist_brief(
    ctx, kind="existing_customer", subject=args.customer,
    title=f"{args.customer} Brief", anchor_date=anchor,
    markdown_body=_render_markdown(args.customer, parts),
)
proof.extend(brief_proof)
```

  and, in the prospect skill, `kind="new_prospect"`, `subject=args.prospect_name`, `title=f"{args.prospect_name} Prospect Brief"`, `anchor_date=_today()`. `_ARTIFACT_SKIP_PROOF` is deleted from both; `ctx.artifacts` is no longer read by either. The prospect skill imports `persist_brief` from `..existing_customer_brief.tools.persist_brief`.

- [ ] **Step 1: Failing offline tests** in `test_brief_skills.py`. Replace `_FakeArtifactStore` with

```python
@dataclass
class _FakeBriefStore:
    inserts: list = field(default_factory=list)
    raises: Exception | None = None
    def insert_brief(self, user_sub, brief):
        if self.raises is not None:
            raise self.raises
        self.inserts.append((user_sub, brief))
```

  `_ctx` gains `brief_store: object | None = None` and `user: object | None = None` and drops `artifacts` (passes `artifacts=None`). Helpers: `_ID = "0199a000-0000-7000-8000-0000000000b1"`; a fixture patching `persist_brief_module.uuid7` to `lambda: _ID` and `persist_brief_module.render_brief` to return a given `RenderedBrief`. Replace Section G's two `pdf`-marked tests with these offline tests, each run for both skills:
  - `…_stores_and_links_the_brief`: `RenderedBrief(html="<p>h</p>", pdf=b"%PDF-1.4 fake", pdf_error=None, page_count=2)`. Assert one artifact, `url == f"/api/briefs/{_ID}/pdf"`, `mime == "application/pdf"`, `name == "Northstar Lines Brief.pdf"` (prospect: `"Meridian Global Shipping Prospect Brief.pdf"`), `result.proof[-3:] == [f"Brief: {_ID}", "Pages: 2", "Bytes: 13"]`; one insert whose `NewBrief` has `id == _ID`, the right `kind`, `subject`, `title`, `status == "ok"`, `html == b"<p>h</p>"`, `pdf == b"%PDF-1.4 fake"`, `markdown` starting with the skill's H1, `anchor_date == _ANCHOR` (existing; prospect patches `prospect_skill._today`), and `user_sub == created_by == "dev|anonymous"`.
  - `…_created_by_is_the_user_sub`: `_ctx(user=<a UserContext with sub "dev|alice">)`; the insert's `user_sub` and `created_by` are `"dev|alice"`.
  - `…_without_a_store_skips`: `artifacts == []`, `result.proof[-1] == "Brief: skipped (no brief store configured)"`, and `render_brief` was not called (the patch raises if called).
  - `…_pdf_not_rendered_still_stores`: `RenderedBrief(html="<p>h</p>", pdf=None, pdf_error="OSError", page_count=None)`. `result.ok is True`, `artifacts == []`, `result.proof[-2:] == [f"Brief: {_ID}", "PDF not rendered: OSError"]`, one insert with `pdf is None` and `pdf_error == "OSError"`.
  - `…_store_failure_degrades`: `_FakeBriefStore(raises=RuntimeError("db down"))`. `result.ok is True`, `artifacts == []`, `result.proof[-1] == "Brief: not stored: RuntimeError"`, and the parts are unchanged.
  - `test_brief_with_a_quoted_title_gets_a_safe_name` (existing skill): customer `'A/S "Rederi"  Lines'` gives `name == "A_S _Rederi_ Lines Brief.pdf"`.

  Also update the two pinned full-proof lists at `test_brief_skills.py:261,335` to end in `"Brief: skipped (no brief store configured)"`, and the module docstring lines 7-10.
- [ ] **Step 2:** Run `tests/test_brief_skills.py` offline to a file. Expected: FAIL at import of `persist_brief`.
- [ ] **Step 3:** Implement `render_brief`, `persist_brief.py`, and the two skill edits. Update the docstrings that describe the old upload (module docstring of `build_brief_pdf.py`, the "5." line in `existing_customer_brief/skill.py:24`, `new_prospect_brief/skill.py:13,64`, the `_today` docstrings that mention a storage key).
- [ ] **Step 4:** Port `existing_customer_brief/tests/test_tools.py`: the `slug` tests stay untouched; delete `test_build_brief_pdf_renders_and_uploads_to_minio`, `_artifact_store()` and any import or helper only they used (`_require_minio`, `httpx`, `ArtifactStore` if now unused); add

```python
@pytest.mark.pdf
@pytest.mark.skipif(not _HAS_WEASYPRINT, reason="weasyprint needs Pango/Cairo - run in the container")
def test_render_brief_produces_a_real_pdf():
    rendered = render_brief("Northstar Lines Q3 Brief", "# Overview\n\n| Port | GP |\n| --- | --- |\n| Singapore | 120000 |\n")
    assert rendered.pdf is not None and rendered.pdf.startswith(b"%PDF")
    assert rendered.pdf_error is None and rendered.page_count >= 1
    assert "<h1>Northstar Lines Q3 Brief</h1>" in rendered.html

def test_render_brief_reports_a_render_failure_instead_of_raising(monkeypatch):
    # offline: make the weasyprint import fail deterministically on every host
    monkeypatch.setitem(sys.modules, "weasyprint", None)
    rendered = render_brief("T", "body")
    assert rendered.pdf is None and rendered.page_count is None
    assert rendered.pdf_error in {"ImportError", "ModuleNotFoundError"}
    assert "<h1>T</h1>" in rendered.html
```

- [ ] **Step 5:** Run `tests/test_brief_skills.py` and that `test_tools.py` offline to a file. Expected: all pass. Prove two guards by breaking and restoring: (a) remove the `try/except` around the insert, `…_store_failure_degrades` fails; (b) make the artifact unconditional, `…_pdf_not_rendered_still_stores` fails.
- [ ] **Step 6: pg e2e.** In `test_chat_e2e_scripted.py` add a helper

```python
_FIXED_PDF = b"%PDF-1.4 e2e fixture"

def _fixed_brief_render(monkeypatch) -> None:
    from poseidon.tasks.customer_insight.skills.existing_customer_brief.tools import (
        build_brief_pdf as render_module, persist_brief as persist_module)
    monkeypatch.setattr(persist_module, "render_brief",
        lambda title, body: render_module.RenderedBrief(
            html="<p>e2e</p>", pdf=_FIXED_PDF, pdf_error=None, page_count=1))
```

  Both brief flow tests take `monkeypatch`, call `_fixed_brief_render(monkeypatch)` and drop their `_drop_artifact_store(app)` call (the helper itself stays: `test_report_flow_pg.py` imports it). The real `BriefStore` wired by `create_app` writes real rows; rows accumulate, as `report_run` rows do. Each brief turn now carries one extra `part` frame of kind `artifact`; run the tests, read where the orchestrator places it relative to the proof part, and update `names_2` and the payload indices accordingly, stating the observed order in the report. New assertions in each flow:

```python
artifact = <the kind == "artifact" part of turn 2>["payload"]
match = re.fullmatch(r"/api/briefs/([0-9a-f-]{36})/pdf", artifact["url"])
assert match and artifact["mime"] == "application/pdf"
assert artifact["name"] == "Northstar Lines Brief.pdf"   # prospect: "Meridian Global Shipping Prospect Brief.pdf"
brief_id = match.group(1)
```

  Existing flow: replace `lines_2[12] == "Artifact: skipped…"` with `lines_2[12:] == [f"Brief: {brief_id}", "Pages: 1", f"Bytes: {len(_FIXED_PDF)}"]` and rewrite the comment block at `:826-834`. Prospect flow: the pinned list at `:1047-1053` ends with those same three lines instead of the skip line. Add to the existing flow one read-back: `app.state.brief_store.get_bytes(<the test user's sub>, brief_id, "pdf") == _FIXED_PDF`. In `test_history_cutover.py` delete `app.state.artifact_store = None` and the docstring paragraph that explains it (lines 394-399); no renderer patch there.
- [ ] **Step 7:** Run the full pg suite serially to a file, then the full offline suite to a file. Expected: pg 362 + Task 2's 6 new, 1 skipped, no failures; offline green. If an offline test fails because an app built with a placeholder DSN now reaches a real `insert_brief`, stop and report the test name; the sanctioned remedy is `app.state.brief_store = None` in that test's own app helper, on Fable's ruling. `git status --short contracts/` empty. `ruff check .` clean.
- [ ] **Step 8:** Commit: `fix(briefs): store brief HTML/PDF/markdown in brief_run and link /api/briefs/{id}/pdf (F1)`.

### Task 4: Next.js routes

**Files:**
- Create: `web/src/lib/serve-document.ts`, `web/src/lib/briefs.ts`, `web/src/app/api/briefs/[runId]/pdf/route.ts`, `web/src/app/api/briefs/[runId]/html/route.ts`, `web/src/app/api/briefs/[runId]/route.test.ts`
- Modify: `web/src/app/api/reports/[runId]/serve.ts`, `web/src/lib/report-files.test.ts`, `web/src/ui/message-parts/ArtifactPart.tsx` (comment at line ~5 only)

Read `web/AGENTS.md` first: this Next.js version differs from training data; check `node_modules/next/dist/docs/` for route handler conventions and copy the existing report routes' shape exactly.

**Interfaces:**
- Consumes: `briefRun` (Task 1); `withUser`, `salesOr403`, `sanitizeFilename`, `RUN_ID_PATTERN` (existing).
- Produces:

```ts
// web/src/lib/serve-document.ts
export type DocumentFile = { bytes: Buffer; fileName: string; contentType: string };
export type DocumentReader = (sub: string, runId: string, kind: "pdf" | "html") => Promise<DocumentFile | null>;
export async function serveDocument(
  request: Request, params: Promise<{ runId: string }>, kind: "pdf" | "html",
  read: DocumentReader, notFoundTitle: string,
): Promise<Response>;

// web/src/lib/briefs.ts
export async function readBriefFile(sub: string, runId: string, kind: "pdf" | "html"): Promise<DocumentFile | null>;
```

  `serveDocument` is the current body of `serveReport` with `readReportFile` and `"report not found"` parameterised; the 404 detail stays `` `no ${kind} for run '${runId}'` ``. `serveReport` keeps its signature and becomes `return serveDocument(request, params, kind, (s, r, k) => readReportFile(s, r, k), "report not found")` (the arrow keeps the report route test's `vi.mock` of `lib/reports` effective). `readBriefFile` mirrors `readReportFile`: rejects a non-matching id, selects `title` and the bytes column through `withUser`, returns null for no row or null bytes, `fileName = sanitizeFilename(title) + "." + kind`. Brief routes: `export const dynamic = "force-dynamic"` and a `GET` calling `serveDocument(request, params, "pdf" | "html", readBriefFile, "brief not found")`, same shape as the report route files.

- [ ] **Step 1: Failing tests.** `briefs/[runId]/route.test.ts` mirrors `reports/[runId]/route.test.ts` with `vi.mock("../../../../lib/briefs", …)` replacing `readBriefFile`:
  - 403 without the role: same problem body as the report test; `read` not called.
  - 404 unknown: `title === "brief not found"`, `detail === "no pdf for run '<RUN>'"`.
  - 200 pdf: `read` resolves `{ bytes: Buffer.from("%PDF-fake"), fileName: "Acme Marine Brief.pdf", contentType: "application/pdf" }`; assert status, `content-type`, `content-disposition` `attachment; filename="Acme Marine Brief.pdf"`, `content-length`, `cache-control` `private, no-store`, and body bytes.
  - 200 html as an attachment; `read` called with `("dev|local", RUN, "html")`.
  - Live block, skipped without `DRIZZLE_DATABASE_URL` like its sibling: insert a `briefRun` row (`kind: "existing_customer"`, `subject: 'A/S "Rederi"'`, `title: 'A/S "Rederi" Brief'`, `anchorDate: "2026-07-01"`, `status: "ok"`, `markdown: "# x"`, `pdf: Buffer.from("%PDF-live")`, `html` omitted, `createdBy: "dev|local"`, `generatedAt`); real `readBriefFile(..., "pdf")` returns those bytes with `fileName === "A_S _Rederi_ Brief.pdf"`; `"html"` returns null; `"not-a-uuid"` returns null.

  In `report-files.test.ts` add the parity fixtures from Task 1's `test_documents.py` as one `it`: the same four inputs and expected outputs.
- [ ] **Step 2:** `cd web && npm test > <scratch>/t4-red.txt 2>&1`. Expected: the brief route test fails on unresolved imports; the parity test passes already (it pins existing behaviour) and the report route test still passes.
- [ ] **Step 3:** Implement the four source files and the `serve.ts` change. Update the `ArtifactPart.tsx` comment: the url is an app-relative download path (`/api/reports/…`, `/api/briefs/…`), no longer a presigned GET.
- [ ] **Step 4:** `npm test` to a file and `npx tsc --noEmit` to a file. Expected: 234 + new tests passed, 14 + the new live block's skipped, tsc clean, and `reports/[runId]/route.test.ts` unmodified (`git diff --stat` shows no change to it). Then run the two route test files once with `DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon` so the live blocks execute; report the result.
- [ ] **Step 5:** Commit: `feat(web): serve brief pdf/html from /api/briefs/[runId] via a shared document server`.

### Task 5: Decision log, runbook, CLAUDE.md figures, final verification

**Files:**
- Modify: `docs/architecture/00-overview.md` (D65 row at line ~245, new D83 row after D82), `infra/runbooks/local.md:405` (the proof-line example) and its "Artifacts (MinIO)" section at `:161` (one sentence: brief PDFs no longer go to MinIO as of D83; removal is a follow-up), `CLAUDE.md` (Commands block and the "Figures verified" paragraph)
- Add to the commit: `docs/superpowers/specs/2026-11-16-brief-pdf-serving-design.md`, `docs/superpowers/plans/2026-11-16-brief-pdf-serving.md`

- [ ] **Step 1:** D83 row, text from the spec's section 6, third column `D65 (extends)`; append "(extended to briefs by D83)" to D65's row. Add C1's behaviour to D83 in one clause: "a failed store write degrades to a proof line, the turn stays ok".
- [ ] **Step 2:** Run, each once, each to its own file, never piped: offline suite; pg suite (serial); `tests/contracts`; `ruff check .`; web `npm test`; web `npx tsc --noEmit`. In the backend container (`docker compose … up -d backend`), run `-m pdf` scoped to `poseidon/tasks/customer_insight` and report the pass line.
- [ ] **Step 3:** Rewrite the CLAUDE.md figures from those files only: the offline, pg and web pass lines, the verification date, the label "(F1 brief PDF serving)", and "four expected migrations" style mentions if any. Vite frontend figures stay marked as carried, not re-run. Do not change any other CLAUDE.md content.
- [ ] **Step 4:** Commit: `docs: D83 brief PDFs served like reports; refresh verified suite figures`.

### Final gate (Fable, then Carlos)

- [ ] Fable whole-branch review of `git diff main...HEAD`: sanction list respected, `contracts/` untouched, report route test untouched, no `ctx.artifacts` read left in either brief skill, every figure in CLAUDE.md traceable to a result file.
- [ ] Live walkthrough in the browser (container backend so WeasyPrint is real; Next dev server per `web/README.md` with `POSEIDON_INTERNAL_TOKEN=dev-internal-token`): run an existing-customer brief and a new-prospect brief, click each PDF link, confirm the file downloads with the expected filename and opens; reload the conversation and click again; confirm an old message's MinIO link is unchanged.
- [ ] Put open judgment calls to Carlos in one message; on his word, `gh pr create` with a written description (what, why, how verified). He merges in the GitHub UI.

---

## Handoff prompt for the execution chat (paste into a fresh chat)

> You are Fable, supervising Poseidon in C:\Users\carlo\github\latest_poseidon (dispatch, review, final
> verification; never implementation code). Read `CLAUDE.md`, then the spec
> `docs/superpowers/specs/2026-11-16-brief-pdf-serving-design.md` and the plan
> `docs/superpowers/plans/2026-11-16-brief-pdf-serving.md` in full. State: main @ 92b091c, nothing
> deployed, no branch yet; Carlos approved the design and ruled C1 (degrade) and C2 (accepted) on
> 2026-11-16, both recorded in the plan's Rulings section. Before dispatching, tell him the execution
> approach (task by task) and get his go. Execute with superpowers:subagent-driven-development, task by
> task: Sonnet implements each task from its plan text plus the Global Constraints, a fresh Sonnet reviews
> each diff without re-running suites, fix rounds until clean, then your whole-branch review and the live
> browser walkthrough in the plan's Final gate. Branch `git checkout -b fix/brief-pdf-serving main`. pg
> suite on the compose db (`docker compose -f infra/docker-compose.yml -f
> .superpowers/sdd/2026-11-16-live-anthropic/compose.live-anthropic.yml up -d db migrate`, re-run `migrate`
> after Task 1; `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon`). Baselines:
> offline 2463/15, pg 362/1, web 234/14 skipped. Commit via scratch file and `git commit -F`; `gh pr
> create` only on Carlos's word; he merges in the GitHub UI. Never Bedrock; never bare `python`; never
> pipe pytest; never `ruff format`.
