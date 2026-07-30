"""The router-facing contract of ``customer_insight.new_prospect_brief``
(Phase 8 Task 4; doc 02 section 4, decision D10).

``prospect_name`` is deliberately plain free text, never validated against
the certified customer dimension: a prospect is BY DEFINITION not in it
(doc 02's own "nothing exists for a prospect yet") -- see ``skill.py``'s
own module docstring, "NO INTERNAL DATA TOOLS", for the fuller reasoning.

``recency_days`` is accepted for Args SYMMETRY with
``existing_customer_brief`` -- both brief skills share the same two-field
shape, matching the D19 entry orchestration's own single subject-prompt
turn regardless of which brief mode was chosen -- but this skill has no
internal-data tool to size a window for (see "NO INTERNAL DATA TOOLS"
again) and the ``research`` subskill's own ``run(ctx, mode, subject)``
signature (fixed by Task 2) accepts no recency parameter either, so this
field is genuinely UNUSED by this skill's v1 flow. Disclosed here rather
than silently accepted: a future phase that gives the research subskill a
recency hook would wire it through this already-present field with no
``Args`` change.

SKILL_META's description names the prospect flow and its research-first
shape -- see ``existing_customer_brief/schema.py``'s own docstring for why
the router prompt contract test needs no per-skill edit of its own.
"""

from pydantic import BaseModel, Field


class Args(BaseModel):
    """Generate a brief for a new prospect -- a company that is not yet a
    certified customer."""

    prospect_name: str = Field(
        min_length=1,
        description="The prospective company's name. Free text -- not a certified customer.",
    )
    recency_days: int = Field(
        default=365,
        ge=1,
        description=(
            "Accepted for Args symmetry with existing_customer_brief. This skill calls "
            "no internal-data tool (nothing exists for a prospect yet), so this field "
            "is currently unused -- see skill.py's module docstring."
        ),
    )


SKILL_META = {
    "description": (
        "Generate a full brief for a new prospect: a company not yet a certified "
        "customer. Runs research first (operational profile plus web research), then "
        "a narrative and a CRM strategy noting no current services -- streamed plus a "
        "PDF. Use for new-prospect or research-first brief requests."
    ),
    "examples": [
        "Start a new prospect brief for Meridian Global Shipping",
        "Research this new prospect: Blue Horizon Tankers",
    ],
}
