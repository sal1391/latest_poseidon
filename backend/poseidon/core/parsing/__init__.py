"""The deterministic pre-LLM parsing pipeline (doc 02 section 5).

Runs on every inbound chat message before any LLM call. As of Task 1:
``normalize`` cleans the raw text, ``carry`` folds a turn's resolved slot
updates onto the prior ``ConversationSlots`` under the omit/clear/replace
rule, and ``types`` defines the shapes every later stage (period parser,
customer/port resolver, skill hinter -- Phase 4 Tasks 2-4) produces on the
way to a ``ParsedTurn``. Importing this package has no side effects.
"""

from .carry import UNSET, SlotUpdates, apply_carry
from .normalize import normalize
from .types import CandidateSkill, ParsedTurn, ParseIssue, ResolvedEntity

__all__ = [
    "CandidateSkill",
    "ParseIssue",
    "ParsedTurn",
    "ResolvedEntity",
    "SlotUpdates",
    "UNSET",
    "apply_carry",
    "normalize",
]
