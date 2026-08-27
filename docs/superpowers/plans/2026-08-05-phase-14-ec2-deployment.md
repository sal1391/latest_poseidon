# Phase 14 — EC2 Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Roadmap reorder (Carlos, 2026-08-05):** EC2 deploys FIRST. This phase is the new Phase 14;
> SPCS (with Cortex/D33) moves to Phase 15 and the Snowflake data backend to Phase 16 (handled by
> a separate Snowflake-side effort). Doc 08's EC2 section already declares "depends on 13,
> independent of 14/15", so the flip is structurally clean. Task 6 amends the docs to record this
> as an owner decision — the amendment text is in this plan, applied only after Carlos's go.

**Goal:** Poseidon live on a public URL — EC2 + Caddy TLS + RDS Postgres + S3 + Auth0 login +
Bedrock via instance profile, all three flows working on synthetic data — with the P9 pre-deploy
hardening HARD GATE (JWKS pre-auth DoS and friends) landed before any auth0-mode deploy, exactly
as that gate was written.

**Architecture:** Two halves. **Offline half (Tasks 1–6, subagent SDD):** the hardening bucket
(JWKS negative cache + off-event-loop resolve; prod-surface gating; a worker claim-privilege
migration that makes memory distillation work on RDS, where no superuser/BYPASSRLS role can
exist; the architecture-fitness test file; boot privilege probe), then the production container
(one image serving SPA + API), the EC2 compose stack (caddy + backend + worker — db/minio are
replaced by RDS/S3), provisioning scripts, runbooks, and the docs amendment. Everything here is
authorable and testable on this machine. **Account-gated half (Tasks 7–9):** AWS provisioning,
Auth0 tenant day, first deploy + smoke + rollback rehearsal — run as one-step-at-a-time
walkthroughs with Carlos driving his own console/CLI (his established preference); credentials
are never typed, stored, or scripted by the orchestrator or any subagent.

**Tech Stack:** Docker multi-stage build (node:22-alpine → python:3.12-slim + WeasyPrint libs),
Caddy 2 (automatic TLS, SSE-friendly proxy), AWS CLI idempotent scripts (Terraform deliberately
deferred, D18), RDS Postgres 16, S3 + lifecycle, IAM instance profile, ECR, Auth0 (PKCE SPA),
anyio.to_thread for the off-loop JWKS resolve, Alembic migration 0009.

## Global Constraints

- **Model policy** (per [[model-delegation-preference]]; the controller — this session — never
  writes implementation code, only coordinates, adjudicates, escalates):
  - **Task 1 (JWKS hardening): opus** implementer, **opus** reviewer — pre-auth security surface,
    the phase's hard-gate item; concurrency + caching semantics under a lock are exactly where a
    standard-tier model produces plausible-but-wrong code.
  - **Task 2 (prod-surface hardening): sonnet/sonnet** — three mechanical, precisely-specified
    changes.
  - **Task 3 (worker claim role + boot probe): opus/opus** — a genuine architectural change to
    the RLS privilege model (the one P13 flagged as "load-bearing finding requiring escalation"
    territory); the reviewer must re-derive the RDS constraint independently, not trust this
    plan's claim.
  - **Task 4 (architecture-fitness file): sonnet/sonnet** — test-writing against pinned
    invariants.
  - **Task 5 (production image + EC2 compose): opus/opus** — image-layout hazards (the
    `parents[4]` ontology anchor, StaticFiles-after-routers ordering, build-arg seam) are
    cross-cutting and silent-failure-shaped.
  - **Task 6 (scripts + runbooks + docs): sonnet/sonnet** — authoring against verbatim content
    given below.
  - **Tasks 7–9 (account-gated walkthroughs): controller-led with Carlos driving, NO subagents.**
  - **Final whole-phase review (over Tasks 1–6's range): opus**, per every phase since P10.
- **Scope fence — this phase does NOT:** no CortexProvider, no provider-parity work, no touching
  the pinned error at `core/llm/roles.py` (D33 defers with SPCS, now Phase 15); no
  `SnowflakeDataClient`, no `DATA_BACKEND` flip — **`DATA_BACKEND=synthetic` in every
  environment this phase touches** (live_chat's snowflake guard must never fire); no
  snowflake-* dependencies; no SPCS spec/EAI/OAuth-token work (Phase 15); no retrieval (P17);
  no Streamlit retirement; no session-lifecycle story; no supervisor role (D12); F3 parser
  widening stays parked (the Meridian-Bunkering counterfactual in the fix ledger stands); the
  remaining ledgered minors stay parked. SPCS-specific deploy-runbook items (ingress
  duplicate-header verification, `SPCS_SALES_USERS` fail-closed) stay with Phase 15.
- **RPO/RTO are Carlos's pile-decision numbers, superseding doc 07 §4's defaults: RPO 24 hours
  (daily backups), RTO next business day.** On EC2 this is satisfied by RDS automated daily
  backups (retention ≥ 7 days) plus a documented restore path — not by the SPCS `pg_dump`
  machinery, which stays a Phase 15 deliverable (D17: RDS managed backups are the point of
  paying for RDS).
- **Deliberate deviations, disclosed up front** (each is surfaced in the plan-review summary;
  none is silent):
  1. **SPA Auth0 config is baked at image build time** (`VITE_AUTH0_*` build args) — the SPA
     reads `import.meta.env` (frontend/src/features/auth/auth0Config.ts), so a fully env-only
     image would need a runtime-config endpoint refactor. Minimal-churn call: build args now,
     documented in `deploy-ec2.md`; the runtime-config refactor is named as a parked candidate
     for whenever auth0-in-SPCS coexistence is wanted. Tenant values are public SPA config, not
     secrets.
  2. **Secrets Manager machinery is deferred.** Doc 07 §6 routes EC2's `DATABASE_URL` through
     Secrets Manager; the merge convention exists for `SNOWFLAKE_*`, which this phase does not
     ship. Instead: a root-owned `/etc/poseidon/backend.env` on the instance, written by Carlos
     during the walkthrough (never by me), holding `DATABASE_URL`, `AUTH0_*`, and optionally
     `PERPLEXITY_API_KEY`. Bedrock and S3 need NO keys at all (instance profile — boto3's
     default chain, already how `bedrock.py:126` and the S3 client construct). Task 6's doc
     amendment records this; Secrets Manager arrives with the Snowflake-credentials effort.
  3. **The architecture-fitness file holds the NEW invariants plus a fragility registry** (a
     module docstring pointing at the persist-ordering white-box test in
     `tests/test_history_cutover.py` and the route sweep in `tests/test_api_auth.py`) rather
     than physically relocating those tests — moving them would drag their fixture stacks along
     for zero behavioral gain. Deviation from the carryforward's literal "all in one file"
     wording, honoring its intent (one place that knows where the fragile tests live).
  4. **`identity_mode=disabled` outside local stays a boot WARNING, not a hard failure** —
     P9 documented that as a deliberate choice (`core/identity.py:275-282`: "legitimate choice
     on a throwaway EC2 box... visibility, not a gate"). The carryforward asks for a runbook
     item, and that is what ships: `deploy-ec2.md` and `smoke.md` both assert
     `IDENTITY_MODE=auth0`. Escalating to a hard fail is offered to Carlos as an option at plan
     review, default no.
- **ENVIRONMENT (binding on every backend run):** the FULL offline exclusion set AND the key
  withheld, always — `env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -m pytest -m "not pg
  and not minio and not pdf and not router_live and not research_live"` (Bash tool; the
  convention note from the fix effort's re-review is standing policy). pg runs additionally need
  `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon`. Windows venv
  `backend/.venv/Scripts/python.exe`. Compose DB is at migration 0008 → this phase applies 0009.
  No live LLM/network calls from any automated test — JWKS tests use the injectable
  `httpx.MockTransport`/`JwksTransport` seam that already exists. `docker build` / `docker
  compose config` runs are local-only and free.
- ASCII .py/.md/.yml authored this phase; deterministic tests; docstrings explain WHY; ruff
  clean on touched Python; conventional commits on `phase-3-8-overnight` with trailer
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; NEVER push; NEVER merge; TDD
  RED-first with captured evidence for every code task.
- **Baselines at phase start (re-pin from a real run at Task 1 dispatch, not from this
  document):** offline ~1611 passed/10 skip, pg ~320/1 skip, vitest 171, ruff + tsc/oxlint
  clean.
- **Sanctioned modifications to existing files (everything else this phase creates is new):**
  `backend/poseidon/core/identity_auth0.py` (Task 1: negative cache + fetch-interval guard +
  injectable clock + docstring rewrite); `backend/poseidon/api/app.py` (Task 1: the ONE
  middleware line at :327 becomes the off-loop call; Task 2: the FastAPI constructor kwargs at
  :35 only; Task 3: one boot-probe call inside the live-mode wiring; Task 5: the StaticFiles
  mount + `static_dir` read, after all routers); `backend/poseidon/api/auth.py` (Task 2:
  `ChatRateLimiter` eviction + injectable clock only); `backend/poseidon/core/db.py` (Task 3:
  the `assert_boot_privileges` helper; Task 4: a malformed-`DATABASE_URL` actionable-error wrap
  ONLY IF Task 4's RED probe shows the current error is not actionable);
  `backend/poseidon/core/config.py` (Task 5: the `static_dir` field only);
  `backend/poseidon/scripts/memory_worker.py` (Task 3: `SET LOCAL ROLE poseidon_worker` in the
  claim transaction + the startup probe call only); `backend/pyproject.toml` (Task 1: declare
  `anyio` as a direct dependency ONLY IF not already declared — it is imported directly now);
  `backend/tests/test_identity_providers.py` (Task 1); `backend/tests/test_api_auth.py`
  (Task 1 middleware off-loop test; Task 2 gating + limiter tests); `backend/tests/
  test_memory_worker.py` (Task 3: rewrite of the claim-privilege pin test + probe coverage);
  `backend/tests/test_migrations.py` (Task 3: extend to 0009); **amended (2026-08-05,
  controller-ratified sanction-line gap, the established P11/P13 resolution pattern —
  Task 3's implementer disclosed rather than silently expanding):**
  `backend/tests/test_personalization_stores.py` (Task 3, ONLY the policy-count proxy
  assertion — its "exactly one policy per table" check was a stricter proxy for the real
  "no admin policy" rule and is tripped by 0009's own plan-mandated worker policy; it becomes
  a per-table policy allow-list plus a direct "no policy names poseidon_admin" assertion,
  nothing else in the file); `backend/.env.example`
  (Tasks 5–6: `STATIC_DIR` + the EC2 rows); `docs/architecture/00-overview.md`,
  `docs/architecture/07-infrastructure.md`, `docs/architecture/08-build-phases.md` (Task 6,
  verbatim text below). **Amended (2026-08-05, Carlos's plan review — F2 resolved as option
  (b), the D16 `row_scope` mechanism, new Task 6b):**
  `backend/poseidon/core/ontology/models.py` (RowScope model + `Entity.row_scope` + column
  validator); `backend/poseidon/core/ontology/loader.py` (ONLY IF parsing the new optional
  field needs more than the model change); `backend/poseidon/core/data/specs.py`
  (`scope_value` on the two specs); `backend/poseidon/core/data/query_builder.py` (fail-closed
  enforcement + predicate + the `resolve_row_scope_value` helper);
  `backend/poseidon/core/data/client.py` + `backend/poseidon/core/data/synthetic_client.py`
  (optional `scope_value` kwarg on `list_dimension_values`/`available_periods`, passed
  through); `backend/poseidon/tasks/data_qa/skills/metric_query/skill.py`,
  `backend/poseidon/tasks/customer_insight/skills/existing_customer_brief/tools/fetch_metrics.py`,
  `backend/poseidon/tasks/customer_insight/skills/existing_customer_brief/tools/fetch_top_ports.py`
  (threading `resolve_row_scope_value(..., ctx.user)` into their spec construction) + their
  co-located `tests/test_tools.py` files; `backend/poseidon/scripts/demo_query.py` (explicit
  `None` + one comment); `backend/tests/test_ontology_loader.py`,
  `backend/tests/test_query_builder_snapshots.py`. **Amended (2026-08-05, Task 6 review —
  two confirmed doc gaps created by the renumber itself):** `docs/architecture/03-llm-routing.md`
  (ONLY the one stale "(Phase 14)" reference at :34 → "(Phase 15)" — the SPCS phase's new
  number, reviewer-confirmed); `docs/architecture/07-infrastructure.md` additionally its title
  line and opening paragraph (ONLY aligning the "SPCS primary / EC2 secondary" sequencing
  language with the D8-revised narrative the same doc now carries everywhere else — EC2
  first-deployed, SPCS corporate-primary deployed after). Nothing outside this list is touched;
  frontend source is deliberately untouched (stage-1 build args only).

## File Map

```
backend/poseidon/core/identity_auth0.py            # negative kid cache + fetch guard (T1)
backend/poseidon/api/app.py                        # off-loop resolve; docs gating; probe; static (T1/2/3/5)
backend/poseidon/api/auth.py                       # ChatRateLimiter eviction (T2)
backend/migrations/versions/0009_worker_claim_role.py  # NEW: poseidon_worker role + policy (T3)
backend/poseidon/core/db.py                        # assert_boot_privileges (T3)
backend/poseidon/scripts/memory_worker.py          # SET LOCAL ROLE claim + startup probe (T3)
backend/tests/test_boot_privileges.py              # NEW (pg) (T3)
backend/tests/test_architecture_fitness.py         # NEW (T4)
backend/poseidon/core/config.py                    # static_dir (T5)
backend/tests/test_static_serving.py               # NEW (T5)
infra/Dockerfile                                   # NEW: the production multi-stage image (T5)
infra/Caddyfile                                    # NEW (T5)
infra/docker-compose.ec2.yml                       # NEW: caddy + backend + worker (T5)
infra/aws/01-security-groups.sh .. 06-budget.sh    # NEW: idempotent provisioning (T6)
infra/runbooks/deploy-ec2.md                       # NEW (T6)
infra/runbooks/smoke.md                            # NEW: either-target checklist, owed since doc 07 §8 (T6)
docs/architecture/{00,07,08}                       # the reorder amendment (T6)
docs/superpowers/plans/2026-08-XX-phase-14-ec2-live.task.md  # NEW at Task 7 dispatch: the live walkthrough tracker
```

---

### Task 1: JWKS pre-auth-DoS hardening (the P9 HARD GATE item)

**Files:** modify `core/identity_auth0.py`, `api/app.py` (middleware line only),
`tests/test_identity_providers.py`, `tests/test_api_auth.py`, `pyproject.toml` (anyio, only if
missing).

**The problem (P9 final review I-5, verbatim carryforward):** `Auth0Provider._fetch_jwks` is
blocking sync httpx I/O on the asyncio event loop, reachable pre-auth by uncredentialed callers:
any request bearing a token with a bogus `kid` triggers a fresh JWKS fetch (the docstring at
:26-30 documents "no negative caching" as a feature), and the fetch itself stalls the entire
event loop. Uncredentialed flood of distinct bogus kids = pre-auth self-DoS on every route.

**Design (both halves required; either alone leaves a hole):**

1. **In the provider — bounded negative cache + minimum fetch interval.** New state under the
   existing `self._lock`: `self._negative_kids: dict[str, float]` (kid → clock() at the failed
   post-fetch lookup), `self._last_fetch_at: float | None`. Module constants:
   `_NEGATIVE_TTL_SECONDS = 300.0`, `_NEGATIVE_MAX_ENTRIES = 1024`,
   `_MIN_FETCH_INTERVAL_SECONDS = 60.0`. Constructor gains
   `clock: Callable[[], float] = time.monotonic` (injectable for tests, the same seam style
   `ChatRateLimiter` gains in Task 2). `_public_key_for_kid(kid)` becomes:
   check positive cache → hit returns key. Check negative cache → entry younger than TTL raises
   the existing `AuthError(401, "unknown signing key", ...)` with NO fetch. Otherwise fetch —
   but only if `_last_fetch_at` is None or older than `_MIN_FETCH_INTERVAL_SECONDS`.
   **Amended (2026-08-05, Task 1 review Important #1, Carlos's ruling: single-flight):** a
   caller that finds the interval slot claimed AND a fetch genuinely in flight right now WAITS
   (bounded — off-lock, on a per-attempt event, capped by the fetch's own explicit timeout) for
   that fetch to complete, then re-checks the positive cache before taking the negative
   outcome — so concurrent cold-start/rotation requests with VALID kids ride the winner's
   fetch instead of receiving spurious 401s. A caller that finds the slot claimed with NO
   fetch in flight (the bogus-kid flood case) still goes straight to the negative outcome with
   no fetch and no wait — the anti-DoS property is unchanged: at most one outbound fetch per
   interval, and attackers never park worker threads. The `httpx.Client` gains an explicit
   bounded timeout (the waiter cap depends on it). After a real fetch, re-check the
   positive cache; still missing → record kid in the negative cache (evicting the OLDEST entry
   when at `_NEGATIVE_MAX_ENTRIES` — dict insertion order suffices) and raise. A kid found
   positive is removed from the negative cache. Rotation still heals: TTL expiry, or any
   successful fetch (triggered by a different kid) repopulating the positive map, both unblock.
   Rewrite the module docstring's :15-30 JWKS paragraph to describe the new contract and WHY
   (name the pre-auth DoS; the old "no negative caching" rationale is superseded, say so).
2. **In the middleware — resolve off the event loop.** `api/app.py:327`'s
   `request.state.user = provider.resolve(headers)` becomes
   `request.state.user = await anyio.to_thread.run_sync(provider.resolve, headers)` (anyio is
   Starlette's own backbone; declare it as a direct dependency if `pyproject.toml` does not
   already). Uniform for ALL provider modes — disabled/spcs resolves are dict lookups and the
   pooled worker-thread hop is noise, while branching per-mode is a second code path to get
   wrong. The existing `AuthError`/generic exception containment at :328-331 is UNCHANGED —
   `run_sync` propagates exceptions as-is. Thread-safety note for the implementer: multiple
   requests may now genuinely race inside the provider; every read/write of the three cache
   fields must hold `self._lock` (the current code only locks the swap at :174-175 — the fetch
   itself must move under the interval guard so concurrent unknown-kid requests cannot stampede
   the tenant).

- [ ] **Step 1 (RED):** in `tests/test_identity_providers.py`, against a counting transport
  (extend the existing `JwksTransport` double to count requests): (a) 25 resolves with the SAME
  bogus kid → exactly 1 JWKS fetch, every call raising the pinned unknown-signing-key
  `AuthError`; (b) 25 resolves with 25 DIFFERENT bogus kids inside the fetch interval → exactly
  1 fetch (the interval guard, not the per-kid cache, is what stops this flood); (c) rotation
  heals — a kid unknown at first, negative-cached, then added to the fixture JWKS: advance the
  injected clock past both TTL and fetch interval, resolve again → token verifies; (d) the
  negative cache never exceeds `_NEGATIVE_MAX_ENTRIES` (feed `_NEGATIVE_MAX_ENTRIES + 10`
  distinct kids with the clock advanced past the fetch interval between each; assert
  `len(provider._negative_kids) <= 1024` — white-box, acceptable in this codebase's established
  pin-test style); (e) the ENTIRE existing 401 matrix (byte-pinned in that file) still passes
  untouched. In `tests/test_api_auth.py`: (f) an off-loop proof — a probe provider whose
  `resolve` records `threading.get_ident()`; drive one request through the real middleware via
  the existing test app factory and assert the recorded ident differs from the event loop
  thread's, proving the middleware call actually crossed `to_thread`.
- [ ] **Step 2:** RED run (offline command from Global Constraints). Capture output.
- [ ] **Step 3:** Implement per the design block. No behavior change for valid tokens.
- [ ] **Step 4:** GREEN; full offline suite; ruff. **Commit** —
  `fix(identity): bound JWKS fetches with a negative kid cache and resolve off the event loop`

### Task 2: Production-surface hardening (docs gating + rate-limiter eviction)

**Files:** modify `api/app.py` (constructor kwargs only), `api/auth.py`, `tests/test_api_auth.py`.

- **Gate `/docs` + `/openapi.json` + `/redoc` outside `deploy_mode=local`** (P9 carryforward:
  all three are open in every mode today). At `app.py:35`:

  ```python
  docs_kwargs = (
      {}
      if boot_settings.deploy_mode == "local"
      else {"docs_url": None, "redoc_url": None, "openapi_url": None}
  )
  app = FastAPI(title="Poseidon API", version="0.1.0", **docs_kwargs)
  ```

  (resolve the settings read order — settings are currently read at :36, one line after the
  constructor; hoist the read above it.)
- **`ChatRateLimiter` bucket eviction** (P9 M-8: `_buckets` grows forever — every distinct sub
  or client IP that ever hits chat-send leaves a bucket permanently). Constructor gains
  `clock: Callable[[], float] = time.monotonic`; `check()` uses it instead of calling
  `time.monotonic()` directly. Eviction is amortized inside `check()` under the existing lock:
  a call counter; every `_EVICT_EVERY = 512` checks, drop every bucket whose
  `now - last_refill > _capacity / _refill_per_second` (fully refilled — indistinguishable from
  a fresh bucket, so dropping is lossless by construction; state that invariant in the
  docstring). Module constant, no config knob.
- [ ] **Step 1 (RED):** in `tests/test_api_auth.py`: (a) an app built with
  `deploy_mode="ec2", identity_mode="auth0"` (+ the auth0 fields) returns 404 on all THREE of
  `/docs`, `/redoc`, `/openapi.json`; (b) the default local app still serves all three; (c)
  limiter: insert 600 distinct keys via `check()`, advance the injected clock past the
  full-refill window, run 512 more checks on one key → assert `len(limiter._buckets)` collapsed
  to ~1 (white-box, matching the concurrency test's existing style); (d) a NOT-yet-refilled
  bucket survives eviction (advance the clock only partially, assert its partial token count is
  preserved through a sweep — the lossless invariant).
- [ ] **Step 2:** RED. Capture. **Step 3:** Implement. **Step 4:** GREEN; offline suite; ruff.
  **Commit** — `fix(api): gate docs surfaces outside local; evict idle rate-limit buckets`

### Task 3: Worker claim role + boot privilege probe (what makes the worker real on RDS)

**Files:** create `migrations/versions/0009_worker_claim_role.py`,
`tests/test_boot_privileges.py`; modify `core/db.py`, `scripts/memory_worker.py`, `api/app.py`
(one probe call), `tests/test_memory_worker.py`, `tests/test_migrations.py`.

**The problem (verified against current code, not just the recon):** the worker's claim query
runs on the raw engine connection with NO role switch (`memory_worker.py`'s own docstring:
"engine.begin(), no SET LOCAL ROLE... the same privileged posture alembic upgrade uses") and
`memory_outbox` is `FORCE ROW LEVEL SECURITY` with an owner-only policy (migration 0008). That
works on compose because the compose DSN user is a true superuser (RLS never applies). **RDS has
no superuser and cannot grant BYPASSRLS** (a Postgres superuser-only attribute; `rds_superuser`
is not one). On RDS the claim query silently returns zero rows forever — the worker looks
healthy and never distills anything, P13's named "load-bearing finding requiring escalation"
scenario arriving on schedule. This task makes claim visibility an explicit, granted privilege
instead of an accident of superuser-ness.

**Design:**

1. **Migration 0009** (house style of 0005–0008: Postgres-only guard, WHY-docstring):

   ```sql
   DO $$ BEGIN CREATE ROLE poseidon_worker NOLOGIN;
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
   GRANT SELECT, UPDATE ON memory_outbox TO poseidon_worker;   -- FOR UPDATE needs both
   CREATE POLICY memory_outbox_worker ON memory_outbox
       TO poseidon_worker USING (true) WITH CHECK (true);
   GRANT poseidon_worker TO CURRENT_USER;  -- the migration DSN user IS the worker DSN user
                                           -- in every habitat (compose + RDS); SET ROLE
                                           -- requires membership for non-superusers
   ```

   Downgrade: drop policy, revoke grants, drop role (guarded). The role gets NOTHING on any
   other table — the worker's per-user reads/writes stay on the `rls_transaction` path exactly
   as P13 built them; this widens ONE query's visibility, not the worker's privilege generally.
   Mirror 0005's schema-grant house style if it granted schema USAGE to `poseidon_app`.
2. **Claim path:** inside `claim_idle_conversations`'s `engine.begin()` block, execute
   `SET LOCAL ROLE poseidon_worker` (same quoting mechanics as `rls_transaction`'s existing
   `SET LOCAL ROLE` line) before the claim SELECT. Update the module docstring's privilege
   story: the claim now runs under an explicitly-granted worker role in every habitat — on
   compose the DSN could still bypass RLS, but the claim no longer depends on that.
3. **Boot probe — the fused P10 I-5 + P13 T4-M6 check.** New `core/db.py` function:

   ```python
   def assert_boot_privileges(engine, settings, *, require_worker_role: bool = False) -> None
   ```

   One connection, three checks, each failing with a RuntimeError that names the variable and
   the fix: (a) `SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user` —
   if privileged AND `settings.database_app_role is None`, refuse: RLS is silently disabled
   (P10 I-5's exact scenario); (b) if `database_app_role` is set, its role must exist in
   `pg_roles` (else `SET LOCAL ROLE` fails at first use — at boot, not mid-request, is the
   point); (c) `require_worker_role=True` (the worker's flavor): `poseidon_worker` must exist
   AND `SET LOCAL ROLE poseidon_worker` must succeed inside a probe transaction that is rolled
   back — the direct empirical rehearsal of the claim path (P13 T4-M6's silent-failure-mode
   probe, fused here as one boot check). Wire it: `create_app`'s live-mode wiring calls it
   (`require_worker_role=False`) right after the engine is built — mock-mode/offline apps build
   no engine and never hit it; `memory_worker`'s startup calls it (`require_worker_role=True`)
   before the first cycle.
4. **Rewrite the claim-privilege pin test** (`test_the_claim_connection_actually_bypasses_row_
   level_security` in `tests/test_memory_worker.py`): the claim's cross-user visibility is now
   POLICY-granted, not superuser-inherited. New assertions (pg): seed outbox rows for two users;
   on a raw connection under `SET LOCAL ROLE poseidon_worker` the claim query sees BOTH; under
   `SET LOCAL ROLE poseidon_app` (negative control) it sees ZERO — proving the visibility comes
   from the worker policy, nothing else. Superuser-ness of the compose DSN is no longer
   load-bearing and no longer pinned.

- [ ] **Step 1 (RED):** `tests/test_boot_privileges.py` (pg): (a) probe passes on the real
  compose DSN with default settings; (b) `database_app_role=None` on the privileged compose DSN
  → RuntimeError naming `DATABASE_APP_ROLE`; (c) `database_app_role="does_not_exist"` →
  RuntimeError naming the missing role; (d) `require_worker_role=True` passes post-0009 and
  fails informatively against a database where the role is absent (drop it in a scratch schema
  transaction or assert the pre-migration state via the migration test instead — implementer's
  call, disclosed). `tests/test_memory_worker.py`: the rewritten pin test above + one
  end-to-end worker cycle still distilling under the new claim path. `tests/test_migrations.py`
  extended to 0009 (upgrade + downgrade round-trip, matching the file's established pattern).
- [ ] **Step 2:** RED (pg suite). Capture. **Step 3:** Implement migration → claim change →
  probe → wiring, in that order, re-running the worker suite between pieces.
- [ ] **Step 4:** Apply 0009 to compose; GREEN pg + offline; ruff. **Commit** —
  `feat(worker): explicit poseidon_worker claim role + boot privilege probe (RDS has no superuser)`

### Task 3b: app-role membership grant + probe rehearsal (plan amendment, 2026-08-05 — Task 5's rehearsal surfaced it)

**Model: opus implementer, opus reviewer** (same domain and same reasoning as Task 3).

**The problem (Task 5 implementer's disclosure, symmetric twin of Task 3's):** migration 0004
created `poseidon_app` but never granted membership to the migration/DSN user the way 0009 does
for `poseidon_worker`. On compose the superuser DSN can `SET ROLE` to anything, so it never
surfaced; on RDS (non-superuser master) every request's `rls_transaction` would fail at
`SET LOCAL ROLE poseidon_app` — while Task 3's boot probe passes, because its check (b) verifies
the role EXISTS, not that the switch works. The phase's own symmetric-pair rule applies: the
worker role got a membership grant AND a rehearsed switch; the app role must get both too.

**Files:** create `backend/migrations/versions/0010_app_role_membership.py`; modify
`backend/poseidon/core/db.py` (extend the probe: when `database_app_role` is set, rehearse
`SET LOCAL ROLE <app_role>` in a rolled-back transaction — the exact mechanics of check (c),
refusing with a message naming the role, `DATABASE_APP_ROLE`, and migration 0010),
`backend/tests/test_migrations.py` (extend the round-trip: `pg_auth_members` membership pin for
`poseidon_app`, mirroring 0009's — `pg_has_role` is vacuous under a superuser, per Task 3's
finding), `backend/tests/test_boot_privileges.py` (the RDS-shaped rehearsal for the app role:
throwaway non-privileged LOGIN role → refuse → `GRANT poseidon_app` → pass, mirroring the
worker-role rehearsal incl. its uuid-suffix and skip hygiene).

**Migration 0010:** `GRANT poseidon_app TO CURRENT_USER` with 0009's WHY comment style
(idempotent by nature — re-granting is a no-op; downgrade revokes, guarded like 0009's fixed
form). Nothing else.

- [ ] **Step 1 (RED):** the membership pin (RED on compose — the row genuinely doesn't exist
  pre-0010), the probe-rehearsal refusal test (RED — probe currently passes with a
  switch-incapable DSN). Capture.
- [ ] **Step 2:** implement migration + probe extension. Apply 0010 to compose.
- [ ] **Step 3:** GREEN pg + offline; ruff. **Commit** —
  `fix(db): grant poseidon_app membership + rehearse the app-role switch at boot (RDS)`

### Task 4: The architecture-fitness test file

**Files:** create `tests/test_architecture_fitness.py`; `core/db.py` ONLY IF the
DATABASE_URL-actionability probe fails (see below).

One file for the invariants that guard the codebase's shape (P9 T3-M4 + P10 routings), plus a
module docstring acting as the **fragility registry**: a pointed list naming the other
fragile white-box tests and where they live (`tests/test_history_cutover.py`'s
persist-before-terminal-frame call-order test; `tests/test_api_auth.py`'s route-classification
sweep) so the next person extending this file knows the full set — the disclosed deviation from
the carryforward's literal one-file wording.

Contents (each its own test):

1. **No-FastAPI-in-core:** walk `backend/poseidon/core/**/*.py` with `ast`, assert no module
   imports `fastapi` or `starlette` (imports of `anyio`/`httpx` are fine and expected). The
   invariant holds today (recon verified zero hits) — this pins it.
2. **Route-sweep floor:** import the sweep helper `tests/test_api_auth.py` already uses
   (`_IncludedRouter.effective_route_contexts()` — reuse, don't duplicate) and assert the
   swept `/api` route count is `>= N`, N pinned from a real run at implementation time with a
   comment naming the count's provenance. A refactor that silently drops routes from the sweep
   (the exact failure mode P9's re-review feared) now fails loudly.
3. **Malformed `DATABASE_URL` is actionable (P10 M3):** construct the engine path with
   `DATABASE_URL="not-a-dsn"` and assert the raised error's message contains the string
   `DATABASE_URL` and a hint of the expected form. RED-probe first: if the current error
   already satisfies this, the test simply pins it and `core/db.py` is untouched; if not, add
   a minimal try/except re-raise wrap at the engine-construction site.
4. **`ruff format --check` gate (P10 M4):** a subprocess test running
   `ruff format --check` over `backend/poseidon`. **Conditional arming:** the implementer first
   runs it manually; if the tree is already clean, the test ships armed; if not (the repo-wide
   format commit is a parked, Carlos-owned pile item), the test ships with
   `pytest.mark.skip(reason="armed after the repo-wide ruff-format commit (blocked-on-Carlos pile item)")`
   and the report says which branch was taken. Never format the tree in this task.

- [ ] **Step 1 (RED):** write all four; run; capture which are RED/GREEN/skipped (1 and 2
  should pass immediately — they pin; 3 is a genuine probe; 4 per its rule).
- [ ] **Step 2:** implement the `core/db.py` wrap only if 3 demanded it. **Step 3:** GREEN;
  offline suite; ruff. **Commit** — `test(fitness): architecture-fitness invariants file`

### Task 5: Production image, SPA serving, EC2 compose, Caddy

**Files:** create `infra/Dockerfile`, `infra/Caddyfile`, `infra/docker-compose.ec2.yml`,
`tests/test_static_serving.py`; modify `core/config.py` (`static_dir` field), `api/app.py`
(mount only), `backend/.env.example`.

**Design:**

1. **`Settings.static_dir: str | None = None`** (`STATIC_DIR` env). When set, `create_app`
   mounts — AFTER every router registration, the last mount in the factory (doc 07 §2's
   "StaticFiles mounted after all `/api/*` routes"; Starlette matches in registration order,
   so `/api/*` and `/health/*` always win):

   ```python
   if settings.static_dir:
       app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="spa")
   ```

   `html=True` serves `index.html` at `/`; no deep-link fallback is needed — the SPA has no
   client-side router today (verified; note it in a comment so whoever adds one knows this is
   the seam). Local dev is untouched (`static_dir` unset → no mount, Vite proxy as today).
2. **`infra/Dockerfile`** (doc 07 §2's contract, made concrete):
   - Stage 1 `node:22-alpine` (pinned per the infra-polish carryforward): copy `frontend/`,
     `ARG VITE_AUTH0_DOMAIN VITE_AUTH0_CLIENT_ID VITE_AUTH0_AUDIENCE` exported as env for the
     build, `npm ci && npm run build`.
   - Stage 2 `python:3.12-slim`: apt WeasyPrint natives (`libpango-1.0-0 libpangocairo-1.0-0
     libcairo2 libgdk-pixbuf-2.0-0 shared-mime-info fonts-dejavu-core` — implementer verifies
     the set against the dev Dockerfile and WeasyPrint 62 docs), `COPY backend/ /app/`,
     `COPY ontology/ /ontology/` (**layout is load-bearing:** the ontology loader anchors
     `parents[4]` from its own file — with the package at `/app/poseidon/...` the repo root
     resolves to `/`, so the vendored ontology MUST land at `/ontology`, byte-identical to the
     compose mount's reasoning at `infra/docker-compose.yml:55-63` — restate it in a Dockerfile
     comment), copy stage-1 `dist` → `/app/static`, `pip install /app` (source-tree layout
     keeps `models.yml`/`prompts/*.md` resolvable, sidestepping the known pyproject
     package-data gap — name that gap in a comment; it stays parked for whenever a
     wheel-install image matters), non-root `USER app`, `EXPOSE 8000`, default `CMD` uvicorn
     with `--factory poseidon.api.app:create_app`.
   - Image tag convention: `poseidon:<git-sha>` — rollback is the previous sha's tag.
3. **`infra/docker-compose.ec2.yml`:** three services, no db, no minio.
   `caddy` (image `caddy:2-alpine`, ports 80/443, mounts `./Caddyfile` + a `caddy_data` volume
   for certs, `POSEIDON_DOMAIN` from env); `backend` (image `${POSEIDON_IMAGE}`, `env_file:
   /etc/poseidon/backend.env`, command mirroring dev compose's start chain: `alembic upgrade
   head && python -m poseidon.scripts.seed_synthetic && uvicorn ...` — the seeder is already
   proven re-run-safe by every dev `compose up`, and doc 08's EC2 section explicitly wants
   "RDS with migrations + synthetic load"; no published ports, caddy reaches it on the compose
   network); `worker` (same image, `python -m poseidon.scripts.memory_worker`, same env_file,
   `depends_on: backend` with the same migration-ownership comment as dev compose — migrations
   keep exactly one owner). Env baked in the file (non-secret): `DEPLOY_MODE=ec2`,
   `DATA_BACKEND=synthetic`, `IDENTITY_MODE=auth0`, `LLM_PROFILE=bedrock`, `LLM_MODE=live`,
   `CHAT_MODE=live` (backend only), `STATIC_DIR=/app/static`, `TOOL_TRANSPORT_PERPLEXITY=
   direct`. From the env_file (Carlos-authored on the box): `DATABASE_URL`, `AUTH0_DOMAIN`,
   `AUTH0_AUDIENCE`, `AUTH0_CLIENT_ID`, `S3_BUCKET`, optional `PERPLEXITY_API_KEY`.
   `S3_ENDPOINT_URL`/`S3_ACCESS_KEY`/`S3_SECRET_KEY` stay UNSET — real S3 through the instance
   profile (the Optional fields already permit it; boto3's default chain does the rest).
4. **`infra/Caddyfile`:**

   ```
   {$POSEIDON_DOMAIN} {
       encode gzip
       reverse_proxy backend:8000 {
           flush_interval -1
       }
   }
   ```

   `flush_interval -1` is the SSE requirement (doc 07 §5's "no buffering"); automatic TLS
   comes from the domain being real (Task 7's DNS decision).

- [ ] **Step 1 (RED):** `tests/test_static_serving.py`: build an app with `static_dir` pointing
  at a tmp dir containing `index.html` + one asset → `/` serves the index, the asset serves,
  and `/api/me` + `/health/live` still resolve to the API (the must-not-shadow pair); an app
  with `static_dir=None` has no `spa` mount (route-name scan); the fitness route-sweep still
  passes with the mount present (mounts are not APIRoutes — pin that assumption).
- [ ] **Step 2:** RED. Capture. **Step 3:** implement field + mount; author Dockerfile,
  Caddyfile, compose file; extend `.env.example` (STATIC_DIR row + an `# --- EC2 ---` block
  naming the env_file split and that S3/Bedrock need no keys on EC2).
- [ ] **Step 4 (local rehearsal — the offline gate for this task):** `docker build -f
  infra/Dockerfile -t poseidon:local-rehearsal .` succeeds (stub VITE args); `docker compose -f
  infra/docker-compose.ec2.yml config` validates with placeholder env; run the PRODUCTION image
  against the dev stack's db/minio (one `docker run` with the dev compose env values,
  `IDENTITY_MODE=disabled LLM_MODE=stub STATIC_DIR=/app/static`) → `/health/ready` 200, `/`
  serves the built SPA, one scripted chat turn answers — the image-layout proof (ontology
  resolution, WeasyPrint imports, static serving) with zero AWS involvement. Capture evidence
  in the report.
- [ ] **Step 5:** pytest GREEN; suites; ruff. **Commit** —
  `feat(infra): production image, SPA serving, EC2 compose + Caddy`

### Task 6: Provisioning scripts, runbooks, docs amendment

**Files:** create `infra/aws/01-security-groups.sh`, `02-rds.sh`, `03-s3.sh`, `04-iam.sh`,
`05-ec2.sh`, `06-budget.sh`, `infra/runbooks/deploy-ec2.md`, `infra/runbooks/smoke.md`; modify
`docs/architecture/00-overview.md`, `07-infrastructure.md`, `08-build-phases.md`,
`backend/.env.example` (EC2 rows if not finished in T5).

1. **Scripts** — small idempotent bash (aws cli v2, `--region us-east-1`, every script
   check-before-create and safe to re-run; NO credentials or account-specific values hardcoded —
   parameters via environment/flags; Carlos runs them in Task 7, they are authored and
   shell-checked offline now). Contents: security groups (443/80 open, 22 restricted to
   `${ADMIN_CIDR}`, RDS SG admitting only the instance SG); RDS `db.t3.micro` Postgres 16,
   automated backups ON, `backup-retention-period 7`, deletion protection ON (satisfies RPO 24h);
   S3 bucket + lifecycle expiry `${RETENTION_ARTIFACT_DAYS:-90}` + public-access-block; IAM role
   + instance profile — Bedrock statements MIRRORED from the proven 2026-08-03
   `PoseidonBedrockInvoke` policy (inference-profile ARNs + all-region `foundation-model/*` +
   the two `aws-marketplace` actions — the exact cross-region gotchas already learned, cite the
   task file) + `s3:GetObject/PutObject/ListBucket` on the artifact bucket only; EC2 launch
   (recommend `t3.small` — doc 07's `t3.micro` predates the worker container; both stated, the
   choice is Carlos's in Task 7) with Elastic IP and a user-data script installing docker +
   compose plugin + a 2G swapfile; budget check script printing the existing AWS Budget alert
   (created 2026-08-03) for verification rather than creating a duplicate.
2. **`infra/runbooks/deploy-ec2.md`** — account prep (pointer to `infra/aws/` script order),
   image build with the `VITE_AUTH0_*` build args + push to ECR (repo creation + login one-liner
   included; local build, not on-box — a t3-class instance should not be doing npm builds),
   `/etc/poseidon/backend.env` contract (every key listed with which are secret and that
   Bedrock/S3 need none), first deploy (`docker compose -f docker-compose.ec2.yml up -d`),
   verify (`/health/ready`, `docker compose logs`), **the identity checklist line: deployed
   env MUST say `IDENTITY_MODE=auth0` — never `disabled` outside local** (the P9 runbook
   carryforward, enforced here and in smoke.md), rollback (retag previous image sha + paired
   `alembic downgrade` per doc 07 §4's migration-rollback contract — the pairing rule restated),
   RDS restore path (point-in-time restore to a new instance + env_file DSN swap; written to
   meet RTO next-business-day; noted as rehearsable-on-request, spins temporary RDS spend).
3. **`infra/runbooks/smoke.md`** — the either-target checklist doc 07 §8 has owed since it was
   written, target-parameterized (a URL): health endpoints; login (auth0: real round-trip incl.
   a role-less user's 403; spcs rows marked "Phase 15"); all three flows on synthetic data
   (default Q&A incl. one carry-over pivot; existing-customer brief; prospect brief); artifact
   PDF download; `turn_run` + `llm_calls` + `message_feedback` rows present for the session's
   turns; memory distillation fires (lower `MEMORY_IDLE_MINUTES` temporarily, watch a
   `kind='memory_update'` `turn_run` row + new memory version appear, restore the setting);
   `/docs` 404s outside local; rate-limit sanity (burst chat sends → 429 with Retry-After);
   free-disk + cert-issuance check rows for the EC2 habitat.
4. **Docs amendment (the reorder, applied ONLY after Carlos's go — exact text):**
   - `00-overview.md` D8 becomes:
     > - D8 (revised 2026-08-05) One container image, two deploy targets sharing one
     >   environment contract. **EC2 deploys first** (owner decision at Phase 13 closure: get a
     >   public, Auth0-gated instance live on proven AWS footing before the corporate-platform
     >   work); **SPCS remains the corporate primary target**, deployed after, with the
     >   Snowflake data backend onlined by a separate Snowflake-side effort. Dev/prod parity
     >   over premature scale-out, unchanged.
   - `08-build-phases.md`: the "Shape of the plan" sentence and the mermaid edges reorder to
     `P13 --> P14[14 EC2 deploy] --> P15[15 SPCS deploy] --> P16[16 Snowflake backend]`; the
     phase sections renumber accordingly — **new Phase 14 (EC2)** = the old Phase 16 section
     plus: a "Preparation deliverables" line naming the P9 hardening hard gate (JWKS
     negative-cache/off-loop, docs-surface gating, rate-limiter eviction, worker claim role +
     boot probe, architecture-fitness file — landed before the auth0-mode deploy, as that gate
     requires), `smoke.md` named as a deliverable, the worker container placed explicitly in
     the EC2 compose stack, RPO/RTO restated as the owner numbers (24h / next business day, RDS
     automated backups), and "optional `DATA_BACKEND=snowflake` flip" struck (that flip now
     belongs to the Snowflake-side effort); **new Phase 15 (SPCS)** = the old Phase 14 section
     verbatim (Cortex/D33 prep intact) with "depends on: 13" and a note that `smoke.md` already
     exists by then; **new Phase 16 (Snowflake backend)** = the old Phase 15 section with
     "depends on: 15" and a line recording it is executed as a separate Snowflake-side effort;
     cut-over criteria's "post-P15" reference becomes "post-P16".
   - `07-infrastructure.md`: §1's compose listing gains the `worker` row (it predates P13);
     §3's table order flips the two target columns' "primary/secondary" labels to
     "first-deployed / corporate-primary" per D8-revised; §4's RPO/RTO paragraph gains one
     sentence: "Owner decision 2026-08-05: RPO 24h, RTO next business day (supersedes the
     defaults above; on EC2, satisfied by RDS automated daily backups)"; §5 gains the worker
     service in its topology sentence, the `VITE_AUTH0_*` build-arg note (disclosed deviation
     from the one-image ideal, runtime-config refactor named as the future fix), and the
     env-file-on-box secrets posture with "Secrets Manager arrives with the Snowflake
     credentials effort"; §8's runbook list marks `smoke.md` as shipped by this phase.
5. Runbook/script quality bar: someone who has never seen this repo can execute deploy-ec2.md
   top to bottom; every command copy-pasteable; no placeholder text without an explicit
   `<REPLACE: why>` marker.

- [ ] **Step 1:** author scripts (shellcheck-clean; a dry-run flag or check-mode where the CLI
  supports it) and both runbooks. **Step 2:** apply the docs amendment exactly as above.
- [ ] **Step 3:** ruff N/A; `shellcheck infra/aws/*.sh` clean (skip with disclosure if
  shellcheck unavailable on the machine); suites untouched (docs/scripts only — state it).
  **Commit** — `feat(infra): EC2 provisioning scripts + runbooks; docs: record the EC2-first reorder (D8 revised)`

### Task 6b: D16 `row_scope` mechanism (plan amendment — Carlos's review, F2 → option (b))

**Model: opus implementer, opus reviewer** — this touches certified-SQL generation and
fail-closed security semantics; wrong here means silent cross-scope data exposure at the future
flip.

**Files:** modify `core/ontology/models.py`, `core/data/specs.py`, `core/data/query_builder.py`,
`core/data/client.py`, `core/data/synthetic_client.py`,
`tasks/data_qa/skills/metric_query/skill.py`,
`tasks/customer_insight/skills/existing_customer_brief/tools/fetch_metrics.py`,
`tasks/customer_insight/skills/existing_customer_brief/tools/fetch_top_ports.py` (+ their
co-located `tests/test_tools.py`), `scripts/demo_query.py`, `tests/test_ontology_loader.py`,
`tests/test_query_builder_snapshots.py`; `core/ontology/loader.py` only if the optional field
needs loader-side handling beyond the model.

**What ships (doc 05 §4's dormant D16 hook, mechanism without policy):** an ontology entity MAY
declare `row_scope: {column, claim}`; the query builder then appends the scope predicate from
`UserContext` automatically. NO certified entity declares it — the certified `ontology.yml` is
NOT modified; the Snowflake-side effort later flips config, not code.

**Design:**

1. **`models.py`:** frozen `RowScope(BaseModel)` with `column: str` and
   `claim: Literal["sub", "email"]` (the two UserContext fields that can plausibly key a
   per-person scope today; widening the Literal later is a one-line change);
   `Entity.row_scope: RowScope | None = None`; an Entity-level validator rejecting a
   `row_scope.column` that is not one of the entity's dimension-role columns (a measure or
   unknown column is a certification error, caught at load, matching the loader's fail-fast
   posture).
2. **`specs.py`:** `scope_value: str | None = None` on `MetricQuerySpec` and
   `BreakdownQuerySpec`, matching the file's existing frozen style.
3. **`query_builder.py`:** a public helper
   `resolve_row_scope_value(entity_name: str, user: UserContext | None) -> str | None` —
   entity without `row_scope` → `None`; declared but `user is None` or the claim's value is
   empty → `SpecValidationError` naming the entity and stating the fail-closed rule; else the
   claim's value off `UserContext`. Enforcement in ALL FOUR builders, both directions of the
   symmetric pair: an entity WITH `row_scope` and no `scope_value` → `SpecValidationError`
   (fail closed — an unthreaded call path breaks loudly at flip time, never leaks); a
   `scope_value` supplied for an entity WITHOUT `row_scope` → `SpecValidationError` too (a
   caller believing scoping exists where it does not is a bug, not a no-op).
   `build_dimension_values_query` and `build_period_range_query` gain an optional
   `scope_value: str | None = None` parameter with the same rules; when declared and provided,
   every builder appends `<column> = <param>` through the existing parameterized-WHERE
   machinery (never string interpolation).
4. **`client.py` / `synthetic_client.py`:** `list_dimension_values` and `available_periods`
   gain `scope_value: str | None = None`, passed through to their builders (protocol + the one
   implementation).
5. **Thread the resolver at the spec-building call sites that own `ctx.user`:**
   `skill.py` (data_qa metric_query), `fetch_metrics.py`, `fetch_top_ports.py` — each passes
   `scope_value=resolve_row_scope_value(<entity>, ctx.user)` into its spec/client calls.
   `demo_query.py` (dev CLI, no user) passes an explicit `None` with a one-line comment noting
   it will fail loudly the day an entity it queries declares `row_scope` — deliberate.
6. **The flip stays a noticed event:** one pin test asserting every entity in the loaded
   vendored ontology has `row_scope is None`, with a comment saying removing this pin IS the
   deliberate act of onlining row scoping (this codebase's established pin-test convention).

- [ ] **Step 1 (RED):** `test_ontology_loader.py` — a fixture entity dict with a valid
  `row_scope` parses (column + claim round-trip); unknown column rejected at load; measure-role
  column rejected; the vendored-ontology all-None pin. `test_query_builder_snapshots.py` — a
  fixture entity WITH `row_scope`: metric + breakdown snapshots on BOTH dialects contain the
  predicate and its bind param; fail-closed matrix on all four builders (declared+missing →
  `SpecValidationError`; undeclared+supplied → `SpecValidationError`); existing snapshots
  byte-unchanged (the no-entity-declares regression pin). Resolver unit matrix
  (none-declared / declared+no-user / declared+empty-claim / declared+valid). Co-located tool
  tests: each threaded call site passes the resolver's value through (spy on the client/spec,
  assert `scope_value=None` today and that the resolver was consulted).
- [ ] **Step 2:** RED run. Capture. **Step 3:** implement in the file order above.
- [ ] **Step 4:** GREEN; full offline + pg suites byte-identical elsewhere; ruff. **Commit** —
  `feat(data): D16 row_scope mechanism — fail-closed scope predicate, no entity declares it yet`

**→ Final whole-phase review (opus) over Tasks 1–6b's commit range, then the fix wave +
re-review per standing SDD process, BEFORE the account-gated half begins.**

**Final-review fix wave (amendment, 2026-08-05 — sanctioned scope for the ONE wave):**
C1/C2/I2/I5/c-fixes in `infra/runbooks/deploy-ec2.md` + `infra/runbooks/smoke.md` (compose
interpolation export/`infra/.env` block; §7/§8 counts through `rls_transaction`; the
get-files-on-the-box step + one cwd convention; parallel rate-limit burst + spend note);
**C3 option (a)** — `backend/poseidon/api/app.py` (hoist the `ArtifactStore` construction out
of the `deploy_mode=="local"` branch into live wiring) + `backend/poseidon/core/chat/
orchestrator.py` (thread `artifacts=` into BOTH `SkillContext` sites, :582 and :1144) +
`backend/tests/test_live_chat_sse.py` (one covering test: a live-mode app constructs the store
and a brief turn's context receives it) — chosen because the amended doc 08 P14 gate mandates
artifact download and this phase provisions S3/IAM for it; I1 — `infra/aws/04-iam.sh` (ECR
statements: GetAuthorizationToken on `*`, Batch/GetDownloadUrlForLayer on the repo ARN); I3 —
`deploy-ec2.md` build prefixed `DOCKER_BUILDKIT=1` + the dockerignore header sentence
(`infra/Dockerfile.dockerignore`); I4 — the D16 flip checklist folded into `RowScope`'s
docstring (`backend/poseidon/core/ontology/models.py`), naming pipeline.py:481/487/501,
orchestrator.py:1313, existing_customer_brief/skill.py:409/413, and the five narrow-signature
fakes; m10 — `backend/poseidon/core/identity_auth0.py` docstring misquote (one line); m33 —
`docs/architecture/00-overview.md` two old-framing lines; reviewer recommendation 3 taken —
`infra/Caddyfile` `encode` gains a match-whitelist omitting `text/event-stream` (smoke §6
still verifies streaming live). **Amended post-wave (controller-ratified sanction gaps, both
C3-required, both the established disclosure pattern): `backend/poseidon/api/live_chat.py`
(ONE line — `artifacts=app_state.artifact_store` threaded into the existing `execute_turn`
call, the only place a real HTTP turn reaches it; identical in kind to P13's ratified
live_chat threading amendment) and `backend/tests/test_chat_e2e_scripted.py` (ONLY the two
pg flows that pinned `Artifact: skipped` — they now drop the store post-construction via a
documented helper so no automated test attempts a real S3 upload and every pinned number
stays byte-identical).** Nothing else.

### Task 7 (account-gated): AWS provisioning walkthrough — Carlos driving

Controller-led, one step at a time (his established preference: exactly one action, wait for
confirmation, then the next). Create
`docs/superpowers/plans/2026-08-XX-phase-14-ec2-live.task.md` at dispatch (the durable tracker,
same convention as `2026-08-03-aws-auth0-setup.task.md`; uncommitted). Credentials: Carlos
types them; they never pass through me or any file I write.

Sequence: ECR repo → local image build (with his tenant's `VITE_AUTH0_*` values) + push →
`01-security-groups.sh` → `02-rds.sh` (db.t3.micro; wait for available; create the app database)
→ `03-s3.sh` → `04-iam.sh` → **the TLS/DNS decision** (open question 2 below — resolved here at
the latest) → `05-ec2.sh` (instance size decision: t3.small recommended, micro is the doc
default) → DNS record to the Elastic IP → `06-budget.sh` verification. Every script result
verified before the next step; failures debugged live, findings recorded in the task file (the
2026-08-03 pattern that caught three real bugs).

### Task 8 (account-gated): Auth0 tenant day — Carlos driving

Same walkthrough discipline, same task file. **Tenant decision first** (open question 3): reuse
`dev-ndwxojqej5feb0s4` (trial expires ~2026-08-25) or create a fresh tenant as prod. Then, per
doc 05 §9 + the tenant-day carryforward checklist: SPA app callback/logout/web-origin URLs for
the deployed origin (**redirect_uri registered per environment** — the carryforward item,
verified against `window.location.origin` exactly); API identifier (`https://poseidon/api`
convention; beware the 2026-08-03 invisible-whitespace lesson — paste clean); post-login Action
emitting `https://wfscorp.com/custom-claims.roles` **as a JSON ARRAY, not a string** (the
second carryforward item — verify by decoding a real token, not by reading the Action source);
RBAC on; two test users (one role-less for the 403 gate); backend env values + `VITE_AUTH0_*`
build args recorded into the task file (values are public SPA config; still no secrets through
me). If the trial tenant is reused: note the expiry date in the task file as a standing risk.

### Task 9 (account-gated): First deploy, smoke, rollback — the phase gate

Execute `deploy-ec2.md` live with Carlos: env_file written by him on the box, compose up,
certificate issuance observed. Then `smoke.md` in full against the public URL — including the
Auth0 login round-trip, the role-less 403, all three flows on synthetic data, artifact
download, run-log + feedback rows, and **memory distillation firing on EC2** (the worker's
first production proof — the Task 3 claim-role change is what makes this pass on RDS). Then the
rollback rehearsal: deploy previous image sha + paired `alembic downgrade` (0009's downgrade
exists and is tested), verify, roll forward. Verify RDS automated backups are ON with the
stated retention (RPO 24h) and walk the restore runbook section verbally (RTO next business
day); an actual restore rehearsal is offered, not forced (temporary RDS spend — Carlos's call).
Findings → the task file; anything needing code → the standing fix-wave process.

**Phase gate (mirrors doc 08's amended P14 validate line):** smoke.md fully green on the public
URL; rollback rehearsed; RPO/RTO configuration verified and restore path documented; hardening
suite green (Tasks 1–4 are in the final review's range); `LLM_MODE=live` on Bedrock through the
instance profile with zero static AWS keys anywhere on the box.

## Open questions for the plan review — RESOLVED (Carlos, 2026-08-05 plan review)

> 1 → **(b)**: build the D16 `row_scope` mechanism now — added as Task 6b above.
> 2 → **undecided**; default recommendation stands (free dynamic-DNS subdomain, e.g. DuckDNS —
>   zero cost, real certificates; swapping to an owned domain later is env/config-only).
>   Finalized at Task 7's TLS/DNS step, BEFORE Task 8 configures Auth0 callbacks.
> 3 → **reuse** the dev trial tenant; its ~2026-08-25 expiry is recorded as a standing risk in
>   the Task 7–9 tracker file.
> 4 → **confirmed**: env-file-on-box; Secrets Manager deferred to the Snowflake-credentials
>   effort.
> 5 → **as is**: the WARNING stands; runbooks enforce.

The original questions as presented (historical record — none blocked Tasks 1–6):

1. **F2, "row-level data scoping by salesperson → fold into Phase 14":** the recon verified the
   new backend has NO salesperson hook (zero grep hits in `poseidon/**` and `ontology.yml`; the
   folded item presumably described the legacy app). Options: **(a)** drop F2 from this phase —
   the certified ontology has no salesperson column to scope by; revisit when the Snowflake-side
   effort certifies one (recommended); **(b)** build doc 05 §4's dormant D16 `row_scope`
   mechanism now (query-builder hook + tests against synthetic, no entity using it) so the
   Snowflake effort only flips config; **(c)** Carlos knows which column in the certified views
   this meant — then it routes to the Snowflake-side effort's certification work regardless.
2. **TLS/DNS:** Caddy's automatic certificates need a real hostname. (a) a cheap domain Carlos
   buys/owns; (b) a free dynamic-DNS subdomain (e.g. DuckDNS); (c) Caddy internal CA =
   browser warnings (fine for a smoke, ugly for demos). Auth0 callbacks also want the final
   origin — deciding before Task 8 avoids re-doing tenant config.
3. **Auth0 tenant:** reuse the dev trial tenant (expires ~2026-08-25) or create a fresh tenant
   now? Doc 05 §9 makes swapping env-only later, so reuse-now is cheap — but the expiry lands
   mid-user-testing if testing starts soon.
4. **Secrets posture:** the plan proposes env-file-on-box this phase (deviation 2, Secrets
   Manager deferred to the Snowflake-credentials effort). Confirm, or ask for Secrets Manager
   now (adds a boto3 secretsmanager code path + tests to Task 5's scope).
5. **`identity_mode=disabled` outside local:** keep P9's deliberate WARNING-not-gate (default;
   runbooks enforce), or escalate to a hard boot failure now?

## Self-review record (writing-plans checklist + [[plan-sanction-line-check]])

- Spec coverage: every item in the task directive mapped — JWKS hard gate (T1), fitness file
  (T4), DATABASE_APP_ROLE boot assertion fused with the pg_roles probe (T3), rate-limiter
  eviction (T2), docs-surface gating (T2), no Cortex (fence), synthetic stays (fence + compose),
  doc-08 EC2 shape incl. Budget alarm + deploy-ec2.md + smoke.md (T5/T6/T7), RPO/RTO owner
  numbers (constraints + T6 + T9), worker placed explicitly (T5 compose + doc 07 amendment),
  docs amendment as owner decision (T6, applied post-go), tenant-day checklist items (T8),
  never-disabled-outside-local runbook item (T6/deviation 4), offline vs account-gated split
  (T1–6 vs T7–9), F2 as an open question (OQ1).
- Sanction-line check: every file path named in any task step cross-checked against the
  sanctioned-modifications line; conceptual references resolved (the sweep helper = "import,
  not modify"; `test_dev_runner.py` deliberately NOT sanctioned — no disabled-mode hard fail).
- Symmetric pairs swept: all THREE docs surfaces gated (docs/redoc/openapi); negative cache
  bounded by BOTH TTL and size; probe wired at BOTH boot sites (app + worker); static mount
  proves BOTH serve-SPA and never-shadow-API; eviction proves BOTH stale-dropped and
  partial-preserved; rollback pairs image tag WITH alembic downgrade; Auth0 config pair
  (backend `AUTH0_*` env AND frontend `VITE_AUTH0_*` build args) named in both runbook and
  `.env.example`; claim-role test has BOTH the positive (worker sees all) and negative control
  (app role sees none).
