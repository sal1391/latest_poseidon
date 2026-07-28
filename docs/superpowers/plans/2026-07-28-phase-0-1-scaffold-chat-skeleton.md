# Poseidon Phase 0 + 1: Scaffold and Chat Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Poseidon monorepo (FastAPI backend + React/Vite frontend + docker-compose) and deliver a runnable ChatGPT-style chat skeleton streaming a scripted mock turn with visible tool steps, mode chips, a skills-picker stub, and mocked feedback capture.

**Architecture:** Monorepo per `docs/architecture/00-overview.md` §6 — `backend/` (FastAPI, python package `poseidon`), `frontend/` (Vite + React 18 + TS), `infra/` (compose). The frontend renders typed message parts through a renderer registry fed by an SSE reducer over a Zustand store (`docs/architecture/01-frontend.md`). The backend serves an in-memory mock chat API emitting the real SSE event protocol (doc 01 §5) so Phase 6 swaps the mock for the real pipeline without touching the frontend.

**Tech Stack:** Python 3.11+, FastAPI, pydantic-settings v2, SQLAlchemy 2, Alembic, pytest, httpx · React 18.3, TypeScript, Vite, Zustand v5, TanStack Query v5, react-markdown, MSW v2, Vitest, React Testing Library · Postgres 16 + pgvector, MinIO (compose).

## Global Constraints

- Python package is `poseidon` inside `backend/` (so doc paths `backend/api/...` map to `backend/poseidon/api/...`). Note this mapping in `backend/README.md`.
- Env contract names come verbatim from `docs/architecture/07-infrastructure.md` §6. Settings crash on missing/malformed values.
- SSE event names/payloads come verbatim from `docs/architecture/01-frontend.md` §5; message-part kinds from §4. Do not invent new names.
- **SSE envelope (resumability contract):** every event's `data` JSON carries `turn_id`, `message_id`, and `event_seq` (monotonic per turn, starting at 1) alongside the event's own fields, and every frame carries an `id: <event_seq>` line before `event:`. The send POST body includes `client_turn_key` (client-generated UUID). The reducer addresses messages by `message_id` (never "the last message") and skips any event whose `event_seq` is not greater than the message's last applied seq.
- Theme: the **Slate** preset is the default `tokens.css` (values in Task 4). Components consume semantic tokens only — never raw hex in component styles.
- Phase 1 renderer registry implements kinds `text`, `chips`, `tool_event`, `error` + a safe fallback. Other kinds come in later phases.
- Tests are committed, never gitignored. Every task ends green: `python -m pytest` (backend) / `npm test -- --run` (frontend).
- All work on branch `phase-0-1-chat-skeleton`. Conventional commits; end every commit body with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Windows dev machine: use `python -m` invocations (`python -m pytest`, `python -m uvicorn`), forward slashes in configs, no `make`. Do not assume Docker is running; compose files must be correct, but per-task validation uses native runs and unit tests. The compose bring-up is the human validation gate at the end.
- Do not touch the existing Streamlit files at repo root (`app.py`, `agents/`, `config.py`, …). They coexist until cut-over.
- DRY. YAGNI: build only what this plan states; no speculative options, no extra deps.

## File Map (created in this plan)

```
backend/
  pyproject.toml            # project + deps + ruff + pytest config
  README.md                 # package-name mapping note, run commands
  alembic.ini
  migrations/env.py         # Alembic wired to Settings
  migrations/versions/0001_baseline.py
  poseidon/__init__.py
  poseidon/core/__init__.py
  poseidon/core/config.py   # Settings (env contract)
  poseidon/api/__init__.py
  poseidon/api/app.py       # create_app() factory
  poseidon/api/health.py    # /health/live, /health/ready
  poseidon/api/mock_chat.py # Phase-1 mock conversations/SSE/feedback
  tests/__init__.py
  tests/test_config.py
  tests/test_health.py
  tests/test_migrations.py
  tests/test_mock_chat.py
frontend/
  package.json / vite.config.ts / tsconfig*.json / index.html  (Vite scaffold)
  src/theme/tokens.css      # Slate preset
  src/theme/base.css        # layout styles consuming tokens
  src/api/types.ts          # DTOs: parts, messages, SSE events
  src/api/client.ts         # REST calls
  src/api/sse.ts            # streaming reader
  src/state/chatStore.ts    # Zustand store + applyEvent reducer
  src/ui/message-parts/registry.tsx + TextPart/ChipsPart/ToolEventPart/ErrorPart/FallbackPart
  src/ui/primitives/Feedback.tsx
  src/features/chat/ChatScreen.tsx
  src/features/chat/Composer.tsx
  src/features/chat/SkillsPicker.tsx
  src/features/conversations/Sidebar.tsx
  src/app/App.tsx / src/app/main.tsx
  src/mocks/handlers.ts     # MSW mirrors of the REST API
  src/test/setup.ts
  src/**/*.test.ts(x)       # per-task tests
infra/
  docker-compose.yml
  backend.Dockerfile.dev
  frontend.Dockerfile.dev
  runbooks/local.md
.gitignore                  # extended, never excludes tests
.env.example                # repo root
```

---

### Task 1: Backend project + Settings (env contract)

**Files:**
- Create: `backend/pyproject.toml`, `backend/README.md`, `backend/poseidon/__init__.py`, `backend/poseidon/core/__init__.py`, `backend/poseidon/core/config.py`, `backend/tests/__init__.py`
- Create: `backend/.env.example` (the legacy root `.env.example` belongs to the Streamlit app — never modify it)
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `poseidon.core.config.Settings` (pydantic-settings BaseSettings) and `get_settings()` (`functools.lru_cache`). Fields (exact names/types):
  `deploy_mode: Literal["local","spcs","ec2"] = "local"` · `database_url: str` (required) · `s3_endpoint_url: str | None = None` · `s3_bucket: str` (required) · `data_backend: Literal["synthetic","snowflake"] = "synthetic"` · `identity_mode: Literal["disabled","auth0","spcs_ingress"] = "disabled"` · `auth0_domain: str | None = None` · `auth0_audience: str | None = None` · `auth0_client_id: str | None = None` · `llm_profile: Literal["bedrock","cortex"] = "bedrock"` · `llm_mode: Literal["stub","live"] = "stub"` · `tool_transport_perplexity: Literal["direct","mcp"] = "direct"` · `perplexity_api_key: str | None = None` · `memory_max_chars: int = 8000` · `memory_keep_versions: int = 20`
- Produces: model validator — if `identity_mode == "auth0"`, the three `auth0_*` fields are required (raise `ValueError` naming the missing fields).

- [ ] **Step 1: Write `backend/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "poseidon-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "pydantic-settings>=2.4",
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "psycopg[binary]>=3.2",
]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27", "ruff>=0.6"]

[tool.setuptools.packages.find]
include = ["poseidon*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 2: Create package inits and `backend/README.md`**

`poseidon/__init__.py` and subpackage `__init__.py` files are empty. README states: layout mapping ("doc paths `backend/api/` = `backend/poseidon/api/`"), install (`pip install -e ".[dev]"` from `backend/`), run (`python -m uvicorn poseidon.api.app:app --reload --port 8000`), test (`python -m pytest`).

- [ ] **Step 3: Write the failing tests**

`backend/tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError


REQUIRED = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}


def make_settings(monkeypatch, **overrides):
    from poseidon.core.config import Settings

    # Hermetic: clear EVERY Settings env var (derived, so the list can't drift)
    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    env = {**REQUIRED, **overrides}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_defaults_are_local_and_stub(monkeypatch):
    s = make_settings(monkeypatch)
    assert s.deploy_mode == "local"
    assert s.identity_mode == "disabled"
    assert s.llm_mode == "stub"
    assert s.data_backend == "synthetic"
    assert s.memory_max_chars == 8000


def test_missing_database_url_crashes(monkeypatch):
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, DATABASE_URL="")  # empty string is malformed


def test_malformed_enum_crashes(monkeypatch):
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, DEPLOY_MODE="cloud")


def test_auth0_mode_requires_auth0_fields(monkeypatch):
    with pytest.raises(ValidationError) as err:
        make_settings(monkeypatch, IDENTITY_MODE="auth0")
    assert "auth0_domain" in str(err.value)


def test_auth0_mode_valid_when_fields_present(monkeypatch):
    s = make_settings(
        monkeypatch, IDENTITY_MODE="auth0", AUTH0_DOMAIN="dev.us.auth0.com",
        AUTH0_AUDIENCE="https://poseidon/api", AUTH0_CLIENT_ID="abc123",
    )
    assert s.identity_mode == "auth0"
```

- [ ] **Step 4: Run tests to verify they fail**

Run from `backend/`: `pip install -e ".[dev]"` then `python -m pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: poseidon.core.config`.

- [ ] **Step 5: Implement `backend/poseidon/core/config.py`**

```python
from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment contract — docs/architecture/07-infrastructure.md §6.

    Startup crashes on any missing or malformed value: no half-configured
    server ever accepts traffic.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    deploy_mode: Literal["local", "spcs", "ec2"] = "local"
    database_url: str
    s3_endpoint_url: str | None = None
    s3_bucket: str
    data_backend: Literal["synthetic", "snowflake"] = "synthetic"
    identity_mode: Literal["disabled", "auth0", "spcs_ingress"] = "disabled"
    auth0_domain: str | None = None
    auth0_audience: str | None = None
    auth0_client_id: str | None = None
    llm_profile: Literal["bedrock", "cortex"] = "bedrock"
    llm_mode: Literal["stub", "live"] = "stub"
    tool_transport_perplexity: Literal["direct", "mcp"] = "direct"
    perplexity_api_key: str | None = None
    memory_max_chars: int = 8000
    memory_keep_versions: int = 20

    @field_validator("database_url", "s3_bucket")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @model_validator(mode="after")
    def auth0_fields_required_in_auth0_mode(self) -> "Settings":
        if self.identity_mode == "auth0":
            missing = [
                name for name in ("auth0_domain", "auth0_audience", "auth0_client_id")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"identity_mode=auth0 requires: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v` → all PASS.

- [ ] **Step 7: Write `backend/.env.example`** (do NOT touch the legacy root `.env.example`)

```bash
# --- Poseidon environment contract (docs/architecture/07-infrastructure.md §6) ---
DEPLOY_MODE=local
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon
S3_ENDPOINT_URL=http://localhost:9000
S3_BUCKET=poseidon-artifacts
DATA_BACKEND=synthetic
IDENTITY_MODE=disabled
# AUTH0_DOMAIN=your-tenant.us.auth0.com   (required when IDENTITY_MODE=auth0)
# AUTH0_AUDIENCE=https://poseidon/api
# AUTH0_CLIENT_ID=...
LLM_PROFILE=bedrock
LLM_MODE=stub
TOOL_TRANSPORT_PERPLEXITY=direct
# PERPLEXITY_API_KEY=...
MEMORY_MAX_CHARS=8000
MEMORY_KEEP_VERSIONS=20
```

- [ ] **Step 8: Commit**

```bash
git add backend/
git commit -m "feat(backend): project scaffold with validated settings env contract"
```

---

### Task 2: FastAPI app factory + health endpoints

**Files:**
- Create: `backend/poseidon/api/__init__.py`, `backend/poseidon/api/app.py`, `backend/poseidon/api/health.py`
- Test: `backend/tests/test_health.py`

**Interfaces:**
- Consumes: `poseidon.core.config.Settings`, `get_settings`.
- Produces: `create_app(settings: Settings | None = None) -> FastAPI` in `poseidon.api.app` — factory ONLY, no module-level `app` (uvicorn runs `poseidon.api.app:create_app --factory`). Health routes: `GET /health/live` → `200 {"status": "ok"}`; `GET /health/ready` → `200 {"status": "ok", "components": {"db": "up"}}` or `503 {"status": "degraded", "components": {"db": "down"}}` (DB probe = SQLAlchemy `SELECT 1`, 2s timeout).

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_health.py`:

```python
import httpx
import pytest

from poseidon.core.config import Settings


def build_app(database_url: str):
    from poseidon.api.app import create_app

    settings = Settings(
        _env_file=None, database_url=database_url, s3_bucket="poseidon-artifacts"
    )
    return create_app(settings)


@pytest.mark.anyio
async def test_live_is_ok_without_db():
    app = build_app("postgresql+psycopg://nobody:nope@127.0.0.1:1/void")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_ready_degraded_when_db_unreachable():
    app = build_app("postgresql+psycopg://nobody:nope@127.0.0.1:1/void")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/health/ready")
    assert r.status_code == 503
    assert r.json()["components"]["db"] == "down"


@pytest.fixture
def anyio_backend():
    return "asyncio"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_health.py -v` → FAIL (`poseidon.api.app` missing). Add `anyio` if needed: it ships with httpx/starlette deps.

- [ ] **Step 3: Implement**

`backend/poseidon/api/health.py`:

```python
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request):
    settings = request.app.state.settings
    try:
        engine = create_engine(
            settings.database_url, connect_args={"connect_timeout": 2}
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db = "up"
    except Exception:
        db = "down"
    finally:
        try:
            engine.dispose()
        except UnboundLocalError:
            pass
    status = "ok" if db == "up" else "degraded"
    return JSONResponse(
        status_code=200 if db == "up" else 503,
        content={"status": status, "components": {"db": db}},
    )
```

`backend/poseidon/api/app.py` — factory only, NO module-level `app = create_app()` (a module-level call would resolve settings at import time and crash test imports; uvicorn uses factory mode instead):

```python
from fastapi import FastAPI

from poseidon.api import health
from poseidon.core.config import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """App factory. Run with: python -m uvicorn poseidon.api.app:create_app --factory"""
    app = FastAPI(title="Poseidon API", version="0.1.0")
    app.state.settings = settings or get_settings()
    app.include_router(health.router)
    return app
```

Update `backend/README.md` run command to the `--factory` form (Task 1 wrote the non-factory form; correct it here).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_health.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(backend): app factory and health endpoints with db probe"
```

---

### Task 3: Alembic baseline

**Files:**
- Create: `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/script.py.mako` (default), `backend/migrations/versions/0001_baseline.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Produces: `alembic upgrade head` works with `DATABASE_URL` from the environment (overriding alembic.ini). Baseline revision `0001` is empty (schema arrives in later phases).

- [ ] **Step 1: Write the failing test**

`backend/tests/test_migrations.py`:

```python
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_upgrade_head_on_sqlite(tmp_path, monkeypatch):
    db = tmp_path / "mig.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert db.exists()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_migrations.py -v` → FAIL (no alembic.ini).

- [ ] **Step 3: Initialize Alembic and wire env**

Run from `backend/`: `python -m alembic init migrations`. Then replace `migrations/env.py` content with:

```python
import os

from alembic import context
from sqlalchemy import create_engine

config = context.config


def get_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is required to run migrations")
    return url


def run_migrations_offline() -> None:
    context.configure(url=get_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

In `alembic.ini`, set `script_location = migrations` and leave `sqlalchemy.url =` empty (env-driven).

Create `migrations/versions/0001_baseline.py`:

```python
"""baseline

Revision ID: 0001
Revises:
Create Date: 2026-07-28
"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/ -v` → all PASS (config, health, migrations).

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "feat(backend): alembic baseline wired to DATABASE_URL"
```

---

### Task 4: Frontend Vite shell + Slate tokens

**Files:**
- Create: `frontend/` via Vite scaffold (React + TS), then `src/theme/tokens.css`, `src/theme/base.css`, `src/app/App.tsx`, `src/app/main.tsx`, `src/test/setup.ts`
- Test: `frontend/src/app/App.test.tsx`

**Interfaces:**
- Produces: `npm run dev` (port 5173, `/api` proxied to `http://localhost:8000`), `npm test -- --run` (Vitest + RTL + jsdom). CSS custom properties per doc 01 §6: `--surface --surface-raised --ink --ink-muted --accent --accent-ink --positive --negative --border --radius-s/m/l --shadow-1/2 --font-display/body/data --motion-fast/slow`.

- [ ] **Step 1: Scaffold**

From repo root: `npm create vite@latest frontend -- --template react-ts`. Then in `frontend/`:
`npm install` · `npm install zustand @tanstack/react-query react-markdown` · `npm install -D vitest @testing-library/react @testing-library/user-event @testing-library/jest-dom jsdom msw`.
Pin React 18 (doc 01 D9): `npm install react@^18.3.1 react-dom@^18.3.1 && npm install -D @types/react@^18 @types/react-dom@^18`. Remove scaffold boilerplate (`App.css`, logo assets, default `index.css` content).

- [ ] **Step 2: Configure Vite + Vitest**

`frontend/vite.config.ts`:

```ts
/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.VITE_PROXY_TARGET ?? "http://localhost:8000" } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
```

`src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

Add to `package.json` scripts: `"test": "vitest"`.

- [ ] **Step 3: Write the failing test**

`src/app/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import App from "./App";

test("renders the app shell with brand and composer placeholder", () => {
  render(<App />);
  expect(screen.getByText("Poseidon")).toBeInTheDocument();
  expect(screen.getByPlaceholderText(/message poseidon/i)).toBeInTheDocument();
});
```

Run: `npm test -- --run` → FAIL (no `./App`).

- [ ] **Step 4: Implement tokens and shell**

`src/theme/tokens.css` (the Slate preset — chosen direction 01):

```css
:root {
  /* color */
  --surface: #ffffff;
  --surface-raised: #f7f7f8;
  --ink: #202123;
  --ink-muted: #8e9196;
  --accent: #202123;
  --accent-ink: #ffffff;
  --positive: #10a37f;
  --negative: #d92d20;
  --border: #e5e5e5;
  /* shape + elevation */
  --radius-s: 8px;
  --radius-m: 12px;
  --radius-l: 24px;
  --shadow-1: 0 1px 2px rgba(0, 0, 0, 0.05);
  --shadow-2: 0 4px 16px rgba(0, 0, 0, 0.08);
  /* type */
  --font-display: "Segoe UI", system-ui, -apple-system, sans-serif;
  --font-body: "Segoe UI", system-ui, -apple-system, sans-serif;
  --font-data: "Segoe UI", system-ui, sans-serif;
  /* motion */
  --motion-fast: 120ms;
  --motion-slow: 240ms;
}
```

`src/theme/base.css` — app shell layout, all values via tokens (sidebar `--surface-raised`, thread max-width 720px centered, composer pill `--radius-l` with `--border`, focus-visible outlines, `font-variant-numeric: tabular-nums` utility class `.data`). Write real CSS for: `.app-shell` (grid: 260px sidebar + 1fr), `.sidebar`, `.thread`, `.composer`, `.chip`, `.tool-step`, `.msg-user`, `.msg-assistant`, `.feedback-row` — Slate look: user message in `--surface-raised` right-aligned bubble, assistant plain on `--surface`, tool steps muted `--ink-muted` 13px rows.

`src/app/App.tsx` (Phase-1 Task-10 replaces the body with ChatScreen; here it is the static shell):

```tsx
import "../theme/tokens.css";
import "../theme/base.css";

export default function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">Poseidon</div>
        <button className="new-chat">+ New chat</button>
      </aside>
      <main className="chat-column">
        <div className="thread" />
        <div className="composer">
          <input placeholder="Message Poseidon…" aria-label="Message Poseidon" />
        </div>
      </main>
    </div>
  );
}
```

`src/app/main.tsx` renders `<App />` into `#root` (StrictMode).

- [ ] **Step 5: Run tests, then commit**

`npm test -- --run` → PASS. `npm run build` → succeeds.

```bash
git add frontend/
git commit -m "feat(frontend): vite shell with slate theme tokens and test harness"
```

---

### Task 5: docker-compose + dev Dockerfiles + local runbook

**Files:**
- Create: `infra/docker-compose.yml`, `infra/backend.Dockerfile.dev`, `infra/frontend.Dockerfile.dev`, `infra/runbooks/local.md`
- Modify: `.gitignore` (append: `node_modules/`, `frontend/dist/`, `__pycache__/`, `*.pyc`, `.venv/`, `.env`, `.pytest_cache/`, `.ruff_cache/`, `.maestro/` — NEVER add `tests` or `docs`)

**Interfaces:**
- Consumes: backend factory entrypoint (`poseidon.api.app:create_app --factory`), frontend dev server, env names from Task 1.
- Produces: `docker compose -f infra/docker-compose.yml up` brings up db (pgvector), minio, backend (runs `alembic upgrade head` then uvicorn), frontend (5173 → proxy to backend).

- [ ] **Step 1: Write `infra/docker-compose.yml`**

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: poseidon
      POSTGRES_PASSWORD: poseidon
      POSTGRES_DB: poseidon
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U poseidon -d poseidon"]
      interval: 5s
      timeout: 3s
      retries: 10

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: poseidon
      MINIO_ROOT_PASSWORD: poseidon123
    ports: ["9000:9000", "9001:9001"]
    volumes: [miniodata:/data]

  backend:
    build: { context: ../backend, dockerfile: ../infra/backend.Dockerfile.dev }
    command: >
      sh -c "python -m alembic upgrade head &&
             python -m uvicorn poseidon.api.app:create_app --factory --host 0.0.0.0 --port 8000 --reload"
    environment:
      DEPLOY_MODE: local
      DATABASE_URL: postgresql+psycopg://poseidon:poseidon@db:5432/poseidon
      S3_ENDPOINT_URL: http://minio:9000
      S3_BUCKET: poseidon-artifacts
      DATA_BACKEND: synthetic
      IDENTITY_MODE: disabled
      LLM_PROFILE: bedrock
      LLM_MODE: stub
      TOOL_TRANSPORT_PERPLEXITY: direct
    volumes: ["../backend:/app"]
    ports: ["8000:8000"]
    depends_on:
      db: { condition: service_healthy }
      minio: { condition: service_started }

  frontend:
    build: { context: ../frontend, dockerfile: ../infra/frontend.Dockerfile.dev }
    command: sh -c "npm install && npm run dev -- --host"
    environment:
      VITE_PROXY_TARGET: http://backend:8000
    volumes: ["../frontend:/app", "/app/node_modules"]
    ports: ["5173:5173"]
    depends_on: [backend]

volumes:
  pgdata:
  miniodata:
```

- [ ] **Step 2: Dockerfiles**

`infra/backend.Dockerfile.dev`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY poseidon ./poseidon
RUN pip install --no-cache-dir -e ".[dev]"
COPY . .
```

`infra/frontend.Dockerfile.dev`:

```dockerfile
FROM node:20-alpine
WORKDIR /app
```

- [ ] **Step 3: Write `infra/runbooks/local.md`**

Contents (write in full): prerequisites (Docker Desktop, or native Python 3.11+/Node 20+); compose bring-up (`docker compose -f infra/docker-compose.yml up --build`, app at `http://localhost:5173`, API at `:8000`, MinIO console at `:9001`); native fallback path (terminal 1: `cd backend`, `pip install -e ".[dev]"`, copy `backend/.env.example` to `backend/.env` with `DATABASE_URL` pointed at any reachable Postgres or left as-is for a degraded `/health/ready`, then `python -m uvicorn poseidon.api.app:create_app --factory --reload --port 8000`; terminal 2: `cd frontend && npm install && npm run dev`); how to run both test suites; note that `/health/ready` reports `db: down` until Postgres is reachable — expected in native mode without a database.

- [ ] **Step 4: Validate what can be validated without Docker**

Run: `docker compose -f infra/docker-compose.yml config` (syntax check) if Docker CLI exists; otherwise proceed — the compose bring-up is the human gate. Backend + frontend suites still green: `python -m pytest` / `npm test -- --run`.

- [ ] **Step 5: Commit**

```bash
git add infra/ .gitignore
git commit -m "feat(infra): docker-compose dev topology with pgvector, minio, backend, frontend"
```

---

### Task 6: Backend mock chat API (conversations, SSE turn, feedback)

**Files:**
- Create: `backend/poseidon/api/mock_chat.py`
- Modify: `backend/poseidon/api/app.py` (include router)
- Test: `backend/tests/test_mock_chat.py`

**Interfaces:**
- Consumes: `create_app` factory.
- Produces (Phase-6 swaps internals, routes stay):
  - `POST /api/conversations` → `201 {"conversation": {"id": str, "title": "New chat"}, "opener": Message}` where `Message = {"id": str, "role": "assistant"|"user", "parts": [Part, ...]}`. Opener parts: `{"kind":"text","payload":{"markdown":"Ask about your data, or pick a flow:"}}` and `{"kind":"chips","payload":{"options":[{"id":"existing_customer","label":"Existing customer"},{"id":"new_prospect","label":"New customer prospect"}]}}`.
  - `GET /api/conversations` → `200 {"conversations": [{"id","title"}, ...]}` (newest first).
  - `GET /api/conversations/{cid}/messages` → `200 {"messages": [Message, ...]}`; 404 if unknown cid (FastAPI default detail JSON in the mock; RFC-7807 arrives with the real API).
  - `POST /api/conversations/{cid}/messages` body `{"text": str, "client_turn_key": str|null}` → `text/event-stream`. **Every event's `data` carries the envelope** `{turn_id, message_id, event_seq}` (event_seq monotonic from 1) plus the event's own fields. Order: `accepted` `{…env, turn_index}` (1-based position of this turn in the conversation) · `tool` `{…env, tool_seq:1, tool:"top_customers", server:"internal", status:"start", label:"Running skill · top_customers…"}` · `tool` (same `tool_seq`, `status:"done"`, label `"top_customers · done · 0.3s"`) · `tool` `{…env, tool_seq:2, tool:"web_research", server:"perplexity", status:"start", label:"Calling Perplexity — marine news search…"}` · `tool` (`tool_seq` 2 `done`, `"Perplexity — 3 sources"`) · 6+ `token` events `{…env, text}` streaming a markdown answer · `done` `{…env, usage:{input_tokens:0, output_tokens:0}}` (no run_id — `turn_id` in the envelope is the run identity). (`tool_seq` is the step number, matching the future `tool_calls` row; `event_seq` is the stream position — both exist, doc 01 §5.) If the user text contains `!error`, after `accepted` emit only `error` `{…env, code:"mock_failure", message:"Mock failure requested", hint:"Remove !error from your message"}`. Both user message and final assistant message are appended to the in-memory store (assistant parts: the two completed tool_event parts + one text part).
  - `POST /api/messages/{mid}/feedback` body `{"verdict":"up"|"down","comment": str|null}` → `204`; idempotent upsert keyed by message id (mock has a single dev user); unknown mid → 404. `GET /api/messages/{mid}/feedback` → `200 {"verdict","comment"}` or `404` (test convenience).
- SSE wire format per event: `id: <event_seq>\n` + `event: <name>\n` + `data: <json>\n\n`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_mock_chat.py`:

```python
import json

import httpx
import pytest

from poseidon.core.config import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app():
    from poseidon.api.app import create_app

    return create_app(Settings(
        _env_file=None,
        database_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/void",
        s3_bucket="poseidon-artifacts",
    ))


async def read_sse(client, cid, text):
    events = []
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"text": text}
    ) as response:
        assert response.status_code == 200
        name = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: "):])))
    return events


@pytest.mark.anyio
async def test_create_conversation_returns_opener_with_chips(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations")
        assert r.status_code == 201
        opener = r.json()["opener"]
        kinds = [p["kind"] for p in opener["parts"]]
        assert kinds == ["text", "chips"]
        ids = [o["id"] for o in opener["parts"][1]["payload"]["options"]]
        assert ids == ["existing_customer", "new_prospect"]


@pytest.mark.anyio
async def test_mock_turn_streams_tools_tokens_done(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        events = await read_sse(client, cid, "top GP customers in Singapore")
        names = [n for n, _ in events]
        assert names[0] == "accepted"
        assert names[-1] == "done"
        tool_events = [d for n, d in events if n == "tool"]
        assert {(t["tool_seq"], t["status"]) for t in tool_events} == {
            (1, "start"), (1, "done"), (2, "start"), (2, "done")}
        assert any(n == "token" for n, _ in events)
        # envelope on every event: turn_id/message_id/event_seq, strictly increasing
        payloads = [d for _, d in events]
        assert all({"turn_id", "message_id", "event_seq"} <= set(d) for d in payloads)
        seqs = [d["event_seq"] for d in payloads]
        assert seqs[0] == 1 and seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert len({d["turn_id"] for d in payloads}) == 1
        # transcript persisted: user + assistant with tool_event + text parts
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]
        assert msgs[-1]["role"] == "assistant"
        assert [p["kind"] for p in msgs[-1]["parts"]].count("tool_event") == 2


@pytest.mark.anyio
async def test_error_trigger_emits_error_event(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        events = await read_sse(client, cid, "please !error now")
        assert [n for n, _ in events] == ["accepted", "error"]
        for _, d in events:  # envelope present on both frames, incl. the error path
            assert {"turn_id", "message_id", "event_seq"} <= set(d)
        err = events[1][1]
        assert err["code"] == "mock_failure" and "message" in err and "hint" in err


@pytest.mark.anyio
async def test_list_conversations_newest_first(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        c1 = (await client.post("/api/conversations")).json()["conversation"]["id"]
        c2 = (await client.post("/api/conversations")).json()["conversation"]["id"]
        listing = (await client.get("/api/conversations")).json()["conversations"]
        assert [c["id"] for c in listing[:2]] == [c2, c1]


@pytest.mark.anyio
async def test_unknown_ids_return_404(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        assert (await client.get("/api/conversations/nope/messages")).status_code == 404
        r = await client.post("/api/conversations/nope/messages", json={"text": "hi"})
        assert r.status_code == 404
        assert (await client.post("/api/messages/nope/feedback",
                                  json={"verdict": "up"})).status_code == 404
        assert (await client.get("/api/messages/nope/feedback")).status_code == 404


@pytest.mark.anyio
async def test_feedback_upsert_roundtrip(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        await read_sse(client, cid, "hello")
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]
        mid = msgs[-1]["id"]
        r = await client.post(f"/api/messages/{mid}/feedback",
                              json={"verdict": "down", "comment": "wrong port"})
        assert r.status_code == 204
        r = await client.post(f"/api/messages/{mid}/feedback", json={"verdict": "up"})
        assert r.status_code == 204
        r = await client.get(f"/api/messages/{mid}/feedback")
        assert r.json() == {"verdict": "up", "comment": None}
```

- [ ] **Step 2: Run to verify failure**

`python -m pytest tests/test_mock_chat.py -v` → FAIL (router missing).

- [ ] **Step 3: Implement `backend/poseidon/api/mock_chat.py`**

```python
"""Phase-1 mock chat API. The routes and SSE protocol are the real contract
(docs/architecture/01-frontend.md §5); Phase 6 replaces the internals."""

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["chat-mock"])

_conversations: dict[str, dict[str, Any]] = {}
_messages: dict[str, list[dict[str, Any]]] = {}
_feedback: dict[str, dict[str, Any]] = {}

_ANSWER_CHUNKS = [
    "Three customers drove most of April's gross profit in Singapore:\n\n",
    "1. **Northstar Lines** — $412.4K\n",
    "2. **Blue Anchor Marine** — $268.0K\n",
    "3. **Crestline Freight** — $203.7K\n\n",
    "Northstar Lines also expanded its Singapore–Jakarta rotation recently — ",
    "ask me for the news summary if useful.",
]


class SendBody(BaseModel):
    text: str
    client_turn_key: str | None = None


class FeedbackBody(BaseModel):
    verdict: str
    comment: str | None = None


def _sse(name: str, payload: dict) -> str:
    return f"id: {payload['event_seq']}\nevent: {name}\ndata: {json.dumps(payload)}\n\n"


def _message(role: str, parts: list[dict]) -> dict:
    return {"id": str(uuid.uuid4()), "role": role, "parts": parts}


@router.post("/conversations", status_code=201)
def create_conversation() -> dict:
    cid = str(uuid.uuid4())
    conversation = {"id": cid, "title": "New chat"}
    opener = _message("assistant", [
        {"kind": "text", "payload": {"markdown": "Ask about your data, or pick a flow:"}},
        {"kind": "chips", "payload": {"options": [
            {"id": "existing_customer", "label": "Existing customer"},
            {"id": "new_prospect", "label": "New customer prospect"},
        ]}},
    ])
    _conversations[cid] = conversation
    _messages[cid] = [opener]
    return {"conversation": conversation, "opener": opener}


@router.get("/conversations")
def list_conversations() -> dict:
    return {"conversations": list(reversed(list(_conversations.values())))}


@router.get("/conversations/{cid}/messages")
def get_messages(cid: str) -> dict:
    if cid not in _messages:
        raise HTTPException(404, detail="unknown conversation")
    return {"messages": _messages[cid]}


@router.post("/conversations/{cid}/messages")
def send_message(cid: str, body: SendBody) -> StreamingResponse:
    if cid not in _messages:
        raise HTTPException(404, detail="unknown conversation")
    _messages[cid].append(_message("user", [
        {"kind": "text", "payload": {"markdown": body.text}}]))
    message_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())

    async def stream():
        event_seq = 0

        def ev(name: str, **fields) -> str:
            nonlocal event_seq
            event_seq += 1
            return _sse(name, {"turn_id": turn_id, "message_id": message_id,
                               "event_seq": event_seq, **fields})

        turn_index = sum(1 for m in _messages[cid] if m["role"] == "user")
        yield ev("accepted", turn_index=turn_index)
        if "!error" in body.text:
            yield ev("error", code="mock_failure",
                     message="Mock failure requested",
                     hint="Remove !error from your message")
            return
        tool_parts = []
        steps = [
            (1, "top_customers", "internal",
             "Running skill · top_customers (GP · Singapore · Apr 2026)",
             "top_customers · done · 0.3s"),
            (2, "web_research", "perplexity",
             "Calling Perplexity — marine news search…",
             "Perplexity — 3 sources"),
        ]
        for tool_seq, tool, server, start_label, done_label in steps:
            yield ev("tool", tool_seq=tool_seq, tool=tool, server=server,
                     status="start", label=start_label)
            await asyncio.sleep(0.35)
            done = {"tool_seq": tool_seq, "tool": tool, "server": server,
                    "status": "done", "label": done_label}
            tool_parts.append({"kind": "tool_event", "payload": done})
            yield ev("tool", **done)
        text = ""
        for chunk in _ANSWER_CHUNKS:
            text += chunk
            yield ev("token", text=chunk)
            await asyncio.sleep(0.12)
        _messages[cid].append({"id": message_id, "role": "assistant",
                               "parts": [*tool_parts,
                                         {"kind": "text",
                                          "payload": {"markdown": text}}]})
        yield ev("done", usage={"input_tokens": 0, "output_tokens": 0})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.post("/messages/{mid}/feedback", status_code=204)
def upsert_feedback(mid: str, body: FeedbackBody) -> None:
    if body.verdict not in ("up", "down"):
        raise HTTPException(422, detail="verdict must be up or down")
    if not _known_message(mid):
        raise HTTPException(404, detail="unknown message")
    _feedback[mid] = {"verdict": body.verdict, "comment": body.comment}


@router.get("/messages/{mid}/feedback")
def get_feedback(mid: str) -> dict:
    if mid not in _feedback:
        raise HTTPException(404, detail="no feedback")
    return _feedback[mid]


def _known_message(mid: str) -> bool:
    return any(m["id"] == mid for msgs in _messages.values() for m in msgs)
```

Register in `app.py`: `from poseidon.api import health, mock_chat` … `app.include_router(mock_chat.router)`.

- [ ] **Step 4: Run tests → PASS, then commit**

`python -m pytest -v` → all PASS.

```bash
git add backend/
git commit -m "feat(backend): mock chat api with real sse protocol, opener chips, feedback upsert"
```

---

### Task 7: Frontend API types + SSE reader

**Files:**
- Create: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/api/sse.ts`
- Test: `frontend/src/api/sse.test.ts`

**Interfaces:**
- Produces `types.ts` (exact):

```ts
export type PartKind = "text" | "chips" | "tool_event" | "error";
export interface TextPayload { markdown: string }
export interface ChipOption { id: string; label: string }
export interface ChipsPayload { options: ChipOption[] }
export interface ToolEventPayload {
  tool_seq: number; tool: string; server: string;
  status: "start" | "done" | "error"; label: string;
}
export interface ErrorPayload { code: string; message: string; hint?: string }
export interface MessagePart { kind: string; payload: unknown }
export interface Message {
  id: string;
  role: "user" | "assistant";
  parts: MessagePart[];
  lastSeq?: number; // highest applied event_seq (client-side replay guard)
}
export interface Conversation { id: string; title: string }
export interface SseEnvelope { turn_id: string; message_id: string; event_seq: number }
export type SseEvent =
  | { name: "accepted"; data: SseEnvelope & { turn_index: number } }
  | { name: "tool"; data: SseEnvelope & ToolEventPayload }
  | { name: "token"; data: SseEnvelope & { text: string } }
  | { name: "part"; data: SseEnvelope & MessagePart }
  | { name: "phase"; data: SseEnvelope & { phase: string; status: "start" | "done" } }
  | { name: "done"; data: SseEnvelope & { usage: unknown } }
  | { name: "error"; data: SseEnvelope & ErrorPayload };
```

- Produces `client.ts`: `createConversation(): Promise<{conversation: Conversation; opener: Message}>` · `listConversations(): Promise<Conversation[]>` · `getMessages(cid: string): Promise<Message[]>` · `postFeedback(mid: string, verdict: "up"|"down", comment?: string): Promise<void>` — thin `fetch` wrappers over `/api/...`, throwing on non-2xx.
- Produces `sse.ts`: `streamTurn(cid: string, text: string, onEvent: (e: SseEvent) => void, signal?: AbortSignal): Promise<void>` — POSTs JSON, reads `response.body` with `TextDecoder`, buffers on `\n\n`, parses `event:`/`data:` lines, calls `onEvent` per event, resolves when the stream closes. Exported helper `parseSseChunk(buffer: string): { events: SseEvent[]; rest: string }` for unit testing.

- [ ] **Step 1: Write the failing test**

`src/api/sse.test.ts`:

```ts
import { parseSseChunk } from "./sse";

test("parses enveloped events (ignoring id: lines) and keeps the incomplete tail", () => {
  const raw =
    'id: 1\nevent: accepted\ndata: {"turn_id":"t1","message_id":"m1","event_seq":1,"turn_index":1}\n\n' +
    'id: 2\nevent: token\ndata: {"turn_id":"t1","message_id":"m1","event_seq":2,"text":"Hello"}\n\n' +
    "id: 3\nevent: token\ndata: {\"te";
  const { events, rest } = parseSseChunk(raw);
  expect(events).toEqual([
    { name: "accepted", data: { turn_id: "t1", message_id: "m1", event_seq: 1, turn_index: 1 } },
    { name: "token", data: { turn_id: "t1", message_id: "m1", event_seq: 2, text: "Hello" } },
  ]);
  expect(rest).toBe('id: 3\nevent: token\ndata: {"te');
});

test("returns no events for a bare fragment", () => {
  const { events, rest } = parseSseChunk("event: to");
  expect(events).toEqual([]);
  expect(rest).toBe("event: to");
});
```

Run `npm test -- --run` → FAIL.

- [ ] **Step 2: Implement `sse.ts`**

```ts
import type { SseEvent } from "./types";

export function parseSseChunk(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = [];
  const blocks = buffer.split("\n\n");
  const rest = blocks.pop() ?? "";
  for (const block of blocks) {
    let name = "";
    let data = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event: ")) name = line.slice(7);
      else if (line.startsWith("data: ")) data = line.slice(6);
    }
    if (name && data) events.push({ name, data: JSON.parse(data) } as SseEvent);
  }
  return { events, rest };
}

export async function streamTurn(
  cid: string,
  text: string,
  onEvent: (e: SseEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`/api/conversations/${cid}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, client_turn_key: crypto.randomUUID() }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error(`turn failed: ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseChunk(buffer);
    buffer = parsed.rest;
    parsed.events.forEach(onEvent);
  }
}
```

Implement `client.ts` with the four wrappers (each: `fetch`, `if (!r.ok) throw new Error(...)`, return parsed JSON field per the Task-6 response shapes; `postFeedback` sends `{verdict, comment: comment ?? null}` and returns void on 204).

- [ ] **Step 3: Run tests → PASS. Commit**

```bash
git add frontend/src/api
git commit -m "feat(frontend): typed api client and sse stream parser"
```

---

### Task 8: chatStore + SSE reducer

**Files:**
- Create: `frontend/src/state/chatStore.ts`
- Test: `frontend/src/state/chatStore.test.ts`

**Interfaces:**
- Consumes: `types.ts`, `client.ts`, `sse.ts`.
- Produces Zustand store `useChatStore` with state `{ conversations: Conversation[]; activeId: string | null; messages: Record<string, Message[]>; streamingByConv: Record<string, boolean>; feedback: Record<string, { verdict: "up" | "down"; comment?: string }> }` and actions:
  - `bootstrap(): Promise<void>` — list conversations; if empty, create one (stores opener).
  - `newConversation(): Promise<string>`; `openConversation(cid): Promise<void>` (loads messages via `getMessages`).
  - `sendMessage(cid, text): Promise<void>` — appends user message locally, sets streaming, calls `streamTurn` wiring events to `applyEvent`, clears streaming on completion or thrown error (thrown → append synthetic error part).
  - `applyEvent(cid, e: SseEvent): void` — pure-ish reducer, exported also as standalone `applyEventTo(messages: Message[], e: SseEvent): Message[]` for tests. Rules (envelope-addressed, never "the last message"): every event targets the message with `id === e.data.message_id`. `accepted` appends `{id, role:"assistant", parts:[], lastSeq: event_seq}` (idempotent — skip if id exists). All other events: find the message by id (if absent, CREATE it with empty parts — replay-safe), then skip the event unless `e.data.event_seq > (msg.lastSeq ?? 0)` (duplicate-delivery guard), then set `lastSeq = event_seq` and apply: `tool` upserts a `tool_event` part matched by the tool-step `payload.tool_seq` (replace in place, else push), envelope fields stripped from the stored payload; `token` appends text to that message's trailing `text` part, else pushes a new one; `part` pushes the part (envelope stripped); `error` pushes `{kind:"error", payload}` (envelope stripped); `done` only advances `lastSeq` (conversation list refresh comes later).
  - `submitFeedback(mid, verdict, comment?)` — optimistic set + `postFeedback`; on failure, revert and rethrow.

- [ ] **Step 1: Write the failing tests**

`src/state/chatStore.test.ts`:

```ts
import type { Message, SseEvent } from "../api/types";
import { applyEventTo } from "./chatStore";

const env = (event_seq: number) => ({ turn_id: "t1", message_id: "a1", event_seq });

const seq: SseEvent[] = [
  { name: "accepted", data: { ...env(1), turn_index: 1 } },
  { name: "tool", data: { ...env(2), tool_seq: 1, tool: "top_customers", server: "internal", status: "start", label: "Running…" } },
  { name: "tool", data: { ...env(3), tool_seq: 1, tool: "top_customers", server: "internal", status: "done", label: "done · 0.3s" } },
  { name: "token", data: { ...env(4), text: "Hello " } },
  { name: "token", data: { ...env(5), text: "world" } },
];

function run(events: SseEvent[], initial: Message[] = []): Message[] {
  return events.reduce((msgs, e) => applyEventTo(msgs, e), initial);
}

test("builds an assistant message with in-place tool updates and merged tokens", () => {
  const msgs = run(seq);
  expect(msgs).toHaveLength(1);
  const parts = msgs[0].parts;
  expect(parts).toHaveLength(2);
  expect(parts[0].kind).toBe("tool_event");
  expect((parts[0].payload as { status: string }).status).toBe("done");
  expect((parts[0].payload as { turn_id?: string }).turn_id).toBeUndefined();
  expect((parts[1].payload as { markdown: string }).markdown).toBe("Hello world");
});

test("accepted is idempotent and duplicate deliveries are skipped by event_seq", () => {
  const msgs = run([seq[0], seq[0], seq[3], seq[3]]);
  expect(msgs).toHaveLength(1);
  expect((msgs[0].parts[0].payload as { markdown: string }).markdown).toBe("Hello ");
});

test("events for an unseen message_id create the message (replay-safe)", () => {
  const msgs = run([{ name: "token", data: { ...env(4), text: "late" } }]);
  expect(msgs).toHaveLength(1);
  expect(msgs[0].id).toBe("a1");
});

test("error event appends an error part", () => {
  const msgs = run([seq[0], { name: "error", data: { ...env(2), code: "x", message: "boom" } }]);
  expect(msgs[0].parts.at(-1)?.kind).toBe("error");
});
```

Run → FAIL.

- [ ] **Step 2: Implement `chatStore.ts`**

```ts
import { create } from "zustand";
import type { Conversation, Message, SseEvent } from "../api/types";
import * as api from "./../api/client";
import { streamTurn } from "../api/sse";

export function applyEventTo(messages: Message[], e: SseEvent): Message[] {
  const { message_id, event_seq } = e.data;
  const msgs = messages.map((m) => ({ ...m, parts: [...m.parts] }));
  let msg = msgs.find((m) => m.id === message_id);
  if (e.name === "accepted") {
    if (msg) return messages;
    msgs.push({ id: message_id, role: "assistant", parts: [], lastSeq: event_seq });
    return msgs;
  }
  if (!msg) {
    // Replay-safe: an event for an unseen message creates it.
    msg = { id: message_id, role: "assistant", parts: [], lastSeq: 0 };
    msgs.push(msg);
  }
  if (event_seq <= (msg.lastSeq ?? 0)) return messages; // duplicate delivery
  msg.lastSeq = event_seq;
  switch (e.name) {
    case "tool": {
      const { turn_id: _t, message_id: _m, event_seq: _s, ...payload } = e.data;
      const i = msg.parts.findIndex(
        (p) => p.kind === "tool_event" &&
          (p.payload as { tool_seq: number }).tool_seq === payload.tool_seq,
      );
      const part = { kind: "tool_event", payload };
      if (i >= 0) msg.parts[i] = part;
      else msg.parts.push(part);
      return msgs;
    }
    case "token": {
      const tail = msg.parts[msg.parts.length - 1];
      if (tail?.kind === "text") {
        msg.parts[msg.parts.length - 1] = {
          kind: "text",
          payload: { markdown: (tail.payload as { markdown: string }).markdown + e.data.text },
        };
      } else {
        msg.parts.push({ kind: "text", payload: { markdown: e.data.text } });
      }
      return msgs;
    }
    case "part": {
      const { turn_id: _t, message_id: _m, event_seq: _s, ...part } = e.data;
      msg.parts.push(part as MessagePart);
      return msgs;
    }
    case "error": {
      const { turn_id: _t, message_id: _m, event_seq: _s, ...payload } = e.data;
      msg.parts.push({ kind: "error", payload });
      return msgs;
    }
    default:
      return msgs; // done/phase: lastSeq already advanced
  }
}
```

Then the store: state + actions exactly as the interface block specifies, each action a thin orchestration over `api.*`/`streamTurn`/`applyEventTo` with `set`/`get`. `sendMessage` appends `{id: crypto.randomUUID(), role: "user", parts: [{kind: "text", payload: {markdown: text}}]}` before streaming.

- [ ] **Step 3: Run tests → PASS. Commit**

```bash
git add frontend/src/state
git commit -m "feat(frontend): chat store with idempotent sse reducer"
```

---

### Task 9: Message-part renderer registry

**Files:**
- Create: `frontend/src/ui/message-parts/registry.tsx`, `TextPart.tsx`, `ChipsPart.tsx`, `ToolEventPart.tsx`, `ErrorPart.tsx`, `FallbackPart.tsx` (same directory)
- Test: `frontend/src/ui/message-parts/registry.test.tsx`

**Interfaces:**
- Consumes: `types.ts` payload types.
- Produces: `PartRenderer({ part, onChipSelect }: { part: MessagePart; onChipSelect?: (id: string, label: string) => void })`. Registry maps `text → TextPart` (react-markdown), `chips → ChipsPart` (buttons; calls `onChipSelect(option.id, option.label)`; all disabled when `payload.disabled === true`), `tool_event → ToolEventPart` (status glyph: `start` → pulsing dot + label, `done` → "✓ " + label in `--ink-muted`, `error` → "✕ " + label in `--negative`), `error → ErrorPart` (bordered card: message + hint), unknown → `FallbackPart` (`<details>` with pretty JSON).

- [ ] **Step 1: Write the failing tests**

```tsx
import { render, screen } from "@testing-library/react";
import { PartRenderer } from "./registry";

test("renders markdown text", () => {
  render(<PartRenderer part={{ kind: "text", payload: { markdown: "**bold** move" } }} />);
  expect(screen.getByText("bold")).toBeInTheDocument();
});

test("renders tool event with done glyph", () => {
  render(<PartRenderer part={{ kind: "tool_event", payload: {
    tool_seq: 1, tool: "t", server: "internal", status: "done", label: "top_customers · done" } }} />);
  expect(screen.getByText(/top_customers · done/)).toBeInTheDocument();
  expect(screen.getByText(/✓/)).toBeInTheDocument();
});

test("unknown kind falls back safely", () => {
  render(<PartRenderer part={{ kind: "metric_grid", payload: { anything: 1 } }} />);
  expect(screen.getByText(/unsupported part: metric_grid/i)).toBeInTheDocument();
});
```

Run → FAIL.

- [ ] **Step 2: Implement the five components + registry**

`registry.tsx`:

```tsx
import type { ComponentType } from "react";
import type { MessagePart } from "../../api/types";
import { TextPart } from "./TextPart";
import { ChipsPart } from "./ChipsPart";
import { ToolEventPart } from "./ToolEventPart";
import { ErrorPart } from "./ErrorPart";
import { FallbackPart } from "./FallbackPart";

export interface PartProps {
  part: MessagePart;
  onChipSelect?: (id: string, label: string) => void;
}

const registry: Record<string, ComponentType<PartProps>> = {
  text: TextPart,
  chips: ChipsPart,
  tool_event: ToolEventPart,
  error: ErrorPart,
};

export function PartRenderer(props: PartProps) {
  const Renderer = registry[props.part.kind] ?? FallbackPart;
  return <Renderer {...props} />;
}
```

Each part component is ≤25 lines, class names from `base.css` (`.tool-step`, `.chip`, `.error-card`), payload cast to its Task-7 type. `FallbackPart` renders `<details className="fallback-part"><summary>Unsupported part: {kind}</summary><pre>{JSON.stringify(payload, null, 2)}</pre></details>`. `TextPart` uses `react-markdown` with default settings.

- [ ] **Step 3: Run tests → PASS. Commit**

```bash
git add frontend/src/ui
git commit -m "feat(frontend): message-part renderer registry with safe fallback"
```

---

### Task 10: ChatScreen assembly + feedback UI + MSW + interaction tests

**Files:**
- Create: `frontend/src/features/chat/ChatScreen.tsx`, `Composer.tsx`, `SkillsPicker.tsx`, `frontend/src/features/conversations/Sidebar.tsx`, `frontend/src/ui/primitives/Feedback.tsx`, `frontend/src/mocks/handlers.ts`
- Modify: `frontend/src/app/App.tsx` (render `<ChatScreen />`)
- Test: `frontend/src/features/chat/ChatScreen.test.tsx`

**Interfaces:**
- Consumes: `useChatStore`, `PartRenderer`, api client.
- Produces the Phase-1 screen (doc 01 §3, Slate look):
  - `Sidebar`: brand, "+ New chat" → `newConversation()`, conversation list (active highlighted) → `openConversation`.
  - Thread: messages of the active conversation; user messages right-aligned bubbles (`.msg-user`), assistant plain (`.msg-assistant`); each part via `PartRenderer`; chip select inserts the template `"Run the {label} flow for "` into the composer input (entry stub — real dispatch is Phase 8).
  - `Composer`: input + send button (disabled while `streamingByConv[activeId]`); Enter submits; `SkillsPicker` button "Skills" opens a popover listing exactly: `Metric query — "Top GP customers for Port of Singapore in April 2026"`, `Web research — "Any recent news on Northstar Lines?"`, `Existing customer brief — "Run the existing-customer brief for …"`, `New prospect brief — "Research prospect …"`; clicking one inserts the quoted example into the input and closes.
  - `Feedback` (on every assistant message footer): thumb-up button → `submitFeedback(mid, "up")`, fills icon; thumb-down → reveals inline "What went wrong?" textarea + "Send" (submits verdict+comment) and "Skip" (verdict only). Aria-labels: "Good response", "Bad response".
  - `src/mocks/handlers.ts`: MSW v2 handlers for `POST /api/conversations`, `GET /api/conversations`, `GET /api/conversations/:cid/messages`, `POST /api/messages/:mid/feedback` returning Task-6-shaped JSON (SSE POST route is NOT MSW-mocked; component tests stub `streamTurn`).

- [ ] **Step 1: Write the failing tests**

`ChatScreen.test.tsx` — setupServer from `../..//mocks/handlers`; `vi.mock("../../api/sse")` so `streamTurn` immediately emits the Task-8 scripted sequence then resolves:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupServer } from "msw/node";
import { vi, beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { handlers } from "../../mocks/handlers";
import type { SseEvent } from "../../api/types";

vi.mock("../../api/sse", () => ({
  streamTurn: vi.fn(async (_cid: string, _text: string, onEvent: (e: SseEvent) => void) => {
    const events: SseEvent[] = [
      { name: "accepted", data: { turn_id: "t1", message_id: "a1", event_seq: 1, turn_index: 1 } },
      { name: "tool", data: { turn_id: "t1", message_id: "a1", event_seq: 2, tool_seq: 1, tool: "top_customers", server: "internal", status: "start", label: "Running skill · top_customers…" } },
      { name: "tool", data: { turn_id: "t1", message_id: "a1", event_seq: 3, tool_seq: 1, tool: "top_customers", server: "internal", status: "done", label: "top_customers · done · 0.3s" } },
      { name: "token", data: { turn_id: "t1", message_id: "a1", event_seq: 4, text: "Three customers drove April." } },
      { name: "done", data: { turn_id: "t1", message_id: "a1", event_seq: 5, usage: {} } },
    ];
    events.forEach(onEvent);
  }),
}));

import ChatScreen from "./ChatScreen";

const server = setupServer(...handlers);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("send → streamed answer with visible tool step", async () => {
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "top gp customers singapore{Enter}");
  await waitFor(() =>
    expect(screen.getByText(/top_customers · done · 0.3s/)).toBeInTheDocument());
  expect(screen.getByText(/Three customers drove April./)).toBeInTheDocument();
});

test("thumbs down opens the comment prompt and submits", async () => {
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => screen.getAllByLabelText("Bad response"));
  await userEvent.click(screen.getAllByLabelText("Bad response").at(-1)!);
  const box = await screen.findByPlaceholderText(/what went wrong/i);
  await userEvent.type(box, "numbers look off");
  await userEvent.click(screen.getByRole("button", { name: /send feedback/i }));
  await waitFor(() =>
    expect(screen.queryByPlaceholderText(/what went wrong/i)).not.toBeInTheDocument());
});

test("skills picker inserts an example prompt", async () => {
  render(<ChatScreen />);
  await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));
  await userEvent.click(screen.getByText(/Metric query/));
  expect(screen.getByPlaceholderText(/message poseidon/i)).toHaveValue(
    "Top GP customers for Port of Singapore in April 2026");
});
```

Run → FAIL.

- [ ] **Step 2: Implement the components**

Keep each file focused: `ChatScreen` composes and owns nothing but wiring; `Composer` owns input state (controlled), exposes `insertText(text)` via prop callback from SkillsPicker/chips (lift the input value into ChatScreen state so chips + skills can set it). Feedback state lives in the store (`feedback[mid]`). MSW `handlers.ts` uses `http.post/http.get` + `HttpResponse.json(...)` (msw v2 imports from `msw`), returning the exact Task-6 shapes with fixed ids (`"c1"`, opener message `"m0"`).

- [ ] **Step 3: Run the full suites**

`npm test -- --run` → all frontend tests PASS. `cd backend && python -m pytest` → all backend tests PASS. `npm run build` → clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "feat(frontend): chat screen with streaming thread, skills picker, feedback capture"
```

---

## Phase Gates (human validation — run after Task 10)

**Phase 0 gate (doc 08):** `docker compose -f infra/docker-compose.yml up --build` → frontend at `localhost:5173`, API docs at `localhost:8000/docs`, `GET /health/ready` → `{"status":"ok","components":{"db":"up"}}`; killing `DATABASE_URL` from the backend service env makes it exit at startup (crash-on-missing). Both test suites green.

**Phase 1 gate (doc 08):** in the browser — new chat shows opener with the two mode chips; sending a question streams two visible tool steps ("Running skill · top_customers…" → "✓", "Calling Perplexity…" → "✓") followed by token-streamed markdown; sending a message containing `!error` renders the error card and re-enables the composer; thumbs-down opens "What went wrong?" and stores the comment (verify `GET /api/messages/{mid}/feedback`); Skills button inserts example prompts; renderer fallback covered by tests.

## Self-Review Notes

- Spec coverage: Task 1–5 ↔ doc 08 Phase 0 deliverables (layout, health, Vite shell, compose, Alembic, lint config, two test harnesses, `.env.example`, crash-on-missing). Task 6–10 ↔ Phase 1 deliverables (sidebar, composer, stream, chips entry, skills stub, registry kinds, SSE mock turn with tool events, feedback UI, MSW). Lint = ruff config (backend) + Vite's eslint scaffold (frontend) — kept minimal deliberately.
- Type consistency: SSE names (`accepted/tool/token/part/phase/done/error`) and part kinds (`text/chips/tool_event/error`) match doc 01 §4–5 across Tasks 6, 7, 8, 9, 10. `Settings` fields match doc 07 §6 names lowercased.
- Known deferrals (deliberate, per doc 08): no auth (P9), no Postgres-backed history (P10), no run log (P11), no real router (P5/6), no Playwright (P6), `phase`/`part` events accepted by the reducer but unused by the mock.
