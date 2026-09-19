import { describe, expect, it } from "vitest";
import { requireSub } from "./request-identity";

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
