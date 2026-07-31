"""chat history + row-level security

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-31

Creates ``conversations``/``messages`` (doc 05 section 6's per-user chat
history model) and locks them down with row-level security (doc 05 section
4, decision D28): ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` on both tables,
one owner policy per table (``USING``/``WITH CHECK`` on
``current_setting('app.user_sub', true)``), and the ``poseidon_app`` role
migration 0004 is the deploy posture for -- a non-owner, non-``BYPASSRLS``
role a real deploy's application connection can eventually run as, so a
forgotten filter returns zero foreign rows instead of leaking (doc 05
section 4's last bullet). ``poseidon.core.db.rls_transaction`` is the
runtime half of D28 -- this migration is the schema half.

**Scope.** THIS migration is ``conversations``/``messages`` only. Doc 05
section 4 names ``turn_run``/``llm_calls``/``tool_calls`` (migration 0003)
and doc 05 section 5's personalization tables among the RLS-scoped set too,
but those are explicitly out of scope for Phase 10 Task 1 (see the phase
plan) -- extending RLS to the run-log tables touches a schema this
migration does not own, and is left for a later task rather than folded in
here as a drive-by.

**One policy per table, not four.** The task brief allows either "one
policy per table covering all commands via USING+WITH CHECK" or "four named
per-command policies"; this migration picks the former (also what doc 05
section 4's own SQL block shows) -- ``conversations_owner``/
``messages_owner`` each apply to every command (Postgres's default when a
``CREATE POLICY`` carries no ``FOR SELECT|INSERT|UPDATE|DELETE`` clause), so
one ``pg_policies`` row per table already answers "does the pinned
predicate apply to every DML command", the same fact four narrower policies
would only prove split four ways. ``test_rls_policies.py``'s catalog test
asserts this exact shape (``cmd = 'ALL'``).

**Ids get no server-side default,** matching migration 0003's own tables:
``poseidon.core.util.uuid7.uuid7()`` mints every ``conversations.id`` /
``messages.id`` in Python before the insert, so id time-ordering is a
property of the APPLICATION's clock, not Postgres's -- unlike 0003 (which
fell back to ``uuid4()`` because no UUIDv7 generator existed yet, see that
migration's own docstring), this is now the real thing.

Same no-op-on-non-Postgres guard as 0002/0003 (see 0002's docstring):
row-level security, ``current_setting``, and Postgres-specific role/grant
DDL have no SQLite equivalent, so this migration does nothing there and
``backend/tests/test_migrations.py``'s ``alembic upgrade head`` against a
throwaway SQLite database stays green.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_CONVERSATIONS = "conversations"
_MESSAGES = "messages"
_APP_ROLE = "poseidon_app"

_MODE_CHECK = "mode in ('existing', 'prospect', 'default')"
_ROLE_CHECK = "role in ('user', 'assistant', 'system')"

_TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)

# The one predicate every owner policy pins (doc 05 section 4, decision
# D28): ``missing_ok=true`` so an unset ``app.user_sub`` reads as NULL and
# the comparison is never TRUE, rather than raising -- see
# ``poseidon.core.db``'s module docstring for the full rationale.
_OWNER_PREDICATE = "user_sub = current_setting('app.user_sub', true)"


def _conversations_columns() -> list[sa.Column]:
    """Doc 05 section 6's ``conversations`` -- fresh list per call (matching
    0002/0003's ``_columns()`` pattern: a ``sa.Column`` is single-use once
    bound to a ``Table``)."""
    return [
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("user_sub", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default="New chat"),
        sa.Column("mode", sa.Text(), server_default="default"),
        sa.Column(
            "state", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    ]


def _messages_columns() -> list[sa.Column]:
    """Doc 05 section 6's ``messages``. ``turn_id`` carries no foreign key
    to ``turn_run`` -- doc 05's own SQL declares none either, so none is
    invented here (the same "do not add a constraint the doc does not
    specify" discipline 0003's docstring states for the reverse direction).
    """
    return [
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(),
            sa.ForeignKey(f"{_CONVERSATIONS}.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_sub", sa.Text(), nullable=False),
        sa.Column("role", sa.Text()),
        sa.Column("parts", postgresql.JSONB(), nullable=False),
        sa.Column("turn_id", postgresql.UUID(), nullable=True),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.create_table(
        _CONVERSATIONS,
        *_conversations_columns(),
        sa.CheckConstraint(_MODE_CHECK, name="ck_conversations_mode"),
    )
    op.create_table(
        _MESSAGES,
        *_messages_columns(),
        sa.CheckConstraint(_ROLE_CHECK, name="ck_messages_role"),
    )

    # Both indexes carry an explicit DESC/ASC shape pinned by doc 05 section
    # 6's own SQL (cursor pagination on exactly these columns, in exactly
    # this order) -- raw DDL rather than `op.create_index`'s plain
    # column-name list, which has no per-column direction of its own.
    op.execute(
        "CREATE INDEX ix_conversations_user_recency "
        "ON conversations (user_sub, updated_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_messages_conversation_order "
        "ON messages (conversation_id, created_at, id)"
    )

    op.execute(f"ALTER TABLE {_CONVERSATIONS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_CONVERSATIONS} FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_MESSAGES} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_MESSAGES} FORCE ROW LEVEL SECURITY")

    op.execute(
        f"CREATE POLICY conversations_owner ON {_CONVERSATIONS} "
        f"USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})"
    )
    op.execute(
        f"CREATE POLICY messages_owner ON {_MESSAGES} "
        f"USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})"
    )

    # Idempotent role creation: `CREATE ROLE` has no `IF NOT EXISTS` clause,
    # and this role is cluster-scoped (see downgrade()'s comment) -- a
    # downgrade/upgrade cycle, or any later migration/deploy that also
    # wants this role, must never hit "role already exists".
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'poseidon_app') THEN "
        "CREATE ROLE poseidon_app NOLOGIN; "
        "END IF; "
        "END $$"
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}")
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_CONVERSATIONS}, {_MESSAGES} TO {_APP_ROLE}"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Dropping a table drops its indexes, policies, and any grants scoped
    # to it -- Postgres removes all three automatically, so there is
    # nothing to reverse here except the tables themselves. `messages`
    # first: it holds the foreign key to `conversations`.
    op.drop_table(_MESSAGES)
    op.drop_table(_CONVERSATIONS)

    # poseidon_app itself, and its schema-level USAGE grant, are
    # deliberately NOT touched: the role is cluster-scoped (CREATE ROLE,
    # not scoped to this schema or database), created idempotently in
    # upgrade() specifically so a downgrade/upgrade cycle -- or a later
    # migration that also wants this role -- never trips over "role
    # already exists". Dropping it here could break any OTHER object in
    # the cluster that already depends on it existing.
