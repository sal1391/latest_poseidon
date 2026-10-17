"""Offline, through the real registry, against ``minimal_payload()`` served by a fake store."""

from types import SimpleNamespace

import pytest

from poseidon.core.config import Settings
from poseidon.core.identity import UserContext
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.tasks.reporting.skills.monthly_performance.tests.payload_fixture import (
    minimal_payload,
)
from poseidon.tasks.reporting.skills.report_lookup.skill import GONE_TEXT, NO_REPORT_TEXT

SKILL = "reporting.report_lookup"
DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"
USER = UserContext(
    sub="dev|alice", email="alice@example.com", name="Alice", roles=("Poseidon:Sales",)
)
RUN_ID = "run-test-0001"
HEAD = (
    "Report: run run-test-0001 - Rotterdam (Customer Broker Office) - September 2026 vs August 2026"
)


class _FakeStore:
    def __init__(self, payload=None):
        self.payload = minimal_payload() if payload is None else payload
        self.calls = []

    def get_run(self, user_sub, run_id):
        self.calls.append((user_sub, run_id))
        if run_id != RUN_ID:
            return None
        return SimpleNamespace(payload=self.payload)


class _RecordingData:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"data client called: {name}")

        return call


@pytest.fixture(scope="module")
def registry():
    return SkillRegistry.discover()


def _ctx(report_ref=RUN_ID, store=None, data=None):
    settings = Settings(_env_file=None, database_url=DSN, s3_bucket="poseidon-artifacts")
    return SkillContext(
        data=data,
        artifacts=None,
        settings=settings,
        state=ConversationSlots(report_ref=report_ref),
        user=USER,
        report_store=_FakeStore() if store is None else store,
    )


def _lookup(registry, ctx, **args):
    result = registry.dispatch(SKILL, args, ctx)
    assert result.ok, result.error
    return result


def test_no_report_ref_answers_open_a_report_first(registry):
    result = _lookup(registry, _ctx(report_ref=None), section="kpis")
    assert result.parts == [{"kind": "text", "payload": {"markdown": NO_REPORT_TEXT}}]
    assert result.proof == ["Report: none open"]


def test_missing_run_answers_gone(registry):
    result = _lookup(registry, _ctx(report_ref="run-other"), section="kpis")
    assert result.parts == [{"kind": "text", "payload": {"markdown": GONE_TEXT}}]
    assert result.proof == ["Report: run run-other not found"]


def test_kpis_is_a_metric_grid_with_prior_as_a(registry):
    store = _FakeStore()
    result = _lookup(registry, _ctx(store=store), section="kpis")
    assert store.calls == [("dev|alice", RUN_ID)]
    assert result.proof == [HEAD, "Section: kpis"]
    (part,) = result.parts
    assert part["kind"] == "metric_grid"
    assert part["payload"]["periods"] == {
        "a": {"start": "2026-08-01", "end": "2026-09-01"},
        "b": {"start": "2026-09-01", "end": "2026-10-01"},
    }
    by_name = {m["name"]: m for m in part["payload"]["metrics"]}
    assert by_name["GP"] == {
        "name": "GP",
        "friendly": "Gross Profit",
        "a": 100000,
        "b": 120000,
        "unit": "USD",
    }
    assert by_name["NUM_WON"]["friendly"] == "# Won"
    assert by_name["NUM_WON"]["unit"] is None


def test_entities_by_gp_pages_rows_and_reports_remaining(registry):
    first = _lookup(registry, _ctx(), section="entities_by_gp", limit=1)
    table, remaining = first.parts
    assert table["payload"]["columns"][:3] == ["Customer", "GP prior", "GP current"]
    assert len(table["payload"]["columns"]) == 13
    assert table["payload"]["rows"][0][0] == "Meridian Bunkering"
    assert len(table["payload"]["rows"]) == 1
    assert remaining["payload"]["markdown"] == "2 rows remaining; ask again with offset=1."
    assert first.proof == [HEAD, "Section: entities_by_gp rows 1-1 of 3"]

    last = _lookup(registry, _ctx(), section="entities_by_gp", limit=5, offset=1)
    assert len(last.parts) == 1
    rows = last.parts[0]["payload"]["rows"]
    assert [r[0] for r in rows] == ["Northstar Lines", "Total"]
    assert last.proof[1] == "Section: entities_by_gp rows 2-3 of 3"


def test_movers_direction_and_driver(registry):
    result = _lookup(registry, _ctx(), section="movers")
    (part,) = result.parts
    assert part["payload"]["columns"] == [
        "Customer",
        "Direction",
        "Driver",
        "GP prior",
        "GP current",
        "GP change",
        "GP %",
    ]
    rows = part["payload"]["rows"]
    assert [(r[0], r[1], r[2]) for r in rows] == [
        ("Meridian Bunkering", "up", "volume-driven"),
        ("Northstar Lines", "down", "volume-driven"),
    ]


def test_new_lost_uses_current_for_new_and_prior_for_lost(registry):
    result = _lookup(registry, _ctx(), section="new_lost")
    (part,) = result.parts
    assert part["payload"]["columns"] == ["Customer", "Status", "GP", "Volume", "Margin"]
    new, lost = part["payload"]["rows"]
    assert new[:3] == ["Oceanic Fuels", "new", "5,000"]
    assert lost[:3] == ["Harbor Tankers", "lost", "4,000"]


def test_narrative_prefers_a_grounded_model_narrative(registry):
    grounded = minimal_payload()
    grounded["narrative"].update(llm_md="MODEL TEXT", grounded=True, flags=["a", "b"])
    result = _lookup(registry, _ctx(store=_FakeStore(grounded)), section="narrative")
    assert result.parts[0]["payload"]["markdown"] == "MODEL TEXT"
    assert result.proof[-1] == "Flags: a; b"

    ungrounded = minimal_payload()
    ungrounded["narrative"].update(llm_md="MODEL TEXT", grounded=False)
    result = _lookup(registry, _ctx(store=_FakeStore(ungrounded)), section="narrative")
    assert result.parts[0]["payload"]["markdown"] == ungrounded["narrative"]["template_md"]
    assert result.proof[-1] == "Flags: none"


def test_context_for_a_book_report_is_a_sentence(registry):
    book = minimal_payload(entity_context=None)
    result = _lookup(registry, _ctx(store=_FakeStore(book)), section="context")
    assert result.parts == [
        {"kind": "text", "payload": {"markdown": "A whole-book report has no entity context."}}
    ]


def test_context_table_joins_offsets_and_ports(registry):
    result = _lookup(registry, _ctx(), section="context")
    (part,) = result.parts
    assert part["payload"]["columns"] == [
        "Customer",
        "Book GP prior",
        "Book GP current",
        "Book change",
        "Share prior",
        "Share current",
        "Office change",
        "Other offices net",
        "Offsets",
        "Ports (rest of book)",
    ]
    (row,) = part["payload"]["rows"]
    assert row == [
        "Meridian Bunkering",
        "90,000",
        "130,000",
        "+40,000",
        "44.4%",
        "53.8%",
        "+30,000",
        "+10,000",
        "Northstar Lines -10,000",
        "Singapore +10,000",
    ]


def test_no_data_client_call_is_ever_made(registry):
    data = _RecordingData()
    for section in (
        "kpis", "entities_by_gp", "entities_by_volume", "ports_by_gp", "ports_by_volume",
        "deal_class", "movers", "new_lost", "narrative", "context",
    ):  # fmt: skip
        _lookup(registry, _ctx(data=data), section=section)
    assert data.calls == []
