# 07 — Infrastructure: Local-First, Then SPCS (Primary) and EC2 (Secondary)

Principle: one architecture, one container image, one environment contract, three habitats.
Everything is developed and validated locally with docker-compose, then deployed unchanged to
**Snowpark Container Services (SPCS) — the primary target, following the proven in-house
pattern** — with **EC2 as the secondary/alternative target**. Only configuration differs
(decision D8, revised).

## 1. Local development topology

```
docker-compose.yml
  db        postgres:16 + pgvector          ports 5432; schemas: app, synthetic
  minio     S3-compatible object store      ports 9000/9001; bucket: poseidon-artifacts
  backend   FastAPI (uvicorn, reload)       port 8000; depends_on db, minio
  frontend  Vite dev server                 port 5173; proxies /api -> backend
```

- First run: `docker compose up` → migrations apply (Alembic) → synthetic dataset generates and
  loads into the `synthetic` schema (doc 04 §4 — the standard local-development practice) →
  app is fully usable at `localhost:5173`.
- `IDENTITY_MODE=disabled` by default locally (fixed dev user + `X-Dev-User` act-as, doc 05
  §2); flip to `auth0` to exercise the real login against a dev tenant.
- LLM: `LLM_PROFILE=bedrock` with an IAM user's keys in `.env` (section 6), or
  `LLM_PROFILE=cortex` with Snowflake credentials. `LLM_MODE=stub` (recorded responses;
  default for tests) keeps the stack usable with zero credentials.
- MinIO stands in for S3 behind the same `boto3` client (endpoint-url config) — artifact code
  is identical in every habitat.

## 2. The container contract

One multi-stage Dockerfile (mirrors the wfs table-chatbot pattern and the current corporate
image conventions):

- **Stage 1 (node):** `npm ci && npm run build` → `frontend/dist` static SPA bundle.
- **Stage 2 (python:3.12-slim or the corporate base image):** install requirements + WeasyPrint
  native libraries (Pango/Cairo — carried from the current Dockerfile); copy `backend/`,
  `ontology/`, `config/`, and `frontend/dist`; run as a non-root user; serve API **and** the
  built SPA from one origin/port with `uvicorn` on 8000 (StaticFiles mounted after all `/api/*`
  routes).

One image serves every target; behavior differences are environment variables only (section 6).
The existing corporate pipeline (Bitbucket → JFrog/ECR, `bitbucket-pipelines.yml`) consumes the
same image, so the corporate deployment path stays compatible by construction.

## 3. Deployment targets at a glance

| | SPCS (primary) | EC2 (secondary) |
|---|---|---|
| Runs as | multi-container **service** in a compute pool | docker-compose behind Caddy |
| Data platform access | Snowpark session via auto-mounted OAuth token | Snowpark session via Secrets Manager credentials |
| App state (Postgres) | second container in the service, block volume (D20) | RDS Postgres + pgvector (D17) |
| Artifacts | MinIO container on a block volume (S3-over-EAI as config alternative) | S3 bucket + lifecycle rule |
| LLM default | Cortex (D21) | Bedrock via instance profile (D21) |
| Identity default | `spcs_ingress` (D22) | `auth0` |
| Outbound calls | External Access Integration (§4) | security-group egress |

## 4. SPCS deployment (primary)

Mirrors the wfs_work_structure pattern (container-agent-app / table-chatbot archetypes) with
Poseidon's service shape:

| Component | Value (convention) |
|---|---|
| Image repository | `SANDBOX.MCA.POSEIDON_REPO` |
| Registry URL | `<org>-<acct>.registry.snowflakecomputing.com/sandbox/mca/poseidon_repo` |
| Compute pool | `CONTAINER_BOX_POOL` (or a dedicated pool) |
| Service | `SANDBOX.MCA.POSEIDON` |
| Endpoint | `api`, port 8000, `public: true` |

**Service specification** (`infra/spcs_spec.yaml`, inlined into `CREATE SERVICE`):

- `containers`: `backend` (the app image; `DEPLOY_MODE=spcs`, `LLM_PROFILE=cortex`,
  `IDENTITY_MODE=spcs_ingress`, `DATA_BACKEND=snowflake` once onlined per doc 08), `db`
  (postgres:16 + pgvector), `minio`.
- `volumes` + `volumeMounts`: block volumes for `PGDATA` and the MinIO data dir — **decision
  D20**: the SPCS container filesystem is ephemeral, so app state (chat history, run log,
  feedback, user memory) lives in the in-service Postgres on a mounted volume, exactly the wfs
  state-DB-plus-volume pattern; Snowflake native/hybrid tables were rejected because they would
  fork the RLS + pgvector + JSONB schema for zero functional gain.
- `endpoints`: the single public `api` endpoint (serves SPA + API).

**Platform mechanics** (all from the wfs pattern):

- Snowflake session: `DEPLOY_MODE=spcs` makes the data client authenticate with the
  auto-mounted OAuth token, read **fresh from `/snowflake/session/token` on every connection**
  (the platform rotates it); `SNOWFLAKE_ACCOUNT`/`SNOWFLAKE_HOST` are injected by SPCS.
- Identity: the public endpoint authenticates visitors as Snowflake users at the platform edge
  and forwards the username in the `Sf-Context-Current-User` header → `IDENTITY_MODE=
  spcs_ingress` (doc 05 §2). `IDENTITY_MODE=auth0` over the same ingress is the documented
  coexistence option (the wfs table-chatbot posture).
- LLM: Cortex needs no key inside the platform (D21). Bedrock and Perplexity (and the Auth0
  JWKS fetch, if `auth0` mode) are outbound calls and require an **External Access
  Integration** on the service: start with the provisioned allow-all EAI (`ALLOW_ALL_EAI`, the
  wfs default), tighten to a named EAI with per-host network rules as a hardening step.

**Operating the in-service state** (`infra/runbooks/backup-restore-spcs.md`). A block volume is
durable storage, not a backup: it does not survive a dropped service, a corrupt write, or a bad
migration. So the Postgres and MinIO containers get an operations contract of their own.

- **Scheduled logical backups.** A `pg_dump --format=custom` of the app database runs on a
  schedule (default: every 6 hours) and is shipped **off-service** to an internal stage in the
  Snowflake account, keyed by timestamp; MinIO's artifact bucket is mirrored to the same stage on
  the same schedule. Backups off the service are the point — a dump sitting on the volume it is
  protecting is not a backup. Each run verifies its own dump (`pg_restore --list`) and fails loudly
  if the archive is unreadable.
- **Documented restore.** The runbook states the full path: create the service from the current
  image, `pg_restore` the chosen dump into the fresh `db` container, re-mirror the artifact bucket,
  verify with the `smoke.md` checklist. The restore is rehearsed as part of the deploy phase gate
  (doc 08 Phase 14) — an unrehearsed restore procedure is a hypothesis, not a procedure.
- **Volume expansion.** Block volume size is a property of the service specification; growing it is
  a service recreate from the spec with a larger `size` on the `PGDATA` volume, restoring from the
  most recent verified dump. Free space on both volumes is checked in the post-deploy smoke run so
  expansion is planned rather than discovered.
- **Migration rollback.** Every Alembic migration ships with a working `downgrade`. Rollback is
  therefore two paired steps: `alembic downgrade <rev>` then redeploy the **previous image tag**
  (the image and the schema revision move together; neither is rolled back alone). Migrations that
  cannot be reversed — a destructive column drop — are expand-and-contract instead, so the rollback
  path always exists.
- **RPO / RTO.** Defaults: **RPO 6 hours** (the backup interval — at most one interval of chat
  history and audit rows is lost) and **RTO 2 hours** (service recreate plus restore plus smoke).
  Both are stated so they can be argued with; the final targets are an **owner decision**, and
  tightening RPO means shortening the interval, which is a configuration change.

**Deploy flow** (`infra/runbooks/deploy-spcs.md`):

1. Build the image; `docker login <org>-<acct>.registry.snowflakecomputing.com`.
2. Tag and push to the image repository (layer-incremental on subsequent pushes).
3. `CREATE SERVICE ... IN COMPUTE POOL ... FROM SPECIFICATION $$...$$
   EXTERNAL_ACCESS_INTEGRATIONS = (...)` (drop first when redeploying).
4. Verify: `SYSTEM$GET_SERVICE_STATUS` → READY; `SYSTEM$GET_SERVICE_LOGS` per container;
   `SHOW ENDPOINTS IN SERVICE` → `ingress_url` (the public URL).
5. Operate: `ALTER SERVICE ... SUSPEND / RESUME` to control spend; redeploy = push new tag +
   recreate service.

Decision D32: the in-service Postgres and MinIO get scheduled logical backups shipped off-service,
a rehearsed restore, and stated RPO/RTO — a mounted volume protects against container restarts and
nothing else.

## 5. EC2 deployment (secondary)

```mermaid
flowchart LR
  U[Browser] -- HTTPS 443 --> RP[EC2: Caddy\nTLS + static frontend]
  RP --> BE[EC2: backend container]
  BE --> RDS[(RDS Postgres + pgvector)]
  BE --> S3[(S3: artifacts)]
  BE --> BR[Bedrock runtime]
  BE --> SF[(Snowflake)]
  BE --> SM[Secrets Manager]
  U -. OIDC .-> A0[Auth0 tenant]
```

- **EC2** (t3.micro to start): the same image via docker-compose; Caddy terminates TLS
  (automatic certificates) and proxies `/api` (SSE-friendly: no buffering, long read timeout).
- **RDS Postgres** with pgvector — decision D17: managed backups and restarts are not a place
  to economize. The `synthetic` schema exists there too, so a demo environment needs no
  Snowflake connectivity.
- **S3** artifacts bucket, pre-signed GETs (doc 05 §8), lifecycle expiry after N days
  (`RETENTION_ARTIFACT_DAYS`, doc 05 §7).
- **IAM instance profile**: `bedrock:InvokeModel*` scoped to the `models.yml` ids, the artifact
  bucket, `secretsmanager:GetSecretValue` — no long-lived keys on the box. Snowflake
  credentials come from Secrets Manager (`DEPLOY_MODE=ec2` merges the secret JSON over the
  `SNOWFLAKE_*` fields, the wfs `aws` convention).
- **Networking:** 443 open (22 restricted); RDS admits only the instance's security group.
- Defaults: `LLM_PROFILE=bedrock` (the natural AWS pairing — no external LLM key needed),
  `IDENTITY_MODE=auth0`.

## 6. Environment contract (12-factor; identical names in all habitats)

| Variable | Local default | SPCS | EC2 |
|----------|---------------|------|-----|
| `DEPLOY_MODE` | `local` | `spcs` | `ec2` |
| `DATABASE_URL` | compose `db` DSN | in-service `db` DSN | Secrets Manager |
| `S3_ENDPOINT_URL` / `S3_BUCKET` | minio / `poseidon-artifacts` | in-service minio / bucket | unset (real S3) / bucket |
| `DATA_BACKEND` | `synthetic` | `synthetic` → `snowflake` (doc 08 gate) | `synthetic` or `snowflake` |
| `SNOWFLAKE_*` | unset or password auth | injected by platform + OAuth token file | Secrets Manager |
| `IDENTITY_MODE` | `disabled` | `spcs_ingress` | `auth0` |
| `AUTH0_DOMAIN` / `AUTH0_AUDIENCE` / `AUTH0_CLIENT_ID` | dev tenant | only if `auth0` mode | prod tenant |
| `LLM_PROFILE` | `bedrock` (or `cortex`) | `cortex` | `bedrock` |
| `LLM_MODE` | `live` or `stub` | `live` | `live` |
| `LLM_PROVIDER_<ROLE>` / `LLM_MODEL_<ROLE>` | unset (profile defaults) | optional overrides | optional overrides |
| `TOOL_TRANSPORT_PERPLEXITY` | `direct` | `direct` | `direct` |
| `PERPLEXITY_API_KEY` | `.env` | service secret/env | Secrets Manager |
| `MEMORY_MAX_CHARS` / `MEMORY_KEEP_VERSIONS` | `8000` / `20` | same | same |
| `MEMORY_IDLE_MINUTES` / `MEMORY_MAX_ATTEMPTS` | `30` / `5` | same | same |
| `RETENTION_AUDIT_DAYS` / `RETENTION_ARTIFACT_DAYS` | `400` / `90` | same | same |
| `BACKUP_INTERVAL_HOURS` / `BACKUP_TARGET` | unset (no-op locally) | `6` / internal stage | `6` / S3 prefix |

Startup validates the full schema with pydantic-settings and **crashes on any missing or
malformed value** — no half-configured server ever accepts traffic. `.env.example` is
maintained as part of the definition of done for any config change.

## 7. Hands-on validation path with trial accounts

A complete rehearsal of the architecture on free/trial tiers, before touching corporate
accounts:

1. **Auth0 free tenant**: SPA app (callbacks `http://localhost:5173`, later the deployed URLs)
   → API identifier `https://poseidon/api` → post-login Action adding `Poseidon:Sales` to
   `https://wfscorp.com/custom-claims.roles` → two test users (one role-less, to verify 403).
2. **Snowflake trial account**: database/schema, image repository, compute pool, an external
   access integration, and the certified views loaded from the synthetic dataset — then a full
   SPCS deploy rehearsal (§4) ending at a working `ingress_url`.
3. **AWS free-tier account**: Bedrock model access in `us-east-1` (Claude + Nova families) →
   IAM dev user for local `.env` → later EC2 t3.micro + RDS db.t3.micro + one S3 bucket +
   instance profile (§5).
4. **Cost guardrails**: AWS Budget alert; compute pool `SUSPEND` when idle; small tiers for
   development-loop LLM calls; `router_live` suites marker-gated so model spend is always a
   deliberate act.
5. Promotion to corporate accounts is an env-var swap (section 6) — nothing in the code knows
   which tenant or account it runs in.

## 8. Runbooks (deliverables of the deploy phases)

- `infra/runbooks/local.md` — clean-machine bring-up, synthetic regeneration, stub vs live LLM.
- `infra/runbooks/deploy-spcs.md` — image push, service spec, EAI, identity mode, verify,
  suspend/resume, rollback (previous image tag).
- `infra/runbooks/backup-restore-spcs.md` — schedule and verify the `pg_dump` + artifact mirror,
  restore into a fresh service, volume expansion, migration rollback (`alembic downgrade` +
  previous image tag), and the RPO/RTO the procedure is written to meet (§4).
- `infra/runbooks/deploy-ec2.md` — account prep, provisioning scripts (small idempotent CLI
  scripts; Terraform deliberately deferred until the topology stabilizes — decision D18), TLS,
  first deploy, rollback.
- `infra/runbooks/smoke.md` — post-deploy checklist run against either target's URL: health
  endpoints, login, all three flows, artifact download, run-log and feedback rows verified.
