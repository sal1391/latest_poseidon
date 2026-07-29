"""Home of the future ``customer_insight.existing_customer_brief`` skill
(Phase 8; doc 02 §4).

No ``schema.py``/``skill.py`` here yet — a ``skills/<name>/`` directory only
becomes a router-visible skill once it has a ``schema.py``
(``SkillRegistry._skill_dirs``), and the parent task is disabled besides. For
now this directory holds only the skill's future tools, built and tested
ahead of the skill that will wire them together.

**Proof-line ownership.** The tools here contribute DETAIL proof lines only
(what each one did: which customer, which windows, how many metrics were
requested, which artifact was written). The header lines every certified
proof block opens with — ``Entity:`` and ``Backend:`` — are the Phase-8
skill's responsibility, added once when it composes the tools' fragments
into the brief's single proof block. A tool cannot honestly write those
lines anyway: it is handed a ``DataClient``, never the ``Settings`` or the
ontology handle they come from.
"""
