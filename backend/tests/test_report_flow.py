"""The report flow's pure step functions (Phase 17 PR A2 Task 6), over the
reporting skill's own FakeDataClient: rows in April, May and June 2026 only."""

from datetime import date

from poseidon.core.chat import report_flow as rf
from poseidon.core.skills.context import ConversationSlots
from poseidon.tasks.reporting.skills.monthly_performance.tests.fake_client import FakeDataClient
from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    minimal_payload,
)

REFERENCE_DATE = date(2026, 7, 15)


def test_step_for():
    assert rf.step_for(ConversationSlots(mode="report")) == "perspective"
    customer = ConversationSlots(
        mode="report", perspective="customer", office_column="CUSTOMER_TEAM_NAME"
    )
    assert rf.step_for(customer) == "office"
    supplier = ConversationSlots(
        mode="report", perspective="supplier", office_column="PRIMARY_SUPPLY_TEAM_OFFICE"
    )
    assert rf.step_for(supplier) == "office"
    assert rf.step_for(ConversationSlots(mode="report", perspective="book")) == "month"
    chosen = ConversationSlots(
        mode="report",
        perspective="customer",
        office_column="CUSTOMER_TEAM_NAME",
        office_value="Gibraltar Office",
    )
    assert rf.step_for(chosen) == "month"


def test_match_perspective_variants_and_rejects_noise():
    for text, expected in [
        ("customer office report", "customer"),
        ("supplier office report", "supplier"),
        ("whole book report", "book"),
        ("Customer", "customer"),
        ("  SUPPLIER  ", "supplier"),
        ("book", "book"),
        ("Whole Book", "book"),
        ("customer office", "customer"),
        ("supplier   office", "supplier"),
    ]:
        assert rf.match_perspective(text) == expected, text
    for noise in ["", "customers", "the customer office please", "office", "GP for June 2026"]:
        assert rf.match_perspective(noise) is None, noise


def test_match_office_is_case_insensitive():
    values = ["Gibraltar Office", "Malta Office", "Spain Office"]
    assert rf.match_office("gibraltar office", values) == "Gibraltar Office"
    assert rf.match_office("  MALTA   OFFICE ", values) == "Malta Office"
    assert rf.match_office("Gibraltar", values) is None
    assert rf.match_office("Atlantis", values) is None


def test_month_chips_last_three_complete_months_newest_first_latest_marked():
    chips = rf.month_chips(FakeDataClient(), REFERENCE_DATE)
    assert chips == [
        {"id": "2026-06-01", "label": "June 2026 (latest)", "send_text": "June 2026"},
        {"id": "2026-05-01", "label": "May 2026", "send_text": "May 2026"},
        {"id": "2026-04-01", "label": "April 2026", "send_text": "April 2026"},
    ]
    # June is running at 2026-06-15, so it is not offered as a complete month.
    assert [c["id"] for c in rf.month_chips(FakeDataClient(), date(2026, 6, 15))] == [
        "2026-05-01",
        "2026-04-01",
    ]
    # A month whose end is the reference date itself is complete.
    assert rf.month_chips(FakeDataClient(), date(2026, 7, 1))[0]["id"] == "2026-06-01"


def test_resolve_month_reads_a_typed_month_and_the_chip_text():
    data = FakeDataClient()
    june = rf.MonthChoice(date(2026, 6, 1), "complete")
    may = rf.MonthChoice(date(2026, 5, 1), "complete")
    assert rf.resolve_month("June 2026", data, REFERENCE_DATE) == june
    assert rf.resolve_month("june 2026", data, REFERENCE_DATE) == june
    assert rf.resolve_month("2026-06", data, REFERENCE_DATE) == june
    assert rf.resolve_month("May 2026", data, REFERENCE_DATE) == may


def test_resolve_month_mtd_phrases_and_running_month_by_name():
    data = FakeDataClient()
    reference = date(2026, 6, 15)
    expected = rf.MonthChoice(date(2026, 6, 1), "mtd")
    for text in ["month to date", "mtd", "MTD", "so far this month", "June 2026", "june"]:
        assert rf.resolve_month(text, data, reference) == expected, text


def test_resolve_month_reasks():
    data = FakeDataClient()
    assert rf.resolve_month("August 2026", data, REFERENCE_DATE) == rf.MONTH_NOT_COMPLETE
    assert rf.resolve_month("January 2026", data, REFERENCE_DATE) == "No data for January 2026."
    assert rf.resolve_month("banana", data, REFERENCE_DATE) == rf.MONTH_UNREADABLE
    assert rf.resolve_month("2025", data, REFERENCE_DATE) == rf.MONTH_UNREADABLE
    assert rf.resolve_month("month to date", data, date(2026, 6, 1)) == rf.MTD_DAY_ONE
    assert rf.resolve_month("June 2026", data, date(2026, 6, 1)) == rf.MTD_DAY_ONE


def test_slots_after_report_seeds_periods_ref_and_pass_through():
    slots = ConversationSlots(
        mode="report",
        customer="Northstar Lines",
        perspective="customer",
        office_column="CUSTOMER_TEAM_NAME",
        office_value="Rotterdam",
    )
    after = rf.slots_after_report(slots, minimal_payload())
    assert after.mode == "default"
    assert (after.period_a, after.period_a_end) == (date(2026, 9, 1), date(2026, 10, 1))
    assert (after.period_b, after.period_b_end) == (date(2026, 8, 1), date(2026, 9, 1))
    assert after.report_ref == "run-test-0001"
    assert after.pass_through == (
        ("CUST_NM", "Meridian Bunkering"),
        ("CUST_NM", "Northstar Lines"),
    )
    # the office and everything else is carried
    assert (after.perspective, after.office_column, after.office_value) == (
        "customer",
        "CUSTOMER_TEAM_NAME",
        "Rotterdam",
    )
    assert after.customer == "Northstar Lines"


def test_slots_after_report_caps_pass_through_at_ten():
    payload = minimal_payload()
    rows = [{"key": f"Customer {i}"} for i in range(12)]
    payload["tables"] = dict(payload["tables"], entities_by_gp={"rows": rows})
    after = rf.slots_after_report(ConversationSlots(mode="report"), payload)
    assert len(after.pass_through) == 10
    assert after.pass_through[-1] == ("CUST_NM", "Customer 9")


def test_flow_slots_reset_clears_the_four_fields_and_keeps_the_rest():
    slots = ConversationSlots(
        customer="Northstar Lines",
        period_a=date(2026, 6, 1),
        period_a_end=date(2026, 7, 1),
        pass_through=(("CUST_NM", "Northstar Lines"),),
        perspective="customer",
        office_column="CUSTOMER_TEAM_NAME",
        office_value="Gibraltar Office",
        report_ref="run-1",
    )
    reset = rf.flow_slots_reset(slots)
    assert reset.mode == "report"
    assert (reset.perspective, reset.office_column, reset.office_value, reset.report_ref) == (
        None,
        None,
        None,
        None,
    )
    assert reset.customer == "Northstar Lines"
    assert (reset.period_a, reset.period_a_end) == (date(2026, 6, 1), date(2026, 7, 1))
    assert reset.pass_through == (("CUST_NM", "Northstar Lines"),)


def test_step_parts_text_then_chips_and_the_reask_replaces_the_prompt():
    data = FakeDataClient()
    parts = rf.step_parts("perspective", ConversationSlots(mode="report"), data, REFERENCE_DATE)
    assert parts == [
        {"kind": "text", "payload": {"markdown": rf.PERSPECTIVE_PROMPT}},
        {"kind": "chips", "payload": {"options": rf.PERSPECTIVE_CHIPS}},
    ]
    slots = ConversationSlots(
        mode="report", perspective="supplier", office_column="PRIMARY_SUPPLY_TEAM_OFFICE"
    )
    parts = rf.step_parts("office", slots, data, REFERENCE_DATE, reask=rf.OFFICE_REASK)
    assert parts[0] == {"kind": "text", "payload": {"markdown": rf.OFFICE_REASK}}
    assert [o["id"] for o in parts[1]["payload"]["options"]] == [
        "Med Supply Team",
        "NW Europe Supply",
    ]


def test_report_arguments_and_answer_summary():
    slots = ConversationSlots(
        mode="report",
        perspective="customer",
        office_column="CUSTOMER_TEAM_NAME",
        office_value="Gibraltar Office",
    )
    assert rf.report_arguments(slots, rf.MonthChoice(date(2026, 6, 1), "complete")) == {
        "perspective": "customer",
        "office_value": "Gibraltar Office",
        "month": "2026-06-01",
        "window_kind": "complete",
    }
    assert rf.answer_summary(minimal_payload()) == (
        "Report generated: Rotterdam (Customer Broker Office), September 2026 vs August 2026."
    )
