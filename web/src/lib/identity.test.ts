import { describe, expect, it } from "vitest";
import { AuthError, IdentityConfigError, resolveIdentity } from "./identity";

const h = (o: Record<string, string>) => new Headers(o);

/** Returns whatever `fn` threw, or `undefined` if it returned normally. */
function caught(fn: () => unknown): unknown {
  try {
    fn();
  } catch (err) {
    return err;
  }
  return undefined;
}

/**
 * The one 401 this module raises, asserted as the whole `(status, title,
 * detail)` triple rather than as a substring of the message: all three fields
 * reach the wire through `proxy.ts`'s RFC-7807 body, and all three are pinned
 * to `identity_spcs.py:113-114,155`. `message` carries the detail, mirroring
 * Python's `super().__init__(detail)` (`identity.py:100`).
 */
function expectMissingSpcsHeader(fn: () => unknown): void {
  const err = caught(fn);
  expect(err).toBeInstanceOf(AuthError);
  const authError = err as AuthError;
  expect(authError.status).toBe(401);
  expect(authError.title).toBe("missing spcs identity header");
  expect(authError.detail).toBe("no valid Sf-Context-Current-User header");
  expect(authError.message).toBe(authError.detail);
}

/**
 * A configuration fault, which must NOT be an `AuthError`: Python separates the
 * two (`RuntimeError` vs `AuthError`) and `proxy.ts` picks 500 over 401 off
 * exactly this distinction, so making one class a subclass of the other would
 * silently turn an operator's mistake back into a user-facing 401.
 */
function expectConfigFault(fn: () => unknown, message: RegExp): void {
  const err = caught(fn);
  expect(err).toBeInstanceOf(IdentityConfigError);
  expect(err).not.toBeInstanceOf(AuthError);
  expect((err as Error).message).toMatch(message);
}

describe("resolveIdentity", () => {
  it("disabled mode returns the fixed dev identity", () => {
    const id = resolveIdentity("disabled", "local", h({}));
    expect(id).toEqual({
      sub: "dev|local",
      email: "dev@local",
      name: "Dev User",
      roles: ["Poseidon:Sales"],
    });
  });

  it("disabled mode honours the X-Dev-User act-as header", () => {
    const id = resolveIdentity("disabled", "local", h({ "x-dev-user": "alice" }));
    expect(id).toMatchObject({ sub: "dev|alice" });
  });

  it("disabled mode ignores a malformed act-as header rather than rejecting", () => {
    const id = resolveIdentity("disabled", "local", h({ "x-dev-user": "bad user!" }));
    expect(id).toMatchObject({ sub: "dev|local" });
  });

  it("spcs_ingress mints an sf| sub from the platform header", () => {
    process.env.SPCS_SALES_USERS = "*";
    const id = resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "CARLOS" }));
    expect(id).toMatchObject({ sub: "sf|carlos", roles: ["Poseidon:Sales"] });
  });

  it("spcs_ingress grants Sales only to allow-listed users", () => {
    process.env.SPCS_SALES_USERS = "alice,bob";
    const allowed = resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "ALICE" }));
    expect(allowed).toMatchObject({ sub: "sf|alice", roles: ["Poseidon:Sales"] });

    // Authenticated by the platform, but not on the list -> no roles.
    const stranger = resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "mallory" }));
    expect(stranger).toMatchObject({ sub: "sf|mallory", roles: [] });
  });

  it("rejects a username over the 64-character cap", () => {
    process.env.SPCS_SALES_USERS = "*";
    expectMissingSpcsHeader(() =>
      resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "a".repeat(65) })),
    );
  });

  it("rejects a dot, which Python's character class excludes", () => {
    const id = resolveIdentity("disabled", "local", h({ "x-dev-user": "first.last" }));
    expect(id).toMatchObject({ sub: "dev|local" });
  });

  it("spcs_ingress refuses to trust the header outside spcs deploy mode", () => {
    expectConfigFault(
      () => resolveIdentity("spcs_ingress", "local", h({ "sf-context-current-user": "CARLOS" })),
      /deploy mode/i,
    );
  });

  it("spcs_ingress 401s when the header is absent", () => {
    expectMissingSpcsHeader(() => resolveIdentity("spcs_ingress", "spcs", h({})));
  });

  it("spcs_ingress 401s identically when the header is present but malformed", () => {
    expectMissingSpcsHeader(() =>
      resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "a b!" })),
    );
  });

  it("auth0 is not wired in this phase", () => {
    expectConfigFault(() => resolveIdentity("auth0", "local", h({})), /not wired/i);
  });
});

// ---------------------------------------------------------------------------
// Parity cases beyond the brief's own list. Each pins a behaviour the Python
// providers actually have (cited inline) that the list above leaves untested,
// and that a plausible-looking TypeScript port would get wrong silently.
// ---------------------------------------------------------------------------

describe("resolveIdentity parity with the Python providers", () => {
  it("derives email and name per act-as user, as DisabledProvider.resolve does", () => {
    // identity.py:214-219 mints email=f"{candidate}@local", name=candidate --
    // NOT the fixed default's "dev@local"/"Dev User". Reusing the default's
    // pair would make every dev|X sub render as the same person.
    const id = resolveIdentity("disabled", "local", h({ "x-dev-user": "alice" }));
    expect(id).toEqual({
      sub: "dev|alice",
      email: "alice@local",
      name: "alice",
      roles: ["Poseidon:Sales"],
    });
  });

  it("lower-cases the act-as value before building the sub", () => {
    // sanitize_username casefolds FIRST, so "ALICE" and "alice" are one
    // identity, keyed on one sub -- not two rows in the database.
    const id = resolveIdentity("disabled", "local", h({ "x-dev-user": "ALICE" }));
    expect(id).toMatchObject({ sub: "dev|alice", email: "alice@local", name: "alice" });
  });

  it("accepts a username of exactly 64 characters", () => {
    // The cap is inclusive: {1,64}. The 65-char rejection above only proves
    // that SOMETHING rejects long names, not that the boundary is right.
    process.env.SPCS_SALES_USERS = "*";
    const id = resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "a".repeat(64) }));
    expect(id).toMatchObject({ sub: `sf|${"a".repeat(64)}` });
  });

  it("treats an empty act-as header exactly like an absent one", () => {
    // "" fails the {1,64} minimum, so disabled mode falls back silently.
    const id = resolveIdentity("disabled", "local", h({ "x-dev-user": "" }));
    expect(id).toMatchObject({ sub: "dev|local" });
  });

  it("401s on an empty spcs header, the same as an absent one", () => {
    expectMissingSpcsHeader(() =>
      resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "" })),
    );
  });

  it("finds the headers regardless of the case the client sent them in", () => {
    // Python's api/app.py lowercases every header name before a provider sees
    // it; Headers.get is case-insensitive, which is the same guarantee.
    expect(resolveIdentity("disabled", "local", h({ "X-Dev-User": "alice" })))
      .toMatchObject({ sub: "dev|alice" });
    process.env.SPCS_SALES_USERS = "*";
    expect(resolveIdentity("spcs_ingress", "spcs", h({ "Sf-Context-Current-User": "CARLOS" })))
      .toMatchObject({ sub: "sf|carlos" });
  });

  it("trims and case-folds the allowlist, as Settings + the provider do", () => {
    // config.py's split_spcs_sales_users strips each comma-separated entry;
    // SpcsIngressProvider then casefolds them at construction. An operator's
    // spacing and casing in SPCS_SALES_USERS must not decide who gets Sales.
    process.env.SPCS_SALES_USERS = " Alice , BOB ";
    expect(resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "alice" })))
      .toMatchObject({ roles: ["Poseidon:Sales"] });
    expect(resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "bob" })))
      .toMatchObject({ roles: ["Poseidon:Sales"] });
  });

  it("grants nobody Sales when the allowlist is unset -- the fail-closed default", () => {
    // config.py: spcs_sales_users defaults to [] deliberately, and an
    // unconfigured allowlist resolves to "authenticated, role-less".
    delete process.env.SPCS_SALES_USERS;
    const id = resolveIdentity("spcs_ingress", "spcs", h({ "sf-context-current-user": "carlos" }));
    expect(id).toEqual({ sub: "sf|carlos", email: null, name: null, roles: [] });
  });

  it("names an unrecognised mode instead of blaming auth0", () => {
    // IDENTITY_MODE is an unvalidated env string proxy.ts casts into the
    // union, so a typo reaches here. Python's resolve_provider (identity.py:
    // 301) echoes the bad value; falling through to the auth0 message would
    // send an operator to debug a mode they never configured.
    expectConfigFault(
      () => resolveIdentity("diabled" as never, "local", h({})),
      /identity_mode="diabled" has no resolver/,
    );
  });

  it("never lets a caller mutate one identity into another", () => {
    // Python's UserContext is a frozen dataclass holding a tuple. The closest
    // honest TypeScript equivalent is minting a fresh object per call, so no
    // two resolved identities share a roles array.
    const first = resolveIdentity("disabled", "local", h({}));
    first.roles.push("Poseidon:Admin");
    const second = resolveIdentity("disabled", "local", h({}));
    expect(second.roles).toEqual(["Poseidon:Sales"]);
  });
});
