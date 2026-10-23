# Poseidon — repo working rules

Chat-first sales-intelligence app for marine fuel sales. Deterministic core: **Python computes,
the LLM only routes**. Solo developer, Windows 11 / PowerShell.

Architecture docs: `docs/architecture/00-08`. **`00-overview.md`'s decision log is the single
index of what was decided** — check it, and check for "revises", before treating any architecture
doc as current. Decisions taken in feature specs used to not propagate; that is what the log is for.

---

## Stack (LOCKED)

- **Python 3.14.4** in `backend/.venv` (`requires-python >=3.11`)
- fastapi 0.140.13 · sqlalchemy 2.0.51 · pydantic 2.13.4 · pytest 9.1.1 · alembic (frozen; not run
  at boot) · `psycopg[binary]` · weasyprint · boto3 · httpx · rapidfuzz · `PyJWT[crypto]`
- **Node 24.14.1 / npm 11.13.0**
- React 18.3.1 · Vite 8.1.1 · TypeScript ~6.0.2 · vitest 4.1.10 · zustand 5 · oxlint
- Postgres 16 + pgvector · MinIO (local object store) · Alembic frozen at **0010** (history
  only) · **Drizzle migrates** (`web/drizzle`, D58)
- Migrating to Next.js 16 / AI SDK 6 / Auth.js / Drizzle / Tailwind (M1–M5); Python is **retained**
  for analytics and PDF rendering. See `docs/superpowers/specs/2026-09-07-nextjs-migration-design.md`.

## Do NOT install / do NOT propose

- **LLM-generated SQL.** Every query derives from the certified ontology. A guarded, additive
  text-to-SQL skill is a documented future path, never a replacement. This is the core safety
  property of the whole design.
- **Microservices.** One modular backend. The coming Next.js + Python split is two runtimes with a
  stated contract, not a licence to keep splitting.
- **Snowflake native/hybrid tables for app state.** Rejected in D20 and still rejected — they fork
  the RLS + pgvector + JSONB schema for zero functional gain. App state is Postgres everywhere.
- **Cortex via the SQL `COMPLETE` function with emulated tools.** D40 chose the Cortex REST
  Messages endpoint with native tool calling.

## Commands — verified 2026-10-23 (Phase 17 PR C), copy-pasteable

```bash
# Offline Python suite -> 2462 passed, 15 skipped, 142 deselected (~250s)
cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest \
  -p no:cacheprovider \
  -m 'not pg and not minio and not pdf and not router_live and not research_live'

# Postgres-backed suite (needs the compose `db` service up; DATABASE_URL points at it, e.g.
# postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon when the compose db is started with a port override; the tracked compose file maps 5432) -> 355 passed, 1 skipped
# (Drizzle-only database; Alembic no longer owns this schema, D58)
cd backend && .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg

# Python lint -> clean today
cd backend && .venv/Scripts/python.exe -m ruff check .

# Regenerate the turn-contract corpus (contracts/turns/) -- only with a reviewed diff in the
# same commit; see contracts/turns/README.md
cd backend && POSEIDON_UPDATE_CONTRACTS=1 .venv/Scripts/python.exe -B -m pytest \
  -p no:cacheprovider tests/contracts -q

# Frontend (Vite, untouched by PR A2, PR B and PR C) -> 171 passed, 18 files; lint clean
cd frontend && npm test -- --run
cd frontend && npm run lint

# Web (Next.js) -> 204 passed, 14 skipped, offline (27 files passed, 1 skipped; verified 2026-10-23 (UI pass PR 1); db-backed files
# skip without DRIZZLE_DATABASE_URL). The web package has NO lint script: its static gate is tsc.
cd web && npm test
cd web && npx tsc --noEmit             # clean

# Run the whole app locally (Drizzle migrate + synthetic seed + uvicorn, then Vite). Compose sets
# POSEIDON_INTERNAL_TOKEN=dev-internal-token for the backend; a host-run Next dev server
# (web/README.md) must export the SAME value to reach the internal contract.
docker compose -f infra/docker-compose.yml up      # backend :8000, frontend :5173

# Migrations (compose's `migrate` service runs this on boot; Drizzle is the authority since Phase 16)
cd web && npx drizzle-kit migrate      # reads DRIZZLE_DATABASE_URL
# Fresh local database after the Phase 16 cutover: docker compose -f infra/docker-compose.yml down -v
```

**Figures verified 2026-10-23 (Phase 17 PR C), each run once, to its own file, never
piped:** offline Python 2462 passed, 15 skipped, 142 deselected (136 plus the four new `router_live` tests); `ruff check` clean;
`tests/contracts` 9 passed (`contracts/` unchanged by PR C; `git status --short contracts/` empty); pg suite 355 passed, 1
skipped, run serially against the compose `db` service (host port 5434 when the compose db is started with a port override; the tracked compose file maps 5432).
Web `npm test` 204 passed, 14 skipped offline (verified 2026-10-23 (UI pass PR 1)), web `npx tsc --noEmit` clean, Vite frontend 171 passed in 18 files and `npm run lint` clean:
the Vite figures carried from PR B, not re-run in PR C or UI pass PR 1 (the Vite app is untouched by both). The web package defines no lint script,
so there is no web lint figure.

**CI: there is none.** No `.github/workflows`. Nothing gates a merge except Carlos, so the suites
above *are* the gate.

## Gotchas — every one of these cost a real debugging session

- **`-m router_live` is never bare.** The live routing-case executor in `tests/test_llm_loop.py` is
  hard-wired to Bedrock and runs the thirteen routing cases, plus the Bedrock smoke, whenever
  `AWS_ACCESS_KEY_ID` or `AWS_PROFILE` is set, whatever `LLM_PROFILE` says, plus the anthropic and cortex
  smokes when their variables are set; this shell has AWS credentials. A bare marker
  run is a paid Bedrock run. Always file-scope it: `-m router_live tests/test_llm_anthropic.py` (needs
  `ANTHROPIC_API_KEY`; expected pass line `3 passed`) or `-m router_live tests/test_llm_cortex.py` (needs `SNOWFLAKE_ACCOUNT_URL` and a token).
- **`group_by` is a list now** (Phase 17 PR B). The dev router and the stub scripts may still send a
  string, which `Args` wraps.
- **Always spell out `.venv/Scripts/python.exe`. Never a bare `python`.** A bare `python` resolves
  to a *different* global 3.14.4 (`AppData/Local/Python/pythoncore-3.14-64`) with `VIRTUAL_ENV`
  unset. That global has `fastapi` and `pytest 9.0.3` but **no `sqlalchemy`** — so it imports
  partway and dies with a confusing `ModuleNotFoundError` rather than a clear wrong-environment
  error. The venv has pytest **9.1.1**. Two importable environments, different versions.
- **Never run `ruff format` repo-wide.** `ruff check` is clean, but `ruff format` would rewrite
  **43 files** — a diff unrelated to any task, buried in the review.
- **Never pipe pytest.** `pytest ... | tail` reports **exit code 0 on a failing run** — the pipe
  masks it. Redirect to a file and tail the file. This has masked a real failure here before.
- **Always use the full marker exclusion** (`pg`, `minio`, `pdf`, `router_live`, `research_live`)
  and `env -u PERPLEXITY_API_KEY`. An incomplete exclusion has fired **real, paid Bedrock calls**.
- **`POSEIDON_ENV_FILE: ""` in compose is deliberate** — it makes the backend read no dotenv, so a
  host `backend/.env` bind-mounted at `/app` cannot shadow compose's values.
- **No `.gitattributes` exists.** A fresh clone gives the 6 tracked `infra/aws/*.sh` files CRLF
  endings and bash rejects them. One-line fix when someone wants it: `*.sh text eol=lf`.
- **Windows CRLF in shell scripts:** `aws.exe` emits CRLF. `$(...)` strips the CR under MSYS but
  `mapfile < <(...)` does **not** — it lands on the last array element and fails silently.
- **Never parallelise the `-m pg` suite.** `test_migrations.py`'s two Alembic round-trips revoke
  and restore `poseidon_app`'s cluster-wide membership mid-run; a concurrent test would see
  `SET LOCAL ROLE` fail, and a crash between revoke and restore leaves the shared dev database
  unable to switch role until `upgrade head` is re-run.
- **The offline run excludes `-m pg`, so a green offline suite does not cover chat output text.**
  Any change to chips, clarify text or part payloads must also run the pg suite. On 2026-10-12 a
  chip-text change left `test_live_chat_sse.py` failing on main until the pg suite was run.
- **WeasyPrint on the host.** `render_pdf` raises `OSError` without Pango/Cairo; the reporting skill
  records `PDF not rendered: OSError` and stores HTML only. Run the `pdf`-marked tests and the
  real PDF check in the container.

## Frozen surfaces — call them, never change them

- **The certified ontology and metric formulas.** `ontology/ontology.yml` is vendored from the WFS
  workspace and changes only through certification. The six certified metrics and the query
  builder's SQL construction produce the numbers the sales team acts on.
- **Migrations 0001–0010 (Alembic) are frozen history.** Never edited, never deleted, never run at
  boot. 0009 and 0010 encode the `poseidon_worker` role and `poseidon_app` membership that exist
  because RDS has no superuser. New schema changes are Drizzle migrations under `web/drizzle/`
  (D58).
- **The SSE envelope and message-part contract.** Frame kinds
  (`accepted`/`phase`/`tool`/`part`/`token`/`done`/`error`) carrying `turn_id`/`message_id`/
  `event_seq`, and the ten part kinds. The renderer registry and run-log reconciliation both key
  off these; changing the shape breaks replay. `LLMResponse` grew a defaulted `reasoning` field in
  PR C (D80); its five original fields and `LLMRecord` are unchanged.
- **The `contracts/turns` corpus.** Golden frame/part sequences pinned on both sides; a change
  lands only with `POSEIDON_UPDATE_CONTRACTS=1` regeneration in the same commit and a reviewer
  reading the diff.
- **Identity subs and the RLS wiring.** Provider-prefixed subs (`dev|`, `sf|`, `auth0|`),
  `rls_transaction`'s `set_config('app.user_sub', ..., true)` and `SET LOCAL ROLE poseidon_app`.
  Every persisted row keys on the sub; the trailing `true` (`is_local`) is what stops identity
  leaking across a pooled connection.

## Conventions

- **Folder law:** `backend/poseidon/tasks/<task>/skills/<skill>/` with tools, subskills and tests
  co-located. **Exception (D46):** prompts move to one canonical top-level `prompts/` tree with XML
  system prompts; skills resolve them by id through `PromptRegistry`.
- Tests ship inside the phase that introduces the behaviour. Never deferred, never gitignored.
- `.superpowers/` is scratch and already self-ignored (`.superpowers/sdd/.gitignore` is `*`).
  Ledgers are kept on disk after a phase closes; they are not tracked.
- Adding a table is **not** just certification — it takes five Python edits, the decisive one being
  the closed `entity: Literal[...]` in `metric_query/schema.py`. See
  `docs/superpowers/specs/2026-09-07-table-onboarding-review.md`.
- **Internal skills.** `SKILL_META['internal'] = True` registers a skill the router never sees
  (`router_skill_ids`, `tool_schemas`, `/api/skills` all omit it) while `dispatch` still runs it.
  `reporting.monthly_performance` is the first.
- **LLM profiles.** `LLM_PROFILE` is `bedrock | anthropic | cortex` (D68); the `anthropic` and
  `cortex` profiles are on Claude 5.5 with model-specific params in `models.yml`, never in provider
  code (D79).

## Blast radius

**Nothing is deployed.** EC2 is parked, SPCS is not up, and no email reaches anyone. Breakage is
cheap right now — but the frozen surfaces above are frozen because the certified layer took real
work to certify, not because of production risk.

## Git

- Feature branch → PR → **Carlos merges in the GitHub UI himself**. Never push to `main`.
- Never `--no-verify`, never disable signing, never bypass a hook. If a hook fails, fix the cause.
- `gh pr create` and other external writes only when explicitly authorised.

---

## How work runs here

1. `superpowers:brainstorming` — shape a feature before any code.
2. `superpowers:writing-plans` — turn the shape into a task-by-task plan under
   `docs/superpowers/plans/`. **Present it and stop; Carlos gives the go before execution.**
3. `superpowers:subagent-driven-development` — one implementer per task, a fresh reviewer per diff,
   fix rounds until clean.

**The two rules that make the reviews mean anything:**

- **The implementer runs the tests, and its report is the evidence.** TDD: it must show the test
  failing first and explain why that failure was the right one.
- **The reviewer is forbidden from re-running the suite.** It judges the code and checks the
  implementer's claims against the diff. This is what stops the two of them laundering a green
  suite between each other.
