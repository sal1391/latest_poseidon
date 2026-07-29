"""The deterministic skill hinter (doc 02 section 5): a keyword/intent
lexicon that produces a ranked, ADVISORY shortlist of candidate skills for
the router. It never dispatches anything itself ("hints are advisory
context for the router, never a hard dispatch" -- doc 02 section 5's own
description of this stage).

Matching contract
------------------
Every lexeme in ``lexicon.KEYWORDS``/``lexicon.MODE_HINTS`` is matched
against the CASEFOLDED, whitespace-normalized text with a WORD-BOUNDARY
regex, so a lexeme like "top" matches the standalone word "top" but never a
substring inside a longer word ("stopped"); a multi-word lexeme ("gross
profit", "win rate", "new customer") matches only as an exact, adjacent
phrase (its own internal single space), so "gross profit" does not match
"gross domestic profit". Each lexeme contributes its weight AT MOST ONCE per
call, no matter how many times it occurs in the text -- ``hint`` is an
advisory PRESENCE signal, not a word-frequency counter, so "top, top, top"
scores the same as a single "top".

``slots`` and MODE_HINTS
--------------------------
Two of ``lexicon.KEYWORDS``' entries (the brief words) name BOTH brief
skills at equal weight -- vocabulary alone cannot say which brief a plain
"give me an overview" wants. ``lexicon.MODE_HINTS`` breaks that tie: its
phrases each add extra weight to the one brief skill they name. ``slots``
matters here for the same reason conversation state matters everywhere else
in this pipeline (period/customer carry, doc 02 section 5): a mode phrase
should not have to be repeated on every turn. When ``slots.mode`` already
holds one of the two values ``lexicon.MODE_SLOT_ALIASES`` recognizes, its
matching phrase counts as matched too -- a phrase restated in the text and a
carried ``slots.mode`` naming the SAME alias are deduplicated (a set of
matched phrase-keys, not a per-source tally), so saying it twice never
doubles the weight.

This module never touches ``data``, the network, or an LLM -- pure text-in,
ranked-list-out, like every other Phase 4 stage.
"""

import re

from poseidon.core.parsing.lexicon import KEYWORDS, MODE_HINTS, MODE_SLOT_ALIASES
from poseidon.core.parsing.normalize import normalize
from poseidon.core.parsing.types import CandidateSkill
from poseidon.core.skills.context import ConversationSlots

# Compiled once at import time, one pattern per lexeme (from both tables) --
# matches the precompiled-pattern convention ``customer_resolver._WORD_RE``
# already uses. ``re.escape`` keeps a multi-word lexeme's internal space
# literal while still escaping anything that would otherwise be regex syntax.
_LEXEME_PATTERNS: dict[str, re.Pattern[str]] = {
    lexeme: re.compile(r"\b" + re.escape(lexeme) + r"\b") for lexeme in (*KEYWORDS, *MODE_HINTS)
}


def hint(text: str, slots: ConversationSlots) -> tuple[CandidateSkill, ...]:
    """Rank candidate skills for ``text`` (see the module docstring).

    Self-normalizing and self-casefolding, like ``period_parser.parse_periods``:
    callers hand it raw-ish text, not pre-cleaned text. Deterministic -- sums
    each matched lexeme's weight per skill id, then sorts by
    ``(-score, skill_id)`` so a tie always breaks the same way. Returns an
    empty tuple when nothing matches: an empty shortlist is a valid, harmless
    outcome (advisory only -- the router still sees the full registry).
    """
    cleaned = normalize(text).casefold()

    matched_lexemes = {lexeme for lexeme in KEYWORDS if _LEXEME_PATTERNS[lexeme].search(cleaned)}
    matched_mode_phrases = {
        phrase for phrase in MODE_HINTS if _LEXEME_PATTERNS[phrase].search(cleaned)
    }
    alias = MODE_SLOT_ALIASES.get(slots.mode)
    if alias is not None:
        matched_mode_phrases.add(alias)  # set union: a restated phrase does not double-count

    scores: dict[str, float] = {}
    for lexeme in matched_lexemes:
        for skill_id, weight in KEYWORDS[lexeme]:
            scores[skill_id] = scores.get(skill_id, 0.0) + weight
    for phrase in matched_mode_phrases:
        for skill_id, weight in MODE_HINTS[phrase]:
            scores[skill_id] = scores.get(skill_id, 0.0) + weight

    ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return tuple(CandidateSkill(skill_id=skill_id, score=score) for skill_id, score in ranked)
