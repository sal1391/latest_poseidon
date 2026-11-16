"""``brief_run`` storage (D83).

Drizzle owns the schema (D58); this module only reads and writes it with
plain SQL. ``brief_run`` is a shared org document like ``report_run``: no row
policy, so any Poseidon:Sales user can read any brief. Every method still runs
inside :func:`poseidon.core.db.rls_transaction` so the identity is set and
``SET LOCAL ROLE`` behaves like every other store.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Engine

from poseidon.core.db import rls_transaction


@dataclass(frozen=True)
class NewBrief:
    id: str  # uuid7 string, minted by the skill
    kind: str  # existing_customer | new_prospect
    subject: str
    title: str
    anchor_date: date
    status: str  # ok
    markdown: str
    html: bytes | None
    pdf: bytes | None
    pdf_error: str | None
    created_by: str  # user sub
    conversation_id: str | None
    generated_at: datetime  # tz-aware


_INSERT_BRIEF_SQL = text(
    "INSERT INTO brief_run ("
    "id, kind, subject, title, anchor_date, status, markdown, html, pdf, pdf_error, "
    "created_by, conversation_id, generated_at"
    ") VALUES ("
    ":id, :kind, :subject, :title, :anchor_date, :status, :markdown, :html, :pdf, :pdf_error, "
    ":created_by, :conversation_id, :generated_at)"
)

# The column is chosen from this fixed map, never from the caller's string.
_BYTES_COLUMNS = {"html": "html", "pdf": "pdf"}
_SELECT_BYTES_SQL = {
    kind: text(f"SELECT {column} FROM brief_run WHERE id = :id")
    for kind, column in _BYTES_COLUMNS.items()
}


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    """``value`` as a :class:`uuid.UUID`, or ``None`` if it is not one."""
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


class BriefStore:
    def __init__(self, engine: Engine, app_role: str | None = None) -> None:
        self._engine = engine
        self._app_role = app_role

    def insert_brief(self, user_sub: str, brief: NewBrief) -> None:
        params = {
            "id": brief.id,
            "kind": brief.kind,
            "subject": brief.subject,
            "title": brief.title,
            "anchor_date": brief.anchor_date,
            "status": brief.status,
            "markdown": brief.markdown,
            "html": brief.html,
            "pdf": brief.pdf,
            "pdf_error": brief.pdf_error,
            "created_by": brief.created_by,
            # the column is a uuid; a chat id that is not one is stored NULL
            "conversation_id": _parse_uuid(brief.conversation_id),
            "generated_at": brief.generated_at,
        }
        with rls_transaction(self._engine, user_sub, app_role=self._app_role) as conn:
            conn.execute(_INSERT_BRIEF_SQL, params)

    def get_bytes(
        self, user_sub: str, brief_id: str, kind: Literal["html", "pdf"]
    ) -> bytes | None:
        if kind not in _SELECT_BYTES_SQL:
            raise ValueError(f"kind must be 'html' or 'pdf', got {kind!r}")
        statement = _SELECT_BYTES_SQL[kind]
        parsed = _parse_uuid(brief_id)
        if parsed is None:
            return None
        with rls_transaction(self._engine, user_sub, app_role=self._app_role) as conn:
            value = conn.execute(statement, {"id": parsed}).scalar_one_or_none()
        return None if value is None else bytes(value)
