"""Tests for Phase 13 Task 3 (doc 05 section 5, doc 01 section 9): the
``/api/me/settings`` + ``/api/me/memory*`` HTTP surface (``api/me.py``) and
its mount switch in ``api/app.py``'s ``_wire_live_chat``.

Mirrors ``test_turns_reconcile.py``'s own pg conventions exactly: a
module-level ``DATABASE_URL`` probe (skips the whole file if unset,
unreachable, or migration 0008 has not been applied), ``httpx.
ASGITransport`` against a real ``create_app()``, and fresh, run-unique
``X-Dev-User`` identities (never bare "alice"/"bob" literals -- re-running
this suite against the same long-lived dev Postgres must never collide with
a previous run's rows). This whole file is pg-only (module docstring's/the
brief's own "Step 1" call): every route under test reaches a real
``ProfileStore``/``MemoryStore`` transaction, so there is no offline double
for any of it.

The ``require_sales`` 401/403 matrix mirrors ``test_api_auth.py``'s own
``_auth0_app`` pattern (a real app under ``identity_mode="auth0"``, its
``identity_provider`` swapped post-construction for one wired to a local,
in-process JWKS transport -- zero real network calls) rather than re-proving
``require_sales`` itself (already proven exhaustively there): one 401
assertion and one 403 assertion per route is enough here, per this task's
own brief.
"""

import os
import uuid

import httpx
import psycopg
import pytest

from poseidon.core.config import Settings
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.identity_auth0 import ROLES_CLAIM, Auth0Provider
from tests.test_identity_providers import (
    AUTH0_TEST_AUDIENCE,
    AUTH0_TEST_DOMAIN,
    JwksTransport,
    generate_rsa_keypair,
    jwk_for,
    mint_auth0_token,
)

pytestmark = pytest.mark.pg

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0008)"

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        "DATABASE_URL is not set - pg /api/me route tests need a Postgres: "
        f"{_UP_HINT}, {_MIGRATE_HINT}",
        allow_module_level=True,
    )

try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'user_profile'"
            )
            if _cur.fetchone() is None:
                pytest.skip(
                    f"user_profile does not exist - {_MIGRATE_HINT}", allow_module_level=True
                )
except psycopg.Error as exc:
    pytest.skip(
        f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}",
        allow_module_level=True,
    )


@pytest.fixture
def pg_database_url() -> str:
    return _DSN


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings_override(pg_database_url, monkeypatch):
    """Mirrors ``test_personalization_stores.py``'s own fixture of the same
    name exactly, for the identical reason: ``UserMemory.write_version``
    reads ``settings.memory_max_chars`` via the process-wide, ``lru_cache``-
    wrapped ``get_settings()`` -- NOT the ``Settings`` instance passed to
    ``create_app`` -- so proving the 422-too-large path needs a real env-var
    override plus a cache clear, not merely a differently-configured app."""
    from poseidon.core.config import get_settings

    applied = {"done": False}

    def _apply(**overrides) -> None:
        for key in (name.upper() for name in Settings.model_fields):
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("DATABASE_URL", pg_database_url)
        monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
        for key, value in overrides.items():
            monkeypatch.setenv(key.upper(), str(value))
        get_settings.cache_clear()
        applied["done"] = True

    yield _apply
    if applied["done"]:
        get_settings.cache_clear()


def _dev_user(name: str) -> str:
    """A fresh, run-unique ``X-Dev-User`` value -- see the module
    docstring's "fresh, run-unique act-as identities" precedent
    (``test_turns_reconcile.py``)."""
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _headers(user: str) -> dict[str, str]:
    return {"X-Dev-User": user}


def _entry(entry_type: str, statement: str, **overrides) -> dict:
    entry = {
        "type": entry_type,
        "statement": statement,
        "source_conversation_id": str(uuid.uuid4()),
        "at": "2026-08-04T12:00:00+00:00",
    }
    entry.update(overrides)
    return entry


def _settings(pg_database_url: str, **overrides) -> Settings:
    defaults: dict = dict(
        _env_file=None,
        database_url=pg_database_url,
        s3_bucket="poseidon-artifacts",
        llm_mode="stub",
        llm_profile="bedrock",
        chat_mode="live",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _app(pg_database_url: str, **overrides):
    from poseidon.api.app import create_app

    return create_app(_settings(pg_database_url, **overrides))


def _auth0_app(transport: httpx.BaseTransport, pg_database_url: str, **overrides):
    """Mirrors ``test_api_auth.py``'s own ``_auth0_app`` helper exactly: a
    real app under ``identity_mode="auth0"``, its ``identity_provider``
    swapped post-construction for one wired to the injected, local-only
    JWKS ``transport`` -- see that module's own docstring for why building
    the real (default-transport) provider ``create_app`` builds at boot is
    harmless (never used; discarded before any request reaches it)."""
    from poseidon.api.app import create_app

    settings = _settings(
        pg_database_url,
        identity_mode="auth0",
        auth0_domain=AUTH0_TEST_DOMAIN,
        auth0_audience=AUTH0_TEST_AUDIENCE,
        auth0_client_id="test-client-id",
        **overrides,
    )
    app = create_app(settings)
    app.state.identity_provider = Auth0Provider(settings, transport=transport)
    return app


# ===========================================================================
# GET/PUT /api/me/settings
# ===========================================================================


@pytest.mark.anyio
async def test_get_settings_for_a_brand_new_user_returns_the_default_shape_never_404(
    pg_database_url,
):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me/settings", headers=headers)

    assert r.status_code == 200
    # memory_max_chars is Task 5's own cap-source-gap amendment (commit
    # 5130fee): additive on this route, sourced from Settings.
    # memory_max_chars (config.py's own default of 8000, since _app/
    # _settings below applies no override) -- never omitted, and never a
    # frontend-hardcoded guess.
    assert r.json() == {"system_instruction": "", "updated_at": None, "memory_max_chars": 8000}


@pytest.mark.anyio
async def test_put_settings_then_get_round_trips(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        put_response = await client.put(
            "/api/me/settings",
            json={"system_instruction": "always show GP in USD thousands"},
            headers=headers,
        )
        get_response = await client.get("/api/me/settings", headers=headers)

    assert put_response.status_code == 200
    body = put_response.json()
    assert body["system_instruction"] == "always show GP in USD thousands"
    assert body["updated_at"] is not None
    # PUT's own response carries no memory_max_chars (the amendment's
    # sanctioned scope names the GET route only) -- so the round-trip
    # proof below compares the two SHARED fields explicitly rather than a
    # single `get_response.json() == body` dict equality, which the GET-only
    # additive field would now break.
    assert get_response.status_code == 200
    get_body = get_response.json()
    assert get_body["system_instruction"] == body["system_instruction"]
    assert get_body["updated_at"] == body["updated_at"]
    assert get_body["memory_max_chars"] == 8000


@pytest.mark.anyio
async def test_get_settings_carries_the_configured_memory_max_chars(pg_database_url):
    """``request.app.state.settings`` is the literal ``Settings`` instance
    ``_app``/``_settings`` below construct and pass straight into
    ``create_app`` -- NOT the process-wide, ``lru_cache``-wrapped
    ``get_settings()`` singleton ``settings_override`` (this file's own
    fixture, used by the 422-too-large test below) exists to patch. So this
    test proves the real cap-source wiring with a plain constructor
    override, no env-var monkeypatching or cache-clearing needed."""
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url, memory_max_chars=4321)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me/settings", headers=headers)

    assert r.status_code == 200
    assert r.json()["memory_max_chars"] == 4321


@pytest.mark.anyio
async def test_put_settings_with_an_empty_string_is_accepted_not_an_error(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.put("/api/me/settings", json={"system_instruction": ""}, headers=headers)

    assert r.status_code == 200
    assert r.json()["system_instruction"] == ""


@pytest.mark.anyio
async def test_two_users_settings_never_leak_at_the_http_layer(pg_database_url):
    alice = _headers(_dev_user("alice"))
    bob = _headers(_dev_user("bob"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await client.put(
            "/api/me/settings",
            json={"system_instruction": "alice's own instruction"},
            headers=alice,
        )
        await client.put(
            "/api/me/settings", json={"system_instruction": "bob's own instruction"}, headers=bob
        )
        alice_get = await client.get("/api/me/settings", headers=alice)
        bob_get = await client.get("/api/me/settings", headers=bob)

    assert alice_get.json()["system_instruction"] == "alice's own instruction"
    assert bob_get.json()["system_instruction"] == "bob's own instruction"


# ===========================================================================
# GET/PUT /api/me/memory, GET /api/me/memory/versions
# ===========================================================================


@pytest.mark.anyio
async def test_get_memory_for_a_brand_new_user_is_404(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me/memory", headers=headers)

    assert r.status_code == 404


@pytest.mark.anyio
async def test_put_memory_with_valid_entries_creates_version_1_and_get_reflects_it(
    pg_database_url,
):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    entries = [_entry("preference", "prefers USD thousands")]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        put_response = await client.put(
            "/api/me/memory", json={"entries": entries}, headers=headers
        )
        get_response = await client.get("/api/me/memory", headers=headers)

    assert put_response.status_code == 200
    body = put_response.json()
    assert body["version"] == 1
    assert body["created_by"] == "user"
    assert body["entries"] == entries
    assert get_response.status_code == 200
    assert get_response.json() == body


@pytest.mark.anyio
async def test_put_memory_again_creates_version_2(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        first = await client.put(
            "/api/me/memory", json={"entries": [_entry("fact", "first version")]}, headers=headers
        )
        second = await client.put(
            "/api/me/memory", json={"entries": [_entry("fact", "second version")]}, headers=headers
        )

    assert first.json()["version"] == 1
    assert second.status_code == 200
    assert second.json()["version"] == 2


@pytest.mark.anyio
async def test_get_memory_versions_lists_both_newest_first_with_entry_count(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await client.put(
            "/api/me/memory", json={"entries": [_entry("fact", "one")]}, headers=headers
        )
        await client.put(
            "/api/me/memory",
            json={"entries": [_entry("fact", "two"), _entry("scope", "three")]},
            headers=headers,
        )
        versions_response = await client.get("/api/me/memory/versions", headers=headers)

    assert versions_response.status_code == 200
    versions = versions_response.json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["created_by"] == "user"
    assert versions[0]["entry_count"] == 2
    assert versions[1]["entry_count"] == 1


@pytest.mark.anyio
async def test_put_memory_over_a_tiny_max_chars_is_422_and_the_write_does_not_land(
    pg_database_url, settings_override
):
    settings_override(memory_max_chars=10)
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    entries = [_entry("preference", "this statement is far longer than ten characters")]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        put_response = await client.put(
            "/api/me/memory", json={"entries": entries}, headers=headers
        )
        get_response = await client.get("/api/me/memory", headers=headers)

    assert put_response.status_code == 422
    body = put_response.json()
    assert body["status"] == 422
    assert body["title"] == "memory_too_large"
    assert "memory_max_chars" in body["detail"]
    assert get_response.status_code == 404, "an oversized write must not land"


@pytest.mark.anyio
async def test_put_memory_with_a_malformed_entry_is_422_and_the_write_does_not_land(
    pg_database_url,
):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    bad_entry = _entry("not-a-real-type", "a valid statement")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        put_response = await client.put(
            "/api/me/memory", json={"entries": [bad_entry]}, headers=headers
        )
        get_response = await client.get("/api/me/memory", headers=headers)

    assert put_response.status_code == 422
    body = put_response.json()
    assert body["status"] == 422
    assert body["title"] == "memory_invalid"
    assert body["detail"]
    assert get_response.status_code == 404, "a malformed write must not land"


@pytest.mark.anyio
async def test_post_restore_on_an_older_version_creates_a_new_version_forcing_created_by_user(
    pg_database_url,
):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    v1_entries = [_entry("fact", "version one statement")]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await client.put("/api/me/memory", json={"entries": v1_entries}, headers=headers)
        await client.put(
            "/api/me/memory",
            json={"entries": [_entry("correction", "version two statement")]},
            headers=headers,
        )

        restore_response = await client.post(
            "/api/me/memory/versions/1/restore", headers=headers
        )

    assert restore_response.status_code == 200
    body = restore_response.json()
    assert body["version"] == 3, "restore must APPEND, not rewrite"
    assert body["created_by"] == "user"
    assert body["entries"] == v1_entries


@pytest.mark.anyio
async def test_post_restore_on_a_nonexistent_version_is_404(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/me/memory/versions/99/restore", headers=headers)

    assert r.status_code == 404


@pytest.mark.anyio
async def test_two_users_memory_never_leaks_at_the_http_layer(pg_database_url):
    alice = _headers(_dev_user("alice"))
    bob = _headers(_dev_user("bob"))
    app = _app(pg_database_url)
    alice_entries = [_entry("fact", "alice's own fact")]
    bob_entries = [_entry("fact", "bob's own fact")]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await client.put("/api/me/memory", json={"entries": alice_entries}, headers=alice)
        await client.put("/api/me/memory", json={"entries": bob_entries}, headers=bob)
        alice_get = await client.get("/api/me/memory", headers=alice)
        bob_get = await client.get("/api/me/memory", headers=bob)
        alice_versions = await client.get("/api/me/memory/versions", headers=alice)
        bob_versions = await client.get("/api/me/memory/versions", headers=bob)

    assert alice_get.json()["entries"] == alice_entries
    assert bob_get.json()["entries"] == bob_entries
    assert len(alice_versions.json()) == 1
    assert len(bob_versions.json()) == 1


@pytest.mark.anyio
async def test_restore_is_scoped_per_user_even_though_version_numbers_overlap(pg_database_url):
    """Task 1's own module docstring (``core/personalization/memory.py``)
    warns that a bare version number is never globally unique -- alice's
    and bob's own version 1 are different rows sharing the same integer.
    Proves ``POST .../restore`` never crosses that boundary at the HTTP
    layer: bob restoring "version 1" gets HIS OWN version 1 back, never
    alice's, even though both exist simultaneously under the same number.
    """
    alice = _headers(_dev_user("alice"))
    bob = _headers(_dev_user("bob"))
    app = _app(pg_database_url)
    alice_v1 = [_entry("fact", "alice's v1")]
    bob_v1 = [_entry("fact", "bob's v1")]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await client.put("/api/me/memory", json={"entries": alice_v1}, headers=alice)
        await client.put("/api/me/memory", json={"entries": bob_v1}, headers=bob)
        await client.put(
            "/api/me/memory", json={"entries": [_entry("fact", "bob's v2")]}, headers=bob
        )

        bob_restore = await client.post("/api/me/memory/versions/1/restore", headers=bob)

    assert bob_restore.status_code == 200
    assert bob_restore.json()["entries"] == bob_v1


# ===========================================================================
# require_sales: one 401 + one 403 assertion per route (not re-proving
# require_sales itself -- see module docstring)
# ===========================================================================

_ROUTES = [
    ("GET", "/api/me/settings"),
    ("PUT", "/api/me/settings"),
    ("GET", "/api/me/memory"),
    ("PUT", "/api/me/memory"),
    ("GET", "/api/me/memory/versions"),
    ("POST", "/api/me/memory/versions/1/restore"),
]


@pytest.mark.parametrize("method,path", _ROUTES)
@pytest.mark.anyio
async def test_every_me_route_401s_an_unauthenticated_caller_under_auth0_mode(
    pg_database_url, method, path
):
    _key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]), pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.request(method, path)

    assert r.status_code == 401


@pytest.mark.parametrize("method,path", _ROUTES)
@pytest.mark.anyio
async def test_every_me_route_403s_a_role_less_caller_under_auth0_mode(
    pg_database_url, method, path
):
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]), pg_database_url)
    token = mint_auth0_token(key1, "key-1", **{ROLES_CLAIM: []})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.request(method, path, headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 403


# ===========================================================================
# ASCII-on-disk (house rule)
# ===========================================================================


def test_me_and_test_me_routes_modules_are_ascii_on_disk():
    from pathlib import Path

    from poseidon.api import me as me_module

    paths = [Path(me_module.__file__), Path(__file__)]
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
