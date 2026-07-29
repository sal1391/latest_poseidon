"""The deterministic customer/port resolver (doc 02 section 5): turns a
customer or port PHRASE into a certified dimension VALUE -- no LLM call, and
no fuzzy guess promoted to fact without a confidence tier attached to it.

Normalization contract
-----------------------
``resolve`` expects ``phrase`` to already have passed through
:func:`~poseidon.core.parsing.normalize.normalize` -- NFC-composed,
whitespace-collapsed -- because Task 4's pipeline runs ``normalize`` once
and both the period parser and this resolver consume that same normalized
text. This module deliberately does not import or call ``normalize``:
doing so would suggest it owns normalization, and it does not. The only
text transform this module performs itself is casefolding, used purely for
comparison -- a certified value is never casefolded before it is returned,
so ``ResolvedEntity.value`` and every entry of ``candidates`` stay
display-ready (the caller shows them to the user as-is, e.g. as
clarification chips).

Tiers, first hit wins
----------------------
1. **exact** -- ``phrase.casefold() == value.casefold()``. Confidence 1.0.
2. **token** -- casefolded WORD SETS equal, order- and
   punctuation-insensitive ("lines northstar" and "Northstar Lines." both
   reduce to {"lines", "northstar"}). Confidence 1.0.
3. **fuzzy** -- ``rapidfuzz.fuzz.token_set_ratio`` against every value,
   scaled to 0..1, best-scored wins. A score >= ``AUTO_APPLY_THRESHOLD``
   (0.80) auto-applies as a resolved entity; ``[CANDIDATE_THRESHOLD,
   AUTO_APPLY_THRESHOLD)`` -- ``[0.60, 0.80)`` -- returns up to
   ``_MAX_CANDIDATES`` (3) candidates, best-first, as a
   ``customer_ambiguous`` issue instead of guessing; below
   ``CANDIDATE_THRESHOLD`` returns a ``customer_unknown`` issue. Both edges
   are closed toward the HIGHER tier: exactly 0.80 auto-applies, exactly
   0.60 lands in the candidate band rather than being unknown.

The fuzzy candidate tuple is the top 3 by score across ALL of ``values``,
not filtered to only the entries that individually clear
``CANDIDATE_THRESHOLD`` -- so a values list with only one plausible entry
can still surface 2 much weaker runners-up alongside it. The brief specifies
the tuple as "top 3, best-first" with no secondary per-candidate cutoff;
this is the literal reading of that, and it keeps the classification rule a
function of the single best score alone. Revisit if real clarification
chips turn out noisy in practice.

Ties -- identical scores, or more than one value exact/token-matching the
same phrase -- break alphabetically on the certified VALUE string (Python's
default codepoint ordering, not a locale collation): every tier sorts its
matches by ``(-score, value)`` (score 1.0 for tiers 1-2) before taking the
first entry, so ranking is a total order and two calls with the same inputs
can never disagree about which value is "best."

rapidfuzz and case: ``fuzz.token_set_ratio`` is CASE-SENSITIVE by default
(verified against the installed rapidfuzz>=3.9 -- there is no hidden
casefolding unless the caller passes ``processor=utils.default_process``).
Both sides of every tier-3 comparison are casefolded before scoring so tier
3 stays case-insensitive like tiers 1 and 2; see ``_score`` for exactly
where that happens and why it is its own function.

``kind`` (``"customer"`` or ``"port"``) changes only the
``customer_unknown`` message's wording ("no customer matching ..." vs.
"no port matching ..."); the ``customer_ambiguous`` message never names a
kind. Both issue CODES stay ``customer_*`` regardless of ``kind`` --
``ParseIssue.code``'s documented vocabulary (``types.py``) has no
``port_ambiguous``/``port_unknown`` -- so a failed port lookup still reports
``customer_ambiguous``/``customer_unknown``. Intentional, not a copy-paste
leftover: callers branch on ``kind`` (which they already know, since they
called ``resolve`` with it), not on the issue code, to tell a customer miss
from a port miss.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz

from poseidon.core.parsing.types import ParseIssue, ResolvedEntity

AUTO_APPLY_THRESHOLD = 0.80
CANDIDATE_THRESHOLD = 0.60

_TIER_EXACT = "exact"
_TIER_TOKEN = "token"
_TIER_FUZZY = "fuzzy"

_ISSUE_AMBIGUOUS = "customer_ambiguous"
_ISSUE_UNKNOWN = "customer_unknown"

# "did you mean one of: A, B, C?" only ever shows this many -- a long tail
# of weak fuzzy matches would not help the user pick.
_MAX_CANDIDATES = 3

# Punctuation- and separator-insensitive word splitting for tier 2. ``\w``
# matches Unicode letters/digits/underscore by default for a ``str``
# pattern, so an accented certified value tokenizes the same way a plain
# ASCII one does.
_WORD_RE = re.compile(r"\w+")


@dataclass(frozen=True)
class Resolution:
    """One ``resolve()`` call's outcome.

    Either ``entity`` is set (a tier resolved with confidence) or ``issue``
    is set (nothing was confident enough to apply automatically) -- never
    both, never neither: exactly one tier below always fires.
    """

    entity: ResolvedEntity | None
    candidates: tuple[str, ...]  # populated only in the fuzzy candidate band, best-first, max 3
    issue: ParseIssue | None  # "customer_ambiguous" (with candidates) | "customer_unknown"


# ---------------------------------------------------------------------------
# Tier 2: token-set equality
# ---------------------------------------------------------------------------


def _token_set(text: str) -> frozenset[str]:
    """The casefolded set of word tokens in ``text``.

    A SET, not a sequence -- word order never matters ("lines northstar"
    and "Northstar Lines" both reduce to {"lines", "northstar"}) -- built
    from ``\\w+`` runs, so any stretch of punctuation or whitespace between
    words is just a separator ("Northstar, Lines." tokenizes the same as
    "Northstar Lines").
    """
    return frozenset(_WORD_RE.findall(text.casefold()))


# ---------------------------------------------------------------------------
# Tier 3: fuzzy score
# ---------------------------------------------------------------------------


def _score(phrase_cf: str, value_cf: str) -> float:
    """``token_set_ratio``, scaled from rapidfuzz's 0..100 to this module's
    0..1.

    Both arguments must already be casefolded by the caller.
    ``fuzz.token_set_ratio`` is CASE-SENSITIVE by default -- there is no
    implicit casefolding unless the caller passes
    ``processor=utils.default_process`` or, as here, pre-folds the inputs
    itself -- so scoring "NORTHSTAR LINES" against "Northstar Lines"
    without this would score far below the same comparison in lowercase,
    silently disagreeing with tiers 1 and 2 about whether case matters.

    Kept as a separately-named function so tests can monkeypatch exactly
    this one call to force an exact 0.80/0.60 threshold-edge score --
    hitting those exactly via natural strings is brittle, since rapidfuzz's
    ratio is a function of edit distance over token lengths -- while still
    exercising the real banding/sorting/tie-break logic in :func:`resolve`
    around it.
    """
    return fuzz.token_set_ratio(phrase_cf, value_cf) / 100


# ---------------------------------------------------------------------------
# Shared result builders
# ---------------------------------------------------------------------------


def _resolved(value: str, phrase: str, confidence: float, tier: str) -> Resolution:
    """A ``Resolution`` for a tier that resolved outright (exact, token, or
    an auto-applied fuzzy match): an entity, no candidates, no issue."""
    entity = ResolvedEntity(value=value, source_text=phrase, confidence=confidence, tier=tier)
    return Resolution(entity=entity, candidates=(), issue=None)


def _unknown(phrase: str, kind: str) -> Resolution:
    """No tier produced anything worth showing: no entity, no candidates."""
    issue = ParseIssue(_ISSUE_UNKNOWN, f"no {kind} matching {phrase!r}")
    return Resolution(entity=None, candidates=(), issue=issue)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def resolve(phrase: str, values: Sequence[str], kind: str = "customer") -> Resolution:
    """Resolve ``phrase`` to one of ``values`` under the three-tier contract
    described in the module docstring: exact, then token-set, then fuzzy.

    ``values`` should be the FULL certified list for this call (e.g. every
    customer name in the ontology) -- ``resolve`` alone decides which one,
    if any, ``phrase`` names; it does not expect a pre-filtered shortlist.
    ``kind`` (``"customer"`` or ``"port"``) only changes the
    ``customer_unknown`` message's wording -- see the module docstring for
    why the issue CODE never becomes ``port_unknown``.
    """
    phrase_cf = phrase.casefold()

    exact_matches = sorted(value for value in values if value.casefold() == phrase_cf)
    if exact_matches:
        return _resolved(exact_matches[0], phrase, 1.0, _TIER_EXACT)

    phrase_tokens = _token_set(phrase)
    token_matches = sorted(value for value in values if _token_set(value) == phrase_tokens)
    if token_matches:
        return _resolved(token_matches[0], phrase, 1.0, _TIER_TOKEN)

    # (-score, value): best score first, ties broken alphabetically on the
    # certified value string -- a total order, so two runs over the same
    # inputs can never disagree about which value ranks where.
    scored = sorted(
        ((_score(phrase_cf, value.casefold()), value) for value in values),
        key=lambda pair: (-pair[0], pair[1]),
    )
    if not scored:
        return _unknown(phrase, kind)

    best_score, best_value = scored[0]
    if best_score >= AUTO_APPLY_THRESHOLD:
        return _resolved(best_value, phrase, best_score, _TIER_FUZZY)

    if best_score >= CANDIDATE_THRESHOLD:
        top = tuple(value for _, value in scored[:_MAX_CANDIDATES])
        issue = ParseIssue(_ISSUE_AMBIGUOUS, f"did you mean one of: {', '.join(top)}?", top)
        return Resolution(entity=None, candidates=top, issue=issue)

    return _unknown(phrase, kind)
