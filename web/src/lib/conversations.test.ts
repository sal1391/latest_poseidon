import { describe, expect, it } from "vitest";
import { listConversations, loadConversation } from "./conversations";

const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;

live("conversations", () => {
  it("lists only the caller's conversations", async () => {
    const alice = await listConversations("dev|alice");
    expect(alice.every((c) => c.userSub === "dev|alice")).toBe(true);
  });

  it("returns null for a conversation the caller does not own", async () => {
    const [first] = await listConversations("dev|alice");
    if (!first) return;
    expect(await loadConversation("dev|bob", first.id)).toBeNull();
  });
});
