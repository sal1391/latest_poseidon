"""Tests for Phase 4 Task 4: the skill hinter (doc 02 section 5's advisory
keyword lexicon) and ``parse_turn`` -- the single function that composes
every earlier Phase 4 stage (normalize, customer/port resolution, period
parsing, slot carry) into one deterministic pass over an inbound chat
message.

Four things are pinned here:

* ``skill_hinter.hint`` -- the weights/order/empty truth table over
  ``lexicon.KEYWORDS``/``MODE_HINTS``: every lexeme fires with its stated
  weight, word-boundary matching rejects a lexeme hiding inside a longer
  word, multi-word lexemes match only as adjacent phrases, ties break
  alphabetically on skill id, and ``slots.mode`` contributes the same
  signal as a restated mode phrase without double-counting it.
* ``pipeline.parse_turn`` against a fake, offline ``DataClient`` -- the
  flagship case, the masking case and its two counter-cases (a lowercase
  bare month is only ever a period; an unresolved month-named customer is
  never masked and can shadow-parse, a documented, accepted residual), slot
  carry (including the customer-carry-as-phrase design -- see
  ``pipeline``'s own module docstring), explicit clear/reset phrases, the
  conservative detection rule in BOTH directions (an unmatched TitleCase run
  with no cue word never produces an issue, even when it exactly names a
  real customer; a STRONG cue word plus a run that fails to resolve DOES),
  the strong/weak port cue split (the same missing port surfaces an issue
  after "port of" and stays silent after "at", for both the unknown and the
  candidate-band outcome), the ``period_b`` slot clearing when a follow-up
  turn asks no comparison, and multi-issue ordering (customer, then port,
  then period).
* ``@pytest.mark.pg`` goldens -- the resolver and the full pipeline against
  the live seeded Postgres database (the established skip-guard pattern from
  ``test_synthetic_client_pg.py``).
* ASCII-only source, matching the Task 2/3 convention: this file and the
  three modules it tests (``lexicon.py``, ``skill_hinter.py``,
  ``pipeline.py``) must decode as plain ASCII, so a byte-pinned message can
  never be silently defeated by a look-alike codepoint.

Non-ASCII characters in expected strings are written as explicit
``\\uXXXX`` escapes (see ``_EM_DASH``), per the Task 2 convention this
mirrors.
"""

import ast
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import NamedTuple

import psycopg
import pytest

from poseidon.core.data.client import PeriodRange
from poseidon.core.data.specs import PeriodWindow
from poseidon.core.data.synthetic_client import SyntheticDataClient, normalize_dsn
from poseidon.core.parsing import lexicon, pipeline, skill_hinter
from poseidon.core.parsing.customer_resolver import resolve
from poseidon.core.parsing.lexicon import (
    EXISTING_CUSTOMER_BRIEF,
    METRIC_QUERY,
    NEW_PROSPECT_BRIEF,
    WEB_RESEARCH,
)
from poseidon.core.parsing.pipeline import DEFAULT_ENTITY, parse_turn
from poseidon.core.parsing.skill_hinter import hint
from poseidon.core.parsing.types import CandidateSkill, ParsedTurn, ParseIssue, ResolvedEntity
from poseidon.core.skills.context import ConversationSlots

# U+2014 EM DASH, written as an escape so the pinned message below cannot be
# confused with a hyphen-minus or an en dash by an editor or an encoding
# round-trip -- see the Task 2 convention (test_parsing_periods.py).
_EM_DASH = "\u2014"

NO_SLOTS = ConversationSlots()


# ===========================================================================
# skill_hinter.hint -- weights, order, empty
# ===========================================================================


class HinterCase(NamedTuple):
    id: str
    text: str
    slots: ConversationSlots
    expected: tuple[CandidateSkill, ...]


HINTER_CASES = [
    # -- empty / no match ----------------------------------------------
    HinterCase("empty_text_matches_nothing", "", NO_SLOTS, ()),
    HinterCase("ordinary_sentence_matches_nothing", "hello there, how are you", NO_SLOTS, ()),
    # -- word-boundary negatives: a lexeme must not match inside a longer word
    HinterCase(
        "top_does_not_match_inside_stopped",
        "the shipment stopped yesterday",
        NO_SLOTS,
        (),
    ),
    HinterCase(
        "market_does_not_match_inside_marketing",
        "our marketing team called",
        NO_SLOTS,
        (),
    ),
    # -- multi-word phrase negative: interrupted words do not match ----
    HinterCase(
        "gross_profit_phrase_does_not_match_when_interrupted",
        "gross domestic profit",
        NO_SLOTS,
        (),
    ),
    # -- metric words, one row each, weight 1.0 -------------------------
    HinterCase(
        "metric_word_gp",
        "show me gp for the quarter",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    HinterCase(
        "metric_word_gross_profit_phrase",
        "what is our gross profit",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    HinterCase(
        "metric_word_volume",
        "what is total volume",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    HinterCase(
        "metric_word_tons",
        "how many tons moved",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    HinterCase(
        "metric_word_margin",
        "check the margin",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    HinterCase(
        "metric_word_win_rate_phrase",
        "what is our win rate",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    HinterCase(
        "metric_word_top",
        "list the top accounts",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    HinterCase(
        "metric_word_breakdown",
        "give me a breakdown",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    HinterCase(
        "metric_word_compare",
        "please compare the two",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    # -- research words, one row each -----------------------------------
    HinterCase(
        "research_word_news",
        "any recent news",
        NO_SLOTS,
        (CandidateSkill(WEB_RESEARCH, 1.0),),
    ),
    HinterCase(
        "research_word_article",
        "did you see that article",
        NO_SLOTS,
        (CandidateSkill(WEB_RESEARCH, 1.0),),
    ),
    HinterCase(
        "research_word_esg",
        "how is their esg rating",
        NO_SLOTS,
        (CandidateSkill(WEB_RESEARCH, 1.0),),
    ),
    HinterCase(
        "research_word_sustainability",
        "read about their sustainability efforts",
        NO_SLOTS,
        (CandidateSkill(WEB_RESEARCH, 1.0),),
    ),
    HinterCase(
        "research_word_market", "how is the market", NO_SLOTS, (CandidateSkill(WEB_RESEARCH, 1.0),)
    ),
    HinterCase(
        "research_word_competitor",
        "who is their competitor",
        NO_SLOTS,
        (CandidateSkill(WEB_RESEARCH, 1.0),),
    ),
    # -- brief words: both brief skills, tied, alphabetical order -------
    HinterCase(
        "brief_word_brief_ties_both_brief_skills",
        "send me a brief",
        NO_SLOTS,
        (CandidateSkill(EXISTING_CUSTOMER_BRIEF, 1.0), CandidateSkill(NEW_PROSPECT_BRIEF, 1.0)),
    ),
    HinterCase(
        "brief_word_report_ties_both_brief_skills",
        "send me a report",
        NO_SLOTS,
        (CandidateSkill(EXISTING_CUSTOMER_BRIEF, 1.0), CandidateSkill(NEW_PROSPECT_BRIEF, 1.0)),
    ),
    HinterCase(
        "brief_word_profile_ties_both_brief_skills",
        "send me the profile",
        NO_SLOTS,
        (CandidateSkill(EXISTING_CUSTOMER_BRIEF, 1.0), CandidateSkill(NEW_PROSPECT_BRIEF, 1.0)),
    ),
    HinterCase(
        "brief_word_overview_ties_both_brief_skills",
        "send me an overview",
        NO_SLOTS,
        (CandidateSkill(EXISTING_CUSTOMER_BRIEF, 1.0), CandidateSkill(NEW_PROSPECT_BRIEF, 1.0)),
    ),
    # -- mode-hint phrases, one row each ---------------------------------
    HinterCase(
        "mode_hint_prospect",
        "any prospect updates",
        NO_SLOTS,
        (CandidateSkill(NEW_PROSPECT_BRIEF, 1.0),),
    ),
    HinterCase(
        "mode_hint_new_customer_phrase",
        "tell me about this new customer",
        NO_SLOTS,
        (CandidateSkill(NEW_PROSPECT_BRIEF, 1.0),),
    ),
    HinterCase(
        "mode_hint_existing_customer_phrase",
        "loop in the existing customer team",
        NO_SLOTS,
        (CandidateSkill(EXISTING_CUSTOMER_BRIEF, 1.0),),
    ),
    # -- summing within one skill ----------------------------------------
    HinterCase(
        "summing_two_metric_lexemes",
        "top gp customers",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 2.0),),
    ),
    HinterCase(
        # "top" occurs twice -- a matched lexeme counts once, not per
        # occurrence (see skill_hinter.hint's docstring).
        "repeated_lexeme_counts_once",
        "top of the top list",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 1.0),),
    ),
    # -- order: distinct scores across two different skills --------------
    HinterCase(
        "order_by_descending_score_across_skills",
        "top gp customers and any news",
        NO_SLOTS,
        (CandidateSkill(METRIC_QUERY, 2.0), CandidateSkill(WEB_RESEARCH, 1.0)),
    ),
    # -- mode phrase breaks a brief-word tie ------------------------------
    HinterCase(
        "mode_phrase_in_text_breaks_the_brief_tie",
        "give me the existing customer overview",
        NO_SLOTS,
        (CandidateSkill(EXISTING_CUSTOMER_BRIEF, 2.0), CandidateSkill(NEW_PROSPECT_BRIEF, 1.0)),
    ),
    # -- slots.mode alone (no text phrase) breaks the tie, both directions
    HinterCase(
        "slots_mode_existing_customer_breaks_the_tie_without_the_phrase",
        "send an overview please",
        ConversationSlots(mode="existing_customer"),
        (CandidateSkill(EXISTING_CUSTOMER_BRIEF, 2.0), CandidateSkill(NEW_PROSPECT_BRIEF, 1.0)),
    ),
    HinterCase(
        "slots_mode_new_prospect_breaks_the_tie_the_other_direction",
        "send a brief please",
        ConversationSlots(mode="new_prospect"),
        (CandidateSkill(NEW_PROSPECT_BRIEF, 2.0), CandidateSkill(EXISTING_CUSTOMER_BRIEF, 1.0)),
    ),
    # -- text phrase and slots.mode naming the SAME alias do not double count
    HinterCase(
        # If this double-counted, existing_customer_brief would score 3.0
        # (overview + text "existing customer" + slot alias), not 2.0.
        "text_phrase_and_slot_alias_for_the_same_mode_do_not_double_count",
        "existing customer overview",
        ConversationSlots(mode="existing_customer"),
        (CandidateSkill(EXISTING_CUSTOMER_BRIEF, 2.0), CandidateSkill(NEW_PROSPECT_BRIEF, 1.0)),
    ),
    # -- an unrecognized mode value is inert, not a crash -----------------
    HinterCase(
        "unrecognized_slots_mode_is_inert",
        "send me the profile",
        ConversationSlots(mode="something_else"),
        (CandidateSkill(EXISTING_CUSTOMER_BRIEF, 1.0), CandidateSkill(NEW_PROSPECT_BRIEF, 1.0)),
    ),
]


@pytest.mark.parametrize("case", HINTER_CASES, ids=[c.id for c in HINTER_CASES])
def test_hint_case(case: HinterCase):
    assert hint(case.text, case.slots) == case.expected


def test_hinter_case_ids_are_unique():
    ids = [case.id for case in HINTER_CASES]
    assert len(set(ids)) == len(ids)


def test_hinter_table_covers_every_skill_id_in_the_lexicon():
    """Every skill id the lexicon can produce appears in at least one row's
    expected result -- a guard against silently adding a lexicon entry the
    table forgets to exercise."""
    covered_skill_ids = {
        skill_id for case in HINTER_CASES for skill_id in (c.skill_id for c in case.expected)
    }
    every_skill_id = {
        skill_id
        for entries in (*lexicon.KEYWORDS.values(), *lexicon.MODE_HINTS.values())
        for skill_id, _weight in entries
    }
    assert every_skill_id <= covered_skill_ids


@pytest.mark.parametrize("case", HINTER_CASES, ids=[c.id for c in HINTER_CASES])
def test_hint_is_deterministic(case: HinterCase):
    assert hint(case.text, case.slots) == hint(case.text, case.slots)


@pytest.mark.parametrize("case", HINTER_CASES, ids=[c.id for c in HINTER_CASES])
def test_hint_result_is_sorted_by_descending_score_then_skill_id(case: HinterCase):
    result = hint(case.text, case.slots)
    scores = [c.score for c in result]
    assert scores == sorted(scores, reverse=True)
    for i in range(len(result) - 1):
        if result[i].score == result[i + 1].score:
            assert result[i].skill_id < result[i + 1].skill_id


# ===========================================================================
# pipeline.parse_turn -- offline, fake DataClient
# ===========================================================================

CUSTOMERS = (
    "Northstar Lines",
    "Blue Anchor Marine",
    "Crestline Freight",
    "May Shipping",  # the month-named customer the masking rule exists for
    "Acme Corporation",
    "Meridian Shipping",
)
PORTS = ("Singapore", "Rotterdam", "Long Beach")
AVAILABLE = PeriodRange(date(2025, 1, 1), date(2026, 6, 30))
REF = date(2026, 7, 15)  # matches the Task 2 convention (mid-July reference)


@dataclass
class FakeDataClient:
    """A minimal, structural ``DataClient`` (see ``core.data.client``) over
    fixed, in-memory pools -- no network, no database. ``calls`` records
    every ``list_dimension_values``/``available_periods`` invocation so
    tests can prove ``parse_turn`` fetches a pool LAZILY (only when a
    customer/port phrase was actually detected), never unconditionally.
    """

    customers: tuple[str, ...] = CUSTOMERS
    ports: tuple[str, ...] = PORTS
    available: PeriodRange = AVAILABLE
    calls: list[tuple[str, str]] = field(default_factory=list)

    def list_dimension_values(
        self, entity: str, column: str, search: str | None = None
    ) -> list[str]:
        self.calls.append(("list_dimension_values", column))
        pool = {"CUST_NM": self.customers, "LOC_NM": self.ports}[column]
        if search is None:
            return list(pool)
        needle = search.casefold()
        return [value for value in pool if needle in value.casefold()]

    def available_periods(self, entity: str) -> PeriodRange:
        self.calls.append(("available_periods", entity))
        return self.available

    def run_metric_query(self, spec):  # pragma: no cover - parse_turn never calls this
        raise NotImplementedError("parse_turn does not run metric queries")

    def run_breakdown_query(self, spec):  # pragma: no cover
        raise NotImplementedError("parse_turn does not run breakdown queries")


def _month(year: int, month: int) -> PeriodWindow:
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return PeriodWindow(date(year, month, 1), date(end_year, end_month, 1))


def _exact(value: str, source_text: str | None = None) -> ResolvedEntity:
    return ResolvedEntity(
        value=value,
        source_text=source_text if source_text is not None else value,
        confidence=1.0,
        tier="exact",
    )


def _turn(
    text: str,
    slots: ConversationSlots,
    period_a: PeriodWindow | None = None,
    period_b: PeriodWindow | None = None,
    customer: ResolvedEntity | None = None,
    port: ResolvedEntity | None = None,
    hints: tuple[CandidateSkill, ...] = (),
    issues: tuple[ParseIssue, ...] = (),
) -> ParsedTurn:
    return ParsedTurn(
        normalized_text=text,
        slots=slots,
        period_a=period_a,
        period_b=period_b,
        customer=customer,
        port=port,
        hints=hints,
        issues=issues,
    )


_UNAVAILABLE_2019_04 = (
    "no data for 2019-04-01..2019-05-01 " + _EM_DASH + " available 2025-01-01..2026-06-30"
)
_UNAVAILABLE_2026_Q3 = (
    "no data for 2026-07-01..2026-10-01 " + _EM_DASH + " available 2025-01-01..2026-06-30"
)


class PipelineCase(NamedTuple):
    id: str
    message: str
    slots: ConversationSlots
    expected: ParsedTurn


PIPELINE_CASES = [
    PipelineCase(
        # The phase-gate flagship: no customer cue at all, a port cue that
        # resolves and gets masked (harmlessly, since nothing here collides
        # with a period phrase), "top"+"gp" leading the hints, zero issues.
        "flagship_port_resolves_no_customer_cue",
        "Top GP customers for Port of Singapore in April 2026",
        NO_SLOTS,
        _turn(
            "Top GP customers for Port of Singapore in April 2026",
            ConversationSlots(port="Singapore", period_a=date(2026, 4, 1)),
            period_a=_month(2026, 4),
            port=_exact("Singapore"),
            hints=(CandidateSkill(METRIC_QUERY, 2.0),),
        ),
    ),
    PipelineCase(
        # Masking case: "May Shipping" auto-applies and gets masked, so the
        # period parser only ever sees "april 2026" -- period_a is April,
        # never May.
        "masking_month_named_customer_resolves_and_masks",
        "gp for May Shipping in april 2026",
        NO_SLOTS,
        _turn(
            "gp for May Shipping in april 2026",
            ConversationSlots(customer="May Shipping", period_a=date(2026, 4, 1)),
            period_a=_month(2026, 4),
            customer=_exact("May Shipping"),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
        ),
    ),
    PipelineCase(
        # Masking counter-case: lowercase "may" has no TitleCase run at all,
        # so no customer is even attempted -- "may 2026" is read as a plain
        # month-year period instead.
        "masking_counter_case_lowercase_may_is_only_a_period",
        "gp for may 2026",
        NO_SLOTS,
        _turn(
            "gp for may 2026",
            ConversationSlots(period_a=date(2026, 5, 1)),
            period_a=_month(2026, 5),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
        ),
    ),
    PipelineCase(
        # Carry: no cue-run in "and for May?" (bare month word, excluded),
        # so customer resolves from the carried slot instead ("Northstar
        # Lines" re-resolves at tier exact); the gated "for May" IS a period
        # phrase and REPLACES the prior carried period_a (Feb -> May).
        "carry_customer_carried_period_replaced",
        "and for May?",
        ConversationSlots(customer="Northstar Lines", period_a=date(2026, 2, 1)),
        _turn(
            "and for May?",
            ConversationSlots(customer="Northstar Lines", period_a=date(2026, 5, 1)),
            period_a=_month(2026, 5),
            customer=_exact("Northstar Lines"),
        ),
    ),
    PipelineCase(
        # Clarification: a candidate-band customer phrase is never masked
        # and never clears the slot, but the rest of the turn (the period)
        # still resolves normally -- a clean, single customer_ambiguous
        # issue, not a cascade of failures.
        "clarification_candidate_band_customer",
        "gp for Acme Corp in april 2026",
        NO_SLOTS,
        _turn(
            "gp for Acme Corp in april 2026",
            ConversationSlots(period_a=date(2026, 4, 1)),
            period_a=_month(2026, 4),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
            issues=(
                ParseIssue(
                    "customer_ambiguous",
                    "did you mean one of: Acme Corporation?",
                    ("Acme Corporation",),
                ),
            ),
        ),
    ),
    PipelineCase(
        # Unavailable period: the window still resolves (and is still
        # written back to the slot -- the user did name it), alongside the
        # byte-pinned period_unavailable message.
        "unavailable_period_still_resolves_and_reports_the_range",
        "gp for april 2019",
        NO_SLOTS,
        _turn(
            "gp for april 2019",
            ConversationSlots(period_a=date(2019, 4, 1)),
            period_a=_month(2019, 4),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
            issues=(ParseIssue("period_unavailable", _UNAVAILABLE_2019_04),),
        ),
    ),
    PipelineCase(
        # Documented residual: "May Traders" is unresolved (customer_unknown)
        # so it is NEVER masked -- its embedded "May", preceded by the
        # gating preposition "for", shadow-parses as a bare-month period
        # (May 2026), not the later "april 2026". Accepted because the turn
        # already carries a customer_unknown issue -- a clarification turn,
        # never a silently wrong answer.
        "residual_unresolved_month_named_customer_shadow_parses",
        "gp for May Traders in april 2026",
        NO_SLOTS,
        _turn(
            "gp for May Traders in april 2026",
            ConversationSlots(period_a=date(2026, 5, 1)),
            period_a=_month(2026, 5),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
            issues=(ParseIssue("customer_unknown", "no customer matching 'May Traders'"),),
        ),
    ),
    PipelineCase(
        # STRONG port cue ("port of X") resolving to nothing: the claim is
        # explicit, so the miss surfaces. kind="port" wording, code stays
        # customer_unknown (customer_resolver's documented vocabulary).
        "port_cue_of_unknown_value",
        "gp for Port of Nowhereland in april 2026",
        NO_SLOTS,
        _turn(
            "gp for Port of Nowhereland in april 2026",
            ConversationSlots(period_a=date(2026, 4, 1)),
            period_a=_month(2026, 4),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
            issues=(ParseIssue("customer_unknown", "no port matching 'Nowhereland'"),),
        ),
    ),
    PipelineCase(
        # WEAK port cue ("at X") resolving to nothing: same phrase, same
        # empty pool, but "at" is an ordinary English preposition, so a miss
        # means the sentence simply did not name a port -- silence, not a
        # clarification the user never invited (see pipeline.py's "Strong
        # and weak port cues").
        "port_cue_at_unknown_value_is_silent",
        "gp at Nowhereland in april 2026",
        NO_SLOTS,
        _turn(
            "gp at Nowhereland in april 2026",
            ConversationSlots(period_a=date(2026, 4, 1)),
            period_a=_month(2026, 4),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
        ),
    ),
    PipelineCase(
        # Strong cue, CANDIDATE band (not unknown): "Rottervale" scores
        # ~0.74 against "Rotterdam" -- inside [0.60, 0.80) -- so the strong
        # claim surfaces did-you-mean chips.
        "port_cue_of_candidate_band_surfaces_chips",
        "gp for Port of Rottervale in april 2026",
        NO_SLOTS,
        _turn(
            "gp for Port of Rottervale in april 2026",
            ConversationSlots(period_a=date(2026, 4, 1)),
            period_a=_month(2026, 4),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
            issues=(
                ParseIssue("customer_ambiguous", "did you mean one of: Rotterdam?", ("Rotterdam",)),
            ),
        ),
    ),
    PipelineCase(
        # Weak cue, same candidate band: the weak-cue rule covers the WHOLE
        # not-auto-resolved space, band as well as unknown -- "at" plus a
        # near-miss is still not a port claim.
        "port_cue_at_candidate_band_is_silent",
        "gp at Rottervale in april 2026",
        NO_SLOTS,
        _turn(
            "gp at Rottervale in april 2026",
            ConversationSlots(period_a=date(2026, 4, 1)),
            period_a=_month(2026, 4),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
        ),
    ),
    PipelineCase(
        # Regression pin (final-review repro): "at Q3" is a period phrase
        # wearing a TitleCase run. The only issue left is the period's own
        # availability miss -- no port issue at all.
        "port_cue_at_bare_quarter_is_only_a_period",
        "gp at Q3",
        NO_SLOTS,
        _turn(
            "gp at Q3",
            ConversationSlots(period_a=date(2026, 7, 1)),
            period_a=PeriodWindow(date(2026, 7, 1), date(2026, 10, 1)),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
            issues=(ParseIssue("period_unavailable", _UNAVAILABLE_2026_Q3),),
        ),
    ),
    PipelineCase(
        # Regression pin (final-review repro): "take a look at <customer>"
        # is ordinary English, not a port claim -- zero issues. (The customer
        # goes undetected here because the port span is blanked before the
        # customer scan and no for/about/on cue precedes the name; that
        # asymmetry is the port-symmetry decision the plan parks, not this
        # rule's doing.)
        "port_cue_at_inside_ordinary_english_is_silent",
        "take a look at Northstar Lines gp for april 2026",
        NO_SLOTS,
        _turn(
            "take a look at Northstar Lines gp for april 2026",
            ConversationSlots(period_a=date(2026, 4, 1)),
            period_a=_month(2026, 4),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
        ),
    ),
    PipelineCase(
        # Quoted span: resolves regardless of TitleCase (quotes are their
        # own unambiguous cue); "brief" ties both brief skills since no mode
        # phrase is present.
        "quoted_span_customer",
        'brief for "Crestline Freight" please',
        NO_SLOTS,
        _turn(
            'brief for "Crestline Freight" please',
            ConversationSlots(customer="Crestline Freight"),
            customer=_exact("Crestline Freight"),
            hints=(
                CandidateSkill(EXISTING_CUSTOMER_BRIEF, 1.0),
                CandidateSkill(NEW_PROSPECT_BRIEF, 1.0),
            ),
        ),
    ),
    PipelineCase(
        "explicit_clear_customer_phrase",
        "please clear the customer",
        ConversationSlots(customer="Northstar Lines"),
        _turn("please clear the customer", ConversationSlots(customer=None)),
    ),
    PipelineCase(
        "explicit_reset_port_phrase",
        "please reset the port",
        ConversationSlots(port="Rotterdam"),
        _turn("please reset the port", ConversationSlots(port=None)),
    ),
    PipelineCase(
        # A clear phrase with nothing to clear is a harmless no-op.
        "explicit_clear_with_no_prior_value",
        "clear the customer",
        NO_SLOTS,
        _turn("clear the customer", NO_SLOTS),
    ),
    PipelineCase(
        # Conservative detection, direction 1 (the hard requirement): a
        # TitleCase run with NO cue word produces neither a customer NOR an
        # issue -- even though "Meridian Shipping" EXACTLY names a real
        # customer in the pool, sitting right there unquoted and uncued.
        "conservative_direction_one_no_cue_no_issue_even_on_a_real_name",
        "Meridian Shipping announced new routes",
        NO_SLOTS,
        _turn("Meridian Shipping announced new routes", NO_SLOTS),
    ),
    PipelineCase(
        # Conservative detection, direction 2 (the other half of the same
        # requirement): a cue word IS present ("for") and the TitleCase run
        # that follows fails to resolve -- an issue DOES fire. (The
        # clarification and residual cases above are further proof of this
        # same direction.)
        "conservative_direction_two_cue_present_unresolved_run_does_issue",
        "gp for Utopia Traders in april 2026",
        NO_SLOTS,
        _turn(
            "gp for Utopia Traders in april 2026",
            ConversationSlots(period_a=date(2026, 4, 1)),
            period_a=_month(2026, 4),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
            issues=(ParseIssue("customer_unknown", "no customer matching 'Utopia Traders'"),),
        ),
    ),
    PipelineCase(
        # Multi-issue combination and order: customer, then port, then
        # period -- all three fail in the same turn. The port miss needs the
        # STRONG cue to surface at all (see the weak-cue cases above), so
        # the port phrase here is "Port of Nowhereland".
        "multi_issue_combination_customer_then_port_then_period",
        "gp for Acme Corp at Port of Nowhereland in april 2019",
        NO_SLOTS,
        _turn(
            "gp for Acme Corp at Port of Nowhereland in april 2019",
            ConversationSlots(period_a=date(2019, 4, 1)),
            period_a=_month(2019, 4),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
            issues=(
                ParseIssue(
                    "customer_ambiguous",
                    "did you mean one of: Acme Corporation?",
                    ("Acme Corporation",),
                ),
                ParseIssue("customer_unknown", "no port matching 'Nowhereland'"),
                ParseIssue("period_unavailable", _UNAVAILABLE_2019_04),
            ),
        ),
    ),
    PipelineCase(
        # Customer and port both resolve in the same turn.
        "customer_and_port_both_resolve_together",
        "gp for Northstar Lines at Singapore in april 2026",
        NO_SLOTS,
        _turn(
            "gp for Northstar Lines at Singapore in april 2026",
            ConversationSlots(
                customer="Northstar Lines", port="Singapore", period_a=date(2026, 4, 1)
            ),
            period_a=_month(2026, 4),
            customer=_exact("Northstar Lines"),
            port=_exact("Singapore"),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
        ),
    ),
    PipelineCase(
        # A comparison phrase flows through as period_b.
        "period_comparison_flows_through_as_period_b",
        "gp april 2026 vs march 2026",
        NO_SLOTS,
        _turn(
            "gp april 2026 vs march 2026",
            ConversationSlots(period_a=date(2026, 4, 1), period_b=date(2026, 3, 1)),
            period_a=_month(2026, 4),
            period_b=_month(2026, 3),
            hints=(CandidateSkill(METRIC_QUERY, 1.0),),
        ),
    ),
    PipelineCase(
        # A plain sentence with nothing to resolve at all is a clean no-op.
        "no_signal_at_all_is_a_clean_no_op",
        "how are things going",
        NO_SLOTS,
        _turn("how are things going", NO_SLOTS),
    ),
]


@pytest.mark.parametrize("case", PIPELINE_CASES, ids=[c.id for c in PIPELINE_CASES])
def test_parse_turn_case(case: PipelineCase):
    result = parse_turn(case.message, case.slots, REF, FakeDataClient())
    assert result == case.expected


def test_pipeline_case_ids_are_unique():
    ids = [case.id for case in PIPELINE_CASES]
    assert len(set(ids)) == len(ids)


def test_table_covers_at_least_fifteen_cases():
    assert len(PIPELINE_CASES) >= 15


@pytest.mark.parametrize("case", PIPELINE_CASES, ids=[c.id for c in PIPELINE_CASES])
def test_parse_turn_is_deterministic(case: PipelineCase):
    """Same inputs, same output -- two independent fake clients (never the
    same instance) so a hidden dependency on client identity or mutable
    state cannot slip through."""
    first = parse_turn(case.message, case.slots, REF, FakeDataClient())
    second = parse_turn(case.message, case.slots, REF, FakeDataClient())
    assert first == second


# ---------------------------------------------------------------------------
# Lazy fetching: a pool is only fetched when there is something to resolve
# ---------------------------------------------------------------------------


def test_ordinary_message_never_fetches_either_dimension_pool():
    client = FakeDataClient()
    parse_turn("how are things going", NO_SLOTS, REF, client)
    fetched_columns = {column for name, column in client.calls if name == "list_dimension_values"}
    assert fetched_columns == set()


def test_clear_phrase_never_fetches_the_pool_it_clears():
    client = FakeDataClient()
    parse_turn("clear the customer", ConversationSlots(customer="Northstar Lines"), REF, client)
    fetched_columns = {column for name, column in client.calls if name == "list_dimension_values"}
    assert fetched_columns == set()


def test_customer_only_message_fetches_only_cust_nm():
    client = FakeDataClient()
    parse_turn("gp for Northstar Lines in april 2026", NO_SLOTS, REF, client)
    fetched_columns = [column for name, column in client.calls if name == "list_dimension_values"]
    assert fetched_columns == ["CUST_NM"]


def test_port_only_message_fetches_only_loc_nm():
    client = FakeDataClient()
    parse_turn("gp at Singapore in april 2026", NO_SLOTS, REF, client)
    fetched_columns = [column for name, column in client.calls if name == "list_dimension_values"]
    assert fetched_columns == ["LOC_NM"]


def test_available_periods_is_always_fetched_exactly_once():
    """Unlike the two dimension pools, period validation always runs."""
    client = FakeDataClient()
    parse_turn("how are things going", NO_SLOTS, REF, client)
    assert client.calls.count(("available_periods", DEFAULT_ENTITY)) == 1


# ---------------------------------------------------------------------------
# entity parameter and default
# ---------------------------------------------------------------------------


def test_default_entity_is_marine_sales_planning_v():
    assert DEFAULT_ENTITY == "MARINE_SALES_PLANNING_V"


def test_custom_entity_is_threaded_through_to_the_data_client():
    client = FakeDataClient()
    parse_turn("gp for Northstar Lines in april 2026", NO_SLOTS, REF, client, entity="OTHER_ENTITY")
    assert ("available_periods", "OTHER_ENTITY") in client.calls


# ---------------------------------------------------------------------------
# Shape and hygiene
# ---------------------------------------------------------------------------


def test_parse_turn_normalized_text_is_unmasked():
    """ParsedTurn.normalized_text is the record of what the user said --
    masking is working-state only and must never leak into it, even when a
    customer span WAS masked before period parsing."""
    result = parse_turn("gp for May Shipping in april 2026", NO_SLOTS, REF, FakeDataClient())
    assert result.normalized_text == "gp for May Shipping in april 2026"
    assert "May Shipping" in result.normalized_text


def test_parse_turn_slots_is_post_carry():
    """``ParsedTurn.slots`` reflects what THIS turn resolved to, not the
    slots the turn walked in with."""
    prior = ConversationSlots(customer="Northstar Lines")
    result = parse_turn("gp for Blue Anchor Marine", prior, REF, FakeDataClient())
    assert result.slots.customer == "Blue Anchor Marine"
    assert prior.customer == "Northstar Lines"  # the input is never mutated


def test_period_b_slot_clears_when_the_next_turn_asks_no_comparison():
    """``period_b`` is NEVER carried (``period_parser``'s own contract: a
    comparison has to be asked for again). The slot has to follow the same
    rule as the field, or turn 2 answers a comparison the user only asked
    for in turn 1 -- run as a real two-turn sequence so the stale value can
    only come from turn 1's own write-back, never from a hand-built prior.
    """
    client = FakeDataClient()
    first = parse_turn("gp april 2026 vs march 2026", NO_SLOTS, REF, client)
    assert first.period_b == _month(2026, 3)
    assert first.slots.period_b == date(2026, 3, 1)

    second = parse_turn("and volume?", first.slots, REF, client)
    assert second.period_a == _month(2026, 4)  # period_a still carries
    assert second.slots.period_a == date(2026, 4, 1)
    assert second.period_b is None
    assert second.slots.period_b is None


def test_parse_turn_mode_region_topic_pass_through_carry_untouched():
    prior = ConversationSlots(
        mode="existing_customer",
        region="APAC",
        topic="pricing",
        pass_through=(("Top GP customer", "Northstar Lines"),),
    )
    result = parse_turn("how are things going", prior, REF, FakeDataClient())
    assert result.slots.mode == "existing_customer"
    assert result.slots.region == "APAC"
    assert result.slots.topic == "pricing"
    assert result.slots.pass_through == (("Top GP customer", "Northstar Lines"),)


# ===========================================================================
# @pytest.mark.pg -- live resolution goldens
#
# This file MIXES offline and live tests, unlike test_synthetic_client_pg.py
# (an all-Postgres file, where a module-level `pytest.skip(allow_module_
# level=True)` is correct and skips only that file). A module-level skip
# here would also skip every offline hinter/pipeline test above whenever
# Postgres happens to be unavailable, which would be wrong -- those tests
# have nothing to do with Postgres and must stay green regardless.
#
# ``_probe_live_postgres`` copies test_synthetic_client_pg.py's reachability
# CONVENTION byte for byte (the same three stages -- no DATABASE_URL,
# unreachable/unmigrated, empty seed -- and the same actionable reasons); it
# is only the SKIP MECHANISM that is adapted: the probe returns
# ``(available, reason)`` instead of skipping directly, and
# ``TestLiveResolutionGoldens.pytestmark`` applies a ``skipif`` scoped to
# just that class, so the rest of this module is unaffected either way.
# ===========================================================================

CONNECT_TIMEOUT_SECONDS = 2
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_SEED_HINT = "seed it with `python -m poseidon.scripts.seed_synthetic`"


def _probe_live_postgres() -> tuple[bool, str]:
    """``(available, reason)`` -- ``reason`` is only meaningful when
    ``available`` is ``False``. Never raises: any connect/lookup failure
    means "not available", exactly like test_synthetic_client_pg.py's own
    probe."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return False, (
            f"DATABASE_URL is not set - pg integration tests need a Postgres: {_UP_HINT}, "
            f"migrate it with `python -m alembic upgrade head`, {_SEED_HINT}"
        )
    try:
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM synthetic.marine_sales_planning_v")
                seeded_rows = cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001 - any connect/lookup failure means "not available"
        return False, (
            f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
            f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT} and migrate it with "
            "`python -m alembic upgrade head`"
        )
    if seeded_rows == 0:
        return False, f"synthetic.marine_sales_planning_v is empty - {_SEED_HINT}"
    return True, ""


_PG_AVAILABLE, _PG_SKIP_REASON = _probe_live_postgres()

if _PG_AVAILABLE:
    _DSN = os.environ["DATABASE_URL"]
    CLIENT: SyntheticDataClient | None = SyntheticDataClient(
        _DSN, connect_timeout=CONNECT_TIMEOUT_SECONDS
    )
    LIVE_CUSTOMERS: tuple[str, ...] = tuple(CLIENT.list_dimension_values(DEFAULT_ENTITY, "CUST_NM"))
    LIVE_PORTS: tuple[str, ...] = tuple(CLIENT.list_dimension_values(DEFAULT_ENTITY, "LOC_NM"))
else:
    # Never fetched when unavailable -- fetching would just raise the same
    # failure the probe above already turned into an actionable skip reason.
    CLIENT = None
    LIVE_CUSTOMERS = ()
    LIVE_PORTS = ()


@pytest.mark.pg
class TestLiveResolutionGoldens:
    """resolve() and parse_turn() against the live seeded database.

    Disclosed substitution: the plan assumed a bare "meridian" would land in
    the multi-Meridian candidate band. Verified against the live 40-name
    pool (10 of them "Meridian *"), bare "meridian" instead AUTO-APPLIES at
    fuzzy confidence 1.0 to "Meridian Bunkering" -- rapidfuzz's
    token_set_ratio scores a pure token subset almost perfectly (the same
    reason a bare "May" auto-applies to "May Shipping" in the offline
    fixture; see pipeline.py's module docstring). "Meridiann" (the double
    letter is the only edit) exercises the SAME behavior the plan wanted --
    a shared-token-prefix candidate band -- against real seeded data: 9 of
    the 10 Meridian names clear CANDIDATE_THRESHOLD, and the max-3 cap trims
    them to the top three by score (Meridian Tankers 0.72, Meridian Lines
    ~0.696, Meridian Shipping ~0.692), deterministically (verified by an
    independent scoring pass during implementation).
    """

    pytestmark = pytest.mark.skipif(not _PG_AVAILABLE, reason=_PG_SKIP_REASON)

    # -- resolve() against live seeded CUST_NM values ----------------------

    def test_live_northstar_linez_typo_auto_applies(self):
        result = resolve("Northstar Linez", LIVE_CUSTOMERS, "customer")
        assert result.entity is not None
        assert result.entity.value == "Northstar Lines"
        assert result.entity.tier == "fuzzy"
        assert result.issue is None

    def test_live_meridiann_yields_the_multi_meridian_candidate_band(self):
        result = resolve("Meridiann", LIVE_CUSTOMERS, "customer")
        assert result.entity is None
        assert result.candidates == ("Meridian Tankers", "Meridian Lines", "Meridian Shipping")
        assert result.issue == ParseIssue(
            "customer_ambiguous",
            "did you mean one of: Meridian Tankers, Meridian Lines, Meridian Shipping?",
            ("Meridian Tankers", "Meridian Lines", "Meridian Shipping"),
        )

    def test_live_resolver_result_is_deterministic(self):
        first = resolve("Meridiann", LIVE_CUSTOMERS, "customer")
        second = resolve("Meridiann", tuple(reversed(LIVE_CUSTOMERS)), "customer")
        assert first == second

    def test_live_demo_trio_are_all_exact_matches(self):
        """Phase-1 mock continuity, same three names
        test_synthetic_client_pg.py pins as the demo trio."""
        for name in ("Northstar Lines", "Blue Anchor Marine", "Crestline Freight"):
            result = resolve(name, LIVE_CUSTOMERS, "customer")
            assert result.entity is not None
            assert result.entity.value == name
            assert result.entity.tier == "exact"

    # -- full parse_turn against SyntheticDataClient ------------------------

    def test_live_parse_turn_flagship_matches_the_offline_result(self):
        result = parse_turn(
            "Top GP customers for Port of Singapore in April 2026", NO_SLOTS, REF, CLIENT
        )
        assert result.customer is None
        assert result.port == ResolvedEntity(
            value="Singapore", source_text="Singapore", confidence=1.0, tier="exact"
        )
        assert result.period_a == _month(2026, 4)
        assert result.hints and result.hints[0].skill_id == METRIC_QUERY
        assert result.issues == ()

    def test_live_parse_turn_resolves_the_northstar_linez_typo(self):
        result = parse_turn("gp for Northstar Linez in april 2026", NO_SLOTS, REF, CLIENT)
        assert result.customer is not None
        assert result.customer.value == "Northstar Lines"
        assert result.customer.tier == "fuzzy"
        assert result.period_a == _month(2026, 4)
        assert result.issues == ()

    def test_live_parse_turn_meridiann_yields_a_clarification_issue(self):
        result = parse_turn("gp for Meridiann in april 2026", NO_SLOTS, REF, CLIENT)
        assert result.customer is None
        assert len(result.issues) == 1
        assert result.issues[0].code == "customer_ambiguous"
        assert result.issues[0].candidates == (
            "Meridian Tankers",
            "Meridian Lines",
            "Meridian Shipping",
        )
        # Not masked -- period_a still resolves normally alongside the issue.
        assert result.period_a == _month(2026, 4)

    def test_live_available_periods_matches_the_seeded_range(self):
        assert CLIENT.available_periods(DEFAULT_ENTITY) == PeriodRange(
            date(2025, 1, 1), date(2026, 6, 30)
        )


# ===========================================================================
# ASCII-only source, matching the Task 2/3 convention
# ===========================================================================


def test_all_four_files_are_ascii_on_disk():
    paths = (
        Path(lexicon.__file__),
        Path(skill_hinter.__file__),
        Path(pipeline.__file__),
        Path(__file__),
    )
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


def test_lexicon_documents_the_future_skill_ids():
    """``research.web_research`` (and, by the same reasoning, the two
    customer_insight brief skills) is not registered anywhere yet -- the
    module docstring must say so, per the task's own requirement that
    referencing a future skill id be documented in lexicon.py."""
    doc = lexicon.__doc__ or ""
    assert "research.web_research" in doc
    assert "not yet" in doc.casefold() or "future" in doc.casefold()


def test_pipeline_module_never_reads_the_wall_clock():
    """Every date pipeline.py constructs comes from ``reference_date`` --
    checked over the parsed AST, the same technique test_parsing_periods.py
    uses to pin period_parser's identical contract."""
    tree = ast.parse(Path(pipeline.__file__).read_text(encoding="utf-8"))
    called = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
        elif isinstance(node.func, ast.Name):
            called.add(node.func.id)
    assert called.isdisjoint({"today", "now", "utcnow", "time", "monotonic", "fromtimestamp"})
