"""Keyword/intent lexicon for :func:`poseidon.core.parsing.skill_hinter.hint`
(doc 02 section 5): which words in a user's message lean toward which skill,
and by how much. Pure data -- no behavior lives here; see ``skill_hinter.py``
for how these tables are matched and summed.

Future skill ids (updated, Phase 7 Task 4)
--------------------------------------------
``research.web_research`` USED TO be future here -- it turned real the
moment Phase 7 Task 4 landed ``poseidon/tasks/research/skills/web_research/``,
exactly the "no lexicon change required" promise the paragraph below always
made, now demonstrated rather than merely asserted. ``data_qa.metric_query``
and ``research.web_research`` are both registered, router-visible skills
today; the remaining two ids this table still references are genuinely
future:

* ``customer_insight.existing_customer_brief`` /
  ``customer_insight.new_prospect_brief`` -- the ``customer_insight`` task
  directory exists (``poseidon/tasks/customer_insight/``) but its
  ``task.yml`` is ``enabled: false``. Of the two brief skills, only
  ``existing_customer_brief`` has a skill directory at all (with tools and
  tests, but no ``schema.py``); ``new_prospect_brief`` has no directory yet.
  Either way ``SkillRegistry.discover`` skips the whole task, so both ids
  are future ids today (Phase 8 finishes and enables it; see that package's
  own docstrings).

Referencing a not-yet-registered id here is deliberate, not a typo: the
hinter is advisory lexicon data, decoupled from what is registered TODAY,
so each id turns real the moment its owning phase lands the skill -- no
lexicon change required, exactly what just happened for
``research.web_research`` above. ``data_qa.metric_query`` was the original
proof of that same decoupling, from the other direction (see
``poseidon/tasks/data_qa/skills/metric_query/schema.py`` -- its own
``SKILL_META['examples']`` even includes "Top GP customers for Port of
Singapore in April 2026" verbatim, which is why that phrase is this task's
flagship pipeline test).

Brief words and mode hints
----------------------------
:data:`KEYWORDS`' four generic brief words (brief/report/profile/overview)
name BOTH brief skills at equal weight -- vocabulary alone cannot say which
brief a plain "give me an overview" wants, so listing only one would be a
guess dressed up as a hint. :data:`MODE_HINTS` is what actually disambiguates:
the phrases "prospect"/"new customer" add weight ONLY to the prospect brief,
"existing customer" adds weight ONLY to the existing-customer brief, so a
message that combines a generic word with a mode phrase ("existing customer
overview") comes back with a decisive leader instead of a tie. A bare generic
word alone still returns both, tied, sorted alphabetically by skill id --
that is a legitimate, harmless outcome (hints are advisory, never a
dispatch; see ``skill_hinter.hint``'s docstring for the tie-break rule).

Deferred: doc 02 section 5 describes the hinter's output as a ranked skill
list "+ mode hints", i.e. a mode signal surfaced alongside the skills. The
shipped :func:`~poseidon.core.parsing.skill_hinter.hint` folds
:data:`MODE_HINTS` INTO the skill scores instead and returns only
``CandidateSkill`` tuples; ``ParsedTurn`` carries no ``mode`` field of its
own, and ``ConversationSlots.mode`` is written by the bubble-entry flow,
not by parsing. Emitting a separate mode hint is deferred to Phase 6, which
owns mode end to end -- doing it here would mean inventing a second output
shape for a consumer that does not exist yet.

:data:`MODE_SLOT_ALIASES` is a third, smaller table beyond the two the task
interface names (``KEYWORDS``, ``MODE_HINTS``). It maps the two mode values
doc 02's bubble-entry chips already use (``poseidon.api.mock_chat``'s
``existing_customer``/``new_prospect`` chip ids) onto the ``MODE_HINTS``
phrase key they mean, so ``skill_hinter.hint`` can treat a carried
``ConversationSlots.mode`` as the same signal as the text phrase -- a user
should not have to repeat "existing customer" on every turn any more than
they should have to repeat the customer's name (doc 02 section 5's carry
philosophy, applied to mode). Nothing in this phase actually sets
``ConversationSlots.mode`` to either value yet (a later phase wires the
bubble-entry flow into conversation state); this table is forward-compatible
with that, not a claim that it is already wired end to end.
"""

METRIC_QUERY = "data_qa.metric_query"
WEB_RESEARCH = "research.web_research"
EXISTING_CUSTOMER_BRIEF = "customer_insight.existing_customer_brief"
NEW_PROSPECT_BRIEF = "customer_insight.new_prospect_brief"

# Every entry below weighs the same: the hinter's only job is to produce an
# advisory shortlist, not a finely-tuned relevance score, so one uniform
# weight keeps the arithmetic (and the tests) trivial to verify by hand --
# a matched lexeme counts once, two matched lexemes for the same skill sum
# to 2x, and so on. See ``skill_hinter.hint`` for the summation itself.
_WEIGHT = 1.0

KEYWORDS: dict[str, tuple[tuple[str, float], ...]] = {
    # -- metric words -> data_qa.metric_query --------------------------
    "gp": ((METRIC_QUERY, _WEIGHT),),
    "gross profit": ((METRIC_QUERY, _WEIGHT),),
    "volume": ((METRIC_QUERY, _WEIGHT),),
    "tons": ((METRIC_QUERY, _WEIGHT),),
    "margin": ((METRIC_QUERY, _WEIGHT),),
    "win rate": ((METRIC_QUERY, _WEIGHT),),
    "top": ((METRIC_QUERY, _WEIGHT),),
    "breakdown": ((METRIC_QUERY, _WEIGHT),),
    "compare": ((METRIC_QUERY, _WEIGHT),),
    # -- research words -> research.web_research ------------------------
    "news": ((WEB_RESEARCH, _WEIGHT),),
    "article": ((WEB_RESEARCH, _WEIGHT),),
    "esg": ((WEB_RESEARCH, _WEIGHT),),
    "sustainability": ((WEB_RESEARCH, _WEIGHT),),
    "market": ((WEB_RESEARCH, _WEIGHT),),
    "competitor": ((WEB_RESEARCH, _WEIGHT),),
    # -- brief words -> both brief skills; MODE_HINTS below breaks the tie
    "brief": ((EXISTING_CUSTOMER_BRIEF, _WEIGHT), (NEW_PROSPECT_BRIEF, _WEIGHT)),
    "report": ((EXISTING_CUSTOMER_BRIEF, _WEIGHT), (NEW_PROSPECT_BRIEF, _WEIGHT)),
    "profile": ((EXISTING_CUSTOMER_BRIEF, _WEIGHT), (NEW_PROSPECT_BRIEF, _WEIGHT)),
    "overview": ((EXISTING_CUSTOMER_BRIEF, _WEIGHT), (NEW_PROSPECT_BRIEF, _WEIGHT)),
}

MODE_HINTS: dict[str, tuple[tuple[str, float], ...]] = {
    "prospect": ((NEW_PROSPECT_BRIEF, _WEIGHT),),
    "new customer": ((NEW_PROSPECT_BRIEF, _WEIGHT),),
    "existing customer": ((EXISTING_CUSTOMER_BRIEF, _WEIGHT),),
}

MODE_SLOT_ALIASES: dict[str, str] = {
    "existing_customer": "existing customer",
    "new_prospect": "prospect",
}
