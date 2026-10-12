"""Phase 16 Task 9: the internal turns route is the public send route with
identity taken from the body (spec section 5.1). The proof is byte-level:
the same scripted turn through both routes yields the same frame sequence.

Every helper this file drives the app with is IMPORTED from
``test_live_chat_sse.py`` rather than copied -- ``_live_app``/``_mock_app``
(the real ``create_app``), ``pg_database_url``/``anyio_backend`` (fixtures),
``read_sse``/``_create_conversation``/``_dev_user``/``_headers``. That is the
whole point of the comparison below: if the public side of it were a local
re-implementation, the test would prove the two ROUTES agree with each
other's copies rather than with the one real public surface.

``read_sse`` is bound to the public path (and to the public body shape), so
only its decode loop is reusable here; :func:`_parse` is that loop, over
lines already read off the internal stream.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_chat_orchestrator import FakeDataClient, RecordingWriter
from tests.test_live_chat_sse import (  # noqa: F401
    _create_conversation,
    _dev_user,
    _headers,
    _live_app,
    _mock_app,
    anyio_backend,
    pg_database_url,
    read_sse,
)

_TOKEN = "test-internal-token"
_HEADERS = {"X-Poseidon-Internal-Token": _TOKEN}
_SALES = {"sub": "dev|local", "roles": ["Poseidon:Sales"]}
_PATH = "/internal/v1/conversations/{cid}/turns"
_UNKNOWN_CID = "00000000-0000-0000-0000-000000000000"

# Verbatim from test_live_chat_sse.py's own flagship test
# (test_live_turn_streams_the_flagship_frame_sequence_and_table_and_proof_
# parts) -- the scripted turn whose seven-frame sequence that test pins.
_FLAGSHIP_TEXT = "Top GP customers for Port of Singapore in April 2026"


def _app(**overrides):
    return _live_app(
        data_client=FakeDataClient(), writer=RecordingWriter(), internal_token=_TOKEN, **overrides
    )


def _strip(frames):
    """(name, data) minus the per-request ids, for cross-route comparison."""
    out = []
    for name, data in frames:
        out.append((name, {k: v for k, v in data.items() if k not in ("turn_id", "message_id")}))
    return out


def _parse(lines):
    """``read_sse``'s own ``event: ``/``data: `` decode loop, copied rather
    than called: that helper opens the PUBLIC path with the public body
    shape, so only this half of it applies to the internal stream."""
    events = []
    name = None
    for line in lines:
        if line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: "):
            events.append((name, json.loads(line[len("data: ") :])))
    return events


def test_internal_turn_requires_the_token():
    client = TestClient(_app())
    resp = client.post(_PATH.format(cid=_UNKNOWN_CID), json={"identity": _SALES, "text": "hi"})
    assert resp.status_code == 401


def test_internal_turn_requires_the_sales_role():
    client = TestClient(_app(), headers=_HEADERS)
    resp = client.post(
        _PATH.format(cid=_UNKNOWN_CID),
        json={"identity": {"sub": "dev|nobody", "roles": []}, "text": "hi"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "caller lacks required role 'Poseidon:Sales'"


def test_internal_turn_in_mock_chat_mode_is_503():
    """A mock-mode app never runs ``_wire_live_chat``, so nothing a turn
    needs is on ``app.state``. The token gate is satisfied first (it has a
    503 of its own for an UNSET token -- hence the detail assertion, which
    is what tells the two 503s apart)."""
    app = _mock_app()
    app.state.settings = app.state.settings.model_copy(update={"internal_token": _TOKEN})
    resp = TestClient(app, headers=_HEADERS).post(
        _PATH.format(cid=_UNKNOWN_CID), json={"identity": _SALES, "text": "hi"}
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "CHAT_MODE=live is required for internal turns"


# noqa: F811 on the two pg signatures below -- ``pg_database_url`` is an
# IMPORTED pytest fixture (pytest resolves a fixture by the name it is bound
# to in this module, so it cannot be aliased away), and ruff reads the
# request for it as a redefinition of that import.
@pytest.mark.pg
def test_internal_turn_404s_for_an_unknown_conversation(pg_database_url):  # noqa: F811
    client = TestClient(_app(database_url=pg_database_url), headers=_HEADERS)
    resp = client.post(_PATH.format(cid=_UNKNOWN_CID), json={"identity": _SALES, "text": "hi"})
    assert resp.status_code == 404


@pytest.mark.pg
@pytest.mark.anyio
async def test_internal_and_public_routes_stream_the_same_frames(pg_database_url):  # noqa: F811
    """The flagship scripted turn (``test_live_turn_streams_the_flagship_
    frame_sequence_and_table_and_proof_parts``) through both routes, in ONE
    app, and the stripped frame lists compared.

    Each side poses as its own run-unique act-as identity -- the public one
    through ``X-Dev-User`` (``read_sse``'s required ``headers``), the
    internal one stated in the body -- following this suite's F2 convention
    (``test_live_chat_sse.py``'s own ``_dev_user``: never dispatch a real
    turn as the shared ``dev|local`` default). The two subs differing is
    deliberate and costs the comparison nothing: no SSE frame carries an
    identity, while a SHARED sub would let the first turn's own
    personalization writes reach the second one.
    """
    app = _app(database_url=pg_database_url)
    public_user = _dev_user("parity-public")
    internal_user = _dev_user("parity-internal")
    internal_identity = {"sub": f"dev|{internal_user}", "roles": ["Poseidon:Sales"]}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid_public = await _create_conversation(client, _headers(public_user))
        public = _strip(
            await read_sse(client, cid_public, _FLAGSHIP_TEXT, "ctk-public", _headers(public_user))
        )

        # The conversation the internal turn runs in must belong to the
        # identity that turn states, exactly as the public side's does:
        # append_user_message is RLS-scoped, so a cid owned by anyone else
        # is the 404 the test above pins.
        cid_internal = await _create_conversation(client, _headers(internal_user))
        async with client.stream(
            "POST",
            _PATH.format(cid=cid_internal),
            json={
                "identity": internal_identity,
                "text": _FLAGSHIP_TEXT,
                "client_turn_key": "ctk-internal",
            },
            headers=_HEADERS,
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            lines = [line async for line in resp.aiter_lines()]
        internal = _strip(_parse(lines))

    assert internal == public
    assert [n for n, _ in internal] == [
        "accepted",
        "tool",
        "tool",
        "part",
        "part",
        "token",
        "done",
    ]
