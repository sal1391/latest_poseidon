import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from "vitest";
import { resetChatStore, useChatStore } from "./chatStore";

/**
 * Unlike chatStore.test.ts, this file does NOT `vi.mock("../api/client")`:
 * `loadMoreConversations` is exercised against the REAL `api/client.ts`
 * implementation over MSW, so the cursor query-param it sends and the
 * `{items, next_cursor}` envelope it parses are proven against something
 * that looks like the real backend, not a hand-rolled stub that could
 * silently drift from it (exactly the gap Task 3's report flagged: "the
 * vitest pass is not a green light" -- see task-3-report.md's own
 * "Concerns" section).
 */
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

beforeEach(() => {
  resetChatStore();
});

test("loadMoreConversations appends the MSW-served next page and forwards the cursor", async () => {
  server.use(
    http.get("/api/conversations", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      expect(cursor).toBe("page-2-cursor");
      return HttpResponse.json({
        items: [{ id: "c2", title: "Second chat" }],
        next_cursor: null,
      });
    }),
  );
  useChatStore.setState({
    conversations: [{ id: "c1", title: "First chat" }],
    conversationsNextCursor: "page-2-cursor",
  });

  await useChatStore.getState().loadMoreConversations();

  expect(useChatStore.getState().conversations).toEqual([
    { id: "c1", title: "First chat" },
    { id: "c2", title: "Second chat" },
  ]);
  expect(useChatStore.getState().conversationsNextCursor).toBeNull();
});

test("loadMoreConversations makes no request when there is no next_cursor", async () => {
  // No handler registered at all; `onUnhandledRequest: "error"` (above) means
  // an actual fetch here would fail the test loudly instead of silently
  // returning a 200 -- this is what proves the guard clause short-circuits
  // before ever calling `api.listConversations`, not merely that it swallows
  // a real network error.
  useChatStore.setState({
    conversations: [{ id: "c1", title: "First chat" }],
    conversationsNextCursor: null,
  });

  await useChatStore.getState().loadMoreConversations();

  expect(useChatStore.getState().conversations).toEqual([{ id: "c1", title: "First chat" }]);
});
