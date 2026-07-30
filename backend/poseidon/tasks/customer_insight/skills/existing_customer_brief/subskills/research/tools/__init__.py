"""Deterministic helpers private to the ``research`` subskill --
``build_query`` composes the D30 whitelist-disciplined outbound query per
schema_name; ``format_phase_section`` turns one call's
:class:`~poseidon.mcp.registry.ResearchResult` (or a degrade) into one
``phase_section`` part. Neither calls an LLM.
"""
