import { describe, expect, it } from "vitest";
import * as schema from "./schema";

describe("introspected schema", () => {
  it("includes the chat history tables", () => {
    expect(schema).toHaveProperty("conversations");
    expect(schema).toHaveProperty("messages");
  });

  it("keys conversations on the provider-prefixed sub", () => {
    const cols = Object.keys((schema.conversations as any)[Symbol.for("drizzle:Columns")]);
    expect(cols).toContain("userSub");
  });
});
