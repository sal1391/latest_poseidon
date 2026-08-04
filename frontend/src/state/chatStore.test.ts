import type { Message, SseEvent } from "../api/types";
import * as api from "../api/client";
import { streamTurn } from "../api/sse";
import { applyEventTo, resetChatStore, useChatStore } from "./chatStore";

// Phase 11 Task 3: chatStore.ts now also imports StreamError (a real class,
// used via `instanceof` in its own catch block) from this module -- partial
// mock via importOriginal, so StreamError stays the REAL class (an
// `instanceof` check against `undefined` would throw TypeError) while
// streamTurn stays a plain stand-in, exactly as every test in this file
// already expects.
vi.mock("../api/sse", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/sse")>();
  return { ...actual, streamTurn: vi.fn(async () => undefined) };
});

// Phase 10 Task 4: both list endpoints now return a `{items, next_cursor}`
// envelope (byte-pinned against the real backend in
// backend/tests/test_history_cutover.py), not a bare array -- these
// defaults mirror that shape so `bootstrap`/`openConversation` see exactly
// what the real `api/client.ts` hands them.
vi.mock("../api/client", () => ({
  listConversations: vi.fn(async () => ({ items: [], next_cursor: null })),
  createConversation: vi.fn(async () => ({
    conversation: { id: "c1", title: "New chat" },
    opener: { id: "m0", role: "assistant" as const, parts: [] },
  })),
  getMessages: vi.fn(async () => ({ items: [], next_cursor: null })),
  postFeedback: vi.fn(async () => undefined),
}));

const env = (event_seq: number) => ({ turn_id: "t1", message_id: "a1", event_seq });

const seq: SseEvent[] = [
  { name: "accepted", data: { ...env(1), turn_index: 1 } },
  { name: "tool", data: { ...env(2), tool_seq: 1, tool: "top_customers", server: "internal", status: "start", label: "Running…" } },
  { name: "tool", data: { ...env(3), tool_seq: 1, tool: "top_customers", server: "internal", status: "done", label: "done · 0.3s" } },
  { name: "token", data: { ...env(4), text: "Hello " } },
  { name: "token", data: { ...env(5), text: "world" } },
];

function run(events: SseEvent[], initial: Message[] = []): Message[] {
  return events.reduce((msgs, e) => applyEventTo(msgs, e), initial);
}

beforeEach(() => {
  resetChatStore();
  vi.clearAllMocks();
});

test("builds an assistant message with in-place tool updates and merged tokens", () => {
  const msgs = run(seq);
  expect(msgs).toHaveLength(1);
  const parts = msgs[0].parts;
  expect(parts).toHaveLength(2);
  expect(parts[0].kind).toBe("tool_event");
  expect((parts[0].payload as { status: string }).status).toBe("done");
  expect((parts[0].payload as { turn_id?: string }).turn_id).toBeUndefined();
  expect((parts[1].payload as { markdown: string }).markdown).toBe("Hello world");
});

test("accepted is idempotent and duplicate deliveries are skipped by event_seq", () => {
  const msgs = run([seq[0], seq[0], seq[3], seq[3]]);
  expect(msgs).toHaveLength(1);
  expect((msgs[0].parts[0].payload as { markdown: string }).markdown).toBe("Hello ");
});

test("events for an unseen message_id create the message (replay-safe)", () => {
  const msgs = run([{ name: "token", data: { ...env(4), text: "late" } }]);
  expect(msgs).toHaveLength(1);
  expect(msgs[0].id).toBe("a1");
});

test("error event appends an error part", () => {
  const msgs = run([seq[0], { name: "error", data: { ...env(2), code: "x", message: "boom" } }]);
  expect(msgs[0].parts.at(-1)?.kind).toBe("error");
});

test("concurrent bootstraps share one run and open a single conversation", async () => {
  const { bootstrap } = useChatStore.getState();

  await Promise.all([bootstrap(), bootstrap()]);

  expect(vi.mocked(api.listConversations)).toHaveBeenCalledTimes(1);
  expect(vi.mocked(api.createConversation)).toHaveBeenCalledTimes(1);
  const state = useChatStore.getState();
  expect(state.conversations).toEqual([{ id: "c1", title: "New chat" }]);
  expect(state.activeId).toBe("c1");
  expect(state.messages.c1).toHaveLength(1);
});

test("a second send while a turn is streaming is dropped", async () => {
  let release!: () => void;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  vi.mocked(streamTurn).mockImplementationOnce(() => held);
  const { sendMessage } = useChatStore.getState();

  const first = sendMessage("c1", "a");
  const second = sendMessage("c1", "b"); // fired mid-stream, must no-op

  expect(vi.mocked(streamTurn)).toHaveBeenCalledTimes(1);
  expect(useChatStore.getState().messages.c1).toHaveLength(1);
  expect(useChatStore.getState().streamingByConv.c1).toBe(true);

  release();
  await Promise.all([first, second]);

  expect(useChatStore.getState().streamingByConv.c1).toBe(false);
  expect(useChatStore.getState().messages.c1).toHaveLength(1);
});

// Phase 10 Task 4 -- carryforward closed: `client_turn_key` generation
// hoisted out of `sse.ts` and into `sendMessage`, so a caller that wants to
// RETRY the same logical send (the backend's own `(user_sub,
// client_turn_key)` short-circuit in orchestrator.py's `_begin_turn` --
// see poseidon-carryforwards.md's "Phase 6" entry) can pass the key back in
// and have it reused verbatim, rather than every attempt minting a fresh
// one and defeating that idempotency check. Both directions asserted
// together (a "sensitivity" pin): a version of `sendMessage` that always
// mints fresh would fail the second test; a version that always reuses (or
// derives the key from `cid`/`text` alone -- content-based "replay"
// detection, deliberately NOT this task's job, see doc-08's own "true
// replay stays P11") would fail the first.
test("sendMessage mints a fresh client_turn_key for each NEW logical send", async () => {
  const { sendMessage } = useChatStore.getState();

  await sendMessage("c1", "a");
  await sendMessage("c1", "a"); // same cid+text, but a SEPARATE send -- no retry key passed

  const calls = vi.mocked(streamTurn).mock.calls;
  expect(calls).toHaveLength(2);
  const [, , firstKey] = calls[0];
  const [, , secondKey] = calls[1];
  expect(typeof firstKey).toBe("string");
  expect(firstKey).not.toBe(secondKey);
});

test("sendMessage reuses an explicitly-passed client_turn_key (a retry of the same logical send)", async () => {
  const { sendMessage } = useChatStore.getState();

  await sendMessage("c1", "a");
  const [, , firstKey] = vi.mocked(streamTurn).mock.calls[0];

  await sendMessage("c1", "a", firstKey); // the retry path: caller hands the SAME key back

  const [, , secondKey] = vi.mocked(streamTurn).mock.calls[1];
  expect(secondKey).toBe(firstKey);
});

// Phase 10 Task 3 (backend) added an additive `"title": str | null` to the
// `done` frame, non-null exactly once (the turn that first names the
// conversation) -- this is the frontend half of that carryforward
// ("done -> conversation-title refresh", poseidon-carryforwards.md's
// "Phase 6" entry).
test("a done event carrying a title updates the matching conversation in place", () => {
  useChatStore.setState({
    conversations: [
      { id: "c1", title: "New chat" },
      { id: "other", title: "Untouched" },
    ],
  });

  useChatStore.getState().applyEvent("c1", {
    name: "done",
    data: { turn_id: "t1", message_id: "a1", event_seq: 9, usage: {}, title: "Atlas Bunkering follow-up" },
  });

  expect(useChatStore.getState().conversations).toEqual([
    { id: "c1", title: "Atlas Bunkering follow-up" },
    { id: "other", title: "Untouched" },
  ]);
});

// Phase 12 Task 4 (page-order amendment, 2026-08-04): "Load earlier
// messages" -- consumes messagesNextCursor[cid], PREPENDS the fetched
// (older) page in front of what is already loaded, since get_messages now
// walks strictly backward in time on every subsequent page.
test("loadEarlierMessages does nothing when there is no cursor for the conversation", async () => {
  useChatStore.setState({
    messages: { c1: [{ id: "m1", role: "assistant", parts: [] }] },
    messagesNextCursor: { c1: null },
  });

  await useChatStore.getState().loadEarlierMessages("c1");

  expect(api.getMessages).not.toHaveBeenCalled();
  expect(useChatStore.getState().messages.c1).toHaveLength(1);
});

test("loadEarlierMessages prepends the fetched older page and advances the cursor", async () => {
  const existing: Message = { id: "m3", role: "user", parts: [] };
  const older1: Message = { id: "m1", role: "assistant", parts: [] };
  const older2: Message = { id: "m2", role: "user", parts: [] };
  useChatStore.setState({
    messages: { c1: [existing] },
    messagesNextCursor: { c1: "cursor-1" },
  });
  vi.mocked(api.getMessages).mockResolvedValueOnce({
    items: [older1, older2],
    next_cursor: "cursor-2",
  });

  await useChatStore.getState().loadEarlierMessages("c1");

  expect(api.getMessages).toHaveBeenCalledWith("c1", "cursor-1");
  // Older page goes IN FRONT, in the order it arrived (already oldest-to-
  // newest within itself), never appended and never reordered.
  expect(useChatStore.getState().messages.c1).toEqual([older1, older2, existing]);
  expect(useChatStore.getState().messagesNextCursor.c1).toBe("cursor-2");
});

test("loadEarlierMessages sets the opener once the fetched page's own next_cursor goes null", async () => {
  const existing: Message = { id: "m2", role: "user", parts: [] };
  const trueOpener: Message = { id: "m1", role: "assistant", parts: [] };
  useChatStore.setState({
    messages: { c1: [existing] },
    messagesNextCursor: { c1: "cursor-1" },
    openerIdByConv: {},
  });
  vi.mocked(api.getMessages).mockResolvedValueOnce({
    items: [trueOpener],
    next_cursor: null, // this was the last (oldest) page -- trueOpener really is the opener
  });

  await useChatStore.getState().loadEarlierMessages("c1");

  expect(useChatStore.getState().openerIdByConv.c1).toBe("m1");
});

// The P10 lesson (review finding I-1, `loadMoreConversations`'s own
// in-flight guard) applied to loadEarlierMessages: a second call arriving
// before the first's fetch resolves must not read the same (stale) cursor
// and prepend the same page twice.
test("loadEarlierMessages in-flight guard: a second call before the first resolves is dropped", async () => {
  let release!: () => void;
  const held = new Promise<{ items: Message[]; next_cursor: string | null }>((resolve) => {
    release = () => resolve({ items: [{ id: "m1", role: "assistant", parts: [] }], next_cursor: null });
  });
  vi.mocked(api.getMessages).mockReturnValueOnce(held);
  useChatStore.setState({
    messages: { c1: [{ id: "m2", role: "user", parts: [] }] },
    messagesNextCursor: { c1: "cursor-1" },
  });

  const first = useChatStore.getState().loadEarlierMessages("c1");
  const second = useChatStore.getState().loadEarlierMessages("c1"); // fired mid-fetch, must no-op

  expect(api.getMessages).toHaveBeenCalledTimes(1);
  release();
  await Promise.all([first, second]);

  expect(useChatStore.getState().messages.c1).toHaveLength(2); // prepended exactly once
  expect(useChatStore.getState().loadingEarlierMessages.c1).toBe(false);
});

test("a done event with a null title (every turn after the first) leaves conversation titles alone", () => {
  useChatStore.setState({ conversations: [{ id: "c1", title: "Atlas Bunkering follow-up" }] });

  useChatStore.getState().applyEvent("c1", {
    name: "done",
    data: { turn_id: "t2", message_id: "a2", event_seq: 20, usage: {}, title: null },
  });

  expect(useChatStore.getState().conversations).toEqual([
    { id: "c1", title: "Atlas Bunkering follow-up" },
  ]);
});
