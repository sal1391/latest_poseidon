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
