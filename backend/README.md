# poseidon-backend

FastAPI backend for Poseidon.

## Layout mapping

Architecture docs (`docs/architecture/`) reference backend paths relative to
`backend/` without the package prefix. Map them onto this repo by inserting
the `poseidon` package name, e.g. doc path `backend/api/` = `backend/poseidon/api/`.

## Configuration scope

`Settings` (`poseidon/core/config.py`) implements the Phase-0 subset of the
environment contract in `docs/architecture/07-infrastructure.md` §6 — the
variables Phase 0/1 actually reads. The rest arrive with the phases that own
them: `SNOWFLAKE_*` with the Snowflake data backend (Phases 3/5),
`LLM_PROVIDER_<ROLE>`/`LLM_MODEL_<ROLE>` with per-role model routing (Phase 13),
and `MEMORY_IDLE_MINUTES`/`MEMORY_MAX_ATTEMPTS`, `RETENTION_*`, `BACKUP_*` with
the deploy phases. `extra="ignore"` means setting one early is harmless; it just
has no effect until its phase lands.

`Settings` reads `backend/.env` by default. Set `POSEIDON_ENV_FILE` to point at
a different dotenv, or to `""` to read none — Docker Compose does the latter so
a host `.env` cannot shadow the container's environment.

## Install

From `backend/`:

```bash
pip install -e ".[dev]"
```

## Run

```bash
python -m uvicorn poseidon.api.app:create_app --factory --reload --port 8000
```

## Test

```bash
python -m pytest
```
