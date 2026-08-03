import { afterEach, expect, test, vi } from "vitest";
import {
  ApiError,
  getFeedback,
  getMe,
  getMessages,
  listConversations,
  setAuthTokenProvider,
} from "./client";
import { streamTurn } from "./sse";

function jsonResponse(body: unknown, init?: ResponseInit): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

/** A closed, empty SSE body: streamTurn's read loop sees `done: true` on
 * its very first `reader.read()` and returns immediately -- these tests
 * care only about the OUTBOUND request's headers, never about parsing
 * any events out of the response. */
function emptySseResponse(): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.close();
    },
  });
  return new Response(stream, { status: 200 });
}

afterEach(() => {
  setAuthTokenProvider(null);
  vi.unstubAllGlobals();
});

test("the injector attaches an Authorization header to BOTH apiFetch and streamTurn requests (one shared builder)", async () => {
  // Pinned by Phase 9 Task 4 (the streamTurn/apiFetch carryforward): this
  // assertion would FAIL if streamTurn issued its own separate fetch call
  // that never consulted the injector -- see this task's own report for
  // the RED proof captured while sse.ts still bypassed the builder.
  const seenAuthHeaders: (string | null)[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    seenAuthHeaders.push(headers.get("Authorization"));
    return String(input).includes("/messages")
      ? emptySseResponse()
      : jsonResponse({ items: [], next_cursor: null });
  });
  vi.stubGlobal("fetch", fetchMock);
  setAuthTokenProvider(() => Promise.resolve("test-token-123"));

  await listConversations(); // apiFetch call site
  await streamTurn("c1", "hello", "turn-key-1", () => undefined); // streamTurn call site

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(seenAuthHeaders).toEqual(["Bearer test-token-123", "Bearer test-token-123"]);
});

test("no token provider set -> requests carry no Authorization header", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    jsonResponse({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);

  await listConversations();

  const [, init] = fetchMock.mock.calls[0];
  expect(new Headers(init?.headers).has("Authorization")).toBe(false);
});

test("a token provider returning null omits the header rather than sending 'Bearer null'", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    jsonResponse({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);
  setAuthTokenProvider(() => Promise.resolve(null));

  await listConversations();

  const [, init] = fetchMock.mock.calls[0];
  expect(new Headers(init?.headers).has("Authorization")).toBe(false);
});

test("getMe returns the parsed identity on 200", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      jsonResponse({
        sub: "dev|local",
        name: "Dev User",
        email: "dev@local",
        roles: ["Poseidon:Sales"],
        identity_mode: "disabled",
      }),
    ),
  );

  const identity = await getMe();

  expect(identity.identity_mode).toBe("disabled");
  expect(identity.roles).toEqual(["Poseidon:Sales"]);
});

test("getMe throws an ApiError carrying the RFC-7807 body on a non-2xx response", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      jsonResponse(
        {
          type: "about:blank",
          title: "missing bearer token",
          detail: "no Authorization header",
          status: 401,
        },
        { status: 401 },
      ),
    ),
  );

  await expect(getMe()).rejects.toBeInstanceOf(ApiError);
  await expect(getMe()).rejects.toMatchObject({
    status: 401,
    problem: { title: "missing bearer token", detail: "no Authorization header" },
  });
});

test("ApiError.problem is null when the failure body is not JSON", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("not json", { status: 500 })),
  );

  await expect(getMe()).rejects.toMatchObject({ status: 500, problem: null });
});

// Phase 10 Task 4: the real backend (poseidon.api.live_chat, byte-pinned in
// backend/tests/test_history_cutover.py) wraps both list endpoints in
// `{items: [...], next_cursor: str | null}` instead of the old bare
// `{conversations: [...]}`/`{messages: [...]}` arrays. These pin the
// envelope pass-through AND the cursor query-param forwarding that makes
// `Sidebar`'s load-more control (and any future "load more messages")
// possible.

test("listConversations returns the {items, next_cursor} envelope untouched", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      jsonResponse({
        items: [{ id: "c1", title: "First chat" }],
        next_cursor: "opaque-cursor-1",
      }),
    ),
  );

  const page = await listConversations();

  expect(page).toEqual({
    items: [{ id: "c1", title: "First chat" }],
    next_cursor: "opaque-cursor-1",
  });
});

test("listConversations forwards a given cursor as a ?cursor= query param", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    jsonResponse({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);

  await listConversations("opaque-cursor-1");

  const [url] = fetchMock.mock.calls[0];
  expect(String(url)).toBe("/api/conversations?cursor=opaque-cursor-1");
});

test("listConversations omits the query string entirely when no cursor is given", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    jsonResponse({ items: [], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);

  await listConversations();

  const [url] = fetchMock.mock.calls[0];
  expect(String(url)).toBe("/api/conversations");
});

test("getMessages returns the {items, next_cursor} envelope and forwards a given cursor", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    jsonResponse({ items: [{ id: "m1", role: "assistant", parts: [] }], next_cursor: null }));
  vi.stubGlobal("fetch", fetchMock);

  const page = await getMessages("c1", "opaque-cursor-2");

  const [url] = fetchMock.mock.calls[0];
  expect(String(url)).toBe("/api/conversations/c1/messages?cursor=opaque-cursor-2");
  expect(page).toEqual({
    items: [{ id: "m1", role: "assistant", parts: [] }],
    next_cursor: null,
  });
});

// Phase 12 Task 2: GET /api/messages/{mid}/feedback -- 200 with the recorded
// verdict, or 404 (no distinction made in the body between "no feedback
// yet" and "message invisible" -- task-2-brief's own note; the caller,
// chatStore's hydrateFeedback, is the one that treats any 404 as a no-op).
test("getFeedback returns the parsed {verdict, comment} body on 200", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => jsonResponse({ verdict: "up", comment: null })),
  );

  const result = await getFeedback("a1");

  expect(result).toEqual({ verdict: "up", comment: null });
});

test("getFeedback throws an ApiError on a 404 -- the caller decides what that means", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      jsonResponse(
        { type: "about:blank", title: "unknown message", detail: "", status: 404 },
        { status: 404 },
      )),
  );

  await expect(getFeedback("a1")).rejects.toMatchObject({ status: 404 });
});

test("streamTurn sends the caller-supplied client_turn_key verbatim, never minting its own", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    emptySseResponse());
  vi.stubGlobal("fetch", fetchMock);

  await streamTurn("c1", "hello", "the-exact-key-the-caller-chose", () => undefined);

  const [, init] = fetchMock.mock.calls[0];
  const body = JSON.parse(String(init?.body)) as { text: string; client_turn_key: string };
  expect(body).toEqual({ text: "hello", client_turn_key: "the-exact-key-the-caller-chose" });
});
