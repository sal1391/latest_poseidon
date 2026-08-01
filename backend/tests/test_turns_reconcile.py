"""Tests for Phase 11 Task 3 (doc 01 section 5, doc 06 sections 1-2): the
reconnect reconciliation endpoint (``GET /api/turns/{id}``) and the
``_begin_turn`` true-replay upgrade for a duplicate ``client_turn_key``
whose original turn already finished ``ok``.

Mirrors ``test_history_cutover.py``'s/``test_runlog_rls.py``'s own
conventions exactly (both already establish the pattern this task's brief
points at): ``httpx.ASGITransport`` against a real ``create_app()``, fresh
run-unique ``X-Dev-User`` identities (never bare "alice"/"bob" literals --
re-running this suite against the same long-lived dev Postgres must never
collide with a previous run's rows), and direct :class:`~poseidon.core.
runlog.RunLogWriter` construction (never raw SQL) whenever a test needs a
turn in a specific, otherwise-unreachable state (``running``, ``error``)
that driving it through the real HTTP pipeline cannot produce on demand.

**Why "running"/"error" turns are built through the writer, not by racing
real requests.** The pinned scope only cares about the STORED status at
the moment a duplicate ``client_turn_key`` arrives, not about how that
status came to be. Building the row directly (``writer.start_turn`` alone
for ``running`` -- deliberately never finalized, mirroring ``test_runlog_
rls.py``'s own "the turn deliberately never calls finalize" precedent;
``writer.start_turn`` + ``writer.finalize(status="error", ...)`` for
``error``) isolates exactly the one variable this suite cares about
(status) with no race/timing dependency at all.

**Exact llm_calls/tool_calls counts below (2 and 1 for the routed
question, 1 and 0 for "hello") are pinned from a real run against this
environment's own compose Postgres** (a throwaway probe script, this
codebase's own "not guessed at" discipline -- see ``test_chat_
orchestrator.py``'s module docstring for the precedent), not inferred from
reading ``loop.py``'s source by hand.
"""

import json
import os
import uuid

import httpx
import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.db import build_engine
from poseidon.core.runlog import RunLogWriter

pytestmark = pytest.mark.pg

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0005)"

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        "DATABASE_URL is not set - pg turn-reconciliation tests need a Postgres: "
        f"{_UP_HINT}, {_MIGRATE_HINT}",
        allow_module_level=True,
    )

# Computed once, mirroring test_runlog_rls.py's own module-level probe --
# this suite needs migration 0005 (turn_run.redacted_at) AND the DELETE
# route it drives for the redaction scenario.
_DSN_ROLE_IS_SUPERUSER = False
try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'turn_run' AND column_name = 'redacted_at'"
            )
            if _cur.fetchone() is None:
                pytest.skip(
                    f"turn_run.redacted_at does not exist - {_MIGRATE_HINT}",
                    allow_module_level=True,
                )
            _cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            _DSN_ROLE_IS_SUPERUSER = _cur.fetchone()[0]
except psycopg.Error as exc:
    pytest.skip(
        f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
        f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT}",
        allow_module_level=True,
    )

_APP_ROLE = "poseidon_app"
_EFFECTIVE_APP_ROLE = _APP_ROLE if _DSN_ROLE_IS_SUPERUSER else None

# Pinned against a real run in this environment (module docstring) -- a
# routed data_qa.metric_query dispatch always logs two llm_calls rows (the
# router's tool-call decision, then its final-answer synthesis) and one
# tool_calls row.
_ROUTED_QUESTION = "GP for Atlas Bunkering in April 2026"
_ROUTED_LLM_CALL_COUNT = 2
_ROUTED_TOOL_CALL_COUNT = 1

# "hello" matches no state-block-driven gate, so DevDeterministicRouter
# answers directly with no tool dispatch at all -- one llm_calls row, zero
# tool_calls, one persisted "text" part. The minimal fixture for the
# replay tests below, which only care about "some real ok turn with some
# real persisted parts", never about data_qa content specifically.
_PLAIN_QUESTION = "hello"


@pytest.fixture
def pg_database_url() -> str:
    return _DSN


@pytest.fixture
def pg_engine():
    engine = build_engine(_DSN)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _dev_user(name: str) -> str:
    """A fresh, run-unique ``X-Dev-User`` value -- see the module
    docstring's "fresh, run-unique act-as identities" precedent."""
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _headers(user: str) -> dict[str, str]:
    return {"X-Dev-User": user}


def _settings(pg_database_url: str, **overrides):
    from poseidon.core.config import Settings

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


def _writer(engine: Engine) -> RunLogWriter:
    return RunLogWriter(engine, app_role=_EFFECTIVE_APP_ROLE)


async def read_sse(
    client: httpx.AsyncClient,
    cid: str,
    text: str,
    headers: dict[str, str],
    client_turn_key: str | None = None,
):
    """Mirrors ``test_history_cutover.py``'s/``test_live_chat_sse.py``'s
    own ``read_sse`` helper exactly -- the wire format is pinned
    byte-identical (``events.py``'s module docstring), so the same parsing
    logic applies unchanged. ``client_turn_key`` is exposed (unlike those
    modules' own helpers, which never need one) since this whole suite's
    point is driving deliberate duplicates."""
    events = []
    body: dict[str, object] = {"text": text}
    if client_turn_key is not None:
        body["client_turn_key"] = client_turn_key
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json=body, headers=headers
    ) as response:
        assert response.status_code == 200
        name = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: ") :])))
    return events


def _pinned_turn_keys() -> set[str]:
    return {
        "id",
        "conversation_id",
        "message_id",
        "kind",
        "status",
        "question",
        "mode",
        "created_at",
        "finished_at",
        "trace_id",
        "redacted",
    }


def _pinned_llm_call_keys() -> set[str]:
    return {
        "seq",
        "provider",
        "model_id",
        "role",
        "prompt_version",
        "status",
        "input_tokens",
        "output_tokens",
        "latency_ms",
    }


def _pinned_tool_call_keys() -> set[str]:
    return {"seq", "tool", "server", "status", "latency_ms"}


# ===========================================================================
# happy rebuild: a real turn via the stub pipeline -> GET returns the
# pinned shape, children seq-ordered, message parts present (cross-checked
# against GET .../messages, the independently-proven source of truth)
# ===========================================================================


@pytest.mark.anyio
async def test_get_turn_rebuilds_the_pinned_shape_for_a_real_stub_pipeline_turn(
    pg_database_url,
):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        events = await read_sse(client, cid, _ROUTED_QUESTION, headers=headers)
        turn_id = events[0][1]["turn_id"]

        turn_response = await client.get(f"/api/turns/{turn_id}", headers=headers)
        messages_response = await client.get(
            f"/api/conversations/{cid}/messages", headers=headers
        )

    assert [name for name, _payload in events][-1] == "done"
    assert turn_response.status_code == 200
    body = turn_response.json()
    assert set(body) == {"turn", "llm_calls", "tool_calls", "message"}

    turn = body["turn"]
    assert set(turn) == _pinned_turn_keys()
    assert turn["id"] == turn_id
    assert turn["conversation_id"] == cid
    assert turn["kind"] == "chat_turn"
    assert turn["status"] == "ok"
    assert turn["question"] == _ROUTED_QUESTION
    assert turn["mode"] == "default"
    assert turn["redacted"] is False
    assert turn["created_at"] is not None
    assert turn["finished_at"] is not None
    assert turn["message_id"] is not None

    llm_calls = body["llm_calls"]
    assert len(llm_calls) == _ROUTED_LLM_CALL_COUNT
    assert [c["seq"] for c in llm_calls] == list(range(1, _ROUTED_LLM_CALL_COUNT + 1))
    for call in llm_calls:
        assert set(call) == _pinned_llm_call_keys()
        assert "args" not in call
        assert "prompt_hash" not in call

    tool_calls = body["tool_calls"]
    assert len(tool_calls) == _ROUTED_TOOL_CALL_COUNT
    assert [c["seq"] for c in tool_calls] == list(range(1, _ROUTED_TOOL_CALL_COUNT + 1))
    for call in tool_calls:
        assert set(call) == _pinned_tool_call_keys()
        assert call["tool"] == "data_qa.metric_query"
        assert "args" not in call

    assert body["message"] is not None
    assert set(body["message"]) == {"id", "parts"}
    assert body["message"]["id"] == turn["message_id"]
    last_persisted = messages_response.json()["items"][-1]
    assert last_persisted["role"] == "assistant"
    assert last_persisted["id"] == body["message"]["id"]
    assert body["message"]["parts"] == last_persisted["parts"]
    assert len(body["message"]["parts"]) > 0


# ===========================================================================
# unknown + foreign id -> 404 both (RLS collapses the two, on purpose)
# ===========================================================================


@pytest.mark.anyio
async def test_get_turn_unknown_id_404s(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get(f"/api/turns/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_turn_malformed_id_404s(pg_database_url):
    """Not even a syntactically valid UUID -- treated exactly like an
    absent one (the same "malformed id reads as not found" discipline
    ``core/chat/history.py``'s own module docstring establishes)."""
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/api/turns/not-a-uuid-at-all", headers=headers)
    assert response.status_code == 404


@pytest.mark.anyio
async def test_get_turn_foreign_id_404s(pg_database_url):
    alice = _dev_user("alice")
    bob = _dev_user("bob")
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=_headers(alice))).json()[
            "conversation"
        ]["id"]
        events = await read_sse(client, cid, _PLAIN_QUESTION, headers=_headers(alice))
        turn_id = events[0][1]["turn_id"]

        bob_response = await client.get(f"/api/turns/{turn_id}", headers=_headers(bob))
        # sanity: alice herself still sees it -- proves bob's 404 above is
        # the RLS-visibility gate, not a bug that blocks everyone.
        alice_response = await client.get(f"/api/turns/{turn_id}", headers=_headers(alice))

    assert bob_response.status_code == 404
    assert alice_response.status_code == 200


# ===========================================================================
# redacted turn: redacted true, question null, counts/status intact
# ===========================================================================


@pytest.mark.anyio
async def test_get_turn_on_a_redacted_turn_nulls_question_but_keeps_counts_and_status(
    pg_database_url,
):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        events = await read_sse(client, cid, _ROUTED_QUESTION, headers=headers)
        turn_id = events[0][1]["turn_id"]

        delete_response = await client.delete(f"/api/conversations/{cid}", headers=headers)
        turn_response = await client.get(f"/api/turns/{turn_id}", headers=headers)

    assert delete_response.status_code == 204
    assert turn_response.status_code == 200
    body = turn_response.json()

    turn = body["turn"]
    assert turn["redacted"] is True
    assert turn["question"] is None
    # doc 05 section 7's own "survives" list: ids/timestamps/status/kind
    # untouched by redaction.
    assert turn["id"] == turn_id
    assert turn["status"] == "ok"
    assert turn["kind"] == "chat_turn"
    assert turn["finished_at"] is not None
    # conversation_id itself still names the (now-deleted) conversation --
    # redaction nulls turn_run's own payload columns, never its foreign
    # ids (doc 05 section 7's schema half never touches conversation_id).
    assert turn["conversation_id"] == cid

    assert len(body["llm_calls"]) == _ROUTED_LLM_CALL_COUNT
    assert len(body["tool_calls"]) == _ROUTED_TOOL_CALL_COUNT
    # tool_calls.args is redacted (nulled) too, but this pinned shape never
    # selected args in the first place -- redaction has no visible effect
    # on this response beyond turn.question/turn.redacted.
    for call in body["tool_calls"]:
        assert set(call) == _pinned_tool_call_keys()


# ===========================================================================
# true replay: same client_turn_key after an ok turn -> SSE yields the
# original parts + done with replayed true, and does NOT execute the
# pipeline (no new turn_run, no new llm_calls -- row counts unchanged)
# ===========================================================================


@pytest.mark.anyio
async def test_duplicate_after_an_ok_turn_replays_the_persisted_parts_with_no_new_rows(
    pg_database_url, pg_engine
):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    # client_turn_key is a real `uuid` column (doc 06 section 1's schema) --
    # a plain-text sentinel would fail the INSERT with InvalidTextRepresentation
    # (verified directly: this test's own RED run).
    replay_key = str(uuid.uuid4())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        first_events = await read_sse(
            client, cid, _PLAIN_QUESTION, headers=headers, client_turn_key=replay_key
        )
        first_turn_id = first_events[0][1]["turn_id"]

        with pg_engine.connect() as conn:
            turn_run_count_before = conn.execute(
                text("SELECT COUNT(*) FROM turn_run WHERE conversation_id = :cid"), {"cid": cid}
            ).scalar_one()
            llm_call_count_before = conn.execute(
                text("SELECT COUNT(*) FROM llm_calls WHERE turn_run_id = :id"),
                {"id": first_turn_id},
            ).scalar_one()
            tool_call_count_before = conn.execute(
                text("SELECT COUNT(*) FROM tool_calls WHERE turn_run_id = :id"),
                {"id": first_turn_id},
            ).scalar_one()

        replay_events = await read_sse(
            client, cid, _PLAIN_QUESTION, headers=headers, client_turn_key=replay_key
        )

        with pg_engine.connect() as conn:
            turn_run_count_after = conn.execute(
                text("SELECT COUNT(*) FROM turn_run WHERE conversation_id = :cid"), {"cid": cid}
            ).scalar_one()
            llm_call_count_after = conn.execute(
                text("SELECT COUNT(*) FROM llm_calls WHERE turn_run_id = :id"),
                {"id": first_turn_id},
            ).scalar_one()
            tool_call_count_after = conn.execute(
                text("SELECT COUNT(*) FROM tool_calls WHERE turn_run_id = :id"),
                {"id": first_turn_id},
            ).scalar_one()

    # No pipeline execution: not one extra turn_run row, not one extra
    # llm_calls/tool_calls row against the ORIGINAL turn's own id.
    assert turn_run_count_before == 1
    assert turn_run_count_after == 1
    assert llm_call_count_after == llm_call_count_before
    assert tool_call_count_after == tool_call_count_before

    # The replay response is its OWN turn/message envelope (a fresh HTTP
    # request mints its own sink) -- never the original's ids -- but every
    # frame of THIS response shares that one envelope, and there is no
    # "error" frame anywhere in it.
    replay_names = [name for name, _payload in replay_events]
    assert replay_names == ["accepted", "part", "done"]
    replay_turn_id = replay_events[0][1]["turn_id"]
    assert replay_turn_id != first_turn_id
    for _name, payload in replay_events:
        assert payload["turn_id"] == replay_turn_id

    part_payload = replay_events[1][1]
    assert part_payload["kind"] == "text"
    # Byte-identical to the ORIGINAL turn's own persisted answer -- proven
    # against the independently-fetched conversation transcript, not
    # merely re-asserted against a literal this test also chose.
    assert part_payload["payload"]["markdown"] == (
        "I can answer certified metric questions "
        + chr(0x2014)
        + " try a metric, a customer or port, and a period."
    )

    done_payload = replay_events[2][1]
    assert done_payload["replayed"] is True

    # The reconciliation endpoint, read back by the ORIGINAL turn_id,
    # still reports the untouched original -- a replay changes nothing
    # about the row it replayed FROM.
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        original = (await client.get(f"/api/turns/{first_turn_id}", headers=headers)).json()
    assert original["turn"]["status"] == "ok"
    assert len(original["llm_calls"]) == llm_call_count_before


@pytest.mark.anyio
async def test_a_normal_turns_done_frame_never_carries_a_replayed_key(pg_database_url):
    """Sensitivity, the other direction: an ORDINARY (non-duplicate) turn's
    ``done`` frame must stay exactly as it was before this task -- no
    ``replayed`` key at all, not even ``false`` -- so every pre-existing
    test asserting this frame's shape elsewhere in this codebase keeps
    passing unchanged."""
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        events = await read_sse(client, cid, _PLAIN_QUESTION, headers=headers)

    done_payload = [payload for name, payload in events if name == "done"][0]
    assert "replayed" not in done_payload


# ===========================================================================
# duplicate against a non-ok turn (running / error) -> today's duplicate_
# turn error frame, byte-identical -- built directly through the writer so
# the status is exactly what each test needs, not raced into existence
# ===========================================================================


@pytest.mark.anyio
async def test_duplicate_against_a_running_turn_stays_the_pinned_duplicate_turn_error(
    pg_database_url, pg_engine
):
    alice = _dev_user("alice")
    headers = _headers(alice)
    user_sub = f"dev|{alice}"
    app = _app(pg_database_url)
    writer = _writer(pg_engine)
    duplicate_key = str(uuid.uuid4())  # client_turn_key is a real `uuid` column
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]

        # Built directly, never finalized -- status stays 'running' forever
        # (test_runlog_rls.py's own identical precedent for this exact
        # shape), with no race against a real in-flight request needed.
        handle = writer.start_turn(
            user_sub=user_sub,
            conversation_id=cid,
            client_turn_key=duplicate_key,
            turn_index=1,
            question="already in flight",
            mode="default",
            parsed={},
        )
        assert handle is not None and handle.created is True

        events = await read_sse(
            client, cid, "a retry while the original is still running", headers=headers,
            client_turn_key=duplicate_key,
        )

    names = [name for name, _payload in events]
    assert names == ["accepted", "error"]
    error_payload = events[1][1]
    assert error_payload["code"] == "duplicate_turn"
    assert error_payload["message"] == (
        "this turn was already processed " + chr(0x2014) + " refresh to load the conversation"
    )
    assert "replayed" not in error_payload


@pytest.mark.anyio
async def test_duplicate_against_an_error_turn_stays_the_pinned_duplicate_turn_error(
    pg_database_url, pg_engine
):
    alice = _dev_user("alice")
    headers = _headers(alice)
    user_sub = f"dev|{alice}"
    app = _app(pg_database_url)
    writer = _writer(pg_engine)
    duplicate_key = str(uuid.uuid4())  # client_turn_key is a real `uuid` column
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]

        handle = writer.start_turn(
            user_sub=user_sub,
            conversation_id=cid,
            client_turn_key=duplicate_key,
            turn_index=1,
            question="the original that failed",
            mode="default",
            parsed={},
        )
        assert handle is not None and handle.created is True
        writer.finalize(
            turn_run_id=handle.turn_run_id,
            user_sub=user_sub,
            status="error",
            message_id=str(uuid.uuid4()),
            answer_summary=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=5,
            error={"title": "llm provider error", "detail": "simulated"},
        )

        events = await read_sse(
            client, cid, "a retry after the original errored", headers=headers,
            client_turn_key=duplicate_key,
        )

    names = [name for name, _payload in events]
    assert names == ["accepted", "error"]
    error_payload = events[1][1]
    assert error_payload["code"] == "duplicate_turn"
    assert error_payload["message"] == (
        "this turn was already processed " + chr(0x2014) + " refresh to load the conversation"
    )
    assert "replayed" not in error_payload


def test_turns_reconcile_module_is_ascii_on_disk():
    """Matches the codebase-wide ASCII-on-disk convention (e.g.
    ``test_runlog_rls_module_is_ascii_on_disk``)."""
    from pathlib import Path

    offending = sorted({byte for byte in Path(__file__).read_bytes() if byte > 0x7F})
    assert not offending, f"{Path(__file__).name} holds non-ASCII bytes: {offending}"
