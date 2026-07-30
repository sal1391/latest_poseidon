import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
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

// Final-review wave item 2 (I1 + M6): send_text is SCOPED to clarification
// chips only -- a blanket "for " prefix would corrupt the opener's own flow
// chips into "for Existing customer", which the customer resolver reads as
// customer_unknown. Both directions pinned here, at the ChipsPart level
// (registry.test.tsx already exercises other part kinds through
// PartRenderer directly, in isolation from the full ChatScreen).

test("clicking a clarification chip sends its send_text, not its bare label", async () => {
  const onChipSelect = vi.fn();
  render(
    <PartRenderer
      part={{
        kind: "chips",
        payload: {
          options: [
            {
              id: "Meridian Shipping",
              label: "Meridian Shipping",
              send_text: "for Meridian Shipping",
            },
          ],
        },
      }}
      onChipSelect={onChipSelect}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Meridian Shipping" }));

  expect(onChipSelect).toHaveBeenCalledWith("Meridian Shipping", "for Meridian Shipping");
});

test("clicking a chip with no send_text (an opener flow chip) still sends its bare label", async () => {
  const onChipSelect = vi.fn();
  render(
    <PartRenderer
      part={{
        kind: "chips",
        payload: { options: [{ id: "existing_customer", label: "Existing customer" }] },
      }}
      onChipSelect={onChipSelect}
    />,
  );

  await userEvent.click(screen.getByRole("button", { name: "Existing customer" }));

  expect(onChipSelect).toHaveBeenCalledWith("existing_customer", "Existing customer");
});

test("unknown kind falls back safely", () => {
  // Phase 8 Task 1 registers metric_grid/artifact for real (see the two
  // renderer tests below) -- a kind with no registered renderer at all is
  // what this test needs, so it now names one no phase has claimed.
  render(<PartRenderer part={{ kind: "some_future_part_kind", payload: { anything: 1 } }} />);
  expect(screen.getByText(/unsupported part: some_future_part_kind/i)).toBeInTheDocument();
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

// The metric_grid payload below is captured verbatim from the backend's own
// P3 tool test (backend/poseidon/tasks/data_qa/skills/metric_query/tests/
// test_tools.py::test_format_parts_metric_with_compare_builds_metric_grid),
// the ONE real producer of this part kind today -- reused rather than
// invented, per the same "captured, not guessed at" discipline the
// table/proof tests above already follow.

test("renders a metric_grid part as a card per metric with both periods' values", () => {
  render(
    <PartRenderer
      part={{
        kind: "metric_grid",
        payload: {
          periods: {
            a: { start: "2026-04-01", end: "2026-05-01" },
            b: { start: "2025-04-01", end: "2025-05-01" },
          },
          metrics: [
            { name: "GP", friendly: "Gross Profit", a: 204000, b: 180501, unit: "USD" },
            { name: "VOLUME", friendly: "Volume", a: 4103, b: 3900, unit: "tons" },
          ],
        },
      }}
    />,
  );

  expect(screen.getByText("Gross Profit")).toBeInTheDocument();
  expect(screen.getByText("204,000 USD")).toBeInTheDocument();
  expect(screen.getByText("180,501 USD")).toBeInTheDocument();
  expect(screen.getByText("Volume")).toBeInTheDocument();
  expect(screen.getByText("4,103 tons")).toBeInTheDocument();
  expect(screen.getByText("3,900 tons")).toBeInTheDocument();
});

test("renders an em dash for a metric_grid side with no data, never a blank or a zero", () => {
  // format_parts.py's own "one side empty is still an answer" rule: a
  // comparison where only ONE period has no rows renders that side as
  // `None`, not a fabricated 0 -- the renderer must show the same honest
  // absence, never a number that could be mistaken for a real measurement.
  render(
    <PartRenderer
      part={{
        kind: "metric_grid",
        payload: {
          periods: {
            a: { start: "2026-04-01", end: "2026-05-01" },
            b: { start: "2025-04-01", end: "2025-05-01" },
          },
          metrics: [{ name: "GP", friendly: "Gross Profit", a: 204000, b: null, unit: "USD" }],
        },
      }}
    />,
  );

  expect(screen.getByText("204,000 USD")).toBeInTheDocument();
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("renders a metric_grid value with no certified unit as a bare number", () => {
  // MARGIN/WIN_RATE's own format_parts.py display rule: no unit at all
  // (see _DISPLAY_OVERRIDES) -- the renderer must not print "undefined" or
  // "null" where a unit would otherwise sit.
  render(
    <PartRenderer
      part={{
        kind: "metric_grid",
        payload: {
          periods: {
            a: { start: "2026-04-01", end: "2026-05-01" },
            b: { start: "2025-04-01", end: "2025-05-01" },
          },
          metrics: [{ name: "MARGIN", friendly: "Margin", a: 50.04, b: 48.5, unit: null }],
        },
      }}
    />,
  );

  expect(screen.getByText("50.04")).toBeInTheDocument();
  expect(screen.getByText("48.5")).toBeInTheDocument();
});

test("renders an artifact part as a download link plus a mime badge", () => {
  render(
    <PartRenderer
      part={{
        kind: "artifact",
        payload: {
          name: "brief.pdf",
          url: "https://example.test/artifacts/brief.pdf",
          mime: "application/pdf",
        },
      }}
    />,
  );

  const link = screen.getByRole("link", { name: "brief.pdf" });
  expect(link).toHaveAttribute("href", "https://example.test/artifacts/brief.pdf");
  expect(screen.getByText("application/pdf")).toBeInTheDocument();
});
