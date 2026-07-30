import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { vi, beforeAll, beforeEach, afterAll, afterEach, test, expect } from "vitest";
import { handlers } from "../../mocks/handlers";
import { resetChatStore, useChatStore } from "../../state/chatStore";
import { streamTurn } from "../../api/sse";
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

// The store is a module singleton (state plus the bootstrap memo): reset both
// so the tests stay order-independent.
beforeEach(resetChatStore);

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

test("composer is disabled while a send waits on the bootstrap window", async () => {
  // Bootstrap still in flight and the turn never settles, so `streamingByConv`
  // cannot be what disables the input — only ChatScreen's own `sending` flag can.
  // The GET never resolves at all -- a timed `delay()` used to stand in here,
  // but this test asserts and returns long before that timer fires, leaving
  // the delayed response an orphaned continuation that fired seconds later,
  // mid-suite, inside a LATER test's own render: it replayed bootstrap
  // against whatever conversation was active at that moment, restamping the
  // shared store's `conversations`/`activeId`/`messages`. A promise that
  // never settles leaves nothing running past this test's own lifetime.
  //
  // No `streamTurn` mock override here (fix round 1: one used to sit here,
  // pointlessly -- this test's own send() blocks at `await bootstrap()`
  // and never reaches `streamTurn` at all, so the queued one-time override
  // was never consumed by this test; it sat in vitest's mock queue and was
  // silently consumed by whichever LATER test called `streamTurn` next
  // instead, handing that unrelated test a promise that never resolves).
  server.use(http.get("/api/conversations", () => new Promise<never>(() => {})));

  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  expect(input).not.toBeDisabled();
  await userEvent.type(input, "hello{Enter}");
  expect(input).toBeDisabled();
});

test("a send in one conversation leaves another conversation's composer live", async () => {
  // The turn never settles. A boolean `sending` flag would keep the composer
  // disabled everywhere for its whole life — including in a conversation that
  // has no turn of its own running.
  vi.mocked(streamTurn).mockImplementationOnce(() => new Promise<void>(() => {}));

  render(<ChatScreen />);
  // The opener only renders once bootstrap has landed, so waiting on it is what
  // guarantees the send below is tied to c1 rather than to the null window.
  await screen.findByText(/Ask about your data/);
  const input = screen.getByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  expect(input).toBeDisabled();

  act(() => {
    useChatStore.setState((s) => ({
      conversations: [{ id: "c2", title: "Second chat" }, ...s.conversations],
      activeId: "c2",
      messages: { ...s.messages, c2: [] },
    }));
  });
  expect(screen.getByPlaceholderText(/message poseidon/i)).not.toBeDisabled();
});

test("skills picker inserts an example prompt", async () => {
  render(<ChatScreen />);
  await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));
  await userEvent.click(screen.getByText(/Metric query/));
  expect(screen.getByPlaceholderText(/message poseidon/i)).toHaveValue(
    "Top GP customers for Port of Singapore in April 2026");
});

test("clicking a chip sends its entry phrase as a user message, not just a composer insert", async () => {
  render(<ChatScreen />);
  // The opener's own flow chips (mocks/handlers.ts's mockOpener) -- waiting
  // for them is what guarantees bootstrap has landed and activeId is set.
  await screen.findByText(/Ask about your data/);

  await userEvent.click(screen.getByRole("button", { name: "Existing customer" }));

  // The pinned D19 entry phrase (P8 whole-branch final-review wave,
  // 2026-07-30, item 9 / M-2), not the bare button label: mockOpener now
  // carries send_text on its opener chips, matching api/live_chat.py's own
  // real opener -- this is what gives D19 a frontend-side pin (before this
  // fix, the mock's own missing send_text let this assertion pass while
  // checking something no real backend click ever sends).
  expect(streamTurn).toHaveBeenCalledWith(
    "c1", "start an existing-customer brief", expect.any(Function));
  // The composer itself was never populated -- this went straight through
  // the send path, unlike SkillsPicker's insert-only affordance above.
  expect(screen.getByPlaceholderText(/message poseidon/i)).toHaveValue("");
  await waitFor(() =>
    expect(screen.getByText(/Three customers drove April./)).toBeInTheDocument());
});

test("a chip is disabled while its conversation's send is in flight", async () => {
  vi.mocked(streamTurn).mockImplementationOnce(() => new Promise<void>(() => {}));
  render(<ChatScreen />);
  await screen.findByText(/Ask about your data/);

  await userEvent.click(screen.getByRole("button", { name: "Existing customer" }));

  expect(screen.getByRole("button", { name: "Existing customer" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "New customer prospect" })).toBeDisabled();
});

test("backend-unreachable bootstrap surfaces a retry banner instead of a silent no-op", async () => {
  // Both requests bootstrap can issue (list, then create-if-empty) fail, so the
  // banner has to come from the catch path rather than from a specific request.
  server.use(
    http.get("/api/conversations", () => new HttpResponse(null, { status: 500 })),
    http.post("/api/conversations", () => new HttpResponse(null, { status: 500 })),
  );

  render(<ChatScreen />);

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/can't reach the poseidon backend/i);
  const retry = screen.getByRole("button", { name: /retry/i });

  // Back to the happy-path handlers before retrying, same as the backend coming
  // back up.
  server.resetHandlers();
  await userEvent.click(retry);

  await screen.findByText(/Ask about your data/);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
