// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const params = vi.hoisted(() => ({ current: {} as Record<string, string> }));
vi.mock("next/navigation", () => ({ useParams: () => params.current }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: ComponentProps<"a"> & { href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("../../lib/actions", () => ({ newConversation: vi.fn() }));

import { Sidebar } from "./Sidebar";

afterEach(() => {
  cleanup();
  params.current = {};
});

const LONG = "x".repeat(120);
const rows = [
  { id: "11111111-1111-7111-8111-111111111111", title: "Acme GP by port" },
  { id: "22222222-2222-7222-8222-222222222222", title: null },
  { id: "33333333-3333-7333-8333-333333333333", title: LONG },
];

describe("Sidebar", () => {
  it("shows the brand, the New chat button and the signed-in sub", () => {
    render(<Sidebar conversations={rows} sub="dev|alice" />);
    expect(screen.getByText("Poseidon")).toBeTruthy();
    expect(screen.getByRole("button", { name: "New chat" })).toBeTruthy();
    expect(screen.getByText(/Signed in as dev\|alice/)).toBeTruthy();
  });

  it("links every conversation and marks the active one with aria-current", () => {
    params.current = { id: rows[0].id };
    render(<Sidebar conversations={rows} sub="dev|alice" />);
    const active = screen.getByRole("link", { name: "Acme GP by port" });
    expect(active.getAttribute("href")).toBe(`/c/${rows[0].id}`);
    expect(active.getAttribute("aria-current")).toBe("page");
    const untitled = screen.getByRole("link", { name: "(untitled)" });
    expect(untitled.getAttribute("aria-current")).toBeNull();
    expect(untitled.className).toContain("text-ink-muted");
    expect(/text-ink(?!-)/.test(untitled.className)).toBe(false);
    expect(active.className).toContain("text-ink");
    expect(active.className).not.toContain("text-ink-muted");
  });

  it("carries a long title in the title attribute so truncation loses nothing", () => {
    render(<Sidebar conversations={rows} sub="dev|alice" />);
    expect(screen.getByRole("link", { name: LONG }).getAttribute("title")).toBe(LONG);
  });

  it("says so when there are no conversations", () => {
    render(<Sidebar conversations={[]} sub="dev|alice" />);
    expect(screen.getByText("No conversations yet")).toBeTruthy();
  });
});
