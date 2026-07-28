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
object storage), `backend` (runs `alembic upgrade head` then serves the API via
uvicorn with `--reload`), and `frontend` (Vite dev server).

Once the stack is up:

- App: `http://localhost:5173`
- API: `http://localhost:8000`
- MinIO console: `http://localhost:9001`

Stop the stack with `Ctrl+C`, or `docker compose -f infra/docker-compose.yml down`
from another terminal. `down` alone leaves the named volumes (`pgdata`,
`miniodata`) intact, so database contents and uploaded objects survive a
restart; add `-v` to `down` to also drop those volumes and start clean.

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
