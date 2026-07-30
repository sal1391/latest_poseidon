"""The router-facing contract of ``customer_insight.existing_customer_brief``
(Phase 8 Task 4; doc 02 section 4.1).

``Args`` is deliberately thin. ``customer`` arrives CERTIFIED: decision D19
(doc 02 section 4's own "Entry orchestration rule") resolves an ambiguous
name through the customer resolver -- clarification chips, an exact
``CUST_NM`` match -- before this skill is ever dispatched, so ``skill.py``
trusts the string it is handed rather than re-validating it against the
dimension itself, one layer further out than ``research.web_research``'s
own optional ``customer``/``port`` fields rely on for the identical reason
(the router/orchestrator resolves; the skill trusts).

``recency_days`` sizes the ONE window this skill's own tools do not already
pin: ``fetch_metrics``'s prior-year/YTD windows are fixed by doc 02 section
4.1 itself ("prior calendar year vs YTD" -- not configurable), but
``fetch_top_ports``'s own docstring explicitly leaves its window to "the
caller's choice ... (prior year, YTD, or otherwise)". ``recency_days``
supplies that "otherwise": the top-ports breakdown covers the
``recency_days`` days immediately before the brief's anchor date -- see
``skill.py``'s own ``_ports_window``. Bounded ``>= 1`` because a window of
zero days is empty by construction (``PeriodWindow`` itself would reject
it); catching that here, as a structured 422 at argument validation, is
cheaper than a raised ``ValueError`` from inside window construction.

SKILL_META's description names both the flow this skill produces (an
existing-customer brief: certified metrics, top ports, external research,
CRM strategy) and the pivot affordances doc 02 section 4 promises after a
brief completes (a metric drill-down, a research question) -- so the
router prompt contract test
(``test_router_prompt_names_every_skill_the_registry_discovers`` in
``test_llm_prompts.py``) passes automatically the moment this skill is
discovered: that test asserts every REGISTERED skill's description line
appears in the rendered router prompt, with no per-skill edit of its own
required.
"""

from pydantic import BaseModel, Field


class Args(BaseModel):
    """Generate a brief for an existing, certified customer."""

    customer: str = Field(
        min_length=1,
        description="Certified customer name (CUST_NM) to generate the brief for.",
    )
    recency_days: int = Field(
        default=365,
        ge=1,
        description=(
            "How many days of port activity, ending today, to include in the "
            "top-ports breakdown. Does not affect the certified metrics window, "
            "which is always prior calendar year vs year-to-date."
        ),
    )


SKILL_META = {
    "description": (
        "Generate a full brief for an existing, certified customer: certified metrics "
        "(prior year vs YTD), top ports, external research, and a CRM strategy "
        "summary -- streamed plus a PDF. Use for 'run the brief for X' naming a known "
        "customer; afterward, pivot into a metric or research question."
    ),
    "examples": [
        "Run the brief for Northstar Lines",
        "Give me an overview of Maersk",
    ],
}
