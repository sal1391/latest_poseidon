# Next.js UI design pass — design

**Date:** 2026-10-23
**Status:** PR 1 merged (#12, 2026-10-23); PR 2 built on feature/ui-pass-2-parts (plan: docs/superpowers/plans/2026-10-23-ui-pass-2-parts.md); PR 3 pending
**Owner of design decisions:** Fable. **Implementation:** worker subagents (Sonnet for the
component work, Haiku where the plan carries the complete code, Opus only for the riskiest
review). Fable never writes implementation code.
**Scope:** `web/` only. `frontend/` (the Vite app) is untouched and remains the look reference.

## 1. Why

The Next.js app (`web/`, what SPCS will serve) was built in Phase 16 to prove frame/part parity
with the Vite app, not to look like anything. It is a 26-line stock Tailwind stylesheet, a bare
conversation list page and a one-column chat with an `<input>` and a `Send` button. Carlos
prefers the Vite app's look ("slate": white surface, raised grey sidebar, pill composer, calm
type) and ruled on 2026-10-12 that the new app gets a Fable-designed styling pass before the
Vite app can be retired. This is that pass.

Decisions taken in the brainstorm (2026-10-23):

| # | Decision |
|---|----------|
| U1 | Same spirit as the Vite app, Fable-designed, not a pixel port. The report surfaces never existed in the Vite app and are designed fresh. |
| U2 | Tokens live in Tailwind 4's `@theme`; components use utility classes that name tokens. No raw hex in a component file. |
| U3 | The Next.js app gets the Vite-style sidebar shell. The root page becomes the empty state inside the shell. |
| U4 | Styling and shell only. No new product behaviour. Feedback thumbs, skills popover, settings panel, user menu are named follow-ons (§8). |
| U5 | Light only. The stock `prefers-color-scheme: dark` block is removed. |
| U6 | Three PRs: (1) tokens, fonts, shell, composer; (2) the nine chat parts; (3) the report surfaces. Each one clickable on its own. |
| U7 | Verification is a stub-mode browser walkthrough through compose (free, no LLM), reviewed as rendered pages, plus the web suite and `tsc`. |

## 2. Non-goals

- No change to the SSE envelope, the ten part kinds, `message-parts.ts`, the registry's
  interface (`PartProps`), route handlers, the proxy, Drizzle, or Python.
- No new dependencies beyond what `web/package.json` already has (Tailwind 4, react-markdown,
  next/font). No component library.
- No dark mode, no mobile design beyond a narrow-width fallback (§3.4).
- No change to `frontend/`.
- No feature ports (§8).

## 3. Section 1 — tokens and shell (PR 1)

### 3.1 Tokens

Source of truth: `web/src/app/globals.css`, in a Tailwind 4 `@theme` block. Values derive from
`frontend/src/theme/tokens.css` with two additions (accent and data font).

| Token | Value | Use |
|-------|-------|-----|
| `--color-surface` | `#ffffff` | page and chat column background, lifted rows |
| `--color-surface-raised` | `#f7f7f8` | sidebar, user bubbles, segmented-control track, hover |
| `--color-ink` | `#202123` | body text, headings |
| `--color-ink-muted` | `#8e9196` | secondary text, labels, placeholders, proof bodies |
| `--color-border` | `#e5e5e5` | hairlines everywhere |
| `--color-accent` | `#1f5f8b` (deep sea blue) | links, active sidebar row marker, focus rings, active tab text. **Not** the send button. |
| `--color-accent-ink` | `#ffffff` | text on accent |
| `--color-send` | `#202123` | the filled send circle (Vite's `--accent`) |
| `--color-positive` | `#10a37f` | positive deltas |
| `--color-negative` | `#d92d20` | negative deltas, error edge, tool error |
| `--color-warn-bg` / `--color-warn-ink` | `#fef3c7` / `#92400e` | the Month to date badge only |
| `--radius-s` / `-m` / `-l` | `8px` / `12px` / `24px` | chips and badges / cards and bubbles / composer pill |
| `--shadow-1` | `0 1px 2px rgba(0,0,0,.05)` | composer at rest, report card |
| `--shadow-2` | `0 4px 16px rgba(0,0,0,.08)` | composer focused |
| `--font-sans` | `"Segoe UI", system-ui, -apple-system, sans-serif` | everything |
| `--font-data` | same stack, with `font-variant-numeric: tabular-nums` applied by a `.data` utility | every number |
| motion | `120ms` fast, `240ms` slow | hover and focus transitions; reduced-motion media query zeroes them |

Rules:
- The Geist font imports in `layout.tsx` go. Segoe UI is the Vite choice and is on every Windows
  laptop the sales team uses; the system stack covers the rest. No webfont download.
- Remove the dark-mode media query and every `dark:` class in `web/src` (PR 1 removes them in
  the shell; PRs 2 and 3 remove them from the parts they restyle; a `grep -r "dark:" web/src`
  must be empty after PR 3).
- Type scale: body 15px / 1.6; small 13px; label 12px uppercase tracking-wide for table
  headers and tile labels; heading-1 20px semibold (page and report titles); heading-2 16px
  semibold (report section titles). No larger sizes anywhere.
- Focus: `outline: 2px solid var(--color-accent); outline-offset: 2px` on `:focus-visible`,
  global.

### 3.2 Shell

`web/src/app/layout.tsx` renders the shell for every page:

```
<body class="h-dvh grid grid-cols-[260px_1fr] bg-surface text-ink font-sans">
  <Sidebar />          server component, reads listConversations(sub)
  <main class="min-h-0 flex flex-col">{children}</main>
</body>
```

- Sidebar (`web/src/ui/shell/Sidebar.tsx`, server): `bg-surface-raised border-r border-border`,
  padding 20px 16px, column flex with 16px gap. Contents top to bottom: brand ("Poseidon",
  heading-1 weight, plus a one-line muted tagline "Sales intelligence"), the **New chat**
  button (full width, white, hairline border, radius-m, hover raises to `shadow-1`; it is the
  existing server action moved out of `page.tsx`), then the conversation list scrolling
  internally (`min-h-0 overflow-y-auto`). Each row is a `Link`, 13px, single line truncated
  with an ellipsis, padding 8px 10px, radius-s, hover `bg-surface`, the **active** row
  `bg-surface` with a 3px accent bar on its left edge and ink text. Untitled rows show
  "(untitled)" in muted ink. The active row is known from the `[id]` segment via
  `useSelectedLayoutSegments` in a tiny client child, or by passing the id down; the plan
  picks one and says why.
- The root page (`web/src/app/page.tsx`) becomes the empty state: centered in the chat column,
  heading-1 "Ask Poseidon", one muted sentence ("Breakdowns, comparisons, briefs and monthly
  reports for your book."), and the same New chat button. No list; the sidebar is the list.
- Identity: `requireSales` stays where it is. The sub is no longer printed in a heading; it
  moves to a muted 12px line at the sidebar foot ("Signed in as <sub>") so the dev identity is
  still visible during walkthroughs.
- Chat column (`web/src/app/c/[id]/page.tsx` + `Chat.tsx`): a header strip (title, heading-1,
  padding 16px 24px, hairline bottom border) then the thread, then the composer. The thread
  is `flex-1 min-h-0 overflow-y-auto`, inner column `max-w-[720px] mx-auto px-4 py-6 space-y-4`.
  Scrolls to the bottom on new messages (behaviour that exists implicitly today because the
  page grew; it must be explicit now that the thread scrolls internally).
- Message rows: the `role` label ("user"/"assistant" in uppercase) is removed. User turn:
  `self-end max-w-[85%] bg-surface-raised rounded-[--radius-m] px-4 py-3`. Assistant turn:
  no bubble, full width, `py-1`, its parts stacked with 12px gap.
- Composer (`web/src/ui/shell/Composer.tsx`, client, extracted from `Chat.tsx`): a pill
  `max-w-[720px] mx-auto mb-6 flex items-center gap-2 border border-border rounded-[--radius-l]
  bg-surface shadow-1 px-2`, `focus-within` to `border-ink-muted shadow-2`. The input is
  borderless, transparent, 15px, padding 14px 4px, placeholder in muted ink, label
  "Message Poseidon" kept. The send button is a 32px filled circle in `--color-send` with a
  white upward arrow glyph (inline SVG, `aria-label="Send"`), and when disabled it becomes
  `bg-surface-raised text-ink-muted`. Enter sends; the form semantics stay so the existing
  tests' `getByRole("button", {name: /send/i})` still resolves.
- The stream error line keeps its text and becomes `text-negative text-[13px]`.
- Narrow widths (§3.4).

### 3.3 Files touched by PR 1

`web/src/app/globals.css`, `web/src/app/layout.tsx`, `web/src/app/page.tsx`,
`web/src/app/c/[id]/page.tsx`, `web/src/app/c/[id]/Chat.tsx`, new `web/src/ui/shell/Sidebar.tsx`,
new `web/src/ui/shell/Composer.tsx`, new `web/src/ui/shell/EmptyState.tsx` (optional, if the
plan prefers it over inlining in `page.tsx`), their tests. `lib/conversations.ts` is called,
not changed.

### 3.4 Narrow widths

Below 768px the grid collapses to one column and the sidebar is hidden behind a "Chats"
button in the chat header that toggles it as an overlay. This is a fallback, not a mobile
design: the thread and composer must be usable at 390px wide with a 16px gutter and no
horizontal scroll. Nothing else is tuned for phones.

## 4. Section 2 — the nine chat parts (PR 2)

Every component keeps its name, file, props (`PartProps`) and behaviour. Tests assert content
and roles, not classes, and keep passing; a test that asserted a class name is updated in the
same commit with the reason in the plan. All `dark:` classes in these files are removed.

| Part | Design |
|------|--------|
| `TextPart` | Markdown with a scoped `.prose-poseidon` rule set in `globals.css`: 15px/1.6 body, `h1`–`h3` all 16px semibold with 12px top margin, lists with normal indent, `code` in the data font on the raised surface, links in accent with underline on hover only, paragraphs 8px apart. react-markdown's defaults otherwise. |
| `ChipsPart` | Pill buttons: `rounded-full border border-border bg-surface px-3 py-1 text-[13px]`, hover `bg-surface-raised`, focus ring accent. Disabled: `opacity-60 cursor-default`, no grey fill, so a replayed turn's chips still read. |
| `ToolEventPart` | One line `text-[13px] text-ink-muted flex items-center gap-2`; running: an 8px dot with the existing `animate-pulse`; done: a check glyph; error: a cross glyph and `text-negative`. |
| `ErrorPart` | `rounded-[--radius-m] border border-border border-l-4 border-l-negative bg-surface p-3 text-[13px]`; message in ink, hint beneath in muted ink. No pink fill. |
| `TablePart` | `w-full text-[13px]`, header cells `text-[12px] uppercase tracking-wide text-ink-muted font-medium text-left pb-1 border-b border-border`, body cells `py-1.5 px-2 border-b border-border`, numeric cells `text-right data`, row hover `bg-surface-raised`, no zebra. Wrapped in `overflow-x-auto`. |
| `ProofPart` | `<details>` with summary "How this was computed" in `text-[13px] font-medium` and a caret that rotates when open (CSS on `details[open] > summary::before`); body `text-[13px] text-ink-muted` list with 4px row gap; the block has a hairline border, radius-m, padding 8px 12px. Closed by default (unchanged). |
| `FallbackPart` | Same block as Proof, summary is the kind, body is the `<pre>` in the data font at 12px. |
| `MetricGridPart` | Cards `rounded-[--radius-m] border border-border p-3`; label `text-[12px] uppercase tracking-wide text-ink-muted`; the two values side by side, each value `text-lg font-semibold data`, unit `text-[12px] text-ink-muted`, the period range beneath `text-[12px] text-ink-muted data`. Two columns from 640px. Null still renders the em dash. |
| `ArtifactPart` | A card `inline-flex items-center gap-3 rounded-[--radius-m] border border-border px-3 py-2`: a 20px file glyph (inline SVG) in muted ink, the name as an accent link (download attribute kept), the MIME as a `rounded-[--radius-s] bg-surface-raised px-1.5 text-[12px] text-ink-muted` badge. |
| `PhaseSectionPart` | `<details open>` with the title as heading-2 in the summary, body set with `.prose-poseidon`; hairline border, radius-m, padding 12px. |

Shared helpers the plan may introduce (in `web/src/ui/primitives/`): `Disclosure` (the
Proof/Fallback/PhaseSection details shell), `DataTable` cell class constants, `Badge`. Keep
them tiny; no new abstractions beyond those three.

## 5. Section 3 — the report surfaces (PR 3)

The report renders as a **document card** inside the thread: `article` with
`rounded-[--radius-m] border border-border bg-surface shadow-1 p-5 space-y-5`, full width of
the 720px column. Components keep their names, props, `data-metric` attributes and ARIA roles.

| Surface | Design |
|---------|--------|
| Header (`ReportPart`) | Scope label as heading-1; period pair beneath in 15px ink; a muted 12px line "Generated … · Source: …" (middle dot, not a pipe); the Month to date badge `rounded-[--radius-s] bg-warn-bg text-warn-ink text-[12px] px-1.5 py-0.5` after the period pair. |
| `KpiTiles` | `grid grid-cols-2 sm:grid-cols-4 gap-3`; tile `rounded-[--radius-m] border border-border p-3`: metric name `text-[12px] uppercase tracking-wide text-ink-muted`; current value `text-xl font-semibold data`; change line `text-[13px] data` coloured `text-positive` / `text-negative` by the sign of the change (sign read from the display string's leading character, `-` or the minus sign, else positive; zero is ink); prior `text-[12px] text-ink-muted data` "prior …". |
| `RankedTables` | The `role="tablist"` becomes a segmented control: track `inline-flex rounded-[--radius-m] bg-surface-raised p-1 gap-1`, each tab `rounded-[--radius-s] px-3 py-1 text-[13px]`, the active one `bg-surface shadow-1 text-accent font-medium`, `aria-selected` unchanged. The table adds a first `<thead>` row of three spanning headers (`colSpan=4`, "GP", "Volume", "Margin", 12px uppercase muted, centered, with a hairline bottom border) above the existing twelve column headers, which become "Prior / Current / Change / %". Body as §4 table. The Other row `text-ink-muted`. Footer `font-semibold border-t border-border`. `overflow-x-auto` kept. |
| `MoverCards` | Two columns "Increases" / "Decreases" as heading-2; each card `rounded-[--radius-m] border border-border p-3 text-[13px]`: entity key `font-medium`; driver as a `Badge`; the GP line "GP prior → current" in the data font with the arrow glyph; the change line coloured by sign as in tiles; the office reconciliation and share-of-book lines `text-[12px] text-ink-muted data`. |
| `NewLostTables` | Two columns, "New" / "Lost" as heading-2, tables as §4, "none" in muted ink. |
| `Narrative` | heading-2 "Narrative", prose in `.prose-poseidon`, flags as a `text-[12px] text-ink-muted` list, "AI narrative withheld" as a muted 13px notice (not bold). The markdown guards (skipHtml, no links/images) are unchanged. |
| `ProofBlock` | The §4 `Disclosure`, summary "How this was computed", closed by default. |

The chips part that follows a report (D74) renders outside the card as a normal chips row.

## 6. Verification

Per PR, in this order, each result to its own file, never piped:

1. `cd web && npm test` (figure recorded in the PR body; 170 passed / 14 skipped is the current
   offline baseline, plus whatever tests the PR adds).
2. `cd web && npx tsc --noEmit` clean.
3. `grep -r "dark:" web/src` — PR 1 may still find hits in parts; PR 3 must find none.
4. Stub-mode browser walkthrough: compose up (`docker compose -f infra/docker-compose.yml up`,
   backend in stub mode as shipped), `npm run dev` in `web/` with `POSEIDON_INTERNAL_TOKEN=
   dev-internal-token` and `DRIZZLE_DATABASE_URL` set per `web/README.md`. Fable drives the
   browser and reviews rendered pages, not extracted text. Minimum script:
   - PR 1: load `/`, see the empty state and sidebar; New chat; send "hello"; see the user
     bubble, the assistant text, the composer mute while in flight; reload and see the same;
     resize to 390px and confirm no horizontal scroll.
   - PR 2: the recorded routing cases that exercise every part kind from
     `backend/tests/routing_cases.yml`: `default_data_qa_breakdown` (table, proof, chips),
     `ambiguous_customer_clarifies` (chips), `pivot_to_research_with_carry` (tool events,
     phase sections, artifact), a compare turn for `metric_grid`, and a forced error for
     `error` (the plan names the exact prompts for each).
   - PR 3: a monthly report turn (`report_lookup_top_ports` family) showing tiles, every
     ranked tab, movers, new/lost, narrative, proof and the following chips row; a month to
     date report for the badge.
5. The Vite suite and the Python suites are not run: nothing under `frontend/` or `backend/`
   changes. If a PR touches either, that PR's plan says so and runs the relevant suite.

Screenshots of each walkthrough go in the PR body.

## 7. Execution shape

Three PRs, each in a fresh chat from a handoff prompt, each on its own branch from `main`:
`feature/ui-pass-1-shell`, `feature/ui-pass-2-parts`, `feature/ui-pass-3-report`. PR 1 landed on `feature/ui-design-pass`, the branch that already held the spec (plan ruling). PR 2 branches
from `main` after PR 1 merges, PR 3 after PR 2. Subagent-driven development per task with a
fresh reviewer per diff. Model split: Sonnet implements every task (routine component and CSS
work against an explicit spec); Haiku only where the plan text carries the complete file; Opus
reviews the PR 1 layout/scroll change and the PR 3 ranked-table header change, which are the
two places a layout regression hides; Sonnet reviews the rest. Fable designs, specs, reviews
the browser walkthrough, never implements.

## 8. Follow-ons (not in this pass)

- Feedback thumbs under assistant messages (needs a Next.js route to the internal contract
  and the feedback table check).
- Skills popover on the composer (`/api/skills` exists on Python; needs a route and the picker).
- Settings panel and user menu (identity in SPCS mode is header-based, so the menu is a
  display, not a logout).
- Dark mode.
- Retiring the Vite app: the cut-over criteria in `docs/architecture/08` still apply; this pass
  removes the "it looks like a scaffold" blocker only.
- Rotate the Anthropic key pasted into the PR C chat (open, not a UI item, listed so it is not
  lost).

## 9. Decision-log entry

On merge of PR 1, add **D82** to `docs/architecture/00-overview.md`: "The Next.js app carries
the Vite app's slate look as Tailwind 4 theme tokens (U1–U7 of
`2026-10-23-nextjs-ui-design-pass-design.md`); sidebar shell, light only, no raw colours in
components; the report renders as a document card. Revises migration design §10's silence on
styling."
