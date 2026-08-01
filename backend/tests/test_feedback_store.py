"""Tests for Phase 12 Task 1 (doc 06 section 7): migration 0006's
``message_feedback`` table and :mod:`poseidon.core.chat.feedback`'s
``FeedbackStore`` -- the durable replacement for ``core/chat/history.py``'s
honest, in-memory ``FeedbackStubStore`` (deleted by this task). That stub's
own three unit tests (``test_history_store.py``'s ``test_feedback_stub_
store_*``) are ported below as ``test_upsert_amends_...``/``test_get_
returns_none_when_no_feedback_recorded_yet`` -- same round-trip/overwrite/
unknown-id assertions, now against a real store backed by a real table.

Mirrors ``test_runlog_rls.py``'s pg conventions exactly: ``httpx.
ASGITransport`` against a real ``create_app()`` for the ONE test that needs
a genuine HTTP turn (the replay scenario -- ``client_turn_key`` dedup logic
lives inside ``execute_turn``, unreachable through direct store calls
alone); every other test builds its fixture rows directly through
``HistoryStore``/``UserHistory``/``RunLogWriter`` (never a bare INSERT for
CONTENT rows -- ``test_runlog_rls.py``'s own "through the writer" discipline),
then drives ``FeedbackStore`` itself for the behavior under test.

**Why almost every fixture message here needs a real ``turn_run`` row.**
``message_feedback.run_id`` is ``NOT NULL REFERENCES turn_run(id)`` -- doc 06
section 7's own schema, not a relaxable choice -- so a message this suite
gives feedback to (other than the opener, deliberately turnless) needs both
a real ``messages`` row (``turn_id`` set) AND a real ``turn_run`` row at
that SAME id, built through ``RunLogWriter`` exactly like ``test_runlog_
rls.py``'s own ``_full_turn`` helper (:func:`_full_message_with_run` below
is that helper, extended one step further: a linked ``messages`` row, since
feedback attaches to a MESSAGE, not a ``turn_run`` row directly).

**Two-user isolation, disclosed.** This app's conversations are single-owner
(``conversations.user_sub``/``messages.user_sub`` never differ) -- there is
no product feature under which a SECOND real user could ever legitimately
reach the same ``message_id`` through the HTTP surface (RLS makes another
user's message invisible, 404, proven separately below and in ``test_
history_cutover.py``'s own gate test). ``message_feedback``'s unique key is
still ``(message_id, user_sub)``, and its OWNER POLICY only ever constrains
its OWN ``user_sub`` column, never the referenced message's owner -- so
:func:`test_two_users_feedback_on_the_same_message_are_two_rows_each_seeing_
only_their_own` seeds the SECOND user's row directly (bypassing the
messages-visibility gate on purpose, mirroring ``test_turns_reconcile.py``'s
own "build the row directly to isolate exactly the one variable this test
cares about") to prove THIS table's own RLS predicate in isolation from that
gate, which is exercised on its own terms elsewhere in this file.

**The replay test is the one genuine production-code change this task
makes outside the feedback surface itself**, disclosed in full in ``api/
live_chat.py``'s own module docstring ("Phase 12 Task 1: replayed messages
persist under the ORIGINAL run's id") and in this task's report: a
throwaway probe against this environment's own compose Postgres (this
codebase's own "not guessed at" discipline) showed that, before this task,
a replayed message's OWN persisted ``messages.turn_id`` was a fresh,
ORPHANED id (no matching ``turn_run`` row at all) -- ``messages.turn_id``
had no reader anywhere in this codebase before Phase 12, so nothing could
have depended on that value being wrong. :func:`test_feedback_on_a_
replayed_message_links_to_the_original_run` is the test that proves the fix.
"""

import os
import uuid
from pathlib import Path

import httpx
import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.chat import feedback as feedback_module
from poseidon.core.chat.feedback import FeedbackNotApplicable, FeedbackStore
from poseidon.core.chat.history import HistoryStore
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.runlog import RunLogWriter
from poseidon.core.util.uuid7 import uuid7

pytestmark = pytest.mark.pg

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0006)"

_DSN = os.environ.get("DATABASE_URL", "")
if not _DSN:
    pytest.skip(
        f"DATABASE_URL is not set - pg feedback-store tests need a Postgres: {_UP_HINT}, "
        f"{_MIGRATE_HINT}",
        allow_module_level=True,
    )

# Mirrors test_runlog_rls.py's own module-level probe exactly, narrowed to
# "does message_feedback exist" (0006, not merely 0005) -- see that
# module's docstring for why this is a probe, not an assumption.
_DSN_ROLE_IS_SUPERUSER = False
try:
    with psycopg.connect(normalize_dsn(_DSN), connect_timeout=CONNECT_TIMEOUT_SECONDS) as _conn:
        with _conn.cursor() as _cur:
            _cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'message_feedback'"
            )
            if _cur.fetchone() is None:
                pytest.skip(
                    f"message_feedback does not exist - {_MIGRATE_HINT}",
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
_ADMIN_ROLE = "poseidon_admin"
_EFFECTIVE_APP_ROLE = _APP_ROLE if _DSN_ROLE_IS_SUPERUSER else None


# ---------------------------------------------------------------------------
# fixtures and small helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_database_url() -> str:
    return _DSN


@pytest.fixture
def pg_engine():
    from poseidon.core.db import build_engine

    engine = build_engine(_DSN)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _fresh_user_sub() -> str:
    return f"test|{uuid.uuid4().hex}"


def _dev_user(name: str) -> str:
    """A fresh, run-unique ``X-Dev-User`` value -- see ``test_history_
    cutover.py``'s own helper of the same name and the same rationale."""
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
    """A fresh app instance against ``pg_database_url``, backed by the REAL
    ``RunLogWriter`` ``create_app``/``_wire_live_chat`` construct (never
    overridden) -- load-bearing here specifically: a replayed turn's
    ``message_feedback.run_id`` FK needs a genuine ``turn_run`` row, which
    only a real writer ever produces."""
    from poseidon.api.app import create_app

    return create_app(_settings(pg_database_url, **overrides))


async def read_sse(
    client: httpx.AsyncClient,
    cid: str,
    text_: str,
    headers: dict[str, str],
    client_turn_key: str | None = None,
):
    """Mirrors ``test_turns_reconcile.py``'s own ``read_sse`` helper
    exactly -- the wire format is pinned byte-identical (``events.py``'s
    module docstring), so the same parsing logic applies unchanged."""
    import json

    events = []
    body: dict[str, object] = {"text": text_}
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


def _full_message_with_run(engine: Engine, user_sub: str, cid: str) -> tuple[str, str]:
    """One real assistant ``messages`` row plus a matching real ``turn_run``
    row at the SAME id -- mirrors ``test_runlog_rls.py``'s own ``_full_
    turn``, extended one step further (a linked ``messages`` row): feedback
    attaches to a MESSAGE, not a ``turn_run`` row directly. Returns
    ``(message_id, turn_run_id)`` -- always equal, exactly like production's
    own ``sink.turn_id`` shared between the two (``api/live_chat.py``'s own
    "Id minting")."""
    user_history = HistoryStore(engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    turn_id = str(uuid7())
    message_id = str(uuid7())
    user_history.write_assistant_message(
        cid,
        {
            "id": message_id,
            "role": "assistant",
            "parts": [{"kind": "text", "payload": {"markdown": "the answer"}}],
        },
        turn_id,
    )
    writer = RunLogWriter(engine, app_role=_EFFECTIVE_APP_ROLE)
    handle = writer.start_turn(
        user_sub=user_sub,
        conversation_id=cid,
        client_turn_key=str(uuid.uuid4()),
        turn_index=1,
        question="hello",
        mode="default",
        parsed={},
        turn_run_id=turn_id,
    )
    assert handle is not None and handle.created is True, "writer.start_turn must succeed"
    writer.finalize(
        turn_run_id=handle.turn_run_id,
        user_sub=user_sub,
        status="ok",
        message_id=message_id,
        answer_summary="the answer",
        input_tokens=1,
        output_tokens=1,
        latency_ms=5,
    )
    # str(...): a real Postgres RETURNING hands back a genuine uuid.UUID for
    # this native uuid column -- test_runlog_writer.py's own pg suite
    # documents this exact round trip. Normalized once so every caller can
    # compare against a plain string unconditionally.
    return message_id, str(handle.turn_run_id)


# ===========================================================================
# offline: ASCII-on-disk (house rule; matches test_history_and_this_test_
# module_are_ascii_on_disk's own precedent)
# ===========================================================================


def test_feedback_module_and_this_test_module_are_ascii_on_disk():
    for path in (Path(feedback_module.__file__), Path(__file__)):
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ===========================================================================
# catalog assertions -- migration 0006 built what the rest of this file
# relies on (mirrors test_runlog_rls.py's own catalog-assertion section)
# ===========================================================================


def test_row_level_security_is_enabled_and_forced(pg_engine):
    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'message_feedback'"
            )
        ).first()

    assert row is not None, "message_feedback must exist (migration 0006)"
    assert row.relrowsecurity is True
    assert row.relforcerowsecurity is True


def test_owner_policy_exists_with_the_pinned_predicate(pg_engine):
    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT cmd, qual, with_check FROM pg_policies "
                "WHERE tablename = 'message_feedback' AND policyname = 'message_feedback_owner'"
            )
        ).first()

    assert row is not None, "message_feedback_owner policy must exist"
    assert row.cmd == "ALL"
    assert "current_setting" in row.qual and "app.user_sub" in row.qual and "user_sub" in row.qual
    assert (
        "current_setting" in row.with_check
        and "app.user_sub" in row.with_check
        and "user_sub" in row.with_check
    )


def test_admin_select_only_policy_exists_with_using_true(pg_engine):
    with pg_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT cmd, roles, qual FROM pg_policies WHERE tablename = 'message_feedback' "
                "AND policyname = 'message_feedback_admin_read'"
            )
        ).first()

    assert row is not None, "message_feedback_admin_read policy must exist"
    assert row.cmd == "SELECT"
    assert list(row.roles) == [_ADMIN_ROLE]
    assert row.qual.strip() == "true"


def test_poseidon_app_has_select_insert_update_but_not_delete(pg_engine):
    """Doc 06 section 7 / D28: removal happens only via the messages FK
    cascade (an RI action that bypasses RLS by design) -- the runtime role
    itself never gets DELETE on this table."""
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = :role AND table_name = 'message_feedback'"
            ),
            {"role": _APP_ROLE},
        ).all()

    assert {row.privilege_type for row in rows} == {"SELECT", "INSERT", "UPDATE"}


def test_poseidon_admin_has_exactly_select_privilege(pg_engine):
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT privilege_type FROM information_schema.table_privileges "
                "WHERE grantee = :role AND table_name = 'message_feedback'"
            ),
            {"role": _ADMIN_ROLE},
        ).all()

    assert {row.privilege_type for row in rows} == {"SELECT"}


# ===========================================================================
# upsert-amend: ported from test_history_store.py's own FeedbackStubStore
# round-trip/overwrite tests, now against the real store (same assertions,
# extended with created_at/updated_at/one-row proof the stub had no columns
# for at all)
# ===========================================================================


def test_upsert_amends_verdict_and_comment_keeping_created_at_stable_and_moving_updated_at(
    pg_engine,
):
    user_sub = _fresh_user_sub()
    conversation, _opener = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(
        user_sub
    ).create_conversation()
    message_id, turn_run_id = _full_message_with_run(pg_engine, user_sub, conversation["id"])
    store = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    store.upsert(message_id, "down", "wrong port")

    assert store.get(message_id) == {"verdict": "down", "comment": "wrong port"}
    with pg_engine.connect() as conn:
        first = conn.execute(
            text(
                "SELECT id, run_id, created_at, updated_at FROM message_feedback "
                "WHERE message_id = :m"
            ),
            {"m": message_id},
        ).first()
    assert str(first.run_id) == turn_run_id

    store.upsert(message_id, "up", None)

    assert store.get(message_id) == {"verdict": "up", "comment": None}
    with pg_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, created_at, updated_at FROM message_feedback WHERE message_id = :m"),
            {"m": message_id},
        ).all()

    assert len(rows) == 1, "amend must stay ONE row via the (message_id, user_sub) unique key"
    second = rows[0]
    assert second.id == first.id
    assert second.created_at == first.created_at, "created_at keeps first-write time"
    assert second.updated_at > first.updated_at, "updated_at moves on amend"


def test_get_returns_none_when_no_feedback_recorded_yet(pg_engine):
    """Ported from FeedbackStubStore's own ``test_feedback_stub_store_get_
    feedback_unknown_mid_returns_none`` -- same "nothing recorded yet"
    outcome, now proven against a real, VISIBLE message (the stub could not
    tell visible from unknown at all; this store can, and this is
    specifically the visible-but-no-verdict-yet case, not the invisible
    one -- see the LookupError tests below for that)."""
    user_sub = _fresh_user_sub()
    conversation, _opener = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(
        user_sub
    ).create_conversation()
    message_id, _turn_run_id = _full_message_with_run(pg_engine, user_sub, conversation["id"])
    store = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    assert store.get(message_id) is None


def test_get_returns_none_for_a_malformed_or_absent_message_id(pg_engine):
    store = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(_fresh_user_sub())

    assert store.get("not-a-uuid") is None
    assert store.get(str(uuid.uuid4())) is None


# ===========================================================================
# two-user isolation -- see module docstring for why the second row is
# seeded directly rather than driven through a second upsert() call
# ===========================================================================


def test_two_users_feedback_on_the_same_message_are_two_rows_each_seeing_only_their_own(pg_engine):
    sub_a = _fresh_user_sub()
    sub_b = _fresh_user_sub()
    conversation, _opener = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(
        sub_a
    ).create_conversation()
    message_id, turn_run_id = _full_message_with_run(pg_engine, sub_a, conversation["id"])
    store = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE)

    store.for_user(sub_a).upsert(message_id, "up", None)
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO message_feedback (id, message_id, run_id, user_sub, verdict, comment) "
                "VALUES (:id, :message_id, :run_id, :user_sub, :verdict, :comment)"
            ),
            {
                "id": str(uuid7()),
                "message_id": message_id,
                "run_id": turn_run_id,
                "user_sub": sub_b,
                "verdict": "down",
                "comment": "wrong customer",
            },
        )

    assert store.for_user(sub_a).get(message_id) == {"verdict": "up", "comment": None}
    assert store.for_user(sub_b).get(message_id) == {
        "verdict": "down",
        "comment": "wrong customer",
    }
    with pg_engine.connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM message_feedback WHERE message_id = :m"), {"m": message_id}
        ).scalar_one()
    assert count == 2


# ===========================================================================
# invisible message -> LookupError (route maps this to 404)
# ===========================================================================


def test_upsert_raises_lookuperror_for_a_never_minted_message_id(pg_engine):
    store = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(_fresh_user_sub())

    with pytest.raises(LookupError):
        store.upsert(str(uuid.uuid4()), "up", None)


def test_upsert_raises_lookuperror_for_a_malformed_message_id(pg_engine):
    store = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(_fresh_user_sub())

    with pytest.raises(LookupError):
        store.upsert("not-a-uuid", "up", None)


def test_upsert_raises_lookuperror_for_another_users_real_message(pg_engine):
    owner_sub = _fresh_user_sub()
    stranger_sub = _fresh_user_sub()
    conversation, _opener = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(
        owner_sub
    ).create_conversation()
    message_id, _turn_run_id = _full_message_with_run(pg_engine, owner_sub, conversation["id"])
    stranger_feedback = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(
        stranger_sub
    )

    with pytest.raises(LookupError):
        stranger_feedback.upsert(message_id, "up", None)


# ===========================================================================
# opener / NULL turn_id -> FeedbackNotApplicable (route maps this to a
# byte-pinned 422 RFC-7807 problem, title "feedback_not_applicable")
# ===========================================================================


def test_upsert_raises_feedbacknotapplicable_for_the_opener_message(pg_engine):
    user_sub = _fresh_user_sub()
    _conversation, opener = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(
        user_sub
    ).create_conversation()
    store = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    with pytest.raises(FeedbackNotApplicable):
        store.upsert(opener["id"], "up", None)


def test_feedbacknotapplicable_is_a_valueerror_subclass():
    """Mirrors MalformedCursor's own precedent (core/chat/history.py) --
    a typed ValueError subclass, not a bare custom exception, so any
    pre-existing broad ``except ValueError`` keeps working unchanged."""
    assert issubclass(FeedbackNotApplicable, ValueError)


@pytest.mark.anyio
async def test_feedback_on_the_opener_returns_422_byte_pinned(pg_database_url):
    headers = _headers(_dev_user("carol"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        create_resp = (await client.post("/api/conversations", headers=headers)).json()
        opener_id = create_resp["opener"]["id"]

        r = await client.post(
            f"/api/messages/{opener_id}/feedback", json={"verdict": "up"}, headers=headers
        )

    assert r.status_code == 422
    assert r.json() == {
        "type": "about:blank",
        "title": "feedback_not_applicable",
        "detail": (
            f"message {opener_id!r} has no linked turn (the opener) - "
            "feedback is not applicable"
        ),
        "status": 422,
    }


# ===========================================================================
# conversation delete cascades feedback rows (proves the RI-bypass-RLS
# cascade mechanism P10 established, now one hop further)
# ===========================================================================


def test_conversation_delete_cascades_to_feedback_rows(pg_engine):
    user_sub = _fresh_user_sub()
    user_history = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    conversation, _opener = user_history.create_conversation()
    message_id, _turn_run_id = _full_message_with_run(pg_engine, user_sub, conversation["id"])
    store = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)
    store.upsert(message_id, "down", "wrong")
    with pg_engine.connect() as conn:
        before = conn.execute(
            text("SELECT COUNT(*) FROM message_feedback WHERE message_id = :m"), {"m": message_id}
        ).scalar_one()
    assert before == 1

    deleted = user_history.delete_conversation(conversation["id"])

    assert deleted is True
    with pg_engine.connect() as conn:
        after = conn.execute(
            text("SELECT COUNT(*) FROM message_feedback WHERE message_id = :m"), {"m": message_id}
        ).scalar_one()
    assert after == 0


# ===========================================================================
# run_id resolution
# ===========================================================================


def test_upsert_resolves_run_id_from_the_messages_turn_id(pg_engine):
    user_sub = _fresh_user_sub()
    conversation, _opener = HistoryStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(
        user_sub
    ).create_conversation()
    message_id, turn_run_id = _full_message_with_run(pg_engine, user_sub, conversation["id"])
    store = FeedbackStore(pg_engine, app_role=_EFFECTIVE_APP_ROLE).for_user(user_sub)

    store.upsert(message_id, "up", None)

    with pg_engine.connect() as conn:
        stored_run_id = conn.execute(
            text("SELECT run_id FROM message_feedback WHERE message_id = :m"), {"m": message_id}
        ).scalar_one()
    assert str(stored_run_id) == turn_run_id


# ===========================================================================
# replay: feedback on a replayed message links to the ORIGINAL run, with no
# special-casing anywhere in FeedbackStore itself -- see module docstring
# ===========================================================================


@pytest.mark.anyio
async def test_feedback_on_a_replayed_message_links_to_the_original_run(
    pg_database_url, pg_engine
):
    headers = _headers(_dev_user("dana"))
    app = _app(pg_database_url)
    transport = httpx.ASGITransport(app=app)
    replay_key = str(uuid.uuid4())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        create_resp = (await client.post("/api/conversations", headers=headers)).json()
        cid = create_resp["conversation"]["id"]
        opener_id = create_resp["opener"]["id"]

        first_events = await read_sse(client, cid, "hello", headers, client_turn_key=replay_key)
        first_turn_id = first_events[0][1]["turn_id"]

        replay_events = await read_sse(client, cid, "hello", headers, client_turn_key=replay_key)
        replay_turn_id = replay_events[0][1]["turn_id"]
        replay_done = next(payload for name, payload in replay_events if name == "done")
        # P11's own pinned contract (test_turns_reconcile.py): the replay
        # response is its OWN fresh SSE envelope, never the original's --
        # this is exactly the id message_feedback.run_id must NOT end up
        # pointing at.
        assert replay_turn_id != first_turn_id
        assert replay_done["replayed"] is True

        msgs = (await client.get(f"/api/conversations/{cid}/messages", headers=headers)).json()[
            "items"
        ]
        assistant_msgs = [m for m in msgs if m["role"] == "assistant" and m["id"] != opener_id]
        assert len(assistant_msgs) == 2, "the original turn's answer + the replay's own new row"
        replay_message_id = assistant_msgs[-1]["id"]

        r = await client.post(
            f"/api/messages/{replay_message_id}/feedback",
            json={"verdict": "up", "comment": "still right"},
            headers=headers,
        )
        assert r.status_code == 204

    with pg_engine.connect() as conn:
        stored_run_id = conn.execute(
            text("SELECT run_id FROM message_feedback WHERE message_id = :m"),
            {"m": replay_message_id},
        ).scalar_one()

    assert str(stored_run_id) == first_turn_id
