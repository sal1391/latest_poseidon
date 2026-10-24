# UI design pass, PR 3: the report document card — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the monthly report as a document card inside the thread: header with the month-to-date badge, KPI tiles coloured by the sign of the change, a segmented control over the ranked tables with a spanning metric header, mover cards with badges, new/lost tables, the narrative in the prose scope, and the proof as the shared disclosure. This completes the design pass: after it, `grep -rn "dark:" web/src` is empty.

**Architecture:** Every report component keeps its name, props, `data-metric` attributes, ARIA roles and heading levels; only classes and a few strings change. The components consume PR 2's primitives (`Disclosure`, `Badge`, the `data-table.ts` constants, `.prose-poseidon`) and one new helper, `toneFor(change)`, which maps a numeric change to `text-positive`, `text-negative` or `text-ink`. `data-table.ts` gains one constant (`TH_NUM`). No change to the SSE/part contract, the report payload, routes, Drizzle or Python.

**Tech Stack:** Next.js 16.3.4, React 19.2.8, Tailwind 4 via `@tailwindcss/postcss`, react-markdown 10, vitest 5 with jsdom and `@testing-library/react`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-nextjs-ui-design-pass-design.md` sections 5, 6, 7, read with the rulings P1-R1 to P1-R3 (PR 1 plan) and P2-R1, P2-R2 (PR 2 plan).

**Model split (Carlos's cost rule):** Haiku for Tasks 1, 2 and 3 (the plan carries every file and test in full). Sonnet for Task 4 (docs and figures). Sonnet reviews Tasks 1 and 3; **Opus reviews Task 2** (the ranked-table header change is the one place spec §7 says a layout regression hides). One Opus whole-branch review at the end. Fable supervises, runs the browser walkthrough and never implements.

**Branch:** `feature/ui-pass-3-report`, cut from `feature/ui-pass-2-parts` at its PR head (PR 2 was not yet merged when this plan was written). The PR is opened against `feature/ui-pass-2-parts` (stacked) and must be re-targeted to `main` once PR 2 merges: `gh pr edit <n> --base main`.

## Global Constraints

- `web/` only. Nothing under `frontend/` or `backend/` changes. If a task finds it must touch either, stop and report.
- No raw hex, no `neutral-*`, `red-*`, `blue-*`, `amber-*` palette classes in any file this PR touches. Colours are the `@theme` tokens (`surface`, `surface-raised`, `ink`, `ink-muted`, `border`, `accent`, `positive`, `negative`, `warn-bg`, `warn-ink`).
- Radii per P1-R1 (`rounded-card` 12px, `rounded-chip` 8px); numbers per P1-R2 (`tabular-nums`); mono per P2-R1 (not used in this PR); badge per P2-R2 (`Badge` primitive, `tone="warn"` for the month-to-date marker).
- **Ruling P3-R1 (heading elements):** the spec's "heading-1" and "heading-2" are type styles (`text-xl font-semibold`, `text-base font-semibold`), not element changes. The scope label stays an `<h3>` and the section titles stay `<h4>`, so the existing tests on heading levels and the document outline are unchanged.
- **Ruling P3-R2 (sign of a change):** the colour of a change line comes from the payload's numeric `change` (`Delta.change`, `Row.change[metric]`), not from the display string's first character as the spec suggests: the number is already on the payload and cannot be fooled by a currency symbol or a "+" prefix. Positive is `text-positive`, negative `text-negative`, zero `text-ink`.
- **Ruling P3-R3 (numeric column headers):** headers above right-aligned numeric columns are right-aligned too (`TH_NUM`); the entity column header stays left (`TH`). The spec's §4 "text-left" was written for the chat table's mixed columns.
- **Ruling P3-R4 (glyphs in mover lines):** the GP line and the share-of-book line use the arrow glyph `→`; the reconciliation line uses the middle dot `·` between its three facts, matching the header's "Generated … · Source: …". The two `ReportPart.test.tsx` assertions that spelled `->` and `|` change in the same commit with this reason.
- Class assertions in tests are allowed for exactly one thing, the sign colour (as PR 1 allowed one for the sidebar row tone): there is no other observable for it in jsdom.
- Existing tests keep passing except the ones named in Tasks 1 and 3 (Proof lookup text; the two glyph strings), each changed in the same commit with the reason in the body.
- Commands: vitest, npm and tsc run inside `web/`; git and grep run from the repo root (the working directory persists between commands). Single file: `npx vitest run <path>`. Full suite: `npm test > <file> 2>&1`, then read the file. **Never pipe a test run.** Never set `DRIZZLE_DATABASE_URL` in a test shell. Never run `next dev` or `next build` from a task; if `web/AGENTS.md` shows as modified, leave it unstaged.
- Before every commit: `git rev-parse --abbrev-ref HEAD` must print `feature/ui-pass-3-report`; after every commit `git log --oneline feature/ui-pass-2-parts..HEAD` must show only this branch's work. Anything else: stop and report.
- Commit messages via a scratch file and `git commit -F`. Each ends with the two attribution lines in the session's system reminder.

## Review Focus

1. **A KPI whose change is exactly zero** (display "0", "+0" or "0.0%"): the change line is plain ink, neither green nor red. Pinned in Task 1's `toneFor` test.
2. **A ranked table with an Other row and a footer:** the Other row reads muted, the footer is bold with a rule above, and the footer's GP current still equals the GP tile (the existing test). Task 2 keeps that test and adds the spanning-header test.
3. **A report at a narrow width** (phone): the segmented control wraps rather than overflowing, the table scrolls inside its wrapper, the card keeps its padding. Browser gate (Task 4) at the 501px floor.
4. **A whole-book report** (no `entity_context`): mover cards omit the reconciliation and share lines without leaving an empty muted block. The existing test is kept (Task 3).
5. **The narrative withheld and ungrounded:** the notice reads as a muted line, not a bold warning, and the template markdown still renders with its links and images stripped. Task 1 adds the notice test; the existing guard test is kept.

---

### Task 1: Tone helper, header, KPI tiles, proof and narrative (Haiku)

**Files:**
- Create: `web/src/ui/report/tone.ts`
- Create: `web/src/ui/report/tone.test.ts`
- Modify: `web/src/ui/primitives/data-table.ts` (one constant)
- Modify: `web/src/ui/report/ReportPart.tsx`
- Modify: `web/src/ui/report/KpiTiles.tsx`
- Modify: `web/src/ui/report/ProofBlock.tsx`
- Modify: `web/src/ui/report/Narrative.tsx`
- Modify: `web/src/ui/report/ReportPart.test.tsx`

**Interfaces:**
- Consumes: `Disclosure`, `Badge` from `web/src/ui/primitives/`; `.prose-poseidon`.
- Produces: `toneFor(change: number): "text-positive" | "text-negative" | "text-ink"`; `TH_NUM` in `data-table.ts`.

- [ ] **Step 1: Write the failing tests**

Create `web/src/ui/report/tone.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { toneFor } from "./tone";

describe("toneFor (ruling P3-R2)", () => {
  it("colours by the sign of the numeric change and leaves zero in ink (Review Focus 1)", () => {
    expect(toneFor(30000)).toBe("text-positive");
    expect(toneFor(-27000)).toBe("text-negative");
    expect(toneFor(0)).toBe("text-ink");
    expect(toneFor(-0)).toBe("text-ink");
  });
});
```

In `web/src/ui/report/ReportPart.test.tsx`: in `"proof is collapsed"`, change `screen.getByText("Proof")` to `screen.getByText("How this was computed")` (reason: spec §5 names the summary). Add these tests inside the `describe`:

```tsx
  it("colours each kpi change line by the sign of its numeric change (ruling P3-R2)", () => {
    const { container } = show();
    for (const [metric, d] of Object.entries(payload.kpis)) {
      const tile = container.querySelector(`[data-metric="${metric}"]`)!;
      const line = within(tile as HTMLElement).getByText(`${d.display.change} (${d.display.pct})`);
      const expected = d.change > 0 ? "text-positive" : d.change < 0 ? "text-negative" : "text-ink";
      expect(line.className).toContain(expected);
    }
  });

  it("header carries the generated line with a middle dot and the period pair", () => {
    show();
    expect(screen.getByText(`Generated ${payload.generated_at} · Source: ${payload.source}`)).toBeTruthy();
  });

  it("narrative has its heading, renders in the prose scope, and a withheld notice is a muted line (Review Focus 5)", () => {
    const { container } = show({ ...payload, narrative: { template_md: "Plain **template** text.", llm_md: null, grounded: false, flags: ["model narrative skipped: stub mode"] } });
    expect(screen.getByRole("heading", { level: 4, name: "Narrative" })).toBeTruthy();
    expect(screen.getByText("template").closest(".prose-poseidon")).toBeTruthy();
    const notice = screen.getByText("AI narrative withheld");
    expect(notice.className).not.toContain("font-medium");
    expect(notice.className).toContain("text-ink-muted");
    expect(container.textContent).toContain("model narrative skipped: stub mode");
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run src/ui/report/tone.test.ts src/ui/report/ReportPart.test.tsx`
Expected: `tone.test.ts` fails to resolve `./tone`; the Proof lookup fails ("How this was computed" not rendered); the kpi-tone test fails (no tone class on the line); the header test fails (the old line uses `|`); the narrative test fails (no heading, no prose scope, bold notice).

- [ ] **Step 3: Write the helper, the constant and the four components**

Create `web/src/ui/report/tone.ts`:

```ts
/**
 * The colour of a change line, from the payload's numeric change (ruling
 * P3-R2): the number is on the payload already, so no reading of a display
 * string's first character is needed and a currency symbol or a "+" prefix
 * cannot mislead it. Zero is plain ink: neither good nor bad news.
 */
export function toneFor(change: number): "text-positive" | "text-negative" | "text-ink" {
  if (change > 0) return "text-positive";
  if (change < 0) return "text-negative";
  return "text-ink";
}
```

In `web/src/ui/primitives/data-table.ts`, add after `TH`:

```ts
export const TH_NUM = `${TH} text-right`;
```

`web/src/ui/report/ReportPart.tsx`:

```tsx
import { Badge } from "../primitives/Badge";
import type { PartProps } from "../message-parts/registry";
import { KpiTiles } from "./KpiTiles";
import { MoverCards } from "./MoverCards";
import { Narrative } from "./Narrative";
import { NewLostTables } from "./NewLostTables";
import { ProofBlock } from "./ProofBlock";
import { RankedTables } from "./RankedTables";
import type { ReportPayload } from "./types";

/** Renders `report` parts as a document card inside the thread (spec 5).
 * Suggested questions arrive as a separate chips part (ruling 9) and render
 * outside the card. */
export function ReportPart({ part }: PartProps) {
  const p = part.payload as ReportPayload;
  return (
    <article className="space-y-5 rounded-card border border-border bg-surface p-5 shadow-1">
      <header className="space-y-1">
        <h3 className="text-xl font-semibold">{p.scope.label}</h3>
        <p className="text-[15px] text-ink">
          {`${p.periods.current.label} vs ${p.periods.prior.label}`}
          {p.periods.window_kind === "mtd" ? (
            <span className="ml-2">
              <Badge tone="warn">Month to date</Badge>
            </span>
          ) : null}
        </p>
        <p className="text-[12px] text-ink-muted">{`Generated ${p.generated_at} · Source: ${p.source}`}</p>
      </header>
      <KpiTiles kpis={p.kpis} />
      <RankedTables tables={p.tables} entityLabel={p.entity.label} />
      <MoverCards movers={p.movers} context={p.entity_context} />
      <NewLostTables newLost={p.new_lost} />
      <Narrative narrative={p.narrative} />
      <ProofBlock proof={p.proof} queries={p.queries} />
    </article>
  );
}
```

`web/src/ui/report/KpiTiles.tsx`:

```tsx
import { toneFor } from "./tone";
import type { Delta } from "./types";

/** One tile per KPI, in payload order: name, current value, the change line
 * coloured by its sign (ruling P3-R2), the prior value beneath (spec 5). */
export function KpiTiles({ kpis }: { kpis: Record<string, Delta> }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {Object.entries(kpis).map(([metric, d]) => (
        <dl key={metric} data-metric={metric} className="rounded-card border border-border p-3">
          <dt className="text-[12px] uppercase tracking-wide text-ink-muted">{metric}</dt>
          <dd className="text-xl font-semibold tabular-nums">{d.display.current}</dd>
          <dd className={`text-[13px] tabular-nums ${toneFor(d.change)}`}>{`${d.display.change} (${d.display.pct})`}</dd>
          <dd className="text-[12px] text-ink-muted tabular-nums">{`prior ${d.display.prior}`}</dd>
        </dl>
      ))}
    </div>
  );
}
```

`web/src/ui/report/ProofBlock.tsx`:

```tsx
import { Disclosure } from "../primitives/Disclosure";
import type { ReportPayload } from "./types";

/** The report's provenance: the proof lines, then one line per query. The
 * shared disclosure, closed by default (spec 5). */
export function ProofBlock({ proof, queries }: Pick<ReportPayload, "proof" | "queries">) {
  const lines = [...proof, ...queries.map((q) => `${q.label} ${q.sql_hash} rows=${q.rows} ms=${q.ms}`)];
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

`web/src/ui/report/Narrative.tsx`:

```tsx
import Markdown from "react-markdown";
import type { ReportNarrative } from "./types";

/** Escape-first, no-links markdown: raw HTML is skipped, anchors and images
 * unwrap to their text (unchanged guards). Heading, prose scope, muted flags
 * and a muted withheld notice per spec 5. */
export function Narrative({ narrative }: { narrative: ReportNarrative }) {
  const md = narrative.grounded && narrative.llm_md ? narrative.llm_md : narrative.template_md;
  const withheld = !narrative.grounded && narrative.llm_md === null;
  return (
    <section className="space-y-2">
      <h4 className="text-base font-semibold">Narrative</h4>
      {withheld ? <p className="text-[13px] text-ink-muted">AI narrative withheld</p> : null}
      <div className="prose-poseidon">
        <Markdown skipHtml disallowedElements={["a", "img"]} unwrapDisallowed>
          {md}
        </Markdown>
      </div>
      {narrative.flags.length > 0 ? (
        <ul className="list-inside list-disc text-[12px] text-ink-muted">
          {narrative.flags.map((flag, i) => (
            <li key={i}>{flag}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npx vitest run src/ui/report/tone.test.ts src/ui/report/ReportPart.test.tsx`
Expected: tone 1 test passes; ReportPart 11 tests pass.

- [ ] **Step 5: Type-check and commit**

`npx tsc --noEmit` in `web/` (clean). From the repo root:

```bash
git rev-parse --abbrev-ref HEAD   # must print feature/ui-pass-3-report
git add web/src/ui/report/tone.ts web/src/ui/report/tone.test.ts web/src/ui/primitives/data-table.ts web/src/ui/report/ReportPart.tsx web/src/ui/report/KpiTiles.tsx web/src/ui/report/ProofBlock.tsx web/src/ui/report/Narrative.tsx web/src/ui/report/ReportPart.test.tsx
git commit -F <scratch message file>
git log --oneline feature/ui-pass-2-parts..HEAD
```

Message: `feat(web): report card header, kpi tiles by sign, proof disclosure, narrative heading (UI pass PR 3, Task 1)`, body: `ReportPart.test.tsx: the proof summary is "How this was computed" (spec 5), so the lookup changes with it.`

---

### Task 2: The ranked tables as a segmented control with a spanning header (Haiku; Opus reviews)

**Files:**
- Modify: `web/src/ui/report/RankedTables.tsx`
- Modify: `web/src/ui/report/ReportPart.test.tsx`

**Interfaces:**
- Consumes: `TABLE`, `TH`, `TH_NUM`, `TR`, `TD`, `TD_NUM` from `web/src/ui/primitives/data-table.ts`.
- Produces: nothing new; `RankedTables({ tables, entityLabel })` unchanged; `role="tablist"`, `role="tab"`, `aria-selected`, `role="tabpanel"` unchanged.

- [ ] **Step 1: Add the failing test**

Add to `web/src/ui/report/ReportPart.test.tsx` inside the `describe`, after `"switches ranked tables and shows a tfoot equal to the gp tile"`:

```tsx
  it("ranked table has a spanning metric header above Prior / Current / Change / % columns (Review Focus 2)", () => {
    const { container } = show();
    const ranked = container.querySelector("table")!;
    const headRows = ranked.querySelectorAll("thead tr");
    expect(headRows).toHaveLength(2);
    const groups = [...headRows[0].querySelectorAll("th")];
    expect(groups.map((th) => th.textContent)).toEqual(["Customer", "GP", "Volume", "Margin"]);
    expect(groups.slice(1).every((th) => th.colSpan === 4)).toBe(true);
    expect(groups[0].rowSpan).toBe(2);
    const columns = [...headRows[1].querySelectorAll("th")].map((th) => th.textContent);
    expect(columns).toEqual(["Prior", "Current", "Change", "%", "Prior", "Current", "Change", "%", "Prior", "Current", "Change", "%"]);
    const foot = ranked.querySelectorAll("tfoot tr td");
    expect(foot).toHaveLength(13);
  });
```

- [ ] **Step 2: Run the test file to verify the new test fails**

Run: `cd web && npx vitest run src/ui/report/ReportPart.test.tsx`
Expected: the new test fails on `headRows` length (1, not 2). The other 11 pass.

- [ ] **Step 3: Rewrite the component**

`web/src/ui/report/RankedTables.tsx`:

```tsx
import { useState } from "react";
import { TABLE, TD, TD_NUM, TH, TH_NUM, TR } from "../primitives/data-table";
import { toneFor } from "./tone";
import type { MetricKey, RankedTable, ReportPayload, Row } from "./types";

const METRICS: MetricKey[] = ["GP", "VOLUME", "MARGIN"];
const METRIC_LABEL: Record<MetricKey, string> = { GP: "GP", VOLUME: "Volume", MARGIN: "Margin" };
const COLUMNS = ["Prior", "Current", "Change", "%"] as const;
/** The spanning metric header: centred over its four columns, hairline beneath. */
const TH_GROUP = "border-b border-border px-2 pb-1 text-center text-[12px] font-medium uppercase tracking-wide text-ink-muted";
/** Footer cells: bold, rule above, no rule beneath (the table ends there). */
const TF = "border-t border-border px-2 py-1.5 font-semibold";
const TF_NUM = `${TF} text-right tabular-nums`;

function cells(row: Row, numClass: string) {
  return METRICS.flatMap((m) => {
    const d = row.display[m];
    return [d.prior, d.current, d.change, d.pct].map((text, i) => (
      <td key={`${m}${i}`} className={i === 2 ? `${numClass} ${toneFor(row.change[m])}` : numClass}>
        {text}
      </td>
    ));
  });
}

function DataRow({ row, muted = false }: { row: Row; muted?: boolean }) {
  return (
    <tr className={muted ? `${TR} text-ink-muted` : TR}>
      <td className={TD}>{row.key}</td>
      {cells(row, TD_NUM)}
    </tr>
  );
}

/** Five ranked tables behind a segmented control (spec 5): the track on the
 * raised surface, the active segment lifted white in accent. The table has
 * a spanning header per metric over Prior / Current / Change / %, the Other
 * row muted, the footer bold with a rule above. Roles and aria-selected are
 * unchanged. */
export function RankedTables({ tables, entityLabel }: { tables: ReportPayload["tables"]; entityLabel: string }) {
  const tabs: { id: string; label: string; first: string; table: RankedTable }[] = [
    { id: "egp", label: `${entityLabel}s by GP`, first: entityLabel, table: tables.entities_by_gp },
    { id: "evol", label: `${entityLabel}s by volume`, first: entityLabel, table: tables.entities_by_volume },
    { id: "pgp", label: "Ports by GP", first: "Port", table: tables.ports_by_gp },
    { id: "pvol", label: "Ports by volume", first: "Port", table: tables.ports_by_volume },
    { id: "deal", label: "Deal class", first: "Deal class", table: tables.deal_class },
  ];
  const [active, setActive] = useState(tabs[0].id);
  const tab = tabs.find((t) => t.id === active) ?? tabs[0];
  const { rows, other, footer } = tab.table;
  return (
    <section className="space-y-3">
      <div role="tablist" aria-label="Ranked tables" className="inline-flex flex-wrap gap-1 rounded-card bg-surface-raised p-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={t.id === active}
            onClick={() => setActive(t.id)}
            className={`rounded-chip px-3 py-1 text-[13px] transition-colors duration-[120ms] ${
              t.id === active ? "bg-surface font-medium text-accent shadow-1" : "text-ink-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="overflow-x-auto">
        <table className={TABLE}>
          <thead>
            <tr>
              <th rowSpan={2} className={`${TH} align-bottom`}>
                {tab.first}
              </th>
              {METRICS.map((m) => (
                <th key={m} colSpan={4} className={TH_GROUP}>
                  {METRIC_LABEL[m]}
                </th>
              ))}
            </tr>
            <tr>
              {METRICS.flatMap((m) =>
                COLUMNS.map((c) => (
                  <th key={`${m}${c}`} className={TH_NUM}>
                    {c}
                  </th>
                )),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <DataRow key={row.key} row={row} />
            ))}
            {other ? <DataRow row={{ ...other, key: other.key || "Other" }} muted /> : null}
          </tbody>
          <tfoot>
            <tr>
              <td className={TF}>{footer.key}</td>
              {cells(footer, TF_NUM)}
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Run the test file to verify it passes**

Run: `cd web && npx vitest run src/ui/report/ReportPart.test.tsx`
Expected: 12 tests pass (the existing switching test still finds "Port" in the first header and `foot[2]` equal to the GP tile).

- [ ] **Step 5: Type-check and commit**

`npx tsc --noEmit` in `web/` (clean). From the repo root:

```bash
git rev-parse --abbrev-ref HEAD   # must print feature/ui-pass-3-report
git add web/src/ui/report/RankedTables.tsx web/src/ui/report/ReportPart.test.tsx
git commit -F <scratch message file>
git log --oneline feature/ui-pass-2-parts..HEAD
```

Message: `feat(web): ranked tables as a segmented control with a spanning metric header (UI pass PR 3, Task 2)`.

---

### Task 3: Mover cards and new/lost tables (Haiku)

**Files:**
- Modify: `web/src/ui/report/MoverCards.tsx`
- Modify: `web/src/ui/report/NewLostTables.tsx`
- Modify: `web/src/ui/report/ReportPart.test.tsx`

**Interfaces:**
- Consumes: `Badge`; `TABLE`, `TH`, `TH_NUM`, `TR`, `TD`, `TD_NUM`; `toneFor`.
- Produces: nothing new.

- [ ] **Step 1: Update the glyph assertions and add the tests**

In `web/src/ui/report/ReportPart.test.tsx`, in `"renders movers with drivers and reconciliation lines"`, change the two expectations to:

```tsx
    expect(screen.getByText(`Office ${d.office_change} · other offices ${d.other_offices_net} · book ${d.book_change}`)).toBeTruthy();
    expect(screen.getByText(`share of book ${ctx.share_of_book.display.prior} → ${ctx.share_of_book.display.current}`)).toBeTruthy();
```

(reason for the commit body: ruling P3-R4, arrow and middle-dot glyphs). Add inside the `describe`:

```tsx
  it("mover cards show the GP line with an arrow and colour the change by sign", () => {
    show();
    const up = payload.movers.increases[0];
    const down = payload.movers.decreases[0];
    expect(screen.getByText(`GP ${up.display.GP.prior} → ${up.display.GP.current}`)).toBeTruthy();
    expect(screen.getByText(`${up.display.GP.change} (${up.display.GP.pct})`).className).toContain("text-positive");
    expect(screen.getByText(`${down.display.GP.change} (${down.display.GP.pct})`).className).toContain("text-negative");
  });

  it("new and lost tables are headed and say none in muted ink when empty", () => {
    show({ ...payload, new_lost: { new: payload.new_lost.new, lost: [] } });
    expect(screen.getByRole("heading", { level: 4, name: "New" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 4, name: "Lost" })).toBeTruthy();
    expect(screen.getByText("none").className).toContain("text-ink-muted");
  });
```

- [ ] **Step 2: Run the test file to verify the changed and new tests fail**

Run: `cd web && npx vitest run src/ui/report/ReportPart.test.tsx`
Expected: the movers test fails (old glyphs), the GP-line test fails (old `->`), the new/lost test fails (the "none" paragraph has no `text-ink-muted`). The others pass.

- [ ] **Step 3: Rewrite the two components**

`web/src/ui/report/MoverCards.tsx`:

```tsx
import { Badge } from "../primitives/Badge";
import { toneFor } from "./tone";
import type { EntityContext, Mover } from "./types";

function Card({ mover, ctx }: { mover: Mover; ctx?: EntityContext }) {
  const gp = mover.display.GP;
  return (
    <li className="rounded-card border border-border p-3 text-[13px]">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{mover.key}</span>
        <Badge>{mover.driver}</Badge>
      </div>
      <div className="tabular-nums">{`GP ${gp.prior} → ${gp.current}`}</div>
      <div className={`tabular-nums ${toneFor(mover.change.GP)}`}>{`${gp.change} (${gp.pct})`}</div>
      {ctx ? (
        <div className="mt-1 text-[12px] text-ink-muted tabular-nums">
          <div>{`Office ${ctx.reconciliation.display.office_change} · other offices ${ctx.reconciliation.display.other_offices_net} · book ${ctx.reconciliation.display.book_change}`}</div>
          <div>{`share of book ${ctx.share_of_book.display.prior} → ${ctx.share_of_book.display.current}`}</div>
        </div>
      ) : null}
    </li>
  );
}

/** Two columns, Increases and Decreases, one card per mover (spec 5): the
 * driver as a badge, the GP line with the arrow glyph, the change coloured
 * by sign (ruling P3-R2), the office reconciliation and share-of-book lines
 * muted and only when the report has entity context. */
export function MoverCards({ movers, context }: { movers: { increases: Mover[]; decreases: Mover[] }; context: Record<string, EntityContext> | null }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {(
        [
          ["Increases", movers.increases],
          ["Decreases", movers.decreases],
        ] as const
      ).map(([title, list]) => (
        <div key={title}>
          <h4 className="mb-2 text-base font-semibold">{title}</h4>
          <ul className="space-y-2">
            {list.map((m) => (
              <Card key={m.key} mover={m} ctx={context?.[m.key]} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
```

`web/src/ui/report/NewLostTables.tsx`:

```tsx
import { TABLE, TD, TD_NUM, TH, TH_NUM, TR } from "../primitives/data-table";
import type { Row } from "./types";

function Block({ title, rows, which }: { title: string; rows: Row[]; which: "current" | "prior" }) {
  return (
    <div>
      <h4 className="mb-2 text-base font-semibold">{title}</h4>
      {rows.length === 0 ? (
        <p className="text-[13px] text-ink-muted">none</p>
      ) : (
        <div className="overflow-x-auto">
          <table className={TABLE}>
            <thead>
              <tr>
                <th className={TH}>Name</th>
                <th className={TH_NUM}>GP</th>
                <th className={TH_NUM}>Volume</th>
                <th className={TH_NUM}>Margin</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.key} className={TR}>
                  <td className={TD}>{r.key}</td>
                  <td className={TD_NUM}>{r.display.GP[which]}</td>
                  <td className={TD_NUM}>{r.display.VOLUME[which]}</td>
                  <td className={TD_NUM}>{r.display.MARGIN[which]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

/** New (current-period values) and Lost (prior-period values) side by side
 * from 640px (spec 5). */
export function NewLostTables({ newLost }: { newLost: { new: Row[]; lost: Row[] } }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Block title="New" rows={newLost.new} which="current" />
      <Block title="Lost" rows={newLost.lost} which="prior" />
    </div>
  );
}
```

- [ ] **Step 4: Run the test file to verify it passes**

Run: `cd web && npx vitest run src/ui/report/ReportPart.test.tsx`
Expected: 14 tests pass.

- [ ] **Step 5: Dark sweep of the whole web tree, full suite, type-check, commit**

From the repo root: `grep -rn "dark:\|neutral-\|amber-\|red-\|blue-" web/src` (expected: no output; this is the design pass's closing condition, spec §6 item 3). In `web/`: `npm test > <scratch file> 2>&1`, read the file; `npx tsc --noEmit` clean.

```bash
git rev-parse --abbrev-ref HEAD   # must print feature/ui-pass-3-report
git add web/src/ui/report/MoverCards.tsx web/src/ui/report/NewLostTables.tsx web/src/ui/report/ReportPart.test.tsx
git commit -F <scratch message file>
git log --oneline feature/ui-pass-2-parts..HEAD
```

Message: `feat(web): mover cards with badges and signed changes, new/lost tables on the shared cells (UI pass PR 3, Task 3)`, body: `ReportPart.test.tsx: the reconciliation and share-of-book strings use the middle dot and the arrow glyph (ruling P3-R4).`

---

### Task 4: Docs and figures (Sonnet), then the browser gate (Fable)

**Files:**
- Modify: `CLAUDE.md` (the web figure in both places)
- Modify: `docs/superpowers/specs/2026-10-23-nextjs-ui-design-pass-design.md` (the `**Status:**` line)
- Read only: `docs/architecture/00-overview.md` (D82 already says the report renders as a document card; confirm and leave it)

- [ ] **Step 1: Run the gates and record the figures**

In `web/`: `npm test > <scratch>/web-test.txt 2>&1`; `npx tsc --noEmit > <scratch>/tsc.txt 2>&1`. From the repo root: `grep -rn "dark:" web/src` (expected: no output). Write down tests passed/skipped and files passed/skipped.

- [ ] **Step 2: Update the docs**

In `CLAUDE.md`, both web figure occurrences become the new count with `(UI pass PR 3)`; the file count line is updated (one new test file, `tone.test.ts`); the provenance phrase `not re-run in PR C, UI pass PR 1 or UI pass PR 2 (the Vite app is untouched by all three)` becomes `not re-run in PR C or the three UI pass PRs (the Vite app is untouched by all of them)`.

In the spec, the `**Status:**` line becomes: `Complete. PR 1 merged (#12, 2026-10-23); PR 2 feature/ui-pass-2-parts (plan 2026-10-23-ui-pass-2-parts.md); PR 3 feature/ui-pass-3-report (plan 2026-10-23-ui-pass-3-report.md). Follow-ons in §8.`

Confirm `docs/architecture/00-overview.md` D82 already reads "the report renders as a document card"; no edit.

- [ ] **Step 3: Commit**

```bash
git rev-parse --abbrev-ref HEAD   # must print feature/ui-pass-3-report
git add CLAUDE.md docs/superpowers/specs/2026-10-23-nextjs-ui-design-pass-design.md
git commit -F <scratch message file>
git log --oneline feature/ui-pass-2-parts..HEAD
```

Message: `docs: web figures for UI pass PR 3, spec status complete`.

- [ ] **Step 4: Browser gate (Fable, not a subagent)**

Compose up with the scratch port override; Next dev on :3000 with `POSEIDON_INTERNAL_TOKEN=dev-internal-token` and `DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon`. In a fresh conversation:

1. Send `start a monthly report`; follow the perspective and month chips the stub flow offers; the report card renders: header with the scope label, period pair, generated line with a middle dot; seven tiles with green/red change lines; the segmented control; the ranked table with the spanning header; mover cards with badges; new/lost; narrative with its heading; the proof disclosure closed; the chips row outside the card. Screenshot.
2. Click every segment: the table switches, the active segment lifts white in accent, the footer stays bold.
3. A month-to-date report (pick the current month if the stub data offers one): the warn badge after the period pair. If the stub data has no partial month, the existing `mtd` test is the evidence; record it.
4. Width 820px and the 501px floor: the segmented control wraps, the table scrolls inside its wrapper, the card keeps its padding, the composer's right edge is visible.
5. Console: no errors or warnings.

Screenshots go in the PR body.

---

## Self-review

- Spec coverage: §5 header (Task 1), KpiTiles (Task 1), RankedTables (Task 2), MoverCards (Task 3), NewLostTables (Task 3), Narrative (Task 1), ProofBlock (Task 1); the chips row outside the card needs no code (separate part). §6 item 3 closing condition in Task 3 Step 5 and Task 4 Step 1. §6 PR 3 gate in Task 4.
- Placeholders: none; every component is given in full.
- Type consistency: `toneFor` signature identical in Tasks 1, 2, 3; `TH_NUM` added in Task 1 and used in Tasks 2 and 3; `Badge` tone prop as PR 2 defined it; `Disclosure` summary prop as PR 2 defined it.
- Review Focus: 1 in `tone.test.ts`; 2 in Task 2's test plus the kept switching test; 3 in the gate; 4 in the kept whole-book test; 5 in Task 1's narrative test plus the kept guard test.
- Test counts: PR 2's final figure plus 1 (tone) + 3 (Task 1) + 1 (Task 2) + 2 (Task 3) = +7; Task 4 records the real figure.
