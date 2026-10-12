import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Count every postgres.js client this file's code opens, without changing what
// postgres() does. "Rejects WITHOUT opening any connection" is asserted against
// this counter rather than inferred from the error message.
const opened = vi.hoisted(() => ({ count: 0 }));
vi.mock("postgres", async (importOriginal) => {
  const actual = (await importOriginal()) as { default: (...args: unknown[]) => unknown };
  const counting = Object.assign((...args: unknown[]) => {
    opened.count += 1;
    return actual.default(...args);
  }, actual.default);
  return { ...actual, default: counting };
});

const REFUSAL = "DRIZZLE_DATABASE_URL is unset: refusing to guess which database to connect to";

// Offline by construction: every test here pins the variable itself, so the
// file behaves the same whether or not the live suite's DRIZZLE_DATABASE_URL is set.
describe("databaseUrl / lazy default client (offline)", () => {
  beforeEach(() => {
    opened.count = 0;
    vi.stubEnv("DRIZZLE_DATABASE_URL", undefined);
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("throws the refusal when the variable is unset", async () => {
    const { databaseUrl } = await import("./client");
    expect(() => databaseUrl()).toThrow(REFUSAL);
  });

  it.each(["", "   "])("throws the refusal when the variable is blank (%j)", async (blank) => {
    vi.stubEnv("DRIZZLE_DATABASE_URL", blank);
    const { databaseUrl } = await import("./client");
    expect(() => databaseUrl()).toThrow(REFUSAL);
  });

  it("returns the value when set, re-reading it on every call", async () => {
    const { databaseUrl } = await import("./client");
    vi.stubEnv("DRIZZLE_DATABASE_URL", "postgresql://u:p@localhost:5434/db");
    expect(databaseUrl()).toBe("postgresql://u:p@localhost:5434/db");
    vi.stubEnv("DRIZZLE_DATABASE_URL", "postgresql://u:p@localhost:5435/other");
    expect(databaseUrl()).toBe("postgresql://u:p@localhost:5435/other");
  });

  it("bootCheck rejects with the refusal when unset, opening no connection", async () => {
    const { bootCheck } = await import("./boot");
    await expect(bootCheck()).rejects.toThrow(REFUSAL);
    expect(opened.count).toBe(0);
  });

  it("withUser rejects with the refusal when unset, opening no connection", async () => {
    const { withUser } = await import("./client");
    await expect(withUser("sub", async () => 1)).rejects.toThrow(REFUSAL);
    expect(opened.count).toBe(0);
  });

  it("importing client and conversations with the variable unset does not throw", async () => {
    vi.resetModules();
    await expect(import("./client")).resolves.toBeDefined();
    await expect(import("../lib/conversations")).resolves.toBeDefined();
    expect(opened.count).toBe(0);
  });
});
