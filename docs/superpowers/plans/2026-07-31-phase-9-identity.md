# Poseidon Phase 9: Identity Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `IdentityProvider` seam of doc 05 §2 (decision D22): `auth0` (RS256 JWT vs cached tenant JWKS, namespace roles claim, 401/403 RFC-7807), `disabled` (fixed dev user + `X-Dev-User` act-as), `spcs_ingress` (`Sf-Context-Current-User` trusted ONLY under `DEPLOY_MODE=spcs`) — all three implemented now, config-selected via `IDENTITY_MODE`, producing one `UserContext(sub, email, name, roles)` that threads into `SkillContext.user`, the run-log's `user_sub`, and every per-user path. Plus the CORS allowlist and a rate limit on the chat POST. **The live Auth0 login round-trip needs a dev tenant (Carlos's pile); everything else validates offline with a local JWKS fixture.**

**Architecture:** One middleware seam; everything below it provider-blind (D22). The `dev|local` constant shipped since P6 is REPLACED by the middleware's UserContext — in `disabled` mode the default sub stays `dev|local` so every existing row/test remains coherent. Role `Poseidon:Sales` required on `/api/*` chat/data routes (403 problem-detail otherwise); the roles claim namespace is `https://wfscorp.com/custom-claims.roles` (tenant parity). No new heavy deps: JWT verification via `PyJWT[crypto]` (the one new dep — the standard minimal choice); the rate limiter is a hand-rolled config-driven token bucket (no slowapi).

**Tech Stack:** Existing backend + frontend. New dep: `PyJWT[crypto]>=2.8`. Frontend: `@auth0/auth0-react` (check package.json — if absent, add; wired behind the identity-mode switch so `disabled` never loads a login).

## Global Constraints

- **Provider-blind below the middleware (D22):** zero downstream code branches on `IDENTITY_MODE`. The middleware sets `request.state.user: UserContext`; live_chat/orchestrator/runlog consume it. `SkillContext` gains `user: object | None = None` (additive, doc 02 §3 names it).
- **`UserContext`:** frozen dataclass `(sub: str, email: str | None, name: str | None, roles: tuple[str, ...])`. Subs provider-prefixed (`auth0|…`, `sf|…`, `dev|…`).
- **`disabled` mode (default):** fixed `UserContext("dev|local", "dev@local", "Dev User", ("Poseidon:Sales",))`; `X-Dev-User: alice` → `dev|alice` (multi-user testing, wfs convention). DEFAULT mode — every existing test/env boots unchanged.
- **`auth0` mode:** verify RS256 vs tenant JWKS (cached; kid-rotation refetch ONCE on unknown kid), `iss` = `https://{AUTH0_DOMAIN}/`, `aud` = `AUTH0_AUDIENCE`, `exp`/`nbf`; extract sub/email/name + roles from the namespace claim. Failures → 401 RFC-7807 (pinned codes: missing/malformed header, bad signature, expired, wrong iss/aud, future nbf); valid token without `Poseidon:Sales` → 403. Settings: `auth0_domain`/`auth0_audience` (existing scaffold fields — verify, reuse).
- **`spcs_ingress` mode:** trust `Sf-Context-Current-User` ONLY when `settings.deploy_mode == "spcs"`; sub = `sf|{username}`; roles granted via config allowlist `SPCS_SALES_USERS` (comma list; `*` = everyone gets `Poseidon:Sales` — document the choice per doc 05 §2's "config choice, recorded per environment"). `spcs_ingress` outside spcs deploy mode → hard boot error (fail-fast, pinned). Header absent in spcs mode → 401.
- **Enforcement scope:** the require-user + require-role dependency guards `/api/conversations*`, `/api/messages*`, `/api/skills`, `/api/dev/*`; `/health/*` stays open. Mock-chat router (test-only) untouched.
- **Rate limit:** config-driven token bucket on the chat-send POST, keyed by sub (fallback client IP): `RATE_LIMIT_CHAT_PER_MINUTE` (default 30; 0 = off, and OFF in `disabled` mode by default so dev/tests are unaffected — document). 429 RFC-7807 with `Retry-After`.
- **CORS:** explicit allowlist `CORS_ALLOW_ORIGINS` (default: the Vite dev origin `http://localhost:5173`); never `*` with credentials.
- **Frontend:** identity mode arrives via `GET /api/me` (new endpoint returning `{sub, name, email, roles, identity_mode}`) — the SPA renders the login gate ONLY when mode is `auth0` and the SDK reports unauthenticated; `disabled`/`spcs_ingress` pass straight through (doc 01 §1's thin seam). `apiFetch` gains an auth-header injector seam; **`streamTurn` routes through `apiFetch`'s request builder (closing the P9 carryforward: two fetch call sites = auth-header trap)**. Auth0 SDK config from `VITE_AUTH0_*` envs; tokens in SDK memory only (D15).
- ENVIRONMENT: withhold PERPLEXITY_API_KEY (env -u) on every suite run; zero live calls (Auth0 included — the JWKS fixture is local).
- ASCII .py; frozen dataclasses; byte-pinned problem details; deterministic; docstrings explain WHY; ruff clean; conventional commits on `phase-3-8-overnight`; trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Baselines: 1389 offline / 43 pg / 41 frontend, all lint clean. The compose stack is up.
- Do not modify P2-P8 core modules EXCEPT the sanctioned additive items: `SkillContext.user`; `core/config.py` Settings additions; `api/live_chat.py` + `api/app.py` middleware wiring + the `dev|local` replacement; `api/dev_runner.py` guard inclusion; frontend `api/client.ts`/`sse.ts` auth threading + the new auth feature dir.

## File Map

```
backend/poseidon/core/identity.py            # UserContext + IdentityProvider protocol + DisabledProvider + resolve_provider()
backend/poseidon/core/identity_auth0.py      # Auth0Provider (JWKS cache, kid rotation, claim validation)
backend/poseidon/core/identity_spcs.py       # SpcsIngressProvider
backend/poseidon/api/auth.py                 # FastAPI middleware/dependencies: current_user, require_sales, rate limiter
backend/poseidon/api/app.py                  # middleware + CORS wiring (additive)
backend/poseidon/api/live_chat.py            # user_sub from request.state.user; GET /api/me
backend/poseidon/core/skills/context.py     # + user (additive, sanctioned)
backend/poseidon/core/config.py              # + identity_mode, spcs_sales_users, rate_limit_chat_per_minute, cors_allow_origins
backend/tests/test_identity_providers.py     # all three providers, unit level
backend/tests/test_api_auth.py               # middleware/guards/429/CORS/me via httpx ASGI + local JWKS fixture
frontend/src/features/auth/*                 # gate, role guard, logout (mode-aware)
frontend/src/api/client.ts + sse.ts          # auth header seam; streamTurn through the shared builder
backend/pyproject.toml                       # + PyJWT[crypto]
```

---

### Task 1: UserContext + protocol + DisabledProvider + middleware wiring + the dev|local replacement

**Files:** `core/identity.py`; `api/auth.py` (current_user dependency only this task); `api/app.py` + `api/live_chat.py` (threading); `core/skills/context.py` (+user); `core/config.py` (+identity_mode Literal["disabled","auth0","spcs_ingress"]="disabled"); tests `test_identity_providers.py` (disabled part) + `test_api_auth.py` (threading part).

Contract pins: DisabledProvider returns the fixed context; `X-Dev-User: alice` → `dev|alice` (sanitize: `[a-z0-9_-]{1,64}` casefolded, else ignore header — pinned); `resolve_provider(settings)` fail-fast on unknown mode. Middleware sets `request.state.user` for EVERY request (cheap in disabled mode). live_chat's `_DEV_USER_SUB` constant is deleted; every use reads the request's user (transcript store keys, writer calls, state store — trace all). The pg E2E stays green unchanged (same default sub). SkillContext.user wired in the orchestrator ctx construction. `GET /api/me` returns the context + identity_mode.

- [ ] Tests FIRST (RED) → implement → GREEN (offline + pg once — user_sub flows must not shift) → ruff. **Commit** — `feat(identity): user context seam with disabled provider and act-as threading`

---

### Task 2: Auth0Provider + JWKS fixture tests + guards + rate limit + CORS

**Files:** `core/identity_auth0.py`; `api/auth.py` (require_sales + rate limiter + 401/403/429 problem shapes); `api/app.py` (CORS); config (+rate/cors fields); pyproject (+PyJWT); tests `test_api_auth.py`.

Auth0Provider: JWKS fetched via httpx (injectable transport), cached in-process; unknown kid → ONE refetch then 401; full validation per the constraints. The local fixture: generate an RSA keypair in-test (cryptography lib arrives with PyJWT[crypto]), serve a JWKS dict through the injected transport, mint test JWTs (valid; expired; nbf-future; wrong aud; wrong iss; bad signature via second key; role-less; malformed header variants). Each → pinned problem detail. Rate limiter: token bucket per key, monotonic clock, thread-safe, 429 + Retry-After; OFF when limit=0; disabled-mode default off. CORS allowlist wiring + a preflight test.

- [ ] Tests FIRST (RED) → implement → GREEN → ruff; verify the full suite unaffected (guards default-open in disabled mode with the fixed user — every existing httpx test passes unchanged; any that don't, reconcile honestly). **Commit** — `feat(identity): auth0 jwks verification, role guard, rate limit, and cors allowlist`

---

### Task 3: SpcsIngressProvider + mode selection + boot discipline

**Files:** `core/identity_spcs.py`; `core/identity.py` (resolver); config (+spcs_sales_users); tests in `test_identity_providers.py`.

Pins: header → `sf|{username}` (casefold, sanitize same rule as act-as); allowlist grants `Poseidon:Sales` (`*` wildcard documented); missing header in spcs mode → 401; `identity_mode=spcs_ingress` with `deploy_mode != "spcs"` → RuntimeError at BOOT (pinned message — the fail-fast rule; a header trustable only behind the platform edge must never be trusted elsewhere); the boot log line names the active identity mode (parity with the research-transport line).

- [ ] Tests FIRST (RED) → implement → GREEN → ruff. **Commit** — `feat(identity): spcs ingress provider with deploy-mode trust gate`

---

### Task 4: Frontend auth seam + the streamTurn/apiFetch carryforward + E2E

**Files:** `frontend/src/features/auth/*` (AuthGate, RoleGuard, logout button in the user-menu slot); `app/` provider wiring; `api/client.ts` (+`getMe`, auth-header injector `setAuthTokenProvider(fn)`), `api/sse.ts` (streamTurn through the shared request builder); vitest; backend `test_api_auth.py` gains the me-endpoint contract test if not already.

Behavior: on boot the SPA calls `GET /api/me`; mode `disabled`/`spcs_ingress` → render app immediately (identity from the response); mode `auth0` → Auth0Provider wrapper (Authorization Code + PKCE, audience from env), unauthenticated → login gate, authenticated → token provider wired into the injector, 403-role → a clear "no access" screen (RFC-7807 rendered). Tokens never in localStorage (D15 — SDK memory). Vitest: disabled-mode renders without any Auth0 import executing (lazy import — pin it); the injector adds the header to BOTH apiFetch and streamTurn requests (the carryforward's point — one builder, pinned); 403 screen renders from a problem payload. Playwright sanity: disabled-mode localhost still boots to the chat (the demo must not regress) — evidence in report.

- [ ] Vitest RED → implement → GREEN; backend suite + pg re-run once; tsc/oxlint clean; Playwright evidence. **Commit** — `feat(identity): mode-aware auth gate and unified request auth threading`

---

## Phase Gate (human validation)

1. Offline: full suites green; `pytest tests/test_api_auth.py -v` shows the 401 matrix (expired/tampered/wrong-aud/…) and the 403 role case, all against the LOCAL fixture — no tenant needed.
2. localhost:5173 boots exactly as before (disabled mode; the demo unchanged).
3. **Needs your Auth0 dev tenant** (pile): set `IDENTITY_MODE=auth0` + `AUTH0_DOMAIN/AUTH0_AUDIENCE` + `VITE_AUTH0_*`, run the live login round-trip. Everything is wired for it; this is config + your tenant, zero code.

## Self-Review Notes

- Doc-08 P9 coverage: all three providers now ✓, config-selected ✓, CORS ✓, rate limit ✓, login round-trip = tenant-gated pile item ✓ honest, role-less 403 ✓, tampered/expired 401 via local fixture ✓, spcs_ingress unit tests + outside-mode rejection ✓, disabled still boots ✓.
- Deliberate scope: no RLS yet (P10 — user_sub now REAL through the stack, which is exactly what P10's policies will bind); no logout-everywhere/session revocation (SDK default behavior); Auth0 tenant config itself is documentation (doc 05 §9) not code.
- Type consistency: UserContext frozen; SkillContext.user object-typed per house pattern; problem details via the shared `problem()` constructor.
