"""Phase 13 Task 1 (doc 05 section 5): the three per-user personalization
stores behind migration 0008 -- ``user_profile`` (:class:`ProfileStore`/
:class:`UserProfile`), ``user_memory`` (:class:`MemoryStore`/
:class:`UserMemory`), and ``memory_outbox`` (:class:`OutboxStore`/
:class:`ConversationOutbox`). Each mirrors ``core/chat/history.py``'s
``HistoryStore``/``UserHistory`` construction shape -- see each module's
own docstring for its own table's specifics.

Importing this package has no side effects: no class touches anything at
construction time (see each module's own docstring).
"""

from .memory import MemoryStore, MemoryTooLarge, MemoryValidationError, UserMemory
from .outbox import ConversationOutbox, OutboxStore
from .profile import ProfileStore, UserProfile

__all__ = [
    "ConversationOutbox",
    "MemoryStore",
    "MemoryTooLarge",
    "MemoryValidationError",
    "OutboxStore",
    "ProfileStore",
    "UserMemory",
    "UserProfile",
]
