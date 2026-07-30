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
further -- even now that Task 2 has shipped a real provider a caller COULD
inject (``PerplexityDirectAdapter``, via ``overrides``), the IMPORT of a
transport's module stays deferred to first use regardless. Laziness here
was never contingent on whether a real implementation existed (it did not,
for either transport, when this was first written); it is that a skill
which never calls ``ctx.tools.research`` must never pay for the import
either, which stays true forever, not just until Task 2/3 shipped.

``overrides`` is that same seam's other half. Passing
``overrides={"research": fake}`` makes ``.research`` return ``fake``
unconditionally: no transport is resolved and no import is attempted, which
is how tests inject a scripted tool and how local dev runs without a
Perplexity key (a fixture-backed tool installed by ``api/app.py``, Task 4).

Task 1 shipped this registry and the typed interface. Task 2 shipped the
"direct" transport for real (``poseidon/mcp/perplexity/adapter.py``); Task
3 has since shipped the "mcp" transport for real too
(``poseidon/mcp/perplexity/mcp_client.py``) -- though "real" there means a
client whose request-shaping and response-normalization are fully live;
the JSON-RPC wire it sends through is not (see ``_build_research``'s "mcp"
branch below and ``mcp_client.py``'s own module docstring for that
boundary, stated honestly rather than papered over). ``_build_research``
imports each transport LAZILY regardless -- inside the branch that needs
it, not at module load time -- for a reason that keeps mattering now that
both target modules exist just as it did before either did: a deferred
import is the same offline-safety property this module already promises,
one layer further down (the cost of even LOOKING for an HTTP-client-
carrying module is paid on first use, not by every process that merely
imports the registry), which is why the import stays inside the branch
rather than moving to module load time now that both transports have
something real to import. ``test_mcp_registry.py``'s
``test_registry_construction_imports_nothing_until_research_is_accessed``
guards this with an import-counting ``sys.meta_path`` finder rather than
relying on either module's absence, precisely so the proof survived both
transitions (Task 2's and Task 3's) without needing to change.

This package lives at ``poseidon.mcp`` -- inside the ``poseidon`` package,
not beside it -- specifically so its top-level name is ``poseidon``, never
bare ``mcp``; see ``__init__.py``'s docstring for the naming history.
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

    ``summary`` (Task 4, additive, sanctioned -- plan amendment 9a5ca1b) is
    the model's own short overall synthesis of ``items``, straight from the
    schema's optional top-level ``summary`` key (see ``schemas/web_
    research.json``): a real research answer's model-written prose, never
    hand-composed here. Defaulted to ``""`` so this stays frozen-compatible
    with every ``ResearchResult(...)`` construction Tasks 1-3 already wrote
    (a degrade path never has a summary to report, and simply leaves this at
    its default). Both transports thread it through their own shared
    ``poseidon.mcp.perplexity.adapter.validate_and_normalize`` call --
    proven identical between them by ``test_perplexity_mcp_client.py``'s own
    transport-flip contract test, which now diffs every field BY NAME
    (``dataclasses.asdict``) rather than a hand-picked subset, so a future
    field added here is covered by that proof automatically.
    """

    items: tuple[dict, ...]
    raw_digest: str
    transport: str
    degraded: bool = False
    degrade_reason: str | None = None
    summary: str = ""


class ResearchTool(Protocol):
    """What every research transport (direct adapter, MCP client, or a
    test's fixture/fake) must answer -- one call shape, written once, the
    same pattern decision D21 uses for
    :class:`poseidon.core.llm.roles.LLMProvider`."""

    def search(
        self, *, query: str, schema_name: str, recency_days: int | None = None
    ) -> ResearchResult: ...


def _mcp_wire_not_configured(method: str, params: dict) -> dict:
    """Placeholder ``wire`` the "mcp" branch of :meth:`ToolServerRegistry
    ._build_research` constructs its :class:`~poseidon.mcp.perplexity
    .mcp_client.PerplexityMcpClient` with, until a real stdio/websocket
    JSON-RPC wire exists -- deploy-phase work per ``mcp_client.py``'s own
    module docstring; no real MCP server exists for this codebase to
    speak to yet.

    Raises unconditionally the moment anything actually calls it.
    ``PerplexityMcpClient.search`` catches whatever its wire raises and
    degrades with reason "mcp wire error" rather than propagating (see
    that module's "DEGRADE RULES" docstring paragraph), so this never
    surfaces as a raw traceback to a skill mid-turn -- it surfaces as the
    same honest, structured "unavailable" answer any other wire failure
    would. A caller that actually needs "mcp" to answer for real today
    must inject a working ``ResearchTool`` via
    ``overrides={"research": ...}`` instead of relying on resolution.
    """
    raise RuntimeError(
        f"mcp transport has no real JSON-RPC wire configured yet {_EM_DASH} "
        "inject a working ResearchTool via overrides={'research': ...} "
        "until a real wire ships"
    )


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
        imports ``poseidon.mcp.perplexity`` (see the module docstring for
        why that import is deferred to here instead of module load time).

        The "direct" branch's keyword argument is no longer provisional:
        Task 2 shipped ``PerplexityDirectAdapter``'s real signature
        (``api_key``, ``model="sonar"``, ``timeout_s=30.0``,
        ``client=None``) in ``poseidon/mcp/perplexity/adapter.py``, and
        ``api_key=...`` below already matched it exactly -- checked
        against the brief's pinned signature during Task 2, no
        disagreement found, so no call-site change was needed; only this
        docstring's "provisional" framing was stale, corrected here.
        ``model``/``timeout_s``/``client`` are left at their constructor
        defaults since ``Settings`` has no per-transport override for them
        today -- a future task that wants one adds the ``Settings`` field
        and threads it through here, not a reason to pass anything today.

        The "mcp" branch's keyword argument is ALSO no longer provisional,
        but for a different reason than "direct"'s: Task 3 shipped
        ``PerplexityMcpClient(wire, schema_dir=None)`` in
        ``poseidon/mcp/perplexity/mcp_client.py``, and the kwarg pinned
        here previously (``api_key=...``) did NOT match that real
        signature at all -- ``PerplexityMcpClient`` has no ``api_key``
        parameter; a real wire is what would carry credentials, not the
        client itself. Fixed here (sanctioned, disclosed in Task 3's
        report) to ``wire=_mcp_wire_not_configured`` -- a placeholder that
        raises unconditionally the moment anything actually calls it,
        since no real stdio/websocket JSON-RPC wire exists anywhere in
        this codebase yet (deploy-phase work; see ``mcp_client.py``'s own
        module docstring). Construction itself still SUCCEEDS (matching
        every other transport's lazy-but-working resolution) because
        :meth:`~poseidon.mcp.perplexity.mcp_client.PerplexityMcpClient
        .search` catches whatever its wire raises and degrades with
        reason "mcp wire error" rather than propagating -- so selecting
        "mcp" without an override today is safe (never crashes a skill's
        turn) but never answers for real either, until a real wire is
        threaded through here in place of the placeholder. ``schema_dir``
        is left at its constructor default (delegating to the adapter's
        own ``load_schema``) since ``Settings`` has no per-transport
        schema-directory override today -- the same reasoning "direct"'s
        ``model``/``timeout_s``/``client`` defaults already rest on,
        above.
        """
        transport = self._settings.tool_transport_perplexity
        if transport == "direct":
            from poseidon.mcp.perplexity.adapter import PerplexityDirectAdapter

            return PerplexityDirectAdapter(api_key=self._settings.perplexity_api_key)
        if transport == "mcp":
            from poseidon.mcp.perplexity.mcp_client import PerplexityMcpClient

            return PerplexityMcpClient(wire=_mcp_wire_not_configured)
        raise RuntimeError(
            f"unknown research transport {transport!r} {_EM_DASH} expected 'direct' or 'mcp'"
        )
