import { render, screen } from "@testing-library/react";
import { PartRenderer } from "./registry";

test("renders markdown text", () => {
  render(<PartRenderer part={{ kind: "text", payload: { markdown: "**bold** move" } }} />);
  expect(screen.getByText("bold")).toBeInTheDocument();
});

test("renders tool event with done glyph", () => {
  render(<PartRenderer part={{ kind: "tool_event", payload: {
    tool_seq: 1, tool: "t", server: "internal", status: "done", label: "top_customers · done" } }} />);
  expect(screen.getByText(/top_customers · done/)).toBeInTheDocument();
  expect(screen.getByText(/✓/)).toBeInTheDocument();
});

test("unknown kind falls back safely", () => {
  render(<PartRenderer part={{ kind: "metric_grid", payload: { anything: 1 } }} />);
  expect(screen.getByText(/unsupported part: metric_grid/i)).toBeInTheDocument();
});
