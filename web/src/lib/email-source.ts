/**
 * Phase 15 Task 7 (PLACEHOLDER): the Entra-vs-Snowflake-stored-procedure
 * email source switch, ported from `backend/poseidon/core/email_source.py`
 * and `Settings.identity_email_source`/`Settings.snowflake_email_proc`
 * (`backend/poseidon/core/config.py`).
 *
 * **Status: PLACEHOLDER.** Carlos is waiting on the stored-procedure code
 * from the Snowflake side. This module ships only the switch and the seam:
 * a config reader with two branches, one of which (`entra`) is today's
 * real, unchanged behaviour, and one of which (`snowflake_proc`) is a stub
 * that throws rather than calling Snowflake. When the procedure code
 * arrives, a follow-up task fills the `snowflake_proc` branch in.
 *
 * **Why it exists.** In `spcs_ingress` mode `Sf-Context-Current-User`
 * carries a bare username and `identity.ts`'s `resolveIdentity` leaves
 * `email`/`name` as `null` -- the email is assumed today to come from Entra
 * (the SSO in front of Snowflake), but this app never actually resolves it.
 * `IDENTITY_EMAIL_SOURCE` names a second, configurable source: a Snowflake
 * stored procedure that maps the username to an email. Both runtimes
 * resolve identity during coexistence, so both get this switch under the
 * same env var names -- see the Python module's own docstring.
 *
 * **Do NOT wire this into `identity.ts`/`proxy.ts` yet.** The stub throws,
 * so wiring it into the request path now would break the default path for
 * nothing. Wiring lands in the follow-up task once real procedure code
 * exists. Recommendation on record (task brief): Python ends up owning the
 * actual Snowflake call, with Next.js getting the email through the Task 5
 * internal contract -- this TS stub exists only so the setting is honoured,
 * config-wise, on both sides.
 */

export type EmailSourceConfig = { source: "entra" } | { source: "snowflake_proc"; proc: string };

const SNOWFLAKE_PROC_PLACEHOLDER_MESSAGE =
  "snowflake_proc email source is a placeholder; awaiting stored procedure code";

/**
 * Reads and validates `IDENTITY_EMAIL_SOURCE`/`SNOWFLAKE_EMAIL_PROC` from
 * `process.env`. This is this runtime's equivalent of Python's
 * fail-at-settings-load guarantee (`Settings.
 * snowflake_email_proc_required_when_selected`): TypeScript has no
 * process-wide settings object that validates once at boot, so every call
 * re-validates -- an unconfigured `snowflake_proc` deploy fails here, at the
 * first email resolution, rather than silently returning something wrong.
 *
 * Unset `IDENTITY_EMAIL_SOURCE` defaults to `"entra"`, matching `Settings`'
 * own default. An unrecognised value throws immediately, naming the
 * offending value -- the TS "guard that throws" the brief's contract table
 * asks for in place of Python's `Literal[...]`.
 */
export function getEmailSourceConfig(): EmailSourceConfig {
  const raw = process.env.IDENTITY_EMAIL_SOURCE ?? "entra";

  if (raw === "entra") {
    return { source: "entra" };
  }

  if (raw === "snowflake_proc") {
    const proc = process.env.SNOWFLAKE_EMAIL_PROC;
    if (!proc) {
      throw new Error(
        "IDENTITY_EMAIL_SOURCE=snowflake_proc requires SNOWFLAKE_EMAIL_PROC to be set",
      );
    }
    return { source: "snowflake_proc", proc };
  }

  throw new Error(`IDENTITY_EMAIL_SOURCE=${JSON.stringify(raw)} is not a recognised email source`);
}

/**
 * Resolve `username` to an email address per the current
 * `IDENTITY_EMAIL_SOURCE` config.
 *
 * `entra` resolves to `null` -- exactly what `resolveIdentity` returns
 * today (no email claim exists to read in spcs_ingress mode). `
 * snowflake_proc` is the stub: it always rejects with
 * `NotImplementedError`'s Python message text (there is no built-in
 * `NotImplementedError` in JS; a plain `Error` carrying the identical
 * message is the honest port), never calls Snowflake.
 */
export async function resolveEmail(username: string): Promise<string | null> {
  void username; // unused in both branches today, exactly like the Python entra branch
  const config = getEmailSourceConfig();
  if (config.source === "entra") {
    return null;
  }
  throw new Error(SNOWFLAKE_PROC_PLACEHOLDER_MESSAGE);
}
