"""message_feedback: persisted thumbs verdicts with run-log linkage

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-01

Creates ``message_feedback`` (doc 06 section 7, decision D25): the durable
replacement for ``core/chat/history.py``'s honest, in-memory
``FeedbackStubStore`` (that module's own docstring named it a stub
precisely because this table did not exist yet -- "in-memory until Phase
12's message_feedback table lands"). One verdict per user per message
(``UNIQUE (message_id, user_sub)``; the application upserts on conflict --
Task 1's own ``core/chat/feedback.py``), FK'd to both ``messages``
(``ON DELETE CASCADE`` -- a conversation delete cascades to its messages
(migration 0004) and now on to their feedback rows too, the SAME
RI-bypasses-RLS mechanism migration 0004's own docstring established for
the conversations -> messages cascade) and ``turn_run`` (migration 0003;
no ``ON DELETE`` clause -- audit rows are never deleted, doc 05 section 7,
so there is no cascade direction to specify here).

Same row-level-security discipline as every RLS-scoped table before it
(migration 0004's owner policy; migration 0005's admin-read policy):
``ENABLE``/``FORCE ROW LEVEL SECURITY``, one ``ALL`` owner policy on
``current_setting('app.user_sub', true)`` (the identical D28 predicate,
reused verbatim -- see ``poseidon.core.db``'s module docstring), one
``FOR SELECT ... USING (true)`` policy for ``poseidon_admin``. That role
was already created, idempotently, by migration 0005 -- this migration only
adds a new policy and grant referencing it, never re-creates the role
itself. ``poseidon_app`` gets ``SELECT, INSERT, UPDATE`` -- deliberately
never ``DELETE``, mirroring migration 0005's identical choice for
``turn_run``/``llm_calls``/``tool_calls`` and for the identical reason: a
feedback row is amended (upsert), never deleted by the application itself
-- the only way one ever disappears is the ``messages`` FK cascade above,
an RI action that bypasses RLS (and therefore needs no DELETE grant at all)
by design.

``updated_at`` is this migration's one deliberate departure from doc 06
section 7's own DDL block (disclosed there too, in Task 1's own plan): the
"upsert amends" contract needs a way to tell "amended just now" apart from
"created a while ago and never touched again" once ``created_at`` itself is
pinned to stay put across an amend (the doc's own comment: "one verdict per
user per message; upsert amends"). No backfill concern -- this is a brand
new table with no pre-existing rows -- so it is simply declared ``NOT NULL
DEFAULT now()`` from the start, like every other timestamp column in this
codebase.

Same no-op-on-non-Postgres guard as every migration since 0002 (see that
module's docstring): row-level security and Postgres-specific grant DDL have
no SQLite equivalent, so this migration does nothing there and
``backend/tests/test_migrations.py``'s ``alembic upgrade head`` against a
throwaway SQLite database stays green.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_TABLE = "message_feedback"
_MESSAGES = "messages"
_TURN_RUN = "turn_run"

_APP_ROLE = "poseidon_app"
_ADMIN_ROLE = "poseidon_admin"

_OWNER_POLICY = "message_feedback_owner"
_ADMIN_POLICY = "message_feedback_admin_read"

_VERDICT_CHECK = "verdict in ('up', 'down')"
_UNIQUE_NAME = "uq_message_feedback_message_id_user_sub"

_TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)

# The one predicate every owner policy in this codebase pins verbatim (doc
# 05 section 4, decision D28) -- reused, not re-derived; see
# poseidon.core.db's own module docstring for the full rationale.
_OWNER_PREDICATE = "user_sub = current_setting('app.user_sub', true)"


def _columns() -> list[sa.Column]:
    """Doc 06 section 7's ``message_feedback``, fresh list per call (0002's
    own ``_columns()`` precedent: a ``sa.Column`` is single-use once bound to
    a ``Table``). ``updated_at`` is this migration's one additive column --
    see the module docstring."""
    return [
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "message_id",
            postgresql.UUID(),
            sa.ForeignKey(f"{_MESSAGES}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(), sa.ForeignKey(f"{_TURN_RUN}.id"), nullable=False),
        sa.Column("user_sub", sa.Text(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.create_table(
        _TABLE,
        *_columns(),
        sa.CheckConstraint(_VERDICT_CHECK, name="ck_message_feedback_verdict"),
        sa.UniqueConstraint("message_id", "user_sub", name=_UNIQUE_NAME),
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_OWNER_POLICY} ON {_TABLE} "
        f"USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY {_ADMIN_POLICY} ON {_TABLE} FOR SELECT TO {_ADMIN_ROLE} USING (true)"
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_TABLE} TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT ON {_TABLE} TO {_ADMIN_ROLE}")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Dropping the table drops its indexes/policies/constraints automatically
    # (migration 0004's downgrade() precedent) -- only the grants (privileges
    # on an object, not owned BY it the way a policy is) need an explicit
    # REVOKE first, and even that is optional (DROP TABLE would take them
    # with it too); kept explicit for symmetry with upgrade() and so a
    # reader never has to wonder whether it was forgotten.
    op.execute(f"REVOKE SELECT ON {_TABLE} FROM {_ADMIN_ROLE}")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON {_TABLE} FROM {_APP_ROLE}")
    op.execute(f"DROP POLICY {_ADMIN_POLICY} ON {_TABLE}")
    op.execute(f"DROP POLICY {_OWNER_POLICY} ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(_TABLE)

    # poseidon_app/poseidon_admin themselves are NOT touched -- both are
    # cluster-scoped roles created idempotently by earlier migrations
    # (0004/0005); dropping either here could break some OTHER object in the
    # cluster that already depends on it existing, the same precedent every
    # migration since 0004 sets in its own downgrade().
