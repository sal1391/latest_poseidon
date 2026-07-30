"""Home of the ``customer_insight.existing_customer_brief`` skill
(Phase 8 Task 4; doc 02 §4).

``schema.py``/``skill.py`` land here in Phase 8 Task 4, which is also when
the parent task's ``task.yml`` flips to ``enabled: true`` -- before that,
this directory held only the skill's tools (``fetch_metrics``,
``fetch_top_ports``, ``build_brief_pdf``), built and tested ahead of the
skill that now wires them together (see each tool module's own docstring
for that history).

**Proof-line ownership.** The tools here contribute DETAIL proof lines only
(what each one did: which customer, which windows, how many metrics were
requested, which artifact was written). The header lines every certified
proof block opens with — ``Entity:`` and ``Backend:`` — are the Phase-8
skill's responsibility, added once when it composes the tools' fragments
into the brief's single proof block. A tool cannot honestly write those
lines anyway: it is handed a ``DataClient``, never the ``Settings`` or the
ontology handle they come from.
"""
