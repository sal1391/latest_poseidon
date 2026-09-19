# Poseidon — repo working rules

Chat-first sales-intelligence app for marine fuel sales. Deterministic core: **Python computes,
the LLM only routes**. Solo developer, Windows 11 / PowerShell.

Architecture docs: `docs/architecture/00-08`. **`00-overview.md`'s decision log is the single
index of what was decided** — check it, and check for "revises", before treating any architecture
doc as current. Decisions taken in feature specs used to not propagate; that is what the log is for.

---

## Stack (LOCKED)

- **Python 3.14.4** in `backend/.venv` (`requires-python >=3.11`)
- fastapi 0.140.13 · sqlalchemy 2.0.51 · pydantic 2.13.4 · pytest 9.1.1 · alembic · `psycopg[binary]`
  · weasyprint · boto3 · rapidfuzz · `PyJWT[crypto]`
- **Node 24.14.1 / npm 11.13.0**
- React 18.3.1 · Vite 8.1.1 · TypeScript ~6.0.2 · vitest 4.1.10 · zustand 5 · oxlint
- Postgres 16 + pgvector · MinIO (local object store) · Alembic at revision **0010**
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

## Commands — verified 2026-09-08, copy-pasteable

```bash
# Offline Python suite -> 1679 passed, 13 skipped, 116 deselected (~131s)
cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest \
  -p no:cacheprovider \
  -m 'not pg and not minio and not pdf and not router_live and not research_live'

# Postgres-backed suite (needs the compose `db` service up)
cd backend && .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg

# Python lint -> clean today
cd backend && .venv/Scripts/python.exe -m ruff check .

# Frontend -> 171 passed, 18 files; lint clean
cd frontend && npm test -- --run
cd frontend && npm run lint

# Web (Next.js) -> 60 passed, 9 skipped, offline (db-backed files skip without DRIZZLE_DATABASE_URL)
cd web && npm test
cd web && npx tsc --noEmit

# Run the whole app locally (migrations + synthetic seed + uvicorn, then Vite)
docker compose -f infra/docker-compose.yml up      # backend :8000, frontend :5173

# Migrations (compose runs this on boot)
cd backend && .venv/Scripts/python.exe -m alembic upgrade head
```

Web (Next.js) suite verified 2026-09-18: `npm test` → 60 passed, 9 skipped (9 test files, 1 skipped
file); `npx tsc --noEmit` → clean.

**Unverified:** the `-m pg` baseline was not run on 2026-09-08 — Docker Desktop was not running.
The last recorded figure was 334 passed / 1 skipped. Treat it as stale until re-run.

**CI: there is none.** No `.github/workflows`. Nothing gates a merge except Carlos, so the suites
above *are* the gate.

## Gotchas — every one of these cost a real debugging session

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

## Frozen surfaces — call them, never change them

- **The certified ontology and metric formulas.** `ontology/ontology.yml` is vendored from the WFS
  workspace and changes only through certification. The six certified metrics and the query
  builder's SQL construction produce the numbers the sales team acts on.
- **Applied migrations 0001–0010.** Only ever add a new one. 0009 and 0010 encode the
  `poseidon_worker` role and `poseidon_app` membership that exist because RDS has no superuser.
- **The SSE envelope and message-part contract.** Frame kinds
  (`accepted`/`phase`/`tool`/`part`/`token`/`done`/`error`) carrying `turn_id`/`message_id`/
  `event_seq`, and the ten part kinds. The renderer registry and run-log reconciliation both key
  off these; changing the shape breaks replay.
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
