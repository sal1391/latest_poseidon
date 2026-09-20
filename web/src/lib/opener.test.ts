import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { OPENER_PARTS } from "./opener";

describe("OPENER_PARTS", () => {
  it("equals Python's _build_opener_parts() byte for byte (contracts/turns/opener.json)", () => {
    const golden = JSON.parse(readFileSync(join(__dirname, "..", "..", "..", "contracts", "turns", "opener.json"), "utf8"));
    expect(OPENER_PARTS).toEqual(golden.parts);
  });
});
