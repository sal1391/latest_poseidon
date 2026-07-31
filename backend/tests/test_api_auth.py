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
"""

from pathlib import Path

import httpx
import pytest
from fastapi import Request

from poseidon.api import auth as auth_module
from poseidon.api.auth import current_user
from poseidon.core.config import Settings
from tests.test_chat_orchestrator import FakeDataClient, RecordingWriter

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
