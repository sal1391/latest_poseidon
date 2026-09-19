import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { eq, sql } from "drizzle-orm";
import postgres from "postgres";
import { conversations } from "./schema";
import { createRlsClient, withUser } from "./client";

// Requires the compose `db` service. Skipped when DRIZZLE_DATABASE_URL is unset.
const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;

live("withUser", () => {
  let aliceConversationId: string;
  let bobConversationId: string;

  // A second, deliberately UNWRAPPED connection -- the control the isolation
  // assertions below need in order to mean anything. This project's compose
  // DSN authenticates as the cluster's superuser (`rolsuper` AND
  // `rolbypassrls`), and Postgres exempts such a role from row-level security
  // unconditionally: no schema-level override exists, not even FORCE ROW
  // LEVEL SECURITY. So "Bob cannot see Alice's row" is only evidence once the
  // same query, over the same DSN, WITHOUT withUser's SET LOCAL ROLE, has been
  // shown to see it. That is exactly the trap the Python wrapper's "round-0
  // correction" documents (backend/poseidon/core/db.py, module docstring).
  let unwrapped: ReturnType<typeof postgres>;

  // ONE physical connection (`max: 1`), injected into its own client instance,
  // so that a query issued after a committed `withUser` is guaranteed to be the
  // next borrower of the connection that transaction just used. See the
  // `is_local` test below for why that is the only vantage point from which the
  // transaction-scoping of the identity GUC is observable at all.
  let pinned: ReturnType<typeof postgres>;
  let pinnedClient: ReturnType<typeof createRlsClient>;

  // One row per user, inserted by this file rather than read out of the
  // synthetic seed, so the visibility assertions below hold against an empty
  // database too. The INSERTs are themselves subject to conversations_owner's
  // WITH CHECK, so each one only succeeds under its own sub.
  async function insertConversation(sub: string, title: string): Promise<string> {
    return withUser(sub, async (tx) => {
      const [row] = await tx
        .insert(conversations)
        .values({ id: crypto.randomUUID(), userSub: sub, title })
        .returning();
      return row.id;
    });
  }

  beforeAll(async () => {
    unwrapped = postgres(process.env.DRIZZLE_DATABASE_URL!);
    pinned = postgres(process.env.DRIZZLE_DATABASE_URL!, { max: 1 });
    pinnedClient = createRlsClient(process.env.DRIZZLE_DATABASE_URL!, { sql: pinned });
    aliceConversationId = await insertConversation("dev|alice", "alice's");
    bobConversationId = await insertConversation("dev|bob", "bob's");
  });

  afterAll(async () => {
    // Delete exactly what this file inserted, each through the wrapper as its
    // own owner (the DELETE is subject to conversations_owner too, so this
    // only works if the row really is that user's). The local database is
    // shared with the synthetic seed, which already owns hundreds of other
    // `dev|alice` and `dev|bob` rows -- none of them are touched.
    for (const [sub, id] of [
      ["dev|alice", aliceConversationId],
      ["dev|bob", bobConversationId],
    ] as const) {
      if (id) {
        await withUser(sub, (tx) =>
          tx.delete(conversations).where(eq(conversations.id, id)),
        );
      }
    }
    await unwrapped?.end();
    await pinned?.end();
  });

  it("lets a user read their own row", async () => {
    const rows = await withUser("dev|alice", (tx) => tx.select().from(conversations));
    expect(rows.map((r) => r.id)).toContain(aliceConversationId);
    // Not just "her row is in there": every row the unfiltered SELECT returned
    // is hers. The store this client is built for adds no WHERE clause at all,
    // so this is the property it depends on.
    expect(rows.every((r) => r.userSub === "dev|alice")).toBe(true);
  });

  it("returns nothing for another user's row", async () => {
    const rows = await withUser("dev|bob", (tx) => tx.select().from(conversations));
    expect(rows.map((r) => r.id)).not.toContain(aliceConversationId);
    // ...and the select worked, rather than passing by returning nothing:
    // Bob sees his own row, and only rows like it.
    expect(rows.map((r) => r.id)).toContain(bobConversationId);
    expect(rows.every((r) => r.userSub === "dev|bob")).toBe(true);
  });

  it("would see both users' rows on the same DSN without withUser, so the isolation above is not vacuous", async () => {
    const rows = await unwrapped<{ id: string }[]>`
      SELECT id FROM conversations
      WHERE id IN (${aliceConversationId}, ${bobConversationId})
    `;
    expect(rows.map((r) => r.id).sort()).toEqual(
      [aliceConversationId, bobConversationId].sort(),
    );
  });

  it("sets the identity GUC and switches role inside the transaction", async () => {
    const [row] = await withUser("dev|alice", (tx) =>
      tx.execute(sql`
        SELECT current_user, session_user,
               current_setting('app.user_sub', true) AS user_sub
      `),
    );
    // The role switch happened, and it is a SWITCH -- session_user is still
    // the DSN's own role, so this is one connection carrying a transaction-
    // scoped role, not a second connection authenticating as someone else.
    expect(row.current_user).toBe("poseidon_app");
    expect(row.session_user).toBe("poseidon");
    expect(row.user_sub).toBe("dev|alice");
  });

  it("scopes the identity to its own transaction, so a committed value cannot leak to the next borrower of the same connection", async () => {
    // The regression guard for `set_config`'s third argument (`is_local`,
    // SET_IDENTITY_SQL in client.ts). Every cheaper vantage point is blind to
    // it: `pg_settings` carries no row for a placeholder GUC in either form, a
    // session-scoped SET is discarded by ROLLBACK just as a local one is, and
    // every path through `withUser` overwrites the value as its first
    // statement. What is left is a COMMITTED transaction observed from outside
    // `withUser` on the same physical connection -- which is what `max: 1` plus
    // an injected client buys.
    const rows = await pinnedClient.withUser("dev|alice", (tx) =>
      tx.select({ id: conversations.id }).from(conversations).limit(1),
    );
    expect(rows).toHaveLength(1); // the transaction really ran, and committed

    const [row] = await pinned<{ sub: string | null }[]>`
      SELECT current_setting('app.user_sub', true) AS sub
    `;
    // The identity did not survive the commit onto the next checkout.
    expect(row.sub).not.toBe("dev|alice");
    // Postgres reverts a transaction-local placeholder GUC to its reset value,
    // which -- verified against this database -- is the empty string rather than
    // NULL once set_config has defined the placeholder in this session.
    expect(row.sub === null || row.sub === "").toBe(true);
  });

  it("refuses a malformed DATABASE_APP_ROLE before any statement runs", async () => {
    const rejected = [
      'poseidon_app"; DROP TABLE conversations; --', // the injection the quoting exists for
      "Poseidon_App", // uppercase: Postgres would fold it, the pattern will not
      "1poseidon", // must not start with a digit
      "a".repeat(64), // 63 is Postgres's own NAMEDATALEN-1 cap
      " poseidon_app", // leading whitespace
      "poseidon_app\n", // a full-match pattern, never a prefix match
    ];
    const previous = process.env.DATABASE_APP_ROLE;
    try {
      for (const role of rejected) {
        process.env.DATABASE_APP_ROLE = role;
        let ranTheCallback = false;
        await expect(
          withUser("dev|alice", async () => {
            ranTheCallback = true;
            return null;
          }),
        ).rejects.toThrow(/not a valid Postgres role identifier/);
        // Fails closed twice over: it throws instead of quietly degrading to
        // "no role switch" (which on this superuser DSN would serve every
        // user's rows to every user), and the caller's work never runs.
        expect(ranTheCallback).toBe(false);
      }
    } finally {
      if (previous === undefined) delete process.env.DATABASE_APP_ROLE;
      else process.env.DATABASE_APP_ROLE = previous;
    }
  });

  it("treats an empty DATABASE_APP_ROLE as no role switch", async () => {
    // Python's ergonomic "unset" (app_role=None / DATABASE_APP_ROLE=""), for a
    // deploy whose DSN already authenticates as an ordinary non-privileged
    // role. On THIS superuser DSN it means RLS does not apply at all -- which
    // is precisely what Python's `assert_boot_privileges` check (a) refuses to
    // boot with, and is asserted here only as the configuration's real effect.
    const previous = process.env.DATABASE_APP_ROLE;
    process.env.DATABASE_APP_ROLE = "";
    try {
      const [row] = await withUser("dev|alice", (tx) =>
        tx.execute(sql`
          SELECT current_user, current_setting('app.user_sub', true) AS user_sub
        `),
      );
      expect(row.current_user).toBe("poseidon");
      // The identity GUC is still set -- skipping the role switch skips only
      // the role switch, never the first statement.
      expect(row.user_sub).toBe("dev|alice");
    } finally {
      if (previous === undefined) delete process.env.DATABASE_APP_ROLE;
      else process.env.DATABASE_APP_ROLE = previous;
    }
  });
});
