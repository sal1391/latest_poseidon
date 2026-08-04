"""message_feedback.verdict: nullable, so a recorded vote can be cleared

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-04

Live-testing follow-up to Phase 12 (migration 0006), not part of that
phase's own plan: the product owner found no way to toggle an already-
recorded thumb back to no-vote/neutral. The design that motivated this
migration deliberately keeps 0006's own D25 decision untouched -- the app
role still has NO DELETE grant on ``message_feedback`` (removal only via the
``messages`` FK cascade, migration 0006's own docstring) -- so "un-vote"
cannot be a row delete. Instead it becomes an ordinary upsert that writes
``verdict = NULL`` into the SAME row, keyed by the table's existing
``(message_id, user_sub)`` unique constraint exactly like every other amend
(0006's own "upsert amends" contract). That upsert is impossible today only
because ``verdict`` is ``NOT NULL`` -- this migration drops exactly that,
and nothing else.

**Why ``ck_message_feedback_verdict`` (0006's own ``verdict in ('up',
'down')`` CHECK) needs no change.** Postgres evaluates a ``CHECK`` against
each row's own column values with three-valued logic: a NULL operand makes
``verdict in ('up', 'down')`` evaluate to NULL, not FALSE, and ``CHECK``
only ever REJECTS a row on an explicit FALSE -- a NULL result passes,
identically to how ``comment IS NULL`` already passes today for a verdict
with no comment. So a bare ``ALTER COLUMN verdict DROP NOT NULL`` is
sufficient on its own; there is no CHECK-constraint edit to make alongside
it, and this migration makes none.

**Everything else about the row stays exactly as 0006 declared it.** RLS
(``ENABLE``/``FORCE ROW LEVEL SECURITY``, the owner/admin-read policies),
the ``poseidon_app``/``poseidon_admin`` grants, the ``UNIQUE (message_id,
user_sub)`` constraint, both foreign keys -- none of them reference
``verdict`` at all, so none of them need touching for this column alone to
become nullable.

Same no-op-on-non-Postgres guard as every migration since 0002 (see that
module's docstring): a plain ``ALTER COLUMN ... DROP NOT NULL`` has a SQLite
equivalent in principle, but this project's SQLite path (``backend/tests/
test_migrations.py``'s throwaway ``alembic upgrade head`` smoke test) never
creates ``message_feedback`` with a NOT NULL ``verdict`` to begin with --
0006's own ``upgrade()`` already returns before ``create_table`` on that
dialect -- so there is nothing for this migration to alter there either.

``downgrade()`` restores ``NOT NULL`` -- this will fail if any row's
``verdict`` is NULL at that moment (a genuinely cleared vote persisted since
this migration's own ``upgrade()`` ran). This is the same inherent, accepted
downgrade risk migration 0005's own ``downgrade()`` already discloses for
``tool_calls.args`` -- tightening a constraint that live data may have since
relied on being relaxed is not worked around here, since doing so would mean
silently discarding the very un-vote state this migration exists to
support.
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

_TABLE = "message_feedback"
_COLUMN = "verdict"
_TEXT = sa.Text()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.alter_column(_TABLE, _COLUMN, existing_type=_TEXT, nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # See the module docstring's final paragraph: fails if any row already
    # has verdict IS NULL, an inherent and accepted downgrade risk once real
    # cleared-vote data exists -- not worked around here.
    op.alter_column(_TABLE, _COLUMN, existing_type=_TEXT, nullable=False)
