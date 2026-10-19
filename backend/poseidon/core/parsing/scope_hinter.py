"""The scope hinter (reports design 7.2, Phase 17 PR B): a lexicon stage
after ``skill_hinter`` that sets ``ParsedTurn.scope_hint`` to ``"book"`` or
``"share"``. Advisory: the router decides; the state block shows the hint.

Matching is the skill hinter's discipline (casefolded, normalised, whole
phrase) with one adjustment: a lexeme that starts or ends with a non-word
character ("% of") gets no word boundary on that side, because ``\b``
cannot sit between a space and a ``%``. ``share`` wins over ``book`` when
both match (plan ruling 9).
"""

import re

from poseidon.core.parsing.lexicon import SCOPE_HINTS
from poseidon.core.parsing.normalize import normalize


def _pattern(lexeme: str) -> re.Pattern[str]:
    left = r"\b" if lexeme[0].isalnum() else ""
    right = r"\b" if lexeme[-1].isalnum() else ""
    return re.compile(left + re.escape(lexeme) + right)


_PATTERNS = {lexeme: _pattern(lexeme) for lexeme in SCOPE_HINTS}


def scope_hint(text: str) -> str | None:
    cleaned = normalize(text).casefold()
    matched = {
        SCOPE_HINTS[lexeme] for lexeme, pattern in _PATTERNS.items() if pattern.search(cleaned)
    }
    if "share" in matched:
        return "share"
    if "book" in matched:
        return "book"
    return None
