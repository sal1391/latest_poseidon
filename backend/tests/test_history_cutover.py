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
