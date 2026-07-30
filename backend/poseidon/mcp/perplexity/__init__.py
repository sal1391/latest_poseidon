"""The first concrete tool server behind :class:`poseidon.mcp.registry
.ToolServerRegistry` (doc 02 section 7): Perplexity web research.

Two transports answer the SAME typed :class:`~poseidon.mcp.registry
.ResearchTool` interface -- ``adapter.PerplexityDirectAdapter`` (this
package, Task 2: a direct REST call via ``httpx``) and
``mcp_client.PerplexityMcpClient`` (Task 3: the MCP-transport client) --
selected by ``Settings.tool_transport_perplexity`` (decision D23, direct
default). ``schemas/`` holds the structured-output JSON Schema files both
transports request from Perplexity and validate responses against;
``fixtures/`` holds the recorded/hand-authored response payloads
``adapter.py``'s tests replay through it (no live network call outside the
``research_live``-marked smoke).
"""
