import { describe, expect, it } from "vitest";
import { monthLabel, reportFileName, sanitizeFilename } from "./report-files";

describe("sanitizeFilename", () => {
  it("replaces hostile characters", () => {
    expect(sanitizeFilename('Rotterdam/"x" Office')).toBe("Rotterdam__x_ Office");
  });
  it("collapses whitespace runs and trims", () => {
    expect(sanitizeFilename("a   b")).toBe("a b");
    expect(sanitizeFilename("  a b  ")).toBe("a b");
  });
  it("falls back to report when empty", () => {
    expect(sanitizeFilename("")).toBe("report");
    expect(sanitizeFilename("   ")).toBe("report");
  });
});

describe("monthLabel", () => {
  it("formats the first day as Month YYYY", () => {
    expect(monthLabel("2026-06-01")).toBe("June 2026");
  });
});

describe("reportFileName", () => {
  it("names a whole-book month-to-date report", () => {
    expect(
      reportFileName({ officeValue: null, periodCurrentStart: "2026-10-01", windowKind: "mtd" }, "pdf"),
    ).toBe("Whole book October 2026 month to date.pdf");
  });
});
