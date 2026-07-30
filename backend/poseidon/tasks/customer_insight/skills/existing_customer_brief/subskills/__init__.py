"""Subskills of the future ``customer_insight.existing_customer_brief`` and
``customer_insight.new_prospect_brief`` skills (Phase 8; doc 02 section 1's
folder law: "SUBSKILLS: internal steps that may call LLM tiers", invoked in
code by their parent skill in a fixed order, never router-visible --
decision D3).

``research/`` (Task 2) is the first to ship -- LLM-free itself (search +
format; see its own module docstring for why doc 02 section 4.3's "then
Sonnet synthesis" is out of THIS subskill's v1 scope). ``contextualize/``
and ``strategize/`` (Task 3) follow the same shape.
"""
