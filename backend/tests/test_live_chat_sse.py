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

import httpx
import pytest

from poseidon.core.config import Settings
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
