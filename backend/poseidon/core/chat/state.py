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
    independent per-conversation turn counter. See the module docstring for
    why in-memory and what eventually replaces it.

    One :class:`threading.Lock` guards BOTH dicts below, not one each:
    every method here does a single dict read or write, already atomic
    under CPython's GIL in practice, but the lock is what makes that
    guarantee explicit and independent of any particular interpreter's
    implementation detail -- every call from every concurrent request
    handler serializes cleanly against every other.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._slots: dict[str, ConversationSlots] = {}
        self._turn_index: dict[str, int] = {}

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


__all__ = ["ConversationStateStore"]
