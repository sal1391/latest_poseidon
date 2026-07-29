"""Task ``customer_insight`` — existing-customer and new-prospect briefs.

``task.yml`` ships ``enabled: false``: ``SkillRegistry.discover()`` skips a
disabled task before importing a single one of its modules (see
``poseidon.core.skills.registry._is_enabled``), which is what lets this tree
hold real, unit-tested deterministic tools — ``fetch_metrics``,
``fetch_top_ports`` (Task 3), ``build_brief_pdf`` (Task 4) — well ahead of
the ``existing_customer_brief`` skill (Phase 8) that will call them in
sequence, without registering anything early.
"""
