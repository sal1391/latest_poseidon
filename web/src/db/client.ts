import { drizzle } from "drizzle-orm/postgres-js";
import { sql } from "drizzle-orm";
import postgres from "postgres";
import * as schema from "./schema";

const connectionString =
  process.env.DRIZZLE_DATABASE_URL ??
  "postgresql://poseidon:poseidon@localhost:5432/poseidon";

const client = postgres(connectionString);
const db = drizzle(client, { schema });

/**
 * Port of Python's `rls_transaction` (backend/poseidon/core/db.py:321).
 * Mirrors it statement for statement, in the same order.
 *
 * `set_config(..., true)` -- the trailing `true` is `is_local`, scoping the
 * setting to THIS transaction. Without it the value would leak to whatever
 * request next borrows this pooled connection, which is precisely the bug
 * the parameter exists to prevent. It is deliberately the FIRST statement of
 * the transaction (decision D28), before any role switch.
 *
 * `SET LOCAL ROLE "<role>"` then drops to a non-owner, non-BYPASSRLS role, so
 * a forgotten filter returns zero foreign rows instead of leaking them. Python
 * takes this role from `Settings.database_app_role`, which DEFAULTS to
 * "poseidon_app" and treats an empty string as "no role switch" -- so this
 * mirrors that: `DATABASE_APP_ROLE` env var, same default, empty means skip.
 *
 * The role name is INTERPOLATED, not bound: `SET ROLE` accepts no bind
 * parameter, unlike `set_config`. Python guards that with `_validate_app_role`
 * against `[a-z_][a-z0-9_]{0,62}` before building any SQL; this does the same.
 * Python also double-quotes the identifier, and so does this.
 */
const APP_ROLE_PATTERN = /^[a-z_][a-z0-9_]{0,62}$/;

function appRole(): string | null {
  const raw = process.env.DATABASE_APP_ROLE ?? "poseidon_app";
  if (raw === "") return null; // the ergonomic "unset", same as Python
  if (!APP_ROLE_PATTERN.test(raw)) {
    throw new Error(
      `DATABASE_APP_ROLE=${JSON.stringify(raw)} is not a valid Postgres role identifier ` +
        "(expected [a-z_][a-z0-9_]{0,62}) -- refusing to interpolate it into SQL",
    );
  }
  return raw;
}

export async function withUser<T>(
  sub: string,
  fn: (tx: typeof db) => Promise<T> | T,
): Promise<T> {
  const role = appRole(); // validated BEFORE any connection is opened
  return db.transaction(async (tx) => {
    await tx.execute(sql`SELECT set_config('app.user_sub', ${sub}, true)`);
    if (role !== null) {
      await tx.execute(sql.raw(`SET LOCAL ROLE "${role}"`));
    }
    return await fn(tx as unknown as typeof db);
  });
}
