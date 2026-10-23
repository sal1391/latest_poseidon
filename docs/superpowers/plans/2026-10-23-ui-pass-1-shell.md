# UI design pass, PR 1: tokens, shell, composer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Next.js app the slate look as Tailwind 4 theme tokens, a sidebar shell with the conversation list, an empty-state root page, a header strip on the chat page, a pill composer, and a thread that scrolls internally.

**Architecture:** Tokens live in `globals.css` under `@theme`. The root layout reads identity and the conversation list on the server and hands them to a client `Shell` (owns the narrow-width toggle) wrapping a server `Sidebar`. The chat page becomes header strip, internally scrolling thread, and a client `Composer` extracted from `Chat.tsx`. No change to the SSE contract, the part renderers, routes, Drizzle or Python.

**Tech Stack:** Next.js 16.3.4 (App Router, server actions), React 19.2.8, Tailwind 4.3.3 via `@tailwindcss/postcss`, vitest 5 with jsdom and `@testing-library/react`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-23-nextjs-ui-design-pass-design.md` sections 3, 6, 7.

**Model split (Carlos's cost rule):** Haiku for Tasks 1 and 2 (the plan carries the complete files and tests; transcription plus running the tests). Sonnet for Tasks 3 to 7 (server/client boundaries, mocks, docs). A Sonnet reviewer after every task. One Opus whole-branch review at the end, focused on the layout, the internal scroll and the server/client boundary of the shell (the two places the spec says a layout regression hides). Fable supervises, runs the browser walkthrough and never implements.

**Branch:** `feature/ui-design-pass`, already cut from `main` @ 606e693 and holding the spec (c09672b) and this plan. PR 1 lands on it. The spec's `feature/ui-pass-1-shell` name is retired; PRs 2 and 3 use the spec's names.

## Global Constraints

- `web/` only. Nothing under `frontend/` or `backend/` changes. If a task finds it must touch either, stop and report.
- Tokens are the spec's §3.1 values, verbatim, in `@theme`. No raw hex in any `.tsx` file.
- **Ruling P1-R1 (name collision):** Tailwind 4 already defines `rounded-s` and `rounded-l` as side utilities, so the spec's `--radius-s/m/l` become `--radius-chip` (8px), `--radius-card` (12px), `--radius-pill` (24px): utilities `rounded-chip`, `rounded-card`, `rounded-pill`.
- **Ruling P1-R2 (data font):** Tailwind's built-in `tabular-nums` utility is the spec's `.data` utility. No custom utility is added.
- **Ruling P1-R3 (narrow toggle placement):** the "Chats" toggle lives in a mobile-only top bar rendered by the client `Shell`, not in the chat page's header strip, because the open/closed state must live above both the sidebar and the page.
- Light only: the `prefers-color-scheme: dark` block and every `dark:` class in the files this PR touches go. Files PR 2 and PR 3 own keep theirs for now.
- The Geist font imports go; the font stack is `"Segoe UI", system-ui, -apple-system, sans-serif`.
- Type scale: body 15px/1.6; small `text-[13px]`; label `text-[12px]`; heading-1 `text-xl font-semibold`.
- Existing tests keep passing unchanged. A test that must change is changed in the same commit with the reason in the commit body.
- Commands: `cd web` first. Single file: `npx vitest run <path>`. Full suite: `npm test > <file> 2>&1`, then read the file. **Never pipe a test run.** Never set `DRIZZLE_DATABASE_URL` in a test shell (the db-backed files must keep skipping). Never run `next dev` or `next build` from a task (Fable runs the dev server for the browser gate); if `web/AGENTS.md` shows as modified, leave it unstaged.
- Before every commit: `git rev-parse --abbrev-ref HEAD` must print `feature/ui-design-pass`; after every commit `git log --oneline main..HEAD` must show only this branch's work. Anything else: stop and report, do not improvise.
- Commit messages via a scratch file and `git commit -F` (PowerShell mangles quotes). Each ends with the two attribution lines in the session's system reminder.

## Review Focus

1. **A long conversation title** (120 characters, no spaces): the sidebar row truncates with an ellipsis, the full title is in the `title` attribute, the sidebar does not widen. Pinned in Task 3's test.
2. **Sixty-plus messages:** the thread scrolls inside its column, the composer stays docked at the foot, the page body never scrolls. Browser gate (Task 7), plus the `overflow-hidden` on `body` in Task 1.
3. **A failed turn** (`status === "error"`): the error line shows and the composer re-enables so the user can retry. Pinned in Task 6's test.
4. **The narrow-width overlay** closes when a conversation is picked, so the user is not left behind a scrim. Pinned in Task 4's test.
5. **A user message with newlines or a long unbroken token** wraps inside its bubble instead of overflowing the column. Pinned in Task 6's test (newline preserved) and `break-words` on the bubble.

---

### Task 1: Theme tokens and the global stylesheet (Haiku)

**Files:**
- Modify: `web/src/app/globals.css` (replace whole file)
- Modify: `web/src/app/layout.tsx` (fonts only; the shell wiring is Task 5)
- Test: `web/src/app/globals.test.ts` (new)

**Interfaces:**
- Produces: utilities `bg-surface`, `bg-surface-raised`, `text-ink`, `text-ink-muted`, `border-border`, `text-accent`, `bg-accent`, `text-accent-ink`, `bg-send`, `text-positive`, `text-negative`, `bg-warn-bg`, `text-warn-ink`, `rounded-chip`, `rounded-card`, `rounded-pill`, `shadow-1`, `shadow-2`, `font-sans`. CSS variables `--color-*`, `--radius-*`, `--shadow-*`, `--motion-fast`, `--motion-slow`.

- [ ] **Step 1: Write the failing test**

`web/src/app/globals.test.ts`:

```ts
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npx vitest run src/app/globals.test.ts`
Expected: FAIL. The `@theme` block has none of the named tokens, the dark media query is present, and `layout.tsx` still imports `next/font`.

- [ ] **Step 3: Replace `globals.css`**

```css
@import "tailwindcss";

/* Slate theme (spec 3.1). Every colour, radius, shadow and font the app uses
   is a token here; components use the utilities Tailwind derives from these
   names and never a raw value. Light only (U5). */
@theme {
  --color-surface: #ffffff;
  --color-surface-raised: #f7f7f8;
  --color-ink: #202123;
  --color-ink-muted: #8e9196;
  --color-border: #e5e5e5;
  --color-accent: #1f5f8b;
  --color-accent-ink: #ffffff;
  --color-send: #202123;
  --color-positive: #10a37f;
  --color-negative: #d92d20;
  --color-warn-bg: #fef3c7;
  --color-warn-ink: #92400e;
  --radius-chip: 8px;
  --radius-card: 12px;
  --radius-pill: 24px;
  --shadow-1: 0 1px 2px rgba(0, 0, 0, 0.05);
  --shadow-2: 0 4px 16px rgba(0, 0, 0, 0.08);
  --font-sans: "Segoe UI", system-ui, -apple-system, sans-serif;
}

:root {
  --motion-fast: 120ms;
  --motion-slow: 240ms;
}

html,
body {
  height: 100%;
}

body {
  margin: 0;
  overflow: hidden;
  background: var(--color-surface);
  color: var(--color-ink);
  font-family: var(--font-sans);
  font-size: 15px;
  line-height: 1.6;
}

:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 4: Drop the Geist fonts from `layout.tsx`**

Replace the whole file with (the shell wiring comes in Task 5; this keeps the page rendering meanwhile):

```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Poseidon",
  description: "Chat-first sales intelligence for marine fuel sales",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-dvh bg-surface font-sans text-ink">{children}</body>
    </html>
  );
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd web && npx vitest run src/app/globals.test.ts`
Expected: PASS, 18 tests.

- [ ] **Step 6: Type check and commit**

Run: `cd web && npx tsc --noEmit` → no output.
Branch check, then commit `web/src/app/globals.css web/src/app/layout.tsx web/src/app/globals.test.ts`:

```
feat(web): slate theme tokens in @theme, light only, system font stack

Spec 3.1. Rulings P1-R1 (radius token names: chip/card/pill, because
rounded-s and rounded-l are Tailwind side utilities) and P1-R2 (tabular-nums
is the data utility).
```

---

### Task 2: The composer (Haiku)

**Files:**
- Create: `web/src/ui/shell/Composer.tsx`
- Test: `web/src/ui/shell/Composer.test.tsx`

**Interfaces:**
- Produces: `export function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void })`. Owns the draft. Calls `onSend` with the raw draft (not trimmed) on submit when the draft is not blank and `disabled` is false, then clears the draft. The input's accessible name is `Message Poseidon`; the button's is `Send`.

- [ ] **Step 1: Write the failing test**

`web/src/ui/shell/Composer.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

afterEach(cleanup);

function input() {
  return screen.getByLabelText("Message Poseidon") as HTMLInputElement;
}
function sendButton() {
  return screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;
}

describe("Composer", () => {
  it("sends the draft on submit and clears it", () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    fireEvent.change(input(), { target: { value: "hello" } });
    fireEvent.submit(input().closest("form")!);
    expect(onSend).toHaveBeenCalledWith("hello");
    expect(input().value).toBe("");
  });

  it("keeps the send button muted while the draft is blank", () => {
    const onSend = vi.fn();
    render(<Composer disabled={false} onSend={onSend} />);
    expect(sendButton().disabled).toBe(true);
    fireEvent.change(input(), { target: { value: "   " } });
    expect(sendButton().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.change(input(), { target: { value: "x" } });
    expect(sendButton().disabled).toBe(false);
  });

  it("disables the input and never sends while a turn is in flight", () => {
    const onSend = vi.fn();
    render(<Composer disabled onSend={onSend} />);
    expect(input().disabled).toBe(true);
    expect(sendButton().disabled).toBe(true);
    fireEvent.submit(input().closest("form")!);
    expect(onSend).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npx vitest run src/ui/shell/Composer.test.tsx`
Expected: FAIL with "Failed to resolve import ./Composer".

- [ ] **Step 3: Write `Composer.tsx`**

```tsx
"use client";

import { useState, type FormEvent } from "react";

/**
 * The docked pill composer (spec 3.2). Owns its draft; the page owns the
 * in-flight state and passes it as `disabled`. Submit sends the draft as
 * typed and clears it. The Send button is a filled circle in `--color-send`
 * that mutes to the raised surface while there is nothing to send.
 */
export function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void }) {
  const [draft, setDraft] = useState("");
  const blank = draft.trim() === "";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || blank) return;
    const text = draft;
    setDraft("");
    onSend(text);
  }

  return (
    <form
      onSubmit={submit}
      className="mx-auto mb-6 flex w-full max-w-[720px] items-center gap-2 rounded-pill border border-border bg-surface px-2 shadow-1 transition-[border-color,box-shadow] duration-[120ms] focus-within:border-ink-muted focus-within:shadow-2"
    >
      <input
        className="min-w-0 flex-1 bg-transparent px-1 py-3.5 text-[15px] text-ink outline-none placeholder:text-ink-muted"
        aria-label="Message Poseidon"
        placeholder="Message Poseidon…"
        value={draft}
        disabled={disabled}
        onChange={(event) => setDraft(event.target.value)}
      />
      <button
        type="submit"
        aria-label="Send"
        disabled={disabled || blank}
        className="inline-flex h-8 w-8 flex-none items-center justify-center rounded-full bg-send text-accent-ink transition-opacity duration-[120ms] disabled:bg-surface-raised disabled:text-ink-muted"
      >
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8 13V3M4 7l4-4 4 4" />
        </svg>
      </button>
    </form>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd web && npx vitest run src/ui/shell/Composer.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 5: Type check and commit**

Run: `cd web && npx tsc --noEmit` → no output.
Branch check, then commit both files: `feat(web): pill composer component (spec 3.2)`.

---

### Task 3: Sidebar, conversation list and the new-conversation action (Sonnet)

**Files:**
- Create: `web/src/lib/actions.ts`
- Create: `web/src/ui/shell/ConversationList.tsx`
- Create: `web/src/ui/shell/Sidebar.tsx`
- Test: `web/src/ui/shell/Sidebar.test.tsx`

**Interfaces:**
- Consumes: `createConversation(sub): Promise<string>` and `requireSales(headers): { sub, roles }` from `web/src/lib`.
- Produces:
  - `export async function newConversation(): Promise<void>` in `lib/actions.ts` (`"use server"`): requires Sales, creates the conversation, `revalidatePath("/", "layout")`, redirects to `/c/<id>`.
  - `export interface ConversationRow { id: string; title: string | null }` and `export function ConversationList({ conversations }: { conversations: ConversationRow[] })` (client; reads the active id with `useParams`).
  - `export function Sidebar({ conversations, sub }: { conversations: ConversationRow[]; sub: string })` (server, synchronous; no data fetching of its own).

- [ ] **Step 1: Write the failing test**

`web/src/ui/shell/Sidebar.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const params = vi.hoisted(() => ({ current: {} as Record<string, string> }));
vi.mock("next/navigation", () => ({ useParams: () => params.current }));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: ComponentProps<"a"> & { href: string }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));
vi.mock("../../lib/actions", () => ({ newConversation: vi.fn() }));

import { Sidebar } from "./Sidebar";

afterEach(() => {
  cleanup();
  params.current = {};
});

const LONG = "x".repeat(120);
const rows = [
  { id: "11111111-1111-7111-8111-111111111111", title: "Acme GP by port" },
  { id: "22222222-2222-7222-8222-222222222222", title: null },
  { id: "33333333-3333-7333-8333-333333333333", title: LONG },
];

describe("Sidebar", () => {
  it("shows the brand, the New chat button and the signed-in sub", () => {
    render(<Sidebar conversations={rows} sub="dev|alice" />);
    expect(screen.getByText("Poseidon")).toBeTruthy();
    expect(screen.getByRole("button", { name: "New chat" })).toBeTruthy();
    expect(screen.getByText(/Signed in as dev\|alice/)).toBeTruthy();
  });

  it("links every conversation and marks the active one with aria-current", () => {
    params.current = { id: rows[0].id };
    render(<Sidebar conversations={rows} sub="dev|alice" />);
    const active = screen.getByRole("link", { name: "Acme GP by port" });
    expect(active.getAttribute("href")).toBe(`/c/${rows[0].id}`);
    expect(active.getAttribute("aria-current")).toBe("page");
    const untitled = screen.getByRole("link", { name: "(untitled)" });
    expect(untitled.getAttribute("aria-current")).toBeNull();
  });

  it("carries a long title in the title attribute so truncation loses nothing", () => {
    render(<Sidebar conversations={rows} sub="dev|alice" />);
    expect(screen.getByRole("link", { name: LONG }).getAttribute("title")).toBe(LONG);
  });

  it("says so when there are no conversations", () => {
    render(<Sidebar conversations={[]} sub="dev|alice" />);
    expect(screen.getByText("No conversations yet")).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npx vitest run src/ui/shell/Sidebar.test.tsx`
Expected: FAIL with "Failed to resolve import ./Sidebar".

- [ ] **Step 3: Write `lib/actions.ts`**

```ts
"use server";

import { revalidatePath } from "next/cache";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { createConversation } from "./conversations";
import { requireSales } from "./request-identity";

/**
 * The New chat action, shared by the sidebar and the empty state. The root
 * layout renders the conversation list, and a layout is not re-fetched on a
 * soft navigation, so the new row would not appear without the layout-scoped
 * revalidation before the redirect.
 */
export async function newConversation(): Promise<void> {
  const { sub } = requireSales(await headers());
  const id = await createConversation(sub);
  revalidatePath("/", "layout");
  redirect(`/c/${id}`);
}
```

- [ ] **Step 4: Write `ConversationList.tsx`**

```tsx
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

export interface ConversationRow {
  id: string;
  title: string | null;
}

/**
 * The sidebar's conversation rows. Client-side only so it can read the
 * active `[id]` segment with `useParams`; the rows themselves come from the
 * server layout. The active row is lifted onto the base surface with a 3px
 * accent bar drawn as an inset shadow so the row's width never changes.
 */
export function ConversationList({ conversations }: { conversations: ConversationRow[] }) {
  const params = useParams<{ id?: string }>();
  const activeId = typeof params?.id === "string" ? params.id : null;
  if (conversations.length === 0) {
    return <p className="px-2.5 text-[13px] text-ink-muted">No conversations yet</p>;
  }
  return (
    <nav className="min-h-0 flex-1 overflow-y-auto" aria-label="Conversation list">
      <ul className="space-y-0.5">
        {conversations.map((c) => {
          const active = c.id === activeId;
          const label = c.title ?? "(untitled)";
          return (
            <li key={c.id}>
              <Link
                href={`/c/${c.id}`}
                title={label}
                aria-current={active ? "page" : undefined}
                className={`block truncate rounded-chip px-2.5 py-2 text-[13px] transition-colors duration-[120ms] ${
                  active ? "bg-surface text-ink shadow-[inset_3px_0_0_0_var(--color-accent)]" : "text-ink hover:bg-surface"
                } ${c.title === null ? "text-ink-muted" : ""}`}
              >
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
```

- [ ] **Step 5: Write `Sidebar.tsx`**

```tsx
import { newConversation } from "../../lib/actions";
import { ConversationList, type ConversationRow } from "./ConversationList";

/**
 * The fixed left column (spec 3.2): brand, New chat, the conversation list
 * scrolling internally, and the signed-in sub at the foot so the dev
 * identity stays visible during walkthroughs. Synchronous: the layout does
 * the reading and passes rows in.
 */
export function Sidebar({ conversations, sub }: { conversations: ConversationRow[]; sub: string }) {
  return (
    <aside className="flex h-full min-h-0 flex-col gap-4 border-r border-border bg-surface-raised px-4 py-5" aria-label="Conversations">
      <div>
        <p className="text-xl font-semibold leading-tight">Poseidon</p>
        <p className="text-[12px] text-ink-muted">Sales intelligence</p>
      </div>
      <form action={newConversation}>
        <button
          type="submit"
          className="w-full rounded-card border border-border bg-surface px-3 py-2 text-[13px] font-medium transition-shadow duration-[120ms] hover:shadow-1"
        >
          New chat
        </button>
      </form>
      <ConversationList conversations={conversations} />
      <p className="mt-auto truncate text-[12px] text-ink-muted" title={sub}>
        Signed in as {sub}
      </p>
    </aside>
  );
}
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd web && npx vitest run src/ui/shell/Sidebar.test.tsx`
Expected: PASS, 4 tests. If React warns about a function `action` on `<form>` in jsdom, that is a console warning, not a failure; note it in the report.

- [ ] **Step 7: Type check and commit**

Run: `cd web && npx tsc --noEmit` → no output.
Branch check, then commit the four files: `feat(web): sidebar with conversation list and shared New chat action (spec 3.2)`.

---

### Task 4: The shell with the narrow-width toggle (Sonnet)

**Files:**
- Create: `web/src/ui/shell/Shell.tsx`
- Test: `web/src/ui/shell/Shell.test.tsx`

**Interfaces:**
- Produces: `export function Shell({ sidebar, children }: { sidebar: ReactNode; children: ReactNode })` (client). Desktop (≥768px): two-column grid, sidebar 260px, `<main>` fluid. Below 768px: a top bar with a `Chats` button (`aria-expanded`, `aria-controls="sidebar"`); the sidebar is hidden until opened, then a fixed 260px overlay with a scrim; any click inside the sidebar (a row, New chat) closes it.

- [ ] **Step 1: Write the failing test**

`web/src/ui/shell/Shell.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { Shell } from "./Shell";

afterEach(cleanup);

function show() {
  return render(
    <Shell sidebar={<a href="/c/1">Row one</a>}>
      <p>page body</p>
    </Shell>,
  );
}

describe("Shell", () => {
  it("renders the sidebar slot and the page inside main", () => {
    show();
    expect(screen.getByText("Row one")).toBeTruthy();
    expect(screen.getByRole("main").textContent).toContain("page body");
  });

  it("the Chats toggle opens and closes the overlay", () => {
    show();
    const toggle = screen.getByRole("button", { name: "Chats" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: "Close conversations" })).toBeNull();
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Close conversations" }));
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("picking something inside the sidebar closes the overlay", () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Chats" }));
    fireEvent.click(screen.getByText("Row one"));
    expect(screen.getByRole("button", { name: "Chats" }).getAttribute("aria-expanded")).toBe("false");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npx vitest run src/ui/shell/Shell.test.tsx`
Expected: FAIL with "Failed to resolve import ./Shell".

- [ ] **Step 3: Write `Shell.tsx`**

```tsx
"use client";

import { useState, type ReactNode } from "react";

/**
 * The app shell (spec 3.2, 3.4). On desktop a 260px sidebar and a fluid
 * main column fill the viewport; the thread scrolls inside main. Below the
 * md breakpoint the sidebar hides behind a Chats toggle (ruling P1-R3: the
 * toggle lives here, not in the page header, because the open state must sit
 * above both) and opens as a fixed overlay with a scrim. Any click inside
 * the open sidebar closes it, so choosing a row never leaves the scrim up.
 */
export function Shell({ sidebar, children }: { sidebar: ReactNode; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="grid h-full grid-cols-1 grid-rows-[auto_1fr] md:grid-cols-[260px_1fr] md:grid-rows-1">
      <div className="flex items-center gap-3 border-b border-border px-4 py-2 md:hidden">
        <button
          type="button"
          aria-expanded={open}
          aria-controls="sidebar"
          onClick={() => setOpen((value) => !value)}
          className="rounded-chip border border-border bg-surface px-3 py-1 text-[13px]"
        >
          Chats
        </button>
        <span className="text-[13px] font-semibold">Poseidon</span>
      </div>
      <div
        id="sidebar"
        onClick={() => setOpen(false)}
        className={`${open ? "fixed inset-y-0 left-0 z-20 w-[260px] shadow-2" : "hidden"} md:static md:block md:w-auto md:shadow-none`}
      >
        {sidebar}
      </div>
      {open ? (
        <button type="button" aria-label="Close conversations" onClick={() => setOpen(false)} className="fixed inset-0 z-10 bg-ink/20 md:hidden" />
      ) : null}
      <main className="flex min-h-0 flex-col">{children}</main>
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd web && npx vitest run src/ui/shell/Shell.test.tsx`
Expected: PASS, 3 tests.

- [ ] **Step 5: Type check and commit**

Run: `cd web && npx tsc --noEmit` → no output.
Branch check, then commit both files: `feat(web): app shell with narrow-width sidebar toggle (spec 3.2, 3.4)`.

---

### Task 5: Wire the layout, the empty-state root page and the chat header (Sonnet)

**Files:**
- Modify: `web/src/app/layout.tsx` (replace whole file)
- Modify: `web/src/app/page.tsx` (replace whole file)
- Modify: `web/src/app/c/[id]/page.tsx` (the returned JSX only)
- Test: `web/src/app/page.test.tsx` (new)

**Interfaces:**
- Consumes: `Shell`, `Sidebar`, `ConversationRow`, `newConversation`, `listConversations`, `requireSales`.
- Produces: the root layout is now `async`, reads identity and the conversation rows, and renders `<Shell sidebar={<Sidebar …/>}>{children}</Shell>`. Pages render inside `<main class="flex min-h-0 flex-col">`, so a page that wants an internally scrolling region uses `flex-1 min-h-0 overflow-y-auto` (Task 6 does).

- [ ] **Step 1: Write the failing test**

`web/src/app/page.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../lib/actions", () => ({ newConversation: vi.fn() }));

import Home from "./page";

afterEach(cleanup);

describe("root page (empty state, spec 3.2)", () => {
  it("greets, explains in one line, and offers New chat", () => {
    render(<Home />);
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Ask Poseidon");
    expect(screen.getByText("Breakdowns, comparisons, briefs and monthly reports for your book.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "New chat" })).toBeTruthy();
  });

  it("no longer lists conversations itself (the sidebar does)", () => {
    render(<Home />);
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByText(/Conversations for/)).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npx vitest run src/app/page.test.tsx`
Expected: FAIL. Today's `Home` is async, reads `headers()` and renders the "Conversations for" heading; in jsdom the render fails or the assertions do.

- [ ] **Step 3: Replace `page.tsx`**

```tsx
import { newConversation } from "../lib/actions";

/** The empty state inside the shell (spec 3.2). The sidebar is the list. */
export default function Home() {
  return (
    <section className="flex flex-1 flex-col items-center justify-center gap-3 px-4 text-center">
      <h1 className="text-xl font-semibold">Ask Poseidon</h1>
      <p className="max-w-md text-[15px] text-ink-muted">Breakdowns, comparisons, briefs and monthly reports for your book.</p>
      <form action={newConversation}>
        <button type="submit" className="rounded-card border border-border bg-surface px-4 py-2 text-[13px] font-medium transition-shadow duration-[120ms] hover:shadow-1">
          New chat
        </button>
      </form>
    </section>
  );
}
```

- [ ] **Step 4: Replace `layout.tsx`**

```tsx
import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";
import { listConversations } from "../lib/conversations";
import { requireSales } from "../lib/request-identity";
import { Shell } from "../ui/shell/Shell";
import { Sidebar } from "../ui/shell/Sidebar";

export const metadata: Metadata = {
  title: "Poseidon",
  description: "Chat-first sales intelligence for marine fuel sales",
};

/**
 * Every page renders inside the shell. Identity and the conversation list
 * are read here, once, under the same RLS the pages use; the proxy has
 * already refused anyone without Poseidon:Sales, so `requireSales` is the
 * belt to that brace. The list is a layout read, so it refreshes on the New
 * chat action's `revalidatePath("/", "layout")` and on a hard reload, not
 * after every turn (titles set by a turn appear on the next navigation).
 */
export default async function RootLayout({ children }: LayoutProps<"/">) {
  const { sub } = requireSales(await headers());
  const rows = await listConversations(sub);
  const conversations = rows.map((c) => ({ id: c.id, title: c.title }));
  return (
    <html lang="en" className="h-full antialiased">
      <body className="h-dvh bg-surface font-sans text-ink">
        <Shell sidebar={<Sidebar conversations={conversations} sub={sub} />}>{children}</Shell>
      </body>
    </html>
  );
}
```

- [ ] **Step 5: Give the chat page its header strip**

In `web/src/app/c/[id]/page.tsx`, replace the `return (...)` block only:

```tsx
  return (
    <>
      <header className="border-b border-border px-6 py-4">
        <h1 className="truncate text-xl font-semibold">{loaded.conversation.title ?? "(untitled)"}</h1>
      </header>
      <Chat conversationId={id} initialMessages={initial} />
    </>
  );
```

Everything above the return (params, `requireSales`, `loadConversation`, `notFound`, `initial`) stays exactly as it is.

- [ ] **Step 6: Run the new test and the whole web suite**

Run: `cd web && npx vitest run src/app/page.test.tsx` → PASS, 2 tests.
Run: `cd web && npm test > ../.superpowers/sdd/2026-10-23-ui-pass-1-shell/web-test-task5.txt 2>&1` (create the folder if absent), then read the tail of the file. Expected: every file passes, the db-backed files skip, no failures.

- [ ] **Step 7: Type check and commit**

Run: `cd web && npx tsc --noEmit` → no output. If `LayoutProps` is reported missing, the generated `.next/types` are absent in this checkout; run `npx next typegen` once and re-run `tsc`; do not run `next dev`.
Branch check, then commit the four files: `feat(web): shell in the root layout, empty-state root page, chat header strip (spec 3.2)`.

---

### Task 6: The chat thread: bubbles, internal scroll, composer (Sonnet)

**Files:**
- Modify: `web/src/app/c/[id]/Chat.tsx` (replace whole file)
- Test: `web/src/app/c/[id]/Chat.test.tsx` (new)

**Interfaces:**
- Consumes: `Composer`, `PartRenderer`, `toMessageParts`, `useChat`, `DefaultChatTransport` (unchanged usage).
- Produces: `Chat` keeps its props `{ conversationId, initialMessages }`. The transport, `client_turn_key` handling, the `busy` definition and the chip `onChipSelect` path are unchanged. The role labels are gone. The thread is `flex-1 min-h-0 overflow-y-auto` and scrolls its end into view whenever `messages` changes.

- [ ] **Step 1: Write the failing test**

`web/src/app/c/[id]/Chat.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PoseidonUIMessage } from "../../../lib/message-parts";

const chat = vi.hoisted(() => ({
  state: {
    messages: [] as PoseidonUIMessage[],
    sendMessage: vi.fn(),
    status: "ready" as "ready" | "submitted" | "streaming" | "error",
    error: undefined as Error | undefined,
  },
}));
vi.mock("@ai-sdk/react", () => ({ useChat: () => chat.state }));
vi.mock("ai", () => ({
  DefaultChatTransport: class {
    constructor(_options: unknown) {}
  },
}));

import { Chat } from "./Chat";

afterEach(() => {
  cleanup();
  chat.state.messages = [];
  chat.state.status = "ready";
  chat.state.error = undefined;
});

const user: PoseidonUIMessage = { id: "u1", role: "user", parts: [{ type: "text", text: "GP for Acme\nby port" }] };
const assistant: PoseidonUIMessage = {
  id: "a1",
  role: "assistant",
  parts: [{ type: "data-part", data: { kind: "text", payload: { markdown: "Here it is." } } } as PoseidonUIMessage["parts"][number]],
};

describe("Chat", () => {
  it("renders user text as a bubble with its line breaks and no role labels", () => {
    chat.state.messages = [user, assistant];
    render(<Chat conversationId="c1" initialMessages={[]} />);
    const bubble = screen.getByText(/GP for Acme/);
    expect(bubble.textContent).toBe("GP for Acme\nby port");
    expect(screen.getByText("Here it is.")).toBeTruthy();
    expect(screen.queryByText("user")).toBeNull();
    expect(screen.queryByText("assistant")).toBeNull();
  });

  it("mutes the composer while a turn is in flight", () => {
    chat.state.status = "streaming";
    render(<Chat conversationId="c1" initialMessages={[]} />);
    expect((screen.getByLabelText("Message Poseidon") as HTMLInputElement).disabled).toBe(true);
  });

  it("shows a failed turn and re-enables the composer so the user can retry", () => {
    chat.state.status = "error";
    chat.state.error = new Error("stream ended without a terminal frame");
    render(<Chat conversationId="c1" initialMessages={[]} />);
    expect(screen.getByText("stream ended without a terminal frame")).toBeTruthy();
    expect((screen.getByLabelText("Message Poseidon") as HTMLInputElement).disabled).toBe(false);
  });
});
```

Check the assistant part shape against `web/src/lib/message-parts.ts` before running: the test needs one assistant part that `toMessageParts` turns into a `text` part with that markdown. If the real type for a data part differs from `{ type: "data-part", data: { kind, payload } }`, use the real shape (look at how `fromMessageParts` builds parts) and keep the assertion.

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npx vitest run "src/app/c/[id]/Chat.test.tsx"`
Expected: FAIL on the first test: the role labels "user" and "assistant" are rendered today (`queryByText("user")` finds the uppercase-styled span by its text content).

- [ ] **Step 3: Replace `Chat.tsx`**

```tsx
"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useEffect, useMemo, useRef } from "react";
import { toMessageParts, type PoseidonUIMessage } from "../../../lib/message-parts";
import { PartRenderer } from "../../../ui/message-parts/registry";
import { Composer } from "../../../ui/shell/Composer";

/** client_turn_key per user message, reused if the SDK retries the same message (Python dedups on it). */
const turnKeys = new Map<string, string>();

/** The text a user message carries, folded back out of its parts. */
function userText(message: PoseidonUIMessage): string {
  return message.parts
    .filter((p) => p.type === "text")
    .map((p) => (p as { text: string }).text)
    .join("");
}

export function Chat({ conversationId, initialMessages }: { conversationId: string; initialMessages: PoseidonUIMessage[] }) {
  const transport = useMemo(
    () =>
      new DefaultChatTransport<PoseidonUIMessage>({
        api: `/api/chat/${conversationId}`,
        // The route takes Python's send body (`text` + `client_turn_key`), not
        // the SDK's default `{ messages }`: the conversation lives in Postgres
        // and Python reads its own history, so shipping the whole transcript
        // back would be both wasted bytes and a second, disagreeing source of
        // truth for what was said.
        prepareSendMessagesRequest: ({ messages }) => {
          const last = messages[messages.length - 1];
          const text = userText(last);
          if (!turnKeys.has(last.id)) turnKeys.set(last.id, crypto.randomUUID());
          return { body: { text, client_turn_key: turnKeys.get(last.id) } };
        },
      }),
    [conversationId],
  );
  const { messages, sendMessage, status, error } = useChat<PoseidonUIMessage>({ id: conversationId, messages: initialMessages, transport });
  /**
   * In flight, which is NOT `status !== "ready"`: the fourth status is
   * `"error"`, and treating it as busy would leave the composer and every chip
   * disabled after a failed turn, with reloading the page as the only way out.
   * `sendMessage` clears the error and moves back to `"submitted"` itself
   * (`AbstractChat.makeRequest`), so retrying from an error state is exactly
   * what should stay possible.
   */
  const busy = status === "submitted" || status === "streaming";

  // The thread scrolls inside its own column now (spec 3.2), so the page no
  // longer grows with the conversation and the browser no longer keeps the
  // newest message in view for free. jsdom has no scrollIntoView; the
  // optional call keeps the component testable there.
  const endRef = useRef<HTMLLIElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [messages]);

  function send(text: string) {
    if (busy || text.trim() === "") return;
    void sendMessage({ text });
  }

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <ol className="mx-auto flex w-full max-w-[720px] flex-col gap-4 px-4 py-6">
          {messages.map((m) =>
            m.role === "user" ? (
              <li key={m.id} className="max-w-[85%] self-end whitespace-pre-wrap break-words rounded-card bg-surface-raised px-4 py-3">
                {userText(m)}
              </li>
            ) : (
              <li key={m.id} className="flex flex-col gap-3 py-1">
                {/* In order, every part: P16-R17 means one assistant message can
                    hold SEVERAL text parts (a token run is closed by the next
                    non-token frame), so there is no such thing as "the" text part
                    to single out. */}
                {toMessageParts(m).map((part, i) => (
                  <PartRenderer key={i} part={part} disabled={busy} onChipSelect={(_id, sendText) => send(sendText)} />
                ))}
              </li>
            ),
          )}
          {/* The translator's `end()` emits an `error` chunk on a stream that died
              without a terminal frame, and `useChat` surfaces it here. Rendering it
              is what makes a truncated turn visible rather than merely short. */}
          {error ? <li className="text-[13px] text-negative">{error.message}</li> : null}
          <li ref={endRef} aria-hidden="true" />
        </ol>
      </div>
      <div className="px-4">
        <Composer disabled={busy} onSend={send} />
      </div>
    </>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd web && npx vitest run "src/app/c/[id]/Chat.test.tsx"`
Expected: PASS, 3 tests.

- [ ] **Step 5: Prove the scroll effect is wired, then restore**

Temporarily delete the `useEffect` block, add to the test file a fourth test that stubs `Element.prototype.scrollIntoView = vi.fn()` before render and asserts it was called once with `{ block: "end" }` after rendering two messages. Run it: it must FAIL. Restore the `useEffect`, run again: PASS. Keep the fourth test.

- [ ] **Step 6: Whole web suite, type check, commit**

Run: `cd web && npm test > ../.superpowers/sdd/2026-10-23-ui-pass-1-shell/web-test-task6.txt 2>&1`, read the tail: all pass, db files skip.
Run: `cd web && npx tsc --noEmit` → no output.
Branch check, then commit both files: `feat(web): chat thread with user bubbles, internal scroll and the pill composer (spec 3.2)`.

---

### Task 7: Dark-class sweep, docs, figures (Sonnet), then the browser gate (Fable)

**Files:**
- Modify: `docs/architecture/00-overview.md` (add D82 after D81 in the decision table)
- Modify: `CLAUDE.md` (the web `npm test` figure line in Commands and the figures paragraph)
- Modify: `docs/superpowers/specs/2026-10-23-nextjs-ui-design-pass-design.md` (status line and §7 branch note)

- [ ] **Step 1: Confirm the shell files carry no dark classes**

Run: `cd web && grep -rn "dark:" src/app src/ui/shell` → no output. (Hits under `src/ui/message-parts` and `src/ui/report` are PR 2 and PR 3's.) Also `grep -rn "#[0-9a-fA-F]\{6\}" src --include=*.tsx` → no output.

- [ ] **Step 2: D82 in the decision log**

Append one row to the decision table in `docs/architecture/00-overview.md`, directly after D81, keeping the table's three columns:

```
| D82 | The Next.js app carries the Vite app's slate look as Tailwind 4 theme tokens (U1–U7 of `docs/superpowers/specs/2026-10-23-nextjs-ui-design-pass-design.md`): sidebar shell, light only, no raw colours in components, the report renders as a document card. Radius tokens are `chip/card/pill` (ruling P1-R1). Revises the migration design's silence on styling (§10). | migration design §10 |
```

- [ ] **Step 3: CLAUDE.md figures**

Run the whole web suite once more to a file: `cd web && npm test > ../.superpowers/sdd/2026-10-23-ui-pass-1-shell/web-test-final.txt 2>&1`, then `npx tsc --noEmit`. Put the pass/skip figure from the file's last lines into `CLAUDE.md`: the `# Web (Next.js)` comment line in Commands and the figures paragraph that follows the block, each marked "verified 2026-10-23 (UI pass PR 1)". Leave every other figure as it is (Python and Vite are untouched by this PR).

- [ ] **Step 4: Spec status**

In the spec, change the Status line to `PR 1 built on feature/ui-design-pass (plan: docs/superpowers/plans/2026-10-23-ui-pass-1-shell.md); PRs 2 and 3 pending` and add to §7, after the branch names: "PR 1 landed on `feature/ui-design-pass`, the branch that already held the spec (plan ruling)."

- [ ] **Step 5: Commit**

Branch check, then commit the three docs: `docs: D82 slate theme, web figures for UI pass PR 1, spec status`.

- [ ] **Step 6: Browser gate (Fable, not a subagent)**

Compose up (`docker compose -f infra/docker-compose.yml up`, stub mode as shipped), then in `web/`: `$env:POSEIDON_INTERNAL_TOKEN="dev-internal-token"; $env:DRIZZLE_DATABASE_URL="postgresql://poseidon:poseidon@localhost:<db port>/poseidon"; npm run dev`. Script from spec §6, PR 1 line: load `/`, see the empty state and the sidebar; New chat; send `hello`; see the user bubble, the assistant text, the composer mute while in flight; reload and see the same; send twenty more messages and confirm the thread scrolls inside its column with the composer docked; resize to 390px, open Chats, pick a row, confirm the overlay closes and there is no horizontal scroll. Screenshots into `.superpowers/sdd/2026-10-23-ui-pass-1-shell/` for the PR body. If `web/AGENTS.md` shows modified afterwards, leave it.

---

## Self-review

- **Spec coverage.** §3.1 tokens: Task 1. Font and dark removal: Tasks 1, 7. §3.2 shell, sidebar, root empty state, identity line, chat header, message rows, composer, error line, scroll: Tasks 2 to 6. §3.3 files: all named. §3.4 narrow widths: Task 4 and the gate. §6 verification: Tasks 5 to 7. §7 execution shape and model split: header. §9 D82: Task 7.
- **Placeholders.** None. Every code step carries the file.
- **Type consistency.** `ConversationRow { id; title: string | null }` is produced by Task 3 and consumed by Task 5's map. `Composer({ disabled, onSend })` is produced by Task 2 and consumed by Task 6. `Shell({ sidebar, children })` by Task 4, consumed by Task 5. `newConversation(): Promise<void>` by Task 3, consumed by Tasks 3 and 5.
- **Review focus.** Items 1, 3, 4, 5 have tests in Tasks 3, 6, 4, 6. Item 2 is the browser gate plus Task 1's `overflow: hidden` assertion.
- **Deviations from the spec, all recorded as rulings:** P1-R1 radius names, P1-R2 no custom `.data` utility, P1-R3 toggle placement, and the branch name.
