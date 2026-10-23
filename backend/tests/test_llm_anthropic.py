"""AnthropicProvider: the direct Claude Messages API over httpx (spec 9.1,
D68/D79). Everything offline here runs through httpx.MockTransport; the two
router_live tests at the bottom are the one paid proof Carlos runs himself
(owner ruling 1, 2026-10-23) and skip without ANTHROPIC_API_KEY."""

import json
import os
from pathlib import Path

import httpx
import pytest

from poseidon.core.llm import anthropic
from poseidon.core.llm.anthropic import NO_API_KEY_MESSAGE, AnthropicProvider
from poseidon.core.llm.messages_api import ProviderCredentialsError

_END_TURN = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
             "content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn",
             "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 2}}
_USER = [{"role": "user", "content": "hi"}]


def _provider(handler, **kwargs):
    return AnthropicProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_posts_to_the_messages_endpoint_with_the_three_headers():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        return httpx.Response(200, json=_END_TURN)

    result = _provider(handler, api_key="sk-test").invoke(
        system="s", messages=_USER, tools=[], model="claude-haiku-5-5", params={"max_tokens": 8})
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "sk-test"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["content-type"] == "application/json"
    assert result.text == "hello" and result.input_tokens == 3


def test_base_url_override_is_honoured():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_END_TURN)

    _provider(handler, api_key="k", base_url="https://proxy.test/").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert seen["url"] == "https://proxy.test/v1/messages"


def test_api_key_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env")
    seen = {}

    def handler(request):
        seen["key"] = request.headers["x-api-key"]
        return httpx.Response(200, json=_END_TURN)

    _provider(handler).invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert seen["key"] == "sk-env"


def test_missing_key_raises_the_pinned_credentials_error_at_invoke_not_construction(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider()  # constructing never touches credentials
    with pytest.raises(ProviderCredentialsError) as err:
        provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert str(err.value) == NO_API_KEY_MESSAGE


def test_auth_error_text_never_contains_the_credential():
    def handler(request):
        return httpx.Response(401, json={"type": "error", "error": {
            "type": "authentication_error", "message": "invalid x-api-key sk-secret-123"}})

    result = _provider(handler, api_key="sk-secret-123").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.stop_reason == "error"
    assert result.text == "anthropic error: authentication_error"
    assert "sk-secret-123" not in result.text


def test_forced_tool_in_instruct_mode_reaches_the_wire_as_auto_plus_instruction():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_END_TURN)

    tool = {"name": "reporting.narrate", "description": "d",
            "input_schema": {"type": "object", "properties": {}}}
    params = {"max_tokens": 10, "thinking": {"type": "between_tools"},
              "tool_choice_mode": "instruct"}
    _provider(handler, api_key="k").invoke(
        system="sys", messages=_USER, tools=[tool], model="claude-sonnet-5-5",
        params=params, tool_choice="reporting.narrate")
    assert seen["body"]["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert seen["body"]["thinking"] == {"type": "between_tools"}
    assert "temperature" not in seen["body"]
    assert "reporting__narrate" in seen["body"]["system"]


def test_anthropic_module_is_ascii_on_disk():
    for path in (Path(anthropic.__file__), Path(__file__)):
        offending = sorted({b for b in path.read_bytes() if b > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ---------------------------------------------------------------------------
# router_live -- the one paid proof (owner ruling 1). Skips without a key.
# RUN ONLY FILE-SCOPED: `-m router_live tests/test_llm_anthropic.py`. A bare
# `-m router_live` would also run the Bedrock smoke (this shell has AWS
# credentials) and the thirteen routing cases live.
# ---------------------------------------------------------------------------

_HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
_NO_KEY_REASON = "no ANTHROPIC_API_KEY in the environment"


def _live_role_client(monkeypatch):
    from poseidon.core.config import Settings
    from poseidon.core.llm.roles import RoleClient

    for key in (name.upper() for name in Settings.model_fields):
        if key != "ANTHROPIC_API_KEY":
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@localhost:5432/poseidon")
    monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
    monkeypatch.setenv("LLM_PROFILE", "anthropic")
    monkeypatch.setenv("LLM_MODE", "live")
    settings = Settings(_env_file=None)
    provider = AnthropicProvider(api_key=settings.anthropic_api_key)
    return RoleClient(settings, providers={"anthropic": provider})


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_KEY, reason=_NO_KEY_REASON)
def test_anthropic_utility_role_live_smoke(monkeypatch):
    """One Haiku 5.5 call through the utility role. Shape only: non-empty
    text, positive token counts, never exact wording."""
    result = _live_role_client(monkeypatch).invoke(
        "utility", system="Reply with a short greeting, nothing else.",
        messages=[{"role": "user", "content": "Say hello."}])
    assert result.stop_reason != "error", result.text
    assert result.text.strip() != ""
    assert result.input_tokens > 0 and result.output_tokens > 0


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_KEY, reason=_NO_KEY_REASON)
def test_anthropic_forced_narrative_live_smoke(monkeypatch):
    """The owner gate of spec 11 row C: the report narrative's forced tool
    call on Sonnet 5.5, which rejects native forcing, must still come back
    as ONE tool_use for reporting.narrate through instruct mode. Figures are
    a tiny hand-made block; the model's prose is not asserted."""
    from poseidon.core.llm.prompt_tree import PromptTree
    from poseidon.tasks.reporting.skills.monthly_performance.tools.narrate import (
        NARRATE_TOOL,
        TOOL_NAME,
    )

    prompt = PromptTree().resolve("reporting/narrate")
    block = {"office": "Gibraltar Office", "month": "2026-06", "gp": {"current": 353000,
             "prior": 380000, "delta": -27000, "delta_pct": -7.1}}
    content = (
        "Computed figures (JSON):\n" + json.dumps(block, indent=1) + "\n\nWrite the narrative now."
    )
    result = _live_role_client(monkeypatch).invoke(
        "synthesis", system=prompt.system, messages=[{"role": "user", "content": content}],
        tools=[NARRATE_TOOL], tool_choice=TOOL_NAME)
    assert result.stop_reason == "tool_use", result.text
    assert [call.name for call in result.tool_calls] == [TOOL_NAME]
    expected_keys = {"narrative_md", "insights", "suggested_questions"}
    assert set(result.tool_calls[0].arguments) >= expected_keys


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_KEY, reason=_NO_KEY_REASON)
def test_anthropic_tool_loop_round_trip_live_smoke(monkeypatch):
    """Review Focus 3 (D80): the first call's reasoning blocks must be echoed
    back and accepted on the second call. Sonnet 5.5 with between_tools may
    attach thinking to a tool_use turn; this is the only live path that
    exercises the echo. Shape only: a tool call, then a non-error reply."""
    from poseidon.core.llm.loop import _assistant_tool_use_message

    tool = {
        "name": "data_qa.metric_query",
        "description": (
            "Certified metric query over the sales book. Call it for any question "
            "about gross profit, volume or margin; it returns the figures."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metrics": {"type": "array", "items": {"type": "string"}},
                "period": {"type": "string"},
            },
            "required": ["metrics", "period"],
        },
    }
    system = (
        "You answer sales questions by calling the data_qa.metric_query tool first, "
        "then summarising its result in one sentence."
    )
    client = _live_role_client(monkeypatch)
    messages = [{"role": "user", "content": "What was gross profit for 2025?"}]
    first = client.invoke("router", system=system, messages=messages, tools=[tool])
    assert first.stop_reason == "tool_use", first.text
    messages.append(_assistant_tool_use_message(first))
    messages.append({
        "role": "user",
        "content": [{
            "toolResult": {
                "toolUseId": first.tool_calls[0].id,
                "content": [{"text": "Results: 1\nGP 2025: 1,234,567 USD"}],
                "status": "success",
            }
        }],
    })
    second = client.invoke("router", system=system, messages=messages, tools=[tool])
    assert second.stop_reason != "error", second.text
    assert second.text.strip() or second.tool_calls
    print(f"reasoning blocks echoed: {len(first.reasoning)}")
