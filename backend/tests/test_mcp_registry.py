"""Tests for Phase 7 Task 1 (doc 02 section 7): ToolServerRegistry, the
typed ResearchResult dataclass, and the Settings/SkillContext plumbing a
skill's tool calls flow through.

Non-ASCII characters in expected strings are never typed literally: see
``_EM_DASH`` below, built via ``chr(0x2014)`` the same way
``poseidon.core.llm.roles._EM_DASH`` is -- an em dash, an en dash and a
hyphen are visually indistinguishable in most editors, so a byte-pinned
message that used a typed character could silently pin the wrong codepoint.
``test_mcp_module_files_are_ascii_on_disk`` enforces that for this file and
the ``poseidon.mcp`` package modules it exercises.

The direct/mcp transport-resolution tests register scripted stand-ins
directly in ``sys.modules`` rather than importing real adapter/client code.
This proves ``_build_research`` targets the correct transport-specific
module by dotted path, independent of whatever real implementation (if
any) exists on disk -- which is exactly why they did NOT need replacing
now that Task 2's ``poseidon/mcp/perplexity/adapter.py`` has shipped for
real (a prior draft of this docstring predicted they would be; disclosed
in Task 2's report as a deliberate choice not to touch them, since a
sys.modules-injected fake proves the SAME thing regardless of module
existence, unlike the test named below).
``poseidon/mcp/perplexity/mcp_client.py`` (Task 3) still does not exist.

Task 1's review (Important 1) flagged that ONE proof in this file was NOT
independent of module existence:
``test_direct_transport_import_failure_names_the_missing_module_today``
asserted a real ``ModuleNotFoundError``,
which stopped being true the moment Task 2 shipped a real, importable
``poseidon.mcp.perplexity.adapter`` -- that test is retired (its own
docstring said it would be) and replaced by
``test_registry_construction_imports_nothing_until_research_is_accessed``
below, which uses an import-counting ``sys.meta_path`` finder instead of
relying on the transport module's absence, so it stays meaningful whether
or not ``adapter.py``/``mcp_client.py`` exist.

``poseidon.mcp`` lives inside the ``poseidon`` package by amendment (Task 1
originally shipped a bare top-level ``mcp``, matching doc 02 section 7's
tree verbatim; the controller relocated it to close off a naming collision
with the real PyPI ``mcp`` SDK -- see ``task-1-report.md``'s amendment
round). Every reference below uses the current, post-relocation path.
"""

import sys
import tomllib
import types
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import poseidon.mcp.registry as mcp_registry
from poseidon.core.config import Settings
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.mcp.registry import ResearchResult, ToolServerRegistry

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
    must not raise -- true for "mcp" because poseidon.mcp.perplexity.
    mcp_client (Task 3) does not exist on disk yet (an eager resolution
    would hit that missing module immediately), and true for "direct"
    regardless of Task 2 having since shipped a real, importable
    poseidon.mcp.perplexity.adapter, since construction must stay lazy
    either way. This test alone can no longer DISTINGUISH "correctly lazy"
    from "eagerly imports" for "direct" now that the module exists -- see
    test_registry_construction_imports_nothing_until_research_is_accessed
    below for the module-existence-independent version of that proof
    (Task 1 review, Important 1).
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
    poseidon.mcp.perplexity.adapter.PerplexityDirectAdapter specifically.
    Task 2 has not shipped that module yet, so a scripted stand-in is
    registered directly in sys.modules -- the same lookup table Python's
    own import system consults -- rather than as a real file on disk.
    """
    sentinel = object()
    calls = []

    def fake_adapter(**kwargs: object) -> object:
        calls.append(kwargs)
        return sentinel

    fake_module = types.ModuleType("poseidon.mcp.perplexity.adapter")
    fake_module.PerplexityDirectAdapter = fake_adapter
    monkeypatch.setitem(
        sys.modules, "poseidon.mcp.perplexity", types.ModuleType("poseidon.mcp.perplexity")
    )
    monkeypatch.setitem(sys.modules, "poseidon.mcp.perplexity.adapter", fake_module)

    registry = ToolServerRegistry(_settings(tool_transport_perplexity="direct"))

    assert registry.research is sentinel
    assert registry.research is sentinel  # cached -- resolved once
    assert len(calls) == 1


def test_mcp_transport_resolves_mcp_perplexity_mcp_client(monkeypatch):
    """Same proof as above for the "mcp" branch, which must target
    poseidon.mcp.perplexity.mcp_client.PerplexityMcpClient specifically --
    a different dotted path than the "direct" branch, so a copy-paste swap
    between the two branches would be caught by running both tests."""
    sentinel = object()
    calls = []

    def fake_client(**kwargs: object) -> object:
        calls.append(kwargs)
        return sentinel

    fake_module = types.ModuleType("poseidon.mcp.perplexity.mcp_client")
    fake_module.PerplexityMcpClient = fake_client
    monkeypatch.setitem(
        sys.modules, "poseidon.mcp.perplexity", types.ModuleType("poseidon.mcp.perplexity")
    )
    monkeypatch.setitem(sys.modules, "poseidon.mcp.perplexity.mcp_client", fake_module)

    registry = ToolServerRegistry(_settings(tool_transport_perplexity="mcp"))

    assert registry.research is sentinel
    assert len(calls) == 1


class _ImportCountingFinder:
    """A ``sys.meta_path`` finder that counts ``find_spec`` calls for
    ``poseidon.mcp.perplexity`` (bare or any dotted child) without ever
    handling them itself -- it always returns ``None``, deferring to
    whichever real finder is already on ``sys.meta_path`` (``PathFinder``
    et al.), so importing still succeeds exactly as it would without this
    finder installed. Installed at index 0 for a single test's duration --
    see the test below that uses it -- so it observes every import ATTEMPT
    for the prefix before the normal machinery resolves it -- this is what
    makes the laziness proof independent of whether
    ``poseidon.mcp.perplexity.adapter`` actually exists on disk: it counts
    attempts, not failures.
    """

    def __init__(self) -> None:
        self.count = 0

    def find_spec(self, fullname, path, target=None):
        if fullname == "poseidon.mcp.perplexity" or fullname.startswith("poseidon.mcp.perplexity."):
            self.count += 1
        return None


def test_registry_construction_imports_nothing_until_research_is_accessed(monkeypatch):
    """CARRIED from Task 1's review (Important 1): the retired
    ``ModuleNotFoundError``-based proof (see the module docstring) held
    only while ``poseidon.mcp.perplexity`` did not exist on disk. This
    replaces it with a guard that holds regardless -- an import-counting
    ``sys.meta_path`` finder proves ``ToolServerRegistry.__init__`` imports
    NOTHING under ``poseidon.mcp.perplexity``, and that the first
    ``.research`` property access is what triggers the first import.

    ``sys.modules`` is force-cleared for the relevant entries first: pytest
    collects (imports) every test module up front, so
    ``test_perplexity_adapter.py``'s own top-level
    ``from poseidon.mcp.perplexity import adapter`` has already populated
    ``sys.modules`` by the time this test body runs, regardless of file
    execution order -- without clearing it, Python would satisfy the
    import from the module cache before ever consulting ``sys.meta_path``,
    and this test would prove nothing. ``monkeypatch.delitem`` restores
    whatever was there afterward, so no other test sees a different
    module object because of this one.
    """
    for name in (
        "poseidon.mcp.perplexity",
        "poseidon.mcp.perplexity.adapter",
        "poseidon.mcp.perplexity.mcp_client",
    ):
        monkeypatch.delitem(sys.modules, name, raising=False)

    finder = _ImportCountingFinder()
    sys.meta_path.insert(0, finder)
    try:
        registry = ToolServerRegistry(_settings(tool_transport_perplexity="direct"))
        assert finder.count == 0  # __init__ imports nothing

        _ = registry.research  # property access is the side effect under test

        assert finder.count >= 1  # the access above triggered (at least) one import
    finally:
        sys.meta_path.remove(finder)


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
    package_dir = Path(mcp_registry.__file__).parent
    paths = (
        package_dir / "__init__.py",
        Path(mcp_registry.__file__),
        Path(__file__),
    )
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
