"""Deterministic tools for the future ``existing_customer_brief`` skill.

``fetch_metrics`` and ``fetch_top_ports`` (Task 3) compose certified
``poseidon.core.data.specs`` queries and run them through a
:class:`~poseidon.core.data.client.DataClient` — no SQL, no
``query_builder`` import, no LLM. ``build_brief_pdf`` (Task 4) will render
the brief to PDF via ``ArtifactStore``. Each tool is unit-tested in
``tests/`` on its own, ahead of the skill that will call them in sequence.
"""
