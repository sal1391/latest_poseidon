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
# Phase 7 Task 4: research.web_research joined the real registry as its
# second enabled skill -- see test_get_skills_returns_registry_backed_shape
# below.
_RESEARCH_DESCRIPTION = REGISTRY.get("research.web_research").description
# Phase 8 Task 4: both customer_insight brief skills join as the registry's
# third and fourth entries (customer_insight sorts before data_qa).
_EXISTING_CUSTOMER_BRIEF_DESCRIPTION = REGISTRY.get(
    "customer_insight.existing_customer_brief"
).description
_NEW_PROSPECT_BRIEF_DESCRIPTION = REGISTRY.get("customer_insight.new_prospect_brief").description


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


def _live_app(*, data_client=None, writer=None, **settings_overrides):
    """A ``chat_mode="live"`` app, with ``app.state.data_client``/
    ``app.state.run_log_writer`` swapped for test doubles when given -- the
    same post-construction substitution ``test_dev_runner.py`` already uses
    for ``app.state.skill_registry``. ``**settings_overrides`` forwards
    additional ``Settings`` fields (Phase 6 Task 5 amendment: e.g.
    ``data_backend="snowflake"`` for the guard tests below) -- every
    call site above this comment passes none, so their behavior is
    unchanged."""
    from poseidon.api.app import create_app

    app = create_app(_settings(chat_mode="live", **settings_overrides))
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
    # Task 5 amendment: live_chat.py now serves the SAME four bootstrap
    # paths mock_chat.py does (create/list conversations, transcript,
    # feedback) -- this used to assert the OPPOSITE, back when that gap was
    # still open (see live_chat.py's own module docstring, "Task 5
    # amendment: the live bootstrap routes"). The mutual exclusivity this
    # test's NAME promises is enforced by app.py's own if/else mount switch
    # (never both app.include_router calls run), not by the two modes
    # serving disjoint path SETS anymore -- proven instead by GET
    # /api/skills, which mock_chat.py never defines at all
    # (test_default_settings_app_mounts_mock_not_live_chat's own assertion,
    # the mirror-image direction).
    assert "/api/conversations" in paths
    assert "/api/messages/{mid}/feedback" in paths


def test_live_mode_app_wires_the_expected_app_state():
    app = _live_app()
    assert hasattr(app.state, "skill_registry")
    assert "data_qa.metric_query" in app.state.skill_registry.skill_ids
    assert hasattr(app.state, "conversation_state_store")
    assert hasattr(app.state, "role_client")
    assert hasattr(app.state, "prompt_registry")
    assert hasattr(app.state, "data_client")
    # Phase 7 Task 4.
    assert hasattr(app.state, "tool_registry")
    # DATABASE_URL is always syntactically valid here (a placeholder host,
    # never a malformed DSN), so the writer is a real, constructed one --
    # see test_run_log_writer_is_none_when_the_engine_cannot_be_built below
    # for the disclosed else-branch.
    assert app.state.run_log_writer is not None


def test_stub_llm_mode_installs_a_fixture_research_tool(capsys):
    """Phase 7 Task 4 (AMENDED post-Task-2): ``LLM_MODE=stub`` (``_settings
    ()``'s own default) installs a ``FixtureResearchTool`` override --
    never a live transport, no matter what ``PERPLEXITY_API_KEY`` happens
    to be set in the ambient environment (key PRESENCE is the wrong gate --
    see ``app.py``'s own ``_build_tool_registry`` docstring)."""
    app = _live_app()

    result = app.state.tool_registry.research.search(query="q", schema_name="web_research")

    assert result.transport == "fixture"
    assert "research transport: fixture (llm_mode=stub)" in capsys.readouterr().out


def test_live_llm_mode_resolves_the_configured_perplexity_transport(capsys):
    """The other half of the AMENDED gate: ``LLM_MODE=live`` leaves
    resolution to ``ToolServerRegistry`` itself, per
    ``TOOL_TRANSPORT_PERPLEXITY`` (default ``"direct"``) -- proven WITHOUT
    ever firing a live call: only the CONSTRUCTED adapter's type is
    checked, never ``.search()`` (``PerplexityDirectAdapter``'s own lazy-
    client contract already proves construction alone touches no network)."""
    from poseidon.mcp.perplexity.adapter import PerplexityDirectAdapter

    app = _live_app(llm_mode="live")

    assert isinstance(app.state.tool_registry.research, PerplexityDirectAdapter)
    assert "research transport: direct (llm_mode=live)" in capsys.readouterr().out


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
    """Phase 8 Task 4: both customer_insight brief skills join the registry
    (``registry.skill_ids``' own sorted-by-id order -- "customer_insight" <
    "data_qa" < "research", and within customer_insight,
    "existing_customer_brief" < "new_prospect_brief" -- so the two brief
    skills now come FIRST, ahead of metric_query/web_research)."""
    app = _live_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/skills")

    assert r.status_code == 200
    body = r.json()
    assert body == [
        {
            "id": "customer_insight.existing_customer_brief",
            "label": "Existing customer brief",
            "description": _EXISTING_CUSTOMER_BRIEF_DESCRIPTION,
        },
        {
            "id": "customer_insight.new_prospect_brief",
            "label": "New prospect brief",
            "description": _NEW_PROSPECT_BRIEF_DESCRIPTION,
        },
        {
            "id": "data_qa.metric_query",
            "label": "Metric query",
            "description": _METRIC_QUERY_DESCRIPTION,
        },
        {
            "id": "research.web_research",
            "label": "Web research",
            "description": _RESEARCH_DESCRIPTION,
        },
    ]


# ===========================================================================
# Phase 6 Task 5 amendment (post-T4 disclosure): the live bootstrap routes --
# mock_chat.py's own conversation create/list/transcript/feedback shapes,
# backed by a minimal in-memory TranscriptStore alongside
# ConversationStateStore. Closes Task 4's own disclosed gap (that task's
# report, Judgment Call 1 / Concern 1): a chat_mode="live" app could not
# serve the frontend's bootstrap() flow end to end.
# ===========================================================================


@pytest.mark.anyio
async def test_post_conversations_returns_the_same_opener_shape_as_mock():
    """Same wire shape mock_chat.py's own create_conversation returns --
    the frontend's bootstrap() reads conversation.id/opener.parts and does
    not care which mode produced them."""
    app = _live_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations")

    assert r.status_code == 201
    body = r.json()
    assert set(body["conversation"]) == {"id", "title"}
    opener = body["opener"]
    assert opener["role"] == "assistant"
    kinds = [p["kind"] for p in opener["parts"]]
    assert kinds == ["text", "chips"]
    ids = [o["id"] for o in opener["parts"][1]["payload"]["options"]]
    assert ids == ["existing_customer", "new_prospect"]


@pytest.mark.anyio
async def test_get_conversations_lists_newest_first():
    app = _live_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        c1 = (await client.post("/api/conversations")).json()["conversation"]["id"]
        c2 = (await client.post("/api/conversations")).json()["conversation"]["id"]
        listing = (await client.get("/api/conversations")).json()["conversations"]

    assert [c["id"] for c in listing[:2]] == [c2, c1]


@pytest.mark.anyio
async def test_get_messages_404_for_a_conversation_id_never_seen():
    app = _live_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get("/api/conversations/never-seen/messages")

    assert r.status_code == 404


@pytest.mark.anyio
async def test_get_messages_returns_the_opener_right_after_create():
    app = _live_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        r = await client.get(f"/api/conversations/{cid}/messages")

    assert r.status_code == 200
    assert [m["role"] for m in r.json()["messages"]] == ["assistant"]


@pytest.mark.anyio
async def test_a_real_turn_is_recorded_into_the_transcript_user_then_assistant_parts():
    """The flagship scripted turn, through the real bootstrap-send-reopen
    round trip: create a conversation for real, send a real turn, reopen
    the transcript and see exactly what was streamed -- assistant messages
    are appended from the turn's emitted parts at done-time (the amendment's
    own words), not re-derived some other way."""
    writer = RecordingWriter()
    app = _live_app(data_client=FakeDataClient(), writer=writer)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        await read_sse(
            client, cid, "Top GP customers for Port of Singapore in April 2026", "ctk-transcript"
        )
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]

    # opener (from create), then the user's question, then the assistant's answer.
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]
    user_msg, assistant_msg = msgs[1], msgs[2]
    assert user_msg["parts"] == [
        {
            "kind": "text",
            "payload": {"markdown": "Top GP customers for Port of Singapore in April 2026"},
        }
    ]
    kinds = [p["kind"] for p in assistant_msg["parts"]]
    assert kinds == ["tool_event", "table", "proof", "text"]
    tool_event = assistant_msg["parts"][0]["payload"]
    assert tool_event["status"] == "done"
    assert tool_event["tool"] == "data_qa.metric_query"
    assert "turn_id" not in tool_event and "event_seq" not in tool_event
    assert assistant_msg["parts"][1]["payload"]["columns"] == ["Customer", "Gross Profit"]
    assert assistant_msg["parts"][3]["payload"]["markdown"].startswith(
        "Certified answer for SINGAPORE"
    )


@pytest.mark.anyio
async def test_a_clarify_turn_is_recorded_into_the_transcript_as_chips_then_text():
    """Final-review wave item 6: contrast with
    ``test_post_conversations_returns_the_same_opener_shape_as_mock``'s own
    OPENER kinds (``["text", "chips"]``) -- the clarify TURN's own
    transcript kinds are the opposite order, chips first then text, matching
    ``orchestrator.py``'s own ``_finish_clarify`` push order (the chips part,
    then the "did you mean" text part)."""
    app = _live_app(data_client=FakeDataClient(), writer=RecordingWriter())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        await read_sse(client, cid, "gp for Meridiann in April 2026", None)
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]

    assistant_msg = msgs[-1]
    kinds = [p["kind"] for p in assistant_msg["parts"]]
    assert kinds == ["chips", "text"]
    # The clarification chips carry the same "for <name>" send_text
    # orchestrator.py now emits (final-review wave item 2) -- the transcript
    # records the part payload verbatim, envelope stripped.
    first_option = assistant_msg["parts"][0]["payload"]["options"][0]
    assert first_option["send_text"] == f"for {first_option['label']}"


@pytest.mark.anyio
async def test_streaming_route_auto_vivifies_transcript_for_an_unregistered_conversation_id():
    """Backward compatibility, disclosed in the module docstring: the
    streaming route itself stays opaque about cid (Task 4's own documented
    choice -- every test above this section dispatches against an ad hoc id
    like "conv-1" that was never created via POST /api/conversations), but
    the transcript store still records whatever happened, so a SUBSEQUENT
    GET on that same id now succeeds instead of 404ing."""
    app = _live_app(data_client=FakeDataClient(), writer=RecordingWriter())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        await read_sse(client, "conv-never-created", "hello", None)
        r = await client.get("/api/conversations/conv-never-created/messages")

    assert r.status_code == 200
    assert [m["role"] for m in r.json()["messages"]] == ["user", "assistant"]


@pytest.mark.anyio
async def test_feedback_roundtrip_and_unknown_message_404():
    app = _live_app(data_client=FakeDataClient(), writer=RecordingWriter())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        await read_sse(client, cid, "hello", None)
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]
        mid = msgs[-1]["id"]

        r = await client.post(
            f"/api/messages/{mid}/feedback", json={"verdict": "down", "comment": "wrong port"}
        )
        assert r.status_code == 204
        r = await client.get(f"/api/messages/{mid}/feedback")
        assert r.json() == {"verdict": "down", "comment": "wrong port"}

        r = await client.post(f"/api/messages/{mid}/feedback", json={"verdict": "up"})
        assert r.status_code == 204
        r = await client.get(f"/api/messages/{mid}/feedback")
        assert r.json() == {"verdict": "up", "comment": None}

        r = await client.post("/api/messages/nope/feedback", json={"verdict": "up"})
        assert r.status_code == 404
        r = await client.get("/api/messages/nope/feedback")
        assert r.status_code == 404


@pytest.mark.anyio
async def test_feedback_invalid_verdict_returns_422():
    app = _live_app(data_client=FakeDataClient(), writer=RecordingWriter())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        await read_sse(client, cid, "hello", None)
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]

        r = await client.post(
            f"/api/messages/{msgs[-1]['id']}/feedback", json={"verdict": "sideways"}
        )

    assert r.status_code == 422


# ===========================================================================
# Phase 6 Task 5 amendment: the data_backend == "snowflake" guard
# dev_runner.py already has (_build_ctx's own structured 501), adapted to
# this endpoint's SSE shape -- fail loudly with ONE error frame, never
# silently query the synthetic schema instead.
# ===========================================================================


class _ExplodingDataClient:
    """Any method call is a test failure -- proves the guard never reaches
    the data client at all ("never silently query the wrong schema")."""

    def __getattr__(self, name):
        def _boom(*_args, **_kwargs):
            raise AssertionError(
                f"data client method {name!r} must never be called behind the snowflake guard"
            )

        return _boom


@pytest.mark.anyio
async def test_snowflake_data_backend_emits_one_structured_error_frame_and_never_touches_data():
    app = _live_app(
        data_client=_ExplodingDataClient(), writer=RecordingWriter(), data_backend="snowflake"
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        events = await read_sse(client, "conv-snow", "hello", None)

    assert [name for name, _data in events] == ["error"]
    error_data = events[0][1]
    assert error_data["code"] == "backend not implemented"
    assert "data_backend='snowflake'" in error_data["message"]
    assert "Phase 15" in error_data["message"]


@pytest.mark.anyio
async def test_snowflake_guard_still_records_an_empty_assistant_message_in_the_transcript():
    app = _live_app(
        data_client=_ExplodingDataClient(), writer=RecordingWriter(), data_backend="snowflake"
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        await read_sse(client, cid, "hello", None)
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]

    assert msgs[-1]["role"] == "assistant"
    assert msgs[-1]["parts"] == []


def test_mock_mode_app_still_has_none_of_the_live_bootstrap_routes():
    """Regression guard: chat_mode="mock" is untouched by this amendment --
    mock_chat.py's OWN routes serve /api/conversations already; this
    amendment's code lives entirely behind chat_mode="live"."""
    app = _mock_app()
    paths = app.openapi()["paths"]
    assert "/api/skills" not in paths
