import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { getEmailSourceConfig, resolveEmail } from "./email-source";

// The two env vars this seam reads -- reset before AND after every test so
// no case leaks its override into the next one (vitest runs this file's
// tests in one process, and process.env is shared, ambient state).
const ENV_KEYS = ["IDENTITY_EMAIL_SOURCE", "SNOWFLAKE_EMAIL_PROC"] as const;

beforeEach(() => {
  for (const key of ENV_KEYS) delete process.env[key];
});

afterEach(() => {
  for (const key of ENV_KEYS) delete process.env[key];
});

describe("getEmailSourceConfig", () => {
  it("defaults to entra when IDENTITY_EMAIL_SOURCE is unset", () => {
    expect(getEmailSourceConfig()).toEqual({ source: "entra" });
  });

  it("throws on an unknown IDENTITY_EMAIL_SOURCE value", () => {
    process.env.IDENTITY_EMAIL_SOURCE = "carrier_pigeon";
    expect(() => getEmailSourceConfig()).toThrow(/carrier_pigeon/);
  });

  it("throws when snowflake_proc is selected without SNOWFLAKE_EMAIL_PROC", () => {
    process.env.IDENTITY_EMAIL_SOURCE = "snowflake_proc";
    expect(() => getEmailSourceConfig()).toThrow(/SNOWFLAKE_EMAIL_PROC/);
  });

  it("accepts snowflake_proc with a procedure name set", () => {
    process.env.IDENTITY_EMAIL_SOURCE = "snowflake_proc";
    process.env.SNOWFLAKE_EMAIL_PROC = "DB.SCHEMA.GET_USER_EMAIL";
    expect(getEmailSourceConfig()).toEqual({
      source: "snowflake_proc",
      proc: "DB.SCHEMA.GET_USER_EMAIL",
    });
  });
});

describe("resolveEmail", () => {
  it("entra resolves to null -- today's unchanged behaviour", async () => {
    await expect(resolveEmail("alice")).resolves.toBeNull();
  });

  it("snowflake_proc without a procedure name fails before the stub is ever reached", async () => {
    process.env.IDENTITY_EMAIL_SOURCE = "snowflake_proc";
    await expect(resolveEmail("alice")).rejects.toThrow(/SNOWFLAKE_EMAIL_PROC/);
  });

  it("snowflake_proc with a name set raises the placeholder stub error", async () => {
    process.env.IDENTITY_EMAIL_SOURCE = "snowflake_proc";
    process.env.SNOWFLAKE_EMAIL_PROC = "DB.SCHEMA.GET_USER_EMAIL";
    await expect(resolveEmail("alice")).rejects.toThrow(
      /snowflake_proc email source is a placeholder; awaiting stored procedure code/,
    );
  });
});
