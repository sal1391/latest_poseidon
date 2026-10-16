"""D67: the entity-scoped grounding check. The mockup narrative is the
must-pass fixture; a paragraph that hands one customer's figure to another
is the must-reject fixture. Every token count below was derived by hand
from the fixture text (see the comment on each)."""

from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    _mrow,
    minimal_payload,
    mockup_payload,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import grounding

# The mockup's "Summary of operations" (docs/reference/mockups/..., lines
# 611-622) with its two non-ASCII glyphs spelled in ASCII: the arrow is
# "->" and the minus sign is "-". 45 non-exempt numeric tokens:
# summary 15, key drivers 11, account context 9, insights 10.
MOCKUP_NARRATIVE = """\
**July 2026 to August 2026, Gibraltar Office.** Overall volume increased by 5.6% (+2,350 tons), while gross profit decreased by 4.3% (-53,500). Margin moved from 29.54/ton to 26.76/ton (-2.78/ton). The office won 298 transactions in August vs 312 in July (14 fewer), with inquiries up 4.5% (531 vs 508). The win rate declined from 61.4% to 56.1%.

## Key drivers

The GP decline is concentrated in one account. Meridian Bunkering's GP fell 56,500 (-26.3%) on volume, down 2,150 tons, while its margin actually improved to 30.92/ton. Northstar Lines offset a third of that with +29,300 (+20.5%), also volume-driven, on 1,150 more tons. Atlas Tankers added 6,300 purely on margin (27.58 -> 30.03/ton). Corsair Maritime lost 3,300 on margin compression despite more volume.

## Account context

Meridian Bunkering did not leave. Its whole-book GP is down only 12,400 (-3.1%): the volume that left Gibraltar reappeared under the Spain Office (+41,000), mostly at Algeciras (+31,200), supplied by the Med Supply Team (+28,400). Gibraltar's share of the account dropped from 53.4% to 40.6%. Northstar's gain is real growth for the account (+21,600 overall), with a small -7,700 offset at Rotterdam.

## Insights

- Margin compression is broad, not one deal: all three deal classes lost margin, CONTRACTED most sharply (31.29 -> 25.43/ton).
- TRADED business gained 800 tons but lost 53,700 of GP, so August's extra volume was priced thinner than July's.
- Inquiries rose 4.5% while wins fell 4.5%; 37 more lost inquiries pushed the win rate down 5.3 points.
- One account came in (Orion Offshore, 12,400 GP) and one went quiet (Kestrel Lines, 18,900 in July).
"""  # noqa: E501


def test_mockup_narrative_passes_with_every_token_checked():
    report = grounding.check(MOCKUP_NARRATIVE, grounding.build_index(mockup_payload()))
    assert report.ok, report.violations
    assert report.unverified == 0
    assert report.checked == 45


def test_must_reject_fixture_attributes_customer_b_figure_to_customer_a():
    """Review Focus 4 / spec 5.6: Meridian's -56,500 and -26.3% handed to
    Northstar. Both tokens are unverified: 56,500 is nobody else's figure
    and -26.3% carries a sign Northstar's own +20.5% cannot match."""
    text = "Northstar Lines' GP fell 56,500 (-26.3%) on lower volume."
    report = grounding.check(text, grounding.build_index(mockup_payload()))
    assert not report.ok
    assert len(report.violations) == 1
    assert report.violations[0].sentence == text
    assert report.violations[0].tokens == ("56,500", "-26.3%")
    assert report.unverified == 2


def test_tokens_normalise_the_unicode_minus_and_the_word_minus():
    # "\u2212" is the Unicode minus sign, escaped so this file stays ASCII on disk.
    text = "GP fell minus 7,700 and \u22122.78/ton to 56.1%; margin was 27.58 -> 30.03/ton."
    assert grounding.numeric_tokens(text) == ["-7,700", "-2.78", "56.1%", "27.58", "30.03"]


def test_years_small_integers_and_period_label_days_are_exempt():
    payload = mockup_payload()
    payload["periods"]["current"]["label"] = "October 1 to 11, 2026 (month to date)"
    payload["periods"]["prior"]["label"] = "September 1 to 11, 2026"
    index = grounding.build_index(payload)
    report = grounding.check("In 2026 the top 3 ports held from day 1 to 11.", index)
    assert report.ok and report.checked == 0


def test_an_unsigned_token_matches_either_sign_but_a_signed_token_must_match():
    index = grounding.build_index(mockup_payload())
    assert grounding.check("Northstar Lines rose 29,300.", index).ok
    assert grounding.check("Northstar Lines rose +29,300.", index).ok
    assert not grounding.check("Northstar Lines fell -29,300.", index).ok


def test_entity_scope_accumulates_within_a_paragraph_and_resets_at_a_blank_line():
    index = grounding.build_index(mockup_payload())
    same = "Meridian Bunkering fell 56,500. Its margin improved to 30.92/ton."
    split = "Meridian Bunkering fell 56,500.\n\nIts margin improved to 30.92/ton."
    assert grounding.check(same, index).ok
    report = grounding.check(split, index)
    assert not report.ok and report.violations[0].tokens == ("30.92",)


def test_a_list_item_is_its_own_paragraph():
    index = grounding.build_index(mockup_payload())
    text = "Meridian Bunkering fell 56,500.\n- Its margin improved to 30.92/ton."
    assert not grounding.check(text, index).ok


def test_offsets_are_indexed_under_the_mover_and_the_named_office():
    index = grounding.build_index(mockup_payload())
    assert grounding.check("The Spain Office gained +41,000.", index).ok
    assert grounding.check("Meridian Bunkering shifted +41,000 elsewhere.", index).ok
    assert grounding.check("Algeciras took +31,200.", index).ok


def test_kpi_figures_are_allowed_in_every_sentence():
    index = grounding.build_index(mockup_payload())
    assert grounding.check("Gibraltar led while the office made 1,182,900 overall.", index).ok


def test_first_word_alias_needs_four_letters_and_uniqueness():
    payload = minimal_payload()
    rows = payload["tables"]["entities_by_gp"]["rows"]
    rows[0]["key"] = "Vega Bulk"
    rows[1]["key"] = "Vega Carriers"
    payload["entity_context"] = {
        "Med Supply Team": payload["entity_context"]["Meridian Bunkering"],
    }
    index = grounding.build_index(payload)
    assert "vega" not in index.aliases
    assert "med" not in index.aliases
    assert index.aliases["vega bulk"] == "Vega Bulk"
    mockup = grounding.build_index(mockup_payload())
    assert mockup.aliases["northstar"] == "Northstar Lines"
    assert mockup.aliases["meridian"] == "Meridian Bunkering"


def test_other_and_total_rows_never_alias_their_first_word():
    payload = mockup_payload()
    # GP 20,000 -> 23,700: a +3,700 change that no other row, context or KPI carries.
    other = _mrow("Other (2 customers)", 20_000, 700, 23_700, 760)
    payload["tables"]["entities_by_gp"]["other"] = other
    index = grounding.build_index(payload)
    assert "other" not in index.aliases
    assert index.aliases["other (2 customers)"] == "Other (2 customers)"
    # The word "other" alone must not pull the Other row's figures into scope.
    assert grounding.check("The other offices gained +3,700.", index).ok is False
    assert grounding.check("Other (2 customers) gained +3,700.", index).ok is True
    # +44,100 is Meridian's other_offices_net: in scope only when Meridian is named.
    assert not grounding.check("The other offices net +44,100.", index).ok
    assert grounding.check("Meridian Bunkering's other offices net +44,100.", index).ok


def test_an_indented_sub_bullet_stays_in_its_parent_paragraph():
    index = grounding.build_index(mockup_payload())
    text = "Meridian Bunkering fell 56,500.\n   - Its margin improved to 30.92/ton."
    assert grounding.check(text, index).ok


def test_possessives_and_markdown_emphasis_still_name_the_entity():
    index = grounding.build_index(mockup_payload())
    assert grounding.find_entities("**Northstar's** gain was +21,600.", index) == [
        "Northstar Lines"
    ]


def test_text_without_tokens_is_ok_and_never_raises():
    index = grounding.build_index(mockup_payload())
    for text in ("", "## Key drivers", "Nothing moved.", "\n\n"):
        report = grounding.check(text, index)
        assert report.ok and report.checked == 0 and report.violations == ()


def test_modules_are_ascii_on_disk():
    from pathlib import Path

    from poseidon.tasks.reporting.skills.monthly_performance.tools import (
        narrative_template,
        questions,
    )

    for module in (grounding, narrative_template, questions):
        Path(module.__file__).read_bytes().decode("ascii")
