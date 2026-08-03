import type { Message } from "../../api/types";

/**
 * Whether `message` should ever be offered thumbs feedback -- i.e. it is an
 * assistant message backed by a real turn, NOT the opener greeting (the
 * first message of every conversation). The backend 422s
 * (`feedback_not_applicable`) on the opener because its `messages.turn_id`
 * is NULL, but the wire `Message` type carries no `turnId`/`turn_id` field
 * to check directly (task-2-brief's own note on this) -- so this uses the
 * one positional/identity signal that IS available: the opener's own
 * message id for this conversation, tracked by `chatStore.openerIdByConv`.
 *
 * `openerId === undefined` (the conversation's opener has not been recorded
 * yet -- should not happen in practice, since `chatStore` always sets
 * `messages` and `openerIdByConv` for a conversation together, but defensive
 * regardless) withholds rather than guesses: offering thumbs where a 422 is
 * possible is the one mistake this predicate exists to prevent, so "unknown"
 * fails closed the same direction as "known opener," never the other way.
 */
export function isTurnBackedAssistantMessage(
  message: Pick<Message, "id" | "role">,
  openerId: string | undefined,
): boolean {
  if (message.role !== "assistant") return false;
  if (openerId === undefined) return false;
  return message.id !== openerId;
}
