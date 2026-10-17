"""The monthly report flow through ``execute_turn`` (Phase 17 PR A2 Task 6),
turn by turn: the entry phrase, the perspective/office/month steps, the
report turn that dispatches ``reporting.monthly_performance`` by hand, and a
follow-up question routed with the carried office.

Offline. The data is the reporting skill's own FakeDataClient (April, May,
June 2026; Gibraltar/Malta/Spain/Valencia customer offices); the report
store is its FakeReportStore; the PDF renderer is replaced by fixed bytes.
The skill resolves its periods against its own ``_today``, so every test
pins that to the same reference date the orchestrator is handed.
Helpers mirror ``test_entry_orchestration.py``'s own (each test module owns
its private helpers)."""

import json
from datetime import date

import pytest

from poseidon.core.chat.dev_router import DevDeterministicRouter
from poseidon.core.chat.events import SseEnvelopeSink
from poseidon.core.chat.orchestrator import (
    ENTRY_PHRASE_EXISTING,
    ENTRY_PHRASE_REPORT,
    execute_turn,
)
from poseidon.core.chat.report_flow import (
    MONTH_PROMPT,
    MTD_DAY_ONE,
    OFFICE_PROMPT,
    OFFICE_REASK,
    PERSPECTIVE_CHIPS,
    PERSPECTIVE_PROMPT,
    PERSPECTIVE_REASK,
)
from poseidon.core.chat.state import ConversationStateStore
from poseidon.core.config import Settings
from poseidon.core.identity import DISABLED_DEFAULT_USER
from poseidon.core.llm.prompt_tree import PromptTree
from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
from poseidon.core.llm.roles import RoleClient
from poseidon.core.skills.registry import SkillRegistry
from poseidon.tasks.reporting.skills.monthly_performance import skill as skill_module
from poseidon.tasks.reporting.skills.monthly_performance.tests.fake_client import (
    FakeDataClient,
    FakeReportStore,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import render
from tests.test_chat_orchestrator import RecordingWriter

REGISTRY = SkillRegistry.discover()
REFERENCE_DATE = date(2026, 7, 15)
REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}
OFFICES = ["Gibraltar Office", "Malta Office", "Spain Office", "Valencia Office"]
REPORT_SKILL = "reporting.monthly_performance"


@pytest.fixture(autouse=True)
def _fake_pdf(monkeypatch):
    monkeypatch.setattr(render, "render_pdf", lambda html: b"%PDF-fake")


def _settings(monkeypatch) -> Settings:
    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LLM_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_ROUTER", raising=False)
    for key, value in {**REQUIRED_ENV, "LLM_MODE": "stub", "LLM_PROFILE": "bedrock"}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def _parse_frame(frame: str) -> tuple[str, dict]:
    assert frame.endswith("\n\n"), repr(frame)
    id_line, event_line, data_line = frame[:-2].split("\n")
    name = event_line[len("event: ") :]
    data = json.loads(data_line[len("data: ") :])
    assert data["event_seq"] == int(id_line[len("id: ") :])
    return name, data


class _Flow:
    """One conversation driven turn by turn; every turn gets a fresh sink."""

    def __init__(self, monkeypatch, conversation_id="conv-report", data=None):
        self.monkeypatch = monkeypatch
        self.settings = _settings(monkeypatch)
        self.state = ConversationStateStore()
        self.store = FakeReportStore()
        self.writer = RecordingWriter()
        self.data = data if data is not None else FakeDataClient()
        self.conversation_id = conversation_id
        self.role_client = RoleClient(self.settings, providers={"stub": DevDeterministicRouter()})
        self.turns = 0

    def say(self, text, reference_date=REFERENCE_DATE, data=None):
        self.monkeypatch.setattr(skill_module, "_today", lambda: reference_date)
        self.turns += 1
        frames: list[str] = []
        sink = SseEnvelopeSink(
            turn_id=f"turn-{self.turns}",
            message_id=f"msg-{self.turns}",
            send=frames.append,
            registry=REGISTRY,
        )
        outcome = execute_turn(
            conversation_id=self.conversation_id,
            user=DISABLED_DEFAULT_USER,
            text=text,
            client_turn_key=None,
            settings=self.settings,
            registry=REGISTRY,
            data=data if data is not None else self.data,
            state=self.state,
            writer=self.writer,
            role_client=self.role_client,
            prompt_registry=PromptRegistry(DEFAULT_PROMPTS_DIR),
            sink=sink,
            reference_date=reference_date,
            report_store=self.store,
            prompt_tree=PromptTree(),
        )
        return outcome, [_parse_frame(f) for f in frames]

    @property
    def slots(self):
        return self.state.get(self.conversation_id)

    def to_month_step(self):
        self.say(ENTRY_PHRASE_REPORT)
        self.say("customer office report")
        self.say("Gibraltar Office")


def _shape(decoded):
    """``accepted``/``done``/``error`` by name, ``tool(start)`` by status,
    ``part <kind>`` by kind."""
    out = []
    for name, data in decoded:
        if name == "part":
            out.append(f"part {data['kind']}")
        elif name == "tool":
            out.append(f"tool({data['status']})")
        else:
            out.append(name)
    return out


def _parts(decoded, kind):
    return [data["payload"] for name, data in decoded if name == "part" and data["kind"] == kind]


def test_entry_resets_the_flow_and_asks_the_perspective_with_chips(monkeypatch):
    flow = _Flow(monkeypatch)
    outcome, decoded = flow.say(ENTRY_PHRASE_REPORT)

    assert outcome.status == "clarify"
    assert _shape(decoded) == ["accepted", "part text", "part chips", "done"]
    assert decoded[1][1]["payload"] == {"markdown": PERSPECTIVE_PROMPT}
    assert decoded[2][1]["payload"] == {"options": PERSPECTIVE_CHIPS}
    assert flow.slots.mode == "report"
    assert flow.slots.perspective is None
    assert flow.writer.finalize_calls[-1]["status"] == "clarify"
    assert flow.writer.finalize_calls[-1]["answer_summary"] == PERSPECTIVE_PROMPT
    assert flow.writer.append_tool_calls == []


def test_entry_phrase_is_matched_casefolded(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.say("Start A Monthly Report")
    assert flow.slots.mode == "report"


def test_customer_perspective_asks_the_office_with_certified_chips(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.say(ENTRY_PHRASE_REPORT)
    outcome, decoded = flow.say("customer office report")

    assert outcome.status == "clarify"
    assert _shape(decoded) == ["accepted", "part text", "part chips", "done"]
    assert decoded[1][1]["payload"] == {"markdown": OFFICE_PROMPT["customer"]}
    assert decoded[2][1]["payload"] == {
        "options": [{"id": v, "label": v, "send_text": v} for v in OFFICES]
    }
    assert flow.slots.mode == "report"
    assert flow.slots.perspective == "customer"
    assert flow.slots.office_column == "CUSTOMER_TEAM_NAME"
    assert flow.writer.finalize_calls[-1]["answer_summary"] == OFFICE_PROMPT["customer"]


def test_unknown_perspective_reasks_with_the_chips_and_keeps_the_slots(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.say(ENTRY_PHRASE_REPORT)
    before = flow.slots
    outcome, decoded = flow.say("the regional one")

    assert outcome.status == "clarify"
    assert _shape(decoded) == ["accepted", "part text", "part chips", "done"]
    assert decoded[1][1]["payload"] == {"markdown": PERSPECTIVE_REASK}
    assert decoded[2][1]["payload"] == {"options": PERSPECTIVE_CHIPS}
    assert flow.slots == before


def test_book_perspective_skips_the_office_step(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.say(ENTRY_PHRASE_REPORT)
    outcome, decoded = flow.say("Whole book")

    assert outcome.status == "clarify"
    assert decoded[1][1]["payload"] == {"markdown": MONTH_PROMPT}
    assert [o["send_text"] for o in decoded[2][1]["payload"]["options"]] == [
        "June 2026",
        "May 2026",
        "April 2026",
    ]
    assert flow.slots.perspective == "book"
    assert flow.slots.office_column is None
    assert flow.slots.office_value is None


def test_office_step_matches_case_insensitively_and_reasks_unknown(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.say(ENTRY_PHRASE_REPORT)
    flow.say("customer office report")
    before = flow.slots

    outcome, decoded = flow.say("Atlantis")
    assert outcome.status == "clarify"
    assert _shape(decoded) == ["accepted", "part text", "part chips", "done"]
    assert decoded[1][1]["payload"] == {"markdown": OFFICE_REASK}
    assert [o["id"] for o in decoded[2][1]["payload"]["options"]] == OFFICES
    assert flow.slots == before  # no put on a re-ask
    assert flow.writer.append_tool_calls == []

    outcome, decoded = flow.say("gibraltar office")
    assert outcome.status == "clarify"
    assert "tool(start)" not in _shape(decoded)
    assert decoded[1][1]["payload"] == {"markdown": MONTH_PROMPT}
    assert decoded[2][1]["payload"]["options"][0] == {
        "id": "2026-06-01",
        "label": "June 2026 (latest)",
        "send_text": "June 2026",
    }
    assert flow.slots.office_value == "Gibraltar Office"
    assert flow.slots.mode == "report"


def test_month_step_reasks_for_missing_month_and_mtd_on_day_one(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.to_month_step()
    before = flow.slots

    outcome, decoded = flow.say("January 2026")
    assert outcome.status == "clarify"
    assert _shape(decoded) == ["accepted", "part text", "part chips", "done"]
    assert decoded[1][1]["payload"] == {"markdown": "No data for January 2026."}

    outcome, decoded = flow.say("month to date", reference_date=date(2026, 6, 1))
    assert outcome.status == "clarify"
    assert decoded[1][1]["payload"] == {"markdown": MTD_DAY_ONE}
    assert _shape(decoded)[-1] == "done"

    assert flow.slots == before
    assert flow.writer.append_tool_calls == []
    assert flow.store.runs == []


def test_month_step_dispatches_the_report_and_seeds_the_slots(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.to_month_step()

    outcome, decoded = flow.say("June 2026")

    assert outcome.status == "ok"
    assert _shape(decoded) == [
        "accepted",
        "tool(start)",
        "tool(done)",
        "part report",
        "part proof",
        "part artifact",
        "part chips",
        "done",
    ]
    assert decoded[1][1]["tool"] == REPORT_SKILL
    report = _parts(decoded, "report")[0]
    assert report["scope"]["office_value"] == "Gibraltar Office"
    run_id = report["run_id"]
    assert _parts(decoded, "artifact")[0]["url"] == f"/api/reports/{run_id}/pdf"
    questions = report["suggested_questions"]
    assert len(questions) == 3
    assert _parts(decoded, "chips")[0] == {
        "options": [
            {"id": f"q{i + 1}", "label": q, "send_text": q} for i, q in enumerate(questions)
        ]
    }

    slots = flow.slots
    assert slots.mode == "default"
    assert slots.report_ref == run_id
    assert slots.report_label == "Gibraltar Office, June 2026 vs May 2026"
    assert (slots.period_a, slots.period_a_end) == (date(2026, 6, 1), date(2026, 7, 1))
    assert (slots.period_b, slots.period_b_end) == (date(2026, 5, 1), date(2026, 6, 1))
    assert slots.perspective == "customer"
    assert slots.office_column == "CUSTOMER_TEAM_NAME"
    assert slots.office_value == "Gibraltar Office"
    expected_pass = tuple(
        ("CUST_NM", row["key"]) for row in report["tables"]["entities_by_gp"]["rows"][:10]
    )
    assert slots.pass_through == expected_pass and expected_pass

    assert len(flow.store.runs) == 1
    assert flow.store.runs[-1].conversation_id == flow.conversation_id
    tool_call = flow.writer.append_tool_calls[-1]
    assert tool_call["tool"] == REPORT_SKILL
    assert tool_call["args"] == {
        "perspective": "customer",
        "office_value": "Gibraltar Office",
        "month": "2026-06-01",
        "window_kind": "complete",
    }
    assert flow.writer.append_llm_calls == []
    final = flow.writer.finalize_calls[-1]
    assert final["status"] == "ok"
    assert final["answer_summary"] == (
        "Report generated: Gibraltar Office (Customer Broker Office), June 2026 vs May 2026."
    )


def test_month_to_date_dispatches_with_window_kind_mtd(monkeypatch):
    flow = _Flow(monkeypatch)
    reference = date(2026, 6, 15)
    flow.say(ENTRY_PHRASE_REPORT, reference_date=reference)
    flow.say("customer office report", reference_date=reference)
    flow.say("Gibraltar Office", reference_date=reference)

    outcome, decoded = flow.say("month to date", reference_date=reference)

    assert outcome.status == "ok", decoded
    # The SSE tool frame carries no arguments on the wire; the dispatched
    # arguments are the run log's tool_calls row and the stored run.
    args = flow.writer.append_tool_calls[-1]["args"]
    assert args["window_kind"] == "mtd"
    assert args["month"] == "2026-06-01"
    assert flow.store.runs[-1].window_kind == "mtd"
    assert flow.store.runs[-1].period_current.start == date(2026, 6, 1)


def test_report_dispatch_failure_keeps_the_flow_at_the_month_step(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.to_month_step()
    before = flow.slots

    outcome, decoded = flow.say("June 2026", data=FakeDataClient(tamper_total=1.0))

    assert outcome.status == "error"
    assert _shape(decoded) == ["accepted", "tool(start)", "tool(error)", "error"]
    assert decoded[-1][1]["code"] == "reconciliation failed"
    assert flow.slots == before
    assert flow.slots.mode == "report" and flow.slots.report_ref is None
    assert flow.store.runs == []
    assert flow.writer.finalize_calls[-1]["status"] == "error"

    outcome, decoded = flow.say("June 2026")
    assert outcome.status == "ok"
    assert flow.slots.report_ref == flow.store.runs[-1].id


def test_second_entry_click_starts_over(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.to_month_step()
    flow.say("June 2026")
    assert flow.slots.report_ref is not None

    outcome, decoded = flow.say(ENTRY_PHRASE_REPORT)

    assert outcome.status == "clarify"
    assert decoded[1][1]["payload"] == {"markdown": PERSPECTIVE_PROMPT}
    slots = flow.slots
    assert slots.mode == "report"
    assert (slots.perspective, slots.office_column, slots.office_value, slots.report_ref) == (
        None,
        None,
        None,
        None,
    )
    assert slots.report_label is None
    # the periods the report carried survive the reset
    assert slots.period_a == date(2026, 6, 1)


def test_a_brief_entry_mid_flow_clears_the_report_fields(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.to_month_step()
    assert flow.slots.perspective == "customer"
    assert flow.slots.office_value == "Gibraltar Office"

    outcome, _ = flow.say(ENTRY_PHRASE_EXISTING)

    assert outcome.status == "clarify"
    slots = flow.slots
    assert slots.mode == "existing_customer"
    assert (
        slots.perspective,
        slots.office_column,
        slots.office_value,
        slots.report_ref,
        slots.report_label,
    ) == (None, None, None, None, None)


def test_a_brief_entry_after_a_finished_report_keeps_the_office(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.to_month_step()
    flow.say("June 2026")
    run_id = flow.slots.report_ref
    assert run_id is not None

    flow.say(ENTRY_PHRASE_EXISTING)

    slots = flow.slots
    assert slots.mode == "existing_customer"
    assert slots.office_value == "Gibraltar Office"
    assert slots.report_ref == run_id


def test_a_question_after_the_report_routes_with_the_carried_office(monkeypatch):
    flow = _Flow(monkeypatch)
    flow.to_month_step()
    flow.say("June 2026")

    outcome, decoded = flow.say("GP for June 2026")

    assert outcome.status == "ok", decoded
    tools = [data["tool"] for name, data in decoded if name == "tool"]
    assert tools and set(tools) == {"data_qa.metric_query"}
    proof = _parts(decoded, "proof")[-1]["lines"]
    assert (
        proof[-1] == "Office scope: CUSTOMER_TEAM_NAME = Gibraltar Office (carried; scope=office)"
    )
