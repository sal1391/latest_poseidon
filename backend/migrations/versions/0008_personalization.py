"""personalization: user_profile, user_memory, memory_outbox

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-04

Creates the three tables doc 05 section 5 names as "personalization data
(owned by the user, injected every turn)": ``user_profile`` (one system-
instruction row per user), ``user_memory`` (an append-only, versioned set of
typed memory entries -- ``current = max(version)`` per user), and
``memory_outbox`` (the durable queue a later phase's idle-triggered worker
claims to distill a finished conversation into a new memory version).
Same ``ENABLE``/``FORCE ROW LEVEL SECURITY`` + one owner policy per table
shape every RLS table since migration 0004 uses (doc 05 section 4, decision
D28: ``USING``/``WITH CHECK`` on ``current_setting('app.user_sub', true)``,
reused verbatim, never re-derived -- see ``poseidon.core.db``'s own module
docstring for the full rationale).

**Divergence 1 -- no ``poseidon_admin`` policy or grant on any of these
three tables, unlike every RLS table since migration 0005.** Doc 05 section
7, verbatim: "Admins have no path to another user's `messages`,
`user_memory`, or `user_profile` -- those tables are RLS-scoped with no
admin policy, deliberately." Migrations 0005/0006 both add a
``poseidon_admin`` ``FOR SELECT ... USING (true)`` policy plus a plain
``GRANT SELECT`` alongside their owner policy, because the run-log/feedback
tables are audit surfaces a named operator may legitimately need to read
across users (cost roll-ups, incident review). A user's personal system
instruction and memory are not an audit surface -- they are exactly the
kind of personal data doc 05 section 7's admin-boundary paragraph draws the
line at. This migration intentionally does NOT copy that block: no
``poseidon_admin`` policy, no ``poseidon_admin`` grant, on ``user_profile``,
``user_memory``, or ``memory_outbox``. A future reader who only skims
0005/0006 and notices this migration's admin-policy block "missing" should
read this paragraph before treating it as a regression to fix -- it is the
point, not an oversight.

**Divergence 2 -- ``user_memory`` gets a ``DELETE`` grant, the first on any
RLS table since migration 0005.** Every other RLS-scoped table in this
codebase (``message_feedback``, ``turn_run``, ``tool_calls``, ``llm_calls``)
deliberately withholds ``DELETE`` from ``poseidon_app`` because each of
those rows is an audit/accountability record: doc 05 section 7's own
governing rule is that an audit trail is redacted, never removed, so
granting ``DELETE`` there would let application code silently defeat that
guarantee at the SQL-privilege level, not just by convention.
``user_memory`` is different in kind: version-retention pruning
(``settings.memory_keep_versions``, enforced by
``poseidon.core.personalization.memory.UserMemory.write_version`` --
deleting versions older than the newest N for that user, immediately after
a new version successfully inserts) is ordinary log-rotation housekeeping
over a user's OWN personal document, not a record anyone is ever
accountable for having produced. Granting ``DELETE`` here does not widen
who can delete what: the owner policy's ``USING`` clause still confines any
``DELETE`` statement to rows whose ``user_sub`` matches the caller's own
identity, exactly like every ``UPDATE``/``INSERT`` grant already made on
every RLS table in this codebase -- this is a different judgment call for a
different kind of row, not a broader exposure. ``user_memory`` gets no
``UPDATE`` grant at all (deliberately the mirror image of the audit tables'
"no DELETE"): a version, once written, is immutable -- amending one would
defeat the whole point of an append-only "a bad distillation is a one-click
restore" design (doc 05 section 5) -- so the only ways a row ever changes
after being written are INSERT (a new version) or DELETE (retention
pruning), never UPDATE.

**Grants, table by table (doc 05 section 5's plan, Global Constraints).**
``user_profile``: ``SELECT, INSERT, UPDATE`` -- no ``DELETE``, since a row
is upserted (``ProfileStore.UserProfile.put``) and never removed by the
app. ``user_memory``: ``SELECT, INSERT, DELETE`` -- no ``UPDATE``, per the
immutable-versions rationale above. ``memory_outbox``: ``SELECT, INSERT,
UPDATE`` -- no ``DELETE``; the only way a row disappears is the
``conversations`` foreign key's ``ON DELETE CASCADE``, the identical
RI-bypasses-RLS mechanism migration 0004's own docstring establishes for
the ``conversations`` -> ``messages`` cascade (extended one hop further by
migration 0006 for ``message_feedback``, and here for a third table).

**Composite primary key on ``user_memory`` (``user_sub``, ``version``),
unlike every other table in this codebase so far.** ``version`` alone is
never globally unique -- every user's own version numbering restarts at 1
-- so, unlike ``conversations``/``messages``/``message_feedback`` (all
keyed by a single globally-unique UUIDv7 ``id``, where relying on RLS alone
to scope a lookup is safe because no two users' rows could ever collide on
that id), a query against ``user_memory`` keyed only on ``version`` WITHOUT
an explicit ``user_sub`` predicate would -- on a connection where RLS is
for any reason not being enforced (the local dev compose role is a
Postgres superuser; see ``poseidon.core.db``'s own module docstring) --
silently return some OTHER user's row at the same version number instead
of failing closed. ``poseidon.core.personalization.memory`` therefore
includes an explicit ``user_sub = :user_sub`` predicate on every
version-keyed query, layered on top of (never a replacement for) RLS
itself -- the same belt-and-suspenders precedent
``poseidon.core.chat.feedback``'s own ``_GET_SQL`` already sets for a
comparably non-globally-unique lookup key.

Same no-op-on-non-Postgres guard as every migration since 0002 (see that
module's docstring): row-level security and Postgres-specific grant DDL
have no SQLite equivalent, so this migration does nothing there and
``backend/tests/test_migrations.py``'s ``alembic upgrade head`` against a
throwaway SQLite database stays green.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_USER_PROFILE = "user_profile"
_USER_MEMORY = "user_memory"
_MEMORY_OUTBOX = "memory_outbox"
_CONVERSATIONS = "conversations"

_APP_ROLE = "poseidon_app"

_TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)
_JSONB = postgresql.JSONB()

# The one predicate every owner policy in this codebase pins verbatim (doc
# 05 section 4, decision D28) -- reused, not re-derived; see
# poseidon.core.db's own module docstring for the full rationale.
_OWNER_PREDICATE = "user_sub = current_setting('app.user_sub', true)"

_OWNER_POLICY_NAME = {
    _USER_PROFILE: "user_profile_owner",
    _USER_MEMORY: "user_memory_owner",
    _MEMORY_OUTBOX: "memory_outbox_owner",
}

_CREATED_BY_CHECK = "created_by in ('user', 'distiller')"
_STATUS_CHECK = "status in ('pending', 'done', 'failed')"


def _user_profile_columns() -> list[sa.Column]:
    """Doc 05 section 5's ``user_profile`` -- fresh list per call (0002's
    own ``_columns()`` precedent: a ``sa.Column`` is single-use once bound
    to a ``Table``)."""
    return [
        sa.Column("user_sub", sa.Text(), primary_key=True),
        sa.Column("system_instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
    ]


def _user_memory_columns() -> list[sa.Column]:
    """Doc 05 section 5's ``user_memory``. Composite primary key
    (``user_sub``, ``version``) -- see the module docstring for why this
    table's own store adds an explicit ``user_sub`` predicate to every
    query, unlike this codebase's UUID-keyed tables."""
    return [
        sa.Column("user_sub", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("entries", _JSONB, nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
    ]


def _memory_outbox_columns() -> list[sa.Column]:
    """Doc 05 section 5's ``memory_outbox``. ``conversation_id`` is both
    the primary key and the foreign key -- one outbox row per conversation,
    ``ON DELETE CASCADE`` so a hard-deleted conversation's outbox row goes
    with it (the same RI-bypasses-RLS cascade migration 0004's own
    docstring establishes)."""
    return [
        sa.Column(
            "conversation_id",
            postgresql.UUID(),
            sa.ForeignKey(f"{_CONVERSATIONS}.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_sub", sa.Text(), nullable=False),
        sa.Column("last_turn_at", _TIMESTAMPTZ, nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", _JSONB, nullable=True),
        sa.Column("updated_at", _TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.create_table(_USER_PROFILE, *_user_profile_columns())
    op.create_table(
        _USER_MEMORY,
        *_user_memory_columns(),
        sa.CheckConstraint(_CREATED_BY_CHECK, name="ck_user_memory_created_by"),
    )
    op.create_table(
        _MEMORY_OUTBOX,
        *_memory_outbox_columns(),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_memory_outbox_status"),
    )

    for table in (_USER_PROFILE, _USER_MEMORY, _MEMORY_OUTBOX):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_OWNER_POLICY_NAME[table]} ON {table} "
            f"USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})"
        )
        # No poseidon_admin policy here -- see the module docstring's
        # "Divergence 1" section. This is deliberate on all three tables.

    # See the module docstring's "Divergence 2" and "Grants" sections for
    # the full per-table rationale -- not repeated here.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_USER_PROFILE} TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, DELETE ON {_USER_MEMORY} TO {_APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON {_MEMORY_OUTBOX} TO {_APP_ROLE}")
    # poseidon_admin gets nothing on any of the three tables above -- no
    # GRANT statement for it appears anywhere in this migration, on purpose.


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON {_MEMORY_OUTBOX} FROM {_APP_ROLE}")
    op.execute(f"REVOKE SELECT, INSERT, DELETE ON {_USER_MEMORY} FROM {_APP_ROLE}")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON {_USER_PROFILE} FROM {_APP_ROLE}")

    for table in (_MEMORY_OUTBOX, _USER_MEMORY, _USER_PROFILE):
        op.execute(f"DROP POLICY {_OWNER_POLICY_NAME[table]} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # memory_outbox first: it holds the foreign key to conversations (no
    # FK to the other two tables in this migration either way, but this
    # keeps drop order symmetric with creation order, table-then-table).
    op.drop_table(_MEMORY_OUTBOX)
    op.drop_table(_USER_MEMORY)
    op.drop_table(_USER_PROFILE)

    # poseidon_app/poseidon_admin themselves are NOT touched -- both are
    # cluster-scoped roles created idempotently by earlier migrations
    # (0004/0005); dropping either here could break some OTHER object in
    # the cluster that already depends on it existing, the same precedent
    # every migration since 0004 sets in its own downgrade().
