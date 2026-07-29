"""Live integration tests for :class:`~poseidon.core.artifacts.ArtifactStore`
against a real MinIO.

Mirrors ``test_synthetic_client_pg.py``'s shape exactly, one level down the
stack: every test here is ``@pytest.mark.minio`` and this whole module is
skipped, at import time, when MinIO is not configured or not reachable within
a couple of seconds — no ``DATABASE_URL``-style split between offline and
live tests is needed because unlike ``fetch_metrics``/``fetch_top_ports``
there is no pure-offline shape to test here: ``ArtifactStore`` has nothing to
assert about *except* what a real bucket does.

```bash
docker compose -f infra/docker-compose.yml up -d minio
S3_ENDPOINT_URL=http://localhost:9000 S3_ACCESS_KEY=poseidon S3_SECRET_KEY=poseidon123 \\
  python -m pytest -m minio -v
```

The bucket this module writes to (``S3_BUCKET``, default ``poseidon-artifacts``)
starts out **not existing** — that is what proves ``ensure_bucket`` is doing
real work rather than assuming a bucket some other setup step already made.
Objects these tests upload are deliberately left in place afterward (never
deleted) so they remain visible as evidence in the MinIO console
(``http://localhost:9001``).
"""

import os

import httpx
import pytest

from poseidon.core.artifacts import ArtifactStore
from poseidon.core.config import Settings

pytestmark = pytest.mark.minio

CONNECT_TIMEOUT_SECONDS = 2

# Skip reasons are ASCII-only on purpose: pytest prints them straight to a
# console that may well be Windows cp1252, where an em dash becomes "?".
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d minio`"

_ENDPOINT = os.environ.get("S3_ENDPOINT_URL", "")
if not _ENDPOINT:
    pytest.skip(
        f"S3_ENDPOINT_URL is not set - minio integration tests need MinIO: {_UP_HINT}",
        allow_module_level=True,
    )

try:
    _health = httpx.get(
        f"{_ENDPOINT.rstrip('/')}/minio/health/live", timeout=CONNECT_TIMEOUT_SECONDS
    )
    _health.raise_for_status()
except Exception as exc:  # noqa: BLE001 - any connect/health failure means "not available"
    pytest.skip(
        f"MinIO at {_ENDPOINT} is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}",
        allow_module_level=True,
    )

# Built once, at import time, exactly like test_synthetic_client_pg.py's
# module-level CLIENT — only reached when the guard above already proved
# MinIO is up, never on a bare offline run.
_SETTINGS = Settings(
    _env_file=None,
    database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
    s3_endpoint_url=_ENDPOINT,
    s3_bucket=os.environ.get("S3_BUCKET", "poseidon-artifacts"),
    s3_access_key=os.environ.get("S3_ACCESS_KEY"),
    s3_secret_key=os.environ.get("S3_SECRET_KEY"),
)
STORE = ArtifactStore(_SETTINGS)


def test_ensure_bucket_is_idempotent():
    """Calling twice must not raise — the second call finds the bucket
    ``head_bucket`` already reports and returns without attempting another
    ``create_bucket``."""
    STORE.ensure_bucket()
    STORE.ensure_bucket()


def test_put_pdf_roundtrip_via_presigned_url():
    """Upload bytes, get back an :class:`ArtifactRef`, and confirm the
    presigned URL it carries actually serves those exact bytes back with the
    PDF content type — the full contract a skill's caller relies on."""
    STORE.ensure_bucket()
    content = b"%PDF-1.4\n%test_artifact_store roundtrip fixture\n%%EOF\n"

    ref = STORE.put_pdf("test-artifact-store/roundtrip.pdf", content)

    assert ref.name == "test-artifact-store/roundtrip.pdf"
    assert ref.mime == "application/pdf"
    assert ref.url.startswith("http")

    response = httpx.get(ref.url, timeout=CONNECT_TIMEOUT_SECONDS)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == content
    assert response.content.startswith(b"%PDF")
