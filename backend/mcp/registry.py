"""Config-driven resolution for external tool servers (doc 02 section 7).

A skill never imports a vendor SDK or reaches for an HTTP client itself; it
calls a typed interface off ``SkillContext.tools`` (e.g.
``ctx.tools.research.search(...)``) and :class:`ToolServerRegistry` decides,
from ``Settings.tool_transport_perplexity``, which transport actually
answers the call. Decision D23 (doc 02 section 7): the direct REST adapter
is the default transport -- proven and already in-house -- while the
MCP-transport client is the standing pattern for servers where MCP is the
native surface.

Resolution is LAZY: the adapter/client for a transport is built only the
first time :attr:`ToolServerRegistry.research` is read, never inside
``__init__``. An offline boot (no network, no API key) must be able to
construct the registry and hand it to every ``SkillContext``
unconditionally -- only a skill that actually calls ``ctx.tools.research``
should ever need Perplexity credentials or an HTTP client to exist. This
mirrors :class:`poseidon.core.llm.roles.RoleClient`'s provider seam: real
providers are supplied by the caller rather than constructed as a side
effect of wiring up the object graph; here that idea runs one indirection
further, since there is no live provider to inject yet at all (Tasks 2-3
have not shipped) -- even the IMPORT of a transport's module is deferred to
first use.

``overrides`` is that same seam's other half. Passing
``overrides={"research": fake}`` makes ``.research`` return ``fake``
unconditionally: no transport is resolved and no import is attempted, which
is how tests inject a scripted tool and how local dev runs without a
Perplexity key (a fixture-backed tool installed by ``api/app.py``, Task 4).

Task 1 ships this registry and the typed interface only. The transports
themselves (``mcp/perplexity/adapter.py`` Task 2,
``mcp/perplexity/mcp_client.py`` Task 3) do not exist on disk yet, so
``_build_research`` imports them LAZILY -- inside the branch that needs
them, not at module load time -- for two reasons: importing a module that
does not exist would break every caller of this file before Task 2/3 ship,
and a deferred import is the same offline-safety property this module
already promises, one layer further down (the cost of even LOOKING for an
HTTP-client-carrying module is paid on first use, not by every process that
merely imports the registry).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from poseidon.core.config import Settings

# U+2014 EM DASH, built via chr() rather than typed literally so this file
# stays pure ASCII on disk (same convention as poseidon.core.llm.roles).
_EM_DASH = chr(0x2014)


@dataclass(frozen=True)
class ResearchResult:
    """One research call's typed answer -- identical in shape regardless of
    which transport answered it.

    ``raw_digest`` is a short count/transport summary for proof lines (e.g.
    "3 results via direct") and must NEVER carry the actual item payload:
    proof lines are deterministic provenance text a user reads in the
    transcript, not a place for vendor content to leak through.
    """

    items: tuple[dict, ...]
    raw_digest: str
    transport: str
    degraded: bool = False
    degrade_reason: str | None = None


class ResearchTool(Protocol):
    """What every research transport (direct adapter, MCP client, or a
    test's fixture/fake) must answer -- one call shape, written once, the
    same pattern decision D21 uses for
    :class:`poseidon.core.llm.roles.LLMProvider`."""

    def search(
        self, *, query: str, schema_name: str, recency_days: int | None = None
    ) -> ResearchResult: ...


class ToolServerRegistry:
    """Resolves ``SkillContext.tools`` to concrete tool-server clients.

    See the module docstring for the laziness rule and the overrides seam.
    """

    def __init__(self, settings: Settings, overrides: Mapping[str, object] | None = None) -> None:
        self._settings = settings
        self._overrides: Mapping[str, object] = {} if overrides is None else overrides
        self._research: ResearchTool | None = None

    @property
    def research(self) -> ResearchTool:
        """The resolved :class:`ResearchTool`, per
        ``settings.tool_transport_perplexity`` -- unless ``overrides``
        supplies one, which always wins and skips resolution entirely (see
        the module docstring's dev/test seam).

        Cached after the first resolution, so a transport is constructed at
        most once per registry.
        """
        if "research" in self._overrides:
            return cast(ResearchTool, self._overrides["research"])
        if self._research is None:
            self._research = self._build_research()
        return self._research

    def _build_research(self) -> ResearchTool:
        """Import and construct the transport named by
        ``tool_transport_perplexity`` -- the only place this module ever
        imports ``mcp.perplexity`` (see the module docstring for why that
        import is deferred to here instead of module load time).

        The constructor keyword arguments below are provisional: Task 2
        (``PerplexityDirectAdapter``) and Task 3 (``PerplexityMcpClient``)
        own their real signatures, and neither module exists on disk yet,
        so neither call below can execute today -- the ``from ... import``
        line always raises first. What this method pins today is which
        DOTTED PATH each transport resolves to, not what its constructor
        accepts.
        """
        transport = self._settings.tool_transport_perplexity
        if transport == "direct":
            from mcp.perplexity.adapter import PerplexityDirectAdapter

            return PerplexityDirectAdapter(api_key=self._settings.perplexity_api_key)
        if transport == "mcp":
            from mcp.perplexity.mcp_client import PerplexityMcpClient

            return PerplexityMcpClient(api_key=self._settings.perplexity_api_key)
        raise RuntimeError(
            f"unknown research transport {transport!r} {_EM_DASH} expected 'direct' or 'mcp'"
        )
