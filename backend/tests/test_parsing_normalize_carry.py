"""Truth-table tests for Phase 4 Task 1: the parsed-turn types, ``normalize``,
the additive ``ConversationSlots`` growth, and the slot carry semantics
(doc 02 section 5).

Four things are pinned here, all new as of this task:

* ``ConversationSlots`` gains ``region``/``topic``/``pass_through`` -- all
  defaulted, so every pre-Phase-4 call site (``test_skill_registry.py``
  included) keeps working unchanged.
* ``normalize`` -- strip, NFC, collapse internal whitespace, preserve case.
* ``parsing.types`` -- the frozen shapes (``ParseIssue``, ``ResolvedEntity``,
  ``CandidateSkill``, ``ParsedTurn``) later Phase 4 tasks build on.
* ``carry.apply_carry`` -- the tri-state (UNSET/None/value) truth table for
  each of the four slots it manages, proven independently and in
  combination, plus the "replace never merges" rule proven on the
  tuple-shaped ``pass_through`` field -- the one place a buggy "merge" is
  actually distinguishable from a correct "replace" (a scalar slot cannot
  tell the two apart).

Non-ASCII test strings are written as explicit ``\\uXXXX`` escapes in the
source (never a literal typed character), so a decomposed (NFD) input and
its precomposed (NFC) expected output can never be silently confused with
each other by an editor or encoding round-trip -- the escape text itself is
plain ASCII; Python resolves it to the real codepoint at parse time.
"""

from dataclasses import FrozenInstanceError, fields, replace
from datetime import date

import pytest

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.parsing.carry import UNSET, SlotUpdates, _UnsetType, apply_carry
from poseidon.core.parsing.normalize import normalize
from poseidon.core.parsing.types import CandidateSkill, ParsedTurn, ParseIssue, ResolvedEntity
from poseidon.core.skills.context import ConversationSlots

# ---------------------------------------------------------------------------
# ConversationSlots growth -- additive only, all new fields defaulted
# ---------------------------------------------------------------------------


def test_new_fields_default_to_empty():
    slots = ConversationSlots()
    assert slots.region is None
    assert slots.topic is None
    assert slots.pass_through == ()


def test_growth_is_additive_old_call_sites_unaffected():
    """The P3 final-review rule: grow by defaulted fields only, never
    reshape. A call site built before Task 1 existed -- positional-free,
    naming only the original five fields -- must still construct the same
    object it always did, now just carrying three more (defaulted) fields.
    """
    slots = ConversationSlots(customer="Acme Shipping", port="Singapore")
    assert (slots.period_a, slots.period_b, slots.mode) == (None, None, "default")
    assert (slots.region, slots.topic, slots.pass_through) == (None, None, ())


def test_new_fields_are_frozen():
    slots = ConversationSlots()
    with pytest.raises(FrozenInstanceError):
        slots.region = "APAC"


# ---------------------------------------------------------------------------
# normalize()
# ---------------------------------------------------------------------------

# "cafe" + combining acute accent U+0301 (NFD: 5 codepoints for "cafe" + the
# mark) must normalize to "caf" + precomposed e-acute U+00E9 (NFC: 4
# codepoints) -- same composition rule applied to a bare "e" + combining
# accent in the last row. Escapes only, so input/expected can never drift
# into the same (already-composed) bytes by accident.
NFD_CAFE = "café today"
NFC_CAFE = "café today"
NFD_E_ACUTE = "é"
NFC_E_ACUTE = "é"

NORMALIZE_TABLE = [
    ("already normal", "already normal"),
    ("  leading and trailing  ", "leading and trailing"),
    ("\t\nleading/trailing whitespace chars\n\t", "leading/trailing whitespace chars"),
    ("internal    multiple   spaces", "internal multiple spaces"),
    ("tabs\tbetween\twords", "tabs between words"),
    ("newlines\nbetween\nwords", "newlines between words"),
    ("windows\r\nline\r\nendings", "windows line endings"),
    ("mixed \t\n whitespace \t\n runs", "mixed whitespace runs"),
    ("PRESERVE Case EXACTLY", "PRESERVE Case EXACTLY"),
    ("", ""),
    ("   \t\n  ", ""),
    ("single", "single"),
    (NFD_CAFE, NFC_CAFE),
    (NFD_E_ACUTE, NFC_E_ACUTE),
]


@pytest.mark.parametrize("raw,expected", NORMALIZE_TABLE)
def test_normalize_table(raw, expected):
    assert normalize(raw) == expected


def test_normalize_is_idempotent():
    raw = "  \t " + NFD_CAFE + "   has   \n extra   whitespace  \t"
    once = normalize(raw)
    assert normalize(once) == once


# ---------------------------------------------------------------------------
# parsing.types -- frozen shapes
# ---------------------------------------------------------------------------


def test_parse_issue_fields_and_default_candidates():
    issue = ParseIssue(code="customer_unknown", message="no customer matching 'foo'")
    assert issue.code == "customer_unknown"
    assert issue.message == "no customer matching 'foo'"
    assert issue.candidates == ()


def test_parse_issue_candidates_and_frozen():
    issue = ParseIssue(
        code="customer_ambiguous",
        message="did you mean one of: Meridian Shipping, Meridian Freight?",
        candidates=("Meridian Shipping", "Meridian Freight"),
    )
    assert issue.candidates == ("Meridian Shipping", "Meridian Freight")
    with pytest.raises(FrozenInstanceError):
        issue.code = "other"


def test_resolved_entity_fields_and_frozen():
    entity = ResolvedEntity(
        value="Northstar Lines", source_text="northstar", confidence=1.0, tier="token"
    )
    assert (entity.value, entity.source_text) == ("Northstar Lines", "northstar")
    assert (entity.confidence, entity.tier) == (1.0, "token")
    with pytest.raises(FrozenInstanceError):
        entity.value = "other"


def test_candidate_skill_fields_and_frozen():
    hint = CandidateSkill(skill_id="data_qa.metric_query", score=2.5)
    assert (hint.skill_id, hint.score) == ("data_qa.metric_query", 2.5)
    with pytest.raises(FrozenInstanceError):
        hint.score = 0.0


def test_parsed_turn_holds_every_field_and_is_frozen():
    slots = ConversationSlots(customer="Acme Shipping")
    period = PeriodWindow(date(2026, 4, 1), date(2026, 5, 1))
    entity = ResolvedEntity(value="Acme Shipping", source_text="acme", confidence=1.0, tier="exact")
    hint = CandidateSkill(skill_id="data_qa.metric_query", score=2.0)
    issue = ParseIssue(code="period_unavailable", message="no data for 2026-04..2026-05")

    turn = ParsedTurn(
        normalized_text="gp for acme in april 2026",
        slots=slots,
        period_a=period,
        period_b=None,
        customer=entity,
        port=None,
        hints=(hint,),
        issues=(issue,),
    )

    assert turn.normalized_text == "gp for acme in april 2026"
    assert turn.slots is slots
    assert (turn.period_a, turn.period_b) == (period, None)
    assert (turn.customer, turn.port) == (entity, None)
    assert turn.hints == (hint,)
    assert turn.issues == (issue,)
    with pytest.raises(FrozenInstanceError):
        turn.normalized_text = "changed"


def test_parsed_turn_empty_issues_means_clean_parse():
    turn = ParsedTurn(
        normalized_text="hello",
        slots=ConversationSlots(),
        period_a=None,
        period_b=None,
        customer=None,
        port=None,
        hints=(),
        issues=(),
    )
    assert turn.issues == ()


# ---------------------------------------------------------------------------
# carry: the UNSET sentinel and SlotUpdates
# ---------------------------------------------------------------------------


def test_unset_repr():
    assert repr(UNSET) == "UNSET"


def test_unset_is_a_module_level_singleton_instance_of_unset_type():
    assert isinstance(UNSET, _UnsetType)


def test_slot_updates_defaults_every_field_to_unset():
    updates = SlotUpdates()
    assert updates.customer is UNSET
    assert updates.port is UNSET
    assert updates.period_a is UNSET
    assert updates.period_b is UNSET


def test_slot_updates_is_frozen():
    updates = SlotUpdates()
    with pytest.raises(FrozenInstanceError):
        updates.customer = "Acme Shipping"


def test_second_unset_instance_still_means_carry():
    """SlotUpdates types each field as ``str | None | _UnsetType`` -- any
    ``_UnsetType`` instance must carry, not only the module's ``UNSET``
    singleton, so ``apply_carry`` cannot silently desync from a stray
    second instance.
    """
    other_unset = _UnsetType()
    assert other_unset is not UNSET

    prior = ConversationSlots(customer="Acme Shipping")
    result = apply_carry(prior, SlotUpdates(customer=other_unset))

    assert result.customer == "Acme Shipping"


# ---------------------------------------------------------------------------
# apply_carry -- the truth table
# ---------------------------------------------------------------------------

PRIOR = ConversationSlots(
    customer="Northstar Lines",
    port="Singapore",
    period_a=date(2026, 4, 1),
    period_b=date(2026, 5, 1),
    mode="existing_customer",
    region="APAC",
    topic="pricing",
    pass_through=(("Top GP customer", "Northstar Lines"),),
)

CARRY_TRUTH_TABLE = [
    # slot, update value, expected result:
    #   (UNSET, prior) -> prior; (None, prior) -> None; (new, prior) -> new
    ("customer", UNSET, "Northstar Lines"),
    ("customer", None, None),
    ("customer", "Meridian Shipping", "Meridian Shipping"),
    ("port", UNSET, "Singapore"),
    ("port", None, None),
    ("port", "Rotterdam", "Rotterdam"),
    ("period_a", UNSET, date(2026, 4, 1)),
    ("period_a", None, None),
    ("period_a", date(2026, 6, 1), date(2026, 6, 1)),
    ("period_b", UNSET, date(2026, 5, 1)),
    ("period_b", None, None),
    ("period_b", date(2026, 7, 1), date(2026, 7, 1)),
]


@pytest.mark.parametrize("slot_name,update_value,expected", CARRY_TRUTH_TABLE)
def test_apply_carry_truth_table(slot_name, update_value, expected):
    updates = SlotUpdates(**{slot_name: update_value})
    result = apply_carry(PRIOR, updates)
    assert getattr(result, slot_name) == expected


def test_apply_carry_no_updates_carries_everything():
    result = apply_carry(PRIOR, SlotUpdates())
    assert result == PRIOR


def test_apply_carry_handles_mixed_states_independently_in_one_call():
    updates = SlotUpdates(
        customer=UNSET,  # carry
        port=None,  # explicit clear
        period_a=date(2026, 6, 1),  # replace
        period_b=UNSET,  # carry
    )
    result = apply_carry(PRIOR, updates)
    assert result.customer == PRIOR.customer
    assert result.port is None
    assert result.period_a == date(2026, 6, 1)
    assert result.period_b == PRIOR.period_b


def test_apply_carry_can_clear_every_managed_slot_at_once():
    updates = SlotUpdates(customer=None, port=None, period_a=None, period_b=None)
    result = apply_carry(PRIOR, updates)
    assert (result.customer, result.port, result.period_a, result.period_b) == (
        None,
        None,
        None,
        None,
    )


def test_apply_carry_can_replace_every_managed_slot_at_once():
    updates = SlotUpdates(
        customer="Meridian Shipping",
        port="Rotterdam",
        period_a=date(2026, 6, 1),
        period_b=date(2026, 7, 1),
    )
    result = apply_carry(PRIOR, updates)
    assert (result.customer, result.port, result.period_a, result.period_b) == (
        "Meridian Shipping",
        "Rotterdam",
        date(2026, 6, 1),
        date(2026, 7, 1),
    )


def test_apply_carry_leaves_mode_region_topic_pass_through_untouched():
    """SlotUpdates has no opinion about mode/region/topic/pass_through --
    apply_carry must carry these four through byte-for-byte regardless of
    what happens to the four slots it does manage.
    """
    updates = SlotUpdates(
        customer=None, port="Rotterdam", period_a=UNSET, period_b=date(2026, 7, 1)
    )
    result = apply_carry(PRIOR, updates)
    assert result.mode == PRIOR.mode
    assert result.region == PRIOR.region
    assert result.topic == PRIOR.topic
    assert result.pass_through == PRIOR.pass_through


def test_unset_sentinel_never_leaks_into_result():
    result = apply_carry(PRIOR, SlotUpdates())
    for slot_field in fields(result):
        assert not isinstance(getattr(result, slot_field.name), _UnsetType)


def test_apply_carry_returns_a_conversation_slots():
    assert isinstance(apply_carry(PRIOR, SlotUpdates()), ConversationSlots)


# ---------------------------------------------------------------------------
# never-merge, proven where scalar replace can't distinguish it from merge:
# the tuple-shaped pass_through field
# ---------------------------------------------------------------------------


def test_pass_through_replace_is_wholesale_never_a_merge():
    """The doc 02 section 5 rule -- a new value REPLACES, never merges -- is
    trivially true for scalar slots (there is nothing to merge a string or
    a date with). It is only actually falsifiable for a tuple-shaped slot
    like ``pass_through``, where a buggy implementation might concatenate
    or dedupe instead of replacing outright. ``apply_carry`` does not
    manage ``pass_through`` in Task 1 (population is wired in a later
    phase, per the Phase 4 plan's self-review notes) -- this pins the
    replace contract directly on ``ConversationSlots`` so nothing
    downstream is tempted to accumulate turns' pass-through values instead
    of replacing them wholesale.
    """
    prior = ConversationSlots(
        pass_through=(("Top GP customer", "Acme Shipping"), ("Port", "Singapore"))
    )
    next_turn = replace(prior, pass_through=(("Region", "APAC"),))

    assert next_turn.pass_through == (("Region", "APAC"),)
    assert len(next_turn.pass_through) == 1
    assert ("Top GP customer", "Acme Shipping") not in next_turn.pass_through
