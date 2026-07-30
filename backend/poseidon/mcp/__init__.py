"""The external tool-server layer (doc 02 section 7).

A skill never imports a vendor SDK or builds an HTTP client itself; it calls
a typed interface off ``SkillContext.tools`` (e.g.
``ctx.tools.research.search(...)``), and :class:`poseidon.mcp.registry
.ToolServerRegistry` decides which transport actually answers the call,
per config. Task 1 (this module and ``registry.py``) ships the registry
and the typed research interface only; ``poseidon.mcp.perplexity`` -- the
first concrete tool server, with both a direct REST adapter and an
MCP-transport client behind it -- lands in Tasks 2-3.

This package lives INSIDE ``poseidon`` (``poseidon.mcp``, not a bare
top-level ``mcp``) by amendment: doc 02 section 7's tree originally showed
it as a sibling of ``poseidon`` (``backend/mcp/``), which Task 1 built
verbatim and then flagged as a naming risk in its report -- a real PyPI
package also named ``mcp`` (the official Model Context Protocol SDK) would
have collided with a bare top-level ``mcp``, most likely biting Task 3's
own MCP-transport client if it ever needed that SDK. The controller
adjudicated and relocated the package here, which keeps the naming intent
(``mcp`` still names the layer) while making the collision structurally
impossible: this package's only import name is ``poseidon.mcp``, never
bare ``mcp``.

Nothing in this package has an import-time side effect: constructing
:class:`~poseidon.mcp.registry.ToolServerRegistry` reads no credentials and
opens no connection, and resolving a transport happens only on first use
(see ``registry.py``).
"""
