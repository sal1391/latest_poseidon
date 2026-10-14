from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    minimal_payload,
    mockup_payload,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import grounding
from poseidon.tasks.reporting.skills.monthly_performance.tools.narrative_template import (
    render_template_narrative,
)


def test_overall_summary_uses_the_display_strings_verbatim():
    text = render_template_narrative(mockup_payload())
    assert text.startswith(
        "**July 2026 to August 2026, Gibraltar Office (Customer Broker Office).**"
    )
    assert (
        "Overall volume increased by 5.6% (+2,350 tons), while gross profit decreased by 4.3% "
        "(-53,500)." in text
    )
    assert "Margin moved from 29.54/ton to 26.76/ton (-2.78/ton)." in text
    assert (
        "The office won 298 transactions in August 2026 vs 312 in July 2026 (14 fewer), "
        "with inquiries up 4.5% (531 vs 508)." in text
    )
    assert "The win rate declined from 61.4% to 56.1%." in text


def test_key_drivers_list_movers_with_their_driver_word():
    text = render_template_narrative(mockup_payload())
    assert "## Key drivers" in text
    assert (
        "*Top increases by GP:*\n1. **Northstar Lines**: GP +29,300 (+20.5%), volume-driven" in text
    )
    assert "   - Volume: 4,810 -> 5,960 tons (+1,150, +23.9%)" in text
    assert (
        "*Top decreases by GP:*\n1. **Meridian Bunkering**: GP -56,500 (-26.3%), volume-driven"
        in text
    )


def test_new_and_lost_sections_name_entities_or_say_none():
    text = render_template_narrative(mockup_payload())
    assert "## New and lost customers" in text
    assert "**New in August 2026:** Orion Offshore (GP 12,400, 480 tons, margin 25.83)" in text
    assert "**Lost from July 2026:** Kestrel Lines (GP 18,900, 610 tons, margin 30.98)" in text
    payload = mockup_payload()
    payload["new_lost"] = {"new": [], "lost": []}
    payload["movers"] = {"increases": [], "decreases": []}
    text = render_template_narrative(payload)
    assert "**New in August 2026:** none" in text
    assert "*Top increases by GP:*\nnone" in text


def test_flat_and_zero_base_wording():
    payload = mockup_payload()
    for metric in ("VOLUME", "NUM_INQUIRIES"):
        payload["kpis"][metric]["display"].update({"change": "flat", "pct": "flat"})
    payload["kpis"]["GP"]["display"].update({"change": "+1,000", "pct": "new"})
    payload["kpis"]["MARGIN"]["display"]["change"] = "flat"
    text = render_template_narrative(payload)
    assert "Overall volume was flat, while gross profit rose from a zero base to 1,182,900." in text
    assert "(unchanged)" in text
    assert "with inquiries flat (531 vs 508)" in text


def test_entity_noun_is_parameterised_for_suppliers():
    payload = mockup_payload()
    payload["entity"] = {"column": "SUPPLIER_NM", "label": "Supplier"}
    assert "## New and lost suppliers" in render_template_narrative(payload)


def test_book_perspective_says_book_not_office():
    payload = mockup_payload()
    payload["scope"] = {"office_column": None, "office_value": None, "label": "Whole book"}
    assert "The book won 298 transactions" in render_template_narrative(payload)


def test_template_narrative_passes_the_grounding_check():
    for payload in (mockup_payload(), minimal_payload()):
        report = grounding.check(render_template_narrative(payload), grounding.build_index(payload))
        assert report.ok, report.violations
