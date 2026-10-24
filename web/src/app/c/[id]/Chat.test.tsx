// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  chat.state.sendMessage.mockClear();
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

/** The thread's scroll container: the `<ol>` is the only list Chat renders. */
function thread() {
  return screen.getByRole("list").parentElement as HTMLDivElement;
}

/** jsdom has no layout, so fake the three numbers the scroll logic reads. */
function fakeScrollBox(el: HTMLElement, { scrollHeight, clientHeight }: { scrollHeight: number; clientHeight: number }) {
  let top = 0;
  Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => scrollHeight });
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => clientHeight });
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => top,
    set: (value: number) => {
      top = value;
    },
  });
}

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

  it("follows the end of the thread while the reader is at the end (Review Focus 3)", () => {
    chat.state.messages = [user];
    const { rerender } = render(<Chat conversationId="c1" initialMessages={[]} />);
    const box = thread();
    fakeScrollBox(box, { scrollHeight: 1000, clientHeight: 400 });
    chat.state.messages = [user, assistant];
    rerender(<Chat conversationId="c1" initialMessages={[]} />);
    expect(box.scrollTop).toBe(1000);
  });

  it("leaves a reader who scrolled up where they are while new content arrives (Review Focus 3)", () => {
    chat.state.messages = [user];
    const { rerender } = render(<Chat conversationId="c1" initialMessages={[]} />);
    const box = thread();
    fakeScrollBox(box, { scrollHeight: 1000, clientHeight: 400 });
    box.scrollTop = 100;
    fireEvent.scroll(box);
    chat.state.messages = [user, assistant];
    rerender(<Chat conversationId="c1" initialMessages={[]} />);
    expect(box.scrollTop).toBe(100);
  });

  it("returns to the end of the thread when the reader sends a message (Review Focus 3)", () => {
    chat.state.messages = [user];
    const { rerender } = render(<Chat conversationId="c1" initialMessages={[]} />);
    const box = thread();
    fakeScrollBox(box, { scrollHeight: 1000, clientHeight: 400 });
    box.scrollTop = 100;
    fireEvent.scroll(box);
    const input = screen.getByLabelText("Message Poseidon") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "and by port?" } });
    fireEvent.submit(input.closest("form")!);
    expect(chat.state.sendMessage).toHaveBeenCalledWith({ text: "and by port?" });
    chat.state.messages = [user, assistant, { id: "u2", role: "user", parts: [{ type: "text", text: "and by port?" }] }];
    rerender(<Chat conversationId="c1" initialMessages={[]} />);
    expect(box.scrollTop).toBe(1000);
  });

  it("keeps the last message's chips clickable after an errored turn (Review Focus 4)", () => {
    const chips: PoseidonUIMessage = {
      id: "a2",
      role: "assistant",
      parts: [{ type: "data-chips", data: { options: [{ id: "x", label: "Pacific Basin" }] } } as PoseidonUIMessage["parts"][number]],
    };
    chat.state.status = "error";
    chat.state.error = new Error("stream ended without a terminal frame");
    chat.state.messages = [user, chips];
    render(<Chat conversationId="c1" initialMessages={[]} />);
    const chip = screen.getByText("Pacific Basin") as HTMLButtonElement;
    expect(chip.disabled).toBe(false);
    fireEvent.click(chip);
    expect(chat.state.sendMessage).toHaveBeenCalledWith({ text: "Pacific Basin" });
  });
});
