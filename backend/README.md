# poseidon-backend

FastAPI backend for Poseidon.

## Layout mapping

Architecture docs (`docs/architecture/`) reference backend paths relative to
`backend/` without the package prefix. Map them onto this repo by inserting
the `poseidon` package name, e.g. doc path `backend/api/` = `backend/poseidon/api/`.

## Install

From `backend/`:

```bash
pip install -e ".[dev]"
```

## Run

```bash
python -m uvicorn poseidon.api.app:app --reload --port 8000
```

## Test

```bash
python -m pytest
```
