import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupServer } from "msw/node";
import { vi, beforeAll, beforeEach, afterAll, afterEach, test, expect } from "vitest";
import { handlers } from "../../mocks/handlers";
import { useChatStore } from "../../state/chatStore";
import type { SseEvent } from "../../api/types";

vi.mock("../../api/sse", () => ({
  streamTurn: vi.fn(async (_cid: string, _text: string, onEvent: (e: SseEvent) => void) => {
    const events: SseEvent[] = [
      { name: "accepted", data: { turn_id: "t1", message_id: "a1", event_seq: 1, turn_index: 1 } },
      { name: "tool", data: { turn_id: "t1", message_id: "a1", event_seq: 2, tool_seq: 1, tool: "top_customers", server: "internal", status: "start", label: "Running skill · top_customers…" } },
      { name: "tool", data: { turn_id: "t1", message_id: "a1", event_seq: 3, tool_seq: 1, tool: "top_customers", server: "internal", status: "done", label: "top_customers · done · 0.3s" } },
      { name: "token", data: { turn_id: "t1", message_id: "a1", event_seq: 4, text: "Three customers drove April." } },
      { name: "done", data: { turn_id: "t1", message_id: "a1", event_seq: 5, usage: {} } },
    ];
    events.forEach(onEvent);
  }),
}));

import ChatScreen from "./ChatScreen";

const server = setupServer(...handlers);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// The store is a module singleton: reset it so the tests stay order-independent.
beforeEach(() => {
  useChatStore.setState({
    conversations: [],
    activeId: null,
    messages: {},
    streamingByConv: {},
    feedback: {},
  });
});

test("send → streamed answer with visible tool step", async () => {
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "top gp customers singapore{Enter}");
  await waitFor(() =>
    expect(screen.getByText(/top_customers · done · 0.3s/)).toBeInTheDocument());
  expect(screen.getByText(/Three customers drove April./)).toBeInTheDocument();
});

test("thumbs down opens the comment prompt and submits", async () => {
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  // Wait for the streamed answer: the opener is an assistant message too, so
  // without this the test would pass against its feedback row even if the send
  // never happened.
  await waitFor(() => screen.getByText(/Three customers drove April./));
  await waitFor(() => screen.getAllByLabelText("Bad response"));
  await userEvent.click(screen.getAllByLabelText("Bad response").at(-1)!);
  const box = await screen.findByPlaceholderText(/what went wrong/i);
  await userEvent.type(box, "numbers look off");
  await userEvent.click(screen.getByRole("button", { name: /send feedback/i }));
  await waitFor(() =>
    expect(screen.queryByPlaceholderText(/what went wrong/i)).not.toBeInTheDocument());
});

test("skills picker inserts an example prompt", async () => {
  render(<ChatScreen />);
  await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));
  await userEvent.click(screen.getByText(/Metric query/));
  expect(screen.getByPlaceholderText(/message poseidon/i)).toHaveValue(
    "Top GP customers for Port of Singapore in April 2026");
});
