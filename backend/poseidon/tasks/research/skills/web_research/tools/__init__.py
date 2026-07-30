"""Deterministic helpers private to ``research.web_research``.

Tools never call an LLM and never talk to the router: ``build_query`` turns
``Args`` into the outbound research query string (the D30 egress whitelist
composer -- see that module's own docstring), ``format_parts`` turns a
:class:`~poseidon.mcp.registry.ResearchResult` into parts and proof.
"""
