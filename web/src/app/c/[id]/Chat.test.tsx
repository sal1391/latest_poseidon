// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PoseidonUIMessage } from "../../../lib/message-parts";

const chat = vi.hoisted(() => ({
  lastOptions: undefined as undefined | { onFinish?: () => void },
  state: {
    messages: [] as PoseidonUIMessage[],
    sendMessage: vi.fn(),
    status: "ready" as "ready" | "submitted" | "streaming" | "error",
    error: undefined as Error | undefined,
  },
}));
vi.mock("@ai-sdk/react", () => ({
  useChat: (options: { onFinish?: () => void }) => {
    chat.lastOptions = options;
    return chat.state;
  },
}));
const nav = vi.hoisted(() => ({ refresh: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: nav.refresh }) }));
vi.mock("ai", () => ({
  DefaultChatTransport: class {
    constructor(_options: unknown) {}
  },
}));

import { Chat } from "./Chat";

afterEach(() => {
  cleanup();
  nav.refresh.mockClear();
  chat.lastOptions = undefined;
  chat.state.messages = [];
  chat.state.status = "ready";
  chat.state.error = undefined;
});

const user: PoseidonUIMessage = { id: "u1", role: "user", parts: [{ type: "text", text: "GP for Acme\nby port" }] };
// The shape `fromMessageParts` builds for a persisted text part (message-parts.ts:48).
const assistant: PoseidonUIMessage = {
  id: "a1",
  role: "assistant",
  parts: [{ type: "text", text: "Here it is.", state: "done" }],
};

describe("Chat", () => {
  it("refreshes the server-rendered sidebar and header when a turn finishes", () => {
    render(<Chat conversationId="c1" initialMessages={[]} />);
    chat.lastOptions!.onFinish!();
    expect(nav.refresh).toHaveBeenCalledTimes(1);
  });

  it("renders user text as a bubble with its line breaks and no role labels", () => {
    chat.state.messages = [user, assistant];
    render(<Chat conversationId="c1" initialMessages={[]} />);
    const bubble = screen.getByText(/GP for Acme/);
    expect(bubble.textContent).toBe("GP for Acme\nby port");
    expect(screen.getByText("Here it is.")).toBeTruthy();
    expect(screen.queryByText("user")).toBeNull();
    expect(screen.queryByText("assistant")).toBeNull();
  });

  it("mutes the composer while a turn is in flight", () => {
    chat.state.status = "streaming";
    render(<Chat conversationId="c1" initialMessages={[]} />);
    expect((screen.getByLabelText("Message Poseidon") as HTMLInputElement).disabled).toBe(true);
  });

  it("shows a failed turn and re-enables the composer so the user can retry", () => {
    chat.state.status = "error";
    chat.state.error = new Error("stream ended without a terminal frame");
    render(<Chat conversationId="c1" initialMessages={[]} />);
    expect(screen.getByText("stream ended without a terminal frame")).toBeTruthy();
    expect((screen.getByLabelText("Message Poseidon") as HTMLInputElement).disabled).toBe(false);
  });

  it("scrolls the end of the thread into view when messages change", () => {
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;
    chat.state.messages = [user, assistant];
    render(<Chat conversationId="c1" initialMessages={[]} />);
    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "end" });
  });
});
