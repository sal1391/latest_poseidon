# Phase 16 — chat parity: streaming through Next.js, Drizzle as migration authority

Date: 2026-09-19
Status: **Owner-approved design (brainstorm rounds 1 and 2, 2026-09-19).** Implementation plan not
yet written; no code is to be written from this document until a plan is presented and passed.
Revises: `2026-09-07-nextjs-migration-design.md` §3 (orchestration ownership rows) and §7 (route
spelling). Builds on: Phase 15 (`docs/superpowers/plans/2026-09-07-phase-15-migration-foundation.md`,
merged as PR #4) and its "Carried into Phase 16" list.

## 1. What this phase delivers, in one paragraph

A person opens the Next.js app, creates a conversation, sends a turn, and watches it stream: text,
metric grids, chips, tables, proof blocks, phase sections, artifacts, tool-step lines, inline
errors. Reloading shows the same parts from history. The Vite app keeps working beside it. Python
still runs every turn; Next.js re-streams it through AI SDK. Drizzle owns the application schema
from this phase on, proven by Python's own row-level-security suite passing against a database
Alembic never touched. Four hardening gaps from Phase 15 close first.

## 2. Decisions taken in brainstorming (2026-09-19)

| # | Decision | Consequence |
|---|---|---|
| P16-1 | Internal route auth is a **shared-secret header** checked by a FastAPI dependency. | New `POSEIDON_INTERNAL_TOKEN` setting in both runtimes. §5.2. |
| P16-2 | The Snowflake email stored procedure is **owned by a Snowflake colleague; not ready**. | The Phase 15 Task 7 placeholder stays unwired. Its three open questions travel with the procedure. |
| P16-3 | R16 (unwired `auth0` → 500) and R18 (newest-first, cap 50) **stay as shipped**. | No task. |
| P16-4 | Next.js pages and the chat route **require `Poseidon:Sales`**. | §7.5. |
| P16-5 | Internal route spelling is **`/dispatch`** (slash). | The migration spec §7 is edited to match the code. |
| P16-6 | **Full parity** per the migration spec's Phase 16 row. | Every part kind, tool steps, run log correlation, replay contract test. |
| P16-7 | **Bridge**: Python keeps the turn loop; Next.js re-streams it. | The "orchestration → Next.js" ownership rows are deferred, recorded as a decision-log revision. §3, §4. |
| P16-8 | Drizzle handover is **one hand-written baseline** re-derived from Alembic 0003–0010. | §8. |
| P16-9 | Chat UI is **functional, lightly styled** Tailwind. | Behaviour parity, not a pixel port of the Vite theme. |
| P16-10 | Reconnect-on-drop reconcile is **out**. | Carried to the parity follow-up list. §13. |
| P16-11 | **Alembic retires from the boot command.** Files 0001–0010 stay untouched as frozen history. | The synthetic schema (0002) is created idempotently by the seed path instead. §8.5. |

## 3. Why the bridge, not the port

The migration spec's ownership table says turn orchestration moves to Next.js + AI SDK, "replaced,
not ported". Exploration on 2026-09-19 showed what that would actually cost in this phase:

- Local development runs the LLM in stub mode through `DevDeterministicRouter`, a Python scripted
  router registered as the "stub" provider (`api/app.py:560`). Moving the loop means porting that
  router to TypeScript or losing offline development.
- `send_message` (`api/live_chat.py:751+`) is not a thin wrapper over `execute_turn`. It owns the
  user-message append, the data-backend guard, title-at-turn-one, transcript folding, and the
  persist-before-terminal-frame rule that closes a real race. All of that would move with it.
- Run log, history, slots and memory-outbox writes would move to Drizzle, and every pinned
  assertion in `test_chat_orchestrator.py` and `test_live_chat_sse.py` would need re-pinning in
  TypeScript before the port could be trusted.
- There is no recorded-turn fixture corpus in the repository; the replay contract test has to be
  built from scratch either way.

The bridge delivers the phase gate (a real turn from the Next app, identical frame sequences)
without betting the phase on rewriting the one part of the system that already works. The port
remains on the roadmap; it now has a contract test and a fixture corpus waiting for it.

**Decision-log entry to add (D57):** Phase 16 keeps turn orchestration in Python behind the
internal contract; Next.js owns transport, client and rendering. Revises migration spec §3's
"Turn orchestration" and "Model providers, agent loop" rows from "Next.js + AI SDK" to "Python
(Phase 16); Next.js + AI SDK deferred, entry point is the `contracts/turns` corpus".

## 4. Architecture

```
browser --HTTP--> Next.js (web/)                             Python (backend/)
                  proxy.ts resolves identity                 identity middleware (public routes)
                  /            conversation list  -Drizzle-+
                  /c/[id]      history + composer -Drizzle-+-- Postgres (RLS on app.user_sub)
                  POST chat route handler                   |
                     | X-Poseidon-Internal-Token, X-Trace-Id|
                     +--POST /internal/v1/conversations/{cid}/turns --> shared turn handler
                           <-- SSE envelope frames (unchanged) --     execute_turn, run log,
                     translator: frame -> UI-message chunk            history, slots, outbox
                     <-- AI SDK UI message stream -- useChat
```

Three new pieces on the Next.js side (chat page, route handler, translator), one new route on the
Python side (internal turns), and one shared handler body both Python routes call. Nothing about
the SSE envelope, the part kinds, the run log or RLS changes. The Vite app's public route is
untouched.

## 5. The internal contract, v1

### 5.1 Routes

| Route | Body | Response |
|---|---|---|
| `POST /internal/v1/skills/{skill_id}/dispatch` (exists) | `{args, identity: {sub, roles}}` | `SkillResult` JSON, HTTP 200 |
| `POST /internal/v1/conversations/{cid}/turns` (new) | `{identity: {sub, roles}, text, client_turn_key}` | `text/event-stream` of the existing envelope frames |

The turns route is the public `POST /api/conversations/{cid}/messages` with identity taken from
the body instead of the request. Its handler body is **extracted once** into a shared function
(working name `stream_turn(cid, text, client_turn_key, user, app_state)`) that both routes call.
The public route keeps `Depends(require_sales)` and the rate limiter on `request.state.user`; the
internal route builds a `UserContext` from the body, applies the same Sales check to it (403 with
the same problem body), and keys the rate limiter on the body's sub. Same 404 for an unknown or
foreign conversation. Same data-backend guard. Same frames, byte for byte.

Both internal routes carry `include_in_schema=False`. `test_internal_dispatch.py`'s OpenAPI
assertion flips to assert absence.

### 5.2 Authentication

- Setting `internal_token: str | None = None` (`POSEIDON_INTERNAL_TOKEN`) on Python's `Settings`;
  the same `POSEIDON_INTERNAL_TOKEN` name read per call on the web side, next to `ANALYTICS_ORIGIN` (one name for one value, per ruling R26).
- FastAPI dependency `require_internal_token`: header `X-Poseidon-Internal-Token` compared with
  `secrets.compare_digest` against the setting. Missing or wrong header → 401 problem body. Setting
  unset → **503** "internal contract not configured" for every internal route, never a pass-through.
- No default value in either runtime. Compose injects one dev value into `backend`; the web README
  names the same value for the host-run Next app. Tests set it explicitly.
- The web client throws before sending when its own token is unset, with a message naming the
  variable, so a misconfigured Next app fails on the first turn rather than on a 503 it cannot
  explain.

### 5.3 Headers that cross

| Header | Direction | Purpose |
|---|---|---|
| `X-Poseidon-Internal-Token` | Next → Python | §5.2 |
| `X-Trace-Id` | Next → Python | Python's `core/obs.py` trace contextvar adopts it; Next mints a uuid per request when absent. Phase 15 did not add this; the migration spec §7 requires it. |
| `content-type: application/json` | Next → Python | as today |

### 5.4 Errors

Non-2xx from Python before the stream starts (401, 403, 404, 422, 503) surfaces to the browser as
the same status with Python's problem body. A failure after the stream has started is a Python
`error` frame, translated per §6. A transport failure mid-stream (Python process gone) ends the
AI SDK stream with an `error` chunk; the client shows an inline error and the persisted message
appears on reload. Reconcile is out (P16-10).

## 6. Frame translation

The translator is a pure function from one envelope frame to zero or more AI SDK UI-message chunks,
with a small amount of per-turn state (whether `text-start` has been written). It lives at
`web/src/lib/turn-stream.ts` and is the thing the contract test pins.

| Python frame | Chunk(s) written | Notes |
|---|---|---|
| `accepted {turn_index}` | `start` with `messageId` = envelope `message_id`, metadata `{turn_id, turn_index}` | first frame of every turn |
| `phase {phase, status}` | `data-phase` with `id` = phase name, data `{phase, status}` | id reuse means a status change replaces, not appends |
| `tool {tool_seq, tool, server, status, label}` | `data-tool_event` with `id` = `tool-<tool_seq>`, data = payload verbatim | `start` then `done`/`error` reconcile into one part; `tool_seq` stays on the part, which is the explicit correlation the spec demands |
| `part {kind, payload}` | `data-<kind>` with data = payload verbatim, no id | append semantics, same as today |
| `token {text}` | `text-start` (once), `text-delta {delta: text}`; `text-end` at `done` | Python emits one token frame per turn today; the translator handles many |
| `done {usage, title?}` | `text-end` if a text was open, then `finish` with metadata `{status: "ok", usage, title}` | `title` is the title-at-turn-one injection the public route already does |
| `error {code, message}` | `data-error` with data = payload, then `finish` with metadata `{status: "error"}` | no `done` follows an `error` on the Python side, so the translator closes the message itself |

Rules:

- The envelope's `event_seq` is not forwarded to the browser. The contract test asserts order.
- AI SDK's native tool-part lifecycle (`tool-input-start`, `tool-output-available`) is **not**
  used. Those parts model tools the SDK executes. Ours are Python's, so they travel as data parts.
- The ten part kinds (`text`, `chips`, `tool_event`, `error`, `table`, `proof`, `metric_grid`,
  `artifact`, `phase_section`, plus the fallback) map to `data-<kind>`. A `part` of kind `text`
  becomes `data-text`, distinct from the streamed text; the adapter in §7.2 collapses both to
  `{kind: "text", payload}` so the renderer sees one shape.
- The translator never inspects payloads. A new part kind in Python appears in the browser as a
  fallback-rendered part with no code change here.

## 7. The Next.js client

### 7.1 Pages and handler

- `/` — conversation list (exists). Gains a "New conversation" action (§7.4).
- `/c/[id]` — history rendered through the registry (replaces Phase 15's raw JSON) plus the
  composer and live transcript. Client component for the streaming part, server component for the
  history read.
- A chat route handler (working path `POST /api/chat/[cid]`). Reads identity from the proxy
  headers, requires Sales, calls Python (§5), pipes through the translator into
  `createUIMessageStream`, returns `createUIMessageStreamResponse`. Note: the existing
  `next.config.ts` rewrite sends `/api/*` to Python; this handler must be excluded from that
  rewrite or mounted under a path the rewrite does not cover. The plan decides which; the spec
  requires that no browser request ever reaches Python directly for a chat turn.

### 7.2 Adapter and registry

- `toMessageParts(uiMessage)`: maps each UI part to Poseidon's `{kind, payload}`. `text` UI parts
  → `{kind: "text", payload: {markdown}}`; `data-<kind>` → `{kind, payload: data}`. Same output
  shape as the `messages.parts` column, so history and live transcript render through one path.
- `web/src/ui/message-parts/registry.tsx`: a port of the Vite registry keyed on `kind`, nine
  renderers plus fallback, Tailwind, no theme tokens. Chips call `onChipSelect(send_text ?? label)`
  and are disabled while a turn streams. Artifact renders a plain anchor (migration spec §5, C08
  downgraded under M5). Tool events render as one line per `tool_seq` with status.

### 7.3 useChat wiring

`useChat` with a custom transport pointing at the route handler, `id` = conversation id, initial
messages from history. Sending a chip's `send_text` is a normal send. A `client_turn_key` (uuid)
is minted per send and reused on the SDK's own retry so Python's idempotency check holds.

### 7.4 Conversation creation

A Drizzle insert inside `withUser`, mirroring `HistoryStore.create_conversation`: uuid7 id, title
"New chat", mode `default`, empty state, and the opener assistant message with the same opener
parts Python writes. The opener parts are copied from Python's fixture, and a test asserts the two
runtimes' opener parts are equal, so a change on one side fails on the other.

### 7.5 Sales gate

`requireSales(headers)` beside `requireSub` reads `x-poseidon-roles` (set by `proxy.ts`), splits on
`,`, requires `Poseidon:Sales`. Pages call it and render a 403 page on failure; the route handler
returns a 403 problem body. Python's `require_sales` remains the reference for the role string.

## 8. Drizzle as migration authority

### 8.1 What the corrected `schema.ts` must contain

Derived from the Alembic files, never from the Phase 15 introspection:

- Tables from 0003–0008 with their CHECK constraints and foreign keys (`ondelete` preserved).
- Every index exactly as created (no operator classes; none exist in 0003–0010).
- Fourteen policies: nine `<table>_owner` with `USING` and `WITH CHECK` both
  `user_sub = current_setting('app.user_sub', true)`; `turn_run_admin_read`,
  `llm_calls_admin_read`, `tool_calls_admin_read`, `message_feedback_admin_read` as
  `FOR SELECT TO poseidon_admin USING (true)`; `memory_outbox_worker` as
  `TO poseidon_worker USING (true) WITH CHECK (true)`.
- Roles `poseidon_app`, `poseidon_admin`, `poseidon_worker` declared as existing.
- The `user_profile.system_instruction` default `''` written correctly.

The header changes from "generated, do not edit" to "the source; verified against migrations
0003–0010 on <date>; the pg suite is the tripwire".

### 8.2 Baseline generation

1. `drizzle-kit generate` from the corrected schema → `web/drizzle/0000_baseline.sql` and the
   snapshot/journal.
2. A custom migration (`drizzle-kit generate --custom`) `0001_roles_grants_force_rls.sql` carrying,
   in this order: idempotent role creation via `DO $$ ... $$` blocks (roles are cluster-wide;
   `CREATE ROLE` fails when a sibling database already made one); `ENABLE` and `FORCE ROW LEVEL
   SECURITY` on all nine RLS tables; every `GRANT` from 0004–0009 verbatim (including the
   deliberate absence of `DELETE` on the audit tables and of `UPDATE` on `user_memory`);
   `GRANT poseidon_worker TO CURRENT_USER` and `GRANT poseidon_app TO CURRENT_USER`.
3. Policies live in the generated SQL because Drizzle can express them; if generation drops any
   predicate, the policy moves to the custom file and the header records why.

### 8.3 Proof

Fresh empty Postgres → `drizzle-kit migrate` → the seed path (§8.5) → Python's `-m pg` suite,
including every test in `test_rls_policies.py`, green **with Alembic never having run**. That suite
already asserts role existence without `BYPASSRLS`, exact DML privileges per table, policy
existence with the pinned predicate, RLS enabled and forced, and pooled-connection isolation. It is
the schema-as-migrated tripwire the migration spec §8 asked for.

### 8.4 Web-side tripwire

A vitest test runs `drizzle-kit generate` against the checked-in schema into a temporary directory
and asserts it produces no new migration. Drift between `schema.ts` and the journal fails here.

### 8.5 Boot order and Alembic

- Compose: a one-shot `migrate` service (Node image, `web/` mounted, `npx drizzle-kit migrate`)
  that `backend` and `worker` wait on with `service_completed_successfully`. `backend`'s command
  drops `python -m alembic upgrade head`.
- The synthetic schema (migration 0002: `CREATE SCHEMA synthetic`, two tables, five indexes) is
  created by a new idempotent `ensure_synthetic_schema(engine)` in the seed module, using
  `IF NOT EXISTS` throughout, called by `seed_synthetic` before it loads rows. Python owns that
  schema alone, which is exactly the case Q3 left with Python.
- Alembic files 0001–0010 and `alembic.ini` stay untouched. The `alembic_version` table no longer
  exists on fresh databases; grep confirms nothing reads it. The three boot-hint strings in
  `db.py` that say "run alembic upgrade head" change to name the Drizzle command.
- Local databases created under Alembic are recreated once: `docker compose down -v`. Nothing is
  deployed, so this is the entire cutover procedure, and the web README says so.

### 8.6 Decision-log entry to add (D58)

Drizzle is the migration authority for application tables from Phase 16; Alembic no longer runs
at boot; 0001–0010 are frozen history; the synthetic schema is created by the seed path; the pg
suite against a Drizzle-migrated database is the drift tripwire.

## 9. Hardening carry-overs (the first tasks)

| Item | Where | Behaviour |
|---|---|---|
| Route auth (R25) | `api/internal.py`, `core/config.py`, `web/src/lib/skills.ts` | §5.2. Plus `include_in_schema=False`; OpenAPI test asserts absence. |
| Web boot probe (R21) | `web/src/instrumentation.ts` calling `assertBootPrivileges` in `web/src/db/client.ts` | Query `pg_roles` for the connection role's `rolsuper`/`rolbypassrls`; if either is true and the app role is unset, throw with the same message shape as Python's. If the app role is set, rehearse `SET LOCAL ROLE` in a transaction that rolls back. Unreachable database → log `boot_privileges_unchecked` and continue. |
| Data-client factory (R24) | new `core/data/factory.py` `build_data_client(settings)` | Returns `SyntheticDataClient` for `synthetic`; raises `DataBackendUnavailable` for anything else. `api/internal.py`, `api/dev_runner.py` and `api/app.py` construct through it. `dev_runner` keeps its structured 501 `SkillResult`; the internal routes return a 501 problem. |
| Sales gate (P16-4) | `web/src/lib/request-identity.ts` | §7.5. |

## 10. Configuration inventory

| Variable | Runtime | Default | Notes |
|---|---|---|---|
| `POSEIDON_INTERNAL_TOKEN` | Python | none → internal routes 503 | compose sets a dev value |
| `POSEIDON_INTERNAL_TOKEN` | Next | none → client throws | same name as Python; README names the compose value |
| `ANALYTICS_ORIGIN` / `BACKEND_ORIGIN` | Next | `http://localhost:8000` | unchanged (R26) |
| `ANALYTICS_TIMEOUT_MS` | Next | 30000 | dispatch only; the turn stream uses no overall timeout, only an idle timeout of 120 s between frames |
| `DRIZZLE_DATABASE_URL` | Next, migrate service | `...localhost:5432/poseidon` | unchanged |
| `DATABASE_APP_ROLE` | Next | `poseidon_app` | now enforced by the boot probe |

## 11. The contract test corpus

- New top-level `contracts/turns/` (precedent: `ontology/`, `prompts/` are shared top-level trees).
- A Python recorder (`backend/tests/contracts/record_turns.py`, run via
  `.venv/Scripts/python.exe -m tests.contracts.record_turns`) drives the pinned flagship scenarios
  from `test_chat_orchestrator.py` / `test_live_chat_sse.py` through the shared turn handler with
  the stub router and writes, per scenario, `<name>.frames.json` (ordered envelope frames) and
  `<name>.parts.json` (the assistant message parts Python persisted). Ids and timestamps are
  normalised to fixed placeholders so fixtures are stable.
- Python golden test: re-run each scenario and compare to the committed fixtures. Regeneration is
  explicit (`POSEIDON_UPDATE_CONTRACTS=1`), so an orchestrator change must update the corpus on
  purpose and the diff is reviewable.
- Vitest contract test: feed `<name>.frames.json` through the translator and AI SDK's stream
  processing, run the adapter, assert the parts equal `<name>.parts.json` in order, payload for
  payload. The metadata (`turn_id`, `turn_index`, `status`, `usage`) is asserted separately.
- Scenarios, minimum: metric query with a metric grid and proof; customer-ambiguous clarify with
  chips; an existing-customer brief with phase sections, tool steps and an artifact; a
  duplicate-turn replay; a mid-turn structured error.

## 12. Phase gate

**Automated:** Python offline suite, ruff, pg suite against a Drizzle-only database, web offline
and live suites, the contract test on both sides, all green. Counts recorded in the morning-review
file.

**What Carlos clicks:** fresh database from `docker compose down -v` then `up`. Open the Next app.
Create a conversation. Send a metric comparison and watch the metric grid and proof stream in.
Send an ambiguous customer name and click a chip. Start an existing-customer brief and watch the
tool-step lines, phase sections and the artifact link. Reload the page and see the same parts from
history. Open the Vite app and send one turn there too. Open the Next app as a user without the
Sales role (the README documents how) and see a 403 page.

**What that proves:** the internal contract is authenticated, Python still owns the turn, every
part kind survives translation, history and live transcript agree, RLS and roles survived the
migration handover, and the old app is unaffected.

## 13. Out of scope, carried forward

- Reconnect-on-drop reconcile in Next (P16-10).
- Feedback, settings, conversation delete, the skills picker in Next.
- The orchestration port to AI SDK (D57); entry point is the `contracts/turns` corpus.
- Reports (Phase 17), the email stored procedure (P16-2), Cortex, SPCS, Snowflake data client
  (Phase 19).
- Visual polish and theme port.
- The `npm audit` moderate findings from drizzle-kit (unchanged, dev-only).

## 14. Documents this phase edits

- Migration spec §3 ownership rows and §7 route spelling (P16-5, D57).
- `docs/architecture/00-overview.md` decision log: D57, D58, and D59 (internal contract auth).
- `CLAUDE.md`: stack line (Alembic "at revision 0010" → "frozen at 0010; Drizzle migrates"),
  commands (Drizzle migrate, contract recorder), Frozen surfaces (add: the `contracts/turns`
  corpus changes only with a regenerated fixture in the same commit).
- `web/README.md`: token, migrate, DB reset, the 403 walkthrough.
- `infra/docker-compose.yml`: migrate service, token, comment rewrite where it names Alembic.

## 15. Known unknowns for the plan writer

- Exact AI SDK 6 API names (`createUIMessageStream`, `createUIMessageStreamResponse`, the
  `start`/`finish` chunk metadata fields, custom transport shape) are to be verified against the
  installed version at plan time via context7, not taken from this document.
- Whether Next 16's `forbidden()` is stable enough for the 403 page or a plain rendered page is
  used. Either satisfies §7.5.
- Whether the `/api/*` rewrite is narrowed or the chat handler moves under `/chat/`. §7.1 states
  the invariant either must satisfy.
