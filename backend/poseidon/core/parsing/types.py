"""Parsed-turn output types (doc 02 section 5): the shapes every stage of
the deterministic parsing pipeline produces on the way to a
:class:`ParsedTurn`. Pure data, no behavior -- the judgment lives in
``normalize``/``carry`` and, from later Phase 4 tasks, ``period_parser``/
``customer_resolver``/``skill_hinter``/``pipeline``.
"""

from dataclasses import dataclass

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.skills.context import ConversationSlots


@dataclass(frozen=True)
class ParseIssue:
    """Something the deterministic pipeline could not resolve cleanly.

    Never raised -- collected in :attr:`ParsedTurn.issues` so a turn with
    an unresolvable customer or an out-of-range period still produces a
    clarification instead of an exception (doc 02 section 6:
    invalid/ambiguous parse gets clarification chips, not a guess).
    """

    code: str  # "period_unavailable" | "customer_ambiguous" | "customer_unknown"
    message: str  # human text, pinned in tests
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolvedEntity:
    """A customer or port mention resolved to a certified dimension value."""

    value: str  # the certified dimension value
    source_text: str
    confidence: float  # 1.0 exact/token, else fuzzy score
    tier: str  # "exact" | "token" | "fuzzy"


@dataclass(frozen=True)
class CandidateSkill:
    """One entry of the skill hinter's advisory, ranked shortlist."""

    skill_id: str
    score: float


@dataclass(frozen=True)
class ParsedTurn:
    """The deterministic pipeline's complete output for one inbound message.

    ``slots`` is POST-carry -- what the turn resolved to, after
    :func:`~poseidon.core.parsing.carry.apply_carry` folds this turn's
    updates onto the prior conversation state, not the raw slots the turn
    walked in with. ``issues`` empty means a clean parse; a non-empty tuple
    never raises -- the caller decides whether/how to surface a
    clarification.
    """

    normalized_text: str
    slots: ConversationSlots
    period_a: PeriodWindow | None
    period_b: PeriodWindow | None  # comparison window when the text asks for one
    customer: ResolvedEntity | None
    port: ResolvedEntity | None
    hints: tuple[CandidateSkill, ...]
    issues: tuple[ParseIssue, ...]  # empty = clean parse
