"""``Args`` -> certified spec: the only place this skill touches a
``poseidon.core.data.specs`` dataclass.

``build_spec`` maps field-for-field and validates nothing itself: an
uncertified metric, dimension, or filter value only surfaces once
``query_builder`` renders the spec (raising ``SpecValidationError``), which
``skill.run`` catches and turns into a 422 whose text is the certified
message verbatim.

Comparison periods are deliberately NOT this function's job. ``Args.
compare_period`` exists for ``skill.run`` to notice and re-drive
``build_spec`` a second time, on a copy of ``args`` whose ``period`` has been
replaced by ``compare_period`` (``args.model_copy(update={"period": ...})``)
- ``build_spec`` itself only ever reads ``args.period``, so calling it twice
on the same ``args`` (period swapped) is exactly "the same query, a
different window", with no separate comparison code path to keep in sync.
"""

from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow

from ..schema import Args


def _filters(args: Args) -> dict[str, tuple[str, ...]]:
    """``[DimFilter(column, values), ...]`` -> ``{column: tuple(values)}``.

    A later filter naming a column already seen overwrites the earlier one
    rather than merging - the router has no certified reason to send two
    entries for the same column, so there is nothing to reconcile.
    """
    return {f.column: tuple(f.values) for f in args.filters}


def build_spec(args: Args) -> MetricQuerySpec | BreakdownQuerySpec:
    """One certified spec for one metric question.

    ``group_by`` present selects a :class:`BreakdownQuerySpec`, ordered by
    the FIRST requested metric ("top GP customers" -> ``metrics=["GP",
    ...]`` -> ``order_by_metric="GP"``, matching how a router names metrics
    in the order that answers the question). ``group_by`` absent selects a
    flat :class:`MetricQuerySpec` over every requested metric.
    """
    period = PeriodWindow(args.period.start, args.period.end)
    metrics = tuple(args.metrics)
    filters = _filters(args)

    if args.group_by is not None:
        return BreakdownQuerySpec(
            entity=args.entity,
            metrics=metrics,
            period=period,
            group_by=args.group_by,
            order_by_metric=metrics[0],
            top_n=args.top_n,
            filters=filters,
        )

    return MetricQuerySpec(
        entity=args.entity,
        metrics=metrics,
        period=period,
        filters=filters,
    )
