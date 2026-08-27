# Poseidon — chat-first sales intelligence for marine fuel

Poseidon is an ask-anything chat application over internal sales data and external market
research. A ChatGPT-style React interface sits in front of a **deterministic core**: Python
functions do all data access, arithmetic, and formatting, while the LLM acts only as a router
that picks skills and fills validated arguments. No model writes SQL, and no model invents a
number — every figure in an answer comes from certified queries against a governed semantic
layer, and every answer carries a proof block showing where it came from.

This repository replaces an earlier three-agent Streamlit dashboard, which still lives here
during cutover (see [Legacy application](#legacy-application) at the bottom).

## The three conversation flows

| Flow | How it starts | What happens |
|---|---|---|
| **Default chat** | Free text | Q&A over the certified data layer — *"top GP customers for Port of Singapore in April 2026"*. Follow-ups can pivot to external research on any entity that came back. |
| **New Customer Prospect** | Company name | Produces a prospect research brief, then lets you pivot into internal data — for example, which customers you already serve at a port the brief mentioned. |
| **Existing Customer** | Customer picker | Runs the analytics suite and produces a brief, then supports drill-downs into ports, lanes and metrics, plus live external questions. |

The chosen flow shapes **entry orchestration only**. After the first deliverable, the full skill
registry is available in every flow, with carry-over context — active customer, port, period —
maintained by a deterministic conversation-state layer rather than by the model's memory.

## How it works

- **Deterministic pre-parsing.** Dates, customer names and skill hints are resolved in Python
  before any model call. Fuzzy customer matching either resolves confidently or asks with
  clarification chips; it never guesses silently.
- **Skills, not prompts.** Every capability is a self-contained directory under
  `backend/poseidon/tasks/<task>/skills/<skill>/`, with its tools, prompts and tests co-located.
- **Certified queries only.** A vendored YAML ontology defines the entities, dimensions and
  measures the query builder is allowed to touch. Adding a table is a certification step, not a
  code change.
- **Provider-agnostic LLM layer.** One interface over AWS Bedrock and Snowflake Cortex, with
  roles mapped to tiers by config — a capable model routes and synthesises, cheaper models do
  grunt work.
- **Identity flows through everything.** A verified identity reaches row-level security, chat
  history and personalization alike, populated by Auth0 or by SPCS ingress.
- **Observability from day one.** Every turn writes a run-log row plus a child row per model
  call and per tool call. Thumbs up/down on any message links back to that run log and feeds a
  router-decision test pipeline.

## Quick start

Requires Docker Desktop, or Docker Engine with the Compose plugin.

```bash
docker compose -f infra/docker-compose.yml up --build
```

That brings up four services — Postgres, MinIO for object storage, the backend API, and the Vite
dev server:

| | |
|---|---|
| App | http://localhost:5173 |
| API | http://localhost:8000 |
| MinIO console | http://localhost:9001 |

**Data is already there.** The backend's start-up runs migrations and seeds a deterministic
synthetic dataset — roughly 24,000 marine sales rows and 16,200 GL rows generated from
`ontology/synthetic/profiles.yml`. The seeder skips when tables already hold rows, so restarting
never rewrites what a running demo is looking at. Local development runs against this synthetic
backend; Snowflake is the production data platform.

Out of the box the stack runs with identity disabled and the LLM in stub mode, so it works with
no credentials at all. Enabling real models or real login is described in
`infra/runbooks/local.md`.

Stop with `Ctrl+C`, or `docker compose -f infra/docker-compose.yml down`. Named volumes survive a
`down`; add `-v` to start genuinely clean.

## Tests

```bash
cd backend && python -m pytest      # offline suite — always green with no services running
cd frontend && npm test             # component and interaction tests
```

Three pytest markers gate tests needing more than a bare Python environment. Each **skips with a
reason** rather than failing when its dependency is absent, which is what keeps the offline suite
honest:

| Marker | Needs | Run it with |
|---|---|---|
| `pg` | Migrated, seeded Postgres | `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon python -m pytest -m pg` |
| `minio` | Reachable MinIO | `S3_ENDPOINT_URL=http://localhost:9000 S3_ACCESS_KEY=poseidon S3_SECRET_KEY=poseidon123 python -m pytest -m minio` |
| `pdf` | WeasyPrint native libraries | Easiest inside the container — see `infra/runbooks/local.md` |

The `pg` tests recompute every expectation in pure Python from the same generator that seeded the
database, then compare against what the certified SQL returned — so they test the query builder,
not a snapshot of itself.

## Repository layout

```
backend/poseidon/
  api/        FastAPI routes, SSE streaming, auth dependencies
  core/       chat orchestration, ontology loader, query builder, identity, LLM providers
  tasks/      skills, grouped by task: data_qa, customer_insight, research
  mcp/        MCP server integrations (Perplexity first)
  scripts/    seeding, demo CLI, operational scripts
  migrations/ Alembic migrations
frontend/src/ React + TypeScript UI, theme-token styling
ontology/     vendored certified semantic layer + synthetic data profiles
infra/        compose files, production Dockerfile, Caddy, AWS provisioning scripts, runbooks
docs/architecture/  the design documents this build follows
```

## Architecture documents

The build follows nine documents in `docs/architecture/`. Start with `00-overview.md`.

| | |
|---|---|
| `00-overview.md` | What Poseidon is, the flows, and the numbered design decisions |
| `01-frontend.md` | UI structure, theming, chat mechanics |
| `02-backend-skills.md` | Skill framework, conversation state, carry-over |
| `03-llm-routing.md` | Provider abstraction, router contract, prompt registry |
| `04-data-ontology.md` | Certified semantic layer and query building |
| `05-auth-identity.md` | Identity providers, row-level security, personalization |
| `06-observability.md` | Run log, feedback capture, router-decision tests |
| `07-infrastructure.md` | Deployment targets, environments, operational posture |
| `08-build-phases.md` | Phase-by-phase build plan and validation gates |

Operational runbooks live in `infra/runbooks/` — `local.md` for development, `deploy-ec2.md` for
deployment, `smoke.md` for post-deploy verification.

## Build status

Phases 0 through 13 are complete and merged: the compose stack, the certified ontology and
synthetic data, the skill framework, deterministic parsing, LLM routing, end-to-end chat,
research via Perplexity, both brief flows, identity, persistent history with row-level security,
observability, feedback capture, and per-user personalization.

Phase 14 puts a public, Auth0-gated instance on EC2. Its build half is complete — production
image, SPA serving, Caddy with streaming-safe proxying, provisioning scripts, and runbooks — and
the deployment itself is in progress. SPCS deployment and the Snowflake data backend follow.

`docs/architecture/08-build-phases.md` has the full plan and each phase's validation gate.

## Legacy application

The original Streamlit dashboard still sits at the repository root — `app.py`, `agents/`,
`auth.py`, `config.py`, `snowflake_client.py`, `pdf_generator.py`, and its own `tests/` — and is
retained until cutover. It is deliberately left untouched by the rewrite; treat it as read-only
history rather than as a component of the new system. `README_BUSINESS.md` and `CONFIG_GUIDE.md`
belong to it, as does the root `requirements.txt`. The new backend's environment contract is
`backend/.env.example`.
