"""Skill ``reporting.report_lookup`` -- answer from the report open in the conversation.

Router-visible. Resolves ``ctx.state.report_ref`` through the report store (scoped to
the caller) and delegates all rendering to ``tools/sections.py``. It runs no query:
the stored payload is the whole data source.
"""

from __future__ import annotations

from typing import cast

from poseidon.core.identity import UserContext
from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.result import SkillResult, problem, text_part

from .schema import Args
from .tools import sections

NO_REPORT_TEXT = (
    "Open a report first: click Monthly report, then pick a perspective, an office and a month."
)
GONE_TEXT = "The report this conversation refers to is no longer available."
ANONYMOUS = "dev|anonymous"


def run(ctx: SkillContext, args: Args) -> SkillResult:
    ref = ctx.state.report_ref
    if ref is None:
        return SkillResult(ok=True, parts=[text_part(NO_REPORT_TEXT)], proof=["Report: none open"])

    store = ctx.report_store
    if store is None:
        return SkillResult(
            ok=False,
            error=problem(
                500,
                "report store unavailable",
                "reporting.report_lookup needs SkillContext.report_store",
            ),
        )

    user = cast(UserContext | None, ctx.user)
    run_row = store.get_run(user.sub if user else ANONYMOUS, ref)  # type: ignore[attr-defined]
    if run_row is None:
        return SkillResult(
            ok=True, parts=[text_part(GONE_TEXT)], proof=[f"Report: run {ref} not found"]
        )

    parts, proof = sections.render(args.section, run_row.payload, args.limit, args.offset)
    return SkillResult(ok=True, parts=parts, proof=proof)
