"""``data_qa.metric_query`` — not implemented yet.

The skill's contract (``Args``, ``SKILL_META``, ``run(ctx, args)``) is real
and registered from today, so discovery, schema/dispatch parity and argument
validation are tested against a real skill rather than a fixture. Only the
body is pending: Task 2 of the phase-3 plan replaces it with spec building,
query execution, typed parts and proof lines.

Until then it answers the way an unimplemented capability should — a
structured 501, not a plausible-looking empty result.
"""

from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.result import SkillResult, problem

from .schema import Args


def run(ctx: SkillContext, args: Args) -> SkillResult:
    return SkillResult(
        ok=False,
        error=problem(501, "not implemented", "metric_query lands in Task 2"),
    )
