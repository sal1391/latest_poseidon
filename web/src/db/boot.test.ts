import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import postgres from "postgres";
import { assertBootPrivileges } from "./client";

describe("assertBootPrivileges (offline)", () => {
  it("logs boot_privileges_unchecked and resolves when the database is unreachable", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const client = postgres("postgresql://nobody:nope@127.0.0.1:1/void", { max: 1, connect_timeout: 1 });
    await expect(assertBootPrivileges(client, "poseidon_app")).resolves.toBeUndefined();
    expect(warn.mock.calls.some(([line]) => String(line).includes("boot_privileges_unchecked"))).toBe(true);
    await client.end();
    warn.mockRestore();
  });

  it("propagates a server-reported error instead of downgrading it to boot_privileges_unchecked", async () => {
    // The server DID answer (a PostgresError), so this is a fault in the
    // check itself -- bad SQL text, a permission problem reading pg_roles,
    // whatever -- never "unreachable". P16-R5: the catch must discriminate.
    const serverError = Object.assign(Object.create(postgres.PostgresError.prototype), {
      message: "permission denied for table pg_roles",
      code: "42501",
    }) as InstanceType<typeof postgres.PostgresError>;
    const fake = Object.assign(
      async () => {
        throw serverError;
      },
      { begin: async () => undefined, end: async () => undefined },
    ) as unknown as ReturnType<typeof postgres>;
    await expect(assertBootPrivileges(fake, "poseidon_app")).rejects.toBe(serverError);
  });
});

// Requires the compose `db` service (its `poseidon` user is a superuser, which
// is exactly the configuration the probe exists to catch). Skipped when unset.
const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;

live("assertBootPrivileges (live)", () => {
  let client: ReturnType<typeof postgres>;
  beforeAll(() => {
    client = postgres(process.env.DRIZZLE_DATABASE_URL!, { max: 1 });
  });
  afterAll(async () => {
    await client?.end();
  });

  it("refuses a superuser DSN with no app role", async () => {
    await expect(assertBootPrivileges(client, null)).rejects.toThrow(/rolsuper or rolbypassrls/);
  });

  it("accepts the app role by rehearsing SET LOCAL ROLE in a rolled-back transaction", async () => {
    await expect(assertBootPrivileges(client, "poseidon_app")).resolves.toBeUndefined();
    const [row] = await client`SELECT current_user AS who`;
    expect(row.who).toBe("poseidon"); // the rehearsal left no role switch behind
  });

  it("names a role that does not exist", async () => {
    await expect(assertBootPrivileges(client, "no_such_role")).rejects.toThrow(/no_such_role/);
  });
});
