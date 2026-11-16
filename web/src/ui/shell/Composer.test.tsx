// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

afterEach(cleanup);

function input() {
  return screen.getByLabelText("Message Poseidon") as HTMLInputElement;
}
function sendButton() {
  return screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
}

describe("Composer", () => {
  it("sends the draft on submit and clears it", () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    fireEvent.change(input(), { target: { value: "hello" } });
    fireEvent.submit(input().closest("form")!);
    expect(onSend).toHaveBeenCalledWith("hello");
    expect(input().value).toBe("");
  });

  it("keeps the send button muted while the draft is blank", () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    expect(sendButton().disabled).toBe(true);
    fireEvent.change(input(), { target: { value: "   " } });
    expect(sendButton().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.change(input(), { target: { value: "x" } });
    expect(sendButton().disabled).toBe(false);
  });

  it("disables the input and never sends while a turn is in flight", () => {
    const onSend = vi.fn();
    render(<Composer disabled onSend={onSend} />);
    expect(input().disabled).toBe(true);
    expect(sendButton().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("takes the focus back when a turn ends (Review Focus 4)", () => {
    const { rerender } = render(<Composer disabled onSend={vi.fn()} />);
    expect(document.activeElement).not.toBe(input());
    rerender(<Composer disabled={false} onSend={vi.fn()} />);
    expect(document.activeElement).toBe(input());
  });

  it("leaves the focus alone when the reader moved it elsewhere during the turn", () => {
    const { rerender } = render(
      <>
        <button type="button">Chats</button>
        <Composer disabled onSend={vi.fn()} />
      </>,
    );
    const chats = screen.getByRole("button", { name: "Chats" });
    chats.focus();
    rerender(
      <>
        <button type="button">Chats</button>
        <Composer disabled={false} onSend={vi.fn()} />
      </>,
    );
    expect(document.activeElement).toBe(chats);
  });

  it("leaves the focus alone when the reader has text selected in the thread", () => {
    const { rerender } = render(
      <>
        <p>Certified answer for Singapore.</p>
        <Composer disabled onSend={vi.fn()} />
      </>,
    );
    const text = screen.getByText("Certified answer for Singapore.").firstChild as Text;
    const range = document.createRange();
    range.setStart(text, 0);
    range.setEnd(text, 9);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    rerender(
      <>
        <p>Certified answer for Singapore.</p>
        <Composer disabled={false} onSend={vi.fn()} />
      </>,
    );
    expect(document.activeElement).not.toBe(input());
    selection.removeAllRanges();
  });
});
