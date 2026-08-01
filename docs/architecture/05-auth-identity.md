# 05 — Identity, Propagation, Per-User Chat History, and Personalization

## 1. Problem with today's model

The current app treats Auth0 as a door: `auth.py::check_auth` shows a login button, decodes the
JWT **without signature verification** (`jwt.decode(..., options={"verify_signature": False})`),
checks the `Poseidon:Sales` role, and then grants full, identity-blind access. Nothing downstream
knows who the user is. The overhaul makes identity a first-class request property, verified
and enforced in the database.

## 2. The identity-provider abstraction

One middleware seam (`IdentityProvider`) produces the same `UserContext(sub, email, name,
roles)` regardless of how the user authenticated. Everything below the middleware — RLS, chat
history, personalization, run log — is provider-blind: swapping providers changes **zero**
downstream code (decision D22).

| `IDENTITY_MODE` | Mechanism | Default habitat |
|-----------------|-----------|-----------------|
| `auth0` | SPA does OIDC PKCE; API verifies the RS256 JWT against the tenant JWKS (§3) | local (when exercising auth) and EC2 |
| `spcs_ingress` | SPCS public ingress authenticates the visitor as a **Snowflake user** at the platform edge and forwards the authenticated username in the `Sf-Context-Current-User` header; the middleware trusts that header **only when `DEPLOY_MODE=spcs`** (it is injected by the platform, unreachable from outside) and maps it to `sub = "sf|<username>"` | SPCS |
| `disabled` | fixed dev `UserContext` (same convention as today's `AUTH0_ENABLED=False`); an `X-Dev-User` act-as header supports multi-user testing (wfs_core convention) | local development |

- **Decision D22 (SPCS default = `spcs_ingress`):** user testing happens with corporate
  Snowflake users the platform has already authenticated at the ingress — a second Auth0 login
  would add friction without a security gain inside the account boundary.
- **Coexistence:** `IDENTITY_MODE=auth0` also works inside SPCS over the public ingress — the
  wfs pattern (Auth0 `require_claims` gating every `/api/*` route) — for audiences that are not
  Snowflake users. It is a config flip, not a code change.
- Role mapping: `auth0` reads roles from the namespace claim (§3); `spcs_ingress` grants
  `Poseidon:Sales` via a config-listed allowlist or a Snowflake-role lookup at login (config
  choice, recorded per environment). The 403 path is identical in both.
- Subs are provider-prefixed (`auth0|…`, `sf|…`) and therefore stable per provider. An
  environment uses one provider; cross-provider account linking is deliberately out of scope.

## 3. Auth0 token flow, end to end

```mermaid
sequenceDiagram
  participant B as React SPA
  participant A as Auth0
  participant API as FastAPI
  participant SK as Skill layer
  participant PG as Postgres (RLS)
  B->>A: Authorization Code + PKCE (audience = poseidon API)
  A-->>B: access token (RS256 JWT) + rotating refresh token (in-memory)
  B->>API: request with Authorization: Bearer <JWT>
  API->>API: verify signature vs Auth0 JWKS (cached), iss, aud, exp; extract sub, roles
  API->>SK: SkillContext.user = UserContext(sub, name, email, roles)
  SK->>PG: transaction: set_config('app.user_sub', <sub>, true); queries
  PG-->>SK: rows visible only where RLS policy admits app.user_sub
```

- **SPA:** `@auth0/auth0-react`, Authorization Code + PKCE, refresh-token rotation. Tokens stay
  in SDK memory; `localStorage` token cache is explicitly disallowed (decision D15). Silent
  re-auth on reload; the API is the only party that ever validates tokens.
- **API middleware:** verify RS256 signature against the tenant JWKS (cached with kid rotation
  handling), `iss` = tenant, `aud` = the API identifier, `exp/nbf`. Extract `sub`, profile
  claims, and roles from the existing namespace claim `https://wfscorp.com/custom-claims.roles`
  (kept for tenant parity). Role `Poseidon:Sales` remains required (403 otherwise, RFC 7807).
- The `IDENTITY_MODE` toggle (§2) swaps one middleware implementation — every layer below it is
  identical in all modes, so identity handling is exercised even in dev.

`UserContext(sub, email, name, roles)` is constructed once per request and injected into
`SkillContext` (doc 02 §3); no code below the middleware reads headers or tokens.

## 4. Row-level security

Chat data is isolated **in the database**, not by remembering to add `WHERE` clauses:

- Every per-user table carries `user_sub text not null`.
- The API sets identity **transaction-scoped**, as the first statement of every transaction:
  `SELECT set_config('app.user_sub', :sub, true)`. The trailing `true` is the is_local flag — the
  setting dies with the transaction, so a pooled connection cannot carry it into the next
  checkout. `SET LOCAL` is deliberately not used: it accepts no bind parameter, which would force
  string interpolation of an identity value into SQL.
- Policies read the context in the missing_ok form:
  `USING (user_sub = current_setting('app.user_sub', true))` for select/update/delete, the same
  predicate in `WITH CHECK` for insert, on `conversations`, `messages`, `turn_run`, `llm_calls`,
  `tool_calls`, `embeddings`, `user_profile`, `user_memory`, `message_feedback` (doc 06 §7). With
  `missing_ok = true` an unset context is NULL, the predicate is never true, and the query returns
  zero rows — an absent identity fails closed rather than raising an exception a caller could
  catch and route around.
- Every owned table is declared `FORCE ROW LEVEL SECURITY` so policies bind the table owner too —
  migrations and admin sessions are not an accidental bypass.
- The application connects as a role that is **not** the table owner and has no `BYPASSRLS` —
  a forgotten filter returns zero foreign rows instead of leaking.

**Decision D28:** identity context is transaction-scoped, read with `missing_ok`, and enforced with
`FORCE` — a pooled connection must never hand the next user the previous user's context.

Required tests (doc 06 §5, L1 category):

1. **Two-user isolation** — two `UserContext`s write conversations; each lists only its own.
2. **No-context** — a connection that never set `app.user_sub` sees zero rows on every RLS table.
3. **Pooled-connection context leak** — two sequential checkouts of the *same* pooled connection
   under different `UserContext`s; the second must see none of the first user's rows. This is the
   test that fails the moment the context is set session-scoped rather than transaction-scoped.
4. **Owner bypass** — a query as the table owner is still filtered (proves `FORCE`).

Domain data (ontology entities) scoping: today access is role-gated, not row-scoped, and the
overhaul preserves that default. The design hook for later: an entity in the ontology may declare
`row_scope: {column, claim}` (e.g. broker column ↔ a user claim); the query builder then appends
the scope predicate from `UserContext` automatically. The hook ships; no entity uses it yet
(decision D16 — YAGNI on policy, not on mechanism).

## 5. Personalization data (owned by the user, injected every turn)

Every user gets two personal documents, both injected into router/synthesis prompts in the
fixed assembly order of doc 03 §3, both visible and editable in the settings surface (doc 01
§9):

```sql
user_profile (
  user_sub text primary key,
  system_instruction text not null default '',   -- the user's personal system prompt
  updated_at timestamptz not null default now()
);
user_memory (
  user_sub text not null,
  version int not null,                          -- monotonic; current = max(version)
  entries jsonb not null,                        -- [{type, statement, source_conversation_id,
                                                 --   at}] — typed, attributed, never free text
  created_by text not null check (created_by in ('user','distiller')),
  created_at timestamptz not null default now(),
  primary key (user_sub, version)
);
memory_outbox (
  conversation_id uuid primary key
    references conversations(id) on delete cascade,
  user_sub text not null,
  last_turn_at timestamptz not null,             -- bumped every turn; the idle clock
  status text not null default 'pending'
    check (status in ('pending','done','failed')),
  attempts int not null default 0,
  last_error jsonb,
  updated_at timestamptz not null default now()
);
```

**Memory is a set of typed, attributed entries — not accumulated prose.** `type` is a closed set
(`preference`, `scope`, `fact`, `correction`); `statement` is one sentence; every entry names the
conversation it came from and when. Prompt assembly (doc 03 §3) renders the current version's
entries to markdown at injection time, and the size cap (`MEMORY_MAX_CHARS`) applies to that
rendered form. An entry is only admissible if it derives from something **the user said or a choice
the user confirmed**; text returned by web-research or any other external tool is never eligible to
become an entry, verbatim or paraphrased — otherwise a poisoned search result becomes a permanent
instruction injected into every future turn.

- **Distillation is a durable outbox job.** Turn completion writes/upserts a `memory_outbox` row in
  the same transaction as the turn, bumping `last_turn_at`. A worker claims rows whose
  `last_turn_at` is older than `MEMORY_IDLE_MINUTES` (default 30) and runs the `memory` role
  (Claude Sonnet by default; config-driven tier, doc 03 §2) over the prior entries plus the
  finished conversation. Failures increment `attempts` and retry with backoff; an exhausted row is
  marked `failed` with `last_error` retained for inspection, never silently dropped. Conversations
  under a minimum turn count are skipped.
- **Decision D31 (revises D24):** the trigger is an explicit idle threshold served by a durable
  queue, not an in-process debounce — a debounce timer loses every pending distillation on restart
  or redeploy, and "end of conversation" is not an event the server ever receives; only inactivity
  is observable.
- Each distillation run is a `turn_run` row with `kind = 'memory_update'` (doc 06 §1), so the
  background model spend is accounted for exactly like a chat turn.
- Distillation writes a **new version** (append-only; the last N versions retained, N config) so a
  bad distillation is a one-click restore.
- User edits also append a version (`created_by = 'user'`); the distiller treats user-authored
  entries as ground truth it must preserve verbatim.
- All three tables are RLS-scoped (§4); no user can read another's instruction, memory, or queue.

## 6. Per-user chat history model

```sql
conversations (
  id uuid primary key,              -- UUIDv7
  user_sub text not null,
  title text not null default 'New chat',   -- generated by the utility tier after turn 1
  mode text check (mode in ('existing','prospect', 'default')) default 'default',
  state jsonb not null default '{}',        -- parsed slots + carry state (doc 02 §5)
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  archived boolean not null default false
);
messages (
  id uuid primary key,              -- UUIDv7
  conversation_id uuid not null references conversations(id) on delete cascade,
  user_sub text not null,           -- denormalized for RLS locality
  role text check (role in ('user','assistant','system')),
  parts jsonb not null,             -- typed message parts (doc 01 §4)
  turn_id uuid,                     -- links to turn_run (doc 06 §1)
  created_at timestamptz not null default now()
);
```

- UUIDv7 keys throughout (insert locality on the hot tables).
- List/reopen/continue: `GET /api/conversations` (cursor pagination on `updated_at,id`);
  `GET /api/conversations/{id}/messages` (cursor on `created_at,id`); resume restores
  `conversations.state` into the parsing pipeline so carry-over slots behave as if the session
  never ended.
- Continue = normal message POST to an existing conversation; new chat = POST creating a
  conversation with the mode-chip opening message.
- Deletion policy: `archived` soft-flag in the UI; hard delete is a user-initiated
  `DELETE /api/conversations/{id}`. What survives it, and for how long, is §7.

## 7. Privacy, retention, and deletion

Retention windows are **configuration**, not code. The defaults below are starting values chosen to
be defensible; the final numbers are an **owner decision** and are recorded per environment.

| Data | Default window | Setting |
|------|----------------|---------|
| Conversations + messages | retained until the user deletes them | — |
| `turn_run` / `llm_calls` / `tool_calls` (audit) | 400 days | `RETENTION_AUDIT_DAYS` |
| `message_feedback` | kept as long as its `turn_run` | — |
| `user_memory` versions | last 20 versions | `MEMORY_KEEP_VERSIONS` |
| Artifacts (PDF briefs) | 90 days, then object-store lifecycle expiry | `RETENTION_ARTIFACT_DAYS` |
| JSON application logs | 30 days | platform log retention |

**Deletion resolves the audit tension explicitly.** `DELETE /api/conversations/{id}` hard-deletes
the conversation, its messages, and its conversation state — the user's content is gone, not
flagged. The `turn_run` rows and their `llm_calls`/`tool_calls` children are **retained with their
payload columns redacted**: `question`, `answer_summary`, `parsed`, `tool_calls.args`, and
`tool_calls.result_digest` (added to this enumerated list by the phase's final-review wave —
`result_digest` carries the same content-bearing proof text, entity/period/filter values verbatim,
that this section's own governing rule below already covers) are nulled and the row is stamped
`redacted_at`; ids, timestamps, model/provider, token counts, latency, and status survive. The
audit trail keeps its shape (who ran what, when, at what cost) and loses its content. Deletion is
what the UI copy promises, so the copy states this in one sentence.

**Admin access boundary.** An `admin` database role may read `turn_run`/`llm_calls`/`tool_calls`
across users — this is the role that runs harvest, cost roll-ups, and incident review. It is
granted to named operators, never to the application's runtime role, and never to a chat user.
There is no admin UI: admin reads are direct SQL against the audit tables, so every one of them
leaves a database-side trace. Admins have no path to another user's `messages`, `user_memory`, or
`user_profile` — those tables are RLS-scoped with no admin policy, deliberately.

**Egress classification — what may leave the boundary, to whom.**

| Processor | May receive | Must never receive |
|-----------|-------------|--------------------|
| LLM provider (Bedrock / Cortex) | conversation messages, user instruction + memory entries, tool schemas, and retrieved internal results (metric values, tables) — this is the processing scope the product requires | credentials, other users' data |
| Web research (Perplexity, direct or MCP) | entity names only: customer, port, region, and a plain-language topic, plus the user's own question text | any internal metric value, computed figure, period-over-period delta, customer ranking, or anything derived from the certified views |
| Object store (S3 / MinIO) | generated artifacts and their metadata | raw conversation transcripts |
| Auth0 | authentication traffic only | conversation content of any kind |

**Decision D29:** retention is configuration with stated defaults, and conversation deletion
hard-deletes content while retaining a redacted audit row — the user's right to delete and the
audit obligation are both absolute, and only redaction satisfies both.

**Decision D30:** web-research queries carry entity names — customer, port, region, topic — plus
the user's own question text, never internal values — the research tool is the one call that
leaves the corporate boundary with an attacker-visible payload, so nothing computed from the
certified views may be embedded in it. The research adapter (doc 02 §7) builds its query from
parsed entity slots and the question, never from a metric result, and a contract test asserts
that no numeric result value appears in an outbound research query.

## 8. API surface (identity-relevant)

"Authenticated" below means the active `IdentityProvider` admitted the request (§2).

| Route | Auth | Notes |
|-------|------|-------|
| `GET /api/me` | authenticated | profile + roles for the UI shell |
| `GET/PUT /api/me/settings` | authenticated + RLS | user system instruction (doc 01 §9) |
| `GET/PUT /api/me/memory`, `GET /api/me/memory/versions` | authenticated + RLS | memory document, size-cap enforced; version list/restore |
| `GET/POST /api/conversations` | authenticated + RLS | list (cursor) / create |
| `GET /api/conversations/{id}/messages` | authenticated + RLS | history (cursor) |
| `DELETE /api/conversations/{id}` | authenticated + RLS | hard-deletes content; audit row redacted and retained (§7) |
| `POST /api/conversations/{id}/messages` | authenticated + RLS | send turn; SSE response (doc 01 §5) |
| `POST /api/messages/{id}/feedback` | authenticated + RLS | verdict + optional comment; idempotent upsert (doc 06 §7) |
| `GET /api/dimensions/customers?q=` | authenticated | type-ahead from `DataClient.list_dimension_values` |
| `GET /api/artifacts/{id}` | authenticated + ownership check | 302 to a short-lived pre-signed object-store URL |
| `GET /health/live`, `/health/ready` | none | liveness instant; readiness checks DB |

Cross-cutting: explicit CORS origin allowlist (the SPA origin only); token-bucket rate limiting
on chat POST (per `sub`); every response carries the request's trace id header.

## 9. Auth0 tenant configuration (summarized; setup steps in doc 07)

- One **SPA application** (callback/logout URLs per environment) and one **API** (identifier
  e.g. `https://poseidon/api`, RS256).
- Refresh-token rotation on; absolute lifetime set; inactivity timeout per org policy.
- A post-login **Action** injects roles into `https://wfscorp.com/custom-claims.roles`
  (mirroring the existing tenant's claim shape so the app works against either tenant).
- Environments differ only by env vars: `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID` (SPA),
  `AUTH0_AUDIENCE`; no code changes between tenants.
