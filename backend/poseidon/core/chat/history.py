"""Phase 10 Task 2 (doc 08): the Postgres-backed store behind migration
0004's ``conversations``/``messages`` tables -- the durable replacement for
``api/live_chat.py``'s in-memory ``TranscriptStore`` (conversations,
messages, feedback) and ``core/chat/state.py``'s in-memory
``ConversationStateStore`` (per-conversation slots/turn-index/brief-done).
Task 3 (route cutover) wires this in without touching either surface's own
callers; this module changes what backs them, not the shapes they serve.

**RLS reliance -- no ``user_sub`` WHERE clauses, anywhere, on purpose.**
Doc 05 section 4 (decision D28): isolation lives in the database, not in a
filter this module could forget to add. Every statement below runs inside
:func:`poseidon.core.db.rls_transaction`, which sets ``app.user_sub`` (and,
on this dev database's privileged DSN, ``SET LOCAL ROLE poseidon_app``) as
the first statements of the transaction; Postgres itself ANDs the owning
table's policy predicate onto every statement from there. A conversation
that does not belong to the caller therefore reads back as zero rows or
``NULL`` -- INDISTINGUISHABLE from a conversation that was never created at
all. That is not a gap; it is the design (see each method's own docstring
for how it turns "zero rows" into the right Python-level shape: ``None``,
``{}``, ``False``, or the empty-slots sentinel).

**Interface delta from the task brief, disclosed up front.** The brief pins
``HistoryStore(engine: Engine)``. This module adds one optional keyword,
``app_role: str | None = None`` -- threaded straight through to every
:func:`rls_transaction` call the same way :class:`~poseidon.core.config.
Settings`'s own ``database_app_role`` threads into every OTHER caller of
that wrapper. Without it, this module would have no way to ever exercise
``SET LOCAL ROLE`` at all, directly contradicting this task's own global
constraint ("identity context transaction-scoped via rls_transaction
(set_config first, then SET LOCAL ROLE per Task 1's wrapper)") on the very
database this task's own pg test suite runs against (``DATABASE_URL``'s
role is a Postgres superuser there -- superusers unconditionally bypass
RLS, so without the role switch every isolation test below would pass for
the wrong reason). ``app_role=None`` is the default, matching
``rls_transaction``'s own neutral default and Settings' own documented
escape hatch for an already-non-privileged DSN; Task 3 passes ``Settings.
database_app_role`` explicitly when it constructs the real one.

**Ids.** Every id this module MINTS (``conversations.id``, and the opener
message's ``messages.id`` inside :meth:`UserHistory.create_conversation`)
comes from :func:`poseidon.core.util.uuid7.uuid7` -- never ``uuid4``, never
a Postgres server-side default (migration 0004 declares none). Every OTHER
id this module only RECEIVES (``message_id``/``turn_id`` on :meth:`~
UserHistory.append_user_message`/:meth:`~UserHistory.write_assistant_
message`, minted by whatever calls this module) is validated as a
well-formed UUID before it ever reaches a bound parameter, but minting
those is the caller's job, not this module's.

**A malformed id is treated exactly like an absent one.** A ``cid`` that is
not even a syntactically valid UUID can never match a real row either way,
so every method below parses it defensively (:func:`_parse_uuid`) and folds
a parse failure into the SAME "not found" outcome the method already has to
support for a genuinely absent or genuinely invisible row -- never an
unhandled ``DataError`` escaping from the database driver as a raw 500.

**Reads fail closed to a harmless default for a bad ID; most writes fail
closed to a silent no-op; three operations raise -- two writes, and (Fix
round 1) one read-side carve-out for a bad CURSOR.**
:meth:`~UserHistory.get_messages`, :meth:`~UserHistory.read_state`,
:meth:`~DbStateStore.get`, and :meth:`~DbStateStore.get_brief_done` all
return the documented "nothing here" value (``None``/``{}``/the
empty-slots sentinel/``False``) for a row that is absent, invisible, or
malformed-id -- never raise for a bad ID. Plain ``conversations``-only
UPDATEs (:meth:`~UserHistory.set_title`, :meth:`~UserHistory.write_state`,
:meth:`~DbStateStore.put`, :meth:`~DbStateStore.set_brief_done`) are
ALREADY fully protected by RLS's own per-row predicate on the UPDATE
itself, so a zero-row match there is simply the correct outcome already --
these no-op silently, adding no bookkeeping of their own. Three operations
differ, each for its own disclosed reason:

0. :meth:`~UserHistory.list_conversations` and :meth:`~UserHistory.get_
   messages` raise :class:`MalformedCursor` (a :class:`ValueError`
   subclass) when their ``cursor`` argument fails to base64/JSON-decode, or
   decodes to something missing the expected keys or holding the wrong
   value types (:func:`_decode_conversations_cursor`/:func:`_decode_
   messages_cursor`). This is deliberately NOT the same "fail closed"
   treatment a bad ID gets, and the asymmetry is intentional: an id is
   something a legitimate caller might reasonably mistype (a stale
   bookmark, a copy-paste slip), so "not found" is an ordinary, expected
   outcome worth absorbing silently. A cursor is different in kind -- it is
   a value ONLY this module itself ever produces (:func:`_encode_cursor`),
   so a cursor that fails to even DECODE means a caller corrupted,
   hand-built, or replayed a tampered/foreign token it was never supposed
   to construct by hand. Silently treating that as "start over at page
   one" would hide exactly the kind of client bug (or cursor replayed
   against the wrong method, or across an unrelated conversation) worth
   surfacing loudly instead. A cursor that DECODES cleanly but describes a
   semantically nonsensical position (e.g. a timestamp before any row
   exists) is emphatically NOT malformed: keyset pagination treats any
   well-typed value as a legitimate continuation point and simply returns
   whatever page that position implies (often an empty one) -- only a
   structural decode/parse/type failure raises.

1. :meth:`~UserHistory.append_user_message` and :meth:`~UserHistory.write_
   assistant_message` first run a same-transaction, RLS-filtered ``UPDATE
   conversations SET updated_at = now() WHERE id = :id`` (which they must
   run anyway, per the brief, to bump recency) and raise ``LookupError`` if
   it matches zero rows, BEFORE ever inserting into ``messages``. This
   closes a real, documented Postgres behavior: foreign key CHECKS BYPASS
   row-level security on the referenced table ("Referential integrity
   checks... always bypass row security", ``ddl-rowsecurity`` docs) -- so a
   bare ``INSERT INTO messages (conversation_id, ...)`` naming another
   user's (existing) conversation would otherwise succeed at the database
   level despite that conversation being invisible to the caller. The
   updated_at bump both satisfies the brief's own requirement AND doubles
   as the ownership gate, with no separate ``user_sub`` WHERE clause added
   anywhere.
2. :meth:`~DbStateStore.next_turn_index` raises ``LookupError`` on zero
   rows because its return type (``int``) has no honest "did nothing"
   value to hand back -- unlike every sibling method above, which returns
   ``None`` either way. Reachable only when a caller asks for a turn index
   on a conversation id that was never created (or belongs to someone
   else) -- never a real path once a conversation is always created before
   any turn against it runs.

**FeedbackStubStore is exactly what its name says: a stub.** It extracts
today's ``TranscriptStore._feedback`` dict and lock verbatim, with the
existence check TranscriptStore used to run (whether ``mid`` names a
message it had ever recorded) DROPPED, because that check read the OLD
``_messages`` dict this store never held. In-memory until Phase 12's
``message_feedback`` table lands; a restart loses every recorded verdict.
"""

import base64
import json
import threading
import uuid
from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.chat.orchestrator import ENTRY_PHRASE_EXISTING, ENTRY_PHRASE_PROSPECT
from poseidon.core.db import rls_transaction
from poseidon.core.skills.context import ConversationSlots
from poseidon.core.util.uuid7 import uuid7

# ---------------------------------------------------------------------------
# small private helpers: id parsing, opaque cursor encoding
# ---------------------------------------------------------------------------


def _parse_uuid(value: str) -> uuid.UUID | None:
    """``value`` as a :class:`uuid.UUID`, or ``None`` if it is not one -- see
    the module docstring's "a malformed id is treated exactly like an
    absent one"."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _parse_optional_uuid(value: str | None) -> uuid.UUID | None:
    """Like :func:`_parse_uuid`, but ``None`` in is ``None`` out (a message
    with no associated turn) -- raises ``ValueError`` for a NON-``None``
    value that still fails to parse, since (unlike a ``cid`` a caller might
    have typo'd) a bad ``turn_id`` here is always this module's own caller
    handing back a value it should have minted itself."""
    if value is None:
        return None
    parsed = _parse_uuid(value)
    if parsed is None:
        raise ValueError(f"turn_id {value!r} is not a valid id")
    return parsed


class MalformedCursor(ValueError):
    """Raised by :meth:`UserHistory.list_conversations`/:meth:`UserHistory.
    get_messages` when their ``cursor`` argument fails to decode, parse, or
    validate against this module's own cursor shape -- see the module
    docstring's numbered item 0 for the full rationale (an id can be
    fail-closed to "not found"; a cursor cannot, since it is a value only
    this module itself ever produces via :func:`_encode_cursor`).
    Deliberately a :class:`ValueError` subclass, not a bare custom
    exception -- Task 3 (route cutover) maps it to a 400 RFC-7807 problem
    detail, and any pre-existing broad ``except ValueError`` a caller might
    already have keeps working unchanged."""


def _encode_cursor(payload: dict) -> str:
    """Opaque urlsafe-base64 of ``payload`` -- callers round-trip this
    string, never parse it (doc 08's cursor contract)."""
    raw = json.dumps(payload, separators=(",", ":")).encode("ascii")
    return base64.urlsafe_b64encode(raw).decode("ascii")


#: Every exception type the decode-through-parse pipeline below can
#: legitimately raise for some malformed input, empirically enumerated
#: (not merely assumed) against every matrix cell the fix-round review
#: named: bad base64 / non-ASCII text -- ``binascii.Error``/``UnicodeError``,
#: both ``ValueError`` subclasses; invalid JSON -- ``json.JSONDecodeError``,
#: also a ``ValueError`` subclass; a decoded value that is not a dict at
#: all, or a dict missing the expected key -- ``TypeError``/``KeyError``;
#: a present-but-wrongly-typed field passed to ``datetime.fromisoformat``/
#: ``uuid.UUID`` -- ``TypeError``/``AttributeError``. One shared tuple so
#: both cursor-shape decoders below catch identically.
_CURSOR_DECODE_ERRORS = (ValueError, TypeError, KeyError, AttributeError)


def _decode_conversations_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """``list_conversations``'s cursor shape: ``{"u": <iso updated_at>,
    "i": <id>}``. Raises :class:`MalformedCursor` for any failure anywhere
    in decode-through-parse (see :data:`_CURSOR_DECODE_ERRORS`) -- a
    structurally broken cursor (bad base64/JSON), one missing a key, or one
    whose ``"u"``/``"i"`` value has the wrong type. A cursor that decodes
    and parses cleanly but describes a nonsensical position (e.g. a
    timestamp before any row exists) is NOT malformed and does not reach
    this ``except`` at all -- see the module docstring's numbered item 0.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        decoded = json.loads(raw)
        updated_at = datetime.fromisoformat(decoded["u"])
        conversation_id = uuid.UUID(decoded["i"])
    except _CURSOR_DECODE_ERRORS as exc:
        raise MalformedCursor(f"cursor {cursor!r} is not a valid conversations cursor") from exc
    return updated_at, conversation_id


def _decode_messages_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """``get_messages``'s cursor shape: ``{"c": <iso created_at>, "i":
    <id>}``. Same failure handling as :func:`_decode_conversations_cursor`
    (see :data:`_CURSOR_DECODE_ERRORS`), different keys."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii"))
        decoded = json.loads(raw)
        created_at = datetime.fromisoformat(decoded["c"])
        message_id = uuid.UUID(decoded["i"])
    except _CURSOR_DECODE_ERRORS as exc:
        raise MalformedCursor(f"cursor {cursor!r} is not a valid messages cursor") from exc
    return created_at, message_id


# ---------------------------------------------------------------------------
# SQL -- every statement below runs only inside rls_transaction (see each
# call site); none of them carries a user_sub predicate of its own.
# ---------------------------------------------------------------------------

_INSERT_CONVERSATION_SQL = text(
    "INSERT INTO conversations (id, user_sub, title, mode) "
    "VALUES (:id, :user_sub, :title, :mode)"
)

_INSERT_MESSAGE_SQL = text(
    "INSERT INTO messages (id, conversation_id, user_sub, role, parts, turn_id) "
    "VALUES (:id, :conversation_id, :user_sub, :role, :parts, :turn_id)"
)

_LIST_CONVERSATIONS_FIRST_PAGE_SQL = text(
    "SELECT id, title, updated_at FROM conversations "
    "ORDER BY updated_at DESC, id DESC LIMIT :fetch_limit"
)

_LIST_CONVERSATIONS_NEXT_PAGE_SQL = text(
    "SELECT id, title, updated_at FROM conversations "
    "WHERE (updated_at, id) < (:cursor_u, :cursor_i) "
    "ORDER BY updated_at DESC, id DESC LIMIT :fetch_limit"
)

_CONVERSATION_EXISTS_SQL = text("SELECT 1 FROM conversations WHERE id = :id")

_GET_MESSAGES_FIRST_PAGE_SQL = text(
    "SELECT id, role, parts, created_at FROM messages "
    "WHERE conversation_id = :conversation_id "
    "ORDER BY created_at ASC, id ASC LIMIT :fetch_limit"
)

_GET_MESSAGES_NEXT_PAGE_SQL = text(
    "SELECT id, role, parts, created_at FROM messages "
    "WHERE conversation_id = :conversation_id AND (created_at, id) > (:cursor_c, :cursor_i) "
    "ORDER BY created_at ASC, id ASC LIMIT :fetch_limit"
)

_BUMP_UPDATED_AT_SQL = text("UPDATE conversations SET updated_at = now() WHERE id = :id")

_SET_TITLE_SQL = text("UPDATE conversations SET title = :title WHERE id = :id")

_READ_STATE_SQL = text("SELECT state FROM conversations WHERE id = :id")

_WRITE_STATE_SQL = text("UPDATE conversations SET state = :state WHERE id = :id")

_PUT_SLOTS_SQL = text(
    "UPDATE conversations SET state = jsonb_set("
    "coalesce(state, '{}'::jsonb), '{slots}', CAST(:slots AS jsonb), true) "
    "WHERE id = :id"
)

_SET_BRIEF_DONE_SQL = text(
    "UPDATE conversations SET state = jsonb_set("
    "coalesce(state, '{}'::jsonb), '{brief_done}', CAST(:value AS jsonb), true) "
    "WHERE id = :id"
)

_GET_BRIEF_DONE_SQL = text("SELECT state -> 'brief_done' FROM conversations WHERE id = :id")

_NEXT_TURN_INDEX_SQL = text(
    "UPDATE conversations SET state = jsonb_set("
    "coalesce(state, '{}'::jsonb), '{turn_index}', "
    "to_jsonb(coalesce((state->>'turn_index')::int, 0) + 1), true) "
    "WHERE id = :id "
    "RETURNING (state->>'turn_index')::int"
)


def _build_opener_parts() -> list[dict]:
    """A fresh opener parts list every call -- never a shared module-level
    literal -- mirroring ``TranscriptStore.create_conversation``'s own
    "build a new dict every time" discipline (live_chat.py:292-336). Same
    two flow chips, same D19 pinned ``send_text`` phrases, IMPORTED from
    ``orchestrator.py`` rather than retyped (the same discipline that
    module's own docstring names for ``TranscriptStore`` itself)."""
    return [
        {"kind": "text", "payload": {"markdown": "Ask about your data, or pick a flow:"}},
        {
            "kind": "chips",
            "payload": {
                "options": [
                    {
                        "id": "existing_customer",
                        "label": "Existing customer",
                        "send_text": ENTRY_PHRASE_EXISTING,
                    },
                    {
                        "id": "new_prospect",
                        "label": "New customer prospect",
                        "send_text": ENTRY_PHRASE_PROSPECT,
                    },
                ]
            },
        },
    ]


class HistoryStore:
    """The per-process entry point: holds the ``Engine`` (and the resolved
    ``app_role``, see the module docstring's disclosed delta) and hands out
    per-user facades. Construction touches nothing -- no connection is
    opened until a :class:`UserHistory` method actually runs one."""

    def __init__(self, engine: Engine, app_role: str | None = None) -> None:
        self._engine = engine
        self._app_role = app_role

    def for_user(self, user_sub: str) -> "UserHistory":
        """A facade scoped to ``user_sub`` -- every statement it runs goes
        through :func:`rls_transaction` with THIS identity, so nothing
        returned by any of its methods can ever be another user's row."""
        return UserHistory(self._engine, user_sub, self._app_role)


class UserHistory:
    """One user's view of their own conversations/messages. Every public
    method opens exactly one :func:`rls_transaction` (never more, never a
    raw ``engine.begin()``) and returns dict shapes byte-compatible with
    today's ``TranscriptStore`` payloads (``api/live_chat.py:269-448``) --
    Task 3 swaps the backing store without changing what any route serves.
    """

    def __init__(self, engine: Engine, user_sub: str, app_role: str | None = None) -> None:
        self._engine = engine
        self._user_sub = user_sub
        self._app_role = app_role

    def _transaction(self):
        return rls_transaction(self._engine, self._user_sub, app_role=self._app_role)

    def create_conversation(self, mode: str = "default") -> tuple[dict, dict]:
        """Insert a new conversation (uuid7 id, title "New chat", ``mode``
        stored in the new ``conversations.mode`` column TranscriptStore
        never had) plus its opener assistant message, in one transaction.
        ``mode`` only selects the stored column value -- the opener's own
        content is identical regardless, exactly matching today's single,
        mode-less opener (the whole point of the opener is to let the user
        PICK a flow via its chips)."""
        conversation_id = uuid7()
        opener_id = uuid7()
        opener_parts = _build_opener_parts()
        with self._transaction() as conn:
            conn.execute(
                _INSERT_CONVERSATION_SQL,
                {
                    "id": str(conversation_id),
                    "user_sub": self._user_sub,
                    "title": "New chat",
                    "mode": mode,
                },
            )
            conn.execute(
                _INSERT_MESSAGE_SQL,
                {
                    "id": str(opener_id),
                    "conversation_id": str(conversation_id),
                    "user_sub": self._user_sub,
                    "role": "assistant",
                    "parts": json.dumps(opener_parts),
                    "turn_id": None,
                },
            )
        conversation = {"id": str(conversation_id), "title": "New chat"}
        opener = {"id": str(opener_id), "role": "assistant", "parts": opener_parts}
        return conversation, opener

    def list_conversations(
        self, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[dict], str | None]:
        """``(items, next_cursor)``, ordered ``updated_at DESC, id DESC``.
        Fetches one extra row past ``limit`` to know whether a next page
        exists without a separate COUNT query; ``next_cursor`` is ``None``
        exactly when that extra row was not there.

        Raises :class:`MalformedCursor` if ``cursor`` fails to decode --
        see the module docstring's numbered item 0 for why this is the one
        read in this module that does NOT fail closed to a harmless
        default."""
        fetch_limit = limit + 1
        params: dict[str, object] = {"fetch_limit": fetch_limit}
        if cursor is None:
            sql = _LIST_CONVERSATIONS_FIRST_PAGE_SQL
        else:
            cursor_u, cursor_i = _decode_conversations_cursor(cursor)
            params["cursor_u"] = cursor_u
            params["cursor_i"] = cursor_i
            sql = _LIST_CONVERSATIONS_NEXT_PAGE_SQL
        with self._transaction() as conn:
            rows = conn.execute(sql, params).all()
        page = rows[:limit]
        items = [{"id": str(row.id), "title": row.title} for row in page]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            next_cursor = _encode_cursor({"u": last.updated_at.isoformat(), "i": str(last.id)})
        return items, next_cursor

    def get_messages(
        self, cid: str, limit: int = 200, cursor: str | None = None
    ) -> tuple[list[dict], str | None] | None:
        """``(items, next_cursor)`` ordered ``created_at ASC, id ASC``, or
        ``None`` when ``cid`` is absent, malformed, or another user's --
        RLS makes the three indistinguishable ON PURPOSE (module
        docstring); the route above 404s all three alike.

        Raises :class:`MalformedCursor` if ``cursor`` fails to decode --
        same carve-out as :meth:`list_conversations`, and checked BEFORE
        the ``cid`` visibility check below, so a malformed cursor against
        someone else's (or a nonexistent) conversation still raises rather
        than returning ``None`` -- the two failure modes are orthogonal,
        and a client sending garbage on both counts should hear about the
        cursor, since that is the input it built itself."""
        parsed_cid = _parse_uuid(cid)
        if parsed_cid is None:
            return None
        fetch_limit = limit + 1
        params: dict[str, object] = {
            "conversation_id": str(parsed_cid),
            "fetch_limit": fetch_limit,
        }
        if cursor is None:
            sql = _GET_MESSAGES_FIRST_PAGE_SQL
        else:
            cursor_c, cursor_i = _decode_messages_cursor(cursor)
            params["cursor_c"] = cursor_c
            params["cursor_i"] = cursor_i
            sql = _GET_MESSAGES_NEXT_PAGE_SQL
        with self._transaction() as conn:
            exists = conn.execute(_CONVERSATION_EXISTS_SQL, {"id": str(parsed_cid)}).first()
            if exists is None:
                return None
            rows = conn.execute(sql, params).all()
        page = rows[:limit]
        items = [{"id": str(row.id), "role": row.role, "parts": row.parts} for row in page]
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            next_cursor = _encode_cursor({"c": last.created_at.isoformat(), "i": str(last.id)})
        return items, next_cursor

    def append_user_message(
        self, cid: str, message_id: str, text: str, turn_id: str | None
    ) -> None:
        """Insert the user's message and bump ``conversations.updated_at``
        -- see the module docstring for why the bump doubles as an
        ownership gate (``LookupError`` on a ``cid`` that is absent,
        malformed, or another user's) closing the FK-bypasses-RLS gap,
        rather than only being "nice recency bookkeeping"."""
        parsed_cid = _parse_uuid(cid)
        if parsed_cid is None:
            raise LookupError(f"conversation {cid!r} is not a valid conversation id")
        parsed_message_id = _parse_uuid(message_id)
        if parsed_message_id is None:
            raise ValueError(f"message_id {message_id!r} is not a valid id")
        parsed_turn_id = _parse_optional_uuid(turn_id)
        parts = [{"kind": "text", "payload": {"markdown": text}}]
        with self._transaction() as conn:
            result = conn.execute(_BUMP_UPDATED_AT_SQL, {"id": str(parsed_cid)})
            if result.rowcount == 0:
                raise LookupError(
                    f"conversation {cid!r} does not exist or is not visible to this user"
                )
            conn.execute(
                _INSERT_MESSAGE_SQL,
                {
                    "id": str(parsed_message_id),
                    "conversation_id": str(parsed_cid),
                    "user_sub": self._user_sub,
                    "role": "user",
                    "parts": json.dumps(parts),
                    "turn_id": str(parsed_turn_id) if parsed_turn_id is not None else None,
                },
            )

    def write_assistant_message(self, cid: str, message: dict, turn_id: str | None) -> None:
        """Single insert at stream end -- ``message`` is the finished dict
        a :class:`TurnTranscriptBuffer` folded over the course of one turn
        (``id``/``role``/``parts``), inserted whole rather than
        incrementally. Same ownership gate as :meth:`append_user_message`,
        for the same reason (module docstring)."""
        parsed_cid = _parse_uuid(cid)
        if parsed_cid is None:
            raise LookupError(f"conversation {cid!r} is not a valid conversation id")
        parsed_message_id = _parse_uuid(message["id"])
        if parsed_message_id is None:
            raise ValueError(f"message id {message['id']!r} is not a valid id")
        parsed_turn_id = _parse_optional_uuid(turn_id)
        with self._transaction() as conn:
            result = conn.execute(_BUMP_UPDATED_AT_SQL, {"id": str(parsed_cid)})
            if result.rowcount == 0:
                raise LookupError(
                    f"conversation {cid!r} does not exist or is not visible to this user"
                )
            conn.execute(
                _INSERT_MESSAGE_SQL,
                {
                    "id": str(parsed_message_id),
                    "conversation_id": str(parsed_cid),
                    "user_sub": self._user_sub,
                    "role": message["role"],
                    "parts": json.dumps(message["parts"]),
                    "turn_id": str(parsed_turn_id) if parsed_turn_id is not None else None,
                },
            )

    def set_title(self, cid: str, title: str) -> None:
        """Silent no-op for an absent/malformed/invisible ``cid`` -- a
        plain ``UPDATE ... WHERE id = :id`` is already fully filtered by
        RLS's own predicate on the statement itself, so there is no
        FK-bypass-style gap here to close (contrast :meth:`append_user_
        message`)."""
        parsed_cid = _parse_uuid(cid)
        if parsed_cid is None:
            return
        with self._transaction() as conn:
            conn.execute(_SET_TITLE_SQL, {"id": str(parsed_cid), "title": title})

    def read_state(self, cid: str) -> dict:
        """The raw ``state`` jsonb -- ``{}`` for an absent, malformed, or
        invisible ``cid`` (never raises)."""
        parsed_cid = _parse_uuid(cid)
        if parsed_cid is None:
            return {}
        with self._transaction() as conn:
            row = conn.execute(_READ_STATE_SQL, {"id": str(parsed_cid)}).first()
        if row is None:
            return {}
        return row[0] or {}

    def write_state(self, cid: str, state: dict) -> None:
        """Replace the ENTIRE ``state`` jsonb wholesale (contrast
        :class:`DbStateStore`'s ``put``/``set_brief_done``/``next_turn_
        index``, each of which touches exactly one key via ``jsonb_set``).
        Silent no-op for an absent/malformed/invisible ``cid`` -- same
        reasoning as :meth:`set_title`."""
        parsed_cid = _parse_uuid(cid)
        if parsed_cid is None:
            return
        with self._transaction() as conn:
            conn.execute(_WRITE_STATE_SQL, {"id": str(parsed_cid), "state": json.dumps(state)})


class TurnTranscriptBuffer:
    """The per-turn in-memory fold, ported VERBATIM in behavior from
    ``TranscriptStore`` (``api/live_chat.py:361-434``) with the
    dict-of-conversations removed: one buffer belongs to exactly one
    streaming turn (built when the turn starts, discarded once :meth:`~
    UserHistory.write_assistant_message` persists the finished message), so
    there is no ``cid``-keyed dict left to remove it FROM, and no
    :class:`threading.Lock` either -- a single turn's frames arrive
    sequentially, on the one worker thread running that turn
    (``api/live_chat.py``'s own thread+queue bridge), never concurrently
    with another turn's buffer.
    """

    def start_assistant_message(self, message_id: str) -> dict:
        """A fresh, empty assistant message dict -- the SAME mutable dict
        :meth:`append_part`/:meth:`record_tool_event`/:meth:`fold_token`
        fill in place as the turn runs, exactly mirroring TranscriptStore's
        own "register before the first frame, then fill in place" contract.
        """
        return {"id": message_id, "role": "assistant", "parts": []}

    def append_part(self, assistant: dict, part: dict) -> None:
        assistant["parts"].append(part)

    def record_tool_event(self, assistant: dict, payload: dict) -> None:
        """A ``tool_seq`` not yet present is PUSHED; a ``tool_seq`` already
        present is REPLACED IN PLACE, at its existing position, never
        re-appended -- see ``TranscriptStore.record_tool_event``'s own
        docstring (``api/live_chat.py:376-417``) for the full rationale
        (keeping a reloaded transcript's part order agreeing with the live
        view's under progressive streaming)."""
        parts = assistant["parts"]
        index = next(
            (
                i
                for i, part in enumerate(parts)
                if part["kind"] == "tool_event"
                and part["payload"]["tool_seq"] == payload["tool_seq"]
            ),
            None,
        )
        part = {"kind": "tool_event", "payload": payload}
        if index is not None:
            parts[index] = part
        else:
            parts.append(part)

    def fold_token(self, assistant: dict, text: str) -> None:
        """A token's text folds into the trailing text part, or starts a
        new one -- same rule as the frontend's own ``applyEventTo``
        "token" case and ``TranscriptStore.fold_token``."""
        parts = assistant["parts"]
        if parts and parts[-1]["kind"] == "text":
            parts[-1] = {
                "kind": "text",
                "payload": {"markdown": parts[-1]["payload"]["markdown"] + text},
            }
        else:
            parts.append({"kind": "text", "payload": {"markdown": text}})


class DbStateStore:
    """Implements EXACTLY ``core/chat/state.py``'s ``ConversationStateStore``
    interface (same five method names, same parameter name --
    ``conversation_id``, not ``cid`` -- same return shapes) so the
    orchestrator needs zero edits when Task 3 swaps this in. Backed by
    ``conversations.state`` jsonb, shaped ``{"slots": {...}, "brief_done":
    bool, "turn_index": int}``; reads a :class:`UserHistory` instance's own
    already-resolved engine/identity/app_role rather than duplicating them
    (both classes live in this one module, tightly coupled by the brief's
    own design -- see ``HistoryStore``/``UserHistory`` above)."""

    def __init__(self, user_history: UserHistory) -> None:
        self._engine = user_history._engine
        self._user_sub = user_history._user_sub
        self._app_role = user_history._app_role

    def _transaction(self):
        return rls_transaction(self._engine, self._user_sub, app_role=self._app_role)

    def get(self, conversation_id: str) -> ConversationSlots:
        """The empty-slots sentinel (``ConversationSlots()``) for an
        absent, malformed, or invisible id -- matching ``ConversationState
        Store.get``'s own "unseen id -> the harmless default" contract."""
        parsed = _parse_uuid(conversation_id)
        if parsed is None:
            return ConversationSlots()
        with self._transaction() as conn:
            row = conn.execute(_READ_STATE_SQL, {"id": str(parsed)}).first()
        if row is None or row[0] is None:
            return ConversationSlots()
        return slots_from_json(row[0].get("slots", {}))

    def put(self, conversation_id: str, slots: ConversationSlots) -> None:
        """Replace wholesale -- touches ONLY the ``state`` jsonb's
        ``"slots"`` key (``jsonb_set``), leaving ``brief_done``/
        ``turn_index`` untouched. Silent no-op for an absent/malformed/
        invisible id -- a plain ``conversations``-only UPDATE is already
        fully RLS-filtered (module docstring)."""
        parsed = _parse_uuid(conversation_id)
        if parsed is None:
            return
        slots_json = json.dumps(slots_to_json(slots))
        with self._transaction() as conn:
            conn.execute(_PUT_SLOTS_SQL, {"id": str(parsed), "slots": slots_json})

    def next_turn_index(self, conversation_id: str) -> int:
        """The next 1-based turn index, incremented atomically by a single
        ``UPDATE ... RETURNING`` (read-modify-write in one statement, no
        separate SELECT) inside one ``rls_transaction``. Raises
        ``LookupError`` when that UPDATE matches zero rows -- see the
        module docstring for why this is the one method that raises
        instead of silently no-oping."""
        parsed = _parse_uuid(conversation_id)
        if parsed is None:
            raise LookupError(f"conversation {conversation_id!r} is not a valid id")
        with self._transaction() as conn:
            row = conn.execute(_NEXT_TURN_INDEX_SQL, {"id": str(parsed)}).first()
        if row is None:
            raise LookupError(
                f"conversation {conversation_id!r} does not exist or is not visible "
                "to this user"
            )
        return row[0]

    def get_brief_done(self, conversation_id: str) -> bool:
        """``False`` for an absent, malformed, or invisible id, or one that
        exists but has never called :meth:`set_brief_done` -- matching
        ``ConversationStateStore.get_brief_done``'s own default."""
        parsed = _parse_uuid(conversation_id)
        if parsed is None:
            return False
        with self._transaction() as conn:
            row = conn.execute(_GET_BRIEF_DONE_SQL, {"id": str(parsed)}).first()
        if row is None or row[0] is None:
            return False
        return bool(row[0])

    def set_brief_done(self, conversation_id: str, value: bool) -> None:
        """Touches ONLY the ``state`` jsonb's ``"brief_done"`` key. Silent
        no-op for an absent/malformed/invisible id -- same reasoning as
        :meth:`put`."""
        parsed = _parse_uuid(conversation_id)
        if parsed is None:
            return
        with self._transaction() as conn:
            conn.execute(_SET_BRIEF_DONE_SQL, {"id": str(parsed), "value": json.dumps(bool(value))})


class FeedbackStubStore:
    """``TranscriptStore._feedback``'s dict + lock, extracted verbatim
    (``api/live_chat.py:436-448``). In-memory until Phase 12's
    ``message_feedback`` table lands: a process restart loses every
    recorded verdict, and (unlike the original) this store no longer knows
    whether ``mid`` names a message that really exists -- that check read
    the old ``_messages`` dict, which lived on TranscriptStore, never here.
    Routes keep their wire contract meanwhile by checking existence some
    other way before calling in (a Task 3 concern, not this store's)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._feedback: dict[str, dict] = {}

    def upsert_feedback(self, mid: str, verdict: str, comment: str | None) -> None:
        with self._lock:
            self._feedback[mid] = {"verdict": verdict, "comment": comment}

    def get_feedback(self, mid: str) -> dict | None:
        with self._lock:
            return self._feedback.get(mid)


def slots_to_json(slots: ConversationSlots) -> dict:
    """:class:`ConversationSlots` -> a plain, jsonb-safe dict: dates become
    ISO strings, ``pass_through`` becomes a list of ``[key, value]`` pairs
    (JSON has no tuple)."""
    return {
        "customer": slots.customer,
        "port": slots.port,
        "period_a": slots.period_a.isoformat() if slots.period_a is not None else None,
        "period_b": slots.period_b.isoformat() if slots.period_b is not None else None,
        "mode": slots.mode,
        "region": slots.region,
        "topic": slots.topic,
        "pass_through": [[key, value] for key, value in slots.pass_through],
    }


def slots_from_json(raw: dict) -> ConversationSlots:
    """The inverse of :func:`slots_to_json`. Unknown keys are ignored
    (forward compatibility: a newer app version's extra key is simply never
    read); a missing key defaults exactly like the dataclass field it
    represents, via ``.get(key, default)`` -- there is no branch here that
    treats "absent" specially, ``.get`` already does."""
    period_a_raw = raw.get("period_a")
    period_b_raw = raw.get("period_b")
    pass_through_raw = raw.get("pass_through", ())
    return ConversationSlots(
        customer=raw.get("customer"),
        port=raw.get("port"),
        period_a=date.fromisoformat(period_a_raw) if period_a_raw is not None else None,
        period_b=date.fromisoformat(period_b_raw) if period_b_raw is not None else None,
        mode=raw.get("mode", "default"),
        region=raw.get("region"),
        topic=raw.get("topic"),
        pass_through=tuple((pair[0], pair[1]) for pair in pass_through_raw),
    )


__all__ = [
    "DbStateStore",
    "FeedbackStubStore",
    "HistoryStore",
    "MalformedCursor",
    "TurnTranscriptBuffer",
    "UserHistory",
    "slots_from_json",
    "slots_to_json",
]
