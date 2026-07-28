import json

import httpx
import pytest

from poseidon.core.config import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def app():
    from poseidon.api.app import create_app

    return create_app(Settings(
        _env_file=None,
        database_url="postgresql+psycopg://nobody:nope@127.0.0.1:1/void",
        s3_bucket="poseidon-artifacts",
    ))


async def read_sse(client, cid, text):
    events = []
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"text": text}
    ) as response:
        assert response.status_code == 200
        name = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: "):])))
    return events


@pytest.mark.anyio
async def test_create_conversation_returns_opener_with_chips(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/conversations")
        assert r.status_code == 201
        opener = r.json()["opener"]
        kinds = [p["kind"] for p in opener["parts"]]
        assert kinds == ["text", "chips"]
        ids = [o["id"] for o in opener["parts"][1]["payload"]["options"]]
        assert ids == ["existing_customer", "new_prospect"]


@pytest.mark.anyio
async def test_mock_turn_streams_tools_tokens_done(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        events = await read_sse(client, cid, "top GP customers in Singapore")
        names = [n for n, _ in events]
        assert names[0] == "accepted"
        assert names[-1] == "done"
        tool_events = [d for n, d in events if n == "tool"]
        assert {(t["tool_seq"], t["status"]) for t in tool_events} == {
            (1, "start"), (1, "done"), (2, "start"), (2, "done")}
        assert any(n == "token" for n, _ in events)
        # envelope on every event: turn_id/message_id/event_seq, strictly increasing
        payloads = [d for _, d in events]
        assert all({"turn_id", "message_id", "event_seq"} <= set(d) for d in payloads)
        seqs = [d["event_seq"] for d in payloads]
        assert seqs[0] == 1 and seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert len({d["turn_id"] for d in payloads}) == 1
        # transcript persisted: user + assistant with tool_event + text parts
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]
        assert msgs[-1]["role"] == "assistant"
        assert [p["kind"] for p in msgs[-1]["parts"]].count("tool_event") == 2


@pytest.mark.anyio
async def test_error_trigger_emits_error_event(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        events = await read_sse(client, cid, "please !error now")
        assert [n for n, _ in events] == ["accepted", "error"]
        for _, d in events:  # envelope present on both frames, incl. the error path
            assert {"turn_id", "message_id", "event_seq"} <= set(d)
        err = events[1][1]
        assert err["code"] == "mock_failure" and "message" in err and "hint" in err


@pytest.mark.anyio
async def test_feedback_upsert_roundtrip(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        await read_sse(client, cid, "hello")
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]
        mid = msgs[-1]["id"]
        r = await client.post(f"/api/messages/{mid}/feedback",
                              json={"verdict": "down", "comment": "wrong port"})
        assert r.status_code == 204
        r = await client.post(f"/api/messages/{mid}/feedback", json={"verdict": "up"})
        assert r.status_code == 204
        r = await client.get(f"/api/messages/{mid}/feedback")
        assert r.json() == {"verdict": "up", "comment": None}


@pytest.mark.anyio
async def test_feedback_mid_stream_is_stored_not_404(app):
    """The UI renders the feedback row as soon as the assistant message appears,
    so a thumbs can land long before `done`. That used to 404."""
    from poseidon.api.mock_chat import SendBody, send_message

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations")).json()["conversation"]["id"]
        # `read_sse` cannot hold a turn open: httpx's ASGITransport runs the app
        # to completion and buffers the body before `client.stream()` yields.
        # Driving the route's generator directly suspends the turn for real —
        # only `accepted` has been emitted here, no tool or token frames yet.
        frames = send_message(cid, SendBody(text="hello")).body_iterator
        first = await frames.__anext__()
        assert "event: accepted" in first
        mid = json.loads(first.split("data: ", 1)[1])["message_id"]

        # The regression: this used to 404, because the assistant message was
        # only appended after the last token.
        r = await client.post(f"/api/messages/{mid}/feedback", json={"verdict": "up"})
        assert r.status_code == 204
        # ...and the turn really is still mid-flight: known id, no parts yet.
        partial = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]
        assert partial[-1]["id"] == mid and partial[-1]["parts"] == []

        async for _ in frames:  # finish the turn
            pass
        assert (await client.get(f"/api/messages/{mid}/feedback")).json()["verdict"] == "up"
        # ...and the completed transcript is still one message of the usual shape
        msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()["messages"]
        assert [m["id"] for m in msgs].count(mid) == 1
        assert [p["kind"] for p in msgs[-1]["parts"]] == ["tool_event", "tool_event", "text"]


@pytest.mark.anyio
async def test_list_conversations_newest_first(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        c1 = (await client.post("/api/conversations")).json()["conversation"]["id"]
        c2 = (await client.post("/api/conversations")).json()["conversation"]["id"]
        listing = (await client.get("/api/conversations")).json()["conversations"]
        assert [c["id"] for c in listing[:2]] == [c2, c1]


@pytest.mark.anyio
async def test_unknown_ids_return_404(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        assert (await client.get("/api/conversations/nope/messages")).status_code == 404
        r = await client.post("/api/conversations/nope/messages", json={"text": "hi"})
        assert r.status_code == 404
        assert (await client.post("/api/messages/nope/feedback",
                                  json={"verdict": "up"})).status_code == 404
        assert (await client.get("/api/messages/nope/feedback")).status_code == 404
