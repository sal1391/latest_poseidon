"""``report_run`` storage (Phase 17 D65, D69).

Drizzle owns the schema (D58); this module only reads and writes it with
plain SQL. ``report_run`` is a shared org document: no row policy, so any
Poseidon:Sales user can read any run. Every method still runs inside
:func:`poseidon.core.db.rls_transaction` so the identity is set and
``SET LOCAL ROLE`` behaves like every other store.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.db import rls_transaction


@dataclass(frozen=True)
class NewRun:
    id: str  # uuid7 string, minted by the skill
    perspective: str  # customer | supplier | book
    office_column: str | None
    office_value: str | None
    period_current: PeriodWindow
    period_prior: PeriodWindow
    window_kind: str  # complete | mtd
    basis: str  # prior_month
    status: str  # ok | error
    payload_version: int
    payload: dict
    html: bytes | None
    pdf: bytes | None
    narrative_grounded: bool
    source: str  # synthetic | snowflake
    created_by: str  # user sub
    conversation_id: str | None
    generated_at: datetime  # tz-aware


@dataclass(frozen=True)
class ReportRun:
    """What ``get_run`` returns. Carries no html/pdf bytes."""

    id: str
    definition_id: str | None
    perspective: str
    office_column: str | None
    office_value: str | None
    period_current: PeriodWindow
    period_prior: PeriodWindow
    window_kind: str
    basis: str
    status: str
    payload_version: int
    payload: dict
    narrative_grounded: bool
    source: str
    created_by: str
    conversation_id: str | None
    superseded_by: str | None
    generated_at: datetime
    created_at: datetime


_INSERT_RUN_SQL = text(
    "INSERT INTO report_run ("
    "id, perspective, office_column, office_value, "
    "period_current_start, period_current_end, period_prior_start, period_prior_end, "
    "window_kind, basis, status, payload_version, payload, html, pdf, "
    "narrative_grounded, source, created_by, conversation_id, generated_at"
    ") VALUES ("
    ":id, :perspective, :office_column, :office_value, "
    ":cur_start, :cur_end, :prior_start, :prior_end, "
    ":window_kind, :basis, :status, :payload_version, CAST(:payload AS jsonb), :html, :pdf, "
    ":narrative_grounded, :source, :created_by, :conversation_id, :generated_at)"
)

_SELECT_RUN_SQL = text(
    "SELECT id, definition_id, perspective, office_column, office_value, "
    "period_current_start, period_current_end, period_prior_start, period_prior_end, "
    "window_kind, basis, status, payload_version, payload, narrative_grounded, source, "
    "created_by, conversation_id, superseded_by, generated_at, created_at "
    "FROM report_run WHERE id = :id"
)

# The column is chosen from this fixed map, never from the caller's string.
_BYTES_COLUMNS = {"html": "html", "pdf": "pdf"}
_SELECT_BYTES_SQL = {
    kind: text(f"SELECT {column} FROM report_run WHERE id = :id")
    for kind, column in _BYTES_COLUMNS.items()
}


def _parse_uuid(value: str) -> uuid.UUID | None:
    """``value`` as a :class:`uuid.UUID`, or ``None`` if it is not one; a
    malformed id can never match a row, so it is treated like an absent one."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _opt_str(value) -> str | None:
    return None if value is None else str(value)


class ReportStore:
    def __init__(self, engine: Engine, app_role: str | None = None) -> None:
        self._engine = engine
        self._app_role = app_role

    def insert_run(self, user_sub: str, run: NewRun) -> None:
        params = {
            "id": run.id,
            "perspective": run.perspective,
            "office_column": run.office_column,
            "office_value": run.office_value,
            "cur_start": run.period_current.start,
            "cur_end": run.period_current.end,
            "prior_start": run.period_prior.start,
            "prior_end": run.period_prior.end,
            "window_kind": run.window_kind,
            "basis": run.basis,
            "status": run.status,
            "payload_version": run.payload_version,
            "payload": json.dumps(run.payload),
            "html": run.html,
            "pdf": run.pdf,
            "narrative_grounded": run.narrative_grounded,
            "source": run.source,
            "created_by": run.created_by,
            "conversation_id": run.conversation_id,
            "generated_at": run.generated_at,
        }
        with rls_transaction(self._engine, user_sub, app_role=self._app_role) as conn:
            conn.execute(_INSERT_RUN_SQL, params)

    def get_run(self, user_sub: str, run_id: str) -> ReportRun | None:
        parsed = _parse_uuid(run_id)
        if parsed is None:
            return None
        with rls_transaction(self._engine, user_sub, app_role=self._app_role) as conn:
            row = conn.execute(_SELECT_RUN_SQL, {"id": parsed}).mappings().first()
        if row is None:
            return None
        return ReportRun(
            id=str(row["id"]),
            definition_id=_opt_str(row["definition_id"]),
            perspective=row["perspective"],
            office_column=row["office_column"],
            office_value=row["office_value"],
            period_current=PeriodWindow(row["period_current_start"], row["period_current_end"]),
            period_prior=PeriodWindow(row["period_prior_start"], row["period_prior_end"]),
            window_kind=row["window_kind"],
            basis=row["basis"],
            status=row["status"],
            payload_version=row["payload_version"],
            payload=row["payload"],
            narrative_grounded=row["narrative_grounded"],
            source=row["source"],
            created_by=row["created_by"],
            conversation_id=_opt_str(row["conversation_id"]),
            superseded_by=_opt_str(row["superseded_by"]),
            generated_at=row["generated_at"],
            created_at=row["created_at"],
        )

    def get_bytes(self, user_sub: str, run_id: str, kind: Literal["html", "pdf"]) -> bytes | None:
        if kind not in _SELECT_BYTES_SQL:
            raise ValueError(f"kind must be 'html' or 'pdf', got {kind!r}")
        statement = _SELECT_BYTES_SQL[kind]
        parsed = _parse_uuid(run_id)
        if parsed is None:
            return None
        with rls_transaction(self._engine, user_sub, app_role=self._app_role) as conn:
            value = conn.execute(statement, {"id": parsed}).scalar_one_or_none()
        return None if value is None else bytes(value)
