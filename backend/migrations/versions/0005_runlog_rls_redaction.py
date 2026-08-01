"""run-log rls, admin read role, and redaction support

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-01

Brings ``turn_run``/``llm_calls``/``tool_calls`` (migration 0003) under the
same row-level-security discipline migration 0004 gave ``conversations``/
``messages`` (doc 05 section 4, decision D28). Doc 05 section 4 always named
these three tables in the RLS-scoped set; migration 0004's own docstring
explicitly deferred them ("out of scope for Phase 10 Task 1 ... left for a
later task rather than folded in here as a drive-by"). This is that later
task -- same ``ENABLE``/``FORCE ROW LEVEL SECURITY``, same one-``ALL``-
policy-per-table shape (``USING``/``WITH CHECK`` on ``current_setting(
'app.user_sub', true)``), for the identical reasons 0004's own docstring
already gives (see that module for the full rationale, not repeated here).

**Two things 0004 did not need that this migration adds.**

1. **The admin read role (doc 05 section 7).** ``poseidon_admin`` -- NOLOGIN,
   idempotent-created exactly like 0004's own ``poseidon_app`` (cluster-
   scoped; see that migration's downgrade() docstring for why idempotent
   creation matters) -- gets one ``FOR SELECT ... USING (true)`` policy per
   run-log table plus a plain ``GRANT SELECT``. Row-level security combines
   multiple PERMISSIVE policies on the same table with OR, so this policy
   does not need to (and must not) replace the owner policy: for a caller
   granted membership in ``poseidon_admin`` and ``SET ROLE``'d to it, the
   owner policy's own predicate evaluates false (nothing sets ``app.
   user_sub`` to an admin session's identity) while this policy's
   ``USING (true)`` evaluates true for every row, and ``false OR true`` still
   admits the row -- an admin session sees every user's data without ever
   needing ``BYPASSRLS`` (doc 05 section 7: "no BYPASSRLS anywhere ... never
   granted to the runtime role"). ``GRANT SELECT`` is still required
   independently of the policy: RLS only filters ROWS an already-permitted
   statement may see, it grants no privilege of its own. This role is never
   granted to ``poseidon_app`` and is granted to named human operators only
   (doc 05 section 7: "granted to named operators, never to the
   application's runtime role, and never to a chat user") -- outside this
   migration's own scope, a per-environment operational step.
2. **Redaction support (doc 05 section 7's deletion contract).**
   ``DELETE /api/conversations/{id}`` hard-deletes a conversation's content
   but retains its ``turn_run``/``tool_calls`` rows with payload columns
   cleared and a ``redacted_at`` stamp (``poseidon.core.runlog.redact_turns_
   for_conversation`` is the runtime half; this migration is the schema
   half). ``turn_run`` gains a new, nullable ``redacted_at timestamptz``
   column. ``tool_calls.args`` -- ``NOT NULL`` since migration 0003, because
   doc 06 section 1 calls it "verbatim... always provided" at WRITE time --
   has that constraint DROPPED: doc 05 section 7's redaction contract
   requires it nullable at DELETE time, and the plan records this exact
   tension, and its resolution (the redaction contract wins), as deliberate.
   ``turn_run.question``/``answer_summary`` were already nullable (migration
   0003) and need no column change to hold ``NULL`` post-redaction.
   ``turn_run.parsed`` stays ``NOT NULL`` (never altered here): redacting it
   means resetting it to ``'{}'::jsonb`` -- the SAME sentinel migration 0003
   already uses for "no parsed content" (its own docstring: "{} for
   non-chat kinds") -- never SQL ``NULL``, so no ``DROP NOT NULL`` is needed
   or wanted for this column. ``llm_calls`` carries no payload columns at
   all (doc 06 section 1's own schema), so it needs no column change either
   -- doc 05 section 7 names it as untouched by redaction on purpose.

**``poseidon_app`` grants tighten, not widen.** Unlike 0004's ``poseidon_app``
grant on ``conversations``/``messages`` (all four DML verbs -- those are
user-owned CONTENT tables where hard delete-by-owner is a real, intended
operation), this migration grants only ``SELECT, INSERT, UPDATE`` on the
three run-log tables -- deliberately never ``DELETE``. Doc 05 section 7: "the
audit trail keeps its shape... and loses its content" -- an audit row is
redacted, never removed, and revoking DELETE at the SQL privilege level
enforces that independently of (and in addition to) whatever application
code does or forgets to do.

Same no-op-on-non-Postgres guard as every migration since 0002 (see that
module's docstring): row-level security, roles, and grants have no SQLite
equivalent, so this migration does nothing there and ``backend/tests/
test_migrations.py``'s ``alembic upgrade head`` against a throwaway SQLite
database stays green.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_TURN_RUN = "turn_run"
_LLM_CALLS = "llm_calls"
_TOOL_CALLS = "tool_calls"
_RUN_LOG_TABLES = (_TURN_RUN, _LLM_CALLS, _TOOL_CALLS)

_APP_ROLE = "poseidon_app"
_ADMIN_ROLE = "poseidon_admin"

_TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)
_JSONB = postgresql.JSONB()

# Same predicate 0004 pins verbatim, on the same missing_ok('app.user_sub')
# GUC (poseidon.core.db's own module docstring has the full rationale) --
# reused here rather than re-derived, since it is the identical D28 contract
# applied to three more tables, not a new one.
_OWNER_PREDICATE = "user_sub = current_setting('app.user_sub', true)"

_OWNER_POLICY_NAME = {
    _TURN_RUN: "turn_run_owner",
    _LLM_CALLS: "llm_calls_owner",
    _TOOL_CALLS: "tool_calls_owner",
}
_ADMIN_POLICY_NAME = {
    _TURN_RUN: "turn_run_admin_read",
    _LLM_CALLS: "llm_calls_admin_read",
    _TOOL_CALLS: "tool_calls_admin_read",
}


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in _RUN_LOG_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {_OWNER_POLICY_NAME[table]} ON {table} "
            f"USING ({_OWNER_PREDICATE}) WITH CHECK ({_OWNER_PREDICATE})"
        )

    # poseidon_admin: idempotent NOLOGIN role -- same "CREATE ROLE has no IF
    # NOT EXISTS, and this role is cluster-scoped" reasoning as 0004's own
    # poseidon_app (see that migration's upgrade() comment) -- a
    # downgrade/upgrade cycle, or any later migration/deploy that also wants
    # this role, must never hit "role already exists".
    op.execute(
        "DO $$ BEGIN "
        f"IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{_ADMIN_ROLE}') THEN "
        f"CREATE ROLE {_ADMIN_ROLE} NOLOGIN; "
        "END IF; "
        "END $$"
    )
    for table in _RUN_LOG_TABLES:
        op.execute(
            f"CREATE POLICY {_ADMIN_POLICY_NAME[table]} ON {table} "
            f"FOR SELECT TO {_ADMIN_ROLE} USING (true)"
        )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_ADMIN_ROLE}")
    op.execute(f"GRANT SELECT ON {_TURN_RUN}, {_LLM_CALLS}, {_TOOL_CALLS} TO {_ADMIN_ROLE}")

    # Redaction support (doc 05 section 7) -- see the module docstring's
    # numbered item 2 for the full column-by-column rationale.
    op.add_column(_TURN_RUN, sa.Column("redacted_at", _TIMESTAMPTZ, nullable=True))
    op.alter_column(_TOOL_CALLS, "args", existing_type=_JSONB, nullable=True)

    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON {_TURN_RUN}, {_LLM_CALLS}, {_TOOL_CALLS} "
        f"TO {_APP_ROLE}"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        f"REVOKE SELECT, INSERT, UPDATE ON {_TURN_RUN}, {_LLM_CALLS}, {_TOOL_CALLS} "
        f"FROM {_APP_ROLE}"
    )

    # Restoring NOT NULL fails if any row was ever redacted (args set to
    # NULL) since this migration's own upgrade() ran -- an inherent, accepted
    # downgrade risk once real redacted data exists, no different in kind
    # from any downgrade that tightens a constraint live data may have since
    # violated; not worked around here (doing so would mean silently
    # discarding the very redaction this migration exists to support).
    op.alter_column(_TOOL_CALLS, "args", existing_type=_JSONB, nullable=False)
    op.drop_column(_TURN_RUN, "redacted_at")

    op.execute(f"REVOKE SELECT ON {_TURN_RUN}, {_LLM_CALLS}, {_TOOL_CALLS} FROM {_ADMIN_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_ADMIN_ROLE}")
    for table in _RUN_LOG_TABLES:
        op.execute(f"DROP POLICY {_ADMIN_POLICY_NAME[table]} ON {table}")
        op.execute(f"DROP POLICY {_OWNER_POLICY_NAME[table]} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # poseidon_admin/poseidon_app themselves are NOT dropped -- cluster-
    # scoped roles, the identical precedent migration 0004's own downgrade()
    # sets: dropping either here could break some OTHER object in the
    # cluster that already depends on it existing, and idempotent creation
    # in upgrade() exists specifically so a downgrade/upgrade cycle never
    # trips "role already exists".
