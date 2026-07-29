"""The deterministic pre-LLM parsing pipeline (doc 02 section 5).

Runs on every inbound chat message before any LLM call, and is complete as
of Phase 4. :func:`parse_turn` is the entry point the rest of the product
calls; everything else here is a stage it composes, exported because each
one is independently useful (and independently tested):

* ``normalize`` -- strip, NFC-compose, collapse whitespace. Always first;
  every other stage assumes its output.
* ``types`` -- the frozen shapes the whole pipeline speaks in
  (``ParseIssue``, ``ResolvedEntity``, ``CandidateSkill``, ``ParsedTurn``).
* ``carry`` -- ``apply_carry`` folds a turn's ``SlotUpdates`` onto the prior
  ``ConversationSlots`` under the omit(``UNSET``)/clear(``None``)/replace
  rule.
* ``period_parser.parse_periods`` -- date prose to the
  ``{period_a, period_b}`` window pair, validated against the entity's
  available range.
* ``customer_resolver.resolve`` -- a customer or port PHRASE to a certified
  dimension VALUE, over the exact/token/fuzzy tiers, or a clarification
  issue instead of a guess.
* ``skill_hinter.hint`` -- an ADVISORY ranked shortlist of candidate skills
  from the ``lexicon`` keyword tables; it never dispatches.
* ``pipeline.parse_turn`` -- all of the above in one deterministic pass over
  one message, returning a ``ParsedTurn``.

Importing this package has no side effects beyond importing those modules.
"""

from .carry import UNSET, SlotUpdates, apply_carry
from .customer_resolver import resolve
from .normalize import normalize
from .period_parser import parse_periods
from .pipeline import parse_turn
from .skill_hinter import hint
from .types import CandidateSkill, ParsedTurn, ParseIssue, ResolvedEntity

__all__ = [
    "CandidateSkill",
    "ParseIssue",
    "ParsedTurn",
    "ResolvedEntity",
    "SlotUpdates",
    "UNSET",
    "apply_carry",
    "hint",
    "normalize",
    "parse_periods",
    "parse_turn",
    "resolve",
]
