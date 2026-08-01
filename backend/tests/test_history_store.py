"""Tests for Phase 10 Task 2 (doc 08): :mod:`poseidon.core.chat.history` --
the Postgres-backed replacement for ``api/live_chat.py``'s in-memory
``TranscriptStore`` (conversations/messages) and
``core/chat/state.py``'s in-memory ``ConversationStateStore`` (per-
conversation slots/turn-index/brief-done).

Two halves, split the same way ``test_runlog_writer.py`` splits itself
(mixed offline+pg IN ONE FILE, per-test ``@pytest.mark.pg`` rather than a
module-level skip -- the task brief names exactly one new test module, and
half of what it covers needs no database at all):

- **Offline** (always run, zero network): the slots serializer
  (``slots_to_json``/``slots_from_json``) is pure Python, dict in dict out;
  ``TurnTranscriptBuffer`` is a pure in-memory fold with no engine anywhere
  in its call chain; ``FeedbackStubStore`` is a plain dict behind a lock.
  None of these three need Postgres to prove correct.
- **``pg``** (``@pytest.mark.pg``, skipped without a reachable, migrated-to-
  0004 Postgres): everything that actually depends on row-level security to
  prove itself right -- ``HistoryStore``/``UserHistory``/``DbStateStore``.
  There is no meaningful fake for RLS (it is a real Postgres feature, not a
  SQL-shape-recording double the way ``test_runlog_writer.py``'s
  ``_RecordingEngine`` stands in for migration 0003's tables, which carry no
  RLS at all) -- the brief's own Step 1 list (cursor pagination, cross-user
  invisibility, restart survival, jsonb round trips) is inherently a pg-only
  list.

**Cleanup pattern.** Unlike ``test_rls_policies.py`` (which deletes every
row it creates through an identity-scoped teardown), this file follows
``test_runlog_writer.py``'s OTHER precedent instead: fresh, unique
``user_sub`` values (``f"test|{uuid4().hex}"``) per test, rows left behind
uncleaned. Two reasons this is the right call here specifically, not just
laziness: (1) ``conversations``/``messages`` carry no unique constraint a
leftover row could ever collide with (unlike ``turn_run``'s ``(user_sub,
client_turn_key)``), so accumulation is purely a disk-space cost on a dev
database, never a correctness risk; (2) RLS itself already guarantees no
later test can ever SEE an earlier test's rows regardless of cleanup --
these tests would isolate correctly even if this file were run in an
infinite loop against the same long-lived compose Postgres. ``HistoryStore``
also exposes no delete method (not in the brief's interface list), so
cleanup would mean either inventing one out of scope or reaching around the
store with raw SQL the way ``test_rls_policies.py`` does; given (1) and (2)
above, that extra code would buy nothing.

**``pg_engine``/``effective_app_role`` fixtures** mirror ``test_rls_policies.
py``'s own module-level guard and ``_EFFECTIVE_APP_ROLE`` computation
exactly, just as pytest FIXTURES instead of module-level globals (like
``test_runlog_writer.py``'s ``pg_engine``, for the same reason: this file
also holds offline tests that must always run, so nothing pg-related may
run at IMPORT time). ``poseidon_app`` is the round-0-correction role
``core/db.py``'s module docstring describes: this dev compose database's
``DATABASE_URL`` role is the cluster's bootstrap superuser, which
unconditionally bypasses RLS unless ``rls_transaction`` is also told to
``SET LOCAL ROLE`` to a genuine non-superuser role first.
"""

import base64
import json
import os
import string
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import create_engine, text

from poseidon.core.chat import history
from poseidon.core.chat.history import (
    DbStateStore,
    FeedbackStubStore,
    HistoryStore,
    MalformedCursor,
    TurnTranscriptBuffer,
    UserHistory,
    slots_from_json,
    slots_to_json,
)
from poseidon.core.data.synthetic_client import normalize_dsn
from poseidon.core.skills.context import ConversationSlots
from poseidon.core.util.uuid7 import uuid7

# ===========================================================================
# offline: slots_to_json / slots_from_json
# ===========================================================================


def test_slots_to_json_and_back_round_trips_a_populated_instance():
    """Dates become ISO strings, pass_through becomes a list of pairs -- the
    two shapes that cannot survive a raw dataclasses.asdict() through
    json.dumps unchanged."""
    slots = ConversationSlots(
        customer="ACME MARINE",
        port="SGSIN",
        period_a=date(2026, 4, 1),
        period_b=date(2026, 3, 1),
        mode="existing",
        region="APAC",
        topic="gp",
        pass_through=(("customer", "ACME MARINE"), ("port", "SGSIN")),
    )

    raw = slots_to_json(slots)

    assert raw == {
        "customer": "ACME MARINE",
        "port": "SGSIN",
        "period_a": "2026-04-01",
        "period_b": "2026-03-01",
        "mode": "existing",
        "region": "APAC",
        "topic": "gp",
        "pass_through": [["customer", "ACME MARINE"], ["port", "SGSIN"]],
    }
    assert slots_from_json(raw) == slots


def test_slots_to_json_and_back_round_trips_the_empty_default_instance():
    slots = ConversationSlots()

    raw = slots_to_json(slots)

    assert raw == {
        "customer": None,
        "port": None,
        "period_a": None,
        "period_b": None,
        "mode": "default",
        "region": None,
        "topic": None,
        "pass_through": [],
    }
    assert slots_from_json(raw) == slots


def test_slots_from_json_ignores_unknown_keys():
    """Forward compatibility: a key a NEWER app version wrote (this one has
    never heard of) must not raise and must not affect any known field."""
    raw = {"mode": "prospect", "totally_unknown_future_field": {"nested": True}}

    assert slots_from_json(raw) == ConversationSlots(mode="prospect")


def test_slots_from_json_defaults_missing_keys_like_the_dataclass():
    assert slots_from_json({}) == ConversationSlots()


# ===========================================================================
# offline: TurnTranscriptBuffer -- ported from api/live_chat.py's
# TranscriptStore (start_assistant_message/append_part/record_tool_event/
# fold_token), the dict-of-conversations removed per the task brief.
# ===========================================================================


def test_turn_transcript_buffer_start_assistant_message_returns_empty_parts_dict():
    buffer = TurnTranscriptBuffer()

    assistant = buffer.start_assistant_message("msg-1")

    assert assistant == {"id": "msg-1", "role": "assistant", "parts": []}


def test_turn_transcript_buffer_append_part_appends_verbatim():
    buffer = TurnTranscriptBuffer()
    assistant = buffer.start_assistant_message("msg-1")

    buffer.append_part(assistant, {"kind": "table", "payload": {"columns": [], "rows": []}})

    assert assistant["parts"] == [{"kind": "table", "payload": {"columns": [], "rows": []}}]


def test_turn_transcript_buffer_fold_token_starts_a_new_text_part():
    buffer = TurnTranscriptBuffer()
    assistant = buffer.start_assistant_message("msg-1")

    buffer.fold_token(assistant, "hello")

    assert assistant["parts"] == [{"kind": "text", "payload": {"markdown": "hello"}}]


def test_turn_transcript_buffer_fold_token_concatenates_onto_the_trailing_text_part():
    buffer = TurnTranscriptBuffer()
    assistant = buffer.start_assistant_message("msg-1")
    buffer.fold_token(assistant, "hello")

    buffer.fold_token(assistant, " world")

    assert assistant["parts"] == [{"kind": "text", "payload": {"markdown": "hello world"}}]


def test_turn_transcript_buffer_fold_token_starts_a_new_part_after_a_non_text_part():
    buffer = TurnTranscriptBuffer()
    assistant = buffer.start_assistant_message("msg-1")
    buffer.append_part(assistant, {"kind": "table", "payload": {"columns": [], "rows": []}})

    buffer.fold_token(assistant, "hello")

    assert len(assistant["parts"]) == 2
    assert assistant["parts"][-1] == {"kind": "text", "payload": {"markdown": "hello"}}


def test_turn_transcript_buffer_record_tool_event_position_matches_the_live_views_own_rule():
    """Ported from test_live_chat_sse.py's own
    test_record_transcript_frame_tool_event_position_matches_the_live_views_own_rule
    (the ONE existing test that exercises TranscriptStore's fold methods
    directly, unit-level, rather than through a full HTTP turn). Same
    assertions, not weakened: a tool_seq not yet seen is PUSHED; the SAME
    tool_seq seen again is REPLACED IN PLACE, at its existing position,
    never re-appended -- so a part streamed between "start" and "done"
    lands AFTER the tool_event, matching the live view's own order.

    Exercised directly against TurnTranscriptBuffer's methods with
    envelope-stripped payloads (what api/live_chat.py's frame decoding would
    have already extracted), rather than via a real SseEnvelopeSink -- SSE
    frame decoding is api/live_chat.py's concern (Task 3), not this store's.
    """
    buffer = TurnTranscriptBuffer()
    assistant = buffer.start_assistant_message("msg-1")
    early_part = {"kind": "metric_grid", "payload": {"periods": {}, "metrics": []}}

    buffer.record_tool_event(
        assistant,
        {"tool_seq": 1, "tool": "customer_insight.existing_customer_brief", "status": "running"},
    )
    buffer.append_part(assistant, early_part)
    buffer.record_tool_event(
        assistant,
        {
            "tool_seq": 1,
            "tool": "customer_insight.existing_customer_brief",
            "status": "done",
            "duration_ms": 1,
        },
    )

    kinds = [p["kind"] for p in assistant["parts"]]
    assert kinds == ["tool_event", "metric_grid"]
    tool_event = assistant["parts"][0]["payload"]
    # replaced in place, not appended a second time -- the FINAL status is
    # "done", carried by the SAME single part this dispatch ever gets.
    assert tool_event["status"] == "done"
    assert tool_event["tool"] == "customer_insight.existing_customer_brief"
    assert assistant["parts"][1] == early_part


# ===========================================================================
# offline: FeedbackStubStore -- today's _feedback dict + lock, extracted
# verbatim (the "is this mid known" existence check is dropped: it read the
# old TranscriptStore's _messages dict, which this store never held).
# ===========================================================================


def test_feedback_stub_store_round_trip():
    store = FeedbackStubStore()

    store.upsert_feedback("msg-1", "down", "wrong port")

    assert store.get_feedback("msg-1") == {"verdict": "down", "comment": "wrong port"}


def test_feedback_stub_store_upsert_overwrites_the_previous_verdict():
    store = FeedbackStubStore()
    store.upsert_feedback("msg-1", "down", "wrong port")

    store.upsert_feedback("msg-1", "up", None)

    assert store.get_feedback("msg-1") == {"verdict": "up", "comment": None}


def test_feedback_stub_store_get_feedback_unknown_mid_returns_none():
    assert FeedbackStubStore().get_feedback("nope") is None


# ===========================================================================
# offline: ASCII-on-disk (house rule; matches
# test_rls_policies_module_is_ascii_on_disk / test_runlog_module_is_ascii_on_disk)
# ===========================================================================


def test_history_and_this_test_module_are_ascii_on_disk():
    for path in (Path(history.__file__), Path(__file__)):
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ===========================================================================
# offline: MalformedCursor -- Fix round 1, Important Finding 1. The decode
# step runs entirely in Python BEFORE list_conversations/get_messages ever
# open an rls_transaction (confirmed by reading the source: the decode call
# sits above the `with self._transaction()` line in both methods), so every
# case below needs no real engine at all -- a placeholder object() proves
# the point: if the decode raised any later than claimed, these tests would
# blow up on a real attribute access against a non-engine object instead of
# cleanly raising MalformedCursor.
# ===========================================================================


def _offline_user_history() -> UserHistory:
    """A UserHistory whose ``engine`` is never touched for any of the cases
    below -- see the section banner above."""
    return UserHistory(object(), "offline-test-sub")


def _b64_json(payload: object) -> str:
    """Base64-encode an arbitrary JSON-serializable ``payload`` the same
    way ``_encode_cursor`` does, but WITHOUT going through this module's
    own key shape -- lets these tests build cursors ``_encode_cursor``
    itself would never produce (a non-dict payload, missing keys, wrong
    value types) while still being valid base64-encoded JSON."""
    raw = json.dumps(payload).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64_not_json() -> str:
    """Valid urlsafe-base64 whose decoded bytes are NOT valid JSON at
    all -- the "valid base64 but not JSON" matrix cell."""
    return base64.urlsafe_b64encode(b"not json at all").decode("ascii")


# The matrix Important Finding 1 asked for, by category: not-base64; valid
# base64 but not JSON; valid JSON but not even a dict (a list, a bare
# string); a dict missing the expected keys; a dict with the right keys but
# wrong value types (an int where a date/uuid string belongs; null).
_MALFORMED_CONVERSATIONS_CURSORS = [
    pytest.param("not-base64", id="not-base64"),
    pytest.param(_b64_not_json(), id="valid-b64-not-json"),
    pytest.param(_b64_json([1, 2, 3]), id="json-is-a-list-not-a-dict"),
    pytest.param(_b64_json("just a string"), id="json-is-a-string-not-a-dict"),
    pytest.param(_b64_json({"x": 1}), id="missing-both-u-and-i-keys"),
    pytest.param(_b64_json({"i": str(uuid.uuid4())}), id="missing-u-key"),
    pytest.param(_b64_json({"u": "2026-04-01T00:00:00"}), id="missing-i-key"),
    pytest.param(_b64_json({"u": 12345, "i": str(uuid.uuid4())}), id="u-has-the-wrong-type"),
    pytest.param(
        _b64_json({"u": "2026-04-01T00:00:00", "i": 12345}), id="i-has-the-wrong-type"
    ),
    pytest.param(_b64_json({"u": "2026-04-01T00:00:00", "i": None}), id="i-is-null"),
    pytest.param(_b64_json({"u": "not a real date", "i": str(uuid.uuid4())}), id="u-is-not-iso"),
    pytest.param(_b64_json({"u": "2026-04-01T00:00:00", "i": "not-a-uuid"}), id="i-is-not-a-uuid"),
]


@pytest.mark.parametrize("bad_cursor", _MALFORMED_CONVERSATIONS_CURSORS)
def test_list_conversations_raises_malformedcursor_for_undecodable_cursors(bad_cursor):
    user_history = _offline_user_history()

    with pytest.raises(MalformedCursor):
        user_history.list_conversations(cursor=bad_cursor)


_MALFORMED_MESSAGES_CURSORS = [
    pytest.param("not-base64", id="not-base64"),
    pytest.param(_b64_not_json(), id="valid-b64-not-json"),
    pytest.param(_b64_json([1, 2, 3]), id="json-is-a-list-not-a-dict"),
    pytest.param(_b64_json("just a string"), id="json-is-a-string-not-a-dict"),
    pytest.param(_b64_json({"x": 1}), id="missing-both-c-and-i-keys"),
    pytest.param(_b64_json({"i": str(uuid.uuid4())}), id="missing-c-key"),
    pytest.param(_b64_json({"c": "2026-04-01T00:00:00"}), id="missing-i-key"),
    pytest.param(_b64_json({"c": 12345, "i": str(uuid.uuid4())}), id="c-has-the-wrong-type"),
    pytest.param(
        _b64_json({"c": "2026-04-01T00:00:00", "i": 12345}), id="i-has-the-wrong-type"
    ),
    pytest.param(_b64_json({"c": "2026-04-01T00:00:00", "i": None}), id="i-is-null"),
    pytest.param(_b64_json({"c": "not a real date", "i": str(uuid.uuid4())}), id="c-is-not-iso"),
    pytest.param(_b64_json({"c": "2026-04-01T00:00:00", "i": "not-a-uuid"}), id="i-is-not-a-uuid"),
]


@pytest.mark.parametrize("bad_cursor", _MALFORMED_MESSAGES_CURSORS)
def test_get_messages_raises_malformedcursor_for_undecodable_cursors(bad_cursor):
    user_history = _offline_user_history()

    with pytest.raises(MalformedCursor):
        user_history.get_messages(str(uuid.uuid4()), cursor=bad_cursor)


def test_get_messages_raises_malformedcursor_before_checking_conversation_visibility():
    """The cursor decode sits ABOVE the exists-check in get_messages's own
    source, so a malformed cursor against a conversation id that would
    otherwise 404 (never created, or another user's) still raises
    MalformedCursor rather than returning None -- the two failure modes
    are orthogonal, and the malformed input the CALLER built (the cursor)
    takes precedence. No pg needed: a placeholder engine plus an id that
    has never been created proves the raise happens before any query."""
    user_history = _offline_user_history()

    with pytest.raises(MalformedCursor):
        user_history.get_messages(str(uuid.uuid4()), cursor="not-base64")


def test_malformedcursor_is_a_valueerror_subclass():
    """A typed exception, not a bare one -- Task 3 catches this specific
    type to map it to a 400 RFC-7807 problem detail, and the ValueError
    parentage means any existing broad `except ValueError` a caller
    already has keeps working unchanged."""
    assert issubclass(MalformedCursor, ValueError)


# ===========================================================================
# offline: limit < 1 -- final-review wave, I-1. Same "no engine touched"
# proof as the MalformedCursor cases above: the guard runs in pure Python
# before either method ever opens a transaction, so an object() engine
# proves it -- if the raise happened any later, these would blow up on a
# real attribute access against a non-engine object instead of cleanly
# raising ValueError. The HTTP route layer (api/live_chat.py) already
# bounds `limit` with FastAPI's own Query(ge=1, ...), so this is the
# "belt" for a caller of UserHistory directly, closing the IndexError
# history.py's own page[-1] lines used to raise for limit <= 0.
# ===========================================================================


@pytest.mark.parametrize("bad_limit", [0, -1, -100], ids=["zero", "negative-one", "very-negative"])
def test_list_conversations_raises_valueerror_for_limit_less_than_1(bad_limit):
    user_history = _offline_user_history()

    with pytest.raises(ValueError, match="limit"):
        user_history.list_conversations(limit=bad_limit)


@pytest.mark.parametrize("bad_limit", [0, -1, -100], ids=["zero", "negative-one", "very-negative"])
def test_get_messages_raises_valueerror_for_limit_less_than_1(bad_limit):
    user_history = _offline_user_history()

    with pytest.raises(ValueError, match="limit"):
        user_history.get_messages(str(uuid.uuid4()), limit=bad_limit)


# ===========================================================================
# pg fixtures -- mirrors test_rls_policies.py's own guard/role computation,
# adapted to a fixture (test_runlog_writer.py's shape) since this file also
# holds offline tests that must always run.
# ===========================================================================

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_MIGRATE_HINT = "migrate it with `python -m alembic upgrade head` (revision 0004)"
_APP_ROLE = "poseidon_app"


@pytest.fixture
def pg_engine():
    """A real ``Engine`` against ``DATABASE_URL``, or a SKIP (never a
    module-level skip -- see the module docstring) with an actionable
    reason: unset, unreachable within 2 seconds, or reachable but not yet
    migrated to 0004."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        pytest.skip(
            f"DATABASE_URL is not set - pg history store tests need a Postgres: "
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
    engine = create_engine(dsn)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def effective_app_role(pg_engine):
    """``poseidon_app`` when ``DATABASE_URL``'s role is a cluster superuser
    (this dev compose database's own bootstrap quirk -- ``core/db.py``'s
    module docstring, "round-0 correction"), ``None`` otherwise -- exactly
    what a real deploy's ``Settings.database_app_role`` resolves to, and
    what ``test_rls_policies.py``'s own ``_EFFECTIVE_APP_ROLE`` computes."""
    with pg_engine.connect() as conn:
        is_superuser = conn.execute(
            text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
        ).scalar_one()
    return _APP_ROLE if is_superuser else None


@pytest.fixture
def history_store(pg_engine, effective_app_role):
    return HistoryStore(pg_engine, app_role=effective_app_role)


def _fresh_user_sub() -> str:
    """A ``user_sub`` unique to this test invocation (``test_rls_policies.
    py``'s own pinned pattern) -- so re-running this suite against a
    long-lived dev Postgres never collides with a previous run's rows."""
    return f"test|{uuid.uuid4().hex}"


# ===========================================================================
# pg: create_conversation
# ===========================================================================


@pytest.mark.pg
def test_create_conversation_mints_uuid7_ids(history_store):
    user_history = history_store.for_user(_fresh_user_sub())

    conversation, opener = user_history.create_conversation()

    assert uuid.UUID(conversation["id"]).version == 7
    assert uuid.UUID(opener["id"]).version == 7


@pytest.mark.pg
def test_create_conversation_matches_transcriptstores_wire_shape(history_store):
    """Byte-compatible with today's TranscriptStore.create_conversation --
    see live_chat.py:292-336 and test_live_chat_sse.py's own
    test_post_conversations_returns_the_same_opener_shape_as_mock /
    test_opener_flow_chips_carry_the_d19_pinned_entry_phrases_as_send_text."""
    user_history = history_store.for_user(_fresh_user_sub())

    conversation, opener = user_history.create_conversation()

    assert set(conversation) == {"id", "title"}
    assert conversation["title"] == "New chat"
    assert opener["role"] == "assistant"
    assert [p["kind"] for p in opener["parts"]] == ["text", "chips"]
    assert opener["parts"][1]["payload"]["options"] == [
        {
            "id": "existing_customer",
            "label": "Existing customer",
            "send_text": "start an existing-customer brief",
        },
        {
            "id": "new_prospect",
            "label": "New customer prospect",
            "send_text": "start a new-prospect brief",
        },
    ]


# ===========================================================================
# pg: list_conversations -- cursor pagination
# ===========================================================================


@pytest.mark.pg
def test_list_conversations_pagination_seven_rows_page_size_three(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    created_ids = [user_history.create_conversation()[0]["id"] for _ in range(7)]
    expected_order = list(reversed(created_ids))  # updated_at DESC, id DESC: newest first

    page1, cursor1 = user_history.list_conversations(limit=3)
    page2, cursor2 = user_history.list_conversations(limit=3, cursor=cursor1)
    page3, cursor3 = user_history.list_conversations(limit=3, cursor=cursor2)

    assert [c["id"] for c in page1] == expected_order[0:3]
    assert [c["id"] for c in page2] == expected_order[3:6]
    assert [c["id"] for c in page3] == expected_order[6:7]
    assert cursor1 is not None
    assert cursor2 is not None
    assert cursor3 is None


@pytest.mark.pg
def test_list_conversations_cursor_is_opaque_urlsafe_base64_text(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    for _ in range(2):
        user_history.create_conversation()

    _items, cursor = user_history.list_conversations(limit=1)

    assert cursor is not None
    allowed = set(string.ascii_letters + string.digits + "-_=")
    assert set(cursor) <= allowed


@pytest.mark.pg
def test_list_conversations_with_a_well_formed_but_absurd_cursor_returns_an_empty_page(
    history_store,
):
    """The other half of Important Finding 1's matrix: a cursor that
    decodes and parses cleanly (valid base64, valid JSON, right keys,
    right value TYPES) but whose values were never actually issued by
    _encode_cursor -- here, a timestamp from before this fresh user_sub
    ever created anything -- is NOT a MalformedCursor. Keyset pagination
    treats any well-typed value as a legitimate continuation point: no
    row's updated_at is less than 1900, so the DESC "less than cursor"
    predicate matches nothing, and the page comes back empty rather than
    raising."""
    user_history = history_store.for_user(_fresh_user_sub())
    user_history.create_conversation()
    absurd_cursor = _b64_json({"u": "1900-01-01T00:00:00", "i": str(uuid.uuid4())})

    items, next_cursor = user_history.list_conversations(cursor=absurd_cursor)

    assert items == []
    assert next_cursor is None


# ===========================================================================
# pg: get_messages -- cursor pagination + cross-user invisibility
# ===========================================================================


@pytest.mark.pg
def test_get_messages_pagination_seven_rows_page_size_three(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, opener = user_history.create_conversation()
    cid = conversation["id"]
    expected_order = [opener["id"]]
    for i in range(6):
        message_id = str(uuid7())
        user_history.append_user_message(cid, message_id, f"message {i}", None)
        expected_order.append(message_id)

    page1, cursor1 = user_history.get_messages(cid, limit=3)
    page2, cursor2 = user_history.get_messages(cid, limit=3, cursor=cursor1)
    page3, cursor3 = user_history.get_messages(cid, limit=3, cursor=cursor2)

    assert [m["id"] for m in page1] == expected_order[0:3]
    assert [m["id"] for m in page2] == expected_order[3:6]
    assert [m["id"] for m in page3] == expected_order[6:7]
    assert cursor1 is not None
    assert cursor2 is not None
    assert cursor3 is None


@pytest.mark.pg
def test_get_messages_with_a_well_formed_but_absurd_cursor_returns_an_empty_page(history_store):
    """Mirrors test_list_conversations_with_a_well_formed_but_absurd_cursor_
    returns_an_empty_page for the ASC-ordered side: a cursor timestamped
    far in the future decodes and parses cleanly, but no message's
    created_at is greater than it, so the "after cursor" predicate matches
    nothing -- an empty, legitimate page, not a MalformedCursor."""
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()
    absurd_cursor = _b64_json({"c": "2099-01-01T00:00:00", "i": str(uuid.uuid4())})

    result = user_history.get_messages(conversation["id"], cursor=absurd_cursor)

    assert result is not None
    items, next_cursor = result
    assert items == []
    assert next_cursor is None


@pytest.mark.pg
def test_get_messages_returns_none_for_another_users_conversation(history_store):
    owner_history = history_store.for_user(_fresh_user_sub())
    other_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = owner_history.create_conversation()

    assert other_history.get_messages(conversation["id"]) is None
    # the owner still sees it -- proves the None above is RLS, not a bug.
    assert owner_history.get_messages(conversation["id"]) is not None


@pytest.mark.pg
def test_get_messages_returns_none_for_a_conversation_id_never_created(history_store):
    user_history = history_store.for_user(_fresh_user_sub())

    assert user_history.get_messages(str(uuid7())) is None


@pytest.mark.pg
def test_get_messages_returns_none_for_a_malformed_conversation_id(history_store):
    """A malformed id can never match a real row -- treated the same as
    absent, never an unhandled database error."""
    user_history = history_store.for_user(_fresh_user_sub())

    assert user_history.get_messages("not-a-uuid") is None


# ===========================================================================
# pg: append_user_message / write_assistant_message
# ===========================================================================


@pytest.mark.pg
def test_append_user_message_then_write_assistant_message_round_trip(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()
    cid = conversation["id"]

    user_history.append_user_message(cid, str(uuid7()), "hello there", None)

    buffer = TurnTranscriptBuffer()
    assistant = buffer.start_assistant_message(str(uuid7()))
    buffer.append_part(assistant, {"kind": "text", "payload": {"markdown": "hi"}})
    user_history.write_assistant_message(cid, assistant, str(uuid7()))

    messages, _cursor = user_history.get_messages(cid)
    assert [m["role"] for m in messages] == ["assistant", "user", "assistant"]
    assert messages[1]["parts"] == [{"kind": "text", "payload": {"markdown": "hello there"}}]
    assert messages[2]["parts"] == [{"kind": "text", "payload": {"markdown": "hi"}}]


@pytest.mark.pg
def test_append_user_message_bumps_the_conversation_to_the_top_of_the_list(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    older, _ = user_history.create_conversation()
    newer, _ = user_history.create_conversation()

    items, _cursor = user_history.list_conversations()
    assert [c["id"] for c in items[:2]] == [newer["id"], older["id"]]

    user_history.append_user_message(older["id"], str(uuid7()), "hello", None)

    items, _cursor = user_history.list_conversations()
    assert [c["id"] for c in items[:2]] == [older["id"], newer["id"]]


@pytest.mark.pg
def test_append_user_message_raises_lookuperror_for_another_users_conversation(history_store):
    """Closes a real Postgres "covert channel": foreign key checks BYPASS
    row-level security on the referenced table (documented Postgres
    behavior), so a bare INSERT into messages naming another user's
    conversation_id would otherwise succeed. append_user_message/
    write_assistant_message gate on a same-transaction, RLS-filtered UPDATE
    of the parent row (which they must run anyway, to bump updated_at)
    BEFORE the INSERT, closing the gap without ever adding a user_sub WHERE
    clause of their own."""
    owner_history = history_store.for_user(_fresh_user_sub())
    other_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = owner_history.create_conversation()

    with pytest.raises(LookupError):
        other_history.append_user_message(conversation["id"], str(uuid7()), "hi", None)


# ===========================================================================
# pg: set_title / read_state / write_state
# ===========================================================================


@pytest.mark.pg
def test_set_title_updates_the_row(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()

    user_history.set_title(conversation["id"], "Renamed chat")

    items, _cursor = user_history.list_conversations()
    assert items[0]["title"] == "Renamed chat"


@pytest.mark.pg
def test_write_state_and_read_state_round_trip_the_raw_jsonb(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()
    cid = conversation["id"]

    user_history.write_state(cid, {"slots": {"mode": "existing"}, "brief_done": True})

    assert user_history.read_state(cid) == {"slots": {"mode": "existing"}, "brief_done": True}


@pytest.mark.pg
def test_read_state_returns_empty_dict_for_an_invisible_conversation(history_store):
    owner_history = history_store.for_user(_fresh_user_sub())
    other_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = owner_history.create_conversation()

    assert other_history.read_state(conversation["id"]) == {}


# ===========================================================================
# pg: delete_conversation -- Fix round 1 (task-1-review.md, Important
# finding 1). This method (Phase 11 Task 1) shipped with zero DIRECT test
# coverage: every exercise before this fix round went through api/
# live_chat.py's DELETE route (test_runlog_rls.py), which calls its own
# inline DELETE statement, never UserHistory.delete_conversation itself
# (see that method's own docstring for why the route can't call it -- it
# needs to share ONE transaction with redact_turns_for_conversation, and
# this method's own rls_transaction is self-contained). The four cases
# below are store-level, direct, and independent of the route entirely.
#
# Both row-bearing tests read `conversations`/`messages` through a bare
# `pg_engine.connect()` (the table owner, RLS-exempt only by FORCE's own
# absence of effect on an owner without FORCE -- migration 0004 DOES force
# RLS, but this dev DSN role is also a cluster superuser, which
# unconditionally bypasses RLS regardless of FORCE; see core/db.py's module
# docstring) rather than through the store's own read methods, so a false
# "nothing here" from get_messages/read_state (which could, in principle,
# have its own bug) can never be mistaken for proof the ROW was deleted.
#
# Sensitivity check (mutation testing, captured here rather than asserted):
# these tests are GREEN against the already-shipped, already-reviewed
# implementation -- there is no missing feature to be RED against. To prove
# they actually discriminate rather than passing regardless of what the
# method does, `delete_conversation`'s own `return result.rowcount > 0` was
# temporarily inverted to `return result.rowcount == 0` and this file was
# re-run: all four scenarios below failed (own-cid: `assert deleted is
# True` failed, actual value False; another-user's-cid and both absent/
# malformed cases: `assert ... is False` failed, actual value True;
# idempotence: the FIRST call's own `is True` assertion failed first). The
# row-survival assertions (conversation/message rows present or absent)
# were UNCHANGED by this specific mutation, since it only touches the
# Python-level return value, never the DELETE statement itself or its RLS
# scoping -- those assertions are validated by construction instead: they
# read real database state through a connection independent of the method
# under test, so a bug in the DELETE's own WHERE clause or a lost RLS
# predicate would show up directly as a wrong row count, not merely as a
# wrong boolean. The mutation was reverted immediately after this check;
# see task-1-report.md's "Fix round 1" section for the full transcript.
# ===========================================================================


@pytest.mark.pg
def test_delete_conversation_own_visible_cid_returns_true_and_cascades_messages(
    history_store, pg_engine
):
    """True; the conversation row is gone; ON DELETE CASCADE (migration
    0004) takes its messages with it -- asserted directly against BOTH
    tables. ``read_state``'s own "absent id" contract is the observable
    proof that state disappears WITH the row (state lives in
    ``conversations.state``, not a separate table with a delete step of
    its own)."""
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()
    cid = conversation["id"]
    user_history.append_user_message(cid, str(uuid7()), "hello", None)
    user_history.write_state(cid, {"slots": {"mode": "existing"}, "brief_done": True})

    deleted = user_history.delete_conversation(cid)

    assert deleted is True
    with pg_engine.connect() as conn:
        conversation_row = conn.execute(
            text("SELECT 1 FROM conversations WHERE id = :id"), {"id": cid}
        ).first()
        message_rows = conn.execute(
            text("SELECT 1 FROM messages WHERE conversation_id = :id"), {"id": cid}
        ).all()
    assert conversation_row is None
    assert message_rows == []
    assert user_history.read_state(cid) == {}


@pytest.mark.pg
def test_delete_conversation_another_users_cid_returns_false_and_leaves_rows_intact(
    history_store, pg_engine
):
    owner_history = history_store.for_user(_fresh_user_sub())
    other_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = owner_history.create_conversation()
    cid = conversation["id"]

    deleted = other_history.delete_conversation(cid)

    assert deleted is False
    with pg_engine.connect() as conn:
        conversation_row = conn.execute(
            text("SELECT 1 FROM conversations WHERE id = :id"), {"id": cid}
        ).first()
        message_rows = conn.execute(
            text("SELECT 1 FROM messages WHERE conversation_id = :id"), {"id": cid}
        ).all()
    assert conversation_row is not None
    assert len(message_rows) == 1  # just the opener -- untouched
    # sanity: the owner herself still sees it -- proves the False above is
    # the RLS-visibility gate, not a bug that silently no-ops for everyone.
    assert owner_history.get_messages(cid) is not None


@pytest.mark.pg
def test_delete_conversation_returns_false_for_a_conversation_id_never_created(history_store):
    user_history = history_store.for_user(_fresh_user_sub())

    assert user_history.delete_conversation(str(uuid7())) is False


@pytest.mark.pg
def test_delete_conversation_returns_false_for_a_malformed_conversation_id(history_store):
    """A malformed id can never match a real row -- treated the same as
    absent (history.py's own module docstring), never an unhandled
    database error."""
    user_history = history_store.for_user(_fresh_user_sub())

    assert user_history.delete_conversation("not-a-uuid") is False


@pytest.mark.pg
def test_delete_conversation_is_idempotent_second_call_returns_false(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()
    cid = conversation["id"]

    first = user_history.delete_conversation(cid)
    second = user_history.delete_conversation(cid)

    assert first is True
    assert second is False


# ===========================================================================
# pg: restart survival -- store A writes, a NEW HistoryStore on a NEW engine
# reads the same rows (proves durability, not an in-process illusion).
# ===========================================================================


@pytest.mark.pg
def test_restart_survival_new_history_store_on_new_engine_reads_the_same_rows(
    effective_app_role,
):
    dsn = os.environ["DATABASE_URL"]
    user_sub = _fresh_user_sub()

    engine_a = create_engine(dsn)
    try:
        store_a = HistoryStore(engine_a, app_role=effective_app_role)
        conversation, opener = store_a.for_user(user_sub).create_conversation()
    finally:
        engine_a.dispose()

    engine_b = create_engine(dsn)
    try:
        store_b = HistoryStore(engine_b, app_role=effective_app_role)
        result = store_b.for_user(user_sub).get_messages(conversation["id"])
    finally:
        engine_b.dispose()

    assert result is not None
    messages, _cursor = result
    assert [m["id"] for m in messages] == [opener["id"]]


# ===========================================================================
# pg: DbStateStore
# ===========================================================================


@pytest.mark.pg
def test_db_state_store_round_trip_through_real_jsonb(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()
    state_store = DbStateStore(user_history)
    slots = ConversationSlots(
        customer="ACME MARINE",
        port="SGSIN",
        period_a=date(2026, 4, 1),
        period_b=date(2026, 3, 1),
        mode="existing",
        region="APAC",
        topic="gp",
        pass_through=(("customer", "ACME MARINE"), ("port", "SGSIN")),
    )

    state_store.put(conversation["id"], slots)

    assert state_store.get(conversation["id"]) == slots


@pytest.mark.pg
def test_db_state_store_get_on_an_unseen_id_returns_the_empty_slots_sentinel(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    state_store = DbStateStore(user_history)

    assert state_store.get(str(uuid7())) == ConversationSlots()


@pytest.mark.pg
def test_db_state_store_get_on_another_users_conversation_returns_the_empty_slots_sentinel(
    history_store,
):
    owner_history = history_store.for_user(_fresh_user_sub())
    other_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = owner_history.create_conversation()
    owner_state = DbStateStore(owner_history)
    owner_state.put(conversation["id"], ConversationSlots(customer="SHOULD NOT LEAK"))

    other_state = DbStateStore(other_history)

    assert other_state.get(conversation["id"]) == ConversationSlots()


@pytest.mark.pg
def test_db_state_store_next_turn_index_monotonic_across_store_instances(history_store):
    user_sub = _fresh_user_sub()
    user_history = history_store.for_user(user_sub)
    conversation, _opener = user_history.create_conversation()
    cid = conversation["id"]

    first_store = DbStateStore(user_history)
    assert first_store.next_turn_index(cid) == 1
    assert first_store.next_turn_index(cid) == 2

    # a brand-new DbStateStore instance holds no Python-side counter of its
    # own -- the count must live in Postgres, not in either object.
    second_store = DbStateStore(history_store.for_user(user_sub))
    assert second_store.next_turn_index(cid) == 3


@pytest.mark.pg
def test_db_state_store_next_turn_index_raises_for_a_conversation_that_does_not_exist(
    history_store,
):
    state_store = DbStateStore(history_store.for_user(_fresh_user_sub()))

    with pytest.raises(LookupError):
        state_store.next_turn_index(str(uuid7()))


@pytest.mark.pg
def test_db_state_store_get_brief_done_round_trip(history_store):
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()
    cid = conversation["id"]
    state_store = DbStateStore(user_history)

    assert state_store.get_brief_done(cid) is False

    state_store.set_brief_done(cid, True)
    assert state_store.get_brief_done(cid) is True

    state_store.set_brief_done(cid, False)
    assert state_store.get_brief_done(cid) is False


@pytest.mark.pg
def test_db_state_store_get_brief_done_on_an_unseen_id_returns_false(history_store):
    state_store = DbStateStore(history_store.for_user(_fresh_user_sub()))

    assert state_store.get_brief_done(str(uuid7())) is False


@pytest.mark.pg
def test_db_state_store_put_does_not_disturb_brief_done_or_turn_index(history_store):
    """put/set_brief_done/next_turn_index each touch exactly one key of the
    shared state jsonb ({"slots", "brief_done", "turn_index"}) -- proving
    they use jsonb_set on a single path, never a wholesale overwrite of the
    column."""
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()
    cid = conversation["id"]
    state_store = DbStateStore(user_history)

    state_store.set_brief_done(cid, True)
    state_store.next_turn_index(cid)
    state_store.put(cid, ConversationSlots(customer="ACME"))

    assert state_store.get_brief_done(cid) is True
    assert state_store.next_turn_index(cid) == 2
    assert state_store.get(cid) == ConversationSlots(customer="ACME")
