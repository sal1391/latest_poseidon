"""app role membership: GRANT poseidon_app TO CURRENT_USER

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-05

Makes the per-request role switch this application performs on EVERY
request legal for a DSN that is not a superuser -- Phase 14 Task 3b, the
symmetric twin of 0009's ``GRANT poseidon_worker TO CURRENT_USER``.

**The bug this closes, stated plainly.** Migration 0004 creates
``poseidon_app``, grants it schema ``USAGE`` and table privileges, and
stops there: it never grants MEMBERSHIP in that role to anybody.
``poseidon.core.db.rls_transaction`` -- the one seam every identity-scoped
query in this codebase opens -- runs ``SET LOCAL ROLE "poseidon_app"`` as
the second statement of every transaction whenever ``DATABASE_APP_ROLE``
is set, and **a role that is not a superuser may only ``SET ROLE`` to a
role it is a member of**. This project's compose Postgres bootstraps its
``DATABASE_URL`` role as the cluster SUPERUSER (the official image's
``POSTGRES_USER`` convention), and a superuser may assume any role in the
cluster, granted or not -- so the missing membership is completely
invisible locally. **RDS has no superuser**, so on the deployment this
codebase is heading for the same statement raises ``permission denied to
set role "poseidon_app"``, and it raises it on every request: a 500 per
request, for every user, from the first one.

The one statement below is the whole fix, and it is the same statement
0009 already carries for the worker's role. That symmetry is the point:
the worker role got a membership grant AND a boot-time rehearsal of the
switch, and the app role -- which is used far more often -- had neither.

**Why a new revision rather than an edit to 0004.** 0004 is already
applied on every database this project has, and alembic runs a revision
exactly once: adding the statement to 0004 would fix precisely the
databases that do not need fixing (the ones built after the edit) and no
existing one. A migration that has run is history, not source.

**Why ``CURRENT_USER``, and what a split deployment must do.** Same
reasoning 0009 states for its own grant: the role that runs migrations IS
the role that serves requests in every habitat this project has (compose:
``poseidon``; EC2/RDS: the master user in ``DATABASE_URL``), so
``CURRENT_USER`` names the right grantee without hard-coding one, and a
migration cannot know a name that only ``DATABASE_URL`` knows. A future
deployment that splits those two users must issue ``GRANT poseidon_app TO
<the application's user>`` itself --
``poseidon.core.db.assert_boot_privileges`` refuses to boot with a message
that says exactly that, rather than letting the first request discover it.

**What this migration deliberately does NOT do.** No policies, no table
grants, no role creation: 0004 owns all three for ``poseidon_app`` and
they are correct as they stand. Membership is the only thing missing, so
membership is the only thing here. Nor does it touch ``poseidon_admin``
(0005): nothing in the request path ever ``SET ROLE``s to it, so it has no
equivalent gap -- and inventing a membership grant for a role that needs
none would hand out privileges this task has no reason to hand out.

**Idempotent by nature, so no guard on the way up.** Re-granting a
membership that already exists is a no-op in Postgres, which is what makes
a re-run (or a downgrade/upgrade cycle) safe without the
``duplicate_object`` handler 0009 needs for its ``CREATE ROLE``. The grant
is deliberately NOT guarded against ``poseidon_app`` being absent either:
that role is 0004's, this chain cannot reach here without it, and a
database where it was removed by hand is one where every request would
fail anyway -- failing loudly during ``alembic upgrade`` is the better
place to find out.

Same no-op-on-non-Postgres guard as every migration since 0002 (see that
module's docstring): roles and grants have no SQLite equivalent, so this
migration does nothing there and ``backend/tests/test_migrations.py``'s
``alembic upgrade head`` against a throwaway SQLite database stays green.
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

#: Migration 0004's role, and the conventional value of
#: ``DATABASE_APP_ROLE`` (``poseidon.core.config.Settings.
#: database_app_role``) that ``rls_transaction`` switches to. Spelled out
#: rather than imported for the reason 0009's own constant states: a
#: migration must not import application code, since alembic runs it
#: against whatever revision a database is at, not against this checkout's
#: modules. ``tests/test_migrations.py``'s round-trip and
#: ``tests/test_boot_privileges.py``'s rehearsal both fail if this spelling
#: ever drifts from 0004's.
_APP_ROLE = "poseidon_app"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # The membership that makes SET ROLE legal for a non-superuser DSN --
    # i.e. the one that makes every request work on RDS, where the switch
    # would otherwise raise "permission denied to set role" and the compose
    # superuser hides the gap completely. See the module docstring.
    op.execute(f"GRANT {_APP_ROLE} TO CURRENT_USER")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Unlike 0009 -- whose downgrade DROPs the role it created, and Postgres
    # drops a role's memberships along with it -- this revoke has to be
    # explicit: ``poseidon_app`` is SHARED (0004 creates it and deliberately
    # never drops it, since other objects in the cluster may depend on it),
    # so nothing else here would take the membership away.
    #
    # Guarded for the same reason 0009's revokes are: a downgrade is exactly
    # when an operator is likeliest to have already removed something by
    # hand, ``REVOKE`` has no ``IF EXISTS`` spelling, and it raises
    # ``undefined_object`` for a role that is gone -- in which case there is
    # nothing to revoke anyway.
    #
    # It cannot distinguish the membership this migration's upgrade() granted
    # from an identical one granted by hand, and revokes either. That is the
    # honest reverse of the one statement above, not a surprise: a downgrade
    # is an operator-initiated step, and a deployment that granted this
    # membership itself re-grants it (or, more simply, upgrades again).
    op.execute(
        "DO $$ BEGIN "
        f"REVOKE {_APP_ROLE} FROM CURRENT_USER; "
        "EXCEPTION WHEN undefined_object THEN NULL; "
        "END $$"
    )
