import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import { resetChatStore, useChatStore } from "../../state/chatStore";
import { Sidebar } from "./Sidebar";

beforeEach(() => {
  resetChatStore();
});

test("renders no load-more control when next_cursor is null", () => {
  act(() => {
    useChatStore.setState({
      conversations: [{ id: "c1", title: "First chat" }],
      conversationsNextCursor: null,
    });
  });

  render(<Sidebar />);

  expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
});

test("renders a load-more control when next_cursor is non-null, and clicking it calls loadMoreConversations", async () => {
  const loadMoreConversations = vi.fn(async () => undefined);
  act(() => {
    useChatStore.setState({
      conversations: [{ id: "c1", title: "First chat" }],
      conversationsNextCursor: "opaque-cursor-1",
      loadMoreConversations,
    });
  });

  render(<Sidebar />);
  const button = screen.getByRole("button", { name: /load more/i });
  await userEvent.click(button);

  expect(loadMoreConversations).toHaveBeenCalledTimes(1);
});

// Phase 10 Task 4 -- the frontend half of "done -> conversation-title
// refresh" (poseidon-carryforwards.md's "Phase 6" entry): this drives the
// REAL `applyEvent` store action (not a stub), so it proves the whole path
// from SSE event to re-rendered sidebar row, not just the store's own state
// shape.
test("a done event's title re-renders the matching sidebar row", () => {
  act(() => {
    useChatStore.setState({ conversations: [{ id: "c1", title: "New chat" }] });
  });

  render(<Sidebar />);
  expect(screen.getByRole("button", { name: "New chat" })).toBeInTheDocument();

  act(() => {
    useChatStore.getState().applyEvent("c1", {
      name: "done",
      data: {
        turn_id: "t1",
        message_id: "a1",
        event_seq: 9,
        usage: {},
        title: "Atlas Bunkering follow-up",
      },
    });
  });

  expect(screen.getByRole("button", { name: "Atlas Bunkering follow-up" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New chat" })).not.toBeInTheDocument();
});
