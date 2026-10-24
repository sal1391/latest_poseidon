# UI design pass, PR 2: the nine chat parts — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restyle the nine chat message-part renderers (plus the fallback) to the slate theme through three tiny primitives, and close PR 1's three deferred thread items (scroll only when the reader is at the end, composer refocus after a turn, chips stay usable after an errored turn).

**Architecture:** Every part keeps its name, file, `PartProps` and behaviour; only classes and markup change. Three primitives under `web/src/ui/primitives/` carry what the parts share: `Disclosure` (the `<details>` shell for Proof, Fallback and PhaseSection), `Badge` (MIME types now, mover drivers and the month-to-date marker in PR 3), and `data-table.ts` (cell class constants for `TablePart` now, the report tables in PR 3). A scoped `.prose-poseidon` rule set and the `.disclosure` caret live in `globals.css`. `Chat.tsx` drops the scroll sentinel for a scroll container that follows the end of the thread only while the reader is there. No change to the SSE/part contract, routes, Drizzle or Python.

**Tech Stack:** Next.js 16.3.4, React 19.2.8, Tailwind 4 via `@tailwindcss/postcss`, react-markdown 10, vitest 5 with jsdom and `@testing-library/react`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-nextjs-ui-design-pass-design.md` sections 4, 6, 7, read with the rulings P1-R1 to P1-R3 in `docs/superpowers/plans/2026-10-23-ui-pass-1-shell.md` and the "Deferred to PR 2" line of its execution record.

**Model split (Carlos's cost rule):** Haiku for Tasks 1, 2 and 3 (the plan carries every file and test in full; transcription plus running the tests). Sonnet for Task 4 (the scroll and focus logic has tests that fake layout, which takes judgment to debug) and Task 5 (docs and figures). A Sonnet reviewer after every task. One Opus whole-branch review at the end, focused on the scroll container (the one place a layout regression hides in this PR), the markdown rules reaching outside their scope, and the primitives' API that PR 3 builds on. Fable supervises, runs the browser walkthrough and never implements.

**Branch:** `feature/ui-pass-2-parts`, cut from `main` @ c1b74f5 (the PR #12 merge).

## Global Constraints

- `web/` only. Nothing under `frontend/` or `backend/` changes. If a task finds it must touch either, stop and report.
- No raw hex, no raw `rgb()`, no `neutral-*`, `red-*`, `blue-*` palette classes in any file this PR touches. Colours are `surface`, `surface-raised`, `ink`, `ink-muted`, `border`, `accent`, `accent-ink`, `negative`, `positive`, `warn-bg`, `warn-ink` (the `@theme` tokens from PR 1).
- Radii per ruling P1-R1: the spec's `rounded-[--radius-m]` is `rounded-card` (12px), `rounded-[--radius-s]` is `rounded-chip` (8px). Numbers per ruling P1-R2: the spec's `data` utility is Tailwind's built-in `tabular-nums`.
- **Ruling P2-R1 (mono font):** the spec's "data font" for `code` and `<pre>` is a `--font-mono` token added to `@theme` (`ui-monospace, "Cascadia Mono", Consolas, monospace`), used as `font-mono` in components and `var(--font-mono)` in the prose rules. Numbers keep `tabular-nums`, never the mono font.
- **Ruling P2-R2 (badge padding):** `Badge` carries `py-0.5` for both its uses (spec §4 omits it, §5 names it); one primitive, one padding.
- Light only: every `dark:` class in `web/src/ui/message-parts/` goes. `web/src/ui/report/` keeps its `dark:` classes until PR 3; the dark sweep is scoped to `message-parts`.
- Type scale: small `text-[13px]`, label `text-[12px]`, heading-2 `text-base font-semibold` (16px), metric value `text-lg font-semibold`.
- Existing tests keep passing unchanged except the two named in Task 2 (the Proof summary text changes to "How this was computed", so the registry test's lookup changes with it) and Task 4 (the scroll test is replaced because the sentinel it asserted on is gone). Each change is in the same commit as the code, with the reason in the commit body.
- Commands: `cd web` first. Single file: `npx vitest run <path>`. Full suite: `npm test > <file> 2>&1`, then read the file. **Never pipe a test run.** Never set `DRIZZLE_DATABASE_URL` in a test shell (the db-backed files must keep skipping). Never run `next dev` or `next build` from a task (Fable runs the dev server for the browser gate); if `web/AGENTS.md` shows as modified, leave it unstaged.
- Before every commit: `git rev-parse --abbrev-ref HEAD` must print `feature/ui-pass-2-parts`; after every commit `git log --oneline main..HEAD` must show only this branch's work. Anything else: stop and report, do not improvise.
- Commit messages via a scratch file and `git commit -F` (PowerShell mangles quotes). Each ends with the two attribution lines in the session's system reminder.

## Review Focus

1. **A markdown answer with a heading, a bulleted list, inline code and a link:** bullets are visible (Tailwind's preflight removes list markers; the prose rules put them back), the heading is 16px semibold, the link is accent. Pinned in Task 2's test (a list item renders as `LI` inside `UL` under `.prose-poseidon`).
2. **A table wider than the 720px column** (twelve columns of amounts): the table scrolls horizontally inside its own wrapper and never widens the thread. Task 3 wraps the table; the browser gate (Task 5) confirms with a wide breakdown.
3. **A reader who scrolled up to re-read while a long answer streams** stays where they are; the moment they send a new message the thread returns to the end. Pinned in Task 4's three scroll tests.
4. **An errored turn:** the chips of the last assistant message stay clickable and the composer gets its focus back when the turn ends. Pinned in Task 4's tests (chips enabled under `status === "error"`; input focused when `disabled` goes false).
5. **A part kind the registry does not know** still renders, with the kind as its summary and the payload readable. The existing fallback test is kept (Task 2).

---

### Task 1: The three primitives and the prose and disclosure rules (Haiku)

**Files:**
- Create: `web/src/ui/primitives/Disclosure.tsx`
- Create: `web/src/ui/primitives/Badge.tsx`
- Create: `web/src/ui/primitives/data-table.ts`
- Create: `web/src/ui/primitives/primitives.test.tsx`
- Modify: `web/src/app/globals.css` (one `@theme` line, two rule blocks appended)
- Modify: `web/src/app/globals.test.ts` (one `describe` appended)

**Interfaces:**
- Consumes: the PR 1 tokens in `globals.css` (`--color-*`, `--radius-chip`, `--radius-card`, `--motion-fast`).
- Produces: `Disclosure({ summary: ReactNode; open?: boolean; section?: boolean; children: ReactNode })`; `Badge({ tone?: "neutral" | "warn"; children: ReactNode })`; constants `TABLE`, `TH`, `TR`, `TD`, `TD_NUM` (strings); CSS classes `.prose-poseidon` and `.disclosure`; token `--font-mono`.

- [ ] **Step 1: Write the failing tests**

Create `web/src/ui/primitives/primitives.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Badge } from "./Badge";
import { Disclosure } from "./Disclosure";

afterEach(cleanup);

describe("Disclosure", () => {
  it("is closed by default and shows the summary and the body", () => {
    render(<Disclosure summary="How this was computed">the body</Disclosure>);
    const details = screen.getByText("How this was computed").closest("details") as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(screen.getByText("the body")).toBeTruthy();
  });

  it("opens when asked, with a heading as its summary", () => {
    render(
      <Disclosure open section summary={<h2>Contextualize</h2>}>
        the body
      </Disclosure>,
    );
    const heading = screen.getByText("Contextualize");
    expect(heading.tagName).toBe("H2");
    expect((heading.closest("details") as HTMLDetailsElement).open).toBe(true);
  });
});

describe("Badge", () => {
  it("renders its text inline", () => {
    render(<Badge>application/pdf</Badge>);
    expect(screen.getByText("application/pdf").tagName).toBe("SPAN");
  });

  it("accepts the warn tone", () => {
    render(<Badge tone="warn">Month to date</Badge>);
    expect(screen.getByText("Month to date")).toBeTruthy();
  });
});
```

Append to `web/src/app/globals.test.ts` (after the existing `describe` block):

```ts
describe("globals.css prose and disclosure rules (spec 4)", () => {
  it("defines the mono font token inside @theme (ruling P2-R1)", () => {
    const theme = css.slice(css.indexOf("@theme"), css.indexOf("}", css.indexOf("@theme")));
    expect(theme).toMatch(/--font-mono: [^;]*monospace;/);
  });

  it("scopes the markdown rules to .prose-poseidon with 16px semibold headings and mono code", () => {
    expect(css).toMatch(/\.prose-poseidon h1,\s*\.prose-poseidon h2,\s*\.prose-poseidon h3\s*\{[^}]*font-size:\s*16px/);
    expect(css).toMatch(/\.prose-poseidon code\s*\{[^}]*var\(--font-mono\)/);
    expect(css).toMatch(/\.prose-poseidon ul\s*\{[^}]*list-style:\s*disc/);
  });

  it("rotates the disclosure caret when the block is open", () => {
    expect(css).toMatch(/\.disclosure\[open\]\s*>\s*summary::before\s*\{[^}]*rotate\(45deg\)/);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run src/ui/primitives/primitives.test.tsx src/app/globals.test.ts`
Expected: the primitives file fails to resolve `./Badge` and `./Disclosure`; the three new globals tests fail on the missing token and rules. The eighteen existing globals tests pass.

- [ ] **Step 3: Write the primitives**

Create `web/src/ui/primitives/Disclosure.tsx`:

```tsx
import type { ReactNode } from "react";

/**
 * The shared `<details>` shell for Proof, Fallback and PhaseSection (spec
 * 4): hairline border, card radius, and a caret that turns when the block
 * opens (the `.disclosure` rules in globals.css). `section` is the roomier
 * variant PhaseSection uses, 12px padding and a plain summary so the title
 * can be a heading; the default is the compact 13px provenance block.
 */
export function Disclosure({
  summary,
  open = false,
  section = false,
  children,
}: {
  summary: ReactNode;
  open?: boolean;
  section?: boolean;
  children: ReactNode;
}) {
  return (
    <details className={`disclosure rounded-card border border-border ${section ? "p-3" : "px-3 py-2"}`} open={open}>
      <summary className={`cursor-pointer ${section ? "" : "text-[13px] font-medium"}`}>{summary}</summary>
      <div className={section ? "mt-2" : "mt-1"}>{children}</div>
    </details>
  );
}
```

Create `web/src/ui/primitives/Badge.tsx`:

```tsx
import type { ReactNode } from "react";

/**
 * A small inline label: the MIME type on an artifact card (spec 4), the
 * mover driver and the month-to-date marker on the report (spec 5). The
 * warn tone is the report's; everything else is neutral.
 */
export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "warn"; children: ReactNode }) {
  const colours = tone === "warn" ? "bg-warn-bg text-warn-ink" : "bg-surface-raised text-ink-muted";
  return <span className={`inline-block rounded-chip px-1.5 py-0.5 text-[12px] ${colours}`}>{children}</span>;
}
```

Create `web/src/ui/primitives/data-table.ts`:

```ts
/**
 * Cell classes shared by TablePart (spec 4) and the report tables (spec 5).
 * Constants rather than a component: the report tables have their own
 * header structure (a spanning row above the column headers) and footer,
 * so what they share with the chat table is the cells, not the markup.
 */
export const TABLE = "w-full border-collapse text-[13px]";
export const TH = "border-b border-border px-2 pb-1 text-left text-[12px] font-medium uppercase tracking-wide text-ink-muted";
export const TR = "hover:bg-surface-raised";
export const TD = "border-b border-border px-2 py-1.5";
export const TD_NUM = `${TD} text-right tabular-nums`;
```

- [ ] **Step 4: Add the token and the rules to the stylesheet**

In `web/src/app/globals.css`, add one line inside the `@theme` block, directly after the `--font-sans` line:

```css
  --font-mono: ui-monospace, "Cascadia Mono", Consolas, monospace;
```

Then append this after the `:focus-visible` rule and before the `@media (prefers-reduced-motion: reduce)` rule:

```css
/* Markdown bodies (spec 4: TextPart and PhaseSection). Scoped to
   .prose-poseidon so none of this reaches the shell. Preflight zeroes
   margins and list markers; these put back the few the prose needs. */
.prose-poseidon {
  font-size: 15px;
  line-height: 1.6;
}
.prose-poseidon p,
.prose-poseidon ul,
.prose-poseidon ol,
.prose-poseidon pre,
.prose-poseidon blockquote {
  margin: 0 0 8px;
}
.prose-poseidon h1,
.prose-poseidon h2,
.prose-poseidon h3 {
  margin: 12px 0 4px;
  font-size: 16px;
  font-weight: 600;
}
.prose-poseidon > :first-child {
  margin-top: 0;
}
.prose-poseidon > :last-child {
  margin-bottom: 0;
}
.prose-poseidon ul {
  list-style: disc;
  padding-left: 1.5em;
}
.prose-poseidon ol {
  list-style: decimal;
  padding-left: 1.5em;
}
.prose-poseidon strong {
  font-weight: 600;
}
.prose-poseidon code {
  font-family: var(--font-mono);
  font-size: 13px;
  padding: 1px 4px;
  border-radius: 4px;
  background: var(--color-surface-raised);
}
.prose-poseidon pre {
  overflow-x: auto;
  padding: 8px 12px;
  border-radius: var(--radius-chip);
  background: var(--color-surface-raised);
}
.prose-poseidon pre code {
  padding: 0;
  background: none;
}
.prose-poseidon a {
  color: var(--color-accent);
  text-decoration: none;
}
.prose-poseidon a:hover {
  text-decoration: underline;
}

/* The Disclosure primitive's caret (spec 4): the native marker goes, a
   rotated border corner takes its place and turns when the block opens. */
.disclosure > summary {
  display: flex;
  align-items: center;
  gap: 8px;
  list-style: none;
}
.disclosure > summary::-webkit-details-marker {
  display: none;
}
.disclosure > summary::before {
  content: "";
  flex: none;
  width: 6px;
  height: 6px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: rotate(-45deg);
  transition: transform var(--motion-fast);
}
.disclosure[open] > summary::before {
  transform: rotate(45deg);
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/ui/primitives/primitives.test.tsx src/app/globals.test.ts`
Expected: 4 primitives tests and 21 globals tests pass.

- [ ] **Step 6: Type-check and commit**

Run: `cd web && npx tsc --noEmit` (expected: clean).

```bash
git rev-parse --abbrev-ref HEAD   # must print feature/ui-pass-2-parts
git add web/src/ui/primitives web/src/app/globals.css web/src/app/globals.test.ts
git commit -F <scratch message file>
git log --oneline main..HEAD      # only this branch's work
```

Message: `feat(web): Disclosure, Badge and data-table primitives; prose and disclosure rules (UI pass PR 2, Task 1)`.

---

### Task 2: Text, Proof, Fallback and PhaseSection on the primitives (Haiku)

**Files:**
- Modify: `web/src/ui/message-parts/TextPart.tsx`
- Modify: `web/src/ui/message-parts/ProofPart.tsx`
- Modify: `web/src/ui/message-parts/FallbackPart.tsx`
- Modify: `web/src/ui/message-parts/PhaseSectionPart.tsx`
- Modify: `web/src/ui/message-parts/registry.test.tsx`

**Interfaces:**
- Consumes: `Disclosure` from Task 1; `.prose-poseidon` from Task 1.
- Produces: nothing new; the four components keep their names and `PartProps`.

- [ ] **Step 1: Update and extend the registry test**

In `web/src/ui/message-parts/registry.test.tsx`, in the last test (`"proof is collapsed, phase sections are open, ..."`), change the Proof lookup from `screen.getByText("Proof")` to `screen.getByText("How this was computed")`. The reason for the commit body: spec §4 renames the Proof summary.

Add these two tests inside the `describe("PartRenderer", ...)` block, after `"renders markdown text"`:

```tsx
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
```

- [ ] **Step 2: Run the registry test to verify the new and changed tests fail**

Run: `cd web && npx vitest run src/ui/message-parts/registry.test.tsx`
Expected: the Proof lookup fails ("How this was computed" is not rendered yet), the list test fails on `.prose-poseidon` missing, the phase-section test fails on the H2. The other tests pass.

- [ ] **Step 3: Rewrite the four components**

`web/src/ui/message-parts/TextPart.tsx`:

```tsx
import Markdown from "react-markdown";
import type { PartProps, TextPayload } from "./registry";

/** Renders `text` parts as markdown (react-markdown's defaults) inside the
 * `.prose-poseidon` rule set from globals.css (spec 4). */
export function TextPart({ part }: PartProps) {
  const { markdown } = part.payload as TextPayload;
  return (
    <div className="prose-poseidon">
      <Markdown>{markdown}</Markdown>
    </div>
  );
}
```

`web/src/ui/message-parts/ProofPart.tsx`:

```tsx
import { Disclosure } from "../primitives/Disclosure";
import type { PartProps, ProofPayload } from "./registry";

/** Renders `proof` parts as a collapsible provenance block, one line per
 * `payload.lines` entry, closed by default (unchanged). The summary reads
 * "How this was computed" (spec 4). */
export function ProofPart({ part }: PartProps) {
  const { lines } = part.payload as ProofPayload;
  return (
    <Disclosure summary="How this was computed">
      <ul className="list-inside list-disc space-y-1 text-[13px] text-ink-muted">
        {lines.map((line, index) => (
          <li key={index}>{line}</li>
        ))}
      </ul>
    </Disclosure>
  );
}
```

`web/src/ui/message-parts/FallbackPart.tsx`:

```tsx
import { Disclosure } from "../primitives/Disclosure";
import type { PartProps } from "./registry";

/** Safe fallback for any part kind the registry doesn't recognize yet: the
 * kind as the summary, the payload as JSON in the mono font (spec 4). */
export function FallbackPart({ part }: PartProps) {
  const { kind, payload } = part;
  return (
    <Disclosure summary={kind}>
      <pre className="overflow-x-auto font-mono text-[12px]">{JSON.stringify(payload, null, 2)}</pre>
    </Disclosure>
  );
}
```

`web/src/ui/message-parts/PhaseSectionPart.tsx`:

```tsx
import Markdown from "react-markdown";
import { Disclosure } from "../primitives/Disclosure";
import type { PartProps, PhaseSectionPayload } from "./registry";

/** Renders `phase_section` parts as a titled, expandable section --
 * `payload.title` as a heading-2 inside the summary, `payload.markdown`
 * rendered the same way `TextPart` renders any other markdown body -- open
 * by default (unlike `ProofPart`'s closed-by-default provenance block: this
 * IS the brief's own narrative content a reader came for, not supplementary
 * evidence to dig into on demand). */
export function PhaseSectionPart({ part }: PartProps) {
  const { title, markdown } = part.payload as PhaseSectionPayload;
  return (
    <Disclosure open section summary={<h2 className="text-base font-semibold">{title}</h2>}>
      <div className="prose-poseidon">
        <Markdown>{markdown}</Markdown>
      </div>
    </Disclosure>
  );
}
```

- [ ] **Step 4: Run the registry test to verify it passes**

Run: `cd web && npx vitest run src/ui/message-parts/registry.test.tsx`
Expected: 10 tests pass.

- [ ] **Step 5: Confirm no dark classes remain in the four files, type-check, commit**

Run: `grep -n "dark:" web/src/ui/message-parts/TextPart.tsx web/src/ui/message-parts/ProofPart.tsx web/src/ui/message-parts/FallbackPart.tsx web/src/ui/message-parts/PhaseSectionPart.tsx` (expected: no output). Run `cd web && npx tsc --noEmit` (expected: clean).

```bash
git rev-parse --abbrev-ref HEAD   # must print feature/ui-pass-2-parts
git add web/src/ui/message-parts/TextPart.tsx web/src/ui/message-parts/ProofPart.tsx web/src/ui/message-parts/FallbackPart.tsx web/src/ui/message-parts/PhaseSectionPart.tsx web/src/ui/message-parts/registry.test.tsx
git commit -F <scratch message file>
git log --oneline main..HEAD
```

Message: `feat(web): text, proof, fallback and phase-section parts on the prose scope and Disclosure (UI pass PR 2, Task 2)`, body: `registry.test.tsx: the Proof summary is "How this was computed" (spec 4), so the lookup changes with it.`

---

### Task 3: Chips, tool events, errors, artifacts, tables and metric grids (Haiku)

**Files:**
- Modify: `web/src/ui/message-parts/ChipsPart.tsx`
- Modify: `web/src/ui/message-parts/ToolEventPart.tsx`
- Modify: `web/src/ui/message-parts/ErrorPart.tsx`
- Modify: `web/src/ui/message-parts/ArtifactPart.tsx`
- Modify: `web/src/ui/message-parts/TablePart.tsx`
- Modify: `web/src/ui/message-parts/MetricGridPart.tsx`
- Modify: `web/src/ui/message-parts/registry.test.tsx`

**Interfaces:**
- Consumes: `Badge` and the `data-table.ts` constants from Task 1.
- Produces: nothing new; the six components keep their names and `PartProps`.

- [ ] **Step 1: Add the failing tests**

Add inside the `describe("PartRenderer", ...)` block of `web/src/ui/message-parts/registry.test.tsx`, after `"artifacts render a plain download anchor"`:

```tsx
  it("artifacts show a MIME badge and keep the download attribute", () => {
    render(<PartRenderer part={{ kind: "artifact", payload: { name: "brief.pdf", url: "https://example.invalid/b.pdf", mime: "application/pdf" } }} />);
    expect(screen.getByText("application/pdf").tagName).toBe("SPAN");
    expect(screen.getByText("brief.pdf").closest("a")!.getAttribute("download")).toBe("brief.pdf");
  });

  it("errors show the hint beneath the message", () => {
    render(<PartRenderer part={{ kind: "error", payload: { code: "x", message: "It broke", hint: "Try a shorter period." } }} />);
    expect(screen.getByText("It broke").tagName).toBe("P");
    expect(screen.getByText("Try a shorter period.").tagName).toBe("P");
  });

  it("tables sit inside a scroll wrapper so a wide breakdown never widens the thread (Review Focus 2)", () => {
    render(<PartRenderer part={{ kind: "table", payload: { columns: ["Customer", "GP"], rows: [["Acme", 12.5]] } }} />);
    const table = screen.getByText("Acme").closest("table")!;
    expect(table.parentElement!.className).toContain("overflow-x-auto");
  });
```

- [ ] **Step 2: Run the registry test to verify the three new tests fail**

Run: `cd web && npx vitest run src/ui/message-parts/registry.test.tsx`
Expected: the hint test passes already (the hint was rendered before); the badge test and the wrapper test fail. Note the hint test's pass in the report: it pins behaviour this task must keep, it is not a TDD red.

- [ ] **Step 3: Rewrite the six components**

`web/src/ui/message-parts/ChipsPart.tsx`:

```tsx
import type { ChipsPayload, PartProps } from "./registry";

/** Renders `chips` parts as a row of pill buttons. Clicking one sends its
 * `send_text` -- falling back to its `label` when absent -- through
 * `onChipSelect`, disabled whenever the payload itself says so, OR while a
 * send for this conversation is already in flight (threaded down as the
 * `disabled` prop). A disabled chip fades rather than greys out, so a
 * replayed turn's chips still read (spec 4). */
export function ChipsPart({ part, onChipSelect, disabled }: PartProps) {
  const { options, disabled: payloadDisabled } = part.payload as ChipsPayload;
  const isDisabled = disabled === true || payloadDisabled === true;
  return (
    <div className="flex flex-wrap gap-2">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          className="rounded-full border border-border bg-surface px-3 py-1 text-[13px] transition-colors duration-[120ms] hover:bg-surface-raised disabled:cursor-default disabled:opacity-60 disabled:hover:bg-surface"
          disabled={isDisabled}
          onClick={() => onChipSelect?.(option.id, option.send_text ?? option.label)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
```

`web/src/ui/message-parts/ToolEventPart.tsx`:

```tsx
import type { PartProps, ToolEventPayload } from "./registry";

const GLYPH: Record<"done" | "error", string> = {
  done: "✓", // checkmark
  error: "✕", // cross
};

/** Renders `tool_event` parts as a compact status line: a pulsing dot while
 * running (Tailwind `animate-pulse`, replacing the Vite original's CSS
 * `tool-dot-pulse` keyframe -- see `frontend/src/theme/base.css` -- as the
 * user's "still working" cue during a tool call), settling to a glyph once
 * done (`✓`) or errored (`✕`), followed by the label. Muted ink, negative
 * ink on error (spec 4). */
export function ToolEventPart({ part }: PartProps) {
  const { status, label } = part.payload as ToolEventPayload;
  const isError = status === "error";
  return (
    <div className={`flex items-center gap-2 text-[13px] ${isError ? "text-negative" : "text-ink-muted"}`}>
      {status === "start" ? (
        <span aria-hidden="true" className="inline-block h-2 w-2 animate-pulse rounded-full bg-current" />
      ) : (
        <span aria-hidden="true">{GLYPH[status]}</span>
      )}
      <span>{label}</span>
    </div>
  );
}
```

`web/src/ui/message-parts/ErrorPart.tsx`:

```tsx
import type { ErrorPayload, PartProps } from "./registry";

/** Renders `error` parts as a card with a negative left rule: the message
 * in ink, the optional hint beneath in muted ink. No tinted fill (spec 4). */
export function ErrorPart({ part }: PartProps) {
  const { message, hint } = part.payload as ErrorPayload;
  return (
    <div className="rounded-card border border-border border-l-4 border-l-negative bg-surface p-3 text-[13px]">
      <p className="text-ink">{message}</p>
      {hint ? <p className="mt-1 text-ink-muted">{hint}</p> : null}
    </div>
  );
}
```

`web/src/ui/message-parts/ArtifactPart.tsx`:

```tsx
import { Badge } from "../primitives/Badge";
import type { ArtifactPayload, PartProps } from "./registry";

/** Renders `artifact` parts as a download card: a file glyph, the file name
 * linking straight to `url` -- a presigned GET the browser fetches directly,
 * so this needs no backend proxy route -- and a badge naming the MIME type
 * (spec 4). */
export function ArtifactPart({ part }: PartProps) {
  const { name, url, mime } = part.payload as ArtifactPayload;
  return (
    <div className="inline-flex items-center gap-3 rounded-card border border-border px-3 py-2 text-[13px]">
      <svg
        aria-hidden="true"
        className="flex-none text-ink-muted"
        width="20"
        height="20"
        viewBox="0 0 20 20"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      >
        <path d="M5 2.5h7l3.5 3.5v11a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-13.5a1 1 0 0 1 1-1z" />
        <path d="M12 2.5V6h3.5" />
      </svg>
      <a className="font-medium text-accent hover:underline" href={url} target="_blank" rel="noopener noreferrer" download={name}>
        {name}
      </a>
      <Badge>{mime}</Badge>
    </div>
  );
}
```

`web/src/ui/message-parts/TablePart.tsx`:

```tsx
import { TABLE, TD, TD_NUM, TH, TR } from "../primitives/data-table";
import type { PartProps, TablePayload } from "./registry";

/** Renders `table` parts as a plain table inside a horizontal scroll
 * wrapper: one header cell per column, numeric cells right-aligned with
 * tabular figures so a column of amounts lines up on its decimal point,
 * rows lifting to the raised surface on hover (spec 4). */
export function TablePart({ part }: PartProps) {
  const { columns, rows } = part.payload as TablePayload;
  return (
    <div className="overflow-x-auto">
      <table className={TABLE}>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} className={TH}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={rowIndex} className={TR}>
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className={typeof cell === "number" ? TD_NUM : TD}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

`web/src/ui/message-parts/MetricGridPart.tsx`:

```tsx
import type { MetricGridPayload, PartProps } from "./registry";

/** One metric's value for one compared period, formatted for display.
 * `null` (one side of the comparison returned no rows) renders as an em
 * dash, never a blank cell that could be mistaken for a genuine zero.
 * `toLocaleString` supplies the thousands separators the certified value
 * itself never carries. The unit (when present) is rendered in its own
 * element -- not fused into this string -- so the bare value stays queryable
 * on its own. */
function formatValue(value: number | null): string {
  if (value === null) return "—"; // em dash
  return value.toLocaleString("en-US");
}

function MetricValue({ value, unit }: { value: number | null; unit: string | null }) {
  return (
    <span className="flex items-baseline gap-1">
      <span className="text-lg font-semibold tabular-nums">{formatValue(value)}</span>
      {unit ? <span className="text-[12px] text-ink-muted">{unit}</span> : null}
    </span>
  );
}

/** Renders `metric_grid` parts as a card per metric, each showing the two
 * compared periods' values side by side; two cards across from 640px
 * (spec 4). */
export function MetricGridPart({ part }: PartProps) {
  const { periods, metrics } = part.payload as MetricGridPayload;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {metrics.map((metric) => (
        <div className="rounded-card border border-border p-3" key={metric.name}>
          <span className="block text-[12px] uppercase tracking-wide text-ink-muted">{metric.friendly}</span>
          <div className="mt-1 grid grid-cols-2 gap-4">
            <div className="flex flex-col">
              <MetricValue value={metric.a} unit={metric.unit} />
              <span className="text-[12px] text-ink-muted tabular-nums">
                {periods.a.start} - {periods.a.end}
              </span>
            </div>
            <div className="flex flex-col">
              <MetricValue value={metric.b} unit={metric.unit} />
              <span className="text-[12px] text-ink-muted tabular-nums">
                {periods.b.start} - {periods.b.end}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run the registry test to verify it passes**

Run: `cd web && npx vitest run src/ui/message-parts/registry.test.tsx`
Expected: 13 tests pass.

- [ ] **Step 5: Confirm the dark sweep of `message-parts` is complete, run the full suite, type-check, commit**

Run: `grep -rn "dark:\|neutral-\|red-\|blue-" web/src/ui/message-parts` (expected: no output). Run `cd web && npm test > <scratch file> 2>&1` and read the file (expected: every file that passed before still passes; the new count is 205 plus the tests added so far). Run `cd web && npx tsc --noEmit` (expected: clean).

```bash
git rev-parse --abbrev-ref HEAD   # must print feature/ui-pass-2-parts
git add web/src/ui/message-parts
git commit -F <scratch message file>
git log --oneline main..HEAD
```

Message: `feat(web): chips, tool-event, error, artifact, table and metric-grid parts on the slate tokens (UI pass PR 2, Task 3)`.

---

### Task 4: The thread follows the reader; the composer takes focus back; chips survive an error (Sonnet)

**Files:**
- Modify: `web/src/app/c/[id]/Chat.tsx`
- Modify: `web/src/app/c/[id]/Chat.test.tsx`
- Modify: `web/src/ui/shell/Composer.tsx`
- Modify: `web/src/ui/shell/Composer.test.tsx`

**Interfaces:**
- Consumes: `useChat` (`messages`, `status`, `error`, `sendMessage`), `Composer({ disabled, onSend })`, `PartRenderer`.
- Produces: nothing new. `Chat` and `Composer` keep their props.

Background for the implementer: PR 1 scrolled a sentinel `<li>` into view on every `messages` change. Its final review deferred three things to this PR: the effect fires per streamed token and pulls a reader who scrolled up back down; the thread rests about 23px short of the bottom (the sentinel's edge, not the list's padding, was aligned); the composer loses focus after each send because the browser blurs a disabled input. The chips-enabled-on-error case had no test.

- [ ] **Step 1: Replace the scroll test and add the chips-on-error test**

In `web/src/app/c/[id]/Chat.test.tsx`, change the `@testing-library/react` import to `import { cleanup, fireEvent, render, screen } from "@testing-library/react";`, then replace the test `"scrolls the end of the thread into view when messages change"` with the following four tests, and add the two helpers above the `describe`:

```tsx
/** The thread's scroll container: the `<ol>` is the only list Chat renders. */
function thread() {
  return screen.getByRole("list").parentElement as HTMLDivElement;
}

/** jsdom has no layout, so fake the three numbers the scroll logic reads. */
function fakeScrollBox(el: HTMLElement, { scrollHeight, clientHeight }: { scrollHeight: number; clientHeight: number }) {
  let top = 0;
  Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => scrollHeight });
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => clientHeight });
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => top,
    set: (value: number) => {
      top = value;
    },
  });
}
```

```tsx
  it("follows the end of the thread while the reader is at the end (Review Focus 3)", () => {
    chat.state.messages = [user];
    const { rerender } = render(<Chat conversationId="c1" initialMessages={[]} />);
    const box = thread();
    fakeScrollBox(box, { scrollHeight: 1000, clientHeight: 400 });
    chat.state.messages = [user, assistant];
    rerender(<Chat conversationId="c1" initialMessages={[]} />);
    expect(box.scrollTop).toBe(1000);
  });

  it("leaves a reader who scrolled up where they are while new content arrives (Review Focus 3)", () => {
    chat.state.messages = [user];
    const { rerender } = render(<Chat conversationId="c1" initialMessages={[]} />);
    const box = thread();
    fakeScrollBox(box, { scrollHeight: 1000, clientHeight: 400 });
    box.scrollTop = 100;
    fireEvent.scroll(box);
    chat.state.messages = [user, assistant];
    rerender(<Chat conversationId="c1" initialMessages={[]} />);
    expect(box.scrollTop).toBe(100);
  });

  it("returns to the end of the thread when the reader sends a message (Review Focus 3)", () => {
    chat.state.messages = [user];
    const { rerender } = render(<Chat conversationId="c1" initialMessages={[]} />);
    const box = thread();
    fakeScrollBox(box, { scrollHeight: 1000, clientHeight: 400 });
    box.scrollTop = 100;
    fireEvent.scroll(box);
    const input = screen.getByLabelText("Message Poseidon") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "and by port?" } });
    fireEvent.submit(input.closest("form")!);
    expect(chat.state.sendMessage).toHaveBeenCalledWith({ text: "and by port?" });
    chat.state.messages = [user, assistant, { id: "u2", role: "user", parts: [{ type: "text", text: "and by port?" }] }];
    rerender(<Chat conversationId="c1" initialMessages={[]} />);
    expect(box.scrollTop).toBe(1000);
  });

  it("keeps the last message's chips clickable after an errored turn (Review Focus 4)", () => {
    const chips: PoseidonUIMessage = {
      id: "a2",
      role: "assistant",
      parts: [{ type: "data-chips", data: { options: [{ id: "x", label: "Pacific Basin" }] } } as PoseidonUIMessage["parts"][number]],
    };
    chat.state.status = "error";
    chat.state.error = new Error("stream ended without a terminal frame");
    chat.state.messages = [user, chips];
    render(<Chat conversationId="c1" initialMessages={[]} />);
    const chip = screen.getByText("Pacific Basin") as HTMLButtonElement;
    expect(chip.disabled).toBe(false);
    fireEvent.click(chip);
    expect(chat.state.sendMessage).toHaveBeenCalledWith({ text: "Pacific Basin" });
  });
```

Also add `chat.state.sendMessage.mockClear();` to the `afterEach`.

In `web/src/ui/shell/Composer.test.tsx`, add inside the `describe`:

```tsx
  it("takes the focus back when a turn ends (Review Focus 4)", () => {
    const { rerender } = render(<Composer disabled onSend={vi.fn()} />);
    expect(document.activeElement).not.toBe(input());
    rerender(<Composer disabled={false} onSend={vi.fn()} />);
    expect(document.activeElement).toBe(input());
  });
```

- [ ] **Step 2: Run the two test files to verify the new tests fail**

Run: `cd web && npx vitest run "src/app/c/[id]/Chat.test.tsx" src/ui/shell/Composer.test.tsx`
Expected: "leaves a reader who scrolled up" fails (the sentinel effect ignores the scroll position, so `scrollTop` is not 100... note: with the sentinel still in place, `scrollIntoView` is a no-op in jsdom and `scrollTop` stays 100, so this test may pass against the old code; the "follows the end" and "returns to the end" tests fail because nothing sets `scrollTop` to 1000). The chips test passes already (it pins the `busy` rule from PR 1; say so in the report). The composer focus test fails (`activeElement` is `body`).

- [ ] **Step 3: Rework the thread's scroll in `Chat.tsx`**

Add at module level, after the `turnKeys` map:

```tsx
/** How close to the end (px) still counts as "reading the latest". */
const NEAR_BOTTOM_PX = 48;
```

Replace the block from the comment `// The thread scrolls inside its own column now (spec 3.2), ...` through the `useEffect` with:

```tsx
  // The thread scrolls inside its own column (spec 3.2), so the browser no
  // longer keeps the newest message in view for free. Follow the end of the
  // thread only while the reader is already there: someone who scrolled up
  // to re-read an earlier answer stays put while tokens stream in. `stick`
  // is re-derived on every scroll and read after every render that changes
  // the messages or the error line; a send always re-engages it.
  const threadRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  function onScroll() {
    const el = threadRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;
  }
  useEffect(() => {
    const el = threadRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [messages, error]);
```

Change `send` to re-engage the follow before sending:

```tsx
  function send(text: string) {
    if (busy || text.trim() === "") return;
    stick.current = true;
    void sendMessage({ text });
  }
```

Change the container to `<div ref={threadRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">`, and delete the sentinel line `<li ref={endRef} aria-hidden="true" />`. `useRef` stays imported; `endRef` is gone.

- [ ] **Step 4: Give the composer its focus back in `Composer.tsx`**

Change the React import to `import { useEffect, useRef, useState, type FormEvent } from "react";`. After `const blank = ...`, add:

```tsx
  // The browser blurs an input the moment it is disabled, so without this
  // every turn would end with the reader clicking back into the composer.
  // Focus returns when `disabled` falls, never on mount: a page that steals
  // focus on load is a worse habit than one that asks for a click.
  const inputRef = useRef<HTMLInputElement>(null);
  const wasDisabled = useRef(disabled);
  useEffect(() => {
    if (wasDisabled.current && !disabled) inputRef.current?.focus();
    wasDisabled.current = disabled;
  }, [disabled]);
```

Add `ref={inputRef}` to the `<input>`.

- [ ] **Step 5: Run the two test files to verify they pass**

Run: `cd web && npx vitest run "src/app/c/[id]/Chat.test.tsx" src/ui/shell/Composer.test.tsx`
Expected: Chat 8 tests pass, Composer 4 tests pass.

- [ ] **Step 6: Prove the guard by breaking it**

Temporarily change `stick.current = el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX;` to `stick.current = true;`, run the Chat test file, and confirm "leaves a reader who scrolled up" fails with `scrollTop` 1000. Restore the line and confirm the file passes again. Record both runs in the report.

- [ ] **Step 7: Full suite, type-check, commit**

Run `cd web && npm test > <scratch file> 2>&1` and read the file; `cd web && npx tsc --noEmit` clean.

```bash
git rev-parse --abbrev-ref HEAD   # must print feature/ui-pass-2-parts
git add "web/src/app/c/[id]/Chat.tsx" "web/src/app/c/[id]/Chat.test.tsx" web/src/ui/shell/Composer.tsx web/src/ui/shell/Composer.test.tsx
git commit -F <scratch message file>
git log --oneline main..HEAD
```

Message: `fix(web): thread follows the reader only at the end, composer refocuses after a turn, chips-on-error test (UI pass PR 2, Task 4)`, body: `Chat.test.tsx: the sentinel scroll test is replaced; the sentinel is gone and the container's own scrollTop is what the thread now moves.`

---

### Task 5: Docs and figures (Sonnet), then the browser gate (Fable)

**Files:**
- Modify: `CLAUDE.md` (the web figure in the Commands comment and in the figures paragraph)
- Modify: `docs/superpowers/specs/2026-10-23-nextjs-ui-design-pass-design.md` (the `**Status:**` line)
- Modify: `docs/superpowers/plans/2026-10-23-ui-pass-1-shell.md` (one line in "PR description": the PR number and merge)

**Interfaces:** none.

- [ ] **Step 1: Run the gates and record the figures**

Run, each to its own file, then read the files:

```bash
cd web && npm test > <scratch>/web-test.txt 2>&1
cd web && npx tsc --noEmit > <scratch>/tsc.txt 2>&1
grep -rn "dark:" web/src/ui/message-parts "web/src/app" web/src/ui/shell web/src/ui/primitives   # expected: no output
grep -rn "dark:" web/src/ui/report | wc -l                                                      # expected: a positive number (PR 3's)
```

Write the test line down (files passed/skipped, tests passed/skipped).

- [ ] **Step 2: Update the three docs**

In `CLAUDE.md`, replace both occurrences of `205 passed, 14 skipped, offline; verified 2026-10-23 (UI pass PR 1)` / `205 passed, 14 skipped offline (verified 2026-10-23 (UI pass PR 1))` with the new count and `(UI pass PR 2)`, keeping the surrounding sentence shape; the `27 files passed, 1 skipped` becomes the new file count; the provenance sentence `not re-run in PR C or UI pass PR 1` becomes `not re-run in PR C, UI pass PR 1 or UI pass PR 2`.

In the spec, the `**Status:**` line becomes: `PR 1 merged (#12, 2026-10-23); PR 2 built on feature/ui-pass-2-parts (plan: docs/superpowers/plans/2026-10-23-ui-pass-2-parts.md); PR 3 pending`.

In the PR 1 plan's "PR description" paragraph, replace `` `git push` and `gh pr create` only on Carlos's word. `` with `` Opened as PR #12 and merged by Carlos on 2026-10-23 (main @ c1b74f5). ``.

- [ ] **Step 3: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print feature/ui-pass-2-parts
git add CLAUDE.md docs/superpowers/specs/2026-10-23-nextjs-ui-design-pass-design.md docs/superpowers/plans/2026-10-23-ui-pass-1-shell.md
git commit -F <scratch message file>
git log --oneline main..HEAD
```

Message: `docs: web figures for UI pass PR 2, spec status, PR 1 merge note`.

- [ ] **Step 4: Browser gate (Fable, not a subagent)**

Compose up with the scratch port override (db 5434, MinIO 9002/9003), Next dev on :3000 with `POSEIDON_INTERNAL_TOKEN=dev-internal-token` and `DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon`. Through the real composer against the stub backend, in a fresh conversation:

1. A breakdown with a period and a port, e.g. `GP by customer at Singapore for April 2026`: table (uppercase muted headers, right-aligned amounts, row hover), proof block closed with "How this was computed" and a turning caret, chips row as pills. Screenshot.
2. A compare, e.g. `GP for Northstar Lines for September 2026 compared with August 2026`: metric grid cards, two across. Screenshot.
3. A research pivot after naming a customer, e.g. `any relevant news on Maersk?`: tool-event lines (pulsing dot then check or cross), and whatever phase sections and artifact the stub path produces. Screenshot.
4. A clarify, e.g. `brief for pacific`: chips (the stub router may answer with the capability message instead; record which).
5. An error part: if no stub path yields one, the registry test is the evidence; record that in the ledger and the PR body.
6. Scroll: with twenty-plus messages, scroll up, send another message, confirm the thread returns to the end; scroll up again and confirm a reload does not move the reader while a turn streams (stub turns are fast; the per-token case is covered by Task 4's tests). Confirm the thread rests flush with the bottom after a turn (no 23px gap). Confirm the composer has focus after the turn ends.
7. A wide table: `GP, volume and margin by customer by port for 2025` (or the widest breakdown the stub returns): the table scrolls inside its wrapper, the thread column does not widen. Screenshot.
8. Console: no errors or warnings.

Any kind the stub path cannot produce is recorded as "covered by tests only" in the ledger and in the PR body.

---

## Self-review

- Spec coverage: every row of §4's table has a task (Text/Proof/Fallback/PhaseSection in Task 2; Chips/ToolEvent/Error/Table/MetricGrid/Artifact in Task 3); the three primitives in Task 1; the prose rule set and the caret in Task 1; §6's PR 2 gate in Task 5; the three deferred PR 1 items in Task 4.
- Placeholders: none. Every component is given in full.
- Type consistency: `Disclosure` props (`summary`, `open`, `section`, `children`) used identically in Tasks 2 and its own test; `Badge` `tone` used in Task 1's test and PR 3; `TABLE/TH/TR/TD/TD_NUM` names used identically in Task 3.
- Review Focus: 1 in Task 2's list test; 2 in Task 3's wrapper test plus the gate; 3 in Task 4's three scroll tests; 4 in Task 4's chips and focus tests; 5 in the kept fallback test.
- Test counts: 205 at main, plus 4 (primitives) + 3 (globals) + 2 (Task 2) + 3 (Task 3) + 3 net (Task 4: four added, one removed) + 1 (Composer) = 221 expected; Task 5 records the real figure.
