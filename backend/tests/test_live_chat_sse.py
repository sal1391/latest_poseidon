"""Tests for Phase 6 Task 4: the live chat HTTP surface (``api/live_chat.py``)
mounted behind ``CHAT_MODE=live``, and the app-factory mount switch itself
(``poseidon/api/app.py``).

Everything here is OFFLINE, mirroring ``test_chat_orchestrator.py``'s own
discipline: a real ``create_app`` (never both routers mounted -- see
``test_default_settings_app_mounts_mock_not_live_chat`` /
``test_live_settings_app_mounts_live_chat_not_mock``), then, for the turn-
streaming tests, ``app.state.data_client``/``app.state.run_log_writer``
swapped for a fake data client and a writer double AFTER construction --
the same post-construction substitution ``test_dev_runner.py``'s own
``test_artifact_refs_serialize_to_the_frontend_wire_shape`` uses for
``app.state.skill_registry``. ``FakeDataClient``/``RecordingWriter`` are
imported straight from ``test_chat_orchestrator.py`` (the same cross-test-
module reuse ``test_dev_runner.py`` already does for its own throwaway-
package fixture) rather than re-derived: the flagship scenario below is
the IDENTICAL scripted turn that suite already pins frame-by-frame, so
reusing its fixtures is what proves the HTTP layer reproduces the exact
same numbers the orchestrator-level suite already established, not a
coincidentally-similar-looking copy.

SSE frames are parsed with the same discipline ``test_mock_chat.py``'s own
``read_sse`` helper uses (httpx ASGI transport, ``client.stream(...)``,
splitting ``event: ``/``data: `` lines) -- the wire format is pinned
byte-for-byte identical to the mock's own ``_sse()`` (see ``events.py``'s
module docstring), so the same parsing works unchanged against either.
"""

import json
import logging

import httpx
import pytest

from poseidon.core.config import Settings
from poseidon.core.skills.registry import SkillRegistry
from tests.test_chat_orchestrator import REGISTRY, FakeDataClient, RecordingWriter

# U+2014 EM DASH, written as an escape (not a typed literal) -- the same
# convention every earlier Phase 4/5/6 suite uses (see test_chat_orchestrator.
# py's own module docstring): an em dash, an en dash and a hyphen are visually
# indistinguishable in most editors, and this file must stay pure ASCII on
# disk regardless (house rule: backend .py files are ASCII-only).
_EM_DASH = chr(0x2014)

_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

_METRIC_QUERY_DESCRIPTION = REGISTRY.get("data_qa.metric_query").description


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


def _mock_app():
    from poseidon.api.app import create_app

    return create_app(_settings())


def _live_app(*, data_client=None, writer=None):
    """A ``chat_mode="live"`` app, with ``app.state.data_client``/
    ``app.state.run_log_writer`` swapped for test doubles when given -- the
    same post-construction substitution ``test_dev_runner.py`` already uses
    for ``app.state.skill_registry``."""
    from poseidon.api.app import create_app

    app = create_app(_settings(chat_mode="live"))
    if data_client is not None:
        app.state.data_client = data_client
    if writer is not None:
        app.state.run_log_writer = writer
    return app


async def read_sse(client: httpx.AsyncClient, cid: str, text: str, client_turn_key: str | None):
    """Mirrors ``test_mock_chat.py``'s own ``read_sse`` helper exactly --
    the wire format is pinned byte-identical (``events.py``'s module
    docstring), so the same parsing logic applies unchanged."""
    events = []
    body = {"text": text}
    if client_turn_key is not None:
        body["client_turn_key"] = client_turn_key
    async with client.stream("POST", f"/api/conversations/{cid}/messages", json=body) as response:
        assert response.status_code == 200
        name = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: ") :])))
    return events


# ===========================================================================
# App-factory mount switch: mock and live are never mounted together
# ===========================================================================


def test_default_settings_app_mounts_mock_not_live_chat():
    """``chat_mode`` defaults to "mock" -- NOTHING changes for an existing
    env: the app exposes mock_chat's own routes and none of live_chat's."""
    app = _mock_app()
    paths = app.openapi()["paths"]
    assert "/api/conversations" in paths
    assert "/api/skills" not in paths


def test_live_mode_app_mounts_live_chat_not_mock():
    app = _live_app()
    paths = app.openapi()["paths"]
    assert "/api/skills" in paths
    assert "/api/conversations/{cid}/messages" in paths
    # mock_chat's OTHER routes (create/list conversations, feedback) have no
    # live equivalent in this task's pinned scope -- see live_chat.py's own
    # module docstring for the disclosed gap.
    assert "/api/conversations" not in paths


def test_live_mode_app_wires_the_expected_app_state():
    app = _live_app()
    assert hasattr(app.state, "skill_registry")
    assert "data_qa.metric_query" in app.state.skill_registry.skill_ids
    assert hasattr(app.state, "conversation_state_store")
    assert hasattr(app.state, "role_client")
    assert hasattr(app.state, "prompt_registry")
    assert hasattr(app.state, "data_client")
    # DATABASE_URL is always syntactically valid here (a placeholder host,
    # never a malformed DSN), so the writer is a real, constructed one --
    # see test_run_log_writer_is_none_when_the_engine_cannot_be_built below
    # for the disclosed else-branch.
    assert app.state.run_log_writer is not None


def test_live_mode_local_deploy_discovers_the_skill_registry_exactly_once(monkeypatch):
    """Fix round 1, MINOR M1: ``chat_mode="live"`` + ``deploy_mode="local"``
    (the DEFAULT ``deploy_mode`` -- every ``_live_app()`` call in this file
    already exercises this exact combination) used to call ``SkillRegistry.
    discover()`` TWICE at boot -- once in ``_wire_live_chat``, once again in
    the local-mode dev-runner block -- building two independent,
    structurally-identical registries and silently discarding the first.
    One boot of a local live app is one discovery walk."""
    calls: list[object] = []
    original_discover = SkillRegistry.discover  # already bound to SkillRegistry

    def counting_discover(*args, **kwargs):
        calls.append(None)
        return original_discover(*args, **kwargs)

    monkeypatch.setattr(SkillRegistry, "discover", counting_discover)

    _live_app()

    assert len(calls) == 1


def test_run_log_writer_is_none_when_the_engine_cannot_be_built(capsys):
    """``create_engine`` -- unlike ``SyntheticDataClient.__init__`` -- DOES
    eagerly parse its URL argument, so a value ``Settings.database_url``'s
    own "not blank" validator accepts but which is not a URL SQLAlchemy can
    parse at all is the one realistic way this branch is reached (see
    ``app.py``'s own ``_build_run_log_writer`` docstring)."""
    app = _live_app_with_database_url("not-a-url-at-all")
    assert app.state.run_log_writer is None
    assert "WARNING" in capsys.readouterr().out


def _live_app_with_database_url(database_url: str):
    from poseidon.api.app import create_app

    return create_app(_settings(chat_mode="live", database_url=database_url))


# ===========================================================================
# POST /api/conversations/{cid}/messages -- the flagship scripted turn,
# reproduced byte-for-byte through the real HTTP surface
# ===========================================================================


@pytest.mark.anyio
async def test_live_turn_streams_the_flagship_frame_sequence_and_table_and_proof_parts():
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        events = await read_sse(
            client, "conv-1", "Top GP customers for Port of Singapore in April 2026", "ctk-1"
        )

    names = [name for name, _data in events]
    assert names == ["accepted", "tool", "tool", "part", "part", "token", "done"]

    payloads = [data for _name, data in events]
    # envelope on every frame: turn_id/message_id/event_seq, one turn_id,
    # strictly increasing event_seq starting at 1 (doc 01 section 5).
    assert all({"turn_id", "message_id", "event_seq"} <= set(p) for p in payloads)
    turn_ids = {p["turn_id"] for p in payloads}
    assert len(turn_ids) == 1
    turn_id = turn_ids.pop()
    seqs = [p["event_seq"] for p in payloads]
    assert seqs == list(range(1, len(payloads) + 1))

    table_payload = payloads[3]
    assert table_payload["kind"] == "table"
    assert table_payload["payload"] == {
        "columns": ["Customer", "Gross Profit"],
        "rows": [
            ["Northstar Lines", 412000],
            ["Blue Anchor Marine", 268500],
            ["Crestline Freight", 155250],
        ],
    }

    proof_payload = payloads[4]
    assert proof_payload["kind"] == "proof"
    assert proof_payload["payload"]["lines"][0] == "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V"
    assert "Rows: 3" in proof_payload["payload"]["lines"]

    token_payload = payloads[5]
    assert token_payload["text"].startswith("Certified answer for SINGAPORE")

    # Run-log turn-id unification, proven at the HTTP layer: the turn_id on
    # every SSE frame IS the turn_run_id the writer double actually received.
    assert len(writer.start_turn_calls) == 1
    assert writer.start_turn_calls[0]["turn_run_id"] == turn_id
    assert len(writer.finalize_calls) == 1
    assert writer.finalize_calls[0]["turn_run_id"] == turn_id


@pytest.mark.anyio
async def test_client_turn_key_retry_gets_pinned_duplicate_turn_error_and_no_second_dispatch():
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        first = await read_sse(
            client, "conv-retry", "Top GP customers for Port of Singapore in April 2026", "ctk-1"
        )
        second = await read_sse(
            client, "conv-retry", "Top GP customers for Port of Singapore in April 2026", "ctk-1"
        )

    first_names = [name for name, _data in first]
    assert first_names == ["accepted", "tool", "tool", "part", "part", "token", "done"]

    names = [name for name, _data in second]
    assert names == ["accepted", "error"]
    error_data = second[1][1]
    assert error_data["code"] == "duplicate_turn"
    assert error_data["message"] == (
        "this turn was already processed " + _EM_DASH + " refresh to load the conversation"
    )

    # No re-dispatch: exactly one turn's worth of tool/finalize rows exist,
    # even though the client sent the request twice.
    assert len(writer.append_tool_calls) == 1
    assert len(writer.finalize_calls) == 1
    # Both attempts were recorded by start_turn (the idempotent-insert path
    # is exercised both times), but only the first one created a row.
    assert len(writer.start_turn_calls) == 2


# ===========================================================================
# Fix round 1, REQUIRED F1 -- an unhandled exception mid-turn (the realistic
# case: a Bedrock network hiccup once LLM_MODE=live) must still end the
# stream cleanly with ONE pinned error frame, finalize the run-log row as
# failed, and leave the app healthy for the next request.
# ===========================================================================


def _crashing_execute_turn(**kwargs):
    """Stands in for the real ``execute_turn``: emits a partial turn (an
    ``accepted`` frame and one token, exactly like a real turn that got as
    far as streaming some of its answer) then raises, unhandled -- the
    realistic shape of a mid-turn provider failure (a network hiccup calling
    Bedrock), not a failure at the very first line."""
    sink = kwargs["sink"]
    sink.accepted(1)
    sink.push_token("partial reply")
    raise RuntimeError("simulated bedrock network hiccup")


@pytest.mark.anyio
async def test_unhandled_exception_mid_turn_emits_pinned_internal_error_and_ends_stream_cleanly(
    monkeypatch, caplog
):
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer)
    monkeypatch.setattr("poseidon.api.live_chat.execute_turn", _crashing_execute_turn)

    transport = httpx.ASGITransport(app=app)
    with caplog.at_level(logging.ERROR, logger="poseidon.api.live_chat"):
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            events = await read_sse(client, "conv-crash", "hello", None)

    # The client sees the partial turn, then ONE pinned error frame, then a
    # clean stream end -- never a raw protocol error (no more frames, no
    # exception raised out of read_sse/the ASGI transport).
    names = [name for name, _data in events]
    assert names == ["accepted", "token", "error"]
    turn_id = events[0][1]["turn_id"]
    error_data = events[2][1]
    assert error_data["code"] == "internal_error"
    assert error_data["message"] == (
        "the turn failed unexpectedly " + _EM_DASH + " the error has been logged"
    )

    # The run-log row is finalized as failed exactly once, keyed by the SAME
    # turn_id every frame carried (the turn-id unification amendment applies
    # on the crash path too), never left orphaned at status='running'.
    assert len(writer.finalize_calls) == 1
    finalize = writer.finalize_calls[0]
    assert finalize["turn_run_id"] == turn_id
    assert finalize["status"] == "error"
    assert finalize["message_id"] is None
    assert finalize["answer_summary"] is None
    assert finalize["input_tokens"] == 0
    assert finalize["output_tokens"] == 0
    assert isinstance(finalize["latency_ms"], int)
    assert finalize["latency_ms"] >= 0
    assert finalize["error"]["title"] == "internal_error"
    assert "RuntimeError" in finalize["error"]["detail"]
    assert "simulated bedrock network hiccup" in finalize["error"]["detail"]

    # Logged at ERROR, once, naming the exception type.
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "RuntimeError" in errors[0].message

    # A follow-up request on the SAME app (real execute_turn restored)
    # succeeds -- the crash left nothing wedged.
    monkeypatch.undo()
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        follow_up = await read_sse(
            client, "conv-crash-2", "Top GP customers for Port of Singapore in April 2026", None
        )
    assert [name for name, _data in follow_up] == [
        "accepted",
        "tool",
        "tool",
        "part",
        "part",
        "token",
        "done",
    ]


@pytest.mark.anyio
async def test_unhandled_exception_with_no_writer_still_ends_stream_cleanly(monkeypatch):
    """``writer=None`` (no DATABASE_URL) must not change the crash-handling
    shape -- only the run-log gains no finalize call, mirroring execute_turn's
    own `writer is not None` guard convention throughout."""
    app = _live_app(data_client=FakeDataClient())
    app.state.run_log_writer = None
    monkeypatch.setattr("poseidon.api.live_chat.execute_turn", _crashing_execute_turn)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        events = await read_sse(client, "conv-crash-no-writer", "hello", None)

    assert [name for name, _data in events] == ["accepted", "token", "error"]


# ===========================================================================
# GET /api/skills -- registry-backed [{id, label, description}]
# ===========================================================================


@pytest.mark.anyio
async def test_get_skills_returns_registry_backed_shape():
    app = _live_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/skills")

    assert r.status_code == 200
    body = r.json()
    assert body == [
        {
            "id": "data_qa.metric_query",
            "label": "Metric query",
            "description": _METRIC_QUERY_DESCRIPTION,
        }
    ]
