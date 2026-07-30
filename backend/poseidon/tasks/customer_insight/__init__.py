"""Task ``customer_insight`` — existing-customer and new-prospect briefs.

``task.yml`` ships ``enabled: true`` as of Phase 8 Task 4:
``SkillRegistry.discover()`` now imports and registers both skills this
tree holds -- ``existing_customer_brief`` and ``new_prospect_brief``. Before
Task 4, ``enabled: false`` let this tree hold real, unit-tested
deterministic tools -- ``fetch_metrics``, ``fetch_top_ports``,
``build_brief_pdf`` (Phase 3) -- well ahead of the skills (Phase 8) that call
them in sequence, without registering anything early; see
``poseidon.core.skills.registry._is_enabled`` for the mechanism that made
that possible.
"""
