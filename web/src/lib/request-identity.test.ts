import { describe, expect, it } from "vitest";
import { requireSales, requireSub } from "./request-identity";

describe("requireSub", () => {
  it("returns the header value when present", () => {
    const h = new Headers({ "x-poseidon-sub": "dev|alice" });
    expect(requireSub(h)).toBe("dev|alice");
  });

  it("throws when the header is absent", () => {
    const h = new Headers();
    expect(() => requireSub(h)).toThrow(
      "x-poseidon-sub missing: the proxy did not run for this request",
    );
  });

  it("throws when the header is blank or whitespace", () => {
    const h = new Headers({ "x-poseidon-sub": "   " });
    expect(() => requireSub(h)).toThrow(
      "x-poseidon-sub missing: the proxy did not run for this request",
    );
  });
});

describe("requireSales", () => {
  it("returns sub and roles when the Sales role is present", () => {
    const h = new Headers({ "x-poseidon-sub": "dev|alice", "x-poseidon-roles": "Poseidon:Sales,Other" });
    expect(requireSales(h)).toEqual({ sub: "dev|alice", roles: ["Poseidon:Sales", "Other"] });
  });

  it("throws a configuration fault when the role is missing (the proxy should have answered 403)", () => {
    const h = new Headers({ "x-poseidon-sub": "dev|alice", "x-poseidon-roles": "" });
    expect(() => requireSales(h)).toThrow(/lacks Poseidon:Sales/);
  });

  it("still requires the sub", () => {
    expect(() => requireSales(new Headers({ "x-poseidon-roles": "Poseidon:Sales" }))).toThrow(
      "x-poseidon-sub missing: the proxy did not run for this request",
    );
  });
});
