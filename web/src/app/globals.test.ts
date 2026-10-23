import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "globals.css"), "utf8");
const layout = readFileSync(join(__dirname, "layout.tsx"), "utf8");

describe("globals.css theme (spec 3.1)", () => {
  it.each([
    ["--color-surface", "#ffffff"],
    ["--color-surface-raised", "#f7f7f8"],
    ["--color-ink", "#202123"],
    ["--color-ink-muted", "#8e9196"],
    ["--color-border", "#e5e5e5"],
    ["--color-accent", "#1f5f8b"],
    ["--color-accent-ink", "#ffffff"],
    ["--color-send", "#202123"],
    ["--color-positive", "#10a37f"],
    ["--color-negative", "#d92d20"],
    ["--color-warn-bg", "#fef3c7"],
    ["--color-warn-ink", "#92400e"],
    ["--radius-chip", "8px"],
    ["--radius-card", "12px"],
    ["--radius-pill", "24px"],
  ])("defines %s as %s inside @theme", (name, value) => {
    const theme = css.slice(css.indexOf("@theme"), css.indexOf("}", css.indexOf("@theme")));
    expect(theme).toContain(`${name}: ${value};`);
  });

  it("is light only: no dark-scheme media query", () => {
    expect(css).not.toMatch(/prefers-color-scheme:\s*dark/);
  });

  it("uses the Segoe UI system stack and no webfont", () => {
    expect(css).toContain('--font-sans: "Segoe UI", system-ui, -apple-system, sans-serif;');
    expect(layout).not.toContain("next/font");
    expect(layout.toLowerCase()).not.toContain("geist");
  });

  it("pins the body to the viewport so the thread scrolls internally, not the page", () => {
    expect(css).toMatch(/html,\s*body\s*\{[^}]*height:\s*100%/);
    expect(css).toMatch(/body\s*\{[^}]*overflow:\s*hidden/);
  });
});
