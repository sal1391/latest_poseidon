"""Offline wiring tests for the brief store: ``create_app`` builds one and
``execute_turn`` threads it into every ``SkillContext`` it builds."""

from poseidon.briefs.store import BriefStore
from poseidon.core.llm.roles import RoleClient
from poseidon.core.skills.registry import SkillRegistry
from tests.test_chat_orchestrator import FakeDataClient, RecordingWriter
from tests.test_live_chat_sse import _live_app, _settings


def test_create_app_wires_a_brief_store():
    assert isinstance(_live_app().state.brief_store, BriefStore)


def test_execute_turn_threads_brief_store_to_both_context_sites(monkeypatch):
    from poseidon.core.chat.dev_router import DevDeterministicRouter
    from poseidon.core.chat.events import SseEnvelopeSink
    from poseidon.core.chat.orchestrator import execute_turn
    from poseidon.core.chat.state import ConversationStateStore
    from poseidon.core.identity import DISABLED_DEFAULT_USER
    from poseidon.core.llm.prompts import DEFAULT_PROMPTS_DIR, PromptRegistry
    from poseidon.core.skills.context import ConversationSlots
    from poseidon.core.skills.result import SkillResult
    from tests.test_chat_orchestrator import REFERENCE_DATE

    app = _live_app(deploy_mode="ec2")
    sentinel = object()

    seen: list = []
    real_dispatch = SkillRegistry.dispatch

    def spy(self, skill_id, arguments, ctx):
        seen.append((skill_id, ctx))
        if skill_id.startswith("customer_insight."):
            return SkillResult(ok=True, parts=[], proof=["spy: brief dispatch intercepted"])
        return real_dispatch(self, skill_id, arguments, ctx)

    monkeypatch.setattr(SkillRegistry, "dispatch", spy)

    settings = _settings(chat_mode="live", deploy_mode="ec2")
    state = ConversationStateStore()
    role_client = RoleClient(settings, providers={"stub": DevDeterministicRouter()})
    prompt_registry = PromptRegistry(DEFAULT_PROMPTS_DIR)

    def run(conversation_id: str, text: str, ctk: str):
        sink = SseEnvelopeSink(
            turn_id=f"turn-{ctk}",
            message_id=f"msg-{ctk}",
            send=lambda _frame: None,
            registry=app.state.skill_registry,
        )
        return execute_turn(
            conversation_id=conversation_id,
            user=DISABLED_DEFAULT_USER,
            text=text,
            client_turn_key=ctk,
            settings=settings,
            registry=app.state.skill_registry,
            data=FakeDataClient(),
            state=state,
            writer=RecordingWriter(),
            role_client=role_client,
            prompt_registry=prompt_registry,
            sink=sink,
            reference_date=REFERENCE_DATE,
            brief_store=sentinel,
        )

    routed = run(
        "conv-brief-routed", "Top GP customers for Port of Singapore in April 2026", "ctk-a"
    )
    assert routed.status == "ok"

    state.put("conv-brief-subject", ConversationSlots(mode="new_prospect"))
    subject = run("conv-brief-subject", "Meridian Global Shipping", "ctk-b")
    assert subject.status == "ok"

    dispatched = [skill_id for skill_id, _ctx in seen]
    assert "data_qa.metric_query" in dispatched
    assert "customer_insight.new_prospect_brief" in dispatched
    assert len(seen) >= 2
    assert [ctx.brief_store for _skill_id, ctx in seen] == [sentinel] * len(seen)
