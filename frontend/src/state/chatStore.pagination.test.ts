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

// Fix round 1 (review finding I-1): `loadMoreConversations` reads
// `conversationsNextCursor` before its own `await`, so two calls fired
// without awaiting the first (a double-click) both read the SAME
// pre-await cursor and, unguarded, both fetch and append the same page --
// duplicate sidebar rows. The held/released response below is what makes
// this a genuine concurrent-request race rather than an artifact of
// same-tick microtask ordering: both calls are proven to reach the network
// (or not) independently of how fast either response happens to resolve.
test("loadMoreConversations guards against a double-click firing two concurrent requests for the same page", async () => {
  let releaseFirstPage: (() => void) | undefined;
  const held = new Promise<void>((resolve) => {
    releaseFirstPage = resolve;
  });
  let requestCount = 0;
  server.use(
    http.get("/api/conversations", async ({ request }) => {
      requestCount += 1;
      const cursor = new URL(request.url).searchParams.get("cursor");
      expect(cursor).toBe("page-2-cursor"); // both calls, if unguarded, read this same pre-await cursor
      await held; // held open until the test releases it -- simulates real in-flight network latency
      return HttpResponse.json({
        items: [{ id: "c2", title: "Second chat" }],
        next_cursor: "page-3-cursor",
      });
    }),
  );
  useChatStore.setState({
    conversations: [{ id: "c1", title: "First chat" }],
    conversationsNextCursor: "page-2-cursor",
  });

  // Fired back to back, neither awaited -- the double-click this finding names.
  const first = useChatStore.getState().loadMoreConversations();
  const second = useChatStore.getState().loadMoreConversations();

  releaseFirstPage?.();
  await Promise.all([first, second]);

  // The guard must stop the second call before it ever reaches the network --
  // a weaker fix that merely de-duplicated the RESULT could still pass a
  // content-only assertion below without this one.
  expect(requestCount).toBe(1);
  expect(useChatStore.getState().conversations).toEqual([
    { id: "c1", title: "First chat" },
    { id: "c2", title: "Second chat" },
  ]);
  expect(useChatStore.getState().conversationsNextCursor).toBe("page-3-cursor");

  // Sensitivity, the other direction: the guard must RESET once settled, so a
  // later, genuinely sequential load-more still works normally -- a fix that
  // permanently latched the flag `true` would fail this half silently.
  server.use(
    http.get("/api/conversations", ({ request }) => {
      const cursor = new URL(request.url).searchParams.get("cursor");
      expect(cursor).toBe("page-3-cursor");
      return HttpResponse.json({ items: [{ id: "c3", title: "Third chat" }], next_cursor: null });
    }),
  );

  await useChatStore.getState().loadMoreConversations();

  expect(useChatStore.getState().conversations).toEqual([
    { id: "c1", title: "First chat" },
    { id: "c2", title: "Second chat" },
    { id: "c3", title: "Third chat" },
  ]);
  expect(useChatStore.getState().conversationsNextCursor).toBeNull();
});
