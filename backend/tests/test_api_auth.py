"""Tests for Phase 9 Task 1: the identity middleware (``api/app.py``'s
``_install_identity_middleware``), ``GET /api/me`` (``api/auth.py``), and
the end-to-end ``user_sub`` threading proof -- a real ``X-Dev-User`` header,
through the real middleware, through ``live_chat.py`` and ``core/chat/
orchestrator.py``, landing in a run-log writer double's ``user_sub``.

"Existing-suite-unchanged" (the disabled-mode-default rule: every pre-
Phase-9 test still boots and behaves identically) is proven by the FULL
offline run, not by any one test in this file -- see this task's own
report for that evidence.

Mirrors ``test_live_chat_sse.py``'s own discipline throughout: a real
``create_app``, ``httpx.ASGITransport``, ``RecordingWriter``/
``FakeDataClient`` imported from ``test_chat_orchestrator.py`` rather than
re-derived (the identical cross-test-module reuse that file already
established for the same fixtures).

Phase 9 Task 2 adds, below the Task 1 sections: the Auth0 mode HTTP-level
matrix (a real app, a real request, the local JWKS fixture -- provider-
level cases already covered by ``test_identity_providers.py`` are not
re-proven here, only what changes AT the HTTP boundary: status codes,
RFC-7807 bodies, ``/health/*`` staying open); the ``/api/skills``/``/api/
dev/*`` role-guard matrix; the chat-send rate limiter; and the CORS
allowlist, including a preflight round trip. The JWKS-fixture helpers
(``generate_rsa_keypair``, ``jwk_for``, ``mint_auth0_token``,
``JwksTransport``, ``AUTH0_TEST_DOMAIN``/``AUTH0_TEST_AUDIENCE``) are
imported from ``test_identity_providers.py`` rather than re-derived -- the
same cross-test-module reuse already established for ``FakeDataClient``/
``RecordingWriter`` above.

Phase 9 Task 3 adds, at the very bottom: the narrow SPCS-ingress
HTTP-boundary slice -- boot fail-fast through the real ``create_app``,
``GET /api/me``, ``/health/*`` staying open, and the ``require_sales`` 403
for a non-allowlisted user. Provider-level cases (allowlist membership,
casefold, malformed-header-401) live in ``test_identity_providers.py``'s
own ``SpcsIngressProvider`` matrix instead, the same provider-tests-there/
HTTP-tests-here split Task 2 established above.

Phase 14 Task 1 adds, at the very bottom: the off-the-event-loop proof for
the identity middleware's own ``provider.resolve`` call. It belongs here
rather than in ``test_identity_providers.py`` for exactly the reason this
file's split already states -- WHICH thread ``resolve`` runs on is a
property of the middleware, not of any provider, and only a real request
through the real middleware can show it.
"""

import os
import sys
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi import Request
from pydantic import ValidationError

from poseidon.api import auth as auth_module
from poseidon.api.auth import current_user
from poseidon.core.config import Settings
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.identity import DISABLED_DEFAULT_USER
from poseidon.core.identity_auth0 import ROLES_CLAIM, Auth0Provider
from tests.test_chat_orchestrator import FakeDataClient, RecordingWriter
from tests.test_identity_providers import (
    AUTH0_TEST_AUDIENCE,
    AUTH0_TEST_DOMAIN,
    FailingJwksTransport,
    JwksTransport,
    UnreachableJwksTransport,
    generate_rsa_keypair,
    jwk_for,
    mint_auth0_token,
    mint_auth0_token_without_sub_claim,
    oct_jwk_for,
)

_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

# ===========================================================================
# Phase 10 Task 3 (the cutover): a chat_mode="live" app's /api/conversations*
# routes now depend on a real Postgres for every call that touches a
# conversation -- see api/live_chat.py's own module docstring. Every test in
# this file that drives such a route now needs a REAL, reachable dsn
# (:func:`pg_database_url`, mirroring test_live_chat_sse.py's own fixture of
# the same name) rather than the placeholder above: against the placeholder,
# a route that reaches an actual rls_transaction call blocks on a real TCP
# connect attempt to a closed loopback port, which does not fail fast on
# every platform. A route whose whole path 404s BEFORE ever opening a
# connection (a malformed, non-uuid ad hoc cid like the ones this file used
# to use -- UserHistory.append_user_message's own fail-fast "not a valid
# id" check runs before any transaction opens) would happen to still return
# quickly against the placeholder, but that is an accident of WHICH check
# fires first, not something these tests should rely on -- they use a real,
# reachable Postgres and a real conversation like every other adapted test
# in this suite.
# ===========================================================================

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0004)"


@pytest.fixture
def pg_database_url() -> str:
    """``DATABASE_URL``, or a SKIP with an actionable reason -- see
    ``test_history_store.py``'s own ``pg_engine``/``test_live_chat_sse.py``'s
    own ``pg_database_url`` for the identical reachability/migration check."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip(
            f"DATABASE_URL is not set - pg live chat tests need a Postgres: "
            f"{_UP_HINT}, {_MIGRATE_HINT}"
        )
    try:
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.conversations')")
                if cur.fetchone()[0] is None:
                    pytest.skip(f"conversations does not exist - {_MIGRATE_HINT}")
    except psycopg.Error as exc:
        pytest.skip(
            f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
            f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}"
        )
    return dsn

# The flagship scripted phrase test_chat_orchestrator.py/test_live_chat_sse.
# py already pin frame-by-frame (DevDeterministicRouter's own lexicon match)
# -- reused here rather than an ad hoc string, so these tests depend on
# nothing new about what LLM_MODE=stub does with arbitrary text, only on
# already-proven routing behavior.
FLAGSHIP_TEXT = "Top GP customers for Port of Singapore in April 2026"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _settings(**overrides) -> Settings:
    defaults: dict = dict(
        _env_file=None,
        database_url=_PLACEHOLDER_DSN,
        s3_bucket="poseidon-artifacts",
        llm_mode="stub",
        llm_profile="bedrock",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _mock_app(**overrides):
    from poseidon.api.app import create_app

    return create_app(_settings(**overrides))


def _live_app(*, data_client=None, writer=None, **overrides):
    """Mirrors test_live_chat_sse.py's own ``_live_app`` helper exactly:
    the same post-construction ``app.state`` substitution that file already
    uses for ``data_client``/``run_log_writer``."""
    from poseidon.api.app import create_app

    app = create_app(_settings(chat_mode="live", **overrides))
    if data_client is not None:
        app.state.data_client = data_client
    if writer is not None:
        app.state.run_log_writer = writer
    return app


def _auth0_app(transport: httpx.BaseTransport, **overrides):
    """Builds a real app under ``identity_mode="auth0"``, then swaps its
    ``identity_provider`` for one wired to the injected, local-only JWKS
    ``transport`` -- the SAME post-construction ``app.state`` substitution
    pattern ``_live_app`` above already uses for ``data_client``/``writer``.

    ``create_app`` itself still calls ``resolve_provider``, which builds a
    real (default-transport) ``Auth0Provider`` at boot -- harmless, since
    building an ``httpx.Client`` opens no connection until a request is
    actually made (this codebase's own "engines are lazy" convention -- see
    ``core/config.py``'s docstring precedent for ``SyntheticDataClient``).
    That instance is simply discarded here before any request ever reaches
    it, so this test suite never performs a real network call (Global
    Constraints: "ZERO live/network calls").
    """
    from poseidon.api.app import create_app

    settings = _settings(
        identity_mode="auth0",
        auth0_domain=AUTH0_TEST_DOMAIN,
        auth0_audience=AUTH0_TEST_AUDIENCE,
        auth0_client_id="test-client-id",
        **overrides,
    )
    app = create_app(settings)
    app.state.identity_provider = Auth0Provider(settings, transport=transport)
    return app


async def _send_turn(client: httpx.AsyncClient, cid: str, text: str, *, headers=None) -> None:
    """Drains one chat turn's SSE stream to completion -- mirrors
    ``test_live_chat_sse.py``'s own ``read_sse``, minus the frame parsing
    this file does not need: the threading proof below cares about the
    run-log writer double's recorded kwargs, not the wire frames. Draining
    ``aiter_lines()`` fully is what guarantees the server-side turn (and
    every writer call it makes) has actually finished by the time this
    returns -- the same guarantee ``read_sse`` relies on."""
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"text": text}, headers=headers or {}
    ) as response:
        assert response.status_code == 200
        async for _line in response.aiter_lines():
            pass


async def _create_conversation(client: httpx.AsyncClient, *, headers=None) -> str:
    """A real conversation id via ``POST /api/conversations`` -- mirrors
    ``test_live_chat_sse.py``'s own helper of the same name. ``headers``
    matters here specifically: a conversation created as one identity is
    invisible (RLS) to every other one, so a test proving something about
    ``bob`` needs a conversation ``bob`` himself created, never one reused
    from ``alice``."""
    r = await client.post("/api/conversations", headers=headers or {})
    return r.json()["conversation"]["id"]


def _dev_user(name: str = "rl") -> str:
    """A fresh, run-unique ``X-Dev-User`` act-as value -- mirrors
    ``test_live_chat_sse.py``'s own ``_dev_user`` helper exactly (see that
    helper's own docstring for the full rationale).

    Final-round fix (2026-08-05): the chat-send rate-limit tests below used
    to dispatch every turn header-less, landing on the shared, fixed
    ``dev|local`` default -- 35 + 10 + 2 turns per full pg run, none of it
    a real user's data, all of it under the one identity Carlos's own
    browser resolves to. Each of those tests genuinely does not care WHICH
    sub it uses (rate limiting is keyed by sub, proven independent by
    ``test_chat_rate_limit_keys_independently_per_act_as_sub`` above), so
    they now pose as a throwaway ``dev|rl-<hex>``-shaped identity instead,
    the same convention ``test_live_chat_sse.py``/``test_chat_e2e_scripted.
    py`` already established for this file's sibling suites.
    ``test_no_act_as_header_still_uses_the_pinned_disabled_mode_default``
    is deliberately NOT converted -- its whole purpose is proving the
    header-less, ``dev|local`` default path."""
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _headers(user: str) -> dict[str, str]:
    return {"X-Dev-User": user}


# ===========================================================================
# current_user -- the dependency's own defensive branch
# ===========================================================================


def test_current_user_raises_when_request_state_user_is_unset():
    """Unreachable through any real request in this task -- the middleware
    always sets request.state.user, unconditionally, before any dependency
    runs (DisabledProvider.resolve never raises -- see its own docstring).
    Proven directly against a bare Request the middleware never touched, so
    a future regression (the middleware silently not running) fails loudly
    here instead of masquerading as a mysterious 401 downstream."""
    request = Request(scope={"type": "http", "headers": []})
    with pytest.raises(RuntimeError, match="identity middleware"):
        current_user(request)


# ===========================================================================
# GET /api/me -- contract, mode-agnostic mounting, act-as
# ===========================================================================


@pytest.mark.anyio
async def test_me_returns_the_fixed_default_identity_with_no_header():
    app = _mock_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me")

    assert r.status_code == 200
    assert r.json() == {
        "sub": "dev|local",
        "name": "Dev User",
        "email": "dev@local",
        "roles": ["Poseidon:Sales"],
        "identity_mode": "disabled",
    }


@pytest.mark.anyio
async def test_me_is_mounted_regardless_of_chat_mode():
    """GET /api/me must not be gated behind chat_mode="live": doc 05
    section 2's frontend seam calls it on boot, before the SPA knows or
    cares which chat surface (mock or live) the backend mounted."""
    for app in (_mock_app(), _live_app()):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/api/me")
        assert r.status_code == 200
        assert r.json()["identity_mode"] == "disabled"


@pytest.mark.anyio
async def test_me_reflects_a_valid_act_as_header():
    app = _mock_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"X-Dev-User": "alice"})

    assert r.status_code == 200
    assert r.json()["sub"] == "dev|alice"


@pytest.mark.anyio
async def test_me_ignores_an_invalid_act_as_header():
    app = _mock_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"X-Dev-User": "not valid!"})

    assert r.json()["sub"] == "dev|local"


# ===========================================================================
# The end-to-end threading proof: X-Dev-User -> the identity middleware ->
# request.state.user -> live_chat.py -> execute_turn -> the run-log
# writer's own user_sub, on every writer call this turn produces.
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_act_as_header_flows_through_to_every_run_log_writer_call(pg_database_url):
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, database_url=pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        headers = {"X-Dev-User": "alice"}
        cid = await _create_conversation(client, headers=headers)
        await _send_turn(client, cid, FLAGSHIP_TEXT, headers=headers)

    assert len(writer.start_turn_calls) == 1
    assert writer.start_turn_calls[0]["user_sub"] == "dev|alice"
    assert len(writer.append_llm_calls) == 2
    assert all(row["user_sub"] == "dev|alice" for row in writer.append_llm_calls)
    assert len(writer.append_tool_calls) == 1
    assert writer.append_tool_calls[0]["user_sub"] == "dev|alice"


@pytest.mark.pg
@pytest.mark.anyio
async def test_no_act_as_header_still_uses_the_pinned_disabled_mode_default(pg_database_url):
    """The pg-stability pin, proven here at the HTTP layer too (see this
    task's own report for the pg re-run): user_sub flows must not shift for
    the common, no-header case -- the SAME "dev|local" every pre-Phase-9
    run-log row already carries."""
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, database_url=pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = await _create_conversation(client)
        await _send_turn(client, cid, FLAGSHIP_TEXT)

    assert writer.start_turn_calls[0]["user_sub"] == "dev|local"


@pytest.mark.pg
@pytest.mark.anyio
async def test_two_act_as_identities_in_the_same_process_get_independent_user_subs(
    pg_database_url,
):
    """Multi-user local testing, the whole point of the act-as header
    (Global Constraints) -- two different X-Dev-User values against the
    SAME running app/registry produce two independent user_subs, never
    conflated with each other or with the fixed default. Each identity
    sends into a conversation IT created: RLS means alice cannot send into
    a conversation only bob's sub has ever created."""
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, database_url=pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        alice_headers = {"X-Dev-User": "alice"}
        bob_headers = {"X-Dev-User": "bob"}
        alice_cid = await _create_conversation(client, headers=alice_headers)
        bob_cid = await _create_conversation(client, headers=bob_headers)
        await _send_turn(client, alice_cid, FLAGSHIP_TEXT, headers=alice_headers)
        await _send_turn(client, bob_cid, FLAGSHIP_TEXT, headers=bob_headers)

    subs = [call["user_sub"] for call in writer.start_turn_calls]
    assert subs == ["dev|alice", "dev|bob"]


# ===========================================================================
# core/config.py -- Phase 9 Task 2's new Settings fields (CORS + rate
# limit), Settings-level only, no ASGI app needed
# ===========================================================================


def test_cors_allow_origins_defaults_to_the_vite_dev_origin():
    assert _settings().cors_allow_origins == ["http://localhost:5173"]


def test_cors_allow_origins_accepts_a_comma_separated_string():
    """The ergonomic shape for a plain ``.env``/compose file -- far simpler
    to author than a JSON array literal (``core/config.py``'s own
    ``split_cors_origins`` validator)."""
    settings = _settings(cors_allow_origins="http://a.test,http://b.test")
    assert settings.cors_allow_origins == ["http://a.test", "http://b.test"]


def test_cors_allow_origins_strips_whitespace_around_commas():
    settings = _settings(cors_allow_origins=" http://a.test , http://b.test ")
    assert settings.cors_allow_origins == ["http://a.test", "http://b.test"]


def test_cors_allow_origins_rejects_a_wildcard():
    """Global Constraints: "never * with credentials" -- boot-time
    fail-fast, the same discipline this file's own docstring pins for
    every other malformed value (see ``core/config.py``'s
    ``no_wildcard_cors_origin`` validator for why this is enforced here
    rather than trusted to never be misconfigured)."""
    with pytest.raises(ValidationError):
        _settings(cors_allow_origins="*")


def test_effective_rate_limit_defaults_to_zero_in_disabled_mode():
    """Global Constraints: "OFF in disabled mode by default so dev/tests
    are unaffected"."""
    settings = _settings()
    assert settings.identity_mode == "disabled"
    assert settings.effective_rate_limit_chat_per_minute == 0


def test_effective_rate_limit_defaults_to_thirty_outside_disabled_mode():
    settings = _settings(
        identity_mode="auth0",
        auth0_domain=AUTH0_TEST_DOMAIN,
        auth0_audience=AUTH0_TEST_AUDIENCE,
        auth0_client_id="test-client-id",
    )
    assert settings.effective_rate_limit_chat_per_minute == 30


def test_effective_rate_limit_explicit_override_wins_in_disabled_mode_too():
    """Judgment call (disclosed in this task's report): an operator's
    EXPLICIT ``RATE_LIMIT_CHAT_PER_MINUTE`` is honored in ANY mode,
    including ``disabled`` -- not just "off unless auth0/spcs_ingress"."""
    settings = _settings(rate_limit_chat_per_minute=5)
    assert settings.effective_rate_limit_chat_per_minute == 5


def test_effective_rate_limit_explicit_zero_means_off_outside_disabled_mode():
    settings = _settings(
        identity_mode="auth0",
        auth0_domain=AUTH0_TEST_DOMAIN,
        auth0_audience=AUTH0_TEST_AUDIENCE,
        auth0_client_id="test-client-id",
        rate_limit_chat_per_minute=0,
    )
    assert settings.effective_rate_limit_chat_per_minute == 0


# ===========================================================================
# Auth0 mode at the HTTP boundary: GET /api/me, and /health/* staying open
# even when a request carries no/a bad token (Global Constraints:
# "/health/* stays open")
# ===========================================================================


@pytest.mark.anyio
async def test_me_under_auth0_mode_with_a_valid_token_returns_the_claims():
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json() == {
        "sub": "auth0|user123",
        "name": "Alice",
        "email": "alice@example.com",
        "roles": ["Poseidon:Sales"],
        "identity_mode": "auth0",
    }


@pytest.mark.anyio
async def test_me_under_auth0_mode_with_no_token_is_401():
    _key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me")

    assert r.status_code == 401
    assert r.json() == {
        "type": "about:blank",
        "title": "missing bearer token",
        "detail": "no Authorization header",
        "status": 401,
    }


@pytest.mark.anyio
async def test_me_under_auth0_mode_with_an_expired_token_is_401():
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key1, "key-1", exp=datetime.now(UTC) - timedelta(minutes=1))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 401
    assert r.json()["title"] == "token expired"


@pytest.mark.anyio
async def test_me_under_auth0_mode_with_a_role_less_token_still_succeeds():
    """Handoff #3 (Task 2's own brief): "/api/me should remain reachable
    for any authenticated-or-disabled-mode user" -- ANY role, including
    none at all, since this is how a caller discovers they lack
    Poseidon:Sales in the first place."""
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key1, "key-1", **{ROLES_CLAIM: []})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json()["roles"] == []


@pytest.mark.anyio
async def test_health_live_stays_open_under_auth0_mode_with_no_token():
    """The identity middleware still runs for /health/* (Task 1's own
    "cheap... paying this on every request" discipline, unchanged) and
    records the AuthError -- but /health/live never depends on
    current_user, so it is never asked, and the request succeeds."""
    _key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/health/live")

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ===========================================================================
# The require-user + require-role guard: /api/skills, /api/dev/*, and (per
# the Controller's Round 0 correction -- these six routes exist TODAY in
# live_chat.py, not only from Phase 10 on) every /api/conversations*/
# /api/messages* route (Global Constraints' enforcement scope)
# ===========================================================================


@pytest.mark.anyio
async def test_api_skills_requires_a_token_under_auth0_mode():
    _key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]), chat_mode="live")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/skills")

    assert r.status_code == 401


@pytest.mark.anyio
async def test_api_skills_403_when_token_lacks_the_sales_role():
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]), chat_mode="live")
    token = mint_auth0_token(key1, "key-1", **{ROLES_CLAIM: []})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/skills", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 403
    assert r.json() == {
        "type": "about:blank",
        "title": "insufficient role",
        "detail": "caller lacks required role 'Poseidon:Sales'",
        "status": 403,
    }


@pytest.mark.anyio
async def test_api_skills_200_when_token_has_the_sales_role():
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]), chat_mode="live")
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/skills", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200


@pytest.mark.anyio
async def test_api_skills_is_open_by_default_in_disabled_mode():
    """Global Constraints: "guards default-open in disabled mode with the
    fixed user" -- proven directly against the guarded route."""
    app = _live_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/skills")

    assert r.status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/conversations"),
        ("GET", "/api/conversations"),
        ("GET", "/api/conversations/some-cid/messages"),
        ("POST", "/api/conversations/some-cid/messages"),
        ("POST", "/api/messages/some-mid/feedback"),
        ("GET", "/api/messages/some-mid/feedback"),
    ],
)
@pytest.mark.anyio
async def test_all_six_conversation_and_message_routes_require_a_token_under_auth0_mode(
    method, path
):
    """Controller's Round 0 correction: the plan's enforcement scope
    ("guards /api/conversations*, /api/messages*, /api/skills, /api/dev/*")
    applies to all six routes live_chat.py already serves TODAY, not only
    from Phase 10 on -- every one of them 401s with no token."""
    _key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]), chat_mode="live")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.request(method, path)

    assert r.status_code == 401
    assert r.json()["title"] == "missing bearer token"


@pytest.mark.anyio
async def test_conversations_post_requires_a_token_under_auth0_mode():
    """The exact pinned 401 body, on one representative conversations
    route (the breadth test above only checks title, across all six)."""
    _key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]), chat_mode="live")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations")

    assert r.status_code == 401
    assert r.json() == {
        "type": "about:blank",
        "title": "missing bearer token",
        "detail": "no Authorization header",
        "status": 401,
    }


@pytest.mark.anyio
async def test_conversations_post_403_when_token_lacks_the_sales_role():
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]), chat_mode="live")
    token = mint_auth0_token(key1, "key-1", **{ROLES_CLAIM: []})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 403
    assert r.json() == {
        "type": "about:blank",
        "title": "insufficient role",
        "detail": "caller lacks required role 'Poseidon:Sales'",
        "status": 403,
    }


@pytest.mark.pg
@pytest.mark.anyio
async def test_conversations_post_200_when_token_has_the_sales_role(pg_database_url):
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(
        JwksTransport([jwk_for(pub1, "key-1")]), chat_mode="live", database_url=pg_database_url
    )
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 201


@pytest.mark.pg
@pytest.mark.anyio
async def test_conversations_post_is_open_by_default_in_disabled_mode(pg_database_url):
    """Global Constraints: "guards default-open in disabled mode with the
    fixed user" -- pinned explicitly on a conversations route too (the
    Controller's Round 0 correction's own ask), not only /api/skills."""
    app = _live_app(database_url=pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations")

    assert r.status_code == 201


@pytest.mark.anyio
async def test_chat_send_requires_a_token_before_rate_limiting_under_auth0_mode():
    """Ordering (Controller's Round 0 correction): require_sales runs
    BEFORE rate_limit_chat_send in this route's own dependencies list, so
    an unauthenticated caller always gets 401 -- never a 429, which would
    both leak rate-limit state to a caller who never authenticated at all
    and contradict "require-user" gating everything downstream of it.
    Proven with an aggressively low limit (1/minute): if the ordering were
    reversed, at least one of these three unauthenticated attempts would
    surface the token bucket's own state (200 then 429) instead of failing
    the auth check first, every single time.
    """
    _key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(
        JwksTransport([jwk_for(pub1, "key-1")]), chat_mode="live", rate_limit_chat_per_minute=1
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        for _ in range(3):
            r = await client.post(
                "/api/conversations/conv-order/messages", json={"text": FLAGSHIP_TEXT}
            )
            assert r.status_code == 401


@pytest.mark.anyio
async def test_dev_skills_run_requires_a_token_under_auth0_mode():
    _key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/dev/skills/whatever/run", json={})

    assert r.status_code == 401


@pytest.mark.anyio
async def test_dev_skills_run_200_when_token_has_the_sales_role():
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post(
            "/api/dev/skills/whatever/run",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )

    # Not a 401/403: the guard passed, so dev_runner.py's OWN "always 200,
    # failure is structured content" contract takes over from here (its own
    # module docstring) -- an unknown skill_id is a 200 with error.status
    # == 404 inside the body, never an HTTP-level 404.
    assert r.status_code == 200
    assert r.json()["ok"] is False


@pytest.mark.anyio
async def test_dev_skills_run_is_open_by_default_in_disabled_mode():
    app = _mock_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/dev/skills/whatever/run", json={})

    assert r.status_code == 200
    assert r.json()["ok"] is False


# ===========================================================================
# Chat-send rate limiter: config-driven token bucket, keyed by sub,
# disabled-mode-default-off (Global Constraints)
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_chat_rate_limit_is_off_by_default_in_disabled_mode(pg_database_url):
    """Global Constraints: "OFF in disabled mode by default so dev/tests
    are unaffected" -- proven directly: MORE than the nominal default of
    30 rapid sends, none blocked, with no explicit override. All 35 land on
    ONE real conversation -- nothing about "35 successful sends, none
    blocked" needs 35 distinct conversations, only 35 distinct turns."""
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, database_url=pg_database_url)
    transport = httpx.ASGITransport(app=app)
    headers = _headers(_dev_user("rl-off"))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = await _create_conversation(client, headers=headers)
        for _i in range(35):
            await _send_turn(client, cid, FLAGSHIP_TEXT, headers=headers)

    assert len(writer.start_turn_calls) == 35


@pytest.mark.pg
@pytest.mark.anyio
async def test_chat_rate_limit_zero_means_off_even_when_explicit(pg_database_url):
    writer = RecordingWriter()
    app = _live_app(
        data_client=FakeDataClient(),
        writer=writer,
        rate_limit_chat_per_minute=0,
        database_url=pg_database_url,
    )
    transport = httpx.ASGITransport(app=app)
    headers = _headers(_dev_user("rl-zero"))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = await _create_conversation(client, headers=headers)
        for _i in range(10):
            await _send_turn(client, cid, FLAGSHIP_TEXT, headers=headers)

    assert len(writer.start_turn_calls) == 10


@pytest.mark.pg
@pytest.mark.anyio
async def test_chat_rate_limit_blocks_once_the_bucket_is_empty(pg_database_url):
    app = _live_app(
        data_client=FakeDataClient(),
        writer=RecordingWriter(),
        rate_limit_chat_per_minute=2,
        database_url=pg_database_url,
    )
    transport = httpx.ASGITransport(app=app)
    headers = _headers(_dev_user("rl-empty"))
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = await _create_conversation(client, headers=headers)
        await _send_turn(client, cid, FLAGSHIP_TEXT, headers=headers)
        await _send_turn(client, cid, FLAGSHIP_TEXT, headers=headers)
        # The THIRD call never reaches the route body at all -- require_sales/
        # rate_limit_chat_send are FastAPI dependencies, resolved before the
        # handler runs, so the empty bucket 429s here regardless of whether
        # "conv-rl-c" names a real conversation (it does not, on purpose --
        # proving the block happens before any conversation lookup).
        r = await client.post(
            "/api/conversations/conv-rl-c/messages",
            json={"text": FLAGSHIP_TEXT},
            headers=headers,
        )

    assert r.status_code == 429
    assert r.json() == {
        "type": "about:blank",
        "title": "rate limit exceeded",
        "detail": "too many chat messages; retry after the interval in the Retry-After header",
        "status": 429,
    }
    # The numeric value is real-clock-dependent (this class's own docstring
    # -- never byte-pinned), but must be a small, positive, sane number of
    # seconds, present exactly where RFC 9110 sec 10.2.3 says to look.
    retry_after = int(r.headers["retry-after"])
    assert 1 <= retry_after <= 60


@pytest.mark.pg
@pytest.mark.anyio
async def test_chat_rate_limit_keys_independently_per_act_as_sub(pg_database_url):
    """Global Constraints: "keyed by sub" -- alice exhausting her own
    bucket must never affect bob's, proven the same way test_api_auth.py's
    own Task 1 section already proves act-as identities are independent."""
    app = _live_app(
        data_client=FakeDataClient(),
        writer=RecordingWriter(),
        rate_limit_chat_per_minute=1,
        database_url=pg_database_url,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        alice_headers = {"X-Dev-User": "alice"}
        bob_headers = {"X-Dev-User": "bob"}
        alice_cid = await _create_conversation(client, headers=alice_headers)
        bob_cid = await _create_conversation(client, headers=bob_headers)
        await _send_turn(client, alice_cid, FLAGSHIP_TEXT, headers=alice_headers)
        # Blocked before the route body runs (same reasoning as the single-
        # user test above) -- "conv-rl-alice-2" need not be real.
        r_alice_second = await client.post(
            "/api/conversations/conv-rl-alice-2/messages",
            json={"text": FLAGSHIP_TEXT},
            headers=alice_headers,
        )
        # Bob's OWN, bob-created conversation -- RLS means alice's cid is
        # invisible to him, so this must be a real conversation bob himself
        # made, not merely a fresh ad hoc string.
        r_bob_first = await client.post(
            f"/api/conversations/{bob_cid}/messages",
            json={"text": FLAGSHIP_TEXT},
            headers=bob_headers,
        )

    assert r_alice_second.status_code == 429
    assert r_bob_first.status_code == 200


def test_chat_rate_limiter_check_is_thread_safe_under_concurrent_callers():
    """Global Constraints: "thread-safe". Reviewer's I-3: ``ChatRateLimiter``'s
    lock (``api/auth.py``'s ``check()``) was correct by inspection but had
    zero test coverage under real concurrency. This exercises it directly:
    a bucket with EXACTLY one token (``per_minute=1``) is hit by many
    threads simultaneously (synchronized through a ``threading.Barrier``,
    never a sleep) -- without the lock's atomicity across the whole
    read-refill-decrement sequence, more than one thread could observe "a
    token is available" before either actually spends it (a classic
    lost-update race), over-admitting more than the one caller the
    configured limit allows.

    Deterministic by construction, not by timing luck:

    - A FRESH key per round means every round starts from a bucket
      provably at exactly 1.0 tokens (never a stale, partially-refilled
      one), so there is nothing to race against except the concurrent
      calls themselves.
    - ``per_minute=1`` refills at ~0.0167 tokens/second; one round (32
      threads, a barrier release, 32 lock-guarded dict operations) runs in
      microseconds -- many orders of magnitude below the ~60 real seconds
      of elapsed time it would take this bucket to refill even one whole
      token, so a slow/loaded machine can only make this test slower,
      never make an extra grant look legitimate.
    - ``sys.setswitchinterval`` is dropped to 10 microseconds for the
      duration of this test only (restored in ``finally``, never leaked
      into any other test in this process): CPython's GIL only forces a
      thread switch roughly every ``sys.getswitchinterval()`` seconds
      (default 5ms) absent I/O, and this bucket's whole read-modify-write
      critical section normally completes in a few microseconds --
      comfortably faster than the default interval. Verified empirically
      (this task's own report) that a version of this exact test using
      the DEFAULT switch interval sees zero lost-update violations even
      with the lock removed entirely: the GIL happens to serialize the
      short critical section anyway on this interpreter, which would make
      that version of the test pass regardless of whether the lock
      exists. Forcing far more frequent preemption is what makes this
      version an honest, sensitive proof of the lock's necessity --
      independently confirmed (same report) to reliably violate
      "exactly one grant" in most rounds when the real lock is removed,
      and to stay at zero violations across 100+ rounds with it in place.
    """
    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(0.00001)
    try:
        limiter = auth_module.ChatRateLimiter(per_minute=1)
        thread_count = 32
        for round_num in range(12):
            key = f"concurrent-key-{round_num}"
            granted = _run_one_concurrent_round(limiter, key, thread_count)
            assert granted == 1, f"round {round_num}: expected exactly 1 grant, got {granted}"
    finally:
        sys.setswitchinterval(old_interval)


def _run_one_concurrent_round(
    limiter: "auth_module.ChatRateLimiter", key: str, thread_count: int
) -> int:
    """One round of ``test_chat_rate_limiter_check_is_thread_safe_under_
    concurrent_callers``: ``thread_count`` threads all call ``limiter.
    check(key)`` as close to simultaneously as a ``threading.Barrier`` can
    arrange, returning how many were granted. A separate function (not a
    closure built fresh inside that test's own ``for`` loop) so ``worker``
    below closes over THIS call's fixed `barrier`/`results`/`key` locals,
    never a loop variable that could be reassigned out from under it
    (ruff B023) -- correctness here does not depend on that distinction
    (each round's threads are fully joined before the next round would
    reassign anything), but the separate-function shape makes that true
    by construction rather than by careful sequencing a reader has to
    verify.
    """
    barrier = threading.Barrier(thread_count)
    results: list[float | None] = [None] * thread_count

    def worker(index: int) -> None:
        barrier.wait()
        results[index] = limiter.check(key)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(thread_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return sum(1 for r in results if r is None)


# ===========================================================================
# CORS allowlist: preflight round trip for an allowed vs. a disallowed
# origin (Global Constraints)
# ===========================================================================


@pytest.mark.anyio
async def test_cors_preflight_allows_the_configured_origin():
    app = _mock_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.options(
            "/api/me",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert r.headers["access-control-allow-credentials"] == "true"


@pytest.mark.anyio
async def test_cors_preflight_rejects_a_disallowed_origin():
    app = _mock_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.options(
            "/api/me",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert r.status_code == 400
    assert "access-control-allow-origin" not in r.headers


# ===========================================================================
# SPCS ingress mode at the HTTP boundary: boot fail-fast through the real
# create_app, GET /api/me, /health/* staying open, and the require_sales
# 403 for a non-allowlisted user (Phase 9 Task 3). Provider-level cases
# (allowlist membership, casefold, malformed-header-401) are already
# covered by test_identity_providers.py -- only what changes AT the HTTP
# boundary is re-proven here, mirroring the Auth0 section's own split
# above. Zero injectable transport needed this time (unlike _auth0_app):
# SpcsIngressProvider does no I/O of any kind.
# ===========================================================================


def _spcs_app(**overrides):
    from poseidon.api.app import create_app

    defaults: dict = dict(identity_mode="spcs_ingress", deploy_mode="spcs", spcs_sales_users="*")
    defaults.update(overrides)
    return create_app(_settings(**defaults))


def test_create_app_fails_fast_for_spcs_ingress_outside_spcs_deploy_mode():
    """The same boot-time RuntimeError test_identity_providers.py proves
    directly against resolve_provider/SpcsIngressProvider, exercised here
    through the real api/app.py wiring: create_app calls resolve_provider
    at the very top of app construction, before any router is mounted or
    any middleware installed -- proof the fail-fast genuinely blocks the
    app from ever booting, not merely a provider-level contract nothing
    else actually calls that way."""
    from poseidon.api.app import create_app

    with pytest.raises(RuntimeError, match="deploy_mode='spcs'"):
        create_app(
            _settings(identity_mode="spcs_ingress", deploy_mode="local", spcs_sales_users="*")
        )


@pytest.mark.anyio
async def test_me_under_spcs_ingress_mode_with_a_valid_header_returns_the_claims():
    app = _spcs_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"Sf-Context-Current-User": "alice"})

    assert r.status_code == 200
    assert r.json() == {
        "sub": "sf|alice",
        "name": None,
        "email": None,
        "roles": ["Poseidon:Sales"],
        "identity_mode": "spcs_ingress",
    }


@pytest.mark.anyio
async def test_me_under_spcs_ingress_mode_with_no_header_is_401():
    app = _spcs_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me")

    assert r.status_code == 401
    assert r.json() == {
        "type": "about:blank",
        "title": "missing spcs identity header",
        "detail": "no valid Sf-Context-Current-User header",
        "status": 401,
    }


@pytest.mark.anyio
async def test_health_live_stays_open_under_spcs_ingress_mode_with_no_header():
    """The identity middleware still runs for /health/* (Task 1's own
    "cheap... paying this on every request" discipline, unchanged) and
    records the AuthError -- but /health/live never depends on
    current_user, so it is never asked, and the request succeeds."""
    app = _spcs_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/health/live")

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_api_skills_403_for_a_non_allowlisted_spcs_user():
    """Global Constraints: "non-member -> authenticated UserContext
    WITHOUT the role" -- proven end to end: require_sales (api/auth.py),
    not SpcsIngressProvider itself, is what turns this into a 403."""
    app = _spcs_app(chat_mode="live", spcs_sales_users="someone-else")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/skills", headers={"Sf-Context-Current-User": "alice"})

    assert r.status_code == 403
    assert r.json() == {
        "type": "about:blank",
        "title": "insufficient role",
        "detail": "caller lacks required role 'Poseidon:Sales'",
        "status": 403,
    }


@pytest.mark.anyio
async def test_api_skills_200_for_an_allowlisted_spcs_user():
    app = _spcs_app(chat_mode="live", spcs_sales_users="alice")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/skills", headers={"Sf-Context-Current-User": "alice"})

    assert r.status_code == 200


# ===========================================================================
# Phase 9 final-review fix wave (2026-07-31): I-1 (provider-failure
# containment), I-2 (mock-router guard), I-3 (route-classification sweep),
# I-4 (duplicate identity-header rejection), M-6 (rate-limit-chat-send
# no-op). See .superpowers/sdd/2026-07-31-phase-9-identity/final-review.md.
# ===========================================================================

# I-1: a provider failure that is NOT a pinned AuthError must never surface
# as a bare 500 -- on /api/me it becomes the SAME 401-family RFC-7807 body
# regardless of which underlying exception caused it (the generic,
# byte-pinned "identity_unavailable" problem); on /health/live it must not
# even be visible, since that route never asks for identity at all. Four
# cases per the review's own table, each proven at both endpoints.
#
# raise_app_exceptions=False (matching the review's own verification
# methodology) so a REGRESSION of this fix shows up as a clean assertion
# failure ("500 != 401") rather than an opaque pytest ERROR with a raw
# traceback -- harmless today, since post-fix no exception ever reaches the
# transport layer either way.


@pytest.mark.anyio
async def test_me_returns_generic_401_when_jwks_is_unreachable():
    key1, _pub1 = generate_rsa_keypair()
    app = _auth0_app(UnreachableJwksTransport())
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 401
    assert r.json() == {
        "type": "about:blank",
        "title": "identity_unavailable",
        "detail": "the identity provider failed unexpectedly; try again shortly",
        "status": 401,
    }


@pytest.mark.anyio
async def test_health_live_stays_open_when_jwks_is_unreachable():
    key1, _pub1 = generate_rsa_keypair()
    app = _auth0_app(UnreachableJwksTransport())
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/health/live", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_me_returns_generic_401_when_jwks_returns_a_5xx():
    key1, _pub1 = generate_rsa_keypair()
    app = _auth0_app(FailingJwksTransport(500))
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 401
    assert r.json() == {
        "type": "about:blank",
        "title": "identity_unavailable",
        "detail": "the identity provider failed unexpectedly; try again shortly",
        "status": 401,
    }


@pytest.mark.anyio
async def test_health_live_stays_open_when_jwks_returns_a_5xx():
    key1, _pub1 = generate_rsa_keypair()
    app = _auth0_app(FailingJwksTransport(500))
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/health/live", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_me_is_401_when_a_valid_signature_token_has_no_sub_claim():
    """The ONE exception to the generic "identity_unavailable" body among
    these four cases: Auth0Provider.resolve now reads `sub` defensively
    and raises its OWN pinned AuthError ("invalid token") directly, rather
    than ever reaching the generic containment path -- a root-cause fix,
    not containment. Uses mint_auth0_token_without_sub_claim (NOT
    mint_auth0_token(..., sub=None): PyJWT itself already rejects a
    PRESENT-but-non-string sub before this fix's own check ever runs, so
    that shortcut would silently test the wrong thing -- see that
    helper's own docstring in test_identity_providers.py)."""
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token_without_sub_claim(key1, "key-1")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 401
    assert r.json()["title"] == "invalid token"


@pytest.mark.anyio
async def test_health_live_stays_open_when_a_valid_signature_token_has_no_sub_claim():
    key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token_without_sub_claim(key1, "key-1")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/health/live", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_me_returns_generic_401_when_a_non_rsa_jwk_collides_with_the_requested_kid():
    """M-10: _public_key_for_kid hands ANY kid-matching JWK to
    RSAAlgorithm.from_jwk without checking kty/use/alg first -- a non-RSA
    key sharing a kid with the token's own header raises
    jwt.exceptions.InvalidKeyError, uncaught by Auth0Provider itself (see
    identity_auth0.py's own resolve() docstring: this is a transport/config
    fault the pinned failure matrix does not name, not a credential problem
    this provider classifies). Contained by the SAME generic I-1 middleware
    path as JWKS-unreachable/5xx above."""
    key1, _pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([oct_jwk_for("key-1")]))
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 401
    assert r.json() == {
        "type": "about:blank",
        "title": "identity_unavailable",
        "detail": "the identity provider failed unexpectedly; try again shortly",
        "status": 401,
    }


@pytest.mark.anyio
async def test_health_live_stays_open_when_a_non_rsa_jwk_collides_with_the_requested_kid():
    key1, _pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([oct_jwk_for("key-1")]))
    token = mint_auth0_token(key1, "key-1")
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/health/live", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# I-2: chat_mode defaults to "mock" REGARDLESS of identity_mode -- an
# auth0 deploy that forgets CHAT_MODE=live must not leave mock_chat.
# router's six routes open.


@pytest.mark.anyio
async def test_conversations_post_requires_a_token_under_auth0_mode_with_default_mock_chat():
    """chat_mode is left at its "mock" default here -- unlike every other
    Auth0-section test above, which passes chat_mode="live" to reach
    live_chat.py instead."""
    _key1, pub1 = generate_rsa_keypair()
    app = _auth0_app(JwksTransport([jwk_for(pub1, "key-1")]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations")

    assert r.status_code == 401


# I-4: a dict comprehension over Headers.items() keeps only the LAST
# occurrence of a repeated header name -- verified (this task's own wave
# report) that a caller who appends a SECOND Sf-Context-Current-User header
# can override whichever identity the SPCS platform edge actually attached,
# entirely undetected, as long as their value sorts last. Rejected outright
# now, before any provider ever sees a collapsed mapping.


@pytest.mark.anyio
async def test_duplicate_sf_context_current_user_headers_is_401_not_last_wins():
    app = _spcs_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get(
            "/api/me",
            headers=[
                ("Sf-Context-Current-User", "platform_alice"),
                ("Sf-Context-Current-User", "forged_mallory"),
            ],
        )

    assert r.status_code == 401
    assert r.json()["title"] == "duplicate identity header"


@pytest.mark.anyio
async def test_health_live_stays_open_with_duplicate_identity_headers():
    app = _spcs_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get(
            "/health/live",
            headers=[
                ("Sf-Context-Current-User", "platform_alice"),
                ("Sf-Context-Current-User", "forged_mallory"),
            ],
        )

    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_duplicate_authorization_headers_is_401():
    """Disclosed judgment call (this task's own wave report): the review's
    I-4 finding is specifically about Sf-Context-Current-User's platform-
    trust assumption, but the identical last-one-wins collapse applies to
    Authorization too -- no legitimate client ever sends two, so this task
    applies the same rejection to both headers in the ONE adapter rather
    than special-casing just one. The values need not be well-formed
    tokens: rejection happens before either is ever inspected."""
    app = _auth0_app(JwksTransport([]))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get(
            "/api/me",
            headers=[("Authorization", "Bearer aaa"), ("Authorization", "Bearer bbb")],
        )

    assert r.status_code == 401
    assert r.json()["title"] == "duplicate identity header"


# M-6: app.state.chat_rate_limiter is only ever SET by _wire_live_chat
# (chat_mode="live") -- a mock-mode app's app.state never gets the
# attribute at all (not even None).


def test_rate_limit_chat_send_no_ops_when_the_limiter_attribute_is_entirely_unset():
    """A bare attribute access here would raise AttributeError instead of
    no-opping the moment ANY mock-mode-reachable route ever attaches this
    dependency -- proven directly against a real mock-mode app.state, no
    live wiring involved."""
    app = _mock_app()
    assert not hasattr(app.state, "chat_rate_limiter")
    request = Request(scope={"type": "http", "headers": [], "app": app})

    auth_module.rate_limit_chat_send(request)  # must not raise


# ---------------------------------------------------------------------------
# I-3: the route-classification sweep. Permanent replacement for a
# hand-maintained guard list's own blind spot (see
# test_all_six_conversation_and_message_routes_require_a_token_under_auth0_
# mode above, which names six routes explicitly and would stay green
# whether or not a SEVENTH one ever lost its guard -- exactly what happened
# once already, Controller's Round 0 correction, cf401b1). This walks the
# app itself and demands every /api/* route account for its own guard,
# rather than trusting a list a future PR has to remember to update.
# ---------------------------------------------------------------------------


def _iter_api_route_contexts(app):
    """Yield one _EffectiveRouteContext per route this app actually serves,
    across every mounted router, regardless of how deeply include_router
    nested it. Verified by direct introspection (this task's own wave
    report) against the installed FastAPI 0.140.13: include_router wraps
    each included APIRouter in a private fastapi.routing._IncludedRouter
    rather than flattening its routes onto app.routes directly, so a naive
    `for route in app.routes` sees FOUR routes total (openapi.json/docs/
    docs-oauth2-redirect/redoc -- FastAPI's own built-ins) and silently
    misses every single /api/* or /health/* route this app serves, which
    would make a sweep like this one vacuously pass no matter what it was
    supposed to catch. Confirmed the review's own named mechanism,
    effective_candidates()/effective_route_contexts(), is real on this
    install (fastapi/routing.py's own _IncludedRouter class) and is what
    actually surfaces every route recursively, however many routers deep."""
    from fastapi.routing import _IncludedRouter

    for route in app.routes:
        if isinstance(route, _IncludedRouter):
            yield from route.effective_route_contexts()


def _dependant_calls(dependant) -> set:
    """Flattens one route's complete dependency graph -- the endpoint
    itself plus EVERY dependency FastAPI will actually resolve to serve it,
    recursively -- regardless of whether a guard was attached via
    include_router(..., dependencies=[...]) (dev_runner.router,
    mock_chat.router as of I-2), a route's own dependencies=[...] kwarg
    (live_chat.py), or a nested Depends() inside another dependency's own
    signature (require_sales's own Depends(current_user)): all three
    shapes land in this SAME nested structure at request-resolution time,
    so walking it is a direct check of what FastAPI will really invoke, not
    a parallel guess at it."""
    calls: set = set()
    if dependant is None:
        return calls
    call = getattr(dependant, "call", None)
    if call is not None:
        calls.add(call)
    for sub in getattr(dependant, "dependencies", None) or []:
        calls |= _dependant_calls(sub)
    return calls


@pytest.mark.parametrize(
    "build_app",
    [lambda: _mock_app(deploy_mode="local"), lambda: _live_app(deploy_mode="local")],
    ids=["chat_mode=mock", "chat_mode=live"],
)
def test_every_api_route_is_either_api_me_or_guarded_by_require_sales(build_app):
    """I-3. deploy_mode="local" in BOTH cases so dev_runner.router is
    mounted alongside whichever chat router the case is actually about --
    between the two parametrized cases, every router this codebase mounts
    (health, auth, mock_chat OR live_chat, dev_runner) gets swept at least
    once. The one open-by-design bucket is GET /api/me; identity_mode stays
    "disabled" for every route otherwise (irrelevant here: this test is
    entirely about STRUCTURE -- which dependency graph a route carries --
    never about a mode's actual runtime behavior)."""
    app = build_app()
    unclassified = []
    for ctx in _iter_api_route_contexts(app):
        if not ctx.path.startswith("/api/"):
            continue
        if ctx.path == "/api/me":
            continue
        if auth_module.require_sales in _dependant_calls(ctx.dependant):
            continue
        unclassified.append(f"{sorted(ctx.methods or [])} {ctx.path}")

    assert not unclassified, (
        "route(s) under /api/* are neither GET /api/me nor guarded by "
        f"require_sales: {unclassified}"
    )


# ===========================================================================
# Phase 14 Task 1 (the phase 9 final review's I-5 carryforward): the
# identity middleware resolves OFF the event loop thread.
#
# Every provider's resolve() is a SYNC method (core/identity.py's own
# protocol -- providers never import FastAPI, and never became async), and
# Auth0Provider's own can perform blocking httpx I/O against the tenant's
# JWKS endpoint. Awaited inline from an async middleware, that call stalls
# the ONE event loop thread this process serves every route from -- and it
# is reachable pre-auth, by an uncredentialed caller, on every request.
# The provider-side half of the fix (bounding those fetches) is proven in
# test_identity_providers.py; this is the other half.
# ===========================================================================


class _ThreadRecordingProvider:
    """Records the thread each ``resolve`` call actually runs on.

    A hand-written double rather than a monkeypatched real provider: the
    ``IdentityProvider`` protocol is structural (one ``resolve(headers)``
    method, no base class to inherit -- see ``core/identity.py``), so the
    smallest honest stand-in is a plain object with that one method, and
    the question under test ("which thread ran it") is the same for every
    mode. Returns the same fixed identity ``DisabledProvider`` would, so
    the request it serves still completes normally and the assertion below
    can also prove the RETURN value crossed back out of the worker thread.
    """

    def __init__(self) -> None:
        self.resolve_threads: list[int] = []

    def resolve(self, headers):
        self.resolve_threads.append(threading.get_ident())
        return DISABLED_DEFAULT_USER


@pytest.mark.anyio
async def test_identity_resolve_runs_off_the_event_loop_thread():
    """The middleware must hand ``provider.resolve`` to a worker thread.

    ``loop_thread`` is read in the test coroutine's own body, which IS the
    event loop thread (``httpx.ASGITransport`` drives the app in-process,
    on this same loop -- no server, no second thread of its own), so a
    recorded ident that differs from it can only mean the call genuinely
    crossed ``anyio.to_thread``. Asserting inequality rather than a
    specific ident is the point: which pooled worker served it is an
    implementation detail of anyio's own thread pool, "not the loop
    thread" is the contract.

    The 200 and the echoed sub are not incidental -- they prove the hop is
    transparent in both directions: headers in, the resolved UserContext
    back out to ``request.state.user``, with no behavior change to the
    route that reads it.
    """
    provider = _ThreadRecordingProvider()
    app = _mock_app()
    app.state.identity_provider = provider
    loop_thread = threading.get_ident()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/me")

    assert r.status_code == 200
    assert r.json()["sub"] == "dev|local"
    assert len(provider.resolve_threads) == 1
    assert provider.resolve_threads[0] != loop_thread


# ===========================================================================
# ASCII-only source
# ===========================================================================


def test_api_auth_module_files_are_ascii_on_disk():
    """``auth.py`` and this test file are wholly this task's own -- checked
    in full. ``app.py`` is DELIBERATELY excluded from a whole-file scan: it
    predates the ASCII convention (two pre-existing em-dash lines in the
    ``deploy_mode == "local"`` block's comment, verified byte-for-byte
    unrelated to and untouched by this task's own additions -- rewriting
    pre-existing prose this task did not author is out of its sanctioned
    edit scope), the same call ``test_chat_orchestrator.py``'s own suite
    already makes for ``context.py``'s ten pre-existing non-ASCII lines.
    Every line this task actually added to ``app.py`` was verified ASCII
    via ``git diff`` (this task's own report records the check)."""
    for path in (Path(auth_module.__file__), Path(__file__)):
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
