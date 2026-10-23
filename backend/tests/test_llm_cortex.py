"""CortexProvider: Snowflake Cortex's Anthropic-compatible Messages endpoint
over httpx (spec 9.2, D40, D68, D79). Offline only (owner ruling); the
live proof is docs/snowflake/CORTEX_HANDOFF.md for the Snowflake-side owner,
whose smoke tests at the bottom skip without SNOWFLAKE_ACCOUNT_URL."""

import json
import os
from pathlib import Path

import httpx
import pytest

from poseidon.core.llm import cortex
from poseidon.core.llm.cortex import NO_ACCOUNT_MESSAGE, NO_TOKEN_MESSAGE, CortexProvider
from poseidon.core.llm.messages_api import ProviderCredentialsError

_END_TURN = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-4-5",
             "content": [{"type": "text", "text": "hello"}], "stop_reason": "end_turn",
             "stop_sequence": None, "usage": {"input_tokens": 3, "output_tokens": 2}}
_USER = [{"role": "user", "content": "hi"}]
_ACCOUNT = "https://myorg-myacct.snowflakecomputing.com"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("SNOWFLAKE_ACCOUNT_URL", "SNOWFLAKE_HOST", "SNOWFLAKE_CORTEX_TOKEN",
                "SNOWFLAKE_CORTEX_TOKEN_TYPE"):
        monkeypatch.delenv(key, raising=False)


def _recording(seen):
    def handler(request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_END_TURN)
    return handler


def _provider(handler, **kwargs):
    kwargs.setdefault("token_file", "/nonexistent/token")
    return CortexProvider(client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs)


def test_posts_to_the_cortex_messages_endpoint_with_bearer_and_version():
    seen = {}
    result = _provider(_recording(seen), account_url=_ACCOUNT + "/", token="pat-1",
                       token_type="PROGRAMMATIC_ACCESS_TOKEN").invoke(
        system="s", messages=_USER, tools=[], model="claude-sonnet-4-5", params={"max_tokens": 8})
    assert seen["url"] == _ACCOUNT + "/api/v2/cortex/v1/messages"
    assert seen["headers"]["authorization"] == "Bearer pat-1"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["x-snowflake-authorization-token-type"] == "PROGRAMMATIC_ACCESS_TOKEN"
    assert seen["body"]["model"] == "claude-sonnet-4-5"
    assert result.text == "hello"


def test_token_type_header_is_omitted_when_unknown():
    seen = {}
    _provider(_recording(seen), account_url=_ACCOUNT, token="t").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert "x-snowflake-authorization-token-type" not in seen["headers"]


def test_account_url_and_token_fall_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT_URL", _ACCOUNT)
    monkeypatch.setenv("SNOWFLAKE_CORTEX_TOKEN", "env-token")
    monkeypatch.setenv("SNOWFLAKE_CORTEX_TOKEN_TYPE", "KEYPAIR_JWT")
    seen = {}
    _provider(_recording(seen)).invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert seen["url"].startswith(_ACCOUNT)
    assert seen["headers"]["authorization"] == "Bearer env-token"
    assert seen["headers"]["x-snowflake-authorization-token-type"] == "KEYPAIR_JWT"


def test_snowflake_host_alone_builds_the_account_url(monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_HOST", "myorg-myacct.snowflakecomputing.com")
    seen = {}
    _provider(_recording(seen), token="t").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert seen["url"] == _ACCOUNT + "/api/v2/cortex/v1/messages"


def test_spcs_token_file_is_used_with_oauth_type(tmp_path):
    token_file = tmp_path / "token"
    token_file.write_text("file-token\n", encoding="utf-8")
    seen = {}
    _provider(_recording(seen), account_url=_ACCOUNT, token_file=token_file).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert seen["headers"]["authorization"] == "Bearer file-token"
    assert seen["headers"]["x-snowflake-authorization-token-type"] == "OAUTH"


def test_token_file_is_reread_on_every_invoke(tmp_path):
    """Snowflake rewrites /snowflake/session/token every few minutes and each
    token lives at most an hour, so a cached first read would go stale."""
    token_file = tmp_path / "token"
    token_file.write_text("first", encoding="utf-8")
    tokens = []

    def handler(request):
        tokens.append(request.headers["authorization"])
        return httpx.Response(200, json=_END_TURN)

    provider = _provider(handler, account_url=_ACCOUNT, token_file=token_file)
    provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    token_file.write_text("second", encoding="utf-8")
    provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert tokens == ["Bearer first", "Bearer second"]


def test_explicit_token_wins_over_env_and_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWFLAKE_CORTEX_TOKEN", "env")
    token_file = tmp_path / "token"
    token_file.write_text("file", encoding="utf-8")
    seen = {}
    _provider(_recording(seen), account_url=_ACCOUNT, token="explicit",
              token_file=token_file).invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert seen["headers"]["authorization"] == "Bearer explicit"


def test_missing_account_url_raises_the_pinned_error_at_invoke():
    provider = _provider(_recording({}), token="t")
    with pytest.raises(ProviderCredentialsError) as err:
        provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert str(err.value) == NO_ACCOUNT_MESSAGE


def test_missing_token_raises_the_pinned_error_at_invoke():
    provider = _provider(_recording({}), account_url=_ACCOUNT)
    with pytest.raises(ProviderCredentialsError) as err:
        provider.invoke(system="", messages=_USER, tools=[], model="m", params={})
    assert str(err.value) == NO_TOKEN_MESSAGE


def test_construction_touches_nothing():
    CortexProvider()  # no env, no file, no network


def test_auth_error_text_never_contains_the_credential():
    def handler(request):
        return httpx.Response(403, json={"type": "error", "error": {
            "type": "permission_error", "message": "Not Authorized for token secret-token-9"}})
    result = _provider(handler, account_url=_ACCOUNT, token="secret-token-9").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "cortex error: permission_error"
    assert "secret-token-9" not in result.text


def test_snowflake_plain_text_error_reports_the_status():
    def handler(request):
        return httpx.Response(402, text="budget exceeded")
    result = _provider(handler, account_url=_ACCOUNT, token="t").invoke(
        system="", messages=_USER, tools=[], model="m", params={})
    assert result.text == "cortex error: HTTP 402" and result.stop_reason == "error"


def test_native_forced_tool_choice_when_no_mode_is_set():
    """No tool_choice_mode param means the native form (what a 4.x or Haiku
    5.5 role would use); temperature is forwarded when present."""
    seen = {}
    tool = {"name": "reporting.narrate", "description": "d", "input_schema": {"type": "object"}}
    _provider(_recording(seen), account_url=_ACCOUNT, token="t").invoke(
        system="s", messages=_USER, tools=[tool], model="claude-haiku-5-5",
        params={"max_tokens": 10, "temperature": 0.3}, tool_choice="reporting.narrate")
    assert seen["body"]["tool_choice"] == {"type": "tool", "name": "reporting__narrate"}
    assert seen["body"]["temperature"] == 0.3


def test_instruct_mode_forced_tool_choice_as_the_cortex_sonnet_roles_use_it():
    """The cortex profile is on Sonnet 5.5 (owner ruling 4): forced calls go
    out as auto plus an instruction, thinking between_tools, no temperature."""
    seen = {}
    tool = {"name": "reporting.narrate", "description": "d", "input_schema": {"type": "object"}}
    _provider(_recording(seen), account_url=_ACCOUNT, token="t").invoke(
        system="s", messages=_USER, tools=[tool], model="claude-sonnet-5-5",
        params={"max_tokens": 10, "thinking": {"type": "between_tools"},
                "tool_choice_mode": "instruct"},
        tool_choice="reporting.narrate")
    assert seen["body"]["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    assert seen["body"]["thinking"] == {"type": "between_tools"}
    assert "temperature" not in seen["body"]
    assert "reporting__narrate" in seen["body"]["system"]


def test_cortex_module_is_ascii_on_disk():
    for path in (Path(cortex.__file__), Path(__file__)):
        offending = sorted({b for b in path.read_bytes() if b > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


# ---------------------------------------------------------------------------
# router_live -- for the Snowflake-side owner (docs/snowflake/CORTEX_HANDOFF.md).
# Never run in this repo's gates. Skips without SNOWFLAKE_ACCOUNT_URL.
# ---------------------------------------------------------------------------

_HAS_ACCOUNT = bool(os.environ.get("SNOWFLAKE_ACCOUNT_URL") or os.environ.get("SNOWFLAKE_HOST"))
_NO_ACCOUNT_REASON = "no SNOWFLAKE_ACCOUNT_URL / SNOWFLAKE_HOST in the environment"


def _live_role_client(monkeypatch):
    from poseidon.core.config import Settings
    from poseidon.core.llm.roles import RoleClient

    keep = {"SNOWFLAKE_ACCOUNT_URL", "SNOWFLAKE_HOST", "SNOWFLAKE_CORTEX_TOKEN",
            "SNOWFLAKE_CORTEX_TOKEN_TYPE"}
    for key in (name.upper() for name in Settings.model_fields):
        if key not in keep:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:x@localhost:5432/poseidon")
    monkeypatch.setenv("S3_BUCKET", "poseidon-artifacts")
    monkeypatch.setenv("LLM_PROFILE", "cortex")
    monkeypatch.setenv("LLM_MODE", "live")
    settings = Settings(_env_file=None)
    provider = CortexProvider(account_url=settings.cortex_account_url,
                              token=settings.snowflake_cortex_token,
                              token_type=settings.snowflake_cortex_token_type)
    return RoleClient(settings, providers={"cortex": provider})


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_ACCOUNT, reason=_NO_ACCOUNT_REASON)
def test_cortex_utility_role_live_smoke(monkeypatch):
    result = _live_role_client(monkeypatch).invoke(
        "utility", system="Reply with a short greeting, nothing else.",
        messages=[{"role": "user", "content": "Say hello."}])
    assert result.stop_reason != "error", result.text
    assert result.text.strip() != ""
    assert result.input_tokens > 0 and result.output_tokens > 0


@pytest.mark.router_live
@pytest.mark.skipif(not _HAS_ACCOUNT, reason=_NO_ACCOUNT_REASON)
def test_cortex_forced_narrative_live_smoke(monkeypatch):
    from poseidon.core.llm.prompt_tree import PromptTree
    from poseidon.tasks.reporting.skills.monthly_performance.tools.narrate import (
        NARRATE_TOOL,
        TOOL_NAME,
    )

    prompt = PromptTree().resolve("reporting/narrate")
    block = {"office": "Gibraltar Office", "month": "2026-06", "gp": {"current": 353000,
             "prior": 380000, "delta": -27000, "delta_pct": -7.1}}
    content = ("Computed figures (JSON):\n" + json.dumps(block, indent=1)
               + "\n\nWrite the narrative now.")
    result = _live_role_client(monkeypatch).invoke(
        "synthesis", system=prompt.system, messages=[{"role": "user", "content": content}],
        tools=[NARRATE_TOOL], tool_choice=TOOL_NAME)
    assert result.stop_reason == "tool_use", result.text
    assert [call.name for call in result.tool_calls] == [TOOL_NAME]
