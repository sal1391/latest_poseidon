# Application stack migration: Next.js 16 + AI SDK 6, Python retained for analytics

Date: 2026-09-07
Status: **Draft for owner review.** Nothing here is approved. Section 9 lists the decisions that
are the owner's, not mine; several later sections depend on them and say so where they do.
Addresses: Codex review finding **C00 (P1)** —
`docs/superpowers/specs/2026-09-05-monthly-performance-reports-codex-review.md`.

## 1. Why this document exists

The monthly-performance-reports design (`2026-09-04-...-design.md`) plans its Phases 15–18 on the
current stack: new FastAPI routes, a React Router split view inside the Vite SPA, Auth0 role work,
Alembic migrations, and extensions to the custom Python model loop. The owner has since confirmed a
migration to **Next.js 16 App Router, AI SDK 6, Tailwind CSS, PostgreSQL, Auth.js and Drizzle ORM**.

Following that phase table literally would spend four phases deepening seams the migration replaces.
This document reworks the architecture and phase dependencies around the target stack so an
implementation plan can be written against something current.

**Confirmed by the owner (2026-09-07), and the basis for everything below:**

| # | Decision |
|---|----------|
| M1 | The application layer migrates to Next.js 16 App Router / AI SDK 6 / Auth.js / Drizzle / Tailwind. |
| M2 | **Python is retained** as an internal service for deterministic analytics and PDF rendering. It is not rewritten in TypeScript. |
| M3 | The deployment target is **SPCS**, not EC2. Phase 14's remaining EC2 tasks (7–9) are parked, not abandoned. |
| M4 | The SPCS app database is managed **Snowflake Postgres** (D39), outside the service. |

The reasoning the owner accepted for M2: the certified financial calculations are the highest-risk
thing in the repository, and rewriting them in TypeScript means re-proving every figure cent-for-cent
against the old implementation. The migration's actual value — chat UI, streaming, sessions, auth,
tool orchestration — is all in the application layer. The arithmetic does not improve in another
language. Codex reached the same recommendation independently.

## 2. What exists today, by size

Measured 2026-09-07, excluding tests and `__pycache__`:

| Area | Lines | Nature |
|---|---:|---|
| `core/chat/` | 4,485 | orchestrator (1,615), dev_router (1,043), history (881), events (546), feedback, state |
| `tasks/` | 3,263 | the skills: metric_query, briefs, web_research |
| `api/` | 3,028 | FastAPI routes incl. `live_chat.py` (SSE), `me.py`, `turns.py`, `auth.py` |
| `scripts/` | 2,800 | memory worker, seeders, harvest/rollup tooling |
| `core/llm/` | 2,147 | provider contract, Bedrock, the agent loop |
| `core/parsing/` | 1,687 | period parser, customer resolver, carry semantics |
| `core/data/` | 871 | `DataClient`, query builder |
| `core/skills/` | 782 | registry, `SkillContext`/`SkillResult` |
| `core/` (top level) | 2,959 | `db.py` (643), `runlog.py` (617), identity (993 across three files), config, obs, artifacts |

Roughly 19,000 lines of Python, plus the React/Vite frontend. M2 means a large fraction of it stays.
The question this document has to answer precisely is *which* fraction — §3 and §9.

## 3. Seam-by-seam ownership map

This is the C00 "map each required technology and existing behavior to its target owner" table. Rows
marked **OWNER** are not settled; see §9.

| Seam | Today | Target owner | Notes |
|---|---|---|---|
| HTTP + routing | `api/app.py`, FastAPI | **Next.js** App Router | Public/authenticated/API boundaries in §5. |
| Chat streaming | `core/chat/events.py` `SseEnvelopeSink`, hand-rolled SSE envelopes | **Next.js** + AI SDK 6 | Contract translation in §4. The riskiest single item. |
| Turn orchestration | `core/chat/orchestrator.py` (1,615 lines) | **Next.js** + AI SDK | Replaced, not ported. |
| Model providers, agent loop | `core/llm/` | **Next.js** + AI SDK | Bedrock and Cortex both need AI SDK providers. §7. |
| Identity | `identity.py`, `identity_auth0.py`, `identity_spcs.py` | **Auth.js** | Session library ≠ identity provider. §6. |
| RLS enforcement | `db.py` `rls_transaction`, `set_config('app.user_sub', …, true)` + `SET LOCAL ROLE` | **Both** — shared discipline | Two clients now touch the same rows. §6. |
| Conversation history, slots | `core/chat/history.py`, `state.py` | **Next.js** + Drizzle | Explicit serializer at `history.py:836–869` must be ported deliberately, not regenerated. |
| Run log | `core/runlog.py` | **Next.js** + Drizzle | Written per turn by whoever orchestrates the turn. |
| Schema + migrations | Alembic, at revision 0010 | **OWNER** — one authority | Recommendation in §8. Codex: do not let both evolve the same tables. |
| Parsing / carry | `core/parsing/` (1,687 lines) | **OWNER** — genuinely ambiguous | §9 Q1. Not obviously "analytics"; not obviously app layer either. |
| Ontology + query builder | `core/ontology/`, `core/data/` | **Python** | Certified semantic layer. Stays, per M2. |
| Skills / deterministic math | `tasks/` | **Python** | The crown jewels. Stays, per M2. |
| Report rendering (PDF) | WeasyPrint | **Python** | Stays, per M2. Replacing it was the main cost of the all-TypeScript option. |
| Background work | `scripts/memory_worker.py`, the proposed report worker | **OWNER** | §9 Q2. Durable execution has to live somewhere. |
| Artifacts | `core/artifacts.py` (S3/MinIO) | **Split** | Brief PDFs keep the object store where one exists; report bytes go to Postgres (D39). |
| Frontend | React 18 + Vite + Zustand | **Next.js** + Tailwind | Renderer registry and the ten part kinds carry over conceptually. |

## 4. The streaming contract — the highest-risk seam

Today's wire format is a bespoke envelope. Every frame carries `turn_id`, `message_id` and a
monotonic `event_seq` (D26), and the frame kinds are:

| Frame | Payload | Purpose |
|---|---|---|
| `accepted` | `{turn_index}` | append pending assistant message |
| `phase` | `{phase, status}` | brief-flow phase progress |
| `tool` | `{tool_seq, tool, server, status, label}` | the visible "Calling Perplexity…" step lines |
| `part` | `{kind, payload}` | append/replace a structured part |
| `token` | `{text}` | append to the streaming text part |
| `done` | `{usage}` | finalize, refresh title |
| `error` | `{code, message}` | inline error card |

Ten structured part kinds ride on `part`: `text`, `chips`, `customer_picker`, `metric_grid`,
`table`, `phase_section`, `tool_event`, `artifact`, `proof`, `error` — and the reports design adds
an eleventh, `report`.

**The migration risk is not the transport, it is the semantics.** AI SDK 6 streams its own typed
message parts and tool-call lifecycle. Three things in the current contract have no automatic
counterpart:

1. **`tool_seq` correlation.** Each `tool` frame's `tool_seq` matches a `tool_calls` row in the run
   log (doc 06 §1). Reconciliation and replay (`GET /api/turns/{id}`) depend on that correspondence.
   AI SDK's tool-call ids are its own; the mapping must be explicit and recorded, or replay breaks.
2. **Incremental part streaming.** `SseEnvelopeSink` tracks `_streamed_counts` per `tool_seq` so a
   dispatch that already streamed parts live does not re-emit them at `tool_done`. That
   de-duplication is load-bearing and easy to lose.
3. **Custom part kinds.** `metric_grid`, `proof`, `phase_section` and `report` are domain parts, not
   text. They must travel as typed data parts, and the client renderer registry must key off the
   same discriminator it does today.

**Requirement:** before any UI work, build a contract test that replays recorded turns through both
implementations and asserts the same ordered sequence of (frame kind, part kind, payload) tuples.
The existing recorded fixtures make this cheap. Do not treat "it looks right in the browser" as
evidence — the de-duplication bug above is invisible until a specific dispatch shape occurs.

## 5. App Router boundaries

Proposed, subject to §9:

- **Public:** the login entry only.
- **Authenticated (server components):** conversation list, the report panel shell, settings. Server
  components read through Drizzle with the RLS discipline of §6.
- **Client components:** the composer, the streaming transcript, the renderer registry, chip
  interactions, the type-ahead customer picker. Anything that holds streaming state is a client
  component.
- **Route handlers (`app/api/*`):** the chat turn endpoint (streams), report byte routes, feedback,
  settings writes, dimension type-ahead.
- **Server actions:** settings and feedback writes are a reasonable fit; the chat turn is not (it
  streams and needs an explicit response contract).

**Report byte routes** must be authenticated fetch-and-download, not anchor navigation — Codex C08.
A plain `<a href>` does not carry the SPA's bearer token, and D39 puts report bytes behind an
authenticated API route in every habitat because SPCS has no object store to presign against. This
is already noted in doc 01 and doc 05 as of commit `1477d7b`.

## 6. Identity, sessions and RLS

**Auth.js is a session library; Auth0 is an identity provider. They are separate choices.** Codex
flagged this explicitly, and it matters: adopting Auth.js does not require dropping Auth0. Auth.js
can federate to Auth0 as an OIDC provider, which preserves the existing tenant, the
`Poseidon:Sales` / `Poseidon:ReportAdmin` role claims (D37), and the Action already configured
during the Phase 9 tenant day.

On SPCS the identity mode is `spcs_ingress` — the platform authenticates at the edge and forwards
`Sf-Context-Current-User`. That is a header-trust mode, and it must survive the migration as one of
the three `IDENTITY_MODE` values, not be collapsed into Auth.js's OAuth flow.

**Subs must not change.** Every persisted row is keyed on a provider-prefixed sub (`auth0|…`,
`sf|…`, `dev|…`). If Auth.js mints differently shaped session identifiers, existing conversation
ownership and RLS policies silently stop matching. **The migration must map the existing subject
format exactly**, and a test must prove an existing conversation is still readable by its owner
after cutover.

**RLS with two clients.** Today one Python client sets `app.user_sub` transaction-scoped and
`SET LOCAL ROLE poseidon_app` before touching user rows. After the migration, Next.js/Drizzle also
holds a connection to the same database. Both must observe the same discipline:

- Every Drizzle transaction touching user-scoped rows sets the GUC the same way, with `is_local`
  true so it cannot leak across a pooled connection.
- Neither connects as a superuser or a `BYPASSRLS` role. Phase 14 Task 3 already discovered RDS has
  no superuser and added the `poseidon_worker` role and boot privilege probe for exactly this class
  of failure; the same applies to Snowflake Postgres and to the new Drizzle connection.
- The Python service, when called by Next.js, must receive the caller's identity explicitly and
  apply it — it can no longer infer identity from its own request context, because the request now
  arrives from another service. §7.

## 7. The internal contract between Next.js and Python

This is the cost the owner accepted with M2. It needs to be explicit rather than incidental.

**Shape:** an authenticated, versioned HTTP contract. Next.js is the only caller. Python exposes
deterministic operations, not a general API.

**What crosses it:**
- `POST /internal/v1/skills/{skill_id}:dispatch` — validated args in, `SkillResult` out (parts,
  proof, payload). This is the existing `SkillResult` shape, so the seam is already the natural one.
- `POST /internal/v1/reports/{run_id}:render` — payload in, HTML/PDF bytes out.
- Read paths for ontology metadata and dimension type-ahead.

**Non-negotiables across the boundary:**
- **Identity is passed explicitly and enforced on the Python side.** A skill dispatch carries the
  caller's sub and roles; Python applies row scope from that, never from ambient state. The D16
  `row_scope` mechanism shipped in Phase 14 is the hook.
- **No model-authored SQL.** Unchanged. The model chooses argument values; Python builds queries
  from the certified ontology.
- **Deterministic math has one home.** Shared calculations must not be reimplemented in TypeScript
  "just for display" — that is how two answers to the same question appear.
- Timeouts, retries and trace-id propagation are specified, not left to defaults. `core/obs.py`
  already carries a trace-id contextvar; the header must cross the boundary.

**Deployment:** two containers in the SPCS service, `web` (Next.js) and `analytics` (Python),
talking over the service-internal network. Triton proves the single-container shape works; this is
that shape plus one sidecar. The public endpoint is Next.js.

## 8. Schema and migration ownership

Codex: *"select one migration authority for shared database tables rather than letting Alembic and
Drizzle evolve them independently."* Agreed — two migration tools against one schema is a
data-corruption path, not a style preference.

**Recommendation (not yet approved — §9 Q3): Drizzle becomes the single authority for application
tables; Python reads them through a generated, checked-in schema it does not migrate.**

Reasoning: the application tables (conversations, messages, state, run log, feedback, user memory,
and the new report tables) are overwhelmingly written by the app layer after this migration. Drizzle
owning them puts migration authority next to the code that changes most. Python keeps Alembic only
if it ends up owning tables nobody else touches — and if it owns none, Alembic retires.

**The counter-argument, honestly:** Alembic is at revision 0010 with real operational scars baked in
— migration 0009's `poseidon_worker` claim role and 0010's `poseidon_app` membership grant exist
because RDS has no superuser, and `assert_boot_privileges` probes for exactly that. Those are not
schema shapes Drizzle will reproduce by itself; they are grants and roles. Whatever authority is
chosen, **that privilege setup must be carried across deliberately and re-proven on Snowflake
Postgres**, which is separately flagged as unverified in doc 07 §4.

A drift tripwire test — schema-as-defined versus schema-as-migrated — should exist on whichever side
does not own migrations.

## 9. Decisions I am not making

These change the work materially and are the owner's call.

**Q1 — Where does the parsing pipeline live?** `core/parsing/` (1,687 lines) does period parsing,
carry semantics, and three-tier fuzzy customer resolution. It is deterministic Python, which argues
for keeping it. But it runs on *every* chat turn before routing, so keeping it in Python means a
network hop per turn, and its state (`ConversationSlots`) is persisted by the app layer, which would
then own the write but not the logic.
*Options:* (a) stays Python, called per turn; (b) moves to TypeScript with the app layer; (c) splits
— carry/slot state in TypeScript, the certified customer resolver in Python.
*My lean:* (a) initially, because C03 already shows the carry logic has live bugs and porting buggy
logic doubles the debugging surface. Revisit once C03 is fixed.

**Q2 — Where does background work run?** The memory worker exists today as a Python process; the
reports design adds a report worker. AI SDK and Next.js do not provide durable background execution.
*Options:* (a) both workers stay Python containers; (b) app-layer jobs move to a TypeScript worker,
Python keeps analytics jobs; (c) a queue product.
*My lean:* (a). It is the fewest moving parts, and `memory_worker.py`'s claim pattern is already
proven — though note C01 disputes the crash-cap claim made for it.

**Q3 — Migration authority: Drizzle or Alembic?** §8. My recommendation is Drizzle, with the
grants/roles caveat carried across deliberately.

**Q4 — Does Auth0 stay as the identity provider behind Auth.js?** §6. Keeping it preserves the
configured tenant, the role claims and the Phase 9 work. Dropping it means redoing that. Note the
trial tenant's expiry history — that is a separate operational question from the library choice.

**Q5 — Coexistence or hard cutover?** Does the Vite SPA keep serving while Next.js is built beside
it, or does the app go dark for a cutover? Coexistence costs a period of two frontends against one
backend; a hard cutover costs a window with nothing usable.
*My lean:* coexistence, because it lets each seam be proven against the running system.

## 10. Phase plan replacing the design's 15–18

Structured on Codex's advice to settle architecture first, then ship **one** reliable report →
grounded-follow-up workflow, and only then add email and the platform rollout. Numbering continues
from the reports design so cross-references stay meaningful.

| Phase | Deliverable | Gate |
|---|---|---|
| **15 — Migration foundation** | Next.js 16 app skeleton; Auth.js wired to the chosen provider (Q4) preserving existing subs; Drizzle schema generated from the current database with the authority decision (Q3) applied; the internal Python contract (§7) with one skill dispatched end to end. No new features. | Log in as an existing user and open an existing conversation with its history intact. Subject-format test green. |
| **16 — Chat parity** | The turn path on AI SDK: streaming, all eleven part kinds, tool-step visibility, the run log written per turn with `tool_seq` correlation preserved. | The §4 contract test: recorded turns replay through the new stack producing an identical ordered frame/part sequence. Existing router-decision suite green. |
| **17 — Report in chat** | The reporting task in Python (analytics, narrative, grounding check, HTML/PDF); the `report` part; report bytes in Postgres with the authenticated download path (C08); the office/month flow. **Depends on C03 being fixed first** — the year-window and comparison carry bug breaks this phase's own gate. | On synthetic data: generate a report in chat, every table total equals its KPI tile, ask three grounded follow-ups correctly, download the PDF after an app restart (C07). |
| **18 — Reports screen + email** | Definitions/runs/sends tables, the durable worker (Q2), ReportAdmin enforcement at dispatch (D37), the mailer with Graph on SPCS (D38), the split view. | New definition → generate → preview → send → open the email → land in the split view with the grounded conversation. |
| **19 — Platform** | Cortex over the AI SDK provider interface with parity test (D40); Snowflake data client; SPCS spec for two containers; Snowflake Postgres wiring with the backup guarantees **verified** against RPO 24h / RTO next business day; the Snowflake-side handoff docs. | Offline: parity and client tests green. Account-gated: `BUILD_SIGNOFF.md` on a Snowflake-connected session, including differential reconciliation against mom-comparison. |

Deliberately deferred: the supplier-perspective requirement (confirmed by the owner in the Codex
review) needs folding into the reporting contract — it belongs in Phase 17's design, and this
document does not yet specify it. Phase 14's EC2 tasks 7–9 stay parked (M3).

## 11. What this document does not yet do

Stated plainly so it is not mistaken for complete:

- It does not resolve **C01–C11** individually. Those are being worked separately; C03 and C07 are
  called out above because they gate Phase 17.
- It does not answer the Codex review's **nine owner questions**, which are product decisions.
- It does not specify the **supplier-perspective** report contract.
- It does not include the **table-onboarding review** Codex assigned.
- No implementation code has been written, and none should be until §9 is answered — Q1 and Q3 in
  particular change what gets built.
- The Next.js/AI SDK API surface here is described at the contract level. Exact syntax should be
  checked against current documentation at implementation time rather than taken from this document.
