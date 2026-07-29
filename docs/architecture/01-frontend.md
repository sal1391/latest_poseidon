# 01 — Frontend: React Chat Application

Scope: UI architecture only. Visual identity is intentionally replaceable (section 6).

## 1. Stack

- React 18 + TypeScript + Vite. No SSR framework — this is an authenticated app, not a public site.
- State: **Zustand** store for chat/session state; **TanStack Query** for server data
  (conversation list, dimension values). No Redux.
- Auth: `@auth0/auth0-react` (Authorization Code + PKCE). Tokens held in memory by the SDK —
  never in `localStorage` (doc 05). The auth layer is a thin seam: under SPCS ingress identity
  (doc 05 §2) the SPA skips client-side login entirely — the platform authenticated the user
  before the first byte — and `GET /api/me` still populates the same session state.
- Streaming: native `EventSource`/`fetch` SSE reader against the backend (`text/event-stream`).

Decision D9: Vite SPA over Next.js — no SEO/SSR requirement; smallest operational surface on EC2.

## 2. Module layout (presentation isolated from logic)

```
frontend/src/
  app/                 # bootstrap: providers (Auth0, QueryClient, Theme), router
  api/                 # typed API client, SSE reader, DTO types (mirrors backend schemas)
  state/               # Zustand stores: chatStore (turns, streaming), sessionStore (mode, user)
  features/
    chat/              # ChatScreen: composes conversation, composer, mode chips (logic here)
    conversations/     # sidebar list, resume, new-chat (logic here)
    auth/              # login gate, role guard, logout
  ui/                  # PRESENTATION ONLY — dumb components, no imports from features/ or api/
    primitives/        # Button, Chip, Card, Spinner, Markdown, Table
    message-parts/     # one renderer per message-part kind (see section 4)
    layout/            # AppShell, Sidebar, ChatColumn
  theme/               # tokens.css (CSS custom properties), ThemeProvider, theme presets
```

Rules that make restyling cheap:

1. `ui/` components receive typed props and theme tokens only. They never fetch, never touch
   stores, never know about Auth0 or SSE. Replacing the entire `ui/` directory (or adding a
   second theme preset) is a legal operation that cannot break behavior.
2. All colors, spacing, radii, type, and motion come from CSS custom properties defined in
   `theme/tokens.css`. Components reference semantic tokens (`--surface`, `--ink`, `--accent`),
   never raw hex values.
3. `features/` own behavior and pass data down; `ui/` renders it. The dependency direction is
   one-way: `features → ui`, never the reverse.

## 3. Screen anatomy and the three flow entries

```
+--------------------------------------------------------------+
| Sidebar                 | Chat column                        |
|  [ New chat ]           |  message stream (scroll)           |
|  Conversations          |  [assistant] Ask about your data,  |
|   - Maersk brief        |   or pick a flow:                  |
|   - Prospect: OceanX    |   ( Existing customer )            |
|   - Top GP: Singapore   |   ( New customer prospect )        |
|  [user menu / settings] |  [ Skills v ]                      |
|                         |  composer: [ text input      ][>] |
+--------------------------------------------------------------+
```

- A new conversation opens ready for **default chat**: the composer is live immediately, and an
  assistant opener carries a `chips` part offering the two bubbles — **Existing customer** and
  **New customer prospect** — as optional entries, not a required gate. Typing a question
  ("what are my top GP customers for Port of Singapore in April 2026") simply starts the
  default flow.
- **Skills picker (default flow):** a `Skills` affordance on the composer opens a preset list of
  the registered skills (metric query, web research, the two briefs) with a one-line description
  and an example prompt each; selecting one inserts a starter template into the composer. It is
  discovery UX only — the backend router remains the decider.
- Bubble entries drive the next affordance:
  - Existing customer → a `customer_picker` part: type-ahead against
    `GET /api/dimensions/customers?q=` (served from the data layer's customer dimension). Free
    text is also accepted; the backend fuzzy-resolves it (doc 02 §5).
  - New customer prospect → plain text entry of the company name.
- Chips remain visible but disabled after selection (conversation history stays truthful).
- **Flow shapes entry only.** After the entry deliverable (brief or first answer), the composer
  is identical in all three flows: internal data questions, external research questions, and
  pivots between them are all legal, with carried context (active customer, port, period) shown
  as removable context tags above the composer (server-owned state, doc 02 §5).
- Mode can also be inferred mid-conversation by the backend parser; the UI treats mode as
  server-owned state and merely reflects it (badge in the header).

## 4. Message model: typed parts + renderer registry

A chat message is `{ id, role, parts: MessagePart[] }`. The backend emits structured parts; the
frontend maps `part.kind` to a renderer via a registry — adding or restyling a visualization is a
new renderer, never a change to chat logic.

| `kind` | Payload | Rendered as |
|--------|---------|-------------|
| `text` | markdown string | streamed markdown |
| `chips` | options[] | selectable chip row (mode selection, clarifications) |
| `customer_picker` | none | type-ahead input part |
| `metric_grid` | periods, metrics[] | the 12-metric card grid (Volume, GP, Margin, Win rate, Won/Inquiries/Lost × prior-year/YTD) |
| `table` | columns, rows | top-5 ports and similar tabular results |
| `phase_section` | phase, markdown | one agent phase (Contextualizer / Researcher / Strategist) as an expandable section |
| `tool_event` | tool, server, status, label | a visible step line in the transcript — e.g. "Calling Perplexity — marine news search…" → "✓ Perplexity — 3 sources"; updated in place as status changes |
| `artifact` | name, url, mime | download card (PDF brief from S3 pre-signed URL) — assembled by the chat emitter from the skill result's `ArtifactRef` field (doc 02); skills never emit this part directly |
| `proof` | lines[] | collapsible provenance block (doc 06) — assembled by the chat emitter from `SkillResult.proof` (a field per doc 02); skills never emit this part directly |
| `error` | code, message, hint | inline error card with recovery action |

Tool-call visibility is **verbose by design**: every external call and skill dispatch surfaces
as a `tool_event` step (doc 03 §3 emits them), so the user watches the work happen instead of a
spinner. Steps persist in the stored transcript — the history stays truthful about what ran.

`MessageRenderer` looks up `registry[part.kind]` and falls back to a safe raw-JSON renderer for
unknown kinds (forward compatibility while backend evolves).

## 5. Streaming protocol (SSE)

**Request.** `POST /api/conversations/{id}/messages` takes `{text, client_turn_key}`. The client
generates `client_turn_key` (UUID) once per send and reuses it verbatim on any retry of that same
send, so a retried POST attaches to the existing turn instead of creating a second one (server-side
uniqueness on `(user_sub, client_turn_key)`, doc 06 §1).

**Response envelope.** The response is `text/event-stream`. Every event's `data` JSON carries the
same envelope — `turn_id`, `message_id`, `event_seq` — alongside that event's own fields, and every
SSE frame also carries an `id: <event_seq>` line. `event_seq` is monotonic within a turn starting at
1. An event read in isolation therefore states which turn and which message it belongs to and where
it sits in the stream; nothing depends on arrival position.

| event | data (in addition to the envelope) | UI effect |
|-------|------------------------------------|-----------|
| `accepted` | `{turn_index}` | append pending assistant message (ids come from the envelope) |
| `phase` | `{phase, status: start\|done}` | phase progress indicator on the message |
| `tool` | `{tool_seq, tool, server, status: start\|done\|error, label}` | append/update a `tool_event` step line (explicit, human-readable tool visibility); `tool_seq` matches the `tool_calls` row (doc 06 §1) |
| `part` | `{kind, payload}` | append/replace a structured part |
| `token` | `{text}` | append to the currently streaming `text`/`phase_section` part |
| `done` | `{usage}` | finalize message, refresh conversation list title |
| `error` | `{code, message}` | render `error` part; composer re-enabled |

Decision D26: every event is self-addressed (envelope + `id:` line) and turn creation is idempotent
(`client_turn_key`) — replay and crash recovery require an event to mean the same thing whenever it
is read, not only in the order it first arrived.

Client rules:

1. The reducer keys message state on `message_id` (from the envelope), never on stream order.
2. It records the highest `event_seq` applied per message and discards any event at or below it —
   valid because `event_seq` is monotonic within the turn, so each message sees a strictly
   increasing subsequence; delivery is treated as at-least-once, and a re-sent frame is a no-op
   rather than a duplicated part.
3. On connection drop it calls `GET /api/turns/{turn_id}` to reconcile from the run log (doc 06)
   rather than replaying the model.

Progressive display from today's app is preserved: each agent phase appears as soon as it
completes, while later phases still run.

## 6. Theming contract (the replaceable skin)

`theme/tokens.css` defines the entire visual vocabulary as semantic tokens:

- Color: `--surface`, `--surface-raised`, `--ink`, `--ink-muted`, `--accent`, `--accent-ink`,
  `--positive`, `--negative`, `--border`.
- Type: `--font-display`, `--font-body`, `--font-data` (tabular numerals for metric cards),
  plus a 5-step size scale.
- Shape/motion: `--radius-s/m/l`, `--shadow-1/2`, `--motion-fast/slow` (respect
  `prefers-reduced-motion`).

Starter preset ("Trident"): carries the existing brand equity — royal blue `#4169E1` as
`--accent` on a light surface, the trident mark, `--font-data` with tabular figures for the
metric grid. This preset is explicitly a first pass: because every component consumes tokens
only, a full re-skin is a new `tokens.css` + optional `ui/` swaps, zero logic changes.

Quality floor (non-negotiable regardless of skin): keyboard operability and visible focus,
`aria-live="polite"` on the streaming message region, reduced-motion compliance, responsive down
to ~360 px width.

## 7. State model

- `sessionStore`: user profile/roles (from the identity provider), active conversation id, mode.
- `chatStore`: messages per conversation, streaming buffer, pending state, per-message feedback
  state; hydrated from `GET /api/conversations/{id}/messages` (cursor-paginated) on open/resume.
- TanStack Query keys: `['conversations']`, `['dimension','customers',q]`, `['settings']`,
  `['memory']`. Mutations invalidate `['conversations']` on `done` (title may have been
  generated by the utility tier, doc 03).

## 8. Feedback capture

Every assistant message renders thumbs up / thumbs down in its hover/footer chrome (not a
message part — feedback is metadata about a message, stored separately, doc 06 §7).

- Thumbs up → one call, `POST /api/messages/{id}/feedback {verdict: "up"}`; icon fills.
- Thumbs down → an inline "What went wrong?" free-text prompt appears under the message;
  submitting sends `{verdict: "down", comment}`. The comment is optional — dismissing still
  records the verdict.
- One verdict per user per message; clicking again amends it (idempotent upsert).
- The verdict is linked server-side to the message and its run-log row, feeding the
  router-decision test harvest (doc 06 §7). The UI promises nothing automatic — copy reads
  "Thanks — this helps us tune Poseidon", not "we will fix this".

## 9. Settings surface

Reached from the user menu. Two user-owned documents, both injected into every turn's prompts
(doc 03 §3), both plainly editable:

- **My instructions** — the user's personal system instruction (free-text, e.g. "I cover the
  Singapore book; always show GP in USD k"). `GET/PUT /api/me/settings`.
- **My memory** — what Poseidon has learned about the user, stored as typed attributed entries
  (doc 05 §5). Rendered as a reviewable list: each entry shows its statement, its type, and the
  conversation and date it came from, and can be edited or deleted individually — with a
  character-budget meter for the rendered form (size cap enforced server-side) and a version list
  (restore any prior version). `GET/PUT /api/me/memory`, `GET /api/me/memory/versions`.

Copy states clearly when each was last updated and by whom ("Updated by Poseidon after your
conversation on …" vs "Edited by you"). Editing never blocks chat — saves are optimistic with
rollback on failure.

## 10. Error and empty states

- Empty conversation list: single call to action — "Start a new chat" with the two mode chips
  explained in one sentence each.
- Backend/RFC-7807 errors surface as `error` parts with the `detail` text and a retry affordance;
  auth errors (401/403) route to the login gate with the reason stated plainly.
- A turn that fails mid-stream keeps its completed parts (they came from deterministic tools) and
  marks only the failed phase — matching the run-log truth.

## 11. Frontend tests

- Vitest + React Testing Library: renderer registry (every `kind` renders, including
  `tool_event` status transitions; unknown kind falls back safely), chip → mode transition,
  default-flow composer with skills picker, feedback interaction (up, down + comment, amend),
  settings/memory editors (size-cap meter, version restore), SSE reducer (event stream → store
  state, `tool` event in-place updates, envelope keying by `message_id`, `event_seq` replay
  dropped as a no-op, retry with the same `client_turn_key` yielding one turn, reconnect
  reconciliation).
- MSW handlers for every API route the UI calls (contract-first; doubles as the mock backend for
  Phase 1 of the build plan, doc 08).
- Playwright smoke: login-stubbed run through all three flows against the mock backend —
  default data Q&A with a research pivot, and both bubbles.
