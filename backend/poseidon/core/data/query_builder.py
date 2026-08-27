"""Deterministic SQL rendering for certified specs (see ``specs.py``).

The LLM never authors SQL: skills build a :class:`~poseidon.core.data.specs.
MetricQuerySpec` or :class:`~poseidon.core.data.specs.BreakdownQuerySpec`
naming only certified entities/metrics/dimensions (per
``poseidon.core.ontology.loader.get_ontology()``), and the functions below
render that spec into a parameterized SQL string for one of two dialects:

- ``"postgres"`` — the local ``synthetic`` schema (the synthetic data client).
- ``"snowflake"`` — the certified Snowflake tables/views (the live client).

Every function returns ``(sql, params)``: one deterministic SQL string
(clauses joined by ``\\n``, in ``SELECT / FROM / WHERE / GROUP BY / HAVING /
ORDER BY / LIMIT`` order — clauses that don't apply are simply omitted) and a
parallel list of ``%s`` placeholder values, in the exact order they appear in
the string. Everything here is dialect-agnostic except three small hooks:
table naming (``_table_name``), date-column rendering (``_date_expr`` —
``TO_DATE(...)`` only for a VARCHAR-typed date column on Snowflake), and the
``ORDER BY ... NULLS LAST`` suffix (Postgres only). See
``backend/tests/test_query_builder_snapshots.py`` for the full pinned
contract, including exact error strings.

Validation always happens before rendering. An unknown entity name raises the
loader's own ``KeyError`` (``Ontology.entity`` — see
``poseidon.core.ontology.models``) verbatim, never wrapped. Every other spec
mistake — an uncertified metric, a filter/group-by column that isn't a
certified dimension of the entity, a filter whose value collection is empty
(which would render an invalid ``IN ()``), an ``order_by_metric`` outside
``metrics``, ``top_n < 1``, or a *volume-mode* violation (see below) — raises
:class:`SpecValidationError`, whose message text is itself part of the
pinned snapshot contract. An inverted/empty period window is rejected even
earlier, by ``PeriodWindow`` itself (a plain ``ValueError`` at construction).

**NULL placeholder.** Every ``COALESCE(...)`` this module renders — group-by
projections and filter predicates alike — uses the entity's own certified
``null_placeholder`` (``poseidon.core.ontology.models.Entity``), never a
hardcoded literal: ``'Unassigned'`` on ``W_MARINE_GL_SOURCE_AI``,
``'Unknown'`` everywhere else. See the loader's ``_NULL_PLACEHOLDERS`` for
the certified rules behind that mapping.

**Row scope (D16, dormant).** An entity MAY declare ``row_scope: {column,
claim}`` (``poseidon.core.ontology.models.RowScope``); every builder then
appends ``<column> = %s`` bound to the caller's own claim value, which
:func:`resolve_row_scope_value` reads off a
:class:`~poseidon.core.identity.UserContext`. NO certified entity declares
one today, so every snapshot in this module's test file is unchanged by the
mechanism -- but the checks are symmetric and FAIL CLOSED in both directions:
a declaring entity with no ``scope_value`` is refused (an unthreaded call
path breaks loudly at flip time rather than returning every row), and a
``scope_value`` supplied for a non-declaring entity is refused too (see
:func:`_require_scope_value`).

**Volume mode** (currently only ``W_MARINE_GL_SOURCE_AI``, but driven
entirely by the ontology's ``dual_purpose_measures[0].unit_pivot`` — see
``_is_volume_mode``): when a spec's filters pin the entity's dual-purpose
pivot column to *only* its pivot value — comparing distinct values, so
``{"CLASS4": ("Volume",)}`` and a duplicate-laden ``{"CLASS4": ("Volume",
"Volume")}`` both qualify, but a mixed IN-list that includes a *different*
value alongside it does not — the filter itself already scopes the
dual-purpose measure to one unit, so the exclusion guard is dropped and a
plain aggregate is refused instead: ``build_metric_query`` always raises in
volume mode, and ``build_breakdown_query`` only accepts a ``group_by`` equal
to the hierarchy level immediately below the pivot column (``CLASS4`` ->
``CLASS3``). Outside volume mode, nothing here changes.
"""

from collections.abc import Mapping

from poseidon.core.identity import UserContext
from poseidon.core.ontology.loader import get_ontology
from poseidon.core.ontology.models import Entity

from .specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow

_SYNTHETIC_SCHEMA = "synthetic"

# WIN_RATE is a diagnostic-only ratio (see ontology.yml's rule text on the
# metric); the small-sample guard has no structured ontology field, so —
# unlike the GL dual-purpose exclusion below — it is legitimately hardcoded
# here.
_SMALL_SAMPLE_METRIC = "WIN_RATE"
_SMALL_SAMPLE_HAVING = 'HAVING SUM("#_INQUIRIES") >= 5'

# The only metric on W_MARINE_GL_SOURCE_AI that measures the dual-purpose
# AMOUNT_USD column. The guard's *clause text* always comes from the
# ontology's `dual_purpose_exclusion` field at render time (never hardcoded);
# this name only identifies *when* to apply it — except in volume mode (see
# `_is_volume_mode`), where the caller's own filter already scopes to one
# unit and the guard would otherwise contradict it.
_MONETARY_METRIC = "MONETARY_TOTAL"


class SpecValidationError(ValueError):
    """A spec references something outside the certified ontology.

    Message strings are part of the snapshot contract — see the error-case
    tests in ``test_query_builder_snapshots.py``.
    """


def _entity(name: str) -> Entity:
    """Resolve ``name`` via the loader.

    An unknown or not-yet-certified (``planned``) name raises the loader's
    own ``KeyError`` (``Ontology.entity``) unmodified — that message is the
    "unknown entity (loader's)" case in the rendering-rules contract.
    """
    return get_ontology().entity(name)


def _require_certified_metrics(entity: Entity, metrics: tuple[str, ...]) -> None:
    for m in metrics:
        if m not in entity.metrics:
            raise SpecValidationError(
                f"unknown metric {m!r} for entity {entity.name} — "
                f"certified: {sorted(entity.metrics)}"
            )


def _require_dimension(entity: Entity, col: str) -> None:
    """Raise unless ``col`` is a certified dimension of ``entity``.

    Covers both the "non-dimension filter/group_by column" case (``col``
    exists but has a different role, e.g. a measure) and the "unknown filter
    column" case (``col`` doesn't exist at all) with the same message — a
    column that was never certified as a dimension "is not a dimension"
    either way.
    """
    if col not in entity.dimensions():
        raise SpecValidationError(f"{col!r} is not a dimension of {entity.name}")


def _require_filter_values(col: str, values: tuple[str, ...]) -> None:
    """Raise unless ``col``'s filter carries at least one value.

    An empty collection would render as ``IN ()`` — a syntax error on both
    dialects — so it can never be a legitimate query. "Filter on nothing"
    is also semantically ambiguous (match everything? match nothing?), so
    it is refused outright rather than being silently dropped or silently
    rendered into invalid SQL.
    """
    if not values:
        raise SpecValidationError(
            f"filter on {col!r} has no values — omit the column or "
            f"provide at least one value"
        )


def _validate_filters(entity: Entity, filters: Mapping[str, tuple[str, ...]]) -> None:
    """Every filter column must be a certified dimension AND carry values.

    Column-level checks run first, per column, so a filter that is both
    uncertified and empty reports the more fundamental problem.
    """
    for col, values in filters.items():
        _require_dimension(entity, col)
        _require_filter_values(col, values)


def resolve_row_scope_value(entity_name: str, user: UserContext | None) -> str | None:
    """The one sanctioned way to produce a spec's ``scope_value`` (D16).

    Returns ``None`` for an entity that declares no ``row_scope`` -- which is
    every certified entity today, so every call site this codebase currently
    has threads a ``None`` through. That is not a no-op: the call sites are
    wired NOW so that the day an entity declares a scope, they carry a real
    identity instead of quietly rendering an unscoped query.

    For an entity that DOES declare one, this fails closed rather than
    guessing:

    - ``user is None`` (an unauthenticated path -- a dev CLI, a background
      job, a call site nobody threaded an identity through) raises
      :class:`SpecValidationError` naming the entity. There is no "no user
      means no scope" fallback: that is precisely the branch that would leak
      every row.
    - a claim whose value is empty/absent (``UserContext.email`` is optional)
      raises too, rather than falling back to another claim -- scoping to the
      wrong column value is a worse failure than refusing.

    Otherwise it returns the named claim's value off the caller's
    :class:`~poseidon.core.identity.UserContext`. The entity name is resolved
    through the loader, so an unknown/planned entity raises the loader's own
    ``KeyError`` here exactly as it would inside any builder.
    """
    entity = _entity(entity_name)
    row_scope = entity.row_scope
    if row_scope is None:
        return None
    if user is None:
        raise SpecValidationError(
            f"{entity.name} declares row_scope on {row_scope.column} "
            f"(claim {row_scope.claim!r}) but there is no caller identity to "
            f"read it from -- refusing to build an unscoped query"
        )
    value = getattr(user, row_scope.claim)
    if not value:
        raise SpecValidationError(
            f"{entity.name} declares row_scope on {row_scope.column} but the "
            f"caller's {row_scope.claim!r} claim is empty -- refusing to build "
            f"an unscoped query"
        )
    return value


def _require_scope_value(entity: Entity, scope_value: str | None) -> None:
    """Enforce the symmetric D16 contract, in BOTH directions, before any
    rendering happens (every builder calls this immediately after resolving
    its entity, ahead of its own validation, so no other spec mistake can
    mask a scope one).

    - entity declares ``row_scope`` and no ``scope_value`` was supplied ->
      refuse. FAIL CLOSED: a call path that was never threaded breaks loudly
      the day an entity declares a scope, instead of silently rendering the
      query without the predicate and returning every row.
    - entity declares none and a ``scope_value`` WAS supplied -> refuse too.
      A caller that believes scoping applies where the ontology certifies
      none is a bug; silently dropping the value would let that belief
      survive undetected onto an entity that also has none.
    """
    row_scope = entity.row_scope
    if row_scope is None:
        if scope_value is not None:
            raise SpecValidationError(
                f"{entity.name} declares no row_scope -- a scope_value must "
                f"not be supplied for it"
            )
        return
    if scope_value is None:
        raise SpecValidationError(
            f"{entity.name} declares row_scope on {row_scope.column} "
            f"(claim {row_scope.claim!r}) but this query carries no "
            f"scope_value -- refusing to build an unscoped query"
        )


def _scope_condition(entity: Entity, scope_value: str | None, params: list) -> str | None:
    """The rendered scope predicate (``<column> = %s``) and its bound param,
    or ``None`` when the entity declares no ``row_scope``.

    Always a placeholder, never string interpolation: the scope value is the
    one value in this module that comes from an external identity provider
    rather than the certified ontology, so it is the one that must never be
    concatenated into SQL. ``_require_scope_value`` has already run by the
    time this is called, so a declaring entity is guaranteed a non-None
    ``scope_value`` here.
    """
    row_scope = entity.row_scope
    if row_scope is None:
        return None
    params.append(scope_value)
    return f"{row_scope.column} = %s"


def _table_name(entity: Entity, dialect: str) -> str:
    if dialect == "postgres":
        return f"{_SYNTHETIC_SCHEMA}.{entity.name.lower()}"
    if dialect == "snowflake":
        return entity.fqn
    raise SpecValidationError(f"unknown dialect {dialect!r}")


def _date_expr(entity: Entity, dialect: str) -> str:
    """Render the entity's date column for use in a comparison.

    Snowflake wraps a VARCHAR-typed date column in ``TO_DATE(...)``
    (``W_MARINE_GL_SOURCE_AI.PERIOD_DATE``); a DATE-typed column, and every
    Postgres query regardless of column type, compares the bare column.
    """
    date_col = entity.date_column
    if dialect == "snowflake" and entity.columns[date_col].type == "VARCHAR":
        return f"TO_DATE({date_col})"
    return date_col


def _metric_expr(entity: Entity, metric_name: str) -> str:
    """The certified metric SQL, verbatim, aliased to its (quoted) name."""
    return f'{entity.metrics[metric_name].sql} AS "{metric_name}"'


def _is_volume_mode(entity: Entity, filters: Mapping[str, tuple[str, ...]]) -> bool:
    """True when ``filters`` pins the entity's dual-purpose measure to
    *only* its certified unit-pivot value — e.g. ``{"CLASS4": ("Volume",)}``
    on ``W_MARINE_GL_SOURCE_AI`` — comparing the **distinct** values given so
    a duplicate like ``("Volume", "Volume")`` (or a caller passing a list
    instead of a tuple) still counts.

    This is the ontology's own signal (``business_rules``: "Volume queries
    drop the exclusion and carry a unit") that the caller has already scoped
    the query to one unit, so the dual-purpose guard must be dropped and no
    plain aggregate is allowed. A mixed IN-list that includes the pivot value
    alongside a *different* value (e.g. ``CLASS4 IN ('Volume', 'Trade GP')``)
    is deliberately NOT volume mode — that query is still asking for a
    monetary total, so the guard still applies and simply drops the Volume
    rows from it. Entities with no dual-purpose measure (``dual_purpose_
    pivot_column is None``) are never in volume mode.
    """
    pivot_col = entity.dual_purpose_pivot_column
    if pivot_col is None:
        return False
    return set(filters.get(pivot_col) or ()) == {entity.dual_purpose_pivot_value}


def _volume_mode_required_group_by(entity: Entity) -> str:
    """The only valid ``group_by`` for a volume-mode breakdown: the
    hierarchy level immediately below the dual-purpose pivot column
    (``CLASS4`` -> ``CLASS3`` on ``W_MARINE_GL_SOURCE_AI``) — every level at
    or above the pivot still mixes incompatible units within one pivot
    value; only the level below is guaranteed single-unit per row group.

    Raises :class:`SpecValidationError` (never a bare ``ValueError``/
    ``IndexError``) if the pivot column isn't one of the entity's
    ``hierarchy_levels`` at all, or if it's the last (narrowest) one with no
    level below it — both cases are structurally unreachable through the
    vendored ontology today (``CLASS4`` is always present and never last),
    but a future ontology edit could hit either, and a bare Python exception
    here would leak an internal ``IndexError`` past the spec-validation
    contract.
    """
    levels = entity.hierarchy_levels
    pivot = entity.dual_purpose_pivot_column
    if pivot not in levels:
        raise SpecValidationError(
            f"pivot column {pivot!r} is not a hierarchy level of {entity.name}"
        )
    pivot_index = levels.index(pivot)
    if pivot_index + 1 >= len(levels):
        raise SpecValidationError(
            f"pivot column {pivot!r} has no level below it in {entity.name}"
        )
    return levels[pivot_index + 1]


def _where_clause(
    entity: Entity,
    dialect: str,
    metrics: tuple[str, ...],
    period: PeriodWindow,
    filters: Mapping[str, tuple[str, ...]],
    params: list,
    scope_value: str | None = None,
) -> str:
    """Build the single-line WHERE clause and extend ``params`` in place.

    Condition order: the half-open period window, then each filter column in
    the order given (OR within a column's IN-list, AND across columns), then
    — only for W_MARINE_GL_SOURCE_AI queries touching MONETARY_TOTAL, and
    only outside volume mode (see ``_is_volume_mode``) — the dual-purpose
    exclusion guard, sourced from the ontology's ``dual_purpose_exclusion``
    field, never hardcoded here, and LAST the D16 row-scope predicate (see
    ``_scope_condition``) for an entity that declares one.

    Scope last, deliberately: every clause a caller can influence renders
    exactly where it always did, so a query over an entity with no
    ``row_scope`` (every certified entity today) is byte-identical to the one
    this module built before the mechanism existed.
    """
    date_expr = _date_expr(entity, dialect)
    conditions = [f"{date_expr} >= %s AND {date_expr} < %s"]
    params.extend([period.start, period.end])

    for col, values in filters.items():
        placeholders = ", ".join(["%s"] * len(values))
        conditions.append(
            f"COALESCE({col}, '{entity.null_placeholder}') IN ({placeholders})"
        )
        params.extend(values)

    if (
        entity.dual_purpose_exclusion
        and _MONETARY_METRIC in metrics
        and not _is_volume_mode(entity, filters)
    ):
        conditions.append(entity.dual_purpose_exclusion)

    scope = _scope_condition(entity, scope_value, params)
    if scope is not None:
        conditions.append(scope)

    return " AND ".join(conditions)


def build_metric_query(spec: MetricQuerySpec, dialect: str) -> tuple[str, list]:
    entity = _entity(spec.entity)
    _require_scope_value(entity, spec.scope_value)
    _require_certified_metrics(entity, spec.metrics)
    _validate_filters(entity, spec.filters)
    if _is_volume_mode(entity, spec.filters):
        required = _volume_mode_required_group_by(entity)
        raise SpecValidationError(
            f"{entity.dual_purpose_pivot_column} = {entity.dual_purpose_pivot_value!r} "
            f"is unit-mixed — a single aggregate is not allowed; use a "
            f"breakdown grouped by {required}"
        )

    params: list = []
    select_list = ", ".join(_metric_expr(entity, m) for m in spec.metrics)
    where = _where_clause(
        entity, dialect, spec.metrics, spec.period, spec.filters, params, spec.scope_value
    )

    clauses = [
        f"SELECT {select_list}",
        f"FROM {_table_name(entity, dialect)}",
        f"WHERE {where}",
    ]
    if _SMALL_SAMPLE_METRIC in spec.metrics:
        clauses.append(_SMALL_SAMPLE_HAVING)

    return "\n".join(clauses), params


def build_breakdown_query(spec: BreakdownQuerySpec, dialect: str) -> tuple[str, list]:
    entity = _entity(spec.entity)
    _require_scope_value(entity, spec.scope_value)
    _require_certified_metrics(entity, spec.metrics)
    _require_dimension(entity, spec.group_by)
    _validate_filters(entity, spec.filters)
    if spec.order_by_metric not in spec.metrics:
        raise SpecValidationError(
            f"order_by_metric {spec.order_by_metric!r} must be one of "
            f"the requested metrics {spec.metrics!r}"
        )
    if spec.top_n < 1:
        raise SpecValidationError(f"top_n must be >= 1, got {spec.top_n}")
    if _is_volume_mode(entity, spec.filters):
        required = _volume_mode_required_group_by(entity)
        if spec.group_by != required:
            raise SpecValidationError(
                f"volume-mode breakdowns must group by {required} "
                f"(each {required} is one unit); got {spec.group_by!r}"
            )

    params: list = []
    select_list = ", ".join(
        [f"COALESCE({spec.group_by}, '{entity.null_placeholder}') AS {spec.group_by}"]
        + [_metric_expr(entity, m) for m in spec.metrics]
    )
    where = _where_clause(
        entity, dialect, spec.metrics, spec.period, spec.filters, params, spec.scope_value
    )

    clauses = [
        f"SELECT {select_list}",
        f"FROM {_table_name(entity, dialect)}",
        f"WHERE {where}",
        "GROUP BY 1",
    ]
    if _SMALL_SAMPLE_METRIC in spec.metrics:
        clauses.append(_SMALL_SAMPLE_HAVING)

    nulls_last = " NULLS LAST" if dialect == "postgres" else ""
    clauses.append(f'ORDER BY "{spec.order_by_metric}" DESC{nulls_last}')
    clauses.append("LIMIT %s")
    params.append(spec.top_n)

    return "\n".join(clauses), params


def build_dimension_values_query(
    entity: str, column: str, search: str | None, dialect: str, scope_value: str | None = None
) -> tuple[str, list]:
    """A dimension-value list is data too: a type-ahead over a scoped entity
    must show one caller only the values their own rows carry, so
    ``scope_value`` follows the identical D16 rules the two spec builders
    apply (see ``_require_scope_value``)."""
    entity_obj = _entity(entity)
    _require_scope_value(entity_obj, scope_value)
    _require_dimension(entity_obj, column)

    params: list = []
    conditions = [f"{column} IS NOT NULL"]
    if search is not None:
        conditions.append(f"{column} ILIKE %s")
        params.append(f"%{search}%")
    scope = _scope_condition(entity_obj, scope_value, params)
    if scope is not None:
        conditions.append(scope)

    clauses = [
        f"SELECT DISTINCT {column}",
        f"FROM {_table_name(entity_obj, dialect)}",
        f"WHERE {' AND '.join(conditions)}",
        f"ORDER BY {column}",
    ]
    return "\n".join(clauses), params


def build_period_range_query(
    entity: str, dialect: str, scope_value: str | None = None
) -> tuple[str, list]:
    """The one builder with no WHERE clause of its own: a scoped entity gains
    exactly one (the scope predicate), keeping SELECT / FROM / WHERE order.
    Scoped here too because MIN/MAX over the whole table would otherwise
    disclose the shape of rows the caller cannot see."""
    entity_obj = _entity(entity)
    _require_scope_value(entity_obj, scope_value)
    date_expr = _date_expr(entity_obj, dialect)

    params: list = []
    clauses = [
        f'SELECT MIN({date_expr}) AS "MIN_DATE", MAX({date_expr}) AS "MAX_DATE"',
        f"FROM {_table_name(entity_obj, dialect)}",
    ]
    scope = _scope_condition(entity_obj, scope_value, params)
    if scope is not None:
        clauses.append(f"WHERE {scope}")
    return "\n".join(clauses), params
