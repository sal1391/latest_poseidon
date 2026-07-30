"""The router-facing contract of ``research.web_research``.

``Args`` is the only thing the model is allowed to author, and it is also
the D30 egress whitelist (``tools/build_query.py``): every field here, and
nothing else, is what the outbound research query is allowed to be built
from. A skill that pulled from ``ctx.state``/prior tool results instead
would be free to leak certified data (a metric figure, a table row) to an
external third-party service -- see ``tools/build_query.py``'s own module
docstring and this skill's egress contract test.
"""

from pydantic import BaseModel, Field


class Args(BaseModel):
    """Ask an external research question, optionally scoped to a customer,
    port, region, or topic."""

    question: str = Field(min_length=1, description="The research question to answer.")
    customer: str | None = Field(
        default=None, description="Certified customer name to focus the research on."
    )
    port: str | None = Field(
        default=None, description="Certified port name to focus the research on."
    )
    region: str | None = Field(default=None, description="Region to focus the research on.")
    topic: str | None = Field(default=None, description="Topic to focus the research on.")
    recency_days: int | None = Field(
        default=30,
        description=(
            "How recent results should be, in days. 7/30/365 map to Perplexity's own "
            "recency filter (week/month/year); any other value, or null, runs unfiltered."
        ),
    )


SKILL_META = {
    "description": (
        "Research current news, market conditions, or customer/port background from the "
        "open web, for the marine fuels and shipping-services industry -- ESG, competitor "
        "moves, regulatory shifts. Use to pivot beyond what the certified data can answer."
    ),
    "examples": [
        "Any relevant news on Northstar Lines I should be aware of?",
        "What's happening with bunker fuel prices at the Port of Singapore?",
    ],
}
