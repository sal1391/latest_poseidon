"""Tests for Phase 10 Task 3 (doc 08): the cutover -- live chat
(``api/live_chat.py``, ``api/app.py``) moves off the in-memory
``TranscriptStore``/``ConversationStateStore`` pair onto Task 2's
Postgres-backed :class:`~poseidon.core.chat.history.HistoryStore`/
:class:`~poseidon.core.chat.history.DbStateStore`.

Every test here is ``@pytest.mark.pg``: the cutover's entire point is that
live chat now depends on a real, migrated Postgres for every route that
touches a conversation, so there is no meaningful offline double for any of
it (the same reasoning ``test_history_store.py``'s own module docstring
gives for its own pg half). Driven through ``httpx`` against a real
``create_app()`` -- never the store classes directly -- so what is proven
is the whole cutover: real HTTP routes, real identity headers, real RLS.

**Fresh, run-unique act-as identities, not bare "alice"/"bob" literals.**
The brief's own scenarios read naturally as "alice"/"bob"; this suite keeps
that flavor (:func:`_dev_user`) but suffixes each with a fresh ``uuid4``
hex chunk, exactly for the reason ``test_history_store.py``'s own
``_fresh_user_sub`` gives: re-running this suite against the same
long-lived dev Postgres must never let one test's "bob lists nothing" read
back a ROW some earlier run (or another test in this same file) left
behind for a literal ``dev|bob``. Rows are left uncleaned afterward
(the same precedent), for the same two reasons: nothing here has a unique
constraint a leftover row could collide with, and RLS itself already
guarantees isolation regardless of cleanup.

**"Real orchestrator, stub LLM, synthetic data."** The carry-after-restart
test drives an actual turn through ``execute_turn`` (never mocked) under
``LLM_MODE=stub`` (``DevDeterministicRouter`` answers) and
``DATA_BACKEND=synthetic`` (``SyntheticDataClient`` against the SAME
Postgres this suite's own ``pg_database_url`` fixture already proved
reachable and migrated -- the compose seed script's real, deterministic
dataset, not a fixture pool). The exact customer name and GP figures below
were verified against that real, running dataset with a throwaway probe
script before being pinned here (this codebase's own "not guessed at"
discipline -- see ``test_chat_orchestrator.py``'s module docstring for the
precedent), not assumed from the offline suites' unrelated fixture data.

**The stub title.** ``LLM_MODE=stub`` routes the ``utility`` role through
``DevDeterministicRouter`` exactly like every other role; a title prompt
(``utility/title``) matches none of that router's state-block-driven gates,
so it falls to the SAME deterministic capability-message fallback every
unrecognized input gets, truncated to :data:`~poseidon.core.llm.titles.
TITLE_MAX_CHARS` (60) characters -- verified directly against a real
``title_for(...)`` call before being pinned as :data:`_STUB_TITLE` below,
not derived from reading the router's source by hand.
"""

import json
import os
import uuid

import httpx
import psycopg
import pytest

from poseidon.core.data.synthetic_client import normalize_dsn

# ===========================================================================
# pg availability -- mirrors test_history_store.py's own pg_engine fixture,
# adapted to hand back the DSN string (an httpx-driven suite builds its own
# create_app(), never a raw Engine).
# ===========================================================================

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0004)"


@pytest.fixture
def pg_database_url() -> str:
    """``DATABASE_URL``, or a SKIP with an actionable reason: unset,
    unreachable within 2 seconds, or reachable but not yet migrated to
    0004 (``conversations`` absent)."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip(
            "DATABASE_URL is not set - pg history-cutover tests need a Postgres: "
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


@pytest.fixture
def anyio_backend():
    return "asyncio"


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
    """A fresh app instance (fresh ``FastAPI``, fresh ``Engine``) against
    ``pg_database_url`` -- calling this twice in one test, both times
    against the SAME dsn, is this suite's "process restart" simulation
    (mirrors ``test_history_store.py``'s own ``test_restart_survival_...``,
    one layer up at the HTTP surface)."""
    from poseidon.api.app import create_app

    return create_app(_settings(pg_database_url, **overrides))


def _dev_user(name: str) -> str:
    """A fresh, run-unique ``X-Dev-User`` value that still reads as
    ``name`` -- see the module docstring's "fresh, run-unique act-as
    identities" for why a bare literal is not reused across test runs."""
    return f"{name}-{uuid.uuid4().hex[:8]}"


def _headers(user: str) -> dict[str, str]:
    return {"X-Dev-User": user}


async def read_sse(client: httpx.AsyncClient, cid: str, text: str, headers: dict[str, str]):
    """Mirrors ``test_live_chat_sse.py``'s/``test_mock_chat.py``'s own
    ``read_sse`` helper exactly -- the wire format is pinned byte-identical
    (``events.py``'s module docstring), so the same parsing logic applies
    unchanged; ``headers`` is required here (never optional) since every
    scenario in this file cares about WHICH identity sent the turn."""
    events = []
    async with client.stream(
        "POST", f"/api/conversations/{cid}/messages", json={"text": text}, headers=headers
    ) as response:
        assert response.status_code == 200
        name = None
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: ") :])))
    return events


# ===========================================================================
# act-as isolation: alice creates + sends; bob lists empty and 404s alice's
# conversation -- RLS, proven through the real HTTP surface.
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_act_as_isolation_bob_sees_none_of_alices_conversation(pg_database_url):
    alice_headers = _headers(_dev_user("alice"))
    bob_headers = _headers(_dev_user("bob"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=alice_headers)).json()[
            "conversation"
        ]["id"]
        await read_sse(client, cid, "hello", headers=alice_headers)

        bob_listing = await client.get("/api/conversations", headers=bob_headers)
        bob_messages = await client.get(f"/api/conversations/{cid}/messages", headers=bob_headers)
        # sanity: alice herself still sees exactly what she made -- proves
        # bob's empty list above is RLS isolation, not a bug that hides
        # everyone's conversations.
        alice_listing = await client.get("/api/conversations", headers=alice_headers)

    assert bob_listing.status_code == 200
    assert bob_listing.json() == {"items": [], "next_cursor": None}
    assert bob_messages.status_code == 404

    assert alice_listing.status_code == 200
    assert [c["id"] for c in alice_listing.json()["items"]] == [cid]


# ===========================================================================
# restart survival: a fresh app instance, same DATABASE_URL, still sees the
# conversation and its messages -- proves persistence, not an in-process
# illusion.
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_restart_survival_conversation_and_messages_persist(pg_database_url):
    headers = _headers(_dev_user("alice"))

    app1 = _app(pg_database_url)
    transport1 = httpx.ASGITransport(app=app1)
    async with httpx.AsyncClient(transport=transport1, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        turn_events = await read_sse(client, cid, "hello", headers=headers)
    assert "done" in [name for name, _payload in turn_events]

    # A brand-new FastAPI app + a brand-new Engine, pointed at the exact
    # same DATABASE_URL -- simulates a process restart (test_history_store.
    # py's own test_restart_survival_... does the identical thing one layer
    # lower, against HistoryStore directly rather than through HTTP).
    app2 = _app(pg_database_url)
    transport2 = httpx.ASGITransport(app=app2)
    async with httpx.AsyncClient(transport=transport2, base_url="http://t") as client:
        listing = await client.get("/api/conversations", headers=headers)
        messages = await client.get(f"/api/conversations/{cid}/messages", headers=headers)

    assert listing.status_code == 200
    assert [c["id"] for c in listing.json()["items"]] == [cid]

    assert messages.status_code == 200
    roles = [m["role"] for m in messages.json()["items"]]
    # opener (from create), the user's "hello", the assistant's answer.
    assert roles == ["assistant", "user", "assistant"]


# ===========================================================================
# continue-with-carry-after-restart: turn 1 (real orchestrator, stub LLM,
# real synthetic data) resolves a customer; a fresh app instance's turn 2
# names no customer at all and can only answer about the SAME one if slots
# were restored from conversations.state, not re-derived from thin air.
# ===========================================================================

# Verified against the real, running seeded synthetic dataset with a
# throwaway probe script (module docstring's own discipline) -- 40 seeded
# customers exist; this one's April/March 2026 GP figures differ enough to
# prove which period answered, and its name is a clean two-word TitleCase
# run the "for X" customer cue detects unambiguously.
_CARRY_CUSTOMER = "Atlas Bunkering"


@pytest.mark.pg
@pytest.mark.anyio
async def test_continue_with_carry_after_restart_answers_target_the_carried_customer(
    pg_database_url,
):
    headers = _headers(_dev_user("alice"))

    app1 = _app(pg_database_url)
    transport1 = httpx.ASGITransport(app=app1)
    async with httpx.AsyncClient(transport=transport1, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        turn1 = await read_sse(
            client, cid, f"GP for {_CARRY_CUSTOMER} in April 2026", headers=headers
        )
    assert [name for name, _payload in turn1][-1] == "done"

    # Fresh app instance, same DATABASE_URL -- turn 2 runs against a
    # brand-new DbStateStore/UserHistory pair with no in-process memory of
    # turn 1 whatsoever; ConversationSlots.customer can only be known if it
    # was actually read back out of conversations.state.
    app2 = _app(pg_database_url)
    transport2 = httpx.ASGITransport(app=app2)
    async with httpx.AsyncClient(transport=transport2, base_url="http://t") as client:
        turn2 = await read_sse(client, cid, "And in March 2026?", headers=headers)

    proof_payloads = [
        payload
        for name, payload in turn2
        if name == "part" and payload.get("kind") == "proof"
    ]
    assert len(proof_payloads) == 1
    proof_lines = proof_payloads[0]["payload"]["lines"]
    # The dispatch's own filter names the CARRIED customer, even though
    # turn 2's text never mentions any customer at all.
    assert f"Filters: CUST_NM IN ({_CARRY_CUSTOMER})" in proof_lines
    assert "Period: 2026-03-01..2026-04-01" in proof_lines

    token_payloads = [payload for name, payload in turn2 if name == "token"]
    assert len(token_payloads) == 1
    assert token_payloads[0]["text"].startswith(f"Certified answer for {_CARRY_CUSTOMER}")

    # turn 2 is turn_index 2 -- the additive done.title field stays null;
    # only a successful FIRST turn ever sets one (see the title tests below).
    done_payloads = [payload for name, payload in turn2 if name == "done"]
    assert len(done_payloads) == 1
    assert done_payloads[0]["title"] is None


# ===========================================================================
# title after first done: persisted (conversations.title) AND carried in
# that same turn's own done frame -- additive field, deterministic under
# the stub RoleClient.
# ===========================================================================

# Verified directly against a real title_for(...) call under LLM_MODE=stub
# before being pinned here (module docstring's "The stub title") -- the
# router's own deterministic capability-message fallback, truncated to
# TITLE_MAX_CHARS (60) characters, mid-word.
_STUB_TITLE = "I can answer certified metric questions " + chr(0x2014) + " try a metric, a cu"


@pytest.mark.pg
@pytest.mark.anyio
async def test_title_set_after_first_successful_turn_persisted_and_in_done_frame(
    pg_database_url,
):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        events = await read_sse(client, cid, "hello", headers=headers)

        listing = await client.get("/api/conversations", headers=headers)

    done_payloads = [payload for name, payload in events if name == "done"]
    assert len(done_payloads) == 1
    assert done_payloads[0]["title"] == _STUB_TITLE

    items = listing.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == cid
    assert items[0]["title"] == _STUB_TITLE
    assert items[0]["title"] != "New chat"


@pytest.mark.pg
@pytest.mark.anyio
async def test_title_is_not_set_for_a_turn_index_one_clarify(pg_database_url):
    """The brief's own gate is ``turn_index == 1`` AND ``status == "ok"`` --
    a first turn that only CLARIFIES must leave the title alone. Uses the
    D19 bubble-entry phrase (``orchestrator.py``'s own
    ``ENTRY_PHRASE_EXISTING``) rather than an ambiguous customer name:
    ``_finish_entry`` ALWAYS ends ``clarify`` deterministically (a fixed
    "which customer is this for?" prompt, no data/resolver involved at
    all), unlike guessing at which real seeded names happen to collide in
    the customer resolver's fuzzy tier -- probed directly and confirmed
    that guess would have been wrong: "Atlas" alone resolves confidently,
    non-ambiguously, to "Atlas Bunkering" against the real seeded pool.
    """
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        events = await read_sse(
            client, cid, "start an existing-customer brief", headers=headers
        )

        listing = await client.get("/api/conversations", headers=headers)

    names = [name for name, _payload in events]
    assert "done" in names
    done_payloads = [payload for name, payload in events if name == "done"]
    assert done_payloads[0]["title"] is None

    items = listing.json()["items"]
    assert items[0]["title"] == "New chat"


# ===========================================================================
# pagination envelope byte-pins
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_list_conversations_envelope_shape_and_pagination_byte_pin(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        created_ids = []
        for _ in range(3):
            r = await client.post("/api/conversations", headers=headers)
            created_ids.append(r.json()["conversation"]["id"])
        expected_order = list(reversed(created_ids))  # newest first

        page1 = await client.get("/api/conversations", params={"limit": 2}, headers=headers)
        page2 = await client.get(
            "/api/conversations",
            params={"limit": 2, "cursor": page1.json()["next_cursor"]},
            headers=headers,
        )

    body1 = page1.json()
    assert set(body1) == {"items", "next_cursor"}
    assert [set(item) for item in body1["items"]] == [{"id", "title"}, {"id", "title"}]
    assert [item["id"] for item in body1["items"]] == expected_order[0:2]
    assert body1["next_cursor"] is not None

    body2 = page2.json()
    assert set(body2) == {"items", "next_cursor"}
    assert [item["id"] for item in body2["items"]] == expected_order[2:3]
    assert body2["next_cursor"] is None


@pytest.mark.pg
@pytest.mark.anyio
async def test_get_messages_envelope_shape_byte_pin(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        r = await client.get(f"/api/conversations/{cid}/messages", headers=headers)

    body = r.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    assert len(body["items"]) == 1
    assert set(body["items"][0]) == {"id", "role", "parts"}
    assert body["items"][0]["role"] == "assistant"


# ===========================================================================
# malformed cursor -> 400 RFC-7807 problem detail, byte-pinned, never a 500
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_malformed_cursor_on_list_conversations_maps_to_400_problem_detail(
    pg_database_url,
):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.get(
            "/api/conversations", params={"cursor": "not-base64"}, headers=headers
        )

    assert r.status_code == 400
    assert r.json() == {
        "type": "about:blank",
        "title": "malformed cursor",
        "detail": "cursor 'not-base64' is not a valid conversations cursor",
        "status": 400,
    }


@pytest.mark.pg
@pytest.mark.anyio
async def test_malformed_cursor_on_get_messages_maps_to_400_problem_detail(pg_database_url):
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        r = await client.get(
            f"/api/conversations/{cid}/messages",
            params={"cursor": "not-base64"},
            headers=headers,
        )

    assert r.status_code == 400
    assert r.json() == {
        "type": "about:blank",
        "title": "malformed cursor",
        "detail": "cursor 'not-base64' is not a valid messages cursor",
        "status": 400,
    }


# ===========================================================================
# feedback 404 gate: an unknown mid and another user's (real) mid are both
# 404, indistinguishable by design.
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_feedback_404_gate_unknown_mid_and_another_users_mid(pg_database_url):
    alice_headers = _headers(_dev_user("alice"))
    bob_headers = _headers(_dev_user("bob"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=alice_headers)).json()[
            "conversation"
        ]["id"]
        turn_events = await read_sse(client, cid, "hello", headers=alice_headers)
        # the SSE envelope's own message_id IS the assistant message's id --
        # minted once per turn, shared by every frame (module docstring of
        # api/live_chat.py, "Id minting").
        assistant_mid = next(payload["message_id"] for _name, payload in turn_events)

        never_minted_mid = str(uuid.uuid4())
        r_unknown_post = await client.post(
            f"/api/messages/{never_minted_mid}/feedback",
            json={"verdict": "up"},
            headers=alice_headers,
        )
        r_unknown_get = await client.get(
            f"/api/messages/{never_minted_mid}/feedback", headers=alice_headers
        )

        r_bob_post = await client.post(
            f"/api/messages/{assistant_mid}/feedback",
            json={"verdict": "up"},
            headers=bob_headers,
        )
        r_bob_get = await client.get(f"/api/messages/{assistant_mid}/feedback", headers=bob_headers)

        # sanity: alice herself, the real owner, is not gated -- proves the
        # 404s above are the RLS-visibility gate, not a bug that blocks
        # everyone.
        r_alice_post = await client.post(
            f"/api/messages/{assistant_mid}/feedback",
            json={"verdict": "up"},
            headers=alice_headers,
        )
        r_alice_get = await client.get(
            f"/api/messages/{assistant_mid}/feedback", headers=alice_headers
        )

    assert r_unknown_post.status_code == 404
    assert r_unknown_get.status_code == 404
    assert r_bob_post.status_code == 404
    assert r_bob_get.status_code == 404
    assert r_alice_post.status_code == 204
    assert r_alice_get.status_code == 200
    assert r_alice_get.json() == {"verdict": "up", "comment": None}


# ===========================================================================
# Fix round 1 (review findings I-1/I-2/I-3, task-3-review.md section (c)):
# three coverage/ordering gaps on top of the cutover above -- I-1 and I-2
# are coverage-only (the reviewer confirmed the underlying code was already
# correct by inspection); I-3 is a genuine ordering fix in api/live_chat.py
# itself (persist-before-terminal-frame), proven RED against the pre-fix
# ordering before being made GREEN -- see task-3-report.md's own "Fix round
# 1" section for the full RED/GREEN evidence this file's tests produced.
# ===========================================================================


@pytest.mark.pg
@pytest.mark.anyio
async def test_title_fallback_to_first_60_chars_when_title_for_returns_empty(pg_database_url):
    """I-1: ``title_for(...)`` only ever returns ``""`` when the role
    client's ``utility``-role call resolves with ``stop_reason="error"``
    (``core/llm/titles.py``'s own docstring) -- under the stub ``RoleClient``
    every OTHER pg test in this suite exercises, that call always succeeds
    (the deterministic capability-message fallback, :data:`_STUB_TITLE`
    above), so the ``computed if computed else body.text[:60]`` ternary's
    ``else`` branch had no coverage anywhere in this suite. This test
    forces exactly that branch: a small wrapper role client answers the
    ``"utility"`` role with a ``stop_reason="error"`` ``LLMResponse`` --
    ``title_for``'s own documented empty-string trigger -- while
    delegating every OTHER role to the real, wrapped client unchanged, so
    the turn itself still completes normally (``status="ok"``) and only
    the title computation is forced down its fallback path. The question
    is deliberately over 60 characters (probed directly against the real
    parser first: no customer/port/period detected, no issues -- the same
    clean, unambiguous shape "hello" already takes elsewhere in this file)
    so the assertion actually exercises truncation, not merely "any
    non-empty fallback" (a short question would pass even a buggy
    implementation that forgot to slice at all).
    """
    from poseidon.core.llm.types import LLMResponse

    class _UtilityErrorRoleClient:
        """Wraps a real ``RoleClient``: the ``"utility"`` role (``title_
        for``'s own role) always resolves as a provider ERROR; every other
        role (the turn's own real router call) delegates unchanged."""

        def __init__(self, real):
            self._real = real

        def resolve(self, role):
            return self._real.resolve(role)

        def invoke(self, role, *, system, messages, tools=()):
            if role == "utility":
                return LLMResponse(
                    text="", tool_calls=(), stop_reason="error", input_tokens=0, output_tokens=0
                )
            return self._real.invoke(role, system=system, messages=messages, tools=tools)

    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    app.state.role_client = _UtilityErrorRoleClient(app.state.role_client)
    transport = httpx.ASGITransport(app=app)
    question = (
        "hello there, this is a plain conversational opener with no "
        "special query keywords in it at all today"
    )
    assert len(question) > 60  # the whole point of this test
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        events = await read_sse(client, cid, question, headers=headers)

        listing = await client.get("/api/conversations", headers=headers)

    done_payloads = [payload for name, payload in events if name == "done"]
    assert len(done_payloads) == 1
    assert done_payloads[0]["title"] == question[:60]

    items = listing.json()["items"]
    assert items[0]["id"] == cid
    assert items[0]["title"] == question[:60]


def _crashing_run_turn(**kwargs):
    """Stands in for ``orchestrator.py``'s own imported ``run_turn`` (from
    ``core.llm.loop``) -- patched at its USE site
    (``poseidon.core.chat.orchestrator.run_turn``), never orchestrator.py's
    own source (a runtime attribute substitution for one test, not a file
    edit -- the identical technique ``test_live_chat_sse.py`` already uses
    one layer up, patching ``poseidon.api.live_chat.execute_turn``).
    Streams ONE real token through the turn's own sink -- folding a
    genuine partial content part into the transcript buffer, exactly like
    a provider that answered partway before a network hiccup -- then
    raises, unhandled. Landing here (rather than patching ``execute_turn``
    itself, the way the crash tests in ``test_live_chat_sse.py`` do) means
    the REAL ``execute_turn`` has already run ``_begin_turn`` for real by
    the time this fires: a genuine ``turn_run`` row exists, with a genuine
    ``turn_index``, for ``writer.finalize`` to actually update -- exactly
    what I-2's own database-level proof needs."""
    kwargs["sink"].push_token("partial reply")
    raise RuntimeError("simulated mid-turn crash")


@pytest.mark.pg
@pytest.mark.anyio
async def test_mid_turn_crash_persists_the_partial_assistant_row_and_marks_turn_run_error(
    pg_database_url, monkeypatch
):
    """I-2: the OLD ``TranscriptStore`` precedent (BASE ``live_chat.py``:
    "this mirrors how the internal-error crash path also leaves behind an
    assistant message with whatever parts existed before the crash") and
    ``TurnTranscriptBuffer``'s own docstring ("discarded once ``UserHistory.
    write_assistant_message`` PERSISTS the finished message," never
    "discarded INSTEAD OF") both already establish that a crash mid-turn
    persists whatever partial content the buffer had folded by the time it
    happened -- the reviewer confirmed this design is correct by direct
    inspection. This test is the missing DATABASE-level proof (a real
    ``GET .../messages`` AND a real ``turn_run`` row), not a design change.

    Uses the REAL ``RunLogWriter`` (no double override): the crash is
    injected deep enough (:func:`_crashing_run_turn`, patched at
    ``orchestrator.py``'s own ``run_turn`` call) that the real
    ``_begin_turn``/``writer.start_turn`` already ran first, so a genuine
    ``turn_run`` row exists for ``finalize()`` to actually update to
    ``status="error"`` -- the status Phase 11's reconciliation endpoint
    will read.
    """
    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    monkeypatch.setattr("poseidon.core.chat.orchestrator.run_turn", _crashing_run_turn)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        events = await read_sse(client, cid, "hello", headers=headers)

        messages = await client.get(f"/api/conversations/{cid}/messages", headers=headers)

    names = [name for name, _payload in events]
    assert names == ["accepted", "token", "error"]
    turn_id = events[0][1]["turn_id"]

    items = messages.json()["items"]
    assert items[-1]["role"] == "assistant"
    assert items[-1]["parts"] == [{"kind": "text", "payload": {"markdown": "partial reply"}}]

    with psycopg.connect(normalize_dsn(pg_database_url)) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM turn_run WHERE id = %s", (turn_id,))
            row = cur.fetchone()
    assert row is not None
    assert row[0] == "error"


@pytest.mark.pg
@pytest.mark.anyio
async def test_normal_turn_calls_write_assistant_message_exactly_once(pg_database_url, monkeypatch):
    """I-3's own "double-write must be impossible" pin -- corrected after
    re-review (``task-3-rereview.md``'s own "New finding"): the ORIGINAL
    version of this test asserted a DB-level row-count property and its
    docstring claimed a removed ``persisted_holder`` guard "would 500" --
    traced and found FALSE, with no RED evidence ever captured for it.

    The TRUE layering, traced through the actual exception-handling chain:
    ``_persist_assistant_message`` (unchanged by this fix round) wraps its
    write in a broad ``except Exception: logger.error(...)`` that never
    re-raises; ``UserHistory.write_assistant_message``'s own ``INSERT`` has
    no ``ON CONFLICT`` clause, and ``messages.id`` is a real
    ``primary_key=True`` column (migration ``0004``), so a genuine SECOND
    ``INSERT`` against the same, already-minted ``message_id`` DOES raise
    ``IntegrityError`` -- but that exception is swallowed exactly where
    described above, never reaching the client and never touching the row
    count (Postgres's own primary key already guarantees at most one row
    exists, guard or no guard). So: **Postgres's primary key is the hard
    backstop against a duplicate ROW; the ``persisted_holder`` flag is what
    prevents the duplicate WRITE ATTEMPT in the first place** -- skipping a
    wasted round trip and a spurious ERROR-level log entry on every normal
    turn's ``finally`` safety-net call, not preventing a row or a 500 that
    the flag was never actually the thing standing between the app and.

    This version discriminates at the layer the flag actually governs,
    using the identical white-box technique already built for
    :func:`test_terminal_frame_is_queued_only_after_the_assistant_row_is_persisted`:
    ``UserHistory.write_assistant_message`` is wrapped to COUNT real calls
    (delegating to the real implementation, never a stand-in), and the
    assertion is on that count -- exactly one -- never on the row count a
    different, pre-existing mechanism already guarantees independent of
    this fix round's own code. The row-count assertion is KEPT below as
    the backstop invariant (shipped behavior is still exactly what a real
    user sees), just no longer the claim being pinned as the flag's own
    proof.
    """
    from poseidon.core.chat.history import UserHistory

    call_count: list[int] = []
    real_write = UserHistory.write_assistant_message

    def counting_write(self, cid, message, turn_id):
        call_count.append(1)
        return real_write(self, cid, message, turn_id)

    monkeypatch.setattr(UserHistory, "write_assistant_message", counting_write)

    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        await read_sse(client, cid, "hello", headers=headers)

        messages = await client.get(f"/api/conversations/{cid}/messages", headers=headers)

    # The flag-governed property: exactly ONE real write ATTEMPT for this
    # turn's own assistant message -- the terminal-frame call site
    # (_finalize_turn, for an ordinary "done" turn) succeeds and the
    # `finally` safety net's own call is skipped, never both firing.
    assert len(call_count) == 1

    # Backstop invariant, unaffected by the flag either way (see the
    # tracing above): shipped behavior still shows exactly one persisted
    # assistant row for this turn, plus the opener from create -- kept so
    # this test still proves what a real user actually sees, even though
    # it is no longer what discriminates the flag's own behavior.
    items = messages.json()["items"]
    assistant_ids = [m["id"] for m in items if m["role"] == "assistant"]
    assert len(assistant_ids) == 2
    assert len(set(assistant_ids)) == 2


@pytest.mark.pg
@pytest.mark.anyio
async def test_terminal_frame_is_queued_only_after_the_assistant_row_is_persisted(
    pg_database_url, monkeypatch
):
    """I-3's own "observe the window directly" pin -- made deterministic at
    the CODE level, not at the test HTTP transport's own timing.

    A first attempt at this test drove ``httpx.ASGITransport`` manually
    (read one SSE line at a time, stop the instant the terminal frame's
    text arrived, query the database via a second, concurrent request
    before ever finishing the first) -- probed directly (see this
    repository's own probe script, reproduced in miniature: a
    ``StreamingResponse`` that yields one chunk, sleeps a full second, then
    yields a second chunk) and found that ``httpx.ASGITransport`` does NOT
    deliver ``StreamingResponse`` chunks progressively at all: the client
    received both chunks concatenated as ONE ``aiter_bytes()`` item, and
    the server-side generator had ALREADY finished (sleep included) before
    the client observed anything. Every attempted "read partway, check
    concurrently" test therefore passed identically whether the underlying
    ordering fix was applied or reverted -- it was testing the test
    transport's own buffering, not this code's ordering, and would not
    have caught the bug it was written for (verified directly: reverting
    just the reorder and re-running that version of this test still
    passed). That approach is abandoned as unsound for this transport
    rather than kept as a test that cannot fail.

    This version instead observes the property that actually determines
    real-world (uvicorn) behavior: within ONE turn, does the call to
    ``UserHistory.write_assistant_message`` (the real database write)
    happen-before the ``queue.Queue.put`` call that enqueues the terminal
    (``"done"``/``"error"``) frame's own text -- the exact statement order
    ``send``/``_finalize_turn``/the ``finally`` safety net now guarantee,
    on the ONE worker thread a turn runs on, regardless of how fast or
    slow any test transport happens to relay bytes afterward. Both calls
    are recorded, in relative order, into one shared list; the persist
    event for this turn's own message id must come first.

    Reverting just the reorder (restoring the pre-fix code that queued the
    terminal frame before ``run_turn_sync``'s own ``finally`` ran the
    persist) makes this exact test FAIL, deterministically, every time --
    verified directly (see task-3-report.md's own Fix round 1 RED
    evidence) -- unlike the abandoned transport-timing approach above.
    """
    import queue as queue_module

    from poseidon.core.chat.history import UserHistory

    event_log: list[tuple[str, str]] = []
    real_write = UserHistory.write_assistant_message
    real_put = queue_module.Queue.put

    def recording_write(self, cid, message, turn_id):
        event_log.append(("persist", message["id"]))
        return real_write(self, cid, message, turn_id)

    def recording_put(self, item, *args, **kwargs):
        if isinstance(item, str) and ("event: done" in item or "event: error" in item):
            event_log.append(("terminal_frame_queued", item))
        return real_put(self, item, *args, **kwargs)

    monkeypatch.setattr(UserHistory, "write_assistant_message", recording_write)
    monkeypatch.setattr(queue_module.Queue, "put", recording_put)

    headers = _headers(_dev_user("alice"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        cid = (await client.post("/api/conversations", headers=headers)).json()["conversation"][
            "id"
        ]
        await read_sse(client, cid, "hello", headers=headers)

    kinds = [kind for kind, _detail in event_log]
    assert "persist" in kinds
    assert "terminal_frame_queued" in kinds
    assert kinds.index("persist") < kinds.index("terminal_frame_queued")
