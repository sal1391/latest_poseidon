"""Skill ``research.web_research`` -- one external research question, one
answer, in the marine fuels and shipping-services lens.

Never touches ``ctx.data``: this skill's only external seam is ``ctx.tools``
(:class:`~poseidon.mcp.registry.ToolServerRegistry`, Phase 7 Tasks 1-3),
which ``SkillContext`` types as plain ``object`` to keep ``poseidon.core``
free of a ``poseidon.mcp`` import (see ``core/skills/context.py``'s own
docstring: "a skill reaching for one casts through the typed interface at
its own call site"). ``_ToolServer`` below is that cast target -- a local
``Protocol`` naming exactly the one property this skill needs
(``.research``), declared here rather than in ``poseidon.mcp.registry``
itself, since this task's sanctioned edits to that module are limited to
``ResearchResult``'s new ``summary`` field.

Two failure shapes render identically (``tools/format_parts.py``'s own
:func:`~poseidon.tasks.research.skills.web_research.tools.format_parts
.degraded_parts`): no tools configured at all (``ctx.tools is None`` --
``SkillContext``'s own default, real only once ``api/app.py`` wires a
registry through) and a real dispatch that came back degraded (the
transport's own honest "unavailable" answer, never an exception). Both are
``ok=True`` skill results, not failures: a research API being unreachable is
not something the model's one self-correction retry (``loop.py``'s own
per-skill chance) could ever fix by trying different arguments, so this
never spends that chance the way a genuine 422/404/500 would.
"""

from typing import Protocol, cast

from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.result import SkillResult
from poseidon.mcp.registry import ResearchTool

from .schema import Args
from .tools.build_query import build_query
from .tools.format_parts import degraded_parts, format_parts

_SCHEMA_NAME = "web_research"

# Byte-pinned (Task 4 brief: "absent tools" renders through the same
# degraded_parts() shape a real degrade does -- this is the "reason" text
# for that specific case).
_NO_TOOLS_REASON = "no research tool server is configured"


class _ToolServer(Protocol):
    """Structural shape this skill needs from ``ctx.tools`` -- exactly what
    a real :class:`~poseidon.mcp.registry.ToolServerRegistry` (or a test's
    small local double standing in for one) provides: a ``.research``
    property answering a :class:`~poseidon.mcp.registry.ResearchTool`. See
    the module docstring for why this is declared locally rather than in
    ``poseidon.mcp.registry`` itself.
    """

    @property
    def research(self) -> ResearchTool: ...


def run(ctx: SkillContext, args: Args) -> SkillResult:
    """Build the outbound query (the D30 whitelist composer,
    ``tools/build_query.py``), dispatch it through ``ctx.tools.research``,
    and shape the result (``tools/format_parts.py``).

    ``ok=True`` unconditionally: this skill's only two failure shapes
    (absent tools, a degraded dispatch) are both honest, successful
    answers -- see the module docstring. There is no argument-validation
    failure mode of this skill's OWN to report beyond what pydantic already
    enforces on ``Args`` before ``run`` is ever called (``SkillRegistry
    .dispatch``'s own 422 path).
    """
    if ctx.tools is None:
        # No ToolServerRegistry at all -- unlike a real dispatch's own
        # degrade, there is no transport that even attempted to answer, so
        # "none" (final-review wave item 3) is the honest transport name for
        # this specific caller, not a stand-in for any real transport value.
        parts, proof = degraded_parts(_NO_TOOLS_REASON, "none")
        return SkillResult(ok=True, parts=parts, proof=proof)

    research_tool = cast(_ToolServer, ctx.tools).research
    query = build_query(args)
    result = research_tool.search(
        query=query, schema_name=_SCHEMA_NAME, recency_days=args.recency_days
    )
    parts, proof = format_parts(query, result)
    return SkillResult(ok=True, parts=parts, proof=proof)
