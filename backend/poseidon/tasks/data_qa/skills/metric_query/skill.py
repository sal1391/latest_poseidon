"""Skill ``data_qa.metric_query`` — one metric question, one answer.

Totals, period comparisons and top-N breakdowns are the same question with
different arguments, so they are one skill: the router picks a capability,
not a product shape.
"""

from collections.abc import Callable
from dataclasses import replace
from typing import TypeVar, cast

from poseidon.core.data.query_builder import SpecValidationError, resolve_row_scope_value
from poseidon.core.data.specs import BreakdownQuerySpec, FrameQuerySpec, MetricQuerySpec
from poseidon.core.identity import UserContext
from poseidon.core.ontology.loader import get_ontology
from poseidon.core.skills.context import ConversationSlots, SkillContext
from poseidon.core.skills.result import SkillResult, problem

from .schema import Args
from .tools.build_spec import build_spec
from .tools.compare_breakdown import WINDOW_TOP_N, compare_breakdown_parts
from .tools.format_parts import format_parts

Spec = MetricQuerySpec | BreakdownQuerySpec | FrameQuerySpec
_S = TypeVar("_S", MetricQuerySpec, BreakdownQuerySpec)


def _carried_office(state: ConversationSlots) -> tuple[str, str] | None:
    """The conversation's carried office as ``(column, value)``, or None."""
    if state.office_column and state.office_value:
        return state.office_column, state.office_value
    return None


def _stamp_office(spec: _S, state: ConversationSlots) -> _S:
    """Put the carried office on ``spec`` as a one-value filter.

    The carried value REPLACES any filter the model wrote on the same column
    (plan ruling 6): inside a carried office the router cannot widen or
    redirect the office by naming another one - it says ``scope="book"``.
    """
    column, value = _carried_office(state)
    return replace(spec, filters={**spec.filters, column: (value,)})


def _office_applies(entity: str, state: ConversationSlots) -> bool:
    """True when an office is carried AND its column is a certified
    dimension of ``entity`` - the same check the query builder runs on every
    filter column. The stamp follows the entity the way D16 row scope does:
    an entity without the column (the GL view) is simply not office-scoped,
    rather than a 422 the router could only escape with ``scope="book"``.
    """
    office = _carried_office(state)
    return office is not None and office[0] in get_ontology().entity(entity).dimensions()


def _office_proof(args: Args, state: ConversationSlots) -> list[str]:
    """The proof's LAST line: which office scope the answer ran under."""
    office = _carried_office(state)
    if office is None:
        return []
    if args.scope == "book":
        return ["Office scope: book (carried office not applied)"]
    if not _office_applies(args.entity, state):
        return [
            f"Office scope: {office[0]} is not a dimension of {args.entity}; "
            "carried office not applied"
        ]
    return [f"Office scope: {office[0]} = {office[1]} (carried; scope=office)"]


def run(ctx: SkillContext, args: Args) -> SkillResult:
    """Build the certified spec(s), run them through ``ctx.data``, and shape
    the result.

    A comparison (``args.compare_period`` set) re-drives :func:`build_spec`
    on a copy of ``args`` whose ``period`` has been swapped for
    ``compare_period``, so both windows go through the exact same mapping
    rules. With ``group_by`` as well, both breakdown windows run at
    ``WINDOW_TOP_N`` rows and merge into one comparison table per metric
    (``tools/compare_breakdown.py``).

    OFFICE SCOPE (Phase 17 A2). Under ``args.scope == "office"`` (the
    default) with an office carried in ``ctx.state``, every spec this run
    builds - both windows of any comparison - carries that office as a
    filter (:func:`_stamp_office`), and the proof's LAST line says so. An
    entity without the office column as a certified dimension is not
    stamped, and the proof's last line says that instead.
    ``scope == "book"`` leaves the carried office off and the proof says
    that instead. No carried office: no stamp, no line.

    Every :class:`SpecValidationError` - raised by the query builder inside
    ``ctx.data``, from either query, regardless of shape - is this skill's
    only failure mode: a 422 carrying the builder's own certified message,
    verbatim, because that text was written to be read by the person who
    asked the question (see ``tools/build_spec.py``: ``build_spec`` itself
    never raises today — it is inside the ``try`` anyway, so that the two
    ``build_spec`` calls sit under the same guard and a future certified
    check moved INTO the mapping cannot become an uncaught 500).

    ROW SCOPE (D16). This function is the only code in this skill holding
    ``ctx.user``, so it is where the scope value is resolved -- once per
    dispatch, and stamped onto BOTH windows of a comparison, so a comparison
    can never leave its second query unscoped. ``build_spec`` stays a pure
    ``Args`` -> spec mapping that knows nothing about identity;
    ``dataclasses.replace`` puts the resolved value on the frozen spec it
    returns. ``ctx.user`` is typed ``object`` on ``SkillContext`` (see that
    class's docstring: every seam past ``data``/``settings``/``state`` is,
    to keep the dependency direction one-way), so this call site casts
    through the typed interface, exactly as the docstring prescribes. Today
    the resolver always answers ``None`` -- no certified entity declares a
    ``row_scope`` -- and the day one does, an entity this skill queries
    without an identified caller raises ``SpecValidationError`` and lands in
    the 422 below rather than answering with rows the caller cannot see.
    """
    stamp = args.scope == "office" and _office_applies(args.entity, ctx.state)
    try:
        scope_value = resolve_row_scope_value(args.entity, cast(UserContext | None, ctx.user))

        def scoped(spec_args: Args) -> Spec:
            """``build_spec`` plus what only ``run`` knows: the D16 row scope
            and, under ``scope="office"``, the carried office."""
            spec = replace(build_spec(spec_args), scope_value=scope_value)
            return _stamp_office(spec, ctx.state) if stamp else spec

        if args.group_by is not None and args.compare_period is not None:
            parts, proof = _compare_breakdown(ctx, args, scoped)
        else:
            parts, proof = _single(ctx, args, scoped)
    except SpecValidationError as exc:
        return SkillResult(ok=False, error=problem(422, "invalid query", str(exc)))

    return SkillResult(ok=True, parts=parts, proof=[*proof, *_office_proof(args, ctx.state)])


def _compare_breakdown(
    ctx: SkillContext, args: Args, scoped: Callable[[Args], Spec]
) -> tuple[list[dict], list[str]]:
    """Breakdown with comparison: both windows at ``WINDOW_TOP_N`` rows (plan
    ruling 7), each scoped and stamped, merged by ``compare_breakdown_parts``
    and capped at the router's own ``top_n``."""
    wide = args.model_copy(update={"top_n": WINDOW_TOP_N})
    spec_current = scoped(wide)
    spec_prior = scoped(wide.model_copy(update={"period": args.compare_period}))
    if not isinstance(spec_current, BreakdownQuerySpec) or not isinstance(
        spec_prior, BreakdownQuerySpec
    ):
        # group_by is set on both copies; a python -O-proof guard against
        # build_spec ever breaking that mapping, not a case that can happen.
        raise TypeError("build_spec with group_by did not return a BreakdownQuerySpec")
    result_current = ctx.data.run_breakdown_query(spec_current)
    result_prior = ctx.data.run_breakdown_query(spec_prior)
    return compare_breakdown_parts(
        spec_current, result_current, spec_prior, result_prior, ctx.settings, args.top_n
    )


def _single(
    ctx: SkillContext, args: Args, scoped: Callable[[Args], Spec]
) -> tuple[list[dict], list[str]]:
    """A breakdown, a total, or a total compared across two windows."""
    spec = scoped(args)
    if isinstance(spec, BreakdownQuerySpec):
        return format_parts(spec, ctx.data.run_breakdown_query(spec), ctx.settings)

    result = ctx.data.run_metric_query(spec)
    compare_period = None
    compare_result = None
    if args.compare_period is not None:
        compare_args = args.model_copy(update={"period": args.compare_period})
        compare_spec = scoped(compare_args)
        if not isinstance(compare_spec, MetricQuerySpec):
            # compare_args.group_by is copied unchanged from args, which is
            # unset here too (spec is not a BreakdownQuerySpec) - a python
            # -O-proof guard against that invariant ever silently breaking,
            # not a case that can happen today.
            raise TypeError(
                f"build_spec(compare_args) returned {type(compare_spec).__name__}, "
                "not a MetricQuerySpec"
            )
        compare_result = ctx.data.run_metric_query(compare_spec)
        compare_period = compare_spec.period
    return format_parts(
        spec,
        result,
        ctx.settings,
        compare_period=compare_period,
        compare_result=compare_result,
    )
