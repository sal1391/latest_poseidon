"""``DataClient`` over the local ``synthetic`` Postgres schema.

This is the first concrete implementation of the adapter seam described in
``client.py`` and ``docs/architecture/04-data-ontology.md`` §3. It owns no
SQL of its own: every statement it executes comes from
``poseidon.core.data.query_builder`` rendered on the ``"postgres"`` dialect,
so the certified metric formulas, the half-open period window, the
``COALESCE(...)`` filter semantics and the GL dual-purpose exclusion guard
are all exercised exactly as the snapshot tests pin them. What this module
adds is the three things a builder cannot do: connect, execute with bound
parameters, and shape rows into the typed results callers see
(``MetricResult`` / ``BreakdownResult`` / ``PeriodRange``).

Connection policy: one short-lived ``psycopg`` connection per call, opened
and closed inside the method (the ``with`` block also commits/rolls back the
implicit read transaction, so no idle-in-transaction sessions pile up). A
pool arrives with the phase that needs concurrency; until then a per-call
connection keeps failures isolated and the object trivially thread-safe —
``SyntheticDataClient`` holds nothing but a DSN string.

DSN handling: the environment contract's ``DATABASE_URL`` is a SQLAlchemy URL
(``postgresql+psycopg://…``) because Alembic and SQLAlchemy read the same
variable. libpq does not understand the ``+driver`` suffix, so
:func:`normalize_dsn` strips it — callers can pass the raw ``DATABASE_URL``
value straight through.

Result shaping notes:

- SQL ``SUM()`` over zero rows is ``NULL``, and every ratio metric is guarded
  by ``NULLIF``. Those arrive as ``None`` and stay ``None`` — never coerced
  to ``0.0``, which would claim "the total is zero" when the truth is "there
  is nothing to total".
- ``WIN_RATE``'s ``HAVING`` guard can eliminate the single aggregate row
  entirely; that is treated the same way (every metric ``None``).
- ``list_dimension_values`` caps at :data:`DIMENSION_VALUE_LIMIT` rows. The
  cap is applied at fetch time rather than by slicing a fully materialized
  list, so a pathologically wide dimension can never balloon memory.
"""

from collections.abc import Sequence
from datetime import date

import psycopg

from . import query_builder as qb
from .client import BreakdownResult, BreakdownRow, MetricResult, PeriodRange
from .specs import BreakdownQuerySpec, MetricQuerySpec

DIALECT = "postgres"

# Dimension-value lists feed pickers and LLM disambiguation prompts, both of
# which are useless past a couple of hundred entries; callers that need to
# narrow further pass ``search``.
DIMENSION_VALUE_LIMIT = 200

DEFAULT_CONNECT_TIMEOUT_SECONDS = 10


def normalize_dsn(url: str) -> str:
    """Turn a SQLAlchemy URL into a libpq-compatible one.

    ``postgresql+psycopg://u:p@h/db`` -> ``postgresql://u:p@h/db``. A URL that
    already has no ``+driver`` suffix (or an entirely different scheme, e.g. a
    libpq key/value DSN) is returned unchanged.
    """
    scheme, separator, rest = url.partition("://")
    if not separator or "+" not in scheme:
        return url
    return f"{scheme.split('+', 1)[0]}{separator}{rest}"


def _as_float(value: object) -> float | None:
    """Coerce one aggregate cell to ``float``, preserving SQL ``NULL``.

    Postgres returns ``double precision`` as a Python ``float`` already; the
    conversion exists so a future ``numeric``-typed metric (which psycopg
    hands back as ``Decimal``) still satisfies the ``dict[str, float | None]``
    contract instead of quietly leaking a different numeric type to callers.
    """
    if value is None:
        return None
    return float(value)  # type: ignore[arg-type]


def _as_date(value: object) -> date | None:
    """MIN/MAX of a ``date`` column comes back as ``datetime.date`` already;
    this only guards the NULL case (an empty table) explicitly."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


class SyntheticDataClient:
    """``DataClient`` (structurally — see ``client.DataClient``) for Postgres."""

    def __init__(
        self, dsn: str, connect_timeout: int = DEFAULT_CONNECT_TIMEOUT_SECONDS
    ) -> None:
        self._dsn = normalize_dsn(dsn)
        self._connect_timeout = connect_timeout

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self._dsn, connect_timeout=self._connect_timeout)

    def _fetch(self, sql: str, params: Sequence, limit: int | None = None) -> list[tuple]:
        """Execute one builder-rendered statement and return its rows.

        ``params or None`` matters: psycopg treats an empty sequence as "this
        query is parameterized" and would then try to interpret any literal
        ``%`` in the SQL as a placeholder. The builder's parameterless
        statements (period range) have no ``%`` today, but passing ``None``
        keeps that from becoming a latent trap.
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params or None)
                return cur.fetchall() if limit is None else cur.fetchmany(limit)

    # -- DataClient -------------------------------------------------------

    def list_dimension_values(
        self, entity: str, column: str, search: str | None = None
    ) -> list[str]:
        sql, params = qb.build_dimension_values_query(entity, column, search, DIALECT)
        rows = self._fetch(sql, params, limit=DIMENSION_VALUE_LIMIT)
        return [row[0] for row in rows]

    def available_periods(self, entity: str) -> PeriodRange:
        sql, params = qb.build_period_range_query(entity, DIALECT)
        rows = self._fetch(sql, params)
        # MIN/MAX over an empty table still returns exactly one row, of NULLs.
        start, end = rows[0] if rows else (None, None)
        return PeriodRange(start=_as_date(start), end=_as_date(end))

    def run_metric_query(self, spec: MetricQuerySpec) -> MetricResult:
        sql, params = qb.build_metric_query(spec, DIALECT)
        rows = self._fetch(sql, params)
        # No row at all == the HAVING guard rejected the group; same meaning
        # as an all-NULL aggregate row, so report every metric as unknown.
        cells: Sequence = rows[0] if rows else (None,) * len(spec.metrics)
        return MetricResult(
            entity=spec.entity,
            period=spec.period,
            values={name: _as_float(cell) for name, cell in zip(spec.metrics, cells, strict=True)},
        )

    def run_breakdown_query(self, spec: BreakdownQuerySpec) -> BreakdownResult:
        sql, params = qb.build_breakdown_query(spec, DIALECT)
        rows = self._fetch(sql, params)
        return BreakdownResult(
            entity=spec.entity,
            group_by=spec.group_by,
            rows=[
                BreakdownRow(
                    key=row[0],
                    values={
                        name: _as_float(cell)
                        for name, cell in zip(spec.metrics, row[1:], strict=True)
                    },
                )
                for row in rows
            ],
        )
