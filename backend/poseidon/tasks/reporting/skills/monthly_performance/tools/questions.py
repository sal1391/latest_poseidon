"""Suggested questions: keep the model's chips whose entity mentions resolve against
the payload, then backfill deterministically to exactly three.

A question is never rewritten. A capitalised word the payload cannot account for
(an entity name or alias, a metric word, a month, a scope or period word) is an
unresolved mention and drops the whole question.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from poseidon.tasks.reporting.skills.monthly_performance.tools.grounding import GroundingIndex

_MONTHS = "january february march april may june july august september october november december"
KNOWN_WORDS: frozenset[str] = frozenset(
    (
        "gp volume margin inquiries inquiry won lost win rate fixtures "
        + _MONTHS
        + " office port ports customer customers supplier suppliers book whole deal class"
        " report month total change"
    ).split()
)

_LEADING = "(\"'*[_"
_TRAILING = "?,.!:;)\"'*]_"
_JOINER = "&"
_TARGET = 3


def _clean(word: str) -> str:
    """Strip surrounding punctuation and a trailing possessive."""
    word = word.lstrip(_LEADING)
    while True:
        stripped = word.rstrip(_TRAILING)
        if stripped.endswith("'s"):
            stripped = stripped[:-2]
        if stripped == word:
            return word
        word = stripped


def _payload_words(payload: Mapping[str, Any]) -> frozenset[str]:
    texts = [
        (payload.get("scope") or {}).get("label"),
        (payload.get("entity") or {}).get("label"),
        *(
            ((payload.get("periods") or {}).get(side) or {}).get("label")
            for side in ("current", "prior")
        ),
    ]
    return frozenset(
        _clean(w).casefold() for t in texts if isinstance(t, str) for w in t.split() if _clean(w)
    )


def _runs(words: Sequence[str]) -> Iterable[list[str]]:
    """Maximal runs of capitalised words; '&' between two of them joins the run."""
    run: list[str] = []
    for i, word in enumerate(words):
        if word[:1].isupper():
            run.append(word)
        elif word == _JOINER and run and i + 1 < len(words) and words[i + 1][:1].isupper():
            run.append(word)
        else:
            if run:
                yield run
            run = []
    if run:
        yield run


def mention_resolves(question: str, index: GroundingIndex, payload: Mapping[str, Any]) -> bool:
    words = [_clean(w) for w in (question or "").split()][1:]
    words = [w for w in words if w]
    known = KNOWN_WORDS | _payload_words(payload)
    names = sorted(
        (alias.split() for alias in index.aliases if " " in alias),
        key=len,
        reverse=True,
    )
    for run in _runs(words):
        folded = [w.casefold() for w in run]
        i = 0
        while i < len(folded):
            longest = next(
                (len(n) for n in names if folded[i : i + len(n)] == n),
                0,
            )
            if longest:
                i += longest
            elif folded[i] in index.aliases or folded[i] in known:
                i += 1
            else:
                return False
    return True


def _top_mover(payload: Mapping[str, Any]) -> str | None:
    """The mover with the largest absolute GP change; a tie goes to the decrease."""
    movers = payload.get("movers") or {}
    best: tuple[float, int, str] | None = None
    for rank, group in ((1, "decreases"), (0, "increases")):
        for mover in movers.get(group) or ():
            magnitude = abs(float(((mover.get("change") or {}).get("GP")) or 0.0))
            candidate = (magnitude, rank, mover["key"])
            if best is None or candidate[:2] > best[:2]:
                best = candidate
    return best[2] if best else None


def backfill_questions(payload: Mapping[str, Any]) -> list[str]:
    out: list[str] = []
    top = _top_mover(payload)
    if top:
        out.append(f"Why did GP change for {top}?")
        if payload.get("perspective") == "supplier":
            office = (payload.get("scope") or {}).get("office_value")
            out.append(f"Which customers did {top} supply in {office}?")
        else:
            out.append(f"Where else does {top} lift fuel?")
    plural = payload["entity"]["label"].lower() + "s"
    current = payload["periods"]["current"]["label"]
    out += [
        "What were the top ports in the report?",
        "Which deal class drove the margin change?",
        f"Which {plural} drove GP in {current}?",
    ]
    return out


def select_questions(
    candidates: Sequence[str], payload: Mapping[str, Any], index: GroundingIndex
) -> list[str]:
    picked: list[str] = []
    seen: set[str] = set()

    def take(question: str) -> None:
        key = question.casefold()
        if key not in seen and len(picked) < _TARGET:
            seen.add(key)
            picked.append(question)

    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            question = candidate.strip()
            if mention_resolves(question, index, payload):
                take(question)
    for question in backfill_questions(payload):
        take(question)
    return picked[:_TARGET]
