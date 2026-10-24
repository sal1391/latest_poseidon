// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PartRenderer } from "./registry";

afterEach(cleanup);

describe("PartRenderer", () => {
  it("renders markdown text", () => {
    render(<PartRenderer part={{ kind: "text", payload: { markdown: "**bold** words" } }} />);
    expect(screen.getByText("bold").tagName).toBe("STRONG");
  });

  it("renders markdown lists with real list items inside the prose scope (Review Focus 1)", () => {
    const { container } = render(<PartRenderer part={{ kind: "text", payload: { markdown: "## Ports\n\n- Singapore\n- Rotterdam\n\nSee `GP` at [docs](https://example.invalid/)." } }} />);
    const item = screen.getByText("Singapore");
    expect(item.tagName).toBe("LI");
    expect(item.parentElement!.tagName).toBe("UL");
    expect(item.closest(".prose-poseidon")).toBeTruthy();
    expect(screen.getByText("Ports").tagName).toBe("H2");
    expect(screen.getByText("GP").tagName).toBe("CODE");
    expect((screen.getByText("docs") as HTMLAnchorElement).href).toBe("https://example.invalid/");
    expect(container.querySelector(".prose-poseidon")).toBeTruthy();
  });

  it("phase sections set their title as a heading inside the summary and their body in the prose scope", () => {
    render(<PartRenderer part={{ kind: "phase_section", payload: { title: "Contextualize", markdown: "**done**" } }} />);
    const title = screen.getByText("Contextualize");
    expect(title.tagName).toBe("H2");
    expect(title.closest("summary")).toBeTruthy();
    expect(screen.getByText("done").closest(".prose-poseidon")).toBeTruthy();
  });

  it("chips send send_text, falling back to the label, and honour disabled", () => {
    const onChipSelect = vi.fn();
    render(
      <PartRenderer
        part={{ kind: "chips", payload: { options: [{ id: "a", label: "Existing customer", send_text: "start an existing-customer brief" }, { id: "b", label: "Plain" }] } }}
        onChipSelect={onChipSelect}
      />,
    );
    fireEvent.click(screen.getByText("Existing customer"));
    fireEvent.click(screen.getByText("Plain"));
    expect(onChipSelect.mock.calls).toEqual([["a", "start an existing-customer brief"], ["b", "Plain"]]);
    cleanup();
    render(<PartRenderer part={{ kind: "chips", payload: { options: [{ id: "a", label: "X" }] } }} onChipSelect={onChipSelect} disabled />);
    expect((screen.getByText("X") as HTMLButtonElement).disabled).toBe(true);
  });

  it("tool events show the label and status", () => {
    render(<PartRenderer part={{ kind: "tool_event", payload: { tool_seq: 1, tool: "data_qa.metric_query", server: null, status: "done", label: "Metric query" } }} />);
    expect(screen.getByText(/Metric query/)).toBeTruthy();
  });

  it("tool events show a pulsing dot while running (P16-R18), not a static glyph", () => {
    const { container } = render(
      <PartRenderer part={{ kind: "tool_event", payload: { tool_seq: 1, tool: "data_qa.metric_query", server: null, status: "start", label: "Metric query" } }} />,
    );
    expect(screen.getByText("Metric query")).toBeTruthy();
    expect(container.querySelector(".animate-pulse")).toBeTruthy();
  });

  it("tables render columns and rows", () => {
    render(<PartRenderer part={{ kind: "table", payload: { columns: ["Customer", "GP"], rows: [["Acme", 12.5]] } }} />);
    expect(screen.getByText("Acme")).toBeTruthy();
    expect(screen.getByText("12.5")).toBeTruthy();
  });

  it("metric grids render friendly names and a dash for null", () => {
    render(<PartRenderer part={{ kind: "metric_grid", payload: { periods: { a: { start: "2025-01-01", end: "2025-03-31" }, b: { start: "2025-04-01", end: "2025-06-30" } }, metrics: [{ name: "GP", friendly: "Gross profit", a: 1000, b: null, unit: "USD" }] } }} />);
    expect(screen.getByText("Gross profit")).toBeTruthy();
    expect(screen.getByText("1,000")).toBeTruthy();
  });

  it("artifacts render a plain download anchor", () => {
    render(<PartRenderer part={{ kind: "artifact", payload: { name: "brief.pdf", url: "https://example.invalid/b.pdf", mime: "application/pdf" } }} />);
    const a = screen.getByText("brief.pdf").closest("a")!;
    expect(a.getAttribute("href")).toBe("https://example.invalid/b.pdf");
  });

  it("proof is collapsed, phase sections are open, errors show the message, unknown kinds fall back to JSON", () => {
    render(<PartRenderer part={{ kind: "proof", payload: { lines: ["SELECT 1"] } }} />);
    expect((screen.getByText("How this was computed").closest("details") as HTMLDetailsElement).open).toBe(false);
    cleanup();
    render(<PartRenderer part={{ kind: "phase_section", payload: { title: "Contextualize", markdown: "done" } }} />);
    expect((screen.getByText("Contextualize").closest("details") as HTMLDetailsElement).open).toBe(true);
    cleanup();
    render(<PartRenderer part={{ kind: "error", payload: { code: "x", message: "It broke" } }} />);
    expect(screen.getByText("It broke")).toBeTruthy();
    cleanup();
    render(<PartRenderer part={{ kind: "hologram", payload: { any: 1 } }} />);
    expect(screen.getByText(/"any": 1/)).toBeTruthy();
  });
});
