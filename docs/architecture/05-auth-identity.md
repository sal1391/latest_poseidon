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
  SK->>PG: transaction: SET LOCAL app.user_sub = <sub>; queries
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
- The API opens each transaction with `SET LOCAL app.user_sub = :sub` (parameterized).
- RLS policies: `USING (user_sub = current_setting('app.user_sub'))` for select/update/delete,
  `WITH CHECK` for insert, on `conversations`, `messages`, `run_log`, `embeddings`,
  `user_profile`, `user_memory`, `message_feedback` (doc 06 §7).
- The application connects as a role that is **not** the table owner and has no `BYPASSRLS` —
  a forgotten filter returns zero foreign rows instead of leaking.
- A dedicated test proves isolation: two `UserContext`s write conversations; each can list only
  its own; a raw query without `SET LOCAL` sees nothing (doc 06 §5, L1 category).

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
  content text not null,                         -- markdown, size-capped (8,000 chars ≈ 2k
                                                 --   tokens) — enforced at write, shown in UI
  created_by text not null check (created_by in ('user','distiller')),
  created_at timestamptz not null default now(),
  primary key (user_sub, version)
);
```

- **Memory distillation:** the `memory` role (Claude Sonnet by default; config-driven tier,
  doc 03 §2) rewrites the memory document from its prior version plus the finished
  conversation. **Decision D24 — trigger is end-of-conversation** (async, debounced, skipped
  for conversations under a minimum turn count): memory is freshest before the user's next
  session, the job is naturally scoped to exactly the content being distilled, and no scheduler
  infrastructure is needed.
- Distillation writes a **new version** (append-only; the last N versions retained, N config)
  so a bad distillation is a one-click restore, and every update is a run-log row
  (`kind = 'memory_update'`, doc 06 §1).
- User edits also append a version (`created_by = 'user'`); the distiller treats user-authored
  content as ground truth it must preserve.
- Both tables are RLS-scoped (§4); no user can read another's instruction or memory.

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
  turn_id uuid,                     -- links to run_log
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
  `DELETE /api/conversations/{id}` (cascades messages; run-log rows are retained for audit with
  the conversation id intact — stated in the UI copy).

## 7. API surface (identity-relevant)

"Authenticated" below means the active `IdentityProvider` admitted the request (§2).

| Route | Auth | Notes |
|-------|------|-------|
| `GET /api/me` | authenticated | profile + roles for the UI shell |
| `GET/PUT /api/me/settings` | authenticated + RLS | user system instruction (doc 01 §9) |
| `GET/PUT /api/me/memory`, `GET /api/me/memory/versions` | authenticated + RLS | memory document, size-cap enforced; version list/restore |
| `GET/POST /api/conversations` | authenticated + RLS | list (cursor) / create |
| `GET /api/conversations/{id}/messages` | authenticated + RLS | history (cursor) |
| `POST /api/conversations/{id}/messages` | authenticated + RLS | send turn; SSE response (doc 01 §5) |
| `POST /api/messages/{id}/feedback` | authenticated + RLS | verdict + optional comment; idempotent upsert (doc 06 §7) |
| `GET /api/dimensions/customers?q=` | authenticated | type-ahead from `DataClient.list_dimension_values` |
| `GET /api/artifacts/{id}` | authenticated + ownership check | 302 to a short-lived pre-signed object-store URL |
| `GET /health/live`, `/health/ready` | none | liveness instant; readiness checks DB |

Cross-cutting: explicit CORS origin allowlist (the SPA origin only); token-bucket rate limiting
on chat POST (per `sub`); every response carries the request's trace id header.

## 8. Auth0 tenant configuration (summarized; setup steps in doc 07)

- One **SPA application** (callback/logout URLs per environment) and one **API** (identifier
  e.g. `https://poseidon/api`, RS256).
- Refresh-token rotation on; absolute lifetime set; inactivity timeout per org policy.
- A post-login **Action** injects roles into `https://wfscorp.com/custom-claims.roles`
  (mirroring the existing tenant's claim shape so the app works against either tenant).
- Environments differ only by env vars: `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID` (SPA),
  `AUTH0_AUDIENCE`; no code changes between tenants.
