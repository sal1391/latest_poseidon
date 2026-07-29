"""Skill ``data_qa.metric_query`` — one metric question, one answer.

Totals, period comparisons and top-N breakdowns are the same question with
different arguments, so they are one skill: the router picks a capability,
not a product shape.
"""

from poseidon.core.data.query_builder import SpecValidationError
from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec
from poseidon.core.skills.context import SkillContext
from poseidon.core.skills.result import SkillResult, problem

from .schema import Args
from .tools.build_spec import build_spec
from .tools.format_parts import format_parts


def run(ctx: SkillContext, args: Args) -> SkillResult:
    """Build the certified spec(s), run them through ``ctx.data``, and shape
    the result.

    A comparison (``args.compare_period`` set, and no ``group_by`` - a
    breakdown never compares) re-drives :func:`build_spec` on a copy of
    ``args`` whose ``period`` has been swapped for ``compare_period``, so
    both windows go through the exact same mapping rules.

    Every :class:`SpecValidationError` - raised by the query builder inside
    ``ctx.data``, from either query, regardless of shape - is this skill's
    only failure mode: a 422 carrying the builder's own certified message,
    verbatim, because that text was written to be read by the person who
    asked the question (see ``tools/build_spec.py``: ``build_spec`` itself
    never raises today — it is inside the ``try`` anyway, so that the two
    ``build_spec`` calls sit under the same guard and a future certified
    check moved INTO the mapping cannot become an uncaught 500).
    """
    try:
        spec = build_spec(args)
        if isinstance(spec, BreakdownQuerySpec):
            result = ctx.data.run_breakdown_query(spec)
            parts, proof = format_parts(spec, result, ctx.settings)
        else:
            result = ctx.data.run_metric_query(spec)
            compare_period = None
            compare_result = None
            if args.compare_period is not None:
                compare_args = args.model_copy(update={"period": args.compare_period})
                compare_spec = build_spec(compare_args)
                if not isinstance(compare_spec, MetricQuerySpec):
                    # compare_args.group_by is copied unchanged from args,
                    # which is unset here too (spec is not a
                    # BreakdownQuerySpec) - a python -O-proof guard against
                    # that invariant ever silently breaking, not a case that
                    # can happen today.
                    raise TypeError(
                        f"build_spec(compare_args) returned {type(compare_spec).__name__}, "
                        "not a MetricQuerySpec"
                    )
                compare_result = ctx.data.run_metric_query(compare_spec)
                compare_period = compare_spec.period
            parts, proof = format_parts(
                spec,
                result,
                ctx.settings,
                compare_period=compare_period,
                compare_result=compare_result,
            )
    except SpecValidationError as exc:
        return SkillResult(ok=False, error=problem(422, "invalid query", str(exc)))

    return SkillResult(ok=True, parts=parts, proof=proof)
