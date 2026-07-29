"""Deterministic helpers private to ``data_qa.metric_query``.

Tools never call an LLM and never talk to the router: they turn ``Args`` into
specs and results into parts. Task 2 fills this package.
"""
