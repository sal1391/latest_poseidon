"""Chat-turn plumbing Phase 6 composes into the orchestrator (Task 3) and
the CHAT app wiring (Task 4): per-conversation slot state
(:class:`ConversationStateStore`) and the deterministic dev router
(:class:`DevDeterministicRouter`) that answers offline chat -- no AWS
credentials, ``LLM_MODE=stub`` -- with real, certified answers instead of a
canned script.

Importing this package has no side effects: neither class touches anything
at construction time (see each module's own docstring).
"""

from .dev_router import DevDeterministicRouter
from .state import ConversationStateStore

__all__ = [
    "ConversationStateStore",
    "DevDeterministicRouter",
]
