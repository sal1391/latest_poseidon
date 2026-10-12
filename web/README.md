# Poseidon — web (Next.js)

Next.js 16 application running beside the existing Vite app during the migration
(decision M1). It reads identity, conversations and messages under the same
Postgres row-level security the Python backend enforces. Python keeps every
skill (M2) — this app dispatches to it over an internal HTTP contract rather
than reimplementing anything.

## Running it

1. Bring up the database: `docker compose -f ../infra/docker-compose.yml up -d db`
2. Start the backend on :8000 (see the root `CLAUDE.md` for the compose command,
   or run it directly with `.venv/Scripts/python.exe`). For chat specifically, see
   **Running with the backend** below — it needs the full compose stack,
   `CHAT_MODE=live`, and a matching `POSEIDON_INTERNAL_TOKEN`.
3. `npm run dev` here — serves on **:3000**.
4. The existing Vite app keeps running separately on **:5173**; this app does
   not replace it yet.

**`DRIZZLE_DATABASE_URL` is required at runtime and has no default.** The app
refuses to start (`bootCheck` throws) and every database call refuses to run
when it is unset or blank (ruling P16-R14): a guessed `localhost:5432` can be
an unrelated project's Postgres on a developer machine, so the runtime refuses
to guess, exactly as `drizzle.config.ts` does. Set it explicitly to whichever
host port your compose `db` is published on (see below). Note this differs
from Python's `DATABASE_URL`, which compose sets as
`postgresql+psycopg://...`; `+psycopg` is SQLAlchemy syntax and Drizzle
rejects it.

**`DRIZZLE_DATABASE_URL` is required for every `drizzle-kit` command** —
`drizzle.config.ts` has no fallback URL and throws without it (ruling P16-R9),
because `npx drizzle-kit migrate` now writes to a real database.

### Running with the backend

Chat needs Python: `/api/chat/<id>` re-streams a turn from the internal
contract, and that contract is shared-secret authenticated. Bring up compose
(`docker compose -f ../infra/docker-compose.yml up`), which starts the backend
with `CHAT_MODE=live` and `POSEIDON_INTERNAL_TOKEN=dev-internal-token`, then
start this app with the **same** token:

```powershell
$env:POSEIDON_INTERNAL_TOKEN="dev-internal-token"
$env:DRIZZLE_DATABASE_URL="postgresql://poseidon:poseidon@localhost:5432/poseidon"
npm run dev
```

If another project holds 5432 on your machine, add a compose override that
republishes `db` on a free port and use that port here.

A mismatched or unset token fails on the first turn with the variable's name
in the message, before any request leaves this process.

`/api/chat/<id>` is answered **here**, and only because the `/api/*` rewrite
is a `fallback`. The array form of `rewrites()` is the `afterFiles` phase, and
Next applies afterFiles rewrites **before dynamic routes** — so as an array
the rewrite swallows `/api/chat/[cid]` and the handler never runs (observed
live: Python answered `{"detail":"Not Found"}`). Under `fallback` Next serves
the routes it defines and anything else under `/api/` falls through to Python,
so Python's access log shows only `/internal/v1/...` for a turn. Do not
restore the array form.

### Checking the 403

Start the dev server in SPCS ingress mode with an allowlist that excludes the
caller, and confirm the proxy refuses before any page or handler runs:

```powershell
$env:IDENTITY_MODE="spcs_ingress"; $env:DEPLOY_MODE="spcs"; $env:SPCS_SALES_USERS="alice"
npm run dev
# in another shell:
curl -H "Sf-Context-Current-User: bob" http://localhost:3000/
```

`bob` is not in `SPCS_SALES_USERS`, so this returns **403** with the 403 page;
`alice` gets the app.

## Testing

`npm test` runs Vitest offline. Files that need a live database skip
themselves when `DRIZZLE_DATABASE_URL` is unset; set it to run them live
against the compose `db` service.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `IDENTITY_MODE` | `disabled` | `disabled` \| `spcs_ingress` \| `auth0` (auth0 not wired this phase) |
| `DEPLOY_MODE` | `local` | Must be `spcs` for the `spcs_ingress` identity header to be trusted |
| `SPCS_SALES_USERS` | `""` (empty) | Comma-separated allowlist for the `Poseidon:Sales` role; empty grants nobody, `*` grants everyone |
| `DRIZZLE_DATABASE_URL` | none (required) | Plain `postgresql://`, not Python's `+psycopg` DSN. No fallback: unset or blank makes boot and every query throw rather than guess `localhost:5432` (P16-R14) |
| `DATABASE_APP_ROLE` | `poseidon_app` | **Empty disables the role switch — and therefore RLS — when the DSN authenticates as a superuser.** |
| `BACKEND_ORIGIN` | `http://localhost:8000` | Target of the `/api/*` dev rewrite |
| `ANALYTICS_ORIGIN` | falls back to `BACKEND_ORIGIN`, then `http://localhost:8000` | Where skill dispatch is sent |
| `ANALYTICS_TIMEOUT_MS` | `30000` | Skill dispatch deadline |
| `POSEIDON_INTERNAL_TOKEN` | none (required) | Shared secret for the Python internal contract; must equal the backend's. Compose uses `dev-internal-token`. |
| `IDENTITY_EMAIL_SOURCE` | `entra` | `entra` \| `snowflake_proc` (placeholder — see below) |
| `SNOWFLAKE_EMAIL_PROC` | `""` (empty) | Placeholder only; the `snowflake_proc` source raises until the procedure code lands |

## Notes

- `dispatchSkill` (`src/lib/skills.ts`) has no call site yet — by design. Phase
  15 proved the seam live (Task 5); wiring a page to call it is later work.
- `src/db/schema.ts` is the hand-maintained migration SOURCE (Phase 16, D58):
  edit it, run `npx drizzle-kit generate`, commit the new migration. Never run
  `drizzle-kit pull` over it — introspection drops RLS predicates and rotates
  index operator classes (see the file header). `src/db/migrations.test.ts`
  fails if the schema and `drizzle/` disagree.
