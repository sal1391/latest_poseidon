"""Per-conversation slot state (doc 03 section 3 item 4): the in-memory
store the chat orchestrator (Phase 6 Task 3) reads before assembling each
turn's system prompt and writes back to after it folds that turn's carry.

In-memory, on purpose, for Phase 6: a plain ``dict`` behind a
``threading.Lock``, correct only as long as the process serving chat runs a
single uvicorn worker (dev's own deployment shape) -- a second worker
process would hold its own, disjoint copy of this store, so a request that
happened to land on the other worker would see no history for a
conversation the first worker already knows about. Phase 10 (History + RLS)
replaces the dict with persisted, per-user state behind this SAME surface --
:meth:`~ConversationStateStore.get`/:meth:`~ConversationStateStore.put`/
:meth:`~ConversationStateStore.next_turn_index` -- so nothing above this
seam (the orchestrator) needs to change when that lands; only what backs it
does.

**Phase 10 Task 3 (the cutover): KEPT, not deleted.** The live path
(``api/live_chat.py``'s real ``POST .../messages`` route) now threads
:class:`~poseidon.core.chat.history.DbStateStore` into
``execute_turn``'s own ``state`` parameter instead of an instance of this
class -- ``DbStateStore`` implements the identical five-method surface
(``get``/``put``/``next_turn_index``/``get_brief_done``/``set_brief_done``,
same parameter name ``conversation_id``) against ``conversations.state``
jsonb, so the orchestrator needed zero edits to accept it. This class stays
because it is still a REAL, live import, not a stray reference: ``core/chat/
orchestrator.py``'s own ``execute_turn`` signature type-hints its ``state``
argument as ``ConversationStateStore`` (structurally satisfied by
``DbStateStore`` today, never enforced at runtime -- Python type hints
are not checked), and ``core/chat/__init__.py`` re-exports this class as
part of the package's own public surface. Deleting the class would break
both without also editing ``orchestrator.py``, which this task's brief
places out of scope (zero orchestrator edits, by design -- see that
module's own docstring). Four offline test modules
(``test_chat_orchestrator.py``, ``test_entry_orchestration.py``,
``test_chat_state_devrouter.py``, ``test_emit_seam_loop_events.py``)
also still construct this class directly for orchestrator/router-level
testing that has nothing to do with the live HTTP surface; none of them
needed any change for this cutover.

Phase 8 Task 5 (D19) adds a THIRD, additive dict here: :meth:`~
ConversationStateStore.get_brief_done`/:meth:`~ConversationStateStore.
set_brief_done`, a per-conversation ``bool`` recording whether a
bubble-entry brief has already completed. Additive, not a reshaping of
``ConversationSlots`` (a parked shape this phase does not touch beyond the
already-present ``mode`` field) -- see :meth:`~ConversationStateStore.
get_brief_done`'s own docstring for the full reasoning, and
``core/chat/orchestrator.py`` for the one caller (the D19 entry/subject-turn
branch). Joins the SAME "Phase 10 replaces the backing store, not the
surface" promise as the two methods above it.
"""

import threading

from poseidon.core.skills.context import ConversationSlots

# Returned by `get` for a conversation_id this store has never seen. One
# shared instance rather than `ConversationSlots()` re-constructed per call:
# harmless either way since the dataclass is frozen (immutable, so no caller
# can mutate a shared default out from under another), but a single named
# constant says "the empty default" once instead of re-spelling the same
# construction at every call site.
_EMPTY_SLOTS = ConversationSlots()


class ConversationStateStore:
    """In-memory ``conversation_id -> ConversationSlots`` map, plus an
    independent per-conversation turn counter and (Phase 8 Task 5) a
    per-conversation brief-completion flag. See the module docstring for
    why in-memory and what eventually replaces it.

    One :class:`threading.Lock` guards all THREE dicts below, not one
    each: every method here does a single dict read or write, already
    atomic under CPython's GIL in practice, but the lock is what makes
    that guarantee explicit and independent of any particular
    interpreter's implementation detail -- every call from every
    concurrent request handler serializes cleanly against every other.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[str, ConversationSlots] = {}
        self._turn_index: dict[str, int] = {}
        # Phase 8 Task 5 (D19): whether THIS conversation has already
        # dispatched a bubble-entry brief to completion -- see this class's
        # own docstring's "brief_done" paragraph for why this rides the
        # store rather than ConversationSlots.
        self._brief_done: dict[str, bool] = {}

    def get(self, conversation_id: str) -> ConversationSlots:
        """The conversation's current slots, or an empty
        :class:`~poseidon.core.skills.context.ConversationSlots` for an id
        never passed to :meth:`put`."""
        with self._lock:
            return self._slots.get(conversation_id, _EMPTY_SLOTS)

    def put(self, conversation_id: str, slots: ConversationSlots) -> None:
        """Replace the conversation's slots wholesale.

        The caller (the orchestrator, after folding this turn's carry via
        :func:`~poseidon.core.parsing.carry.apply_carry`) always hands a
        complete, already-merged :class:`ConversationSlots` -- this store
        does no merging of its own, the same "replace, never merge"
        discipline ``ConversationSlots``'s own docstring describes for
        individual slots, applied here to the whole object.
        """
        with self._lock:
            self._slots[conversation_id] = slots

    def next_turn_index(self, conversation_id: str) -> int:
        """The next 1-based turn index for ``conversation_id``, monotonic
        per conversation and independent of :meth:`get`/:meth:`put`.

        A separate counter, not derived from the slots dict, so a
        conversation whose every turn cleared its slots back to empty (or
        one never seen by :meth:`put` at all -- a caller is free to number
        turns before it has anything to store) still counts turns
        correctly.
        """
        with self._lock:
            index = self._turn_index.get(conversation_id, 0) + 1
            self._turn_index[conversation_id] = index
            return index

    def get_brief_done(self, conversation_id: str) -> bool:
        """Whether ``conversation_id`` has already dispatched a D19
        bubble-entry brief to a SUCCESSFUL completion (Phase 8 Task 5).
        ``False`` for an id never passed to :meth:`set_brief_done`, the
        same "unseen id -> the harmless default" convention :meth:`get`
        already uses for slots.

        This is an ADDITIVE method on the SAME in-memory store
        :meth:`get`/:meth:`put`/:meth:`next_turn_index` already share
        (guarded by the identical lock, one more plain dict keyed by
        ``conversation_id``) -- not a new field on
        :class:`~poseidon.core.skills.context.ConversationSlots`. Two
        reasons, both from the orchestrator's own D19 entry-branch design:
        ``ConversationSlots`` is a PARKED shape this phase does not
        reshape (only ``mode`` -- already present since Phase 4 -- is
        actually written by this task); and "has a brief completed THIS
        conversation" is orchestration bookkeeping about the CONVERSATION,
        the same category as ``next_turn_index``'s own counter, not a
        piece of parsed conversational STATE a skill or a prompt ever
        needs to read (contrast ``mode``, which DOES ride slots because
        the hinter and every downstream prompt need to see it). Phase 10
        (History + RLS) replaces this whole store's in-memory dicts with
        persisted, per-user state behind this SAME surface, exactly as
        this class's own module docstring already promises for
        :meth:`get`/:meth:`put`/:meth:`next_turn_index` -- this method
        joins that promise, not a new one.
        """
        with self._lock:
            return self._brief_done.get(conversation_id, False)

    def set_brief_done(self, conversation_id: str, value: bool) -> None:
        """Record whether ``conversation_id``'s current D19 brief flow is
        done. ``True`` once a bubble-entry brief dispatch SUCCEEDS
        (``execute_turn`` then routes every later turn through the normal
        registry again); the D19 entry branch resets this back to
        ``False`` on a fresh flow-chip click, so a second brief can be
        started in the same conversation without carrying over the first
        one's completion.
        """
        with self._lock:
            self._brief_done[conversation_id] = value


__all__ = ["ConversationStateStore"]
