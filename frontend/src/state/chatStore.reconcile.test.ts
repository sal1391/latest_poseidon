import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import type { SseEvent } from "../api/types";
import { StreamError, streamTurn } from "../api/sse";
import { resetChatStore, useChatStore } from "./chatStore";

/**
 * Phase 11 Task 3 (doc 01 section 5, client rule 3): "On connection drop
 * [the client] calls GET /api/turns/{turn_id} to reconcile from the run log
 * ... rather than replaying the model." This is the frontend half of that
 * contract -- a THIN hook: when `sendMessage`'s own stream throws mid-turn
 * (a `StreamError` carrying a known ``turnId``), it fetches `GET
 * /api/turns/:id` and materializes the recovered parts into the
 * conversation, instead of the generic `stream_failed` error bubble.
 *
 * `streamTurn` itself is mocked (its own StreamError/turn_id-capturing
 * contract is proven directly, against a real stream, in `sse.test.ts` --
 * re-proving stream mechanics here would only re-test sse.ts through an
 * extra layer of indirection). `api/client.ts`'s `getTurn` is NOT mocked --
 * this suite drives it over REAL MSW (the `chatStore.pagination.test.ts`
 * precedent, not `chatStore.test.ts`'s own full-module `vi.mock`), so the
 * request path and the folded response shape are proven against something
 * that looks like the real backend, never a hand-rolled stub that could
 * silently drift from it.
 */
vi.mock("../api/sse", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/sse")>();
  return { ...actual, streamTurn: vi.fn() };
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

beforeEach(() => {
  resetChatStore();
  vi.clearAllMocks();
});

test("a stream that errors mid-turn with a known turn_id fetches getTurn and materializes the recovered parts", async () => {
  vi.mocked(streamTurn).mockRejectedValueOnce(
    new StreamError("simulated network drop", "turn-drop-1"),
  );
  let getTurnRequests = 0;
  server.use(
    http.get("/api/turns/turn-drop-1", () => {
      getTurnRequests += 1;
      return HttpResponse.json({
        turn: {
          id: "turn-drop-1",
          conversation_id: "c1",
          message_id: "recovered-1",
          kind: "chat_turn",
          status: "ok",
          question: "hello",
          mode: "default",
          created_at: "2026-08-01T00:00:00Z",
          finished_at: "2026-08-01T00:00:01Z",
          trace_id: null,
          redacted: false,
        },
        llm_calls: [],
        tool_calls: [],
        message: {
          id: "recovered-1",
          parts: [{ kind: "text", payload: { markdown: "the reconciled answer" } }],
        },
      });
    }),
  );

  await useChatStore.getState().sendMessage("c1", "hello");

  expect(getTurnRequests).toBe(1);
  const messages = useChatStore.getState().messages.c1;
  const last = messages[messages.length - 1];
  expect(last.id).toBe("recovered-1");
  expect(last.role).toBe("assistant");
  expect(last.parts).toEqual([{ kind: "text", payload: { markdown: "the reconciled answer" } }]);
  // The generic stream_failed bubble must NOT also be present -- reconcile
  // REPLACES it, never merely supplements it.
  expect(messages.some((m) => m.parts.some((p) => p.kind === "error"))).toBe(false);
});

test("a stream drop after accepted+part frames merges the reconciled message by id instead of duplicating it", async () => {
  /**
   * P11 whole-branch final-review wave, 2026-08-01, item 3 / I-3. Every
   * OTHER test in this file mocks `streamTurn` to reject immediately, so
   * `onEvent` is never called and the store never gains the "accepted"
   * placeholder message before the reconcile branch runs -- precisely the
   * one state the REAL code cannot be in: `StreamError.turnId` is non-null
   * only when at least one frame arrived (sse.ts's own `lastTurnId`), and
   * the first frame of every turn is "accepted", which `applyEventTo`
   * already pushes into the store under this exact `message_id`. This test
   * drives that REAL sequence -- accepted, then a partial "part", then the
   * drop -- through the store's own reducers (`applyEvent`/`applyEventTo`,
   * never a shortcut), so the reconcile hook runs against the state it
   * actually sees in production: a message with the recovered id ALREADY
   * present, carrying partial parts.
   */
  const messageId = "recovered-4";
  const turnId = "turn-drop-4";
  const accepted: SseEvent = {
    name: "accepted",
    data: { turn_id: turnId, message_id: messageId, event_seq: 1, turn_index: 1 },
  };
  const part: SseEvent = {
    name: "part",
    data: {
      turn_id: turnId,
      message_id: messageId,
      event_seq: 2,
      kind: "text",
      payload: { markdown: "partial before the drop" },
    },
  };
  vi.mocked(streamTurn).mockImplementationOnce(async (_cid, _text, _turnKey, onEvent) => {
    onEvent(accepted);
    onEvent(part);
    throw new StreamError("simulated network drop", turnId);
  });
  server.use(
    http.get(`/api/turns/${turnId}`, () =>
      HttpResponse.json({
        turn: {
          id: turnId,
          conversation_id: "c1",
          message_id: messageId,
          kind: "chat_turn",
          status: "ok",
          question: "hello",
          mode: "default",
          created_at: "2026-08-01T00:00:00Z",
          finished_at: "2026-08-01T00:00:01Z",
          trace_id: null,
          redacted: false,
        },
        llm_calls: [],
        tool_calls: [],
        message: {
          id: messageId,
          parts: [{ kind: "text", payload: { markdown: "the reconciled answer" } }],
        },
      }),
    ),
  );

  await useChatStore.getState().sendMessage("c1", "hello");

  const messages = useChatStore.getState().messages.c1;
  const matches = messages.filter((m) => m.id === messageId);
  // Exactly ONE message survives with this id -- not the partial one, not a
  // second, duplicate entry with the same id (ChatScreen.tsx's own
  // `key={message.id}` would otherwise collide, React-warn, and risk DOM
  // node reuse across the two entries).
  expect(matches).toHaveLength(1);
  expect(matches[0].role).toBe("assistant");
  expect(matches[0].parts).toEqual([{ kind: "text", payload: { markdown: "the reconciled answer" } }]);
  expect(messages.some((m) => m.parts.some((p) => p.kind === "error"))).toBe(false);
});

test("no reconcile call when the stream completes normally", async () => {
  vi.mocked(streamTurn).mockResolvedValueOnce(undefined);
  // Deliberately NO handler for GET /api/turns/* -- onUnhandledRequest:
  // "error" (above) means a reconcile call here would fail this test
  // loudly, proving the hook never fires on a clean completion, not merely
  // that it swallows a network error.

  await useChatStore.getState().sendMessage("c1", "hello");

  // sendMessage resolved with no thrown error and no MSW violation --
  // exactly today's pre-existing, unchanged happy path.
  expect(useChatStore.getState().streamingByConv.c1).toBe(false);
});

test("a stream error with no known turn_id shows the generic error, no reconcile attempted", async () => {
  vi.mocked(streamTurn).mockRejectedValueOnce(new StreamError("turn failed: 500", null));
  // No GET /api/turns/* handler -- a reconcile attempt with no turn_id
  // would have nothing to call anyway, but this also proves it is never
  // even tried.

  await useChatStore.getState().sendMessage("c1", "hello");

  const messages = useChatStore.getState().messages.c1;
  const last = messages[messages.length - 1];
  expect(last.role).toBe("assistant");
  expect(last.parts[0].kind).toBe("error");
});

test("a stream error whose own getTurn reconcile call also fails falls back to the generic error bubble", async () => {
  vi.mocked(streamTurn).mockRejectedValueOnce(
    new StreamError("simulated network drop", "turn-drop-2"),
  );
  server.use(http.get("/api/turns/turn-drop-2", () => new HttpResponse(null, { status: 500 })));

  await useChatStore.getState().sendMessage("c1", "hello");

  const messages = useChatStore.getState().messages.c1;
  const last = messages[messages.length - 1];
  expect(last.role).toBe("assistant");
  expect(last.parts[0].kind).toBe("error");
});

test("a stream error whose getTurn reconcile succeeds but reports no message falls back to the generic error bubble", async () => {
  vi.mocked(streamTurn).mockRejectedValueOnce(
    new StreamError("simulated network drop", "turn-drop-3"),
  );
  server.use(
    http.get("/api/turns/turn-drop-3", () =>
      HttpResponse.json({
        turn: {
          id: "turn-drop-3",
          conversation_id: "c1",
          message_id: null,
          kind: "chat_turn",
          status: "running",
          question: "hello",
          mode: "default",
          created_at: "2026-08-01T00:00:00Z",
          finished_at: null,
          trace_id: null,
          redacted: false,
        },
        llm_calls: [],
        tool_calls: [],
        message: null,
      })),
  );

  await useChatStore.getState().sendMessage("c1", "hello");

  const messages = useChatStore.getState().messages.c1;
  const last = messages[messages.length - 1];
  expect(last.role).toBe("assistant");
  expect(last.parts[0].kind).toBe("error");
});
