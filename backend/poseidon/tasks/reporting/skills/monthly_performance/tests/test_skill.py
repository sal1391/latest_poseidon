"""Offline, through the real registry, against FakeDataClient's hand-built
multi-office rows. Every number asserted below is derived by hand from
fake_client.multi_office_rows() -- the derivations are in the comments."""

from datetime import date

import pytest

from poseidon.core.config import Settings
from poseidon.core.identity import UserContext
from poseidon.core.llm.roles import RoleClient
from poseidon.core.llm.stub import StubProvider
from poseidon.core.llm.types import LLMResponse, ToolCall
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.tasks.reporting.skills.monthly_performance import skill as skill_module
from poseidon.tasks.reporting.skills.monthly_performance.tests.fake_client import (
    FakeDataClient,
    multi_office_rows,
)
from poseidon.tasks.reporting.skills.monthly_performance.tools import render
from poseidon.tasks.reporting.skills.monthly_performance.tools.narrate import NARRATE_TOOL

SKILL = "reporting.monthly_performance"
DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"
USER = UserContext(
    sub="dev|alice", email="alice@example.com", name="Alice", roles=("Poseidon:Sales",)
)
JUNE = date(2026, 6, 1)


class _FakeStore:
    def __init__(self):
        self.inserted = []

    def insert_run(self, user_sub, run):
        self.inserted.append((user_sub, run))


class _ExplodingLLM:
    def invoke(self, *args, **kwargs):
        raise AssertionError("the model must not be called in this scenario")


@pytest.fixture(autouse=True)
def _pdf_and_today(monkeypatch):
    monkeypatch.setattr(render, "render_pdf", lambda html: b"%PDF-fake")
    monkeypatch.setattr(skill_module, "_today", lambda: date(2026, 10, 14))


@pytest.fixture(scope="module")
def registry():
    return SkillRegistry.discover()


def _ctx(data=None, store=None, llm=None, llm_mode="stub", user=USER):
    settings = Settings(
        _env_file=None, database_url=DSN, s3_bucket="poseidon-artifacts", llm_mode=llm_mode
    )
    return SkillContext(
        data=data or FakeDataClient(),
        artifacts=None,
        settings=settings,
        state=ConversationSlots(),
        llm=llm,
        user=user,
        report_store=_FakeStore() if store is None else store,
    )


def _run(registry, ctx, **args):
    base = {"perspective": "customer", "office_value": "Gibraltar Office", "month": "2026-06-01"}
    base.update(args)
    return registry.dispatch(SKILL, base, ctx)


def _payload(result):
    assert result.ok, result.error
    assert [p["kind"] for p in result.parts] == ["report"]
    return result.parts[0]["payload"]


def _tool_reply(narrative_md, questions, insights=None):
    return LLMResponse(
        text="",
        tool_calls=(
            ToolCall(
                id="t1",
                name="reporting.narrate",
                arguments={
                    "narrative_md": narrative_md,
                    "insights": insights
                    or [
                        "Northstar Lines added 1,000 tons.",
                        "Meridian Bunkering lost 2,000 tons.",
                        "The office margin moved to 28.02/ton.",
                    ],
                    "context_notes": [],
                    "suggested_questions": questions,
                },
            ),
        ),
        stop_reason="tool_use",
        input_tokens=1,
        output_tokens=1,
    )


GOOD_NARRATIVE = (
    "Northstar Lines grew GP +30,000 (+30.0%) on volume. Meridian Bunkering fell -60,000 (-30.0%)."
)
GOOD_QUESTIONS = [
    "Why did GP drop for Meridian Bunkering?",
    "Where else does Northstar Lines lift fuel?",
    "Which deal class drove the margin change?",
]
# Meridian's -60,000 and -30.0% handed to Northstar: two unverified tokens
# (Northstar's own strings are +30,000 and +30.0%; the sign must match).
BAD_NARRATIVE = "Northstar Lines' GP fell 60,000 (-30.0%) on lower volume."


def _live_ctx(script, store=None):
    provider = StubProvider(script)
    settings = Settings(
        _env_file=None, database_url=DSN, s3_bucket="poseidon-artifacts", llm_mode="live"
    )
    llm = RoleClient(settings, providers={"bedrock": provider})
    return _ctx(store=store, llm=llm, llm_mode="live"), provider


# ---------------------------------------------------------------------------
# the customer office run
# ---------------------------------------------------------------------------


def test_customer_office_run_is_ok_and_carries_the_hand_derived_figures(registry):
    """Gibraltar Office, May -> June 2026. Prior GP 380,000 (100+200+60+20+0 k),
    current 353,000 (130+140+66+12+5 k): -27,000 = -7.1%. Volume 13,700 ->
    12,600: -1,100 = -8.0%. Margin 27.74 -> 28.02 (+0.28). Won 39 -> 35,
    inquiries 66 -> 64, lost 27 -> 29. Win rate 59.1% -> 54.7%."""
    ctx = _ctx()
    payload = _payload(_run(registry, ctx))

    assert payload["payload_version"] == 1 and payload["perspective"] == "customer"
    assert payload["entity"] == {"column": "CUST_NM", "label": "Customer"}
    assert payload["scope"] == {
        "office_column": "CUSTOMER_TEAM_NAME",
        "office_value": "Gibraltar Office",
        "label": "Gibraltar Office (Customer Broker Office)",
    }
    assert payload["periods"]["current"]["label"] == "June 2026"
    assert payload["periods"]["prior"]["label"] == "May 2026"
    assert payload["periods"]["window_kind"] == "complete"
    gp = payload["kpis"]["GP"]["display"]
    assert gp == {"prior": "380,000", "current": "353,000", "change": "-27,000", "pct": "-7.1%"}
    assert payload["kpis"]["VOLUME"]["display"]["change"] == "-1,100"
    assert payload["kpis"]["MARGIN"]["display"] == {
        "prior": "27.74",
        "current": "28.02",
        "change": "+0.28",
        "pct": "+1.0%",
    }
    assert payload["kpis"]["WIN_RATE"]["display"]["prior"] == "59.1%"
    assert payload["kpis"]["NUM_LOST"]["display"]["change"] == "+2"
    assert [r["key"] for r in payload["tables"]["entities_by_gp"]["rows"]] == [
        "Meridian Bunkering",
        "Northstar Lines",
        "Atlas Tankers",
        "Orion Offshore",
        "Vega Bulk",
        "Kestrel Lines",
    ]
    assert payload["tables"]["entities_by_gp"]["other"] is None
    assert [(m["key"], m["driver"]) for m in payload["movers"]["increases"]] == [
        ("Northstar Lines", "volume-driven"),
        ("Orion Offshore", "new"),
        ("Atlas Tankers", "margin-driven"),
        ("Vega Bulk", "margin-driven"),
    ]
    assert [(m["key"], m["driver"]) for m in payload["movers"]["decreases"]] == [
        ("Meridian Bunkering", "volume-driven"),
        ("Kestrel Lines", "lost"),
    ]
    assert [r["key"] for r in payload["new_lost"]["new"]] == ["Orion Offshore"]
    assert [r["key"] for r in payload["new_lost"]["lost"]] == ["Kestrel Lines"]
    assert payload["tables"]["ports_by_gp"]["rows"][0]["key"] == "Gibraltar"
    # TRADED June = 130,000 + 66,000 + 12,000 = 208,000; INVENTORY = 140,000 + 5,000 = 145,000.
    assert [r["key"] for r in payload["tables"]["deal_class"]["rows"]] == ["TRADED", "INVENTORY"]
    assert payload["narrative"]["llm_md"] is None and payload["narrative"]["grounded"] is True
    assert payload["narrative"]["flags"] == ["model narrative skipped: stub mode"]
    assert payload["narrative"]["template_md"].startswith(
        "**May 2026 to June 2026, Gibraltar Office (Customer Broker Office).**"
    )
    assert payload["suggested_questions"] == [
        "Why did GP change for Meridian Bunkering?",
        "Where else does Meridian Bunkering lift fuel?",
        "What were the top ports in the report?",
    ]
    assert payload["source"] == "synthetic" and payload["generated_by"] == "Alice"
    assert [q["label"] for q in payload["queries"]] == [
        "frame prior",
        "frame current",
        "total prior",
        "total current",
        "context prior",
        "context current",
    ]
    assert any(line.startswith("Run: report_run ") for line in payload["proof"])


def test_entity_context_share_offsets_and_reconciliation(registry):
    """Meridian: office 200,000 -> 140,000 (-60,000); book 300,000 -> 290,000
    (-10,000, -3.3%); share 66.7% -> 48.3%; other offices net +50,000; Spain
    Office +50,000 is the one opposite-sign offset; Algeciras +50,000 is the
    rest-of-book port. Northstar: book +20,000, Spain Office -10,000. Atlas:
    one office, share 100.0%, no offsets. Vega: zero prior book GP -> n/a."""
    payload = _payload(_run(registry, _ctx()))
    context = payload["entity_context"]
    meridian = context["Meridian Bunkering"]
    assert meridian["book"]["GP"]["display"] == {
        "prior": "300,000",
        "current": "290,000",
        "change": "-10,000",
        "pct": "-3.3%",
    }
    assert meridian["share_of_book"]["display"] == {"prior": "66.7%", "current": "48.3%"}
    assert meridian["reconciliation"]["display"] == {
        "office_change": "-60,000",
        "other_offices_net": "+50,000",
        "book_change": "-10,000",
    }
    assert [(o["key"], o["display"]) for o in meridian["offsets"]] == [("Spain Office", "+50,000")]
    assert [(p["key"], p["display"]) for p in meridian["ports_rest_of_book"]] == [
        ("Algeciras", "+50,000")
    ]
    northstar = context["Northstar Lines"]
    assert northstar["book"]["GP"]["display"]["change"] == "+20,000"
    assert [(o["key"], o["display"]) for o in northstar["offsets"]] == [("Spain Office", "-10,000")]
    atlas = context["Atlas Tankers"]
    assert atlas["share_of_book"]["display"] == {"prior": "100.0%", "current": "100.0%"}
    assert atlas["offsets"] == [] and atlas["ports_rest_of_book"] == []
    assert atlas["reconciliation"]["display"]["other_offices_net"] == "flat"
    assert context["Vega Bulk"]["share_of_book"]["display"]["prior"] == "n/a"
    assert context["Vega Bulk"]["share_of_book"]["prior"] is None
    assert context["Kestrel Lines"]["share_of_book"]["display"]["current"] == "n/a"
    assert set(context) == {
        "Northstar Lines",
        "Orion Offshore",
        "Atlas Tankers",
        "Vega Bulk",
        "Meridian Bunkering",
        "Kestrel Lines",
    }


def test_every_footer_display_equals_its_kpi_tile(registry):
    payload = _payload(_run(registry, _ctx()))
    kpis = payload["kpis"]
    for name, table in payload["tables"].items():
        footer = table["footer"]
        assert footer["key"] == "Total", name
        for metric in ("GP", "VOLUME", "MARGIN"):
            assert footer["display"][metric] == kpis[metric]["display"], (name, metric)


def test_payload_carries_a_hostile_name_verbatim(registry):
    """Review Focus 2: the engine stores the name as data; the renderer
    (Task 6) is what escapes it."""
    hostile = '<img src="http://evil.example/x.png"> {{ 7*7 }}'
    rows = [
        dict(r, CUST_NM=hostile) if r["CUST_NM"] == "Atlas Tankers" else r
        for r in multi_office_rows()
    ]
    store = _FakeStore()
    payload = _payload(_run(registry, _ctx(data=FakeDataClient(rows=rows), store=store)))
    assert any(r["key"] == hostile for r in payload["tables"]["entities_by_gp"]["rows"])
    assert hostile in payload["entity_context"]
    html = store.inserted[0][1].html.decode("utf-8")
    assert "<img" not in html and "&lt;img" in html


def test_run_row_and_artifact(registry):
    store = _FakeStore()
    result = _run(registry, _ctx(store=store))
    payload = _payload(result)
    assert len(store.inserted) == 1
    sub, run = store.inserted[0]
    assert sub == "dev|alice" and run.created_by == "dev|alice"
    assert run.id == payload["run_id"] and run.status == "ok" and run.payload_version == 1
    assert run.perspective == "customer" and run.office_column == "CUSTOMER_TEAM_NAME"
    assert run.office_value == "Gibraltar Office" and run.window_kind == "complete"
    assert run.period_current.start == JUNE and run.period_prior.start == date(2026, 5, 1)
    assert run.pdf == b"%PDF-fake"
    assert run.html.lstrip().startswith((b"<!", b"<html"))
    assert run.narrative_grounded is True and run.source == "synthetic"
    assert [(a.name, a.url, a.mime) for a in result.artifacts] == [
        (
            "Gibraltar Office June 2026.pdf",
            f"/api/reports/{payload['run_id']}/pdf",
            "application/pdf",
        )
    ]
    assert result.proof == payload["proof"]


def test_pdf_failure_is_a_flag_not_a_failed_run(registry, monkeypatch):
    def _boom(html):
        raise OSError("cannot load library 'gobject-2.0-0'")

    monkeypatch.setattr(render, "render_pdf", _boom)
    store = _FakeStore()
    result = _run(registry, _ctx(store=store))
    payload = _payload(result)
    assert "PDF not rendered: OSError" in payload["narrative"]["flags"]
    assert store.inserted[0][1].pdf is None and result.artifacts == []


# ---------------------------------------------------------------------------
# the other perspectives
# ---------------------------------------------------------------------------


def test_supplier_perspective_ranks_suppliers_in_the_supply_office(registry):
    """Med Supply Team is every Shell row: May 360,000 (100+200+60+0 k), June
    341,000 (130+140+66+5 k). One supplier, so the context says share 100%."""
    payload = _payload(
        _run(registry, _ctx(), perspective="supplier", office_value="Med Supply Team")
    )
    assert payload["entity"] == {"column": "SUPPLIER_NM", "label": "Supplier"}
    assert payload["scope"]["label"] == "Med Supply Team (Supply Team Office)"
    assert payload["kpis"]["GP"]["display"] == {
        "prior": "360,000",
        "current": "341,000",
        "change": "-19,000",
        "pct": "-5.3%",
    }
    assert [r["key"] for r in payload["tables"]["entities_by_gp"]["rows"]] == ["Shell"]
    assert payload["entity_context"]["Shell"]["share_of_book"]["display"] == {
        "prior": "100.0%",
        "current": "100.0%",
    }
    assert (
        payload["suggested_questions"][1] == "Which customers did Shell supply in Med Supply Team?"
    )
    assert "## New and lost suppliers" in payload["narrative"]["template_md"]


def test_book_perspective_has_no_office_and_no_context(registry):
    """Whole book June: 130+40+140+150+66+12+5 = 543,000;
    May: 100+50+200+100+60+20+0+30 = 560,000."""
    store = _FakeStore()
    result = _run(registry, _ctx(store=store), perspective="book", office_value=None)
    payload = _payload(result)
    assert payload["scope"] == {"office_column": None, "office_value": None, "label": "Whole book"}
    assert payload["entity_context"] is None
    assert payload["kpis"]["GP"]["display"]["current"] == "543,000"
    assert payload["kpis"]["GP"]["display"]["prior"] == "560,000"
    assert [q["label"] for q in payload["queries"]] == [
        "frame prior",
        "frame current",
        "total prior",
        "total current",
    ]
    assert "Context: none (whole book report)" in payload["proof"]
    assert result.artifacts[0].name == "Whole book June 2026.pdf"
    assert store.inserted[0][1].office_column is None


def test_book_perspective_rejects_an_office_value(registry):
    result = _run(registry, _ctx(), perspective="book", office_value="Gibraltar Office")
    assert result.ok is False and result.error["status"] == 422
    assert "office_value" in result.error["detail"]


def test_customer_perspective_requires_an_office_value(registry):
    result = _run(registry, _ctx(), office_value=None)
    assert result.ok is False and result.error["status"] == 422
    assert "office_value" in result.error["detail"]


# ---------------------------------------------------------------------------
# sparse and empty slices (spec section 10, Review Focus 1)
# ---------------------------------------------------------------------------


def test_office_with_rows_in_the_prior_month_only_is_an_ok_run_with_lost_entities(registry):
    """Malta Office: Solent Ferries in May only. Current is all zero, Solent
    is lost, the footers are zero, no narrative call, questions backfilled."""
    store = _FakeStore()
    ctx = _ctx(store=store, llm=_ExplodingLLM(), llm_mode="live")
    payload = _payload(_run(registry, ctx, office_value="Malta Office"))
    assert payload["kpis"]["GP"]["display"] == {
        "prior": "30,000",
        "current": "0",
        "change": "-30,000",
        "pct": "-100.0%",
    }
    rows = payload["tables"]["entities_by_gp"]["rows"]
    assert [(r["key"], r["status"]) for r in rows] == [("Solent Ferries", "lost")]
    assert payload["tables"]["ports_by_gp"]["footer"]["display"]["GP"]["current"] == "0"
    assert payload["movers"]["increases"] == []
    assert [(m["key"], m["driver"]) for m in payload["movers"]["decreases"]] == [
        ("Solent Ferries", "lost")
    ]
    assert payload["narrative"]["flags"] == ["model narrative skipped: no rows in June 2026"]
    assert payload["narrative"]["llm_md"] is None
    assert payload["entity_context"]["Solent Ferries"]["share_of_book"]["display"] == {
        "prior": "100.0%",
        "current": "n/a",
    }
    assert payload["suggested_questions"] == [
        "Why did GP change for Solent Ferries?",
        "Where else does Solent Ferries lift fuel?",
        "What were the top ports in the report?",
    ]
    assert store.inserted[0][1].status == "ok"


def test_no_rows_in_either_month_is_an_ok_run_with_no_data_sections(registry):
    """Valencia Office exists (April rows) but has nothing in May or June."""
    store = _FakeStore()
    ctx = _ctx(store=store, llm=_ExplodingLLM(), llm_mode="live")
    payload = _payload(_run(registry, ctx, office_value="Valencia Office"))
    assert payload["narrative"]["flags"] == ["no data in either month"]
    assert payload["suggested_questions"] == []
    assert all(table["rows"] == [] for table in payload["tables"].values())
    assert payload["movers"] == {"increases": [], "decreases": []}
    assert payload["new_lost"] == {"new": [], "lost": []}
    assert payload["entity_context"] == {}
    assert payload["kpis"]["GP"]["display"] == {
        "prior": "0",
        "current": "0",
        "change": "flat",
        "pct": "flat",
    }
    assert (
        "Overall volume was flat, while gross profit was flat."
        in payload["narrative"]["template_md"]
    )
    assert "Context: none (no movers)" in payload["proof"]
    assert store.inserted[0][1].status == "ok"


# ---------------------------------------------------------------------------
# refusals before any query (spec sections 5.2, 10; Review Focus 5)
# ---------------------------------------------------------------------------


def test_month_absent_from_the_data_is_a_422_and_writes_nothing(registry):
    store = _FakeStore()
    result = _run(registry, _ctx(store=store), month="2026-03-01")
    assert result.ok is False and result.error["status"] == 422
    assert result.error["title"] == "no data for that month"
    assert "March 2026" in result.error["detail"]
    assert store.inserted == []


def test_running_month_as_complete_is_a_422(registry, monkeypatch):
    monkeypatch.setattr(skill_module, "_today", lambda: date(2026, 6, 15))
    result = _run(registry, _ctx())
    assert result.ok is False and result.error["status"] == 422
    assert "not complete yet" in result.error["detail"]


def test_mtd_on_day_one_returns_422_before_any_query(registry, monkeypatch):
    monkeypatch.setattr(skill_module, "_today", lambda: date(2026, 6, 1))
    data = FakeDataClient()
    result = _run(registry, _ctx(data=data), window_kind="mtd")
    assert result.ok is False and result.error["status"] == 422
    assert "no complete days" in result.error["detail"]
    assert data.calls == []


def test_mtd_run_names_month_to_date_in_labels_and_the_file(registry, monkeypatch):
    """June 1-14 vs May 1-14: Northstar, Meridian, Atlas, Orion and Vega all
    lift on the 10th, so the day-matched slices carry the same June rows
    and May rows as the complete run."""
    monkeypatch.setattr(skill_module, "_today", lambda: date(2026, 6, 15))
    result = _run(registry, _ctx(), window_kind="mtd")
    payload = _payload(result)
    assert payload["periods"]["current"]["label"] == "June 1 to 14, 2026 (month to date)"
    assert payload["periods"]["prior"]["label"] == "May 1 to 14, 2026"
    assert payload["periods"]["window_kind"] == "mtd"
    assert payload["kpis"]["GP"]["display"]["current"] == "353,000"
    assert result.artifacts[0].name == "Gibraltar Office June 2026 month to date.pdf"
    assert "window: month to date" in payload["proof"][1]


def test_unknown_office_is_a_422(registry):
    result = _run(registry, _ctx(), office_value="Atlantis Office")
    assert result.ok is False and result.error["status"] == 422
    assert result.error["detail"] == "unknown CUSTOMER_TEAM_NAME value 'Atlantis Office'"


def test_month_not_on_day_one_is_a_422(registry):
    result = _run(registry, _ctx(), month="2026-06-15")
    assert result.ok is False and result.error["status"] == 422


def test_missing_report_store_is_a_structured_500(registry):
    ctx = SkillContext(
        data=FakeDataClient(),
        artifacts=None,
        settings=Settings(_env_file=None, database_url=DSN, s3_bucket="poseidon-artifacts"),
        state=ConversationSlots(),
        user=USER,
    )
    result = _run(registry, ctx)
    assert result.ok is False and result.error["status"] == 500
    assert result.error["title"] == "report store unavailable"


# ---------------------------------------------------------------------------
# failures after the queries (spec section 10)
# ---------------------------------------------------------------------------


def test_reconciliation_failure_is_an_error_and_writes_no_row(registry):
    store = _FakeStore()
    result = _run(registry, _ctx(data=FakeDataClient(tamper_total=1.0), store=store))
    assert result.ok is False and result.error["status"] == 500
    assert result.error["title"] == "reconciliation failed"
    assert "May 2026" in result.error["detail"]
    assert store.inserted == []


def test_reconciliation_tolerates_half_a_cent(registry):
    assert _run(registry, _ctx(data=FakeDataClient(tamper_total=0.004))).ok is True


def test_data_backend_error_is_a_500_and_writes_no_row(registry):
    store = _FakeStore()
    data = FakeDataClient(fail_frames_with=RuntimeError("connection reset"))
    result = _run(registry, _ctx(data=data, store=store))
    assert result.ok is False and result.error["status"] == 500
    assert "connection reset" in result.error["detail"]
    assert store.inserted == []


def test_row_scope_is_resolved_once_and_stamped_on_every_spec(registry, monkeypatch):
    """D16: every spec the skill builds carries the resolved scope value."""
    seen = []
    monkeypatch.setattr(skill_module, "resolve_row_scope_value", lambda entity, user: "SCOPED")

    class _Spy(FakeDataClient):
        def run_frame_query(self, spec):
            seen.append(spec.scope_value)
            return super().run_frame_query(spec)

        def run_metric_query(self, spec):
            seen.append(spec.scope_value)
            return super().run_metric_query(spec)

    assert _run(registry, _ctx(data=_Spy())).ok is True
    assert seen and set(seen) == {"SCOPED"}


# ---------------------------------------------------------------------------
# the model narrative (live mode with a scripted provider)
# ---------------------------------------------------------------------------


def test_live_mode_uses_the_model_narrative_when_it_grounds(registry):
    ctx, provider = _live_ctx([_tool_reply(GOOD_NARRATIVE, GOOD_QUESTIONS)])
    payload = _payload(_run(registry, ctx))
    assert payload["narrative"]["llm_md"] == GOOD_NARRATIVE
    assert payload["narrative"]["grounded"] is True and payload["narrative"]["flags"] == []
    assert payload["suggested_questions"] == GOOD_QUESTIONS
    assert len(provider.calls) == 1
    assert provider.calls[0]["tool_choice"] == "reporting.narrate"
    assert provider.calls[0]["tools"] == [NARRATE_TOOL]
    assert "marginals" in provider.calls[0]["system"]
    content = provider.calls[0]["messages"][0]["content"]
    assert '"353,000"' in content and "353000" not in content
    assert any("grounded (" in line and "of" in line for line in payload["proof"])


def test_grounding_failure_twice_uses_the_template_and_flags_the_count(registry):
    """Review Focus 4: the retry names the offending sentence; the second
    failure withholds the model prose and shows the count."""
    store = _FakeStore()
    ctx, provider = _live_ctx(
        [_tool_reply(BAD_NARRATIVE, GOOD_QUESTIONS), _tool_reply(BAD_NARRATIVE, GOOD_QUESTIONS)],
        store=store,
    )
    payload = _payload(_run(registry, ctx))
    assert len(provider.calls) == 2
    assert BAD_NARRATIVE in provider.calls[1]["messages"][0]["content"]
    assert "failed the figure check" in provider.calls[1]["messages"][0]["content"]
    assert payload["narrative"]["llm_md"] is None
    assert payload["narrative"]["grounded"] is False
    assert payload["narrative"]["flags"] == ["AI narrative withheld: 2 unverified figures"]
    assert payload["suggested_questions"] == GOOD_QUESTIONS
    assert store.inserted[0][1].narrative_grounded is False


def test_grounding_failure_then_success_keeps_the_retry(registry):
    ctx, provider = _live_ctx(
        [_tool_reply(BAD_NARRATIVE, GOOD_QUESTIONS), _tool_reply(GOOD_NARRATIVE, GOOD_QUESTIONS)]
    )
    payload = _payload(_run(registry, ctx))
    assert len(provider.calls) == 2
    assert (
        payload["narrative"]["llm_md"] == GOOD_NARRATIVE
        and payload["narrative"]["grounded"] is True
    )


def test_malformed_tool_output_uses_the_template_and_flags_it(registry):
    plain = LLMResponse(
        text="Here is prose instead of a tool call.",
        tool_calls=(),
        stop_reason="end_turn",
        input_tokens=1,
        output_tokens=1,
    )
    ctx, provider = _live_ctx([plain])
    payload = _payload(_run(registry, ctx))
    assert payload["narrative"]["llm_md"] is None and payload["narrative"]["grounded"] is False
    assert payload["narrative"]["flags"] == ["model narrative failed: malformed tool output"]
    assert len(provider.calls) == 1
    assert len(payload["suggested_questions"]) == 3


def test_provider_error_uses_the_template_and_flags_it(registry):
    error = LLMResponse(
        text="", tool_calls=(), stop_reason="error", input_tokens=0, output_tokens=0
    )
    ctx, _ = _live_ctx([error])
    payload = _payload(_run(registry, ctx))
    assert payload["narrative"]["flags"] == ["model narrative failed: provider error"]
    assert payload["narrative"]["grounded"] is False


def test_model_questions_naming_unknown_entities_are_dropped_and_backfilled(registry):
    questions = [
        "Why did Poseidon Shipping leave?",
        "Why did GP drop for Meridian Bunkering?",
        "Any news on Atlantis Office?",
    ]
    ctx, _ = _live_ctx([_tool_reply(GOOD_NARRATIVE, questions)])
    payload = _payload(_run(registry, ctx))
    assert payload["suggested_questions"][0] == "Why did GP drop for Meridian Bunkering?"
    assert len(payload["suggested_questions"]) == 3
    assert not any("Poseidon" in q or "Atlantis" in q for q in payload["suggested_questions"])


def test_context_notes_are_appended_and_grounded(registry):
    reply = _tool_reply(GOOD_NARRATIVE, GOOD_QUESTIONS)
    args = dict(
        reply.tool_calls[0].arguments,
        context_notes=["Spain Office took +50,000 of Meridian Bunkering's volume."],
    )
    reply = LLMResponse(
        text="",
        tool_calls=(ToolCall(id="t1", name="reporting.narrate", arguments=args),),
        stop_reason="tool_use",
        input_tokens=1,
        output_tokens=1,
    )
    ctx, _ = _live_ctx([reply])
    payload = _payload(_run(registry, ctx))
    assert payload["narrative"]["grounded"] is True
    assert payload["narrative"]["llm_md"].endswith(
        "## Notes\n- Spain Office took +50,000 of Meridian Bunkering's volume."
    )


def test_no_llm_client_in_live_mode_is_a_flag(registry):
    payload = _payload(_run(registry, _ctx(llm=None, llm_mode="live")))
    assert payload["narrative"]["flags"] == ["model narrative failed: no LLM client"]
    assert payload["narrative"]["grounded"] is False


def test_anonymous_caller_is_recorded_as_dev_anonymous(registry):
    store = _FakeStore()
    payload = _payload(_run(registry, _ctx(store=store, user=None)))
    assert store.inserted[0][0] == "dev|anonymous" and payload["generated_by"] == "dev|anonymous"
