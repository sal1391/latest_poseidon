# Phase 12 — Feedback Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Real feedback: thumbs verdicts persisted per user per message with run-log linkage, thumbs-down comments harvested first into router candidates, verdict-rate roll-ups, and the long-parked accessibility pass over the chat frontend.

**Architecture:** Migration 0006 creates `message_feedback` per doc 06 §7's DDL under the same D28 discipline (RLS + FORCE + poseidon_app grants; the FK cascade from `messages` deletes feedback with the conversation — RI actions bypass RLS by design, the P10-established mechanism). A real `FeedbackStore` replaces P10's honest `FeedbackStubStore` behind the existing route contracts, adding the `run_id` join (from `messages.turn_id`) and RLS scoping. The frontend wires the existing thumbs UI live with a "what went wrong" prompt and amend flow, and withholds thumbs from non-turn messages (the opener). The harvest exporter gains thumbs-down-first priority with full run context; a verdict roll-up script joins feedback → turn_run → llm_calls by skill/role/prompt-version. The a11y pass and the two routed chat-UX gaps (older-messages pagination consumption, load-more busy state) close out the frontend.

**Tech Stack:** Alembic (hand-written), SQLAlchemy Core via `rls_transaction`, FastAPI, React/zustand/vitest/MSW, Playwright evidence.

## Global Constraints

- **Schema (doc 06 §7, verbatim DDL in Task 1):** `message_feedback` (id uuid PK UUIDv7, message_id uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE, run_id uuid NOT NULL REFERENCES turn_run(id), user_sub text NOT NULL, verdict text CHECK (verdict IN ('up','down')), comment text, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (message_id, user_sub)). One verdict per user per message; **upsert amends** (verdict and comment both replaceable; `created_at` keeps first-write time, add `updated_at` — a deliberate additive column, documented).
- **D28:** every `message_feedback` statement runs inside `rls_transaction(engine, user_sub, app_role=settings.database_app_role)`; RLS policies USING/WITH CHECK on `current_setting('app.user_sub', true)`; FORCE; poseidon_app gets SELECT/INSERT/UPDATE (no DELETE — removal happens only via the messages FK cascade, which as an RI action bypasses RLS by design); poseidon_admin gets SELECT (harvest/roll-up read across users).
- **Route contracts preserved:** `POST /api/messages/{mid}/feedback` (idempotent upsert, 204) and `GET /api/messages/{mid}/feedback` keep their existing shapes and the P10 existence gate (RLS-filtered visibility → 404 on unknown/foreign mid). NEW pin: feedback on a message with NULL `turn_id` (the opener) → 422 RFC-7807 with a pinned code (`feedback_not_applicable`) — doc 06 §7 requires the run-log join, and the a11y carry list already says "no thumbs on the opener".
- **Replay rows:** a replayed assistant message shares the ORIGINAL `turn_id` — feedback on it links to the original run. No special-casing.
- **Harvest priority (doc 06 §7 / D25):** `export_router_cases.py` exports thumbs-down rows FIRST (ordered before unreviewed turns), each candidate carrying the full run context — question, parsed, the turn's tool and LLM calls (names/roles/status, NEVER args or prompt hashes — the P11 exclusion rule holds), answer summary — and the user's comment. Candidate schema stays `question`/`expected: TODO-human-review` per the eb67500 ruling, extended with a `feedback:` block (verdict, comment, source message id as comments).
- **Verdict roll-up:** `scripts/feedback_rollup.py [--by skill|role|prompt_version] [--since]` → JSON lines `{group, up, down, down_rate}`; skill attribution from the turn's dispatch record (read how `turn_run.parsed`/`tool_calls` record the routed skill — pin the actual column at Task 3 dispatch after reading; the plan deliberately defers that one lookup to implementation with a disclosure requirement). Runs under the same operator posture as P11's scripts.
- **A11y pass (the parked carry list, verbatim items):** thread-wide `aria-live="polite"` must NOT re-announce every token — use a status region announcing turn lifecycle (thinking → done) instead; SkillsPicker gets outside-click close, focus management, and `aria-haspopup`; no thumbs on the opener greeting (enforced by the 422 pin + UI withholding). Keyboard focus visible on all new controls.
- **Chat-UX gaps routed into this phase (from the P10/P11 carry map):** a "Load earlier messages" control consuming `getMessages`' `next_cursor` (the unconsumed page currently hides the LATEST messages of >200-message conversations — fix the consumption so the newest page loads first and earlier history pages in on demand; if that requires flipping the backend's page order, STOP: that is a plan conflict to escalate, not an implementer choice); the `loadingMoreConversations` flag wired to the Sidebar control (disable + busy state). NOT in scope: titling brief-flow conversations (pile — needs Carlos's product nod).
- **ENVIRONMENT:** withhold PERPLEXITY_API_KEY on every backend run (`env -u PERPLEXITY_API_KEY`); zero live calls; Windows venv `backend/.venv/Scripts/python.exe`; pg needs `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon` (compose at 0005 → this phase applies 0006); no `-q` stacking.
- ASCII .py; deterministic; docstrings WHY; ruff clean on touched; conventional commits on `phase-3-8-overnight` + trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; NEVER push; TDD RED-first with genuine evidence.
- Baselines at phase start: re-pin at T1 dispatch from P11-close values (offline 1590/109, pg 175/1, vitest 88, lints clean) adjusted for any P11 final-review wave.
- Sanctioned modifications to closed files: `api/live_chat.py` (feedback routes swap stub→store + the 422 pin), `core/chat/history.py` NOT touched (feedback lives in its own store module); `poseidon/scripts/export_router_cases.py` (the thumbs-down-first extension — reopened with its own review); frontend `features/*` + `state/chatStore.ts` + `api/client.ts` (thumbs live + a11y + the two UX gaps); `mocks/handlers.ts`. Everything else untouched.

## File Map

```
backend/migrations/versions/0006_message_feedback.py   # NEW: table + RLS + FORCE + grants (+updated_at)
backend/poseidon/core/chat/feedback.py                 # NEW: FeedbackStore (upsert/get, run_id resolution, RLS-wrapped)
backend/poseidon/api/live_chat.py                      # feedback routes: stub -> FeedbackStore; 422 feedback_not_applicable pin
backend/poseidon/scripts/export_router_cases.py        # thumbs-down-first + feedback block + run context
backend/poseidon/scripts/feedback_rollup.py            # NEW: verdict rates by skill/role/prompt_version
backend/tests/test_feedback_store.py                   # NEW (pg): store + RLS four-pattern + upsert-amend + 422 + cascade
backend/tests/test_harvest_cost.py                     # extended: thumbs-down-first ordering + feedback block
frontend/src/features/feedback/*                       # thumbs live, what-went-wrong prompt, amend flow
frontend/src/features/conversations/Sidebar.tsx        # loadingMoreConversations wiring
frontend/src/features/chat/* (thread view)             # Load-earlier control; aria-live status region; SkillsPicker a11y
frontend/src/state/chatStore.ts + api/client.ts        # feedback actions; older-messages pagination consumption
```

---

### Task 1: Migration 0006 + FeedbackStore + route swap

**Files:** create `migrations/versions/0006_message_feedback.py`, `core/chat/feedback.py`, `tests/test_feedback_store.py`; modify `api/live_chat.py` (routes swap + 422 pin); extend `tests/test_migrations.py` to 0006.

**Interfaces produced:** `FeedbackStore(engine, app_role)` with `for_user(sub) -> UserFeedback`; `UserFeedback.upsert(message_id, verdict, comment) -> None` (raises `LookupError` on invisible message → route 404; raises `FeedbackNotApplicable` on NULL turn_id → route 422 pinned; resolves `run_id` from `messages.turn_id` inside the same transaction); `UserFeedback.get(message_id) -> dict | None` (the route's existing GET shape).

- [ ] **Step 1 (RED):** pg tests: DDL/catalog assertions (RLS+FORCE, policies, grants incl. NO app DELETE, admin SELECT); upsert-amend (up→down flips verdict, comment replaced, `created_at` stable, `updated_at` moves, still ONE row via the unique key); two-user isolation (same message, two subs → two rows; each GET sees own); invisible message → LookupError/404; NULL-turn_id message (the opener) → FeedbackNotApplicable/422 byte-pinned; conversation delete cascades feedback rows (count 0 after DELETE — proves the RI-bypass mechanism works for cascade); run_id lands equal to the message's turn_id.
- [ ] **Step 2:** RED run. Capture.
- [ ] **Step 3:** Implement migration (house style, Postgres-only guard, 0005→0006) + store + route swap; delete `FeedbackStubStore` and its honest-stub docstring (its contract is now real; port any stub tests that still assert route shapes).
- [ ] **Step 4:** Apply 0006 to compose; GREEN; full offline+pg; ruff. **Commit** — `feat(feedback): persistent message verdicts with run-log linkage`

### Task 2: Frontend — thumbs live + what-went-wrong + amend

**Files:** `frontend/src/features/feedback/*`, `state/chatStore.ts`, `api/client.ts`, `mocks/handlers.ts` + vitest.

- [ ] **Step 1 (RED):** vitest: thumbs render ONLY on turn-backed assistant messages (opener shows none); up-click POSTs and reflects state; down-click opens the comment prompt, submit POSTs verdict+comment; re-vote amends (UI reflects the flip); GET hydrates existing verdicts on conversation open; 422 from the API surfaces as a quiet no-op (defensive — the UI should never offer thumbs where 422 is possible, pin both layers); MSW handlers updated to the real shapes.
- [ ] **Step 2:** RED. Capture. **Step 3:** Implement thin (the P1-era thumbs UI exists — wire it; do not restyle beyond the a11y task's items). **Step 4:** vitest + tsc + oxlint GREEN. **Commit** — `feat(feedback): live thumbs with amendable what-went-wrong comments`

### Task 3: Harvest priority + verdict roll-up

**Files:** `poseidon/scripts/export_router_cases.py` (extension), `poseidon/scripts/feedback_rollup.py` (new), `tests/test_harvest_cost.py` (extended).

- [ ] **Step 1 (RED):** pg tests: seeded turns with mixed feedback — export orders thumbs-down candidates FIRST, each carrying the `feedback:` block (verdict, comment) + run context, still excluding memory_update/redacted, args/prompt-hash exclusion re-asserted on the feedback path; roll-up per dimension with hand-computed rates (up/down/down_rate); skill attribution — READ the dispatch record's actual location first (turn_run.parsed vs tool_calls), disclose the finding, pin the chosen source in the test.
- [ ] **Step 2:** RED. **Step 3:** Implement (operator posture docstrings; `--since` discipline). **Step 4:** GREEN; doc-08-style validation — export one real thumbs-down from the compose db, YAML verbatim in the report. **Commit** — `feat(feedback): thumbs-down-first harvest and verdict rate roll-up`

### Task 4: A11y pass + routed chat-UX gaps

**Files:** `frontend/src/features/chat/*`, `features/conversations/Sidebar.tsx`, `state/chatStore.ts`, `api/client.ts` + vitest.

- [ ] **Step 1 (RED):** vitest: status region announces turn lifecycle (thinking→done) once per transition, NOT per token (assert the aria-live node's text changes exactly twice across a streamed turn); SkillsPicker closes on outside click + returns focus to its trigger + has `aria-haspopup`; visible focus styles on thumbs/load-more/load-earlier (class-level assertions); "Load earlier" appears only when messages `next_cursor` is non-null, fetches the next page, PREPENDS older messages preserving scroll anchor (test the reducer's ordering), in-flight guard (the P10 lesson — deferred MSW double-click test); Sidebar load-more disabled + busy while `loadingMoreConversations`.
- [ ] **Step 2:** RED. **Step 3:** Implement. NOTE the page-order caution from Global Constraints: if consuming `next_cursor` newest-first requires a backend ordering change, STOP and report — that is a plan conflict, not an implementer choice.
- [ ] **Step 4:** vitest + tsc + oxlint; backend suites once (expect unchanged); E2E evidence: Playwright — thumbs round-trip on localhost, the announcer behavior, load-earlier on a >200-message seeded conversation (seed via a quick script through the API if none exists; disclose). **Commit** — `feat(feedback): accessibility pass with older-message paging and busy states`

---

## Phase Gate (human validation)

1. Suites green (pg now covers feedback RLS + cascade + 422; vitest covers thumbs/a11y/paging).
2. localhost:5173: thumb a real answer up, flip it down with a comment, reload — the verdict survives; the opener has no thumbs; a screen reader (or the aria-live DOM) announces turn lifecycle without per-token spam.
3. `feedback_rollup.py --by skill` and the thumbs-down-first export against your real usage (tight `--since`).
4. Two-browser act-as: verdicts are per-user (alice's thumb doesn't appear for bob).

## Self-Review Notes

- Doc-08 P12 coverage: message_feedback + RLS ✓ (T1), idempotent upsert ✓ (T1), thumbs + what-went-wrong wired live ✓ (T2), harvest extension thumbs-down-first with run context + comment ✓ (T3), verdict roll-up ✓ (T3). Validation lines: up/down/amend round-trips persist + one-verdict-per-user (T1/T2 tests + gate 2), thumbs-down-with-comment exports into a candidate carrying run context (T3 step 4).
- Carry folds: the full a11y list ✓ (T4), messages next_cursor consumption ✓ (T4, with the escalation tripwire), loadingMoreConversations wiring ✓ (T4). Left in the pile deliberately: brief-flow titling (product nod), session-lifecycle story (own phase), P14 fitness items.
- Type consistency: `FeedbackStore(engine, app_role).for_user(sub)` mirrors `HistoryStore`'s established shape; `FeedbackNotApplicable` follows `MalformedCursor`'s typed-exception precedent (route maps to pinned 422); scripts follow P11's operator posture.
- Known risks, named: the 422-vs-404 boundary on feedback (invisible message vs visible-but-turnless) must stay unambiguous — RLS makes foreign messages INVISIBLE (404) while the opener is VISIBLE but turnless (422); the skill-attribution source is deliberately deferred to T3's read-then-disclose; deleting `FeedbackStubStore` touches P10-reviewed code — its tests port, not vanish.
