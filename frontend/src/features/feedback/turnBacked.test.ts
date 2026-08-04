import { expect, test } from "vitest";
import type { Message } from "../../api/types";
import { isTurnBackedAssistantMessage } from "./turnBacked";

function msg(id: string, role: Message["role"]): Pick<Message, "id" | "role"> {
  return { id, role };
}

test("a user message is never feedback material, regardless of id", () => {
  expect(isTurnBackedAssistantMessage(msg("m0", "user"), "m0", false)).toBe(false);
});

test("the opener (assistant message whose id matches openerId) is not turn-backed", () => {
  expect(isTurnBackedAssistantMessage(msg("m0", "assistant"), "m0", false)).toBe(false);
});

test("an assistant message that is NOT the opener is turn-backed", () => {
  expect(isTurnBackedAssistantMessage(msg("a1", "assistant"), "m0", false)).toBe(true);
});

// Defensive fail-closed case (task-2-brief: "the UI should never offer
// thumbs where 422 is possible, pin both layers"): an unknown opener with no
// proof the opener is out of view must withhold, never guess a message is
// safe to vote on.
test("an unknown opener with no older pages withholds rather than guesses turn-backed", () => {
  expect(isTurnBackedAssistantMessage(msg("a1", "assistant"), undefined, false)).toBe(false);
});

// Final-review finding (Phase 12 whole-phase review): a conversation over
// `limit` messages has `openerId === undefined` until the user pages all the
// way back via "Load earlier" -- for exactly that state, `hasOlderPages`
// (`messagesNextCursor[cid] !== null`) PROVES the opener isn't among the
// currently-loaded messages, so every loaded assistant message genuinely IS
// turn-backed. Before this fix, `openerId === undefined` alone withheld
// thumbs from EVERY message in a long conversation, including the most
// recent answer -- this is the case that regression covers.
test("an unknown opener WITH older pages still to load is turn-backed (long-conversation case)", () => {
  expect(isTurnBackedAssistantMessage(msg("a1", "assistant"), undefined, true)).toBe(true);
});
