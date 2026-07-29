"""Table-driven tests for Phase 4 Task 3: the deterministic three-tier
customer/port resolver (doc 02 section 5) -- exact, token-set, and fuzzy
resolution, the fuzzy candidate band, and the two issue codes.

What the table pins, tier by tier:

* exact casefold equality, including a mixed-case phrase against a
  differently-cased certified value;
* token-set equality -- word order AND punctuation both ignored;
* a fuzzy match strong enough to auto-apply (a one-letter typo);
* the fuzzy candidate band with REAL (non-monkeypatched) rapidfuzz scores,
  proving the tier actually calls rapidfuzz correctly end to end, and
  pinning this module's reading of "top 3, best-first": the tuple is the
  top 3 by score across ALL of ``values``, not filtered to just the ones
  that individually clear ``CANDIDATE_THRESHOLD`` (see the module
  docstring's "Ties" section for the reasoning) -- so two much weaker
  runners-up ride along with the one plausible match in this case;
* the "no match at all" (unknown) outcome, including the empty-phrase edge;
* ``kind="port"`` changing the unknown message's wording while the issue
  CODE stays ``customer_unknown`` (the module's documented vocabulary has
  no ``port_unknown``);
* tier precedence -- exact beats a plausible fuzzy match elsewhere in
  ``values``, token beats one too -- proving each tier short-circuits
  rather than always ranking every value by fuzzy score;
* the exact-tier tie-break: two values that both casefold-equal the
  phrase resolve alphabetically on the certified value string, regardless
  of which one came first in ``values``.

Separate from the table: the two threshold EDGES (exactly 0.80, exactly
0.60) and the candidate band's max-3/ordering/tie-break rule are pinned
with a monkeypatched ``_score`` rather than crafted natural-language
strings, because hitting an exact float via real rapidfuzz scoring is
brittle -- the monkeypatch replaces only the scoring function, so the real
threshold comparisons, sort, and truncation in ``resolve`` still run.

Non-ASCII characters are never typed directly in this file (there are none
needed for any pinned message here) -- ``test_both_files_are_ascii_on_disk``
enforces that for this file and the resolver module both, matching the
Task 2 convention.
"""

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import NamedTuple

import pytest

from poseidon.core.parsing import customer_resolver
from poseidon.core.parsing.customer_resolver import (
    AUTO_APPLY_THRESHOLD,
    CANDIDATE_THRESHOLD,
    Resolution,
    resolve,
)
from poseidon.core.parsing.types import ParseIssue, ResolvedEntity

# ---------------------------------------------------------------------------
# Fixtures-as-constants
# ---------------------------------------------------------------------------

CUSTOMERS = ("Northstar Lines", "Meridian Shipping", "Acme Corporation")
PORTS = ("Singapore", "Rotterdam", "Long Beach")


class Case(NamedTuple):
    """One complete resolver contract: phrase + values + kind -> exact Resolution."""

    id: str
    phrase: str
    values: tuple[str, ...]
    kind: str
    expected: Resolution


CASES = [
    # -- Tier 1: exact casefold equality -------------------------------
    Case(
        "exact_match_same_case",
        "Northstar Lines",
        CUSTOMERS,
        "customer",
        Resolution(
            ResolvedEntity(
                value="Northstar Lines",
                source_text="Northstar Lines",
                confidence=1.0,
                tier="exact",
            ),
            (),
            None,
        ),
    ),
    Case(
        "exact_match_is_casefolded",
        "NORTHSTAR lines",
        CUSTOMERS,
        "customer",
        Resolution(
            ResolvedEntity(
                value="Northstar Lines",
                source_text="NORTHSTAR lines",
                confidence=1.0,
                tier="exact",
            ),
            (),
            None,
        ),
    ),
    Case(
        "port_exact_match",
        "Singapore",
        PORTS,
        "port",
        Resolution(
            ResolvedEntity(
                value="Singapore", source_text="Singapore", confidence=1.0, tier="exact"
            ),
            (),
            None,
        ),
    ),
    # -- Tier 2: token-set equality --------------------------------------
    Case(
        # The brief's own example: reordered words still match.
        "token_set_match_reordered_words",
        "lines northstar",
        CUSTOMERS,
        "customer",
        Resolution(
            ResolvedEntity(
                value="Northstar Lines",
                source_text="lines northstar",
                confidence=1.0,
                tier="token",
            ),
            (),
            None,
        ),
    ),
    Case(
        # Punctuation is a separator, never part of a token.
        "token_set_match_ignores_punctuation",
        "northstar, lines.",
        CUSTOMERS,
        "customer",
        Resolution(
            ResolvedEntity(
                value="Northstar Lines",
                source_text="northstar, lines.",
                confidence=1.0,
                tier="token",
            ),
            (),
            None,
        ),
    ),
    # -- Tier 3: fuzzy ----------------------------------------------------
    Case(
        # A one-letter typo among competing values -- proves "best-scored"
        # selection, not just single-candidate matching.
        "fuzzy_auto_apply_typo",
        "Northstar Linez",
        CUSTOMERS,
        "customer",
        Resolution(
            ResolvedEntity(
                value="Northstar Lines",
                source_text="Northstar Linez",
                confidence=0.9333333333333332,
                tier="fuzzy",
            ),
            (),
            None,
        ),
    ),
    Case(
        # Real rapidfuzz scores (not monkeypatched): "Acme Corporation"
        # scores 0.72 (in the candidate band) while "Northstar Lines"
        # (0.333) and "Meridian Shipping" (0.308) are nowhere close to
        # plausible -- yet both ride along as the 2nd/3rd candidates
        # because this module's top-3 is unfiltered by
        # CANDIDATE_THRESHOLD per value. "Zenith Freight" (0.174) is the
        # 4th-best and is the one entry the max-3 cap actually excludes.
        "candidate_band_natural_scores_top_three_unfiltered",
        "acme corp",
        ("Acme Corporation", "Meridian Shipping", "Northstar Lines", "Zenith Freight"),
        "customer",
        Resolution(
            None,
            ("Acme Corporation", "Northstar Lines", "Meridian Shipping"),
            ParseIssue(
                "customer_ambiguous",
                "did you mean one of: Acme Corporation, Northstar Lines, Meridian Shipping?",
                ("Acme Corporation", "Northstar Lines", "Meridian Shipping"),
            ),
        ),
    ),
    # -- Unknown, including the empty-phrase edge ------------------------
    Case(
        "unknown_completely_unrelated_phrase",
        "banana boats inc",
        CUSTOMERS,
        "customer",
        Resolution(
            None,
            (),
            ParseIssue("customer_unknown", "no customer matching 'banana boats inc'"),
        ),
    ),
    Case(
        "empty_phrase_is_unknown",
        "",
        CUSTOMERS,
        "customer",
        Resolution(None, (), ParseIssue("customer_unknown", "no customer matching ''")),
    ),
    # -- kind changes wording, never the code -----------------------------
    Case(
        "kind_port_changes_unknown_message_wording_not_code",
        "nowhere port",
        PORTS,
        "port",
        Resolution(
            None,
            (),
            ParseIssue("customer_unknown", "no port matching 'nowhere port'"),
        ),
    ),
    # -- Tier precedence: earlier tiers short-circuit later ones ---------
    Case(
        "exact_tier_wins_over_a_plausible_fuzzy_match_elsewhere_in_values",
        "acme",
        ("ACME", "Acme Universal Freight Systems"),
        "customer",
        Resolution(
            ResolvedEntity(value="ACME", source_text="acme", confidence=1.0, tier="exact"),
            (),
            None,
        ),
    ),
    Case(
        # "Northstar Linez" tokenizes to {"northstar", "linez"} -- it would
        # only ever be a fuzzy candidate -- while "Northstar Lines"
        # token-matches outright, so tier 2 resolves before tier 3 is
        # ever consulted.
        "token_tier_wins_over_a_plausible_fuzzy_match_elsewhere_in_values",
        "lines northstar",
        ("Northstar Linez", "Northstar Lines"),
        "customer",
        Resolution(
            ResolvedEntity(
                value="Northstar Lines",
                source_text="lines northstar",
                confidence=1.0,
                tier="token",
            ),
            (),
            None,
        ),
    ),
    # -- Determinism of the exact tier's own tie-break --------------------
    Case(
        # Both "acme" and "ACME" casefold-match; list order is deliberately
        # the "wrong" way round (lowercase first) to prove the result comes
        # from sorting the certified value string, not from ``values``'
        # order.
        "exact_tier_ties_break_alphabetically_on_the_certified_value",
        "acme",
        ("acme", "ACME"),
        "customer",
        Resolution(
            ResolvedEntity(value="ACME", source_text="acme", confidence=1.0, tier="exact"),
            (),
            None,
        ),
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_resolve_case(case: Case):
    assert resolve(case.phrase, case.values, case.kind) == case.expected


def test_case_ids_are_unique():
    ids = [case.id for case in CASES]
    assert len(set(ids)) == len(ids)


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_resolve_is_deterministic(case: Case):
    """Same inputs, same output -- no randomness, no hidden state, no
    dependence on ``values``' iteration order beyond what the documented
    alphabetical tie-break already accounts for."""
    first = resolve(case.phrase, case.values, case.kind)
    second = resolve(case.phrase, case.values, case.kind)
    assert first == second


def test_ambiguous_message_never_mentions_kind():
    """Unlike the unknown message, "did you mean one of: ...?" is the same
    text regardless of ``kind`` -- pinned separately from the table since
    it is a negative assertion (absence of a word), not a value equality."""
    result = resolve(
        "acme corp",
        ("Acme Corporation", "Meridian Shipping", "Northstar Lines", "Zenith Freight"),
        kind="port",
    )
    assert result.issue is not None
    assert result.issue.code == "customer_ambiguous"
    assert result.issue.message == (
        "did you mean one of: Acme Corporation, Northstar Lines, Meridian Shipping?"
    )
    assert "port" not in result.issue.message


def test_empty_values_list_is_unknown_not_a_crash():
    result = resolve("anything", ())
    assert result.entity is None
    assert result.candidates == ()
    assert result.issue == ParseIssue("customer_unknown", "no customer matching 'anything'")


# ---------------------------------------------------------------------------
# Threshold edges and the candidate band's shape -- monkeypatched scorer
# ---------------------------------------------------------------------------


def test_score_of_exactly_the_auto_apply_threshold_auto_applies(monkeypatch):
    """0.80 is closed toward the auto tier, not the candidate band. Hitting
    this exactly with a real rapidfuzz score over natural strings is
    brittle, so the scorer is replaced -- everything downstream of it
    (the >= comparison, the Resolution shape) is still the real code."""
    monkeypatch.setattr(
        customer_resolver, "_score", lambda phrase_cf, value_cf: AUTO_APPLY_THRESHOLD
    )

    result = resolve("whatever the user typed", ("Only Certified Value",))

    assert result.entity == ResolvedEntity(
        value="Only Certified Value",
        source_text="whatever the user typed",
        confidence=AUTO_APPLY_THRESHOLD,
        tier="fuzzy",
    )
    assert result.candidates == ()
    assert result.issue is None


def test_score_of_exactly_the_candidate_threshold_lands_in_the_band(monkeypatch):
    """0.60 is closed toward the candidate band, not unknown."""
    monkeypatch.setattr(
        customer_resolver, "_score", lambda phrase_cf, value_cf: CANDIDATE_THRESHOLD
    )

    result = resolve("whatever the user typed", ("Only Certified Value",))

    assert result.entity is None
    assert result.candidates == ("Only Certified Value",)
    assert result.issue == ParseIssue(
        "customer_ambiguous",
        "did you mean one of: Only Certified Value?",
        ("Only Certified Value",),
    )


def test_score_just_under_the_candidate_threshold_is_unknown(monkeypatch):
    monkeypatch.setattr(
        customer_resolver, "_score", lambda phrase_cf, value_cf: CANDIDATE_THRESHOLD - 0.01
    )

    result = resolve("whatever the user typed", ("Only Certified Value",))

    assert result.entity is None
    assert result.candidates == ()
    assert result.issue == ParseIssue(
        "customer_unknown", "no customer matching 'whatever the user typed'"
    )


def test_candidate_band_caps_at_three_best_first_with_alphabetical_tie_break(monkeypatch):
    """The brief's required proof: max 3, best-first, ties alphabetical --
    with 5 values and full control over every score, so the ordering can
    only come from the real sort/truncation logic in ``resolve``, not from
    which natural strings happened to be picked."""
    scores_by_value_cf = {
        "zeta corp": 0.70,
        "beta corp": 0.70,  # ties Zeta Corp -- tie-break decides the order
        "alpha corp": 0.65,
        "delta corp": 0.61,  # 4th-best -- excluded by the max-3 cap alone
        "omega corp": 0.10,  # excluded by the cap AND far below the band
    }

    def fake_score(phrase_cf: str, value_cf: str) -> float:
        return scores_by_value_cf[value_cf]

    monkeypatch.setattr(customer_resolver, "_score", fake_score)

    result = resolve(
        "some garbled query",
        ("Zeta Corp", "Beta Corp", "Alpha Corp", "Delta Corp", "Omega Corp"),
    )

    # Both 0.70s outrank the 0.65 regardless of the tie-break -- the
    # tie-break only decides order WITHIN a tie (Beta before Zeta), it
    # never lets a tied pair drop below a strictly lower score.
    assert result.entity is None
    assert result.candidates == ("Beta Corp", "Zeta Corp", "Alpha Corp")
    assert result.issue == ParseIssue(
        "customer_ambiguous",
        "did you mean one of: Beta Corp, Zeta Corp, Alpha Corp?",
        ("Beta Corp", "Zeta Corp", "Alpha Corp"),
    )


# ---------------------------------------------------------------------------
# Shape and hygiene
# ---------------------------------------------------------------------------


def test_resolution_is_frozen():
    result = Resolution(entity=None, candidates=(), issue=None)
    with pytest.raises(FrozenInstanceError):
        result.entity = None  # type: ignore[misc]


def test_resolution_exposes_the_three_interface_fields():
    entity = ResolvedEntity(value="Acme", source_text="acme", confidence=1.0, tier="exact")
    result = Resolution(entity=entity, candidates=(), issue=None)
    assert result.entity is entity
    assert result.candidates == ()
    assert result.issue is None


def test_auto_apply_and_candidate_threshold_values():
    assert AUTO_APPLY_THRESHOLD == 0.80
    assert CANDIDATE_THRESHOLD == 0.60


def test_module_does_not_import_normalize():
    """The resolver assumes ``normalize`` already ran (Task 4's pipeline
    contract: normalize once, feed the same text to the period parser and
    this resolver) -- importing it here would suggest this module owns
    normalization, which it does not. Checked over the AST, same technique
    Task 2 uses to pin its own module-level contract, so a stray import
    added later fails a specific test instead of just looking odd in review.
    """
    tree = ast.parse(Path(customer_resolver.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "poseidon.core.parsing.normalize" not in imported


def test_module_documents_that_kind_only_changes_message_wording():
    doc = customer_resolver.__doc__ or ""
    assert "kind" in doc
    assert "customer_*" in doc


def test_both_files_are_ascii_on_disk():
    """The byte-pinned messages only stay pinned if no look-alike codepoint
    can slip into either file -- see the Task 2 convention this mirrors."""
    for path in (Path(customer_resolver.__file__), Path(__file__)):
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
