"""Tests for Phase 7 Task 1 (doc 02 section 7): ToolServerRegistry, the
typed ResearchResult dataclass, and the Settings/SkillContext plumbing a
skill's tool calls flow through.

Non-ASCII characters in expected strings are never typed literally: see
``_EM_DASH`` below, built via ``chr(0x2014)`` the same way
``poseidon.core.llm.roles._EM_DASH`` is -- an em dash, an en dash and a
hyphen are visually indistinguishable in most editors, so a byte-pinned
message that used a typed character could silently pin the wrong codepoint.
``test_mcp_module_files_are_ascii_on_disk`` enforces that for this file and
the ``mcp`` package modules it exercises.

The direct/mcp transport-resolution tests register scripted stand-ins
directly in ``sys.modules`` rather than importing real adapter/client code:
``mcp/perplexity/adapter.py`` (Task 2) and ``mcp/perplexity/mcp_client.py``
(Task 3) do not exist yet. This proves ``_build_research`` targets the
correct transport-specific module today without depending on either task;
Task 2/3 replace the two resolution tests with real-construction tests once
those modules ship for real.
"""

import sys
import tomllib
import types
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import mcp
from mcp.registry import ResearchResult, ToolServerRegistry
from poseidon.core.config import Settings
from poseidon.core.skills.context import ConversationSlots, SkillContext

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk (poseidon.core.llm.roles uses the same trick) --
# see the module docstring.
_EM_DASH = chr(0x2014)

# Present and non-blank is all Settings asks of it; nothing here connects
# (mirrors test_skill_registry.py's PLACEHOLDER_DSN).
_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"


def _settings(**overrides: object) -> Settings:
    """A valid, directly-constructed ``Settings`` (no env/monkeypatch
    needed -- mirrors test_skill_registry.py's ``_ctx`` helper) with any
    field overridden for the cases below."""
    fields: dict[str, object] = {
        "_env_file": None,
        "database_url": _PLACEHOLDER_DSN,
        "s3_bucket": "poseidon-artifacts",
    }
    fields.update(overrides)
    return Settings(**fields)


# ---------------------------------------------------------------------------
# ResearchResult
# ---------------------------------------------------------------------------


def test_research_result_is_a_frozen_dataclass_with_pinned_defaults():
    result = ResearchResult(items=(), raw_digest="0 results via fixture", transport="fixture")

    assert result.degraded is False
    assert result.degrade_reason is None
    with pytest.raises(FrozenInstanceError):
        result.transport = "direct"


# ---------------------------------------------------------------------------
# ToolServerRegistry -- laziness (no construction on init)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("transport", ["direct", "mcp"])
def test_construction_never_imports_a_transport_module(transport):
    """Offline boots build a ToolServerRegistry for every SkillContext
    unconditionally (see the module docstring's laziness rule): __init__
    must not construct -- or even import -- a transport client. Proof: this
    must not raise, even though neither mcp.perplexity.adapter (Task 2) nor
    mcp.perplexity.mcp_client (Task 3) exists on disk yet -- an eager
    resolution would hit that missing module immediately.
    """
    ToolServerRegistry(_settings(tool_transport_perplexity=transport))


# ---------------------------------------------------------------------------
# ToolServerRegistry -- resolution table: unknown / override
# ---------------------------------------------------------------------------


def test_unknown_transport_raises_pinned_runtime_error():
    """Settings.tool_transport_perplexity is a Literal["direct", "mcp"], so
    pydantic rejects a bad value at construction time -- but the registry
    guards independently rather than trusting that upstream check, since
    Settings is not validate-on-assignment (a caller can mutate the
    attribute after construction) and nothing stops a duck-typed
    settings-like object from carrying any string at all. Mutate after a
    valid construction to reach the guard here.
    """
    settings = _settings(tool_transport_perplexity="direct")
    settings.tool_transport_perplexity = "carrier-pigeon"
    registry = ToolServerRegistry(settings)

    with pytest.raises(RuntimeError) as err:
        _ = registry.research  # property access is the side effect under test

    assert str(err.value) == (
        f"unknown research transport 'carrier-pigeon' {_EM_DASH} expected 'direct' or 'mcp'"
    )


def test_override_research_wins_without_resolving_a_transport():
    """overrides is the dev/test seam mirroring RoleClient's provider
    registry (poseidon.core.llm.roles.RoleClient): a caller-supplied
    research tool always wins, so tests and local dev never need a real
    transport."""
    fake = object()
    registry = ToolServerRegistry(
        _settings(tool_transport_perplexity="direct"), overrides={"research": fake}
    )

    assert registry.research is fake


def test_override_wins_even_over_an_unknown_transport():
    """The override bypasses resolution ENTIRELY: it wins even when the
    configured transport would otherwise raise, because resolution never
    runs at all once an override is supplied."""
    fake = object()
    settings = _settings(tool_transport_perplexity="direct")
    settings.tool_transport_perplexity = "carrier-pigeon"
    registry = ToolServerRegistry(settings, overrides={"research": fake})

    assert registry.research is fake


# ---------------------------------------------------------------------------
# ToolServerRegistry -- resolution table: direct / mcp
# ---------------------------------------------------------------------------


def test_direct_transport_resolves_mcp_perplexity_adapter(monkeypatch):
    """_build_research's "direct" branch must target
    mcp.perplexity.adapter.PerplexityDirectAdapter specifically. Task 2 has
    not shipped that module yet, so a scripted stand-in is registered
    directly in sys.modules -- the same lookup table Python's own import
    system consults -- rather than as a real file on disk.
    """
    sentinel = object()
    calls = []

    def fake_adapter(**kwargs: object) -> object:
        calls.append(kwargs)
        return sentinel

    fake_module = types.ModuleType("mcp.perplexity.adapter")
    fake_module.PerplexityDirectAdapter = fake_adapter
    monkeypatch.setitem(sys.modules, "mcp.perplexity", types.ModuleType("mcp.perplexity"))
    monkeypatch.setitem(sys.modules, "mcp.perplexity.adapter", fake_module)

    registry = ToolServerRegistry(_settings(tool_transport_perplexity="direct"))

    assert registry.research is sentinel
    assert registry.research is sentinel  # cached -- resolved once
    assert len(calls) == 1


def test_mcp_transport_resolves_mcp_perplexity_mcp_client(monkeypatch):
    """Same proof as above for the "mcp" branch, which must target
    mcp.perplexity.mcp_client.PerplexityMcpClient specifically -- a
    different dotted path than the "direct" branch, so a copy-paste swap
    between the two branches would be caught by running both tests."""
    sentinel = object()
    calls = []

    def fake_client(**kwargs: object) -> object:
        calls.append(kwargs)
        return sentinel

    fake_module = types.ModuleType("mcp.perplexity.mcp_client")
    fake_module.PerplexityMcpClient = fake_client
    monkeypatch.setitem(sys.modules, "mcp.perplexity", types.ModuleType("mcp.perplexity"))
    monkeypatch.setitem(sys.modules, "mcp.perplexity.mcp_client", fake_module)

    registry = ToolServerRegistry(_settings(tool_transport_perplexity="mcp"))

    assert registry.research is sentinel
    assert len(calls) == 1


def test_direct_transport_import_failure_names_the_missing_module_today():
    """Documents current reality without pinning it forever: today, with
    neither Task 2 nor Task 3 shipped, resolving ANY transport fails at the
    same "mcp.perplexity" package -- neither adapter.py nor mcp_client.py
    exists, so the failure happens one level up, before Python ever looks
    for either file specifically (see the two sys.modules-scripted tests
    above for the per-transport proof). This test is expected to stop
    applying once Task 2 gives "direct" something real to construct.
    """
    registry = ToolServerRegistry(_settings(tool_transport_perplexity="direct"))

    with pytest.raises(ModuleNotFoundError) as err:
        _ = registry.research  # property access is the side effect under test

    assert err.value.name == "mcp.perplexity"


# ---------------------------------------------------------------------------
# SkillContext.tools
# ---------------------------------------------------------------------------


def test_skill_context_tools_defaults_to_none():
    """Additive per Phase 7 Task 1: every one of P3/P4/P6's existing
    SkillContext(...) call sites omits tools entirely and must keep working
    unchanged -- provable only if the field defaults to None."""
    ctx = SkillContext(data=object(), artifacts=None, settings=_settings())

    assert ctx.tools is None


def test_skill_context_tools_accepts_an_explicit_value():
    fake_registry = object()

    ctx = SkillContext(data=object(), artifacts=None, settings=_settings(), tools=fake_registry)

    assert ctx.tools is fake_registry


def test_skill_context_remains_frozen_with_tools_field():
    ctx = SkillContext(data=object(), artifacts=None, settings=_settings())

    with pytest.raises(FrozenInstanceError):
        ctx.tools = object()


def test_skill_context_state_default_is_unaffected_by_the_new_field():
    """tools is appended after state in the dataclass's field order (both
    now carry defaults) -- state's own default must still construct on its
    own, unaffected by the new field sitting after it."""
    ctx = SkillContext(data=object(), artifacts=None, settings=_settings())

    assert ctx.state == ConversationSlots()


# ---------------------------------------------------------------------------
# pyproject wiring
# ---------------------------------------------------------------------------


def test_research_live_marker_is_registered():
    """Task 2's live-Perplexity smoke (test_perplexity_adapter.py) needs
    this marker to exist before it can use it -- read pyproject.toml
    directly rather than reaching into pytest's own marker registry."""
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))

    markers = data["tool"]["pytest"]["ini_options"]["markers"]

    assert "research_live: live Perplexity research tests requiring an API key" in markers


# ---------------------------------------------------------------------------
# ASCII-only source, matching the Phase 5 convention
# ---------------------------------------------------------------------------


def test_mcp_module_files_are_ascii_on_disk():
    """Byte-pinned messages (the unknown-transport RuntimeError) stay
    pinned only if no look-alike codepoint can slip into any of these files
    -- see the module docstring."""
    package_dir = Path(mcp.__file__).parent
    paths = (
        package_dir / "__init__.py",
        Path(mcp.registry.__file__),
        Path(__file__),
    )
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
