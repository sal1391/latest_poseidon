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
 * There are three states, all safe:
 *   1. `openerId` is known -- straightforward identity check.
 *   2. `openerId` is `undefined` AND `hasOlderPages` is true (i.e. the
 *      conversation's `messagesNextCursor` is non-null) -- the opener has
 *      not been WALKED BACK TO yet, but it is provably not among the
 *      currently-loaded messages either: a non-null next-cursor means at
 *      least one message strictly older than everything loaded still
 *      exists, so the true opener is out there, not here. Every
 *      currently-loaded assistant message is therefore genuinely
 *      turn-backed, and offering thumbs on it is safe.
 *   3. `openerId` is `undefined` AND `hasOlderPages` is false -- neither
 *      signal can vouch for this message. `chatStore` sets `messages`,
 *      `messagesNextCursor`, and `openerIdByConv` together in one atomic
 *      update at every one of its four load sites, so in practice this
 *      combination should not arise for any cid that actually has loaded
 *      messages (a null cursor there implies the opener WAS just
 *      determined) -- but the predicate does not lean on that invariant
 *      holding forever. Withholds rather than guesses: offering thumbs
 *      where a 422 is possible is the one mistake this predicate exists to
 *      prevent, so this state fails closed the same direction as "known
 *      opener, this message matches it," never the other way.
 *
 * Before Phase 12 Task 4's page-order amendment, state 2 could not arise
 * (a conversation's first-loaded page was always its true start), so this
 * predicate only ever saw states 1 and 3. Task 4 made state 2 the ROUTINE
 * case for any conversation over `limit` messages until the user pages all
 * the way back via "Load earlier" -- omitting `hasOlderPages` here used to
 * silently fall through to state 3's withhold-everywhere behavior for the
 * conversation's entire visible history, including the newest answer on
 * screen (final-review finding, Phase 12 whole-phase review).
 */
export function isTurnBackedAssistantMessage(
  message: Pick<Message, "id" | "role">,
  openerId: string | undefined,
  hasOlderPages: boolean,
): boolean {
  if (message.role !== "assistant") return false;
  if (openerId !== undefined) return message.id !== openerId;
  return hasOlderPages;
}
