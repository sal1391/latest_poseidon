import { http, HttpResponse } from "msw";
import type { Conversation, Message } from "../api/types";

/** Fixed ids keep component assertions readable: one conversation, one opener. */
export const mockConversation: Conversation = { id: "c1", title: "New chat" };

/** Task-6 opener shape: an intro line plus the two flow-entry chips. */
export const mockOpener: Message = {
  id: "m0",
  role: "assistant",
  parts: [
    { kind: "text", payload: { markdown: "Ask about your data, or pick a flow:" } },
    {
      kind: "chips",
      payload: {
        options: [
          { id: "existing_customer", label: "Existing customer" },
          { id: "new_prospect", label: "New customer prospect" },
        ],
      },
    },
  ],
};

/**
 * MSW v2 handlers for the mock chat API.
 *
 * The conversation list starts empty so a mounting client takes the
 * create-first-conversation path and lands on the opener above. The SSE turn
 * route (`POST /api/conversations/:cid/messages`) is deliberately absent —
 * component tests stub `api/sse` instead of streaming over the network.
 */
export const handlers = [
  http.post("/api/conversations", () =>
    HttpResponse.json({ conversation: mockConversation, opener: mockOpener }, { status: 201 })),

  http.get("/api/conversations", () => HttpResponse.json({ conversations: [] })),

  http.get("/api/conversations/:cid/messages", () =>
    HttpResponse.json({ messages: [mockOpener] })),

  http.post("/api/messages/:mid/feedback", () => new HttpResponse(null, { status: 204 })),
];
