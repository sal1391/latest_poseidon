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
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import Request
from pydantic import ValidationError

from poseidon.api import auth as auth_module
from poseidon.api.auth import current_user
from poseidon.core.config import Settings
from poseidon.core.identity_auth0 import ROLES_CLAIM, Auth0Provider
from tests.test_chat_orchestrator import FakeDataClient, RecordingWriter
from tests.test_identity_providers import (
    AUTH0_TEST_AUDIENCE,
    AUTH0_TEST_DOMAIN,
    JwksTransport,
    generate_rsa_keypair,
    jwk_for,
    mint_auth0_token,
)

_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

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


@pytest.mark.anyio
async def test_act_as_header_flows_through_to_every_run_log_writer_call():
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await _send_turn(client, "conv-actas", FLAGSHIP_TEXT, headers={"X-Dev-User": "alice"})

    assert len(writer.start_turn_calls) == 1
    assert writer.start_turn_calls[0]["user_sub"] == "dev|alice"
    assert len(writer.append_llm_calls) == 2
    assert all(row["user_sub"] == "dev|alice" for row in writer.append_llm_calls)
    assert len(writer.append_tool_calls) == 1
    assert writer.append_tool_calls[0]["user_sub"] == "dev|alice"


@pytest.mark.anyio
async def test_no_act_as_header_still_uses_the_pinned_disabled_mode_default():
    """The pg-stability pin, proven here at the HTTP layer too (see this
    task's own report for the pg re-run): user_sub flows must not shift for
    the common, no-header case -- the SAME "dev|local" every pre-Phase-9
    run-log row already carries."""
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await _send_turn(client, "conv-default", FLAGSHIP_TEXT)

    assert writer.start_turn_calls[0]["user_sub"] == "dev|local"


@pytest.mark.anyio
async def test_two_act_as_identities_in_the_same_process_get_independent_user_subs():
    """Multi-user local testing, the whole point of the act-as header
    (Global Constraints) -- two different X-Dev-User values against the
    SAME running app/registry produce two independent user_subs, never
    conflated with each other or with the fixed default."""
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await _send_turn(client, "conv-alice", FLAGSHIP_TEXT, headers={"X-Dev-User": "alice"})
        await _send_turn(client, "conv-bob", FLAGSHIP_TEXT, headers={"X-Dev-User": "bob"})

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
# The require-user + require-role guard: /api/skills, /api/dev/* (Global
# Constraints' enforcement scope -- /api/conversations*/messages* are
# deferred to Phase 10, per this task's own brief)
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


@pytest.mark.anyio
async def test_chat_rate_limit_is_off_by_default_in_disabled_mode():
    """Global Constraints: "OFF in disabled mode by default so dev/tests
    are unaffected" -- proven directly: MORE than the nominal default of
    30 rapid sends, none blocked, with no explicit override."""
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        for i in range(35):
            await _send_turn(client, f"conv-rl-{i}", FLAGSHIP_TEXT)

    assert len(writer.start_turn_calls) == 35


@pytest.mark.anyio
async def test_chat_rate_limit_zero_means_off_even_when_explicit():
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer, rate_limit_chat_per_minute=0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        for i in range(10):
            await _send_turn(client, f"conv-rl0-{i}", FLAGSHIP_TEXT)

    assert len(writer.start_turn_calls) == 10


@pytest.mark.anyio
async def test_chat_rate_limit_blocks_once_the_bucket_is_empty():
    app = _live_app(
        data_client=FakeDataClient(), writer=RecordingWriter(), rate_limit_chat_per_minute=2
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await _send_turn(client, "conv-rl-a", FLAGSHIP_TEXT)
        await _send_turn(client, "conv-rl-b", FLAGSHIP_TEXT)
        r = await client.post("/api/conversations/conv-rl-c/messages", json={"text": FLAGSHIP_TEXT})

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


@pytest.mark.anyio
async def test_chat_rate_limit_keys_independently_per_act_as_sub():
    """Global Constraints: "keyed by sub" -- alice exhausting her own
    bucket must never affect bob's, proven the same way test_api_auth.py's
    own Task 1 section already proves act-as identities are independent."""
    app = _live_app(
        data_client=FakeDataClient(), writer=RecordingWriter(), rate_limit_chat_per_minute=1
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await _send_turn(client, "conv-rl-alice-1", FLAGSHIP_TEXT, headers={"X-Dev-User": "alice"})
        r_alice_second = await client.post(
            "/api/conversations/conv-rl-alice-2/messages",
            json={"text": FLAGSHIP_TEXT},
            headers={"X-Dev-User": "alice"},
        )
        r_bob_first = await client.post(
            "/api/conversations/conv-rl-bob-1/messages",
            json={"text": FLAGSHIP_TEXT},
            headers={"X-Dev-User": "bob"},
        )

    assert r_alice_second.status_code == 429
    assert r_bob_first.status_code == 200


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
