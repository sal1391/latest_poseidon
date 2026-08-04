import { waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
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

  // `openConversation` no longer awaits hydration internally (final-review
  // finding, Phase 12 whole-phase review: the fan-out must not block
  // `bootstrap()`'s critical path) -- so this test needs its own `waitFor`
  // rather than asserting immediately once `openConversation` resolves.
  await useChatStore.getState().openConversation("c1");

  await waitFor(() => expect(getCalls).toEqual(["a1"]));
  expect(useChatStore.getState().feedback.a1).toEqual({ verdict: "up", comment: undefined });
  expect(useChatStore.getState().feedback["opener-1"]).toBeUndefined();
});

// Final-review finding (Phase 12 whole-phase review, Finding 1): Task 4's
// page-order amendment means `openerIdByConv[cid]` stays undefined for any
// conversation over `limit` messages until the user pages all the way back
// -- before this fix, `isTurnBackedAssistantMessage`'s "unknown opener ->
// withhold" default made THIS state fire zero GETs, silently hiding every
// previously-recorded verdict on a long conversation. `next_cursor`
// non-null is what proves the opener isn't among the loaded messages, so
// hydration must still fire here.
test("hydrateFeedback fires GET for a turn-backed assistant message when the opener is unknown but next_cursor is non-null (long conversation)", async () => {
  const getCalls: string[] = [];
  server.use(
    http.get("/api/messages/:mid/feedback", ({ params }) => {
      getCalls.push(params.mid as string);
      return HttpResponse.json({ verdict: "up", comment: null });
    }),
  );
  useChatStore.setState({
    messages: { c1: [{ id: "a1", role: "assistant", parts: [] }] },
    // Non-null: there IS an older page not yet loaded, so the true opener is
    // provably not "a1" (or anything else currently loaded).
    messagesNextCursor: { c1: "cursor-1" },
    openerIdByConv: {}, // opener genuinely not yet known for c1
  });

  await useChatStore.getState().hydrateFeedback("c1");

  expect(getCalls).toEqual(["a1"]);
  expect(useChatStore.getState().feedback.a1).toEqual({ verdict: "up", comment: undefined });
});

// Same long-conversation state, but confirming the WITHHOLD side stays
// correct too: when `next_cursor` IS null (nothing further back to load, so
// the loaded page's own first item -- separately tracked in
// `openerIdByConv` by `openConversation`/`loadEarlierMessages` -- is where
// the opener question gets settled), an opener that is still `undefined`
// here would only mean a genuinely unresolved state, so hydration must not
// fire blindly.
test("hydrateFeedback withholds when the opener is unknown and next_cursor is null", async () => {
  const getCalls: string[] = [];
  server.use(
    http.get("/api/messages/:mid/feedback", ({ params }) => {
      getCalls.push(params.mid as string);
      return HttpResponse.json({ verdict: "up", comment: null });
    }),
  );
  useChatStore.setState({
    messages: { c1: [{ id: "a1", role: "assistant", parts: [] }] },
    messagesNextCursor: { c1: null },
    openerIdByConv: {},
  });

  await useChatStore.getState().hydrateFeedback("c1");

  expect(getCalls).toEqual([]);
});

// Finding 3 (same final-review wave): hydration is the ONLY path by which a
// persisted verdict ever reaches the UI, so a silent non-404 failure would
// make a user's real recorded verdict vanish with zero diagnostics. A 404
// (nothing recorded yet) must stay silent -- it is the expected, common case.
test("hydrateFeedback warns on a non-404 failure but stays silent on a 404", async () => {
  const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  server.use(
    http.get("/api/messages/:mid/feedback", ({ params }) =>
      params.mid === "a1"
        ? new HttpResponse(null, { status: 500 })
        : new HttpResponse(null, { status: 404 })),
  );
  useChatStore.setState({
    messages: {
      c1: [
        { id: "a1", role: "assistant", parts: [] },
        { id: "a2", role: "assistant", parts: [] },
      ],
    },
    messagesNextCursor: { c1: "cursor-1" },
    openerIdByConv: {},
  });

  await useChatStore.getState().hydrateFeedback("c1");

  expect(warnSpy).toHaveBeenCalledTimes(1);
  expect(warnSpy.mock.calls[0][0]).toContain("a1");
  warnSpy.mockRestore();
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

// Un-vote follow-up to Phase 12: submitFeedback(mid, null) POSTs
// {verdict: null, comment: null} and, on success, REMOVES the feedback[mid]
// entry entirely rather than storing {verdict: null, comment: undefined} --
// this keeps local state consistent with what a fresh GET would now return
// (404 -> no entry).
test("submitFeedback(mid, null) clears an existing feedback entry", async () => {
  server.use(
    http.post("/api/messages/:mid/feedback", () => new HttpResponse(null, { status: 204 })),
  );
  await useChatStore.getState().submitFeedback("a1", "up");
  expect(useChatStore.getState().feedback.a1).toEqual({ verdict: "up", comment: undefined });

  const seenBodies: unknown[] = [];
  server.use(
    http.post("/api/messages/:mid/feedback", async ({ request }) => {
      seenBodies.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
  );
  await useChatStore.getState().submitFeedback("a1", null);

  expect(seenBodies).toEqual([{ verdict: null, comment: null }]);
  expect(useChatStore.getState().feedback.a1).toBeUndefined();
  expect("a1" in useChatStore.getState().feedback).toBe(false);
});

test("submitFeedback(mid, null) rolls back to the previous entry on failure", async () => {
  server.use(
    http.post("/api/messages/:mid/feedback", () => new HttpResponse(null, { status: 204 })),
  );
  await useChatStore.getState().submitFeedback("a1", "down", "wrong numbers");
  expect(useChatStore.getState().feedback.a1).toEqual({
    verdict: "down",
    comment: "wrong numbers",
  });

  server.use(
    http.post("/api/messages/:mid/feedback", () => new HttpResponse(null, { status: 500 })),
  );
  await expect(useChatStore.getState().submitFeedback("a1", null)).rejects.toThrow();

  expect(useChatStore.getState().feedback.a1).toEqual({
    verdict: "down",
    comment: "wrong numbers",
  });
});

test("submitFeedback(mid, null) on a message with no prior entry rolls back to absent on failure", async () => {
  server.use(
    http.post("/api/messages/:mid/feedback", () => new HttpResponse(null, { status: 500 })),
  );

  await expect(useChatStore.getState().submitFeedback("never-voted", null)).rejects.toThrow();

  expect(useChatStore.getState().feedback["never-voted"]).toBeUndefined();
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
