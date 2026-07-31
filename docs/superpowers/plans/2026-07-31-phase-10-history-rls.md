# Phase 10 — Chat History + RLS Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two in-memory chat stores with Postgres-backed `conversations`/`messages` tables under row-level security, so history survives restarts, users are isolated in the database, and a reopened conversation continues with restored parser state.

**Architecture:** One new migration (0004) creates the doc-05 §6 schema with RLS policies, `FORCE ROW LEVEL SECURITY`, and a non-owner app role. A transaction-scoped identity wrapper (D28) is the only way the app touches these tables. A `HistoryStore` presents interfaces shaped like today's `TranscriptStore`/`ConversationStateStore` so `live_chat.py` and the orchestrator cut over with minimal edits; per-turn SSE folding stays in memory (a `TurnTranscriptBuffer`) and the assistant message is written once at stream end. Conversation titles wire the existing-but-uncalled `title_for` utility at first-turn finalize. The frontend gains cursor pagination, done→title refresh, and a stable per-send `client_turn_key`.

**Tech Stack:** Alembic (hand-written, no ORM), SQLAlchemy Core (sync), psycopg driver via `DATABASE_URL`, FastAPI, React/Vite/zustand/vitest/MSW.

## Global Constraints

- **D28 (verbatim posture):** identity context is transaction-scoped, read with `missing_ok`, and enforced with `FORCE`. The API sets identity as the FIRST statement of every transaction: `SELECT set_config('app.user_sub', :sub, true)` with a bind parameter — `SET LOCAL` is deliberately not used (accepts no bind parameter; would force string interpolation of an identity value into SQL). Policies read `current_setting('app.user_sub', true)` so an unset context is NULL and fails closed (zero rows), never an exception.
- **Schema (doc 05 §6, verbatim DDL in Task 1):** `conversations` (id uuid PK UUIDv7, user_sub text not null, title default 'New chat', mode check in ('existing','prospect','default') default 'default', state jsonb default '{}', created_at/updated_at timestamptz, archived bool default false); `messages` (id uuid PK UUIDv7, conversation_id FK→conversations ON DELETE CASCADE, user_sub text not null denormalized for RLS locality, role check in ('user','assistant','system'), parts jsonb not null, turn_id uuid unconstrained, created_at). Cursor pagination keys: conversations `(updated_at, id)`, messages `(created_at, id)`.
- **The four required RLS tests (doc 05 §4 / doc 06 §5 L1 — all four MUST exist, pg-marked):** (1) two-user isolation; (2) no-context connection sees zero rows on every RLS table; (3) pooled-connection context leak — two sequential checkouts of the SAME pooled connection under different users, second sees none of the first's rows; (4) owner-bypass — a query as the table owner is still filtered (proves FORCE).
- **RLS scope THIS phase:** `conversations` + `messages` only. Run-log tables (`turn_run`/`llm_calls`/`tool_calls`) get RLS in Phase 11 — do not touch them.
- **Explicitly NOT this phase (P11/P12 own them):** conversation deletion + redaction; `GET /api/turns/{id}` reconciliation; upgrading the `duplicate_turn` short-circuit to true replay; persisting feedback (`message_feedback` table is P12 — feedback routes keep working against an honest in-memory stub).
- **UUIDv7:** id generation is a pure-Python RFC 9562 helper (no new dependency); Postgres does not generate ids.
- **Identity:** every table access goes through the D28 wrapper with `user_sub` from `request.state.user` (P9's seam). No route handler or store method takes a raw SQL path around it. X-Dev-User act-as (disabled mode) is the multi-user local test vehicle.
- **ENVIRONMENT:** withhold PERPLEXITY_API_KEY on every suite run (`env -u PERPLEXITY_API_KEY`); zero live/external calls; stub LLM mode (`title_for` runs on the stub RoleClient deterministically). Windows venv `backend/.venv/Scripts/python.exe`; pg suite needs `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon` (compose stack up); do not stack extra `-q` pytest flags.
- ASCII-only .py; frozen dataclasses; byte-pinned wire shapes where asserted; deterministic tests; docstrings explain WHY; ruff clean on touched files (22 pre-existing format-drift files stay untouched); conventional commits on `phase-3-8-overnight` with trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; NEVER push.
- **TDD RED-first** with genuine RED evidence in every task report.
- Baselines at phase start: offline 1514 passed; pg 43 passed + 1 pre-existing skip; vitest 65; tsc/oxlint/ruff clean.
- Sanctioned modifications to earlier-phase files: `api/app.py` (single-engine wiring + store swap), `api/live_chat.py` (route backing + pagination + title emission; `TranscriptStore` deletion), `core/chat/state.py` (`ConversationStateStore` retirement — see Task 3), `api/sse.ts`→`frontend/src/api/sse.ts` + `client.ts` + `chatStore.ts` (Task 4 scope), `infra/docker-compose.yml` ONLY if a migration-run step needs documenting (prefer runbook note). `core/chat/orchestrator.py`: ZERO edits expected (the state-store swap is interface-identical); if an edit proves necessary, disclose it as a judgment call with the reason.
- `core/parsing/*`, skills, LLM loop, MCP layer: untouched.

## File Map

```
backend/poseidon/core/util/uuid7.py            # NEW: RFC 9562 UUIDv7 generator (pure fn, injectable clock)
backend/poseidon/core/db.py                    # NEW: engine factory + rls_transaction(engine, user_sub) D28 wrapper
backend/migrations/versions/0004_chat_history.py  # NEW: tables, indexes, RLS, FORCE, app role + grants
backend/poseidon/core/chat/history.py          # NEW: HistoryStore/UserHistory + slots serializer + DbStateStore + TurnTranscriptBuffer + FeedbackStubStore
backend/poseidon/api/app.py                    # single shared Engine; HistoryStore wiring (replaces both stores)
backend/poseidon/api/live_chat.py              # routes on UserHistory; pagination params; title at first done; TranscriptStore DELETED
backend/poseidon/core/chat/state.py            # ConversationStateStore retired (see Task 3 step 6)
backend/tests/test_uuid7.py                    # NEW
backend/tests/test_rls_policies.py             # NEW: the four required tests + catalog assertions (pg)
backend/tests/test_history_store.py            # NEW: store unit+pg tests (round-trip, cursors, restart survival)
backend/tests/test_history_cutover.py          # NEW: HTTP + orchestrator integration on pg (act-as isolation, carry-after-restart, title)
frontend/src/api/client.ts + types.ts          # paginated list/messages shapes; title on Conversation
frontend/src/api/sse.ts                        # streamTurn(cid, text, clientTurnKey) — key injected, not minted here
frontend/src/state/chatStore.ts                # load-more actions; title refresh on done; key minting per logical send
frontend/src/features/conversations/Sidebar.tsx # load-more button; title updates
frontend/package.json                          # @tanstack/react-query REMOVED (installed, zero imports — YAGNI ruling)
```

---

### Task 1: UUIDv7 + migration 0004 + the D28 wrapper + the four RLS tests

**Files:** create `core/util/uuid7.py`, `core/db.py`, `migrations/versions/0004_chat_history.py`, `tests/test_uuid7.py`, `tests/test_rls_policies.py`; extend `tests/test_migrations.py` (chain reaches 0004).

**Interfaces produced (later tasks rely on these exactly):**
- `uuid7(now_ms: int | None = None) -> uuid.UUID` — version 7, variant RFC; 48-bit big-endian unix-ms timestamp; rest random.
- `build_engine(database_url: str) -> Engine` (thin `create_engine` wrapper, the one place pool config lives).
- `rls_transaction(engine: Engine, user_sub: str)` — `@contextmanager`, yields a `Connection` inside `engine.begin()`, having executed `SELECT set_config('app.user_sub', :sub, true)` as the FIRST statement. Docstring carries D28 and the SET-LOCAL rejection rationale.

- [ ] **Step 1 (RED):** `test_uuid7.py`: version nibble == 7; variant bits 10; `uuid7(now_ms=1)` < `uuid7(now_ms=2)` as int (time-ordered); two calls same `now_ms` differ (randomness); timestamp round-trip (top 48 bits == now_ms). `test_rls_policies.py` (pg-marked): write the four required tests against the not-yet-existing tables via `rls_transaction` + raw SQL inserts/selects, plus catalog tests: `pg_class.relrowsecurity AND relforcerowsecurity` true for both tables; role `poseidon_app` exists with `rolbypassrls = false` and has exactly SELECT/INSERT/UPDATE/DELETE on both tables; policies exist for select/insert/update/delete with the pinned predicate. Cleanup pattern: each test uses unique `user_sub` values (`f"test|{uuid4().hex}"`) and deletes its rows in teardown through `rls_transaction` under the owning sub.
- [ ] **Step 2:** Run — uuid7 tests fail (module missing); pg tests fail (tables missing). Capture RED.
- [ ] **Step 3:** Implement `uuid7.py` (~25 lines, pure), `db.py`, and migration `0004_chat_history.py` (`revision="0004", down_revision="0003"`, Postgres-only like 0003 — no-op elsewhere):

```sql
CREATE TABLE conversations (
  id uuid PRIMARY KEY,
  user_sub text NOT NULL,
  title text NOT NULL DEFAULT 'New chat',
  mode text CHECK (mode IN ('existing','prospect','default')) DEFAULT 'default',
  state jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  archived boolean NOT NULL DEFAULT false
);
CREATE TABLE messages (
  id uuid PRIMARY KEY,
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  user_sub text NOT NULL,
  role text CHECK (role IN ('user','assistant','system')),
  parts jsonb NOT NULL,
  turn_id uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_conversations_user_recency ON conversations (user_sub, updated_at DESC, id DESC);
CREATE INDEX ix_messages_conversation_order ON messages (conversation_id, created_at, id);
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;
CREATE POLICY conversations_owner ON conversations
  USING (user_sub = current_setting('app.user_sub', true))
  WITH CHECK (user_sub = current_setting('app.user_sub', true));
CREATE POLICY messages_owner ON messages
  USING (user_sub = current_setting('app.user_sub', true))
  WITH CHECK (user_sub = current_setting('app.user_sub', true));
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'poseidon_app') THEN
    CREATE ROLE poseidon_app NOLOGIN;
  END IF;
END $$;
GRANT USAGE ON SCHEMA public TO poseidon_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON conversations, messages TO poseidon_app;
```
(one policy per table covering all commands via USING+WITH CHECK is acceptable; four named per-command policies equally acceptable — pick one, assert it in the catalog test. The role is deploy posture: local runtime keeps the configured DSN user, and FORCE makes even that owner-safe — which is exactly what required test 4 proves. Downgrade drops tables + policies but NOT the role — roles are cluster-scoped; leave with a comment.)
- [ ] **Step 4:** Apply to the compose db: `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon backend/.venv/Scripts/python.exe -m alembic upgrade head` (from backend/). Run pg suite → all four RLS tests + catalog tests GREEN; existing 43 unaffected. Run offline suite (migration chain test now expects 0004).
- [ ] **Step 5:** ruff on touched; **Commit** — `feat(history): chat history schema with row-level security and identity transaction wrapper`

### Task 2: HistoryStore — Postgres-backed conversations/messages/state behind today's interfaces

**Files:** create `core/chat/history.py`, `tests/test_history_store.py`.

**Interfaces produced (Task 3 relies on these exactly):**
- `HistoryStore(engine: Engine)` with `for_user(user_sub: str) -> UserHistory`.
- `UserHistory` methods (dict shapes byte-compatible with today's `TranscriptStore` payloads — read `live_chat.py:269-448` first and mirror the keys):
  - `create_conversation(mode: str = "default") -> tuple[dict, dict]` — inserts conversation (uuid7 id, title 'New chat') + the opener assistant message row; returns the same `(conversation, opener)` dict shapes routes serve today.
  - `list_conversations(limit: int = 50, cursor: str | None = None) -> tuple[list[dict], str | None]` — ordered `updated_at DESC, id DESC`; opaque cursor = urlsafe-base64 of `{"u": "<iso updated_at>", "i": "<id>"}`; returns `(items, next_cursor)`, `next_cursor=None` on last page.
  - `get_messages(cid: str, limit: int = 200, cursor: str | None = None) -> tuple[list[dict], str | None] | None` — `None` when the conversation is invisible (absent OR other user's — RLS makes these indistinguishable, which is the point; route 404s either way). Ordered `created_at ASC, id ASC`.
  - `append_user_message(cid: str, message_id: str, text: str, turn_id: str | None) -> None` — inserts the user row, bumps `conversations.updated_at`.
  - `write_assistant_message(cid: str, message: dict, turn_id: str | None) -> None` — single insert at stream end; bumps `updated_at`.
  - `set_title(cid: str, title: str) -> None`.
  - `read_state(cid: str) -> dict` / `write_state(cid: str, state: dict) -> None` — the raw jsonb (`{}` when invisible).
- `TurnTranscriptBuffer` — the per-turn in-memory fold: port `start_assistant_message` / `append_part` / `record_tool_event` / `fold_token` VERBATIM in behavior from `TranscriptStore` (same dict shapes, same part-folding rules; it is the same code with the dict-of-conversations removed). One buffer per streaming turn, discarded after `write_assistant_message`.
- `DbStateStore(user_history: UserHistory)` — implements EXACTLY today's `ConversationStateStore` interface (`get(cid) -> ConversationSlots`, `put(cid, slots)`, `next_turn_index(cid) -> int`, `get_brief_done(cid) -> bool`, `set_brief_done(cid, value)`) backed by `conversations.state` jsonb `{"slots": {...}, "brief_done": bool, "turn_index": int}`. `next_turn_index` increments atomically: `UPDATE conversations SET state = jsonb_set(state, '{turn_index}', ...) ... RETURNING` inside one `rls_transaction`. `get` on unseen/invisible cid returns the empty-slots sentinel (today's behavior).
- Slots serializer: `slots_to_json(slots: ConversationSlots) -> dict` / `slots_from_json(raw: dict) -> ConversationSlots` — dates as ISO strings, `pass_through` as list of `[key, value]` pairs, unknown keys IGNORED on read (forward compatibility), missing keys default like the dataclass.
- `FeedbackStubStore` — today's `_feedback` dict + lock, extracted verbatim; docstring states plainly: in-memory until Phase 12's `message_feedback` table; restart loses feedback; routes keep their contract meanwhile.

- [ ] **Step 1 (RED):** `test_history_store.py`: serializer round-trip (populated + empty + date-bearing + pass_through slots; unknown-key tolerance); uuid7 ids on created rows; cursor pagination — create 7 conversations, page size 3 → 3/3/1 with stable order and None terminal cursor; same for messages; `get_messages` None for other-user cid (two `for_user` facades); restart survival — store A writes, NEW `HistoryStore` on a NEW engine reads the same rows; `DbStateStore` round-trip through real jsonb; `next_turn_index` monotonic across store instances; `TurnTranscriptBuffer` behavior tests ported from existing TranscriptStore tests (find them in the existing suite; port assertions, do not weaken).
- [ ] **Step 2:** RED run (module missing). Capture.
- [ ] **Step 3:** Implement `history.py`. Every SQL statement runs inside `rls_transaction(self._engine, self._user_sub)`. No statement anywhere in the module bypasses the wrapper.
- [ ] **Step 4:** GREEN (offline serializer/buffer tests + pg store tests); existing suites untouched.
- [ ] **Step 5:** ruff; **Commit** — `feat(history): postgres-backed conversation store with state snapshot round-trip`

### Task 3: Cutover — live_chat + app wiring on HistoryStore, titles at first done

**Files:** modify `api/app.py`, `api/live_chat.py`, `core/chat/state.py`; create `tests/test_history_cutover.py`.

**Interfaces consumed:** Task 2's exactly. **Produced for Task 4:** `GET /api/conversations?limit=&cursor=` → `{"items": [...], "next_cursor": str|null}` (each item now includes `title`, `mode`, `updated_at`); `GET /api/conversations/{cid}/messages?limit=&cursor=` → same envelope; the `done` SSE event payload gains an additive `"title": str|null` field (null except the event that first sets it).

- [ ] **Step 1 (RED):** `test_history_cutover.py` (pg-marked, httpx against `create_app()`): act-as isolation — `X-Dev-User: alice` creates + sends; `X-Dev-User: bob` lists (empty) and GETs alice's cid (404); alice relists after simulated restart (fresh app instance, same DATABASE_URL) and sees her conversation with messages; continue-with-carry-after-restart — turn 1 sets a customer slot (real orchestrator, stub LLM, synthetic data), fresh app instance, turn 2 sends a follow-up that only parses correctly if slots were restored from `conversations.state` (assert the answer targets the carried customer); title — after turn 1's `done`, `conversations.title != 'New chat'` (stub `title_for` is deterministic) and the done frame carried it; pagination envelope shapes byte-pinned.
- [ ] **Step 2:** RED run. Capture.
- [ ] **Step 3:** `app.py`: build ONE `Engine` via `db.build_engine(settings.database_url)`; pass it to `RunLogWriter` AND `HistoryStore` (delete the second `create_engine` — one pool per process; health.py's throwaway probe engine stays as-is). `app.state.history_store = HistoryStore(engine)`; delete `transcript_store`/`conversation_state_store` wiring.
- [ ] **Step 4:** `live_chat.py`: routes resolve `user_history = request.app.state.history_store.for_user(request.state.user.sub)` per request. List/messages gain the pagination params + envelope. Send route: `append_user_message` at accept; `TurnTranscriptBuffer` folds frames exactly where `_record_transcript_frame` folds today; on terminal frame, `write_assistant_message`; orchestrator receives `DbStateStore(user_history)` — signature-identical, zero orchestrator edits. After a SUCCESSFUL first turn (`turn_index == 1`, status ok), call `title_for(question, role_client, prompt_registry)`; on `""` fall back to `question[:60]`; `set_title`, and include it in that turn's `done` payload (additive field only — every existing done assertion must pass unchanged). Feedback routes → `FeedbackStubStore`. Delete `TranscriptStore` (class + tests move/port per Task 2 step 1).
- [ ] **Step 5:** `core/chat/state.py`: retire `ConversationStateStore` — if nothing imports it after cutover, delete the class and migrate its docstring's D19 rationale to `DbStateStore`; if mock-chat or tests still import it, KEEP it with a docstring line "live path uses DbStateStore since Phase 10" and disclose which callers remain.
- [ ] **Step 6:** Full suites: offline, pg (grown), vitest untouched (backend-only task), ruff. Existing done-event assertions unchanged (additive-field proof).
- [ ] **Step 7:** **Commit** — `feat(history): live chat cutover to persistent history with turn-one titles`

### Task 4: Frontend — pagination, title refresh, stable turn keys, react-query removal

**Files:** modify `frontend/src/api/{client.ts,types.ts,sse.ts}`, `frontend/src/state/chatStore.ts`, `frontend/src/features/conversations/Sidebar.tsx`, `frontend/src/mocks/handlers.ts`, `frontend/package.json`; vitest files alongside.

**Interfaces consumed:** Task 3's envelopes and the done-event `title` field.

- [ ] **Step 1 (RED):** vitest: `listConversations` returns `{items, next_cursor}` and store appends on `loadMore()` (MSW pages 2); done event with `title` updates the matching conversation in the store (sidebar re-renders the new title); `client_turn_key` stability — `sendMessage` mints ONE key per logical send and passes it to `streamTurn`; a retry of the SAME logical send (the existing retry path) reuses the key; a NEW send mints a new one (sensitivity: assert two sends differ, retry matches); types compile (`tsc`).
- [ ] **Step 2:** RED run. Capture.
- [ ] **Step 3:** Implement: `types.ts` `Conversation {id, title, mode?, updated_at?}` + `Page<T> {items, next_cursor}`; `client.ts` `listConversations(cursor?)`/`getMessages(cid, cursor?)` return pages; `sse.ts` `streamTurn(cid, text, clientTurnKey, ...)` — key is a required parameter, `crypto.randomUUID()` call REMOVED from sse.ts; `chatStore.ts` mints the key in `sendMessage`, stores `next_cursor`, adds `loadMoreConversations()`, handles done-title; `Sidebar.tsx` load-more button (renders only when `next_cursor` non-null).
- [ ] **Step 4:** Remove `@tanstack/react-query` from package.json + `npm install` (ruling: installed since scaffold, zero imports anywhere — grep-proof it in the report; the store layer is zustand by shipped convention).
- [ ] **Step 5:** vitest + tsc + oxlint GREEN; backend offline + pg once (no backend edits expected — confirm unchanged).
- [ ] **Step 6:** E2E evidence (the phase's visible win): against compose — create a conversation, send a turn, `docker compose -f infra/docker-compose.yml restart backend`, reload localhost:5173 → the conversation and its messages are still there, with its generated title. Capture command output + browser evidence (Playwright if available; disclosed real-socket probes otherwise).
- [ ] **Step 7:** **Commit** — `feat(history): sidebar pagination, title refresh, and stable turn keys`

---

## Phase Gate (human validation)

1. Offline + pg suites green (pg now includes the four doc-05 §4 RLS tests by name).
2. localhost:5173: conversations survive a backend restart; reopening one and asking a carry-over question ("what about February?") answers from restored state; sidebar titles appear after each conversation's first turn.
3. Two-browser act-as check (optional): two profiles with different `X-Dev-User` values see disjoint sidebars.

## Self-Review Notes

- Doc-08 P10 coverage: migrations ✓ (T1), RLS+role+FORCE ✓ (T1), D28 wrapper ✓ (T1), four required tests ✓ (T1 by name), list/resume/continue with cursors ✓ (T2/T3), state snapshot restore into the parser ✓ (T2 serializer + T3 integration test), sidebar real data ✓ (was already real; T4 makes it paginated+titled). Deletion/redaction deliberately absent (P11 per doc 08).
- Carryforwards folded: client_turn_key hoist ✓ (T4), done→title refresh ✓ (T3 backend + T4 frontend), dedupe: the four existing guards remain; true replay stays P11 by the orchestrator's own docstring; react-query dropped ✓ (T4, YAGNI with grep proof).
- Type consistency: `UserHistory` dict shapes mirror today's `TranscriptStore` payloads (T2 reads before porting); `DbStateStore` implements the exact 5-method `ConversationStateStore` interface (orchestrator zero-edit); cursors are one opaque encoding used by both list endpoints; `uuid7()` is the only id mint.
- Known risk, named: T3's title call adds one utility-role LLM invocation per first turn — stub mode makes it deterministic and free; live mode routes through the existing RoleClient seam (no new egress class; D30 unaffected — utility role is an in-provider call, not a tool).
