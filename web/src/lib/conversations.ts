import { asc, desc, eq } from "drizzle-orm";
import { withUser } from "../db/client";
import { conversations, messages } from "../db/schema";

/**
 * Ruling R18: newest-first, capped at 50, id as a tiebreaker on equal
 * timestamps -- mirrors Python's list (backend/poseidon/core/chat/
 * history.py:265-266) and the `ix_conversations_user_recency` index, which is
 * built on `(user_sub, updated_at DESC, id DESC)`. No cursor paging here.
 */
export async function listConversations(sub: string) {
  return withUser(sub, (tx) =>
    tx
      .select()
      .from(conversations)
      .orderBy(desc(conversations.updatedAt), desc(conversations.id))
      .limit(50),
  );
}

export async function loadConversation(sub: string, id: string) {
  return withUser(sub, async (tx) => {
    const [conversation] = await tx
      .select()
      .from(conversations)
      .where(eq(conversations.id, id));
    // RLS already filtered this -- a foreign id simply returns no row rather
    // than raising, so an absent row means "not yours or not there", and the
    // caller cannot tell the difference. That is the intended behaviour.
    if (!conversation) return null;
    const rows = await tx
      .select()
      .from(messages)
      .where(eq(messages.conversationId, id))
      .orderBy(asc(messages.createdAt));
    return { conversation, messages: rows };
  });
}
