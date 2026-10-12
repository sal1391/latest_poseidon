import { afterAll, describe, expect, it } from "vitest";
import { eq } from "drizzle-orm";
import { withUser } from "../db/client";
import { conversations, messages } from "../db/schema";
import { createConversation, listConversations, loadConversation } from "./conversations";
import { OPENER_PARTS } from "./opener";

const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;

describe("loadConversation", () => {
  it("returns null for a non-uuid id without touching the db", async () => {
    expect(await loadConversation("dev|alice", "not-a-uuid")).toBeNull();
  });
});

live("conversations", () => {
  // Set by the createConversation test below, and cleaned up here the way
  // client.test.ts cleans up its own inserts: delete through withUser as the
  // owning sub, so the DELETE is itself subject to conversations_owner /
  // messages_owner. Left undefined if that test never runs or never gets far
  // enough to create anything.
  let createdConversationId: string | undefined;

  afterAll(async () => {
    if (!createdConversationId) return;
    await withUser("dev|alice", (tx) =>
      tx.delete(messages).where(eq(messages.conversationId, createdConversationId!)),
    );
    await withUser("dev|alice", (tx) =>
      tx.delete(conversations).where(eq(conversations.id, createdConversationId!)),
    );
  });

  it("lists only the caller's conversations", async () => {
    const alice = await listConversations("dev|alice");
    expect(alice.every((c) => c.userSub === "dev|alice")).toBe(true);
  });

  it("returns null for a conversation the caller does not own", async () => {
    const [first] = await listConversations("dev|alice");
    if (!first) return;
    expect(await loadConversation("dev|bob", first.id)).toBeNull();
  });

  it("createConversation inserts a conversation and its opener under RLS, newest first", async () => {
    const id = await createConversation("dev|alice");
    createdConversationId = id;
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    const loaded = await loadConversation("dev|alice", id);
    expect(loaded?.conversation.title).toBe("New chat");
    expect(loaded?.messages.map((m) => m.role)).toEqual(["assistant"]);
    expect(loaded?.messages[0].parts).toEqual(OPENER_PARTS);
    expect(await loadConversation("dev|bob", id)).toBeNull();
    expect((await listConversations("dev|alice"))[0].id).toBe(id);
  });
});
