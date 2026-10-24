// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Shell } from "./Shell";

afterEach(cleanup);

function show() {
  return render(
    <Shell sidebar={<button type="button">Row one</button>}>
      <p>page body</p>
    </Shell>,
  );
}

describe("Shell", () => {
  it("renders the sidebar slot and the page inside main", () => {
    show();
    expect(screen.getByText("Row one")).toBeTruthy();
    expect(screen.getByRole("main").textContent).toContain("page body");
  });

  it("the Chats toggle opens and closes the overlay", () => {
    show();
    const toggle = screen.getByRole("button", { name: "Chats" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: "Close conversations" })).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Close conversations" }));
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("picking something inside the sidebar closes the overlay", () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    fireEvent.click(screen.getByText("Row one"));
    expect(screen.getByRole("button", { name: "Chats" }).getAttribute("aria-expanded")).toBe("false");
  });
});
