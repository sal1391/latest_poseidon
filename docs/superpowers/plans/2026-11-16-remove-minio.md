# Remove MinIO / Object Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the object-store seam (MinIO locally, S3 on EC2) from the app, the infra and the current-state docs, now that brief and report bytes live in Postgres (D65, D83).

**Architecture:** Pure removal, no new behaviour. The store handle (`SkillContext.artifacts`, `app.state.artifact_store`, `ArtifactStore`) and the `s3_*` settings go; the artifact *reference* (`ArtifactRef`, `SkillResult.artifacts`, the SSE `artifact` part) stays untouched. A new decision D84 records "no object store in any habitat".

**Tech Stack:** Python 3.14 / FastAPI / pydantic-settings, docker compose, bash (infra/aws), Markdown docs.

**Spec:** `docs/superpowers/specs/2026-11-16-brief-pdf-serving-design.md` (its Non-goals name this follow-up) and D83 in `docs/architecture/00-overview.md`. Carlos's rulings of 2026-11-16 are in "Rulings" below.

**Model split:** Sonnet implements every task. Sonnet reviews every task diff (fresh reviewer, forbidden from re-running the suite). Fable does the final whole-branch review. No Opus, no Haiku.

**Execution approach:** subagent-driven, task by task, one worktree, serial (tasks share files).

**Branch:** `chore/remove-minio`, created with `git checkout -b chore/remove-minio main`.

## Rulings (Carlos, 2026-11-16)

1. (Revised same day, supersedes "remove it all here".) The EC2 habitat is dead but its files are **left in the repo**: `infra/aws/*.sh` (including `03-s3.sh` and the S3 statements in `04-iam.sh`), `infra/docker-compose.ec2.yml` and `infra/runbooks/deploy-ec2.md` keep their content. Each gets a short "PARKED" banner saying the target is SPCS + Snowflake Postgres and that its S3 parts are stale since D84. No script is edited beyond the banner, none is run. Bedrock stays as an LLM provider for now (a later switch to the Snowflake endpoint is possible, not part of this PR).
2. Doc sweep covers every current-state doc plus a new D84. History is untouched: `docs/architecture/08-build-phases.md`, everything under `docs/superpowers/`, frozen Alembic files.
3. Retention is parked: D84 notes the 90-day bucket expiry is gone and brief/report retention is an open item.

## Decisions taken in this plan (flag any you disagree with)

- **boto3 stays** in `pyproject.toml`: `core/llm/bedrock.py` uses it.
- **`SkillResult.artifacts` and `ArtifactRef` stay.** They feed the frozen SSE artifact part.
- **The ~55 test files that pass `s3_bucket=` / `"S3_BUCKET"` are swept**, even though `Settings` has `extra="ignore"` and would tolerate them. Leaving them would keep a dead variable looking required. It is its own commit so the review is mechanical.
- **The Vite frontend is not touched** (three stale "presigned GET" comments in `frontend/src` stay; that app is frozen until M-cutover).
- **Runbook "Verifying crash-on-missing configuration"** currently uses `S3_BUCKET` as the demo variable. It switches to a malformed enum (`DEPLOY_MODE: bogus`), verified live in Task 6.

## Global Constraints

- Always `.venv/Scripts/python.exe`, never bare `python`.
- Never pipe pytest. Redirect to a file, then read the file's tail.
- Offline suite command, exactly (until Task 3 removes the marker, `not minio` stays in the expression):
  `cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' > <scratch>/offline.txt 2>&1`
- Never run `-m router_live`. Never call Bedrock. Never `ruff format`. `ruff check .` must stay clean.
- pg suite is serial only, against the compose db on host port 5434:
  `docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-11-16-brief-pdf-serving/compose.ports.yml up -d db migrate`, then `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon`.
- Frozen surfaces: ontology, Alembic 0001-0010, SSE envelope and part contract, `contracts/turns` (`git status --short contracts/` must stay empty; if a regeneration seems needed, stop and report), identity/RLS wiring.
- Before every commit: `git rev-parse --abbrev-ref HEAD` must print `chore/remove-minio`; after it, `git log --oneline main..HEAD` shows only this plan's commits. Otherwise stop and report, do not improvise.
- Commit messages go in a scratch file and are committed with `git commit -F <path>`. Never `--no-verify`. No push, no `gh pr create`.
- A file not named in a task's Files block may be edited only when that task's sweep pattern matches it. Anything else: stop and report.
- Baselines on main @ 4af17f9: offline 2475 passed / 15 skipped / 153 deselected; pg 368 passed / 1 skipped; web 241 passed / 15 skipped.

## Review Focus

1. **A leftover `S3_BUCKET` in a developer's `backend/.env` or shell** must not break boot. `Settings` has `extra="ignore"`; Task 2 pins it with a test.
2. **`SkillContext` positional construction.** `artifacts` is the second field; any `SkillContext(data, None, settings)` call would silently shift `settings` into the wrong slot after removal. Task 1 greps for positional calls and its fitness test pins the field order.
3. **A brief turn on a live app after removal** must still produce the `/api/briefs/{id}/pdf` artifact part. Covered by the existing pg tests; Task 6 also clicks it in a browser.
4. **Compose boot with no `minio` service**: `depends_on` in `backend` must not name it, and an orphaned `miniodata` volume on an existing machine must be harmless. Task 6 boots it.
5. **The parked EC2 files must stay syntactically intact.** A banner is a comment only: `bash -n` on every touched script and `docker compose config` shape must be unaffected. Task 4 checks both.

---

### Task 1: Remove the store handle and its threading

**Files:**
- Delete: `backend/poseidon/core/artifacts.py`, `backend/tests/test_artifact_store.py`
- Modify: `backend/poseidon/core/skills/context.py` (field at :183, TYPE_CHECKING import :115-116, docstring paragraph :98-105, `ArtifactRef` docstring :170-172)
- Modify: `backend/poseidon/core/chat/orchestrator.py` (TYPE_CHECKING import :330-334; `artifacts` parameter and pass-through at :501, :566-570, :640, :663, :710, :1381, :1443, :1623, :1709, :1773, :1797). Lines :1473 and :1827 (`"artifacts": result.artifacts`) are the SSE payload and **stay**.
- Modify: `backend/poseidon/api/app.py` (import :10; local block :178-209 keeps only what is not store-related; comment :469; block :614-634)
- Modify: `backend/poseidon/api/live_chat.py` (:1038-1046), `backend/poseidon/api/internal.py` (:168, comment :179), `backend/poseidon/api/dev_runner.py` (docstring :64-66, :89; :109 stays)
- Modify: `backend/poseidon/scripts/demo_report.py` (:174 only), `backend/poseidon/tasks/customer_insight/skills/existing_customer_brief/tools/__init__.py` (:7 docstring)
- Modify tests: `backend/tests/test_live_chat_sse.py` (delete `test_live_wiring_builds_an_artifact_store_and_turns_carry_it`, :318-414), `backend/tests/test_brief_wiring.py` (:3 docstring reference), `backend/tests/test_chat_e2e_scripted.py` (delete `_drop_artifact_store`, :195-206), `backend/tests/test_report_flow_pg.py` (:23, :46), `backend/tests/test_architecture_fitness.py`
- Sweep pattern (any backend `.py`): `artifacts=None` inside a `SkillContext(...)` call, and comments naming `ArtifactStore`, `artifact_store` or `ctx.artifacts`.

**Interfaces:**
- Produces: `SkillContext(data, settings, state=..., tools=..., ...)` with no `artifacts` field; `execute_turn` and its helpers take no `artifacts` parameter; `app.state` has no `artifact_store`.

- [ ] **Step 1: Write the failing fitness tests** in `backend/tests/test_architecture_fitness.py`:

```python
def test_object_store_seam_is_gone():
    import dataclasses, importlib.util
    from poseidon.core.skills.context import SkillContext

    assert importlib.util.find_spec("poseidon.core.artifacts") is None
    names = [f.name for f in dataclasses.fields(SkillContext)]
    assert "artifacts" not in names
    assert names[:2] == ["data", "settings"]


def test_no_source_file_mentions_the_store():
    # walks backend/poseidon/**/*.py; fails listing every file containing
    # "ArtifactStore" or "artifact_store"
```

- [ ] **Step 2: Run them, confirm both fail** for the right reason (module still importable; field present; files listed). Record the failure output in the report.
- [ ] **Step 3: Grep for positional `SkillContext(` calls** (any call whose second argument is not a keyword). Convert each to keywords before removing the field. Report the list, even if empty.
- [ ] **Step 4: Remove the field, the parameters, the app-state wiring, the module and the tests listed above.** In `app.py`'s `deploy_mode == "local"` block, delete the store construction, the `ensure_bucket()` try/except and their comments; keep the dev-runner mounting and the `flush=True` comment only if a print still follows it. Rewrite `ArtifactRef`'s docstring to: `url` is an app-relative path such as `/api/briefs/{id}/pdf` that the app itself serves.
- [ ] **Step 5: Run the two fitness tests (pass), `ruff check .` (clean), then the full offline suite.** Expected: 2475 + 2 new = **2477 passed, 15 skipped**; deselected drops by the number of tests in the deleted `test_artifact_store.py` and by 1 if the deleted wiring test was `pg`-marked. Report the exact line and reconcile any difference test by test.
- [ ] **Step 6: Prove the guard.** Temporarily re-add `artifacts: object | None = None` to `SkillContext`, confirm `test_object_store_seam_is_gone` fails, restore, confirm it passes.
- [ ] **Step 7: Commit** `refactor: remove ArtifactStore and SkillContext.artifacts (D84)`.

### Task 2: Remove the `s3_*` settings

**Files:**
- Modify: `backend/poseidon/core/config.py` (:26-33 fields and comment; :264 validator becomes `@field_validator("database_url")`)
- Modify: `backend/tests/test_config.py`, `backend/tests/test_architecture_fitness.py`
- Modify docstrings: `backend/poseidon/scripts/cost_rollup.py` (:68-77), `feedback_rollup.py` (:90), `memory_worker.py` (:178)
- Sweep pattern (backend tests and scripts): `s3_bucket=`, `"S3_BUCKET"`, `S3_BUCKET=`, `s3_endpoint_url`, `s3_access_key`, `s3_secret_key`, and prose that calls `S3_BUCKET` required.
- Modify: any tracked `.env.example` that lists `S3_*` or `MINIO_*` (search hidden files too).

**Interfaces:**
- Produces: `Settings` whose only required field is `database_url`.

- [ ] **Step 1: Write the failing tests.**

```python
# test_architecture_fitness.py
def test_settings_have_no_object_store_fields():
    from poseidon.core.config import Settings
    assert not [n for n in Settings.model_fields if n.startswith("s3_")]

# test_config.py
def test_a_leftover_s3_bucket_variable_is_ignored(monkeypatch):
    # set DATABASE_URL and S3_BUCKET=poseidon-artifacts, build Settings(_env_file=None)
    # assert it builds and has no attribute s3_bucket

def test_database_url_is_the_only_required_setting(monkeypatch):
    # clear env, set only DATABASE_URL, Settings(_env_file=None) builds
```

- [ ] **Step 2: Run, confirm the first fails** (fields present) and the third fails (`s3_bucket` missing). The second fails on the `hasattr` assertion. Record output.
- [ ] **Step 3: Remove the four fields and narrow the validator.**
- [ ] **Step 4: Sweep** every match of the pattern out of tests and script docstrings. Where a test's docstring explains that `S3_BUCKET` must be set for `get_settings()` to validate, rewrite it to name `DATABASE_URL` only. Do not change any assertion other than deleting S3-specific ones; if a test exists only to prove `S3_BUCKET` is required, delete it and list it in the report.
- [ ] **Step 5: `ruff check .`, then the full offline suite.** Expected: Task 1's figure + 3, minus any deleted S3-only tests (each named in the report).
- [ ] **Step 6: Run the pg suite serially** (output to a file). Expected: 368 minus the tests Task 1 deleted from pg-marked files; reconcile by name. `git status --short contracts/` empty.
- [ ] **Step 7: Prove the guard.** Re-add `s3_bucket: str` to `Settings`, confirm `test_database_url_is_the_only_required_setting` fails, restore.
- [ ] **Step 8: Commit** `refactor: drop s3_* settings and their test scaffolding`.

### Task 3: Remove the `slug` helper and the `minio` marker

**Files:**
- Modify: `backend/poseidon/tasks/customer_insight/skills/existing_customer_brief/tools/build_brief_pdf.py` (delete `slug` :53-74 and `_SLUG_DISALLOWED` :50; drop `import re` only if nothing else uses it)
- Modify: `.../existing_customer_brief/tests/test_tools.py` (delete the slug tests around :489-514 and the import)
- Modify: `backend/pyproject.toml` (:43 marker line; **`boto3>=1.34` at :17 stays**)
- Sweep pattern (backend): the word `minio` in any case. After this task the only allowed hits under `backend/` are in frozen Alembic files.

- [ ] **Step 1: Grep `\bslug\b` across `backend/poseidon` and `web/src`** and confirm the only non-test caller-free definition is this one. If anything imports it, stop and report.
- [ ] **Step 2: Delete the helper, its tests and the marker; sweep `minio` mentions** in comments and docstrings (reword, do not leave dangling references to a "minio-marked suite").
- [ ] **Step 3: `ruff check .`, then the full offline suite with the new exclusion** `-m 'not pg and not pdf and not router_live and not research_live'`. Expected: Task 2's figure minus the number of deleted slug test cases (count parametrized cases individually and state the arithmetic).
- [ ] **Step 4: Commit** `chore: remove unused slug helper and the minio pytest marker`.

### Task 4: Infra

**Files:**
- Modify: `infra/docker-compose.yml` (delete service :16-23, the four `S3_*` lines in `backend` :51-54 and `worker` :111-114, `depends_on` :84, volume :156)
- Modify: `infra/docker-compose.live.yml` (:15 comment), `infra/Dockerfile` (:148 comment: "Artifacts go to S3" becomes "Brief and report bytes are stored in Postgres")
- Banner only (no other edit): `infra/docker-compose.ec2.yml`, `infra/runbooks/deploy-ec2.md`, `infra/aws/03-s3.sh`, `infra/aws/04-iam.sh`

Banner text, as a comment in the file's own syntax, placed directly under the shebang or at the top:

> PARKED (D84, 2026-11-16): the EC2 habitat is not a deploy target; Poseidon ships to Snowpark Container Services with Snowflake Postgres. This file is kept for reference only. The app no longer reads `S3_*` and has no object store, so every S3 / artifact-bucket step here is stale.

- [ ] **Step 1: Edit `infra/docker-compose.yml`, the live compose comment and the Dockerfile comment.** Verify with `docker compose -f infra/docker-compose.yml config > <scratch>/compose.txt 2>&1` (exit 0, no `minio`, no `S3_`).
- [ ] **Step 2: Add the banner to the four parked files.** `git diff --stat` must show only added lines in those four. Run `bash -n` on both scripts. Do **not** run any `infra/aws` script, with or without `--dry-run`: their lookups hit a real AWS account.
- [ ] **Step 3: Grep `infra/` for `minio|S3|s3:|bucket`**; the only allowed hits are inside the four parked files, the rate-limiter "bucket" comment in `Dockerfile`, and runbook text handled in Task 5. List what remains.
- [ ] **Step 4: Commit** `chore(infra): remove the minio service; mark the EC2 habitat parked`.

### Task 5: Docs and decision D84

**Files:**
- Modify: `docs/architecture/00-overview.md` (new D84 row after D83; mark D39 and D83 "(completed by D84)"; diagram node `OBJ` :93 and its edges removed; prose :196)
- Modify: `docs/architecture/05-auth-identity.md` (:253 object-store row, :283 `/api/artifacts/{id}` row removed; :284 already describes the byte routes)
- Modify: `docs/architecture/07-infrastructure.md` (:15-16, :29, :45, :59, :81-94 wording, :165, :191, :196-199, :219, :250)
- Modify: `docs/architecture/02-backend-skills.md` (:150) and `03-llm-routing.md` (:239) only where they state the store as current fact
- Modify: `infra/runbooks/local.md` (:22, :33, :37, markers section :99-158, delete "Artifacts (MinIO)" :161-187, "Verifying crash-on-missing configuration" :511-527), `infra/runbooks/smoke.md` (:151-154 check the wording says the link is an app route)
- Modify: `README.md` (:52 "three services", :59, :88), `docs/snowflake/CORTEX_HANDOFF.md` (:74, :83, :93, :115, :124), `CLAUDE.md` (:19 stack line, :42 and :108 marker exclusion; figures are Task 6's)
- Not touched: `docs/architecture/08-build-phases.md`, `docs/superpowers/**`, `frontend/**`.

D84 text, verbatim:

> | D84 | No object store in any habitat. MinIO is removed from the app and local stack: `ArtifactStore`, `SkillContext.artifacts`, the `S3_*` settings, the `minio` test marker and the compose `minio` service. Brief and report bytes are Postgres rows served by Next.js routes (D65, D83). The EC2 habitat is parked: the deploy target is SPCS with Snowflake Postgres (D39); `infra/aws/`, `docker-compose.ec2.yml` and `deploy-ec2.md` are kept for reference under a PARKED banner and their S3 steps are stale. `boto3` remains for the Bedrock provider, which stays for now. The bucket's 90-day expiry went with it; retention of `brief_run` / `report_run` rows is an open item for the first real deploy. | completes D39 and D83; revises doc 07 §1, §5, §6 and doc 05 §8 |

- [ ] **Step 1: Add D84 and the two back-references.**
- [ ] **Step 2: Sweep the docs above.** In doc 07, the EC2 section (§5) and the EC2 column of the §6 environment table are not rewritten: §5 gets the PARKED note as its first line and the `S3_*` row of the table is deleted. Rule for everything else: a sentence stating MinIO/S3/presigned URLs as how things work today is rewritten or deleted; a sentence recording history ("D20 originally kept MinIO in the service") stays, with "(removed by D84)" where it would otherwise mislead.
- [ ] **Step 3: Rewrite the runbook's crash-on-missing section** to use `DEPLOY_MODE: bogus` in the `backend` service and expect a `ValidationError` naming `deploy_mode`. Mark it "verified in Task 6".
- [ ] **Step 4: Final grep** for `minio|MinIO|S3_|presigned|pre-signed|ArtifactStore` across the repo excluding `docs/superpowers`, `.superpowers`, `docs/architecture/08-build-phases.md`, `frontend`, Alembic files, `node_modules`, `.venv`. Report every remaining hit with a one-line justification.
- [ ] **Step 5: Commit** `docs: D84 no object store; sweep MinIO/S3 from current-state docs`.

### Task 6: Whole-branch verification and figures

**Files:**
- Modify: `CLAUDE.md` (the "Commands" comments and the "Figures verified" paragraph), `README.md` only if it quotes a figure.

- [ ] **Step 1: Run each suite once, each to its own file:** offline (new four-marker exclusion), `ruff check .`, `tests/contracts -q` (9 passed, `git status --short contracts/` empty), pg serial, web `npm test` and `npx tsc --noEmit` (expected unchanged: 241 passed / 15 skipped, tsc clean).
- [ ] **Step 2: Boot the real stack:** `docker compose -f infra/docker-compose.yml down` then `up -d`; confirm no `minio` container, backend answers `/api/health`, and the backend log has no "artifact bucket" warning. Run the `pdf`-marked brief tests in the backend container (1 passed).
- [ ] **Step 3: Prove the runbook claim.** Set `DEPLOY_MODE: bogus` in the backend service, bring it up, capture the `ValidationError`, restore the file, confirm `git diff infra/docker-compose.yml` is empty against the Task 4 commit.
- [ ] **Step 4: Write the verified figures into `CLAUDE.md`** with today's date, dropping `minio` from every command and the gotcha list.
- [ ] **Step 5: Commit** `docs: refresh verified suite figures after the MinIO removal`.

After Task 6: Fable reviews the whole branch diff, then walks one brief turn in the browser (stub router) and clicks the PDF link. The PR is opened only on Carlos's word.

## Manual items for Carlos (not done by any agent)

- If `infra/aws/03-s3.sh` was ever run for real, a `poseidon-artifacts-<account-id>` bucket still exists in AWS. Nothing uses it; delete it in the console whenever convenient.
- On this machine, the old `miniodata` docker volume is orphaned. `docker volume ls` shows it; `docker volume rm infra_miniodata` removes it (name to be confirmed in Task 6's report).
