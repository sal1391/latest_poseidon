// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PartRenderer } from "../message-parts/registry";
import type { ReportPayload } from "./types";

afterEach(cleanup);

const CORPUS = join(__dirname, "..", "..", "..", "..", "contracts", "turns");
const golden = JSON.parse(readFileSync(join(CORPUS, "report_turn.json"), "utf8")) as { parts: { kind: string; payload: unknown }[] };
const payload = golden.parts.find((p) => p.kind === "report")!.payload as ReportPayload;

function show(p: ReportPayload = payload) {
  return render(<PartRenderer part={{ kind: "report", payload: p }} />);
}

describe("ReportPart", () => {
  it("renders the scope and the period labels", () => {
    show();
    expect(screen.getByRole("heading", { level: 3 }).textContent).toBe(payload.scope.label);
    expect(screen.getByText(/June 2026 vs May 2026/)).toBeTruthy();
    expect(screen.queryByText("Month to date")).toBeNull();
  });

  it("renders seven kpi tiles in payload order", () => {
    const { container } = show();
    const metrics = [...container.querySelectorAll("[data-metric]")].map((e) => e.getAttribute("data-metric"));
    expect(metrics).toEqual(Object.keys(payload.kpis));
    expect(metrics).toHaveLength(7);
  });

  it("switches ranked tables and shows a tfoot equal to the gp tile", () => {
    const { container } = show();
    expect(screen.getByText("Customers by GP").getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByText("Ports by GP"));
    expect(screen.getByText("Ports by GP").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByText("Customers by GP").getAttribute("aria-selected")).toBe("false");
    const ranked = container.querySelector("table")!;
    expect(within(ranked).getByText("Port")).toBeTruthy();
    const foot = ranked.querySelectorAll("tfoot tr td");
    expect(foot[2].textContent).toBe(payload.kpis.GP.display.current);
  });

  it("renders movers with drivers and reconciliation lines", () => {
    show();
    expect(screen.getByText("Increases")).toBeTruthy();
    expect(screen.getByText("Decreases")).toBeTruthy();
    expect(screen.getAllByText("volume-driven").length).toBeGreaterThan(0);
    const key = payload.movers.decreases[0].key;
    const ctx = payload.entity_context[key];
    const d = ctx.reconciliation.display;
    expect(screen.getByText(`Office ${d.office_change} | other offices ${d.other_offices_net} | book ${d.book_change}`)).toBeTruthy();
    expect(screen.getByText(`share of book ${ctx.share_of_book.display.prior} -> ${ctx.share_of_book.display.current}`)).toBeTruthy();
  });

  it("narrative never renders links or html", () => {
    const { container } = show({
      ...payload,
      narrative: { ...payload.narrative, grounded: false, template_md: "see [x](javascript:alert(1)) and <img src=x onerror=alert(1)>" },
    });
    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("see x and");
  });

  it("proof is collapsed", () => {
    show();
    expect((screen.getByText("Proof").closest("details") as HTMLDetailsElement).open).toBe(false);
  });

  it("an mtd payload shows the badge", () => {
    show({ ...payload, periods: { ...payload.periods, window_kind: "mtd" } });
    expect(screen.getByText("Month to date")).toBeTruthy();
  });
});
