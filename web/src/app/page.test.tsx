// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/actions", () => ({ newConversation: vi.fn() }));

import Home from "./page";

afterEach(cleanup);

describe("root page (empty state, spec 3.2)", () => {
  it("greets, explains in one line, and offers New chat", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Ask Poseidon");
    expect(screen.getByText("Breakdowns, comparisons, briefs and monthly reports for your book.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "New chat" })).toBeTruthy();
  });

  it("no longer lists conversations itself (the sidebar does)", () => {
    render(<Home />);
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByText(/Conversations for/)).toBeNull();
  });
});
