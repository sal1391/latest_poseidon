"""The external tool-server layer (doc 02 section 7).

A skill never imports a vendor SDK or builds an HTTP client itself; it calls
a typed interface off ``SkillContext.tools`` (e.g.
``ctx.tools.research.search(...)``), and :class:`mcp.registry
.ToolServerRegistry` decides which transport actually answers the call,
per config. Task 1 (this module and ``registry.py``) ships the registry
and the typed research interface only; ``mcp.perplexity`` -- the first
concrete tool server, with both a direct REST adapter and an MCP-transport
client behind it -- lands in Tasks 2-3.

This package is deliberately a sibling of ``poseidon``, not a submodule of
it: see ``registry.py``'s module docstring and the Task 1 report for the
naming trade-off that follows from matching doc 02 section 7's tree
verbatim (a real PyPI package also named ``mcp`` would collide with this
one if ever added as a dependency).

Nothing in this package has an import-time side effect: constructing
:class:`~mcp.registry.ToolServerRegistry` reads no credentials and opens no
connection, and resolving a transport happens only on first use (see
``registry.py``).
"""
