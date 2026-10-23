"""Tests for Phase 5 Task 1 (doc 03 sections 1-2): the normalized LLM
types, config-driven model profiles, and RoleClient's resolve/invoke split
-- the stub/live seam every later Phase 5 task and the agent loop build on.

Non-ASCII characters in expected strings are written as explicit ``\\uXXXX``
escapes (see ``_EM_DASH``), the same convention the Phase 4 parsing suites
use: an em dash, an en dash and a hyphen are visually indistinguishable in
most editors, so a byte-pinned message that used a typed character could
silently pin the wrong codepoint. ``test_llm_module_files_are_ascii_on_disk``
enforces that for this file and the four ``llm`` package modules.
"""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from poseidon.core.llm import roles, stub, types
from poseidon.core.llm.roles import (
    DEFAULT_MODELS_PATH,
    ModelProfileError,
    RoleClient,
    RoleConfig,
    load_model_profiles,
)
from poseidon.core.llm.stub import StubProvider
from poseidon.core.llm.types import LLMResponse, ToolCall

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}

# U+2014 EM DASH, written as an escape for the reason given in the module
# docstring.
_EM_DASH = "\u2014"


def _settings(monkeypatch, **overrides):
    """A hermetic ``Settings`` instance -- mirrors ``test_config.py``'s
    ``make_settings``: every Settings-derived env var is cleared first so a
    real ``.env`` or ambient shell variable can never leak into a
    role-resolution test.

    ``LLM_MODEL_ROUTER``/``LLM_PROVIDER_ROUTER`` are cleared too even though
    they are not Settings fields (RoleClient.resolve reads them straight
    from ``os.environ``, by design -- see roles.py) -- the only two dynamic
    per-role names this file exercises.
    """
    from poseidon.core.config import Settings

    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LLM_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER_ROUTER", raising=False)

    env = {**REQUIRED_ENV, **overrides}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


# ---------------------------------------------------------------------------
# types.py -- frozen normalized shapes
# ---------------------------------------------------------------------------


def test_tool_call_is_frozen():
    call = ToolCall(id="t1", name="data_qa.metric_query", arguments={"metric": "GP"})
    with pytest.raises(FrozenInstanceError):
        call.name = "other"


def test_llm_response_is_frozen():
    response = LLMResponse(
        text="hi", tool_calls=(), stop_reason="end_turn", input_tokens=1, output_tokens=1
    )
    with pytest.raises(FrozenInstanceError):
        response.text = "bye"


def test_role_config_is_frozen():
    config = RoleConfig(provider="bedrock", model="m", params={})
    with pytest.raises(FrozenInstanceError):
        config.provider = "cortex"


# ---------------------------------------------------------------------------
# load_model_profiles -- both profiles, six roles each, exact model strings
# (doc 03 section 2, transcribed exactly)
# ---------------------------------------------------------------------------

_ANTHROPIC_PROFILE = {
    "router": RoleConfig("anthropic", "claude-sonnet-5-5",
                         {"max_tokens": 4096, "thinking": {"type": "between_tools"},
                          "tool_choice_mode": "instruct"}),
    "synthesis": RoleConfig("anthropic", "claude-sonnet-5-5",
                            {"max_tokens": 8192, "thinking": {"type": "between_tools"},
                             "tool_choice_mode": "instruct"}),
    "supervisor": RoleConfig("anthropic", "claude-opus-5-5", {"enabled": False}),
    "memory": RoleConfig("anthropic", "claude-sonnet-5-5",
                         {"max_tokens": 2048, "thinking": {"type": "between_tools"},
                          "tool_choice_mode": "instruct"}),
    "utility": RoleConfig("anthropic", "claude-haiku-5-5",
                          {"max_tokens": 512, "thinking": {"type": "disabled"}}),
    "micro": RoleConfig("anthropic", "claude-haiku-5-5",
                        {"max_tokens": 256, "thinking": {"type": "disabled"}}),
}
# Owner ruling 4 (2026-10-23): cortex is the same six rows, provider key aside.
_CORTEX_PROFILE = {
    role: replace(cfg, provider="cortex") for role, cfg in _ANTHROPIC_PROFILE.items()
}

_EXPECTED_PROFILES: dict[str, dict[str, RoleConfig]] = {
    "bedrock": {
        "router": RoleConfig(
            "bedrock",
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            {"max_tokens": 4096, "temperature": 0.2},
        ),
        "synthesis": RoleConfig(
            "bedrock",
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            {"max_tokens": 8192, "temperature": 0.3},
        ),
        "supervisor": RoleConfig(
            "bedrock", "us.anthropic.claude-opus-4-1-20250805-v1:0", {"enabled": False}
        ),
        "memory": RoleConfig(
            "bedrock",
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
            {"max_tokens": 2048, "temperature": 0.1},
        ),
        "utility": RoleConfig(
            "bedrock", "us.amazon.nova-lite-v1:0", {"max_tokens": 512, "temperature": 0.0}
        ),
        "micro": RoleConfig(
            "bedrock", "us.amazon.nova-micro-v1:0", {"max_tokens": 256, "temperature": 0.0}
        ),
    },
    "cortex": _CORTEX_PROFILE,
    "anthropic": _ANTHROPIC_PROFILE,
}

_BEDROCK_ROUTER = _EXPECTED_PROFILES["bedrock"]["router"]


def test_default_models_path_exists_under_poseidon_config():
    """Ships inside the ``poseidon`` package (a sibling of ``core/``), not a
    repo-root path -- doc 03 names repo-root ``config/models.yml``; this
    deviates deliberately (phase-5 plan's Global Constraints) so the file
    is packaged with every image and needs no extra container mount."""
    assert DEFAULT_MODELS_PATH.is_file()
    assert DEFAULT_MODELS_PATH.parts[-3:] == ("poseidon", "config", "models.yml")


def test_load_model_profiles_both_profiles_six_roles_exact():
    profiles = load_model_profiles(DEFAULT_MODELS_PATH)

    assert set(profiles) == {"bedrock", "cortex", "anthropic"}
    for profile_name, expected_roles in _EXPECTED_PROFILES.items():
        assert set(profiles[profile_name]) == set(expected_roles)
        for role_name, expected_config in expected_roles.items():
            assert profiles[profile_name][role_name] == expected_config


def test_load_model_profiles_wraps_invalid_yaml(tmp_path):
    """A syntax error in ``models.yml`` surfaces as the framework's own
    :class:`ModelProfileError` naming the file -- not as a raw
    ``yaml.YAMLError`` escaping with a stream position instead of the
    offending path (matches ``skills/registry.py``'s manifest wrapping)."""
    bad = tmp_path / "models.yml"
    bad.write_text("profiles: [unterminated", encoding="utf-8")

    with pytest.raises(ModelProfileError) as err:
        load_model_profiles(bad)

    assert str(bad) in str(err.value)
    assert "not valid YAML" in str(err.value)


# ---------------------------------------------------------------------------
# Settings -- the five fields RoleClient depends on (two pre-existing:
# llm_mode/llm_profile; three added by this task)
# ---------------------------------------------------------------------------


def test_settings_defaults_for_llm_layer(monkeypatch):
    """Every Settings field RoleClient depends on is defaulted, so a bare
    local environment (no LLM_*/MODELS_PATH/etc. set at all) never crashes
    at startup -- only the pre-existing required fields (DATABASE_URL,
    S3_BUCKET) do."""
    settings = _settings(monkeypatch)

    assert settings.llm_mode == "stub"
    assert settings.llm_profile == "bedrock"
    assert settings.models_path is None
    assert settings.prompts_dir is None
    assert settings.agent_max_iterations == 10


def test_role_client_uses_custom_models_path_when_set(tmp_path, monkeypatch):
    """``models_path``, once set, is what RoleClient actually reads -- not
    just the packaged default (doc 03 section 2 lets a deploy override the
    file; this is the only place that override is exercised)."""
    custom = tmp_path / "custom_models.yml"
    custom.write_text(
        "profiles:\n"
        "  bedrock:\n"
        "    router: { provider: bedrock, model: custom-router-model, max_tokens: 1 }\n",
        encoding="utf-8",
    )
    settings = _settings(
        monkeypatch, LLM_PROFILE="bedrock", LLM_MODE="stub", MODELS_PATH=str(custom)
    )
    client = RoleClient(settings)

    config = client.resolve("router")

    assert config.model == "custom-router-model"
    assert config.params == {"max_tokens": 1}


# ---------------------------------------------------------------------------
# RoleClient.resolve -- profile selection, env overrides, unknown role
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_override", "provider_override", "expected_model", "expected_provider"),
    [
        pytest.param(None, None, _BEDROCK_ROUTER.model, "bedrock", id="unset"),
        pytest.param("custom-model-x", None, "custom-model-x", "bedrock", id="model_only"),
        pytest.param(None, "cortex", _BEDROCK_ROUTER.model, "cortex", id="provider_only"),
        pytest.param("custom-model-x", "cortex", "custom-model-x", "cortex", id="both"),
    ],
)
def test_resolve_env_override_table(
    monkeypatch, model_override, provider_override, expected_model, expected_provider
):
    overrides = {}
    if model_override is not None:
        overrides["LLM_MODEL_ROUTER"] = model_override
    if provider_override is not None:
        overrides["LLM_PROVIDER_ROUTER"] = provider_override
    settings = _settings(monkeypatch, LLM_PROFILE="bedrock", LLM_MODE="stub", **overrides)
    client = RoleClient(settings)

    config = client.resolve("router")

    assert config.model == expected_model
    assert config.provider == expected_provider
    assert config.params == _BEDROCK_ROUTER.params  # env overrides never touch params


def test_resolve_env_override_is_scoped_to_its_own_role(monkeypatch):
    """``LLM_MODEL_ROUTER`` overrides only the ``router`` role -- a sibling
    role with no matching env var resolves straight from the profile."""
    settings = _settings(
        monkeypatch, LLM_PROFILE="bedrock", LLM_MODE="stub", LLM_MODEL_ROUTER="custom-model-x"
    )
    client = RoleClient(settings)

    config = client.resolve("synthesis")

    assert config == _EXPECTED_PROFILES["bedrock"]["synthesis"]


def test_resolve_unknown_role_raises_pinned_key_error(monkeypatch):
    settings = _settings(monkeypatch, LLM_PROFILE="bedrock", LLM_MODE="stub")
    client = RoleClient(settings)

    with pytest.raises(KeyError) as err:
        client.resolve("nonexistent")

    expected = (
        f"unknown LLM role 'nonexistent' {_EM_DASH} roles: "
        "['memory', 'micro', 'router', 'supervisor', 'synthesis', 'utility']"
    )
    assert err.value.args[0] == expected


# ---------------------------------------------------------------------------
# RoleClient.invoke -- the stub/live seam (doc 03 section 1; doc 08)
# ---------------------------------------------------------------------------

_SCRIPTED = LLMResponse(
    text="the answer",
    tool_calls=(ToolCall(id="1", name="data_qa.metric_query", arguments={"metric": "GP"}),),
    stop_reason="tool_use",
    input_tokens=10,
    output_tokens=5,
)


def test_invoke_stub_mode_uses_stub_provider_and_records_request(monkeypatch):
    """Stub mode substitutes the stub provider regardless of the CONFIGURED
    provider -- here the profile is cortex, which is unavailable live (see
    the paired test below), and invoke() still succeeds because LLM_MODE is
    checked before the provider ever matters."""
    settings = _settings(monkeypatch, LLM_PROFILE="cortex", LLM_MODE="stub")
    stub_provider = StubProvider([_SCRIPTED])
    client = RoleClient(settings, providers={"stub": stub_provider})

    result = client.invoke(
        "router", system="sys prompt", messages=[{"role": "user", "content": "hi"}]
    )

    assert result == _SCRIPTED
    assert stub_provider.calls == [
        {
            "system": "sys prompt",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [],
            "model": "claude-sonnet-5-5",  # the CONFIGURED cortex router model
            "params": {
                "max_tokens": 4096,
                "thinking": {"type": "between_tools"},
                "tool_choice_mode": "instruct",
            },
            "tool_choice": None,
        }
    ]
    # resolve() still reports the configured provider: mode substituted only
    # the ANSWERING provider, not what config says (see module docstring).
    assert client.resolve("router").provider == "cortex"


def test_cortex_profile_mirrors_anthropic_except_the_provider_key():
    profiles = load_model_profiles(DEFAULT_MODELS_PATH)
    assert {
        r: replace(c, provider="anthropic") for r, c in profiles["cortex"].items()
    } == profiles["anthropic"]


def test_anthropic_profile_parses_with_no_temperature_anywhere():
    """The 5.5 generation returns 400 on any non-default temperature and on
    native forced tool_choice; the profile, not the provider, carries that
    knowledge (owner ruling 4, 2026-10-23)."""
    profiles = load_model_profiles(DEFAULT_MODELS_PATH)
    assert profiles["anthropic"] == _ANTHROPIC_PROFILE
    assert all("temperature" not in cfg.params for cfg in profiles["anthropic"].values())


def test_invoke_live_mode_cortex_dispatches_to_registered_provider(monkeypatch):
    """D68 replaces the D33 refusal: cortex is an ordinary live provider key."""
    settings = _settings(monkeypatch, LLM_PROFILE="cortex", LLM_MODE="live")
    cortex_provider = StubProvider([_SCRIPTED])
    client = RoleClient(settings, providers={"cortex": cortex_provider})
    assert client.invoke("router", system="s", messages=[]) == _SCRIPTED
    assert cortex_provider.calls[0]["model"] == "claude-sonnet-5-5"


def test_invoke_live_mode_cortex_unregistered_names_the_missing_key(monkeypatch):
    settings = _settings(monkeypatch, LLM_PROFILE="cortex", LLM_MODE="live")
    client = RoleClient(settings, providers={})
    with pytest.raises(RuntimeError) as err:
        client.invoke("router", system="s", messages=[])
    assert str(err.value) == (
        f"no provider registered for key 'cortex' (llm_mode='live') {_EM_DASH} registered: []"
    )


def test_invoke_live_mode_anthropic_dispatches_with_profile_params(monkeypatch):
    settings = _settings(
        monkeypatch, LLM_PROFILE="anthropic", LLM_MODE="live", ANTHROPIC_API_KEY="sk-test"
    )
    provider = StubProvider([_SCRIPTED])
    client = RoleClient(settings, providers={"anthropic": provider})
    tools = [{"name": "t", "description": "d", "input_schema": {}}]
    client.invoke("synthesis", system="s", messages=[], tools=tools, tool_choice="t")
    assert provider.calls[0]["model"] == "claude-sonnet-5-5"
    assert provider.calls[0]["params"]["tool_choice_mode"] == "instruct"
    assert provider.calls[0]["tool_choice"] == "t"


def test_invoke_live_mode_bedrock_dispatches_to_registered_provider(monkeypatch):
    """Live mode dispatches to whatever provider is registered under the
    resolved name; ``StubProvider`` stands in as a recording double."""
    settings = _settings(monkeypatch, LLM_PROFILE="bedrock", LLM_MODE="live")
    bedrock_provider = StubProvider([_SCRIPTED])
    client = RoleClient(settings, providers={"bedrock": bedrock_provider})

    result = client.invoke("micro", system="s", messages=[])

    assert result == _SCRIPTED
    assert bedrock_provider.calls == [
        {
            "system": "s",
            "messages": [],
            "tools": [],
            "model": "us.amazon.nova-micro-v1:0",
            "params": {"max_tokens": 256, "temperature": 0.0},
            "tool_choice": None,
        }
    ]


def test_invoke_unregistered_live_provider_raises_pinned_runtime_error(monkeypatch):
    """Task 1 review carry, hardened in Task 4 (the agent loop is the first
    real caller): a mode/role combination whose provider key was never
    registered used to escape as a bare ``KeyError('bedrock')`` -- a message
    that names the missing key and nothing else. The pinned RuntimeError
    names all three things an operator needs to fix it: which key was
    wanted, which mode asked for it, and what was actually registered."""
    settings = _settings(monkeypatch, LLM_PROFILE="bedrock", LLM_MODE="live")
    client = RoleClient(settings, providers={})

    with pytest.raises(RuntimeError) as err:
        client.invoke("router", system="s", messages=[])

    assert str(err.value) == (
        f"no provider registered for key 'bedrock' (llm_mode='live') {_EM_DASH} registered: []"
    )


def test_invoke_unregistered_stub_provider_raises_pinned_runtime_error(monkeypatch):
    """The same guard from the other side of the seam: in stub mode the key
    is always ``"stub"`` no matter what the role is configured for, so a
    registry holding only a live provider is just as misconfigured -- and
    the message says so by listing what IS registered."""
    settings = _settings(monkeypatch, LLM_PROFILE="bedrock", LLM_MODE="stub")
    client = RoleClient(settings, providers={"bedrock": StubProvider([_SCRIPTED])})

    with pytest.raises(RuntimeError) as err:
        client.invoke("router", system="s", messages=[])

    assert str(err.value) == (
        f"no provider registered for key 'stub' (llm_mode='stub') {_EM_DASH} "
        "registered: ['bedrock']"
    )


def test_invoke_passes_provider_a_copy_of_the_caller_tools_list(monkeypatch):
    """Task 1 review carry, hardened here (Task 3's sanctioned one-line
    addition, `tools = list(tools)`): protects tools=[]'s shared mutable
    default from a provider that mutates the list it receives -- pinned by
    asserting the provider got a different list object, not just that the
    caller's own list happens to be unchanged (which a same-object pass-
    through would also satisfy for a non-mutating double like StubProvider)."""
    settings = _settings(monkeypatch, LLM_PROFILE="bedrock", LLM_MODE="stub")
    stub_provider = StubProvider([_SCRIPTED])
    client = RoleClient(settings, providers={"stub": stub_provider})
    tools = [{"name": "t"}]

    client.invoke("router", system="s", messages=[], tools=tools)

    assert stub_provider.calls[0]["tools"] == tools
    assert stub_provider.calls[0]["tools"] is not tools


# ---------------------------------------------------------------------------
# StubProvider -- scripted replay, request recording, exhaustion
# ---------------------------------------------------------------------------


def test_stub_provider_pops_script_in_order():
    first = LLMResponse(
        text="one", tool_calls=(), stop_reason="end_turn", input_tokens=1, output_tokens=1
    )
    second = LLMResponse(
        text="two", tool_calls=(), stop_reason="end_turn", input_tokens=2, output_tokens=2
    )
    provider = StubProvider([first, second])

    result1 = provider.invoke(system="s", messages=[], tools=[], model="m", params={})
    result2 = provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert result1 is first
    assert result2 is second


def test_stub_provider_records_every_call_kwargs():
    provider = StubProvider([_SCRIPTED])

    provider.invoke(
        system="sys",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "t"}],
        model="m1",
        params={"temperature": 0.1},
    )

    assert provider.calls == [
        {
            "system": "sys",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"name": "t"}],
            "model": "m1",
            "params": {"temperature": 0.1},
            "tool_choice": None,
        }
    ]


def test_stub_provider_raises_on_exhaustion_and_still_records():
    provider = StubProvider([_SCRIPTED])
    provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    with pytest.raises(AssertionError) as err:
        provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert str(err.value) == "stub script exhausted"
    assert len(provider.calls) == 2  # the exhausting call is still recorded


def test_stub_provider_script_argument_not_mutated():
    """The caller's own sequence is copied at construction, so it can be
    reused to build a second StubProvider for a second run of the same
    scenario."""
    script = [_SCRIPTED]
    provider = StubProvider(script)

    provider.invoke(system="s", messages=[], tools=[], model="m", params={})

    assert script == [_SCRIPTED]


# ---------------------------------------------------------------------------
# ASCII-only source, matching the Phase 4 parsing convention
# ---------------------------------------------------------------------------


def test_llm_module_files_are_ascii_on_disk():
    """Byte-pinned messages (the unknown-role error, the missing-provider RuntimeError)
    stay pinned only if no look-alike codepoint can slip into any of these
    files -- see the module docstring."""
    package_dir = Path(roles.__file__).parent
    paths = (
        package_dir / "__init__.py",
        Path(types.__file__),
        Path(roles.__file__),
        Path(stub.__file__),
        Path(__file__),
    )
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"


def test_tool_choice_is_forwarded_only_when_set(monkeypatch):
    seen = []

    class _Provider:
        def invoke(self, *, system, messages, tools, model, params, tool_choice=None):
            seen.append(tool_choice)
            return _SCRIPTED

    class _LegacyProvider:  # the six existing doubles have no tool_choice parameter
        def invoke(self, *, system, messages, tools, model, params):
            seen.append("legacy")
            return _SCRIPTED

    settings = _settings(monkeypatch, LLM_PROFILE="bedrock", LLM_MODE="stub")
    client = RoleClient(settings, providers={"stub": _Provider()})
    client.invoke(
        "router",
        system="s",
        messages=[],
        tools=[{"name": "t", "description": "d", "input_schema": {}}],
        tool_choice="t",
    )
    client.invoke("router", system="s", messages=[], tools=[])
    legacy = RoleClient(settings, providers={"stub": _LegacyProvider()})
    legacy.invoke("router", system="s", messages=[], tools=[])
    assert seen == ["t", None, "legacy"]


def test_stub_records_tool_choice():
    provider = StubProvider([_SCRIPTED])
    provider.invoke(system="s", messages=[], tools=[], model="m", params={}, tool_choice="t")
    assert provider.calls[0]["tool_choice"] == "t"
    assert {"system", "messages", "tools", "tool_choice"} <= set(provider.calls[0])
