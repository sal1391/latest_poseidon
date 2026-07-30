# Local development runbook

Operating instructions for running Poseidon on a local machine, either via
Docker Compose or natively.

## Prerequisites

Choose one of:

- **Docker Desktop** (or an equivalent Docker Engine with the Compose plugin)
  for the containerized path.
- **Native tooling**: Python 3.11+ and Node 20+ for the fallback path below.

## Compose bring-up

From the repo root:

```bash
docker compose -f infra/docker-compose.yml up --build
```

This starts four services: `db` (Postgres with pgvector), `minio` (S3-compatible
object storage), `backend` (runs `alembic upgrade head`, then seeds the
synthetic dataset, then serves the API via uvicorn with `--reload`), and
`frontend` (Vite dev server).

Once the stack is up:

- App: `http://localhost:5173`
- API: `http://localhost:8000`
- MinIO console: `http://localhost:9001`

Stop the stack with `Ctrl+C`, or `docker compose -f infra/docker-compose.yml down`
from another terminal. `down` alone leaves the named volumes (`pgdata`,
`miniodata`) intact, so database contents and uploaded objects survive a
restart; add `-v` to `down` to also drop those volumes and start clean.

## Synthetic data

The local database ships with data. The `backend` service's start-up command is
`alembic upgrade head && python -m poseidon.scripts.seed_synthetic && uvicorn …`,
so a `docker compose up` on an empty volume leaves you with a queryable
`synthetic` schema — roughly 24,000 marine sales rows and 16,200 GL rows,
generated deterministically from `ontology/synthetic/profiles.yml` — with no
manual step. The backend logs show all three phases in order:

```
INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, synthetic schema
seeded synthetic: 24000 sales rows / 16200 GL rows, checksum 221813a4…
INFO:     Application startup complete.
```

The seeder **skips when the tables already hold rows**, printing
`already seeded (24000 sales rows / 16200 GL rows); use --force to truncate and
reload`. That is what makes it safe on the start-up path: restarting the backend
never rewrites the data a running demo is looking at.

Note that compose bind-mounts `../ontology` at `/ontology` (read-only) alongside
`../backend` at `/app`. Both the ontology loader and the generator resolve their
default paths relative to the repo root, which is `/` inside that container — so
without that mount the seeder cannot find `profiles.yml`.

### Reseeding by hand

Point `DATABASE_URL` at the compose database and run the module from `backend/`:

```bash
cd backend
export DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon

python -m poseidon.scripts.seed_synthetic            # skips if already populated
python -m poseidon.scripts.seed_synthetic --force    # truncate + reload (default seed)
python -m poseidon.scripts.seed_synthetic --force --seed 7   # a different dataset
```

Each successful load prints the row counts and a `sha256` checksum of the
generated dataset. The generator is a pure function of (profile file, seed), so
that checksum is the determinism proof: `--force --seed 7` followed by `--force`
restores the original dataset byte for byte, and the checksum comes back
identical. It is also platform-independent — seeding from a Windows host and
from inside the Linux container produce the same digest.

`--force` truncates both tables first, so the schema only ever holds one
complete dataset — never a partial load, never two seeds mixed together.

### Querying it

`python -m poseidon.scripts.demo_query` (same `DATABASE_URL`) is the human smoke
test. It runs the real `SyntheticDataClient` against the real database and
prints the available period range, the six certified metrics for prior-year vs
year-to-date, and the top-5 customers by gross profit at the Port of Singapore
for April 2026. If that output looks right, the whole chain — ontology, query
builder, seeded schema, client — is working.

### Test markers: `pg`, `minio`, `pdf`

Three pytest markers (registered in `backend/pyproject.toml`) gate tests that
need something beyond the plain Python environment. Each one skips with an
actionable reason — never an error — when its dependency is not available, so
the plain `python -m pytest` suite always stays green offline.

**`pg`** — needs a reachable, migrated, seeded Postgres (`DATABASE_URL`).
`backend/tests/test_synthetic_client_pg.py`, plus the `pg`-marked tests mixed
into some `poseidon/tasks/**/tests/test_*.py` modules, recompute every
expectation in pure Python from the same generator the seeder used, then
compare against what the certified SQL returns:

```bash
cd backend
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \
  python -m pytest -m pg -v
```

These tests read the database, they never seed it — migrate and seed first.
Without a reachable database (or no `DATABASE_URL` at all) they skip after a
2-second connect timeout.

**`minio`** — needs a reachable MinIO (`S3_ENDPOINT_URL`, plus
`S3_ACCESS_KEY`/`S3_SECRET_KEY` for the local container's static credentials —
see `backend/.env.example`). `backend/tests/test_artifact_store.py` exercises
`ArtifactStore` — bucket creation and a real presigned-URL round trip — against
whatever bucket `S3_BUCKET` names (default `poseidon-artifacts`):

```bash
cd backend
S3_ENDPOINT_URL=http://localhost:9000 S3_ACCESS_KEY=poseidon S3_SECRET_KEY=poseidon123 \
  python -m pytest -m minio -v
```

Unlike `pg`/`pdf`, MinIO has no native-library requirement — `boto3` is pure
Python — so `minio`-marked tests can run on a bare host once MinIO is up and
the three variables above are exported. They skip after a 2-second health
probe (`/minio/health/live`) when MinIO is not reachable.

**`pdf`** — needs WeasyPrint's native libraries (Pango, Cairo, GDK-Pixbuf),
which `pip install weasyprint` does **not** provide; without them `import
weasyprint` raises `OSError` (not `ImportError`) even though the pip install
itself succeeded. Because that failure only shows up at import time, the
`pdf` marker's tests probe `weasyprint` explicitly at module load
(`_HAS_WEASYPRINT`, a `try`/`except` around the import) rather than relying on
collection to fail cleanly. `infra/backend.Dockerfile.dev` installs the
native libraries before `pip install`, so the container is the one place
`pdf`-marked tests actually run:

```bash
docker compose -f infra/docker-compose.yml up -d --build backend
docker compose -f infra/docker-compose.yml exec backend python -m pytest -m "pdf or minio" -v
```

Running `pdf` together with `minio` in one command is deliberate:
`existing_customer_brief`'s `build_brief_pdf` tool is marked both — it renders
a PDF with WeasyPrint *and* uploads it to MinIO in the same test — and the
running `backend` container already has `DATABASE_URL`, `S3_ENDPOINT_URL`,
`S3_ACCESS_KEY`, and `S3_SECRET_KEY` set (`docker-compose.yml`), so no extra
environment setup is needed there.

## Artifacts (MinIO)

Generated files (today: PDF briefs from `build_brief_pdf`) are objects in the
`minio` service's `S3_BUCKET` (default `poseidon-artifacts`), never files on
the backend's own filesystem. The bucket does **not** pre-exist — nothing
creates it as part of compose bring-up — `ArtifactStore.ensure_bucket()`
creates it lazily and idempotently the first time any code path needs it (a
`minio`-marked test today; eventually a real skill run).

Browse what has been uploaded at the MinIO console, `http://localhost:9001`
(same credentials as `docker-compose.yml`'s `MINIO_ROOT_USER`/
`MINIO_ROOT_PASSWORD`: `poseidon` / `poseidon123`). Every artifact a skill
hands back to the frontend is a presigned GET URL (one-hour expiry) pointing
at an object there — the backend uploads once and then gets out of the way;
the browser fetches the file directly from MinIO.

Caveat for whichever phase wires up the real frontend download: a presigned
URL is signed for whatever `S3_ENDPOINT_URL` the backend used to generate it
— inside the `backend` container that is `http://minio:9000`, a hostname
that only resolves on the compose network. That is fine for tests and
server-to-server fetches running inside the same network, but a browser
outside it cannot resolve `minio` at all; serving artifact links to a real
browser will need either a publicly reachable MinIO endpoint or a proxy/
rewrite step, not just today's `ArtifactStore.put_pdf`.

## Dev skill runner

`POST /api/dev/skills/{skill_id}/run` is a local-only HTTP surface for
calling any registered skill directly — no chat pipeline, no LLM, no router
(those arrive Phases 5/6). `create_app` mounts this route, and builds
`app.state.skill_registry` via `SkillRegistry.discover()`, only when
`DEPLOY_MODE=local`; the route does not exist at all in `spcs`/`ec2` (a
request there gets a genuine HTTP 404, since nothing is mounted). On
start-up the backend logs what discovery found, e.g. `skills registered:
data_qa.metric_query` — a quick way to confirm the registry built cleanly
without making a request at all.

The request body is the skill's raw `Args` — the same JSON shape the future
router will pass a skill — and the response is its `SkillResult`, serialized:

```json
{"ok": true, "parts": [...], "proof": [...], "artifacts": [...], "error": null}
```

**Every response is HTTP 200.** An unknown `skill_id`, invalid arguments, or
a skill-internal bug are never HTTP-level errors — they come back as
`ok: false` with an `error` object (`{"type", "title", "detail", "status"}`,
RFC-7807 shape) carrying the real status (404/422/500 respectively). This
mirrors the contract the real router loop will run on: a failure is content
the loop reads, never an exception it has to survive.
(`data_backend=snowflake` — not yet implemented, Phase 15 — answers the same
way, with a structured 501.)

Example: the certified top-5 GP customers at the Port of Singapore, April
2026 (the same seeded data the `pg`-marked tests and `demo_query` check):

```bash
curl -s -X POST localhost:8000/api/dev/skills/data_qa.metric_query/run \
  -H "Content-Type: application/json" \
  -d '{"metrics":["GP"],"period":{"start":"2026-04-01","end":"2026-05-01"},"filters":[{"column":"LOC_NM","values":["Singapore"]}],"group_by":"CUST_NM"}'
```

```json
{"ok":true,"parts":[{"kind":"table","payload":{"columns":["Customer","Gross Profit"],"rows":[["Meridian Marine",70119],["Meridian Maritime",47958],["Meridian Shipmanagement",38087],["Blue Anchor Marine",30411],["Northstar Lines",25325]]}}],"proof":["Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V","Backend: synthetic","Period: 2026-04-01..2026-05-01","Filters: LOC_NM IN (Singapore)","Group by: CUST_NM (top 5)","Rows: 5"],"artifacts":[],"error":null}
```

The same call with `"column":"PORT_NM"` — not a certified dimension of
`MARINE_SALES_PLANNING_V` (`ontology.yml`'s `negative_constraints` names it
as the wrong column; `LOC_NM` is right) — surfaces the query builder's own
certified message verbatim, as a structured 422 instead of a table:

```bash
curl -s -X POST localhost:8000/api/dev/skills/data_qa.metric_query/run \
  -H "Content-Type: application/json" \
  -d '{"metrics":["GP"],"period":{"start":"2026-04-01","end":"2026-05-01"},"filters":[{"column":"PORT_NM","values":["Singapore"]}],"group_by":"CUST_NM"}'
```

```json
{"ok":false,"parts":[],"proof":[],"artifacts":[],"error":{"type":"about:blank","title":"invalid query","detail":"'PORT_NM' is not a dimension of MARINE_SALES_PLANNING_V","status":422}}
```

## Live chat (`CHAT_MODE`)

`docker-compose.yml`'s `backend` service sets `CHAT_MODE: live` (Phase 6 Task
5's cutover) — `poseidon.api.app.create_app` mounts `poseidon.api.live_chat`'s
router instead of `mock_chat.py`'s scripted demo. `CHAT_MODE` defaults to
`mock` (`poseidon/core/config.py`) for every environment that does not set
it explicitly, so a bare `python -m uvicorn poseidon.api.app:create_app
--factory` (no compose) still serves the mock unless you export
`CHAT_MODE=live` yourself.

In live mode the chat runs the real pipeline end to end: `parse_turn` (the
deterministic pipeline) -> `DevDeterministicRouter` (the stub LLM provider —
`LLM_MODE=stub` is compose's own default; no AWS credentials needed) ->
`data_qa.metric_query` against the seeded `synthetic` schema -> the run-log
writer (`turn_run`/`llm_calls`/`tool_calls`, migration 0003). The same six
routes `poseidon/api/live_chat.py` exposes (`POST`/`GET /api/conversations`,
`GET /api/conversations/{cid}/messages`, `POST /api/conversations/{cid}/
messages`, `POST`/`GET /api/messages/{mid}/feedback`, `GET /api/skills`)
back the frontend's own `bootstrap()` flow unchanged — `localhost:5173`
create-a-conversation, send-a-message, reopen-the-transcript all work
against live mode exactly as they did against mock.

**Rebuilding the backend image.** `live_chat.py`'s dependencies
(`rapidfuzz`, `jinja2` — the customer/port resolver and the prompt
templates) are declared in `backend/pyproject.toml` but only actually
`pip install`ed inside the image at build time; a long-running container
built from an older image will not have them. Rebuild before (re)starting
whenever the image predates those dependencies landing:

```bash
docker compose -f infra/docker-compose.yml build backend
docker compose -f infra/docker-compose.yml up -d
```

The seeder's own "skips when the tables already hold rows" behavior (see
"Synthetic data" above) means a rebuild + restart never reseeds — confirm
the checksum is unchanged with the same generator command the seeder itself
uses:

```bash
docker exec infra-backend-1 python -m poseidon.scripts.generate_synthetic
# sales_rows=24000 gl_rows=16200 checksum=886dd91a... (must match every time)
```

**Frontend serving stale code.** After a long-lived local session, the
`frontend` container can keep serving code from before your latest change
even though `../frontend` is bind-mounted — if the UI does not reflect a
change you know landed, restart just that service:

```bash
docker compose -f infra/docker-compose.yml restart frontend
```

### The 4-turn gate script

The Phase 6 Phase Gate's own scripted conversation, driven either at
`localhost:5173` or with `curl` against `localhost:8000` directly. Two of
the four turns carry an explicit year rather than the doc-08 shorthand
("and for May 2026?", not a bare "and for May?"; see
`backend/tests/test_chat_e2e_scripted.py`'s own module docstring for why: a
bare relative month resolves against the REAL current date in live mode,
unlike an offline test that gets to pin one, so a bare phrase would
silently ask a different question depending on which day you run this).

1. `Top GP customers for Port of Singapore in April 2026` -> a `table` part
   (top-5 customers by GP) + a collapsible `proof` block + a certified-answer
   line; `turn_run.status = 'ok'`.
2. `and for May 2026?` -> carries: same topic, period replaces to May 2026.
   The port does NOT re-filter this turn (a bare follow-up has no "port
   of"/"at" cue of its own — a documented, parked pipeline.py asymmetry, not
   a bug) — the table becomes a single Gross Profit total across every port.
3. `same for Port of Rotterdam` -> port replaces to Rotterdam, period still
   carries from turn 2. (Note: `for Rotterdam` alone is read as a CUSTOMER
   cue, not a port one — say "port of" or "at" to name a port.)
4. `gp for Meridiann in April 2026` (capitalized "Meridiann" — the customer
   resolver's cue-run detection requires it) -> lands in the fuzzy
   candidate band against the seeded pool: a `chips` part naming the
   Meridian family (`Meridian Tankers` / `Meridian Lines` / `Meridian
   Shipping`) + a "did you mean...?" text part; `turn_run.status =
   'clarify'`, no skill dispatch, no `llm_calls` rows. Clicking a chip sends
   a `"for <name>"` send text as a new plain message, not the bare label
   (final-review wave item 2) — that "for " cue is what lets the
   deterministic parser resolve the customer on this follow-up (a bare name
   alone carries no cue word, the same asymmetry turn 3's own "for
   Rotterdam" note describes); the turn resolves to the chosen customer and
   completes normally.

Turn 1 alone, direct against the API with curl (a quick sanity check
without opening the UI):

```bash
curl -N -s -X POST localhost:8000/api/conversations/8f14e45f-ceea-467e-add8-3f8d1a5e2b1c/messages \
  -H "Content-Type: application/json" \
  -d '{"text":"Top GP customers for Port of Singapore in April 2026","client_turn_key":"3f9d9d0a-6e1e-4b8b-9c9b-2f6f6c5a9d11"}'
```

Expect an SSE stream: `accepted`, two `tool` frames (start then done), two
`part` frames (table then proof), one `token` frame with the certified
answer, then `done`. Both the conversation id in the URL and
`client_turn_key` in the body must be real UUIDs, like the shipped
frontend's own `crypto.randomUUID()`: the streaming route itself accepts
any string as the conversation id (it never validates or looks it up), but
`turn_run.conversation_id`/`client_turn_key` are both UUID columns, so a
hand-typed non-UUID value for either one (e.g. a bare `demo-1` in the URL,
or `client_turn_key: "turn-1"`) fails the run-log insert silently
(`RunLogWriter` never raises) — leaving no `turn_run` row and nothing for
the idempotent-retry check to match against, even though the chat turn
itself still streams and answers normally (live-verified: curling this
exact example with a bare `demo-1` conversation id gets the identical SSE
stream above, but logs `runlog start_turn failed: ... invalid input syntax
for type uuid: "demo-1"` on the server and writes no row at all).

### Inspecting the run-log rows the script wrote

```sql
SELECT status, input_tokens, output_tokens FROM turn_run ORDER BY created_at;
```

The four scripted turns show up with statuses `ok`, `ok`, `ok`, `clarify` (in
whatever order you ran them — this is a SHARED table other tests and other
runs of this same script also write into, so filter by `question` or a
recent `created_at` window if the table already holds history). Every row's
`input_tokens`/`output_tokens` reads `0`: `DevDeterministicRouter` is a
stub, not a model, so the run log honestly records zero usage for a stub
turn rather than a placeholder pretending to be real (`dev_router.py`'s own
module docstring). A dispatching turn (1-3) has exactly 2 `llm_calls` rows
(the tool-use call, then the end-turn call) and exactly 1 `tool_calls` row;
the clarify turn (4) has zero of both — the clarify short-circuit in
`orchestrator.py` fires before any provider call.

```bash
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \
  cd backend && python -m pytest tests/test_chat_e2e_scripted.py -m pg -v
```

runs this exact script (with the two disclosed text substitutions above)
against a REAL `create_app(chat_mode="live")` app and re-derives every one
of these assertions from the live database.

## Native fallback

Use this path when Docker isn't available. It runs the backend and frontend
directly on the host, in two terminals.

**Terminal 1 — backend:**

```bash
cd backend
pip install -e ".[dev]"
```

Copy `backend/.env.example` to `backend/.env`. Point `DATABASE_URL` at any
Postgres instance you have reachable, or leave it as-is — the server will
still start, and `/health/ready` will simply report the database as
unreachable until one is available (see note below).

```bash
python -m uvicorn poseidon.api.app:create_app --factory --reload --port 8000
```

**Terminal 2 — frontend:**

```bash
cd frontend
npm install
npm run dev
```

## Running the test suites

Backend, from `backend/`:

```bash
python -m pytest
```

Frontend, from `frontend/`:

```bash
npm test -- --run
```

## Verifying crash-on-missing configuration

`Settings` refuses to build when a required value is missing, so a
half-configured server never accepts traffic. To see it, comment out
`S3_BUCKET` from the `backend` service's `environment` block in
`infra/docker-compose.yml` and bring the stack up: `alembic upgrade head`
still succeeds, then uvicorn's app factory raises a `ValidationError` for the
missing field and the container exits instead of serving.

Use `S3_BUCKET`, not `DATABASE_URL`, for this check. The backend command runs
`alembic upgrade head` first, and `migrations/env.py` raises its own
`RuntimeError: DATABASE_URL is required to run migrations` before the app is
ever imported — so dropping `DATABASE_URL` proves the migration guard, not the
Settings validator.

The container ignores any `backend/.env` you have on the host, even though
`../backend` is bind-mounted at `/app`: compose sets `POSEIDON_ENV_FILE=""`,
which tells `Settings` to read no dotenv at all. Without that, a stray host
`.env` would silently supply the value you just removed and the check would
appear to fail. Restore the line when you're done.

## Note on `/health/ready` and the database

`/health/ready` reports `db: down` until Postgres is actually reachable at
`DATABASE_URL`. In native mode without a local database running, this is
expected — the endpoint degrades gracefully rather than failing the process.
Under Docker Compose, the `backend` service waits on the `db` healthcheck
before starting, so `db` should report `up` once the stack finishes coming up.
