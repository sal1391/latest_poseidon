import { describe, expect, it } from "vitest";
import { toneFor } from "./tone";

describe("toneFor (ruling P3-R2)", () => {
  it("colours by the sign of the numeric change and leaves zero in ink (Review Focus 1)", () => {
    expect(toneFor(30000)).toBe("text-positive");
    expect(toneFor(-27000)).toBe("text-negative");
    expect(toneFor(0)).toBe("text-ink");
    expect(toneFor(-0)).toBe("text-ink");
  });
});
