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

// The table/proof payloads below are the flagship turn's own pinned shapes
// (backend/tests/test_chat_orchestrator.py::
// test_flagship_singapore_top_gp_frame_sequence_and_writer_rows), reused
// verbatim rather than invented, so these tests prove the renderers handle
// the EXACT shape the live orchestrator actually produces.

test("renders a table part with columns as headers and rows as cells", () => {
  render(
    <PartRenderer
      part={{
        kind: "table",
        payload: {
          columns: ["Customer", "Gross Profit"],
          rows: [
            ["Northstar Lines", 412000],
            ["Blue Anchor Marine", 268500],
            ["Crestline Freight", 155250],
          ],
        },
      }}
    />,
  );
  expect(screen.getByRole("columnheader", { name: "Customer" })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Gross Profit" })).toBeInTheDocument();
  expect(screen.getByText("Northstar Lines")).toBeInTheDocument();
  expect(screen.getByText("412000")).toBeInTheDocument();
  expect(screen.getAllByRole("row")).toHaveLength(4); // header + 3 data rows
});

test("renders a proof part as a closed, collapsible block with one line per entry", () => {
  render(
    <PartRenderer
      part={{
        kind: "proof",
        payload: {
          lines: [
            "Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V",
            "Backend: synthetic",
            "Period: 2026-04-01..2026-05-01",
            "Filters: LOC_NM IN (SINGAPORE)",
            "Group by: CUST_NM (top 5)",
            "Rows: 3",
          ],
        },
      }}
    />,
  );
  expect(screen.getByText("How this was computed")).toBeInTheDocument();
  expect(screen.getByText("Entity: SANDBOX.MCA.MARINE_SALES_PLANNING_V")).toBeInTheDocument();
  expect(screen.getByText("Rows: 3")).toBeInTheDocument();
  expect(screen.getByText("How this was computed").closest("details")).not.toHaveAttribute("open");
});
