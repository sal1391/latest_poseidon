"""worker claim role: poseidon_worker

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-05

Gives the memory-distillation worker's cross-user claim query an explicitly
GRANTED privilege instead of an accident of superuser-ness (Phase 14 Task
3).

**The bug this closes, stated plainly.** ``memory_outbox`` is ``FORCE ROW
LEVEL SECURITY`` with an owner-only policy (migration 0008), and
``poseidon.scripts.memory_worker``'s claim -- "which conversations, across
ALL users, have gone idle" -- is the one query in this system that cannot
be scoped to a single ``app.user_sub``. Until this migration it saw rows
only because this project's compose Postgres bootstraps its
``DATABASE_URL`` role as the cluster superuser, and a Postgres superuser
bypasses row-level security unconditionally. **RDS has no superuser and
cannot grant ``BYPASSRLS``** (a superuser-only attribute; ``rds_superuser``
is not one). On RDS the owner policy would therefore apply to the claim,
``current_setting('app.user_sub', true)`` would be NULL, and the claim
would return ZERO rows on every cycle -- forever, with the worker logging
healthy empty polls and nothing ever being distilled. A silent failure of
an entire feature, on the deployment this codebase is heading for.

**What this migration grants, and what it deliberately does not.**
``poseidon_worker`` gets ``SELECT, UPDATE`` on ``memory_outbox`` and ONE
permissive policy on that table -- and NOTHING on any other table in this
database. Row-level security ORs permissive policies together, so
``memory_outbox_worker``'s ``USING (true)`` admits every row for a session
that has ``SET ROLE``d to ``poseidon_worker``, while 0008's owner policy
keeps behaving exactly as before for every other session; the grant is
required independently of the policy, since RLS only filters rows a
statement is already privileged to touch. ``UPDATE`` is granted alongside
``SELECT`` because the claim is a ``SELECT ... FOR UPDATE SKIP LOCKED``:
row locking is checked against the UPDATE privilege even though the
statement writes nothing. The worker's every OTHER read and write (the
conversation, the memory version, the outbox status transition) still goes
through the ordinary per-user ``rls_transaction``/``poseidon_app`` path
Phase 13 built -- this widens ONE query's visibility, not the worker's
privilege in general.

**No ``poseidon_admin`` policy, still.** Migration 0008's own "Divergence
1" -- doc 05 section 7: "Admins have no path to another user's
``messages``, ``user_memory``, or ``user_profile`` -- those tables are
RLS-scoped with no admin policy, deliberately" -- is untouched here. The
role this migration adds is a MACHINE role for one query, not a human read
surface, and nothing below grants it (or anyone else) a way to read a
user's memory or profile.

**``GRANT poseidon_worker TO CURRENT_USER``, and why it is not optional.**
A Postgres superuser may ``SET ROLE`` to anything; every other role may
only ``SET ROLE`` to a role it is a MEMBER of. On RDS the DSN role is never
a superuser, so without this membership the worker's own ``SET LOCAL ROLE
poseidon_worker`` would raise ``permission denied to set role`` on every
cycle -- the same feature broken a different way. ``CURRENT_USER`` is the
right target rather than a hard-coded name because the role that runs
migrations IS the role that runs the worker in every habitat this project
has (compose: ``poseidon``; EC2/RDS: the master user in ``DATABASE_URL``).
A future deployment that splits those two must grant the membership to its
worker user explicitly -- ``poseidon.core.db.assert_boot_privileges``
refuses to start the worker with a message that says exactly that, rather
than letting it discover the problem one silent cycle at a time.

**Two disclosed divergences from the 0004/0005 role house style.**

1. *Idempotent creation via the ``duplicate_object`` exception rather than
   ``IF NOT EXISTS (SELECT FROM pg_roles ...)``.* Same intent (a
   downgrade/upgrade cycle, or any other deploy that also wants this role,
   must never hit "role already exists"), one fewer failure mode: the
   check-then-act form has a real, if narrow, race between two concurrent
   ``alembic upgrade head`` runs against the same cluster, which the
   exception handler simply does not have. Roles are cluster-scoped, so
   "two deploys, one cluster" is not hypothetical.
2. *The downgrade DROPs the role*, where 0004/0005 deliberately leave
   ``poseidon_app``/``poseidon_admin`` in place. Those two are shared: they
   are referenced by several migrations and by ``DATABASE_APP_ROLE``, so
   dropping either could break some other object that already depends on
   it. ``poseidon_worker`` is created by exactly this migration, used by
   exactly one query, and granted on exactly one table -- it is this
   migration's to own, so leaving it behind would just be litter. The drop
   is still guarded, and a role that cannot be dropped because something
   outside this migration came to depend on it is left alone rather than
   failing the downgrade.

Same no-op-on-non-Postgres guard as every migration since 0002 (see that
module's docstring): roles, policies and grants have no SQLite equivalent,
so this migration does nothing there and ``backend/tests/
test_migrations.py``'s ``alembic upgrade head`` against a throwaway SQLite
database stays green.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_MEMORY_OUTBOX = "memory_outbox"

#: Kept identical to ``poseidon.core.db.WORKER_ROLE`` (which is what
#: ``poseidon.scripts.memory_worker``'s claim and the boot probe both read).
#: A migration must not import application code -- alembic runs it against
#: whatever revision a database is at, not against this checkout's modules
#: -- so the name is spelled out here and pinned by
#: ``tests/test_boot_privileges.py``/``tests/test_memory_worker.py``, both
#: of which fail the moment the two spellings drift apart.
_WORKER_ROLE = "poseidon_worker"

_WORKER_POLICY = "memory_outbox_worker"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Idempotent NOLOGIN role -- see the module docstring's "divergence 1"
    # for why this uses the duplicate_object handler rather than 0004/0005's
    # IF NOT EXISTS check.
    op.execute(
        "DO $$ BEGIN "
        f"CREATE ROLE {_WORKER_ROLE} NOLOGIN; "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )
    # Schema USAGE, mirroring 0004's poseidon_app and 0005's poseidon_admin
    # verbatim. Redundant on a stock Postgres (PUBLIC holds USAGE on schema
    # public by default) and stated anyway, so this role keeps working in a
    # hardened database where that default was revoked.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_WORKER_ROLE}")
    # SELECT *and* UPDATE: the claim is SELECT ... FOR UPDATE SKIP LOCKED,
    # and row locking is checked against UPDATE (module docstring).
    op.execute(f"GRANT SELECT, UPDATE ON {_MEMORY_OUTBOX} TO {_WORKER_ROLE}")
    op.execute(
        f"CREATE POLICY {_WORKER_POLICY} ON {_MEMORY_OUTBOX} "
        f"TO {_WORKER_ROLE} USING (true) WITH CHECK (true)"
    )
    # The membership that makes SET ROLE legal for a non-superuser DSN --
    # the whole reason this migration is what makes the worker work on RDS.
    # See the module docstring's own section on this statement.
    op.execute(f"GRANT {_WORKER_ROLE} TO CURRENT_USER")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Every statement below tolerates the object it targets already being
    # gone, because a downgrade is exactly when that is likeliest: an
    # operator who hit a problem may well have dropped the role or the
    # policy by hand before reaching for `alembic downgrade`. An earlier
    # draft guarded only the DROP ROLE, which meant a hand-removed role
    # aborted this downgrade two statements EARLIER, at the first REVOKE --
    # while the comment claimed it was covered.
    op.execute(f"DROP POLICY IF EXISTS {_WORKER_POLICY} ON {_MEMORY_OUTBOX}")
    # One block for the two REVOKEs: `REVOKE ... FROM <role>` raises
    # undefined_object if the role is gone, and there is nothing to revoke
    # in that case anyway. (`REVOKE` has no IF EXISTS spelling, hence the
    # DO block rather than a flag.)
    op.execute(
        "DO $$ BEGIN "
        f"REVOKE SELECT, UPDATE ON {_MEMORY_OUTBOX} FROM {_WORKER_ROLE}; "
        f"REVOKE USAGE ON SCHEMA public FROM {_WORKER_ROLE}; "
        "EXCEPTION WHEN undefined_object THEN NULL; "
        "END $$"
    )
    # Memberships (the GRANT ... TO CURRENT_USER above, and any a deploy
    # added by hand) are dropped by Postgres along with the role itself, so
    # they need no REVOKE of their own here.
    #
    # Kept as its OWN block, deliberately: a PL/pgSQL block that catches an
    # exception rolls back everything done inside it, so folding the drop in
    # with the revokes above would mean a role that cannot be dropped keeps
    # its grants too. Separated, the privileges always come off even when
    # the role itself has to stay.
    #
    # dependent_objects_still_exist is swallowed rather than raised: it can
    # only mean something OUTSIDE this migration granted this role a
    # privilege, and failing a downgrade -- which would roll back the policy
    # and grant drops above with it -- is a worse answer than leaving a
    # NOLOGIN role with no privileges behind. undefined_object covers a
    # downgrade run twice, or a database where the role was removed by hand.
    op.execute(
        "DO $$ BEGIN "
        f"DROP ROLE {_WORKER_ROLE}; "
        "EXCEPTION WHEN undefined_object THEN NULL; "
        "WHEN dependent_objects_still_exist THEN NULL; "
        "END $$"
    )
