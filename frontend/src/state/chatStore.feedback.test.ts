import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from "vitest";
import { handlers } from "../mocks/handlers";
import { resetChatStore, useChatStore } from "./chatStore";

/**
 * Unlike chatStore.test.ts (which vi.mock's `../api/client` wholesale), this
 * file drives `openConversation`/`submitFeedback` against the REAL
 * `mocks/handlers.ts` fixtures over MSW -- the `chatStore.pagination.
 * test.ts`/`chatStore.reconcile.test.ts` precedent. That is deliberate here:
 * it proves both the hydration fan-out AND the feedback routes' real wire
 * shapes against the SAME handlers a component test renders behind, not a
 * hand-rolled stub that could silently drift from either (task-2-brief's
 * own "MSW handlers updated to the real shapes" RED item).
 */
const server = setupServer(...handlers);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

beforeEach(() => {
  resetChatStore();
});

test("openConversation records the opener's message id in openerIdByConv", async () => {
  server.use(
    http.get("/api/conversations/c1/messages", () =>
      HttpResponse.json({
        items: [{ id: "opener-1", role: "assistant", parts: [] }],
        next_cursor: null,
      })),
  );

  await useChatStore.getState().openConversation("c1");

  expect(useChatStore.getState().openerIdByConv.c1).toBe("opener-1");
});

test("openConversation hydrates GET feedback for turn-backed assistant messages, never for the opener or a user message", async () => {
  const getCalls: string[] = [];
  server.use(
    http.get("/api/conversations/c1/messages", () =>
      HttpResponse.json({
        items: [
          { id: "opener-1", role: "assistant", parts: [] },
          { id: "u1", role: "user", parts: [] },
          { id: "a1", role: "assistant", parts: [] },
        ],
        next_cursor: null,
      })),
    http.get("/api/messages/:mid/feedback", ({ params }) => {
      getCalls.push(params.mid as string);
      if (params.mid === "a1") return HttpResponse.json({ verdict: "up", comment: null });
      return new HttpResponse(null, { status: 404 });
    }),
  );

  await useChatStore.getState().openConversation("c1");

  expect(getCalls).toEqual(["a1"]);
  expect(useChatStore.getState().feedback.a1).toEqual({ verdict: "up", comment: undefined });
  expect(useChatStore.getState().feedback["opener-1"]).toBeUndefined();
});

test("hydration treats a 404 as nothing-to-hydrate, not a thrown error -- openConversation still resolves", async () => {
  server.use(
    http.get("/api/conversations/c1/messages", () =>
      HttpResponse.json({
        items: [
          { id: "opener-1", role: "assistant", parts: [] },
          { id: "a1", role: "assistant", parts: [] },
        ],
        next_cursor: null,
      })),
    http.get("/api/messages/:mid/feedback", () => new HttpResponse(null, { status: 404 })),
  );

  await expect(useChatStore.getState().openConversation("c1")).resolves.toBeUndefined();
  expect(useChatStore.getState().feedback.a1).toBeUndefined();
});

test("submitFeedback POSTs the real {verdict, comment} body shape", async () => {
  let seenBody: unknown;
  server.use(
    http.post("/api/messages/:mid/feedback", async ({ request }) => {
      seenBody = await request.json();
      return new HttpResponse(null, { status: 204 });
    }),
  );

  await useChatStore.getState().submitFeedback("a1", "down", "numbers look off");

  expect(seenBody).toEqual({ verdict: "down", comment: "numbers look off" });
  expect(useChatStore.getState().feedback.a1).toEqual({
    verdict: "down",
    comment: "numbers look off",
  });
});

test("a re-vote (amend) sends the new verdict and the store reflects the flip", async () => {
  const seenBodies: unknown[] = [];
  server.use(
    http.post("/api/messages/:mid/feedback", async ({ request }) => {
      seenBodies.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
  );

  await useChatStore.getState().submitFeedback("a1", "up");
  expect(useChatStore.getState().feedback.a1).toEqual({ verdict: "up", comment: undefined });

  await useChatStore.getState().submitFeedback("a1", "down", "actually wrong");
  expect(useChatStore.getState().feedback.a1).toEqual({
    verdict: "down",
    comment: "actually wrong",
  });

  expect(seenBodies).toEqual([
    { verdict: "up", comment: null },
    { verdict: "down", comment: "actually wrong" },
  ]);
});

// task-2-brief's "422 quiet no-op" item, proven at the store layer (the
// UI-layer half -- thumbs never rendering on the opener at all -- is proven
// in ChatScreen.test.tsx; "pin both layers" per the brief).
test("submitFeedback rolls back the optimistic write on a 422 feedback_not_applicable response", async () => {
  server.use(
    http.post("/api/messages/:mid/feedback", () =>
      HttpResponse.json(
        {
          type: "about:blank",
          title: "feedback_not_applicable",
          detail: "message has no linked turn",
          status: 422,
        },
        { status: 422 },
      )),
  );

  await expect(useChatStore.getState().submitFeedback("opener-1", "down")).rejects.toThrow();

  expect(useChatStore.getState().feedback["opener-1"]).toBeUndefined();
});
