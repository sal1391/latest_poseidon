import { afterEach, expect, test, vi } from "vitest";
import { ApiError, getMe, listConversations, setAuthTokenProvider } from "./client";
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
    return String(input).includes("/messages") ? emptySseResponse() : jsonResponse({ conversations: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  setAuthTokenProvider(() => Promise.resolve("test-token-123"));

  await listConversations(); // apiFetch call site
  await streamTurn("c1", "hello", () => undefined); // streamTurn call site

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(seenAuthHeaders).toEqual(["Bearer test-token-123", "Bearer test-token-123"]);
});

test("no token provider set -> requests carry no Authorization header", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    jsonResponse({ conversations: [] }));
  vi.stubGlobal("fetch", fetchMock);

  await listConversations();

  const [, init] = fetchMock.mock.calls[0];
  expect(new Headers(init?.headers).has("Authorization")).toBe(false);
});

test("a token provider returning null omits the header rather than sending 'Bearer null'", async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    jsonResponse({ conversations: [] }));
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
