import { drizzle, type PostgresJsDatabase } from "drizzle-orm/postgres-js";
import { sql } from "drizzle-orm";
import postgres from "postgres";
import * as schema from "./schema";

type Database = PostgresJsDatabase<typeof schema>;

/**
 * The one frozen statement in this module, named exactly as Python names it
 * (`_SET_IDENTITY_SQL`, backend/poseidon/core/db.py:145) and for the same
 * reason: the text is greppable in one place, and a regression test can assert
 * its properties instead of re-deriving them from an inlined template. It is a
 * function where Python's is a plain constant only because Drizzle's `sql`
 * template binds its parameter at construction time, whereas SQLAlchemy's
 * `text()` carries a named `:sub` placeholder and binds at execution. The
 * parameter is still bound, never interpolated.
 *
 * `set_config(..., true)` -- the trailing `true` is `is_local`, scoping the
 * setting to THIS transaction. Without it the value would leak to whatever
 * request next borrows this pooled connection, which is precisely the bug
 * the parameter exists to prevent. It is deliberately the FIRST statement of
 * the transaction (decision D28), before any role switch.
 */
const SET_IDENTITY_SQL = (sub: string) =>
  sql`SELECT set_config('app.user_sub', ${sub}, true)`;

/**
 * `SET LOCAL ROLE "<role>"` drops to a non-owner, non-BYPASSRLS role, so a
 * forgotten filter returns zero foreign rows instead of leaking them. Python
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

interface RlsClientOptions {
  /** postgres.js pool size. Only `1` is interesting: it pins every query in
   * the instance to ONE physical connection, which is the only vantage point
   * from which `SET_IDENTITY_SQL`'s `is_local` argument is observable. */
  max?: number;
  /** An already-built postgres.js client to use INSTEAD of opening one from
   * `connectionString`. Injection inwards only: this module hands a pool to
   * nobody, so no caller can reach a connection that has skipped `withUser`. */
  sql?: ReturnType<typeof postgres>;
}

interface RlsClient {
  withUser<T>(sub: string, fn: (tx: Database) => Promise<T> | T): Promise<T>;
}

/**
 * Build an identity-scoped client over one connection pool. The pool itself is
 * closed over and never returned -- `withUser` is the only way to reach it,
 * which is the property that makes "every query is RLS-scoped" checkable by
 * reading this file rather than auditing every call site.
 *
 * Port of Python's `rls_transaction` (backend/poseidon/core/db.py:321).
 * `withUser` mirrors it statement for statement, in the same order.
 */
export function createRlsClient(
  connectionString: string,
  options: RlsClientOptions = {},
): RlsClient {
  const client =
    options.sql ??
    (options.max === undefined
      ? postgres(connectionString)
      : postgres(connectionString, { max: options.max }));
  const db = drizzle(client, { schema });

  async function withUser<T>(
    sub: string,
    fn: (tx: Database) => Promise<T> | T,
  ): Promise<T> {
    const role = appRole(); // validated BEFORE any connection is opened
    return db.transaction(async (tx) => {
      await tx.execute(SET_IDENTITY_SQL(sub));
      if (role !== null) {
        await tx.execute(sql.raw(`SET LOCAL ROLE "${role}"`));
      }
      return await fn(tx as unknown as Database);
    });
  }

  return { withUser };
}

const connectionString =
  process.env.DRIZZLE_DATABASE_URL ??
  "postgresql://poseidon:poseidon@localhost:5432/poseidon";

/** The default instance every caller uses. Same signature as before the
 * factory existed, so no call site changes. */
export const { withUser } = createRlsClient(connectionString);
