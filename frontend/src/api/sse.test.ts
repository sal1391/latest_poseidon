import { afterEach, vi } from "vitest";
import { parseSseChunk, StreamError, streamTurn } from "./sse";

afterEach(() => {
  vi.unstubAllGlobals();
});

/** A stream that delivers `frames` and then either closes cleanly or
 * errors (a simulated network drop) -- verified directly (this task's own
 * RED-phase debugging) that `pull`, not `start`, is where the error must
 * fire: enqueueing a chunk and erroring in the SAME synchronous tick
 * discards the queued chunk entirely, so the reader's first `read()` would
 * never see it. */
function sseResponse(frames: string[], { thenError = false } = {}): Response {
  const encoder = new TextEncoder();
  let errored = false;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      if (!thenError) controller.close();
    },
    pull(controller) {
      if (thenError && !errored) {
        errored = true;
        controller.error(new Error("simulated network drop"));
      }
    },
  });
  return new Response(stream, { status: 200 });
}

test("parses enveloped events (ignoring id: lines) and keeps the incomplete tail", () => {
  const raw =
    'id: 1\nevent: accepted\ndata: {"turn_id":"t1","message_id":"m1","event_seq":1,"turn_index":1}\n\n' +
    'id: 2\nevent: token\ndata: {"turn_id":"t1","message_id":"m1","event_seq":2,"text":"Hello"}\n\n' +
    "id: 3\nevent: token\ndata: {\"te";
  const { events, rest } = parseSseChunk(raw);
  expect(events).toEqual([
    { name: "accepted", data: { turn_id: "t1", message_id: "m1", event_seq: 1, turn_index: 1 } },
    { name: "token", data: { turn_id: "t1", message_id: "m1", event_seq: 2, text: "Hello" } },
  ]);
  expect(rest).toBe('id: 3\nevent: token\ndata: {"te');
});

test("returns no events for a bare fragment", () => {
  const { events, rest } = parseSseChunk("event: to");
  expect(events).toEqual([]);
  expect(rest).toBe("event: to");
});

test("tolerates CRLF framing", () => {
  const raw =
    'id: 1\r\nevent: token\r\ndata: {"turn_id":"t1","message_id":"m1","event_seq":1,"text":"Hi"}\r\n\r\n';
  const { events, rest } = parseSseChunk(raw);
  expect(events).toEqual([
    { name: "token", data: { turn_id: "t1", message_id: "m1", event_seq: 1, text: "Hi" } },
  ]);
  expect(rest).toBe("");
});

test("skips a malformed frame and keeps parsing", () => {
  const raw =
    'event: token\ndata: {"turn_id":"t1","message_id":"m1","event_seq":1,"text":"a"}\n\n' +
    "event: token\ndata: {not json}\n\n" +
    'event: token\ndata: {"turn_id":"t1","message_id":"m1","event_seq":3,"text":"b"}\n\n';
  const { events } = parseSseChunk(raw);
  expect(events.map((e) => (e.data as { text?: string }).text)).toEqual(["a", "b"]);
});

// Phase 11 Task 3 (doc 01 section 5, client rule 3): streamTurn's own error
// path -- chatStore.ts's on-drop reconcile hook reads StreamError.turnId
// back to decide whether GET /api/turns/{id} is even worth attempting.

test("a stream that errors mid-turn throws StreamError carrying the last turn_id it saw", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () =>
      sseResponse(
        [
          'id: 1\nevent: accepted\ndata: {"turn_id":"turn-drop-1","message_id":"a1","event_seq":1,"turn_index":2}\n\n',
        ],
        { thenError: true },
      )),
  );
  const seen: unknown[] = [];

  await expect(
    streamTurn("c1", "hello", "key1", (e) => seen.push(e)),
  ).rejects.toMatchObject({ turnId: "turn-drop-1" });

  // The event before the drop still reached the caller -- a reconcile is a
  // recovery path for what came AFTER, never a reason to withhold what
  // already streamed successfully.
  expect(seen).toHaveLength(1);
});

test("a stream that fails before any frame arrives throws StreamError with turnId null", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 500 })));

  await expect(streamTurn("c1", "hello", "key1", () => undefined)).rejects.toMatchObject({
    turnId: null,
  });
});

test("StreamError thrown by streamTurn is always an instance of StreamError", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 500 })));

  await expect(streamTurn("c1", "hello", "key1", () => undefined)).rejects.toBeInstanceOf(
    StreamError,
  );
});
