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

### Integration tests

`backend/tests/test_synthetic_client_pg.py` is marked `pg` and is the only test
module that touches a database. It recomputes every expectation in pure Python
from the same generator the seeder used, then compares against what the
certified SQL returns:

```bash
cd backend
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon \
  python -m pytest -m pg -v
```

Those tests read the database, they never seed it — migrate and seed first. With
no reachable database (or no `DATABASE_URL` at all) the module skips with an
actionable reason after a 2-second connect timeout, so the plain `python -m
pytest` suite stays green offline.

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
