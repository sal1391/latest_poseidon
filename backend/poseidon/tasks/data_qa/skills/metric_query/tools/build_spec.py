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

from poseidon.core.data.query_builder import TIME_BUCKETS
from poseidon.core.data.specs import (
    BreakdownQuerySpec,
    FrameQuerySpec,
    MetricQuerySpec,
    PeriodWindow,
)

from ..schema import Args


def _filters(args: Args) -> dict[str, tuple[str, ...]]:
    """``[DimFilter(column, values), ...]`` -> ``{column: tuple(values)}``.

    A later filter naming a column already seen overwrites the earlier one
    rather than merging - the router has no certified reason to send two
    entries for the same column, so there is nothing to reconcile.
    """
    return {f.column: tuple(f.values) for f in args.filters}


def is_frame_path(group_by: list[str] | None) -> bool:
    """Plan ruling 1: one plain certified dimension keeps the breakdown SQL;
    two or more columns, or any time bucket, go through a frame."""
    if group_by is None:
        return False
    return len(group_by) > 1 or group_by[0] in TIME_BUCKETS


def build_spec(args: Args) -> MetricQuerySpec | BreakdownQuerySpec | FrameQuerySpec:
    """One certified spec for one metric question.

    The path rule (:func:`is_frame_path`): a single plain ``group_by`` column
    selects a :class:`BreakdownQuerySpec`, ranked by ``order_by`` (default the
    FIRST requested metric: "top GP customers" -> ``order_by_metric="GP"``) in
    ``order``. Two or more columns, or any time bucket, select a
    :class:`FrameQuerySpec` in ``group_by`` order. ``group_by`` absent selects
    a flat :class:`MetricQuerySpec` over every requested metric.
    """
    period = PeriodWindow(args.period.start, args.period.end)
    metrics = tuple(args.metrics)
    filters = _filters(args)

    if args.group_by is not None and is_frame_path(args.group_by):
        return FrameQuerySpec(
            entity=args.entity,
            metrics=metrics,
            period=period,
            group_by=tuple(args.group_by),
            filters=filters,
        )
    if args.group_by is not None:
        return BreakdownQuerySpec(
            entity=args.entity,
            metrics=metrics,
            period=period,
            group_by=args.group_by[0],
            order_by_metric=args.order_by or metrics[0],
            top_n=args.top_n,
            filters=filters,
            order=args.order,
        )
    return MetricQuerySpec(entity=args.entity, metrics=metrics, period=period, filters=filters)
