import type { Message, SseEvent } from "../api/types";
import * as api from "../api/client";
import { streamTurn } from "../api/sse";
import { applyEventTo, resetChatStore, useChatStore } from "./chatStore";

vi.mock("../api/sse", () => ({ streamTurn: vi.fn(async () => undefined) }));

vi.mock("../api/client", () => ({
  listConversations: vi.fn(async () => []),
  createConversation: vi.fn(async () => ({
    conversation: { id: "c1", title: "New chat" },
    opener: { id: "m0", role: "assistant" as const, parts: [] },
  })),
  getMessages: vi.fn(async () => []),
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
