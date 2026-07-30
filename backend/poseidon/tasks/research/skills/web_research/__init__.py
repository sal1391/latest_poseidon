"""Skill ``research.web_research`` -- one external research question, one
answer, in the marine fuels and shipping-services lens.

The router's pivot skill (doc 02 section 4): where ``data_qa.metric_query``
answers from the certified ontology, this answers from the open web via
``ctx.tools.research`` (:class:`~poseidon.mcp.registry.ToolServerRegistry`,
Phase 7 Tasks 1-3) -- news, market conditions, ESG/sustainability, a
customer or port's outside context. Never touches ``ctx.data``.
"""
