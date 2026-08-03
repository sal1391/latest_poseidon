import { expect, test } from "vitest";
import type { Message } from "../../api/types";
import { isTurnBackedAssistantMessage } from "./turnBacked";

function msg(id: string, role: Message["role"]): Pick<Message, "id" | "role"> {
  return { id, role };
}

test("a user message is never feedback material, regardless of id", () => {
  expect(isTurnBackedAssistantMessage(msg("m0", "user"), "m0")).toBe(false);
});

test("the opener (assistant message whose id matches openerId) is not turn-backed", () => {
  expect(isTurnBackedAssistantMessage(msg("m0", "assistant"), "m0")).toBe(false);
});

test("an assistant message that is NOT the opener is turn-backed", () => {
  expect(isTurnBackedAssistantMessage(msg("a1", "assistant"), "m0")).toBe(true);
});

// Defensive fail-closed case (task-2-brief: "the UI should never offer
// thumbs where 422 is possible, pin both layers"): an unknown opener must
// withhold, never guess a message is safe to vote on.
test("an unknown opener (not yet tracked) withholds rather than guesses turn-backed", () => {
  expect(isTurnBackedAssistantMessage(msg("a1", "assistant"), undefined)).toBe(false);
});
