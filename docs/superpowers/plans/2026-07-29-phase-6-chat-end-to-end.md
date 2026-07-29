# Poseidon Phase 6: Conversational Data Q&A End to End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The default flow live in the chat (doc 08 P6): parse → route → `data_qa.metric_query` → streamed `table`/`metric_grid` parts + proof + verbose tool steps + clarification chips + carry-over follow-ups, with the minimal run-log writer recording every turn from the first end-to-end conversation onward. All in stub LLM mode (no AWS keys); the mock chat stays intact until the live path passes its gates.

**Architecture:** A new `core/chat/` orchestration layer composes the shipped stacks — P4's `parse_turn`, P5's `run_turn`/RoleClient/PromptRegistry, P3's registry/dispatch — behind one `execute_turn` service. An EventSink adapter converts loop events + skill parts into doc-01 §5 SSE envelope events. The run-log writer (doc 06 §1 three tables) records provisional-insert/append/finalize around it. HTTP mounts a live chat router at the same paths as the mock, selected by `CHAT_MODE` (default stays `mock` until the final task flips it after E2E passes). Conversation STATE (ConversationSlots + pass-through) is held server-side in memory keyed by conversation id — doc 08 assigns durable conversations/messages to Phase 10; `turn_run.conversation_id` is a plain uuid, no FK.

**Tech Stack:** Existing backend + frontend. No new deps. Identity: the fixed dev user sub `dev|local` everywhere a `user_sub` is required (Phase 9 replaces it via the IdentityProvider seam).

## Global Constraints

- **Stub LLM mode throughout.** The chat path runs with `llm_mode="stub"`; no test or runtime path may require AWS. The provider registry wiring follows the canonical shapes documented in `roles.py` (P5 M4).
- **The mock chat is untouched and remains the default** until Task 5 flips `CHAT_MODE` to `live` in compose AFTER the E2E suite passes. `mock_chat.py`, its tests, and the frontend contract stay byte-identical through Tasks 1-4. `localhost:5173` must stay demo-able at every commit.
- **SSE contract is doc 01 §5 verbatim**: envelope `turn_id`/`message_id`/`event_seq` on every event + `id: <event_seq>` frame lines; events `accepted{turn_index}` / `tool{tool_seq,tool,server,status,label}` / `part{kind,payload}` / `token{text}` / `done{usage}` / `error{code,message}`; `event_seq` monotonic from 1 per turn; `client_turn_key` idempotency (retry attaches, never duplicates — backed by the doc 06 unique `(user_sub, client_turn_key)`).
- **Run-log schema is doc 06 §1 verbatim** (three tables, checks, uniques, indexes). Write discipline: provisional insert `status='running'` at turn start; children appended as calls return; finalize sets terminal status + `finished_at` + `latency_ms` + token roll-up. The writer NEVER raises into the turn path — every write wrapped; a write failure logs at ERROR and the turn proceeds (TM1 CSV-writer rule). `kind` supports `chat_turn` and `memory_update` now (memory runs arrive P13).
- **Proof/artifact part reconciliation (ADJUDICATED here, closing the P3 carry):** skills keep `proof` (and artifact refs) as FIELDS on their results (doc 02, all shipped code). The CHAT EMITTER converts: `SkillResult.proof` → one `part{kind:"proof", payload:{lines}}` appended after that skill's own parts; an `ArtifactRef` → `part{kind:"artifact", payload:{name,url,mime}}`. Doc 01 §4 gets a one-line clarifying note per row (committed with this plan). Skills and their tests change ZERO.
- **Clarification contract:** a ParsedTurn whose issues contain `customer_ambiguous` produces a `chips` part (options = the candidates) + a short text part, status `clarify` in `turn_run`, and NO skill dispatch for that turn. Clicking a chip sends its value as the next user message (frontend rule — deterministic v1).
- **Pass-through wiring (P4/P5 carry, doc 02 §5 — Phase 6 is the named owner):** when a dispatched skill's result parts contain a ranked/tabular value column for a certified dimension (metric_query's table of CUST_NM/LOC_NM values), the orchestrator repopulates `ConversationSlots.pass_through` with `((label, exact_value), ...)` from that result (capped at 10) via `SlotUpdates` (replace-wholesale, never merge — T1 carry semantics). `render_state_block` must render pass_through when non-empty — if P5's implementation omits it, an ADDITIVE fix to `render_state_block` + test is sanctioned.
- **Parked decisions stay parked:** port-carry asymmetry, carry-granularity-to-month, MODE_SLOT_ALIASES — current behavior is the default; nothing in this phase changes them.
- **Settings wiring (P5 carry):** the orchestrator constructs PromptRegistry from `settings.prompts_dir` (fallback packaged default) and passes `settings.agent_max_iterations` to `run_turn` — both fields finally consumed by shipping code.
- **Stub routing for the DEV chat (bounded, deterministic — NOT an NLU project):** `DevDeterministicRouter` is an `LLMProvider` implementation (registered as the `"stub"` provider in the CHAT wiring only) that scripts itself per-call from the rendered state block's ParsedTurn facts: (a) if the turn's parse carries `customer_ambiguous` the orchestrator never calls it (chips short-circuit); (b) if hints lead with `data_qa.metric_query` AND the parse resolved a period → emit ONE `tool_use` for `data_qa.metric_query` with args from the parse (metric GP default; "top" wording → group_by CUST_NM top 5; resolved port/customer as filters; comparison periods when present), then on the next call `end_turn` with a one-line deterministic narrative naming entity + period; (c) anything else → `end_turn` with the pinned capability message ("I can answer certified metric questions — try a metric, a customer or port, and a period."). It parses the state block it is HANDED (a real provider sees the same text) — no reach-around into orchestrator internals. Unit-tested like any provider.
- Deterministic everywhere; ASCII .py; frozen dataclasses; byte-pinned messages; docstrings explain WHY; ruff clean; conventional commits on `phase-3-8-overnight`; every commit trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Do not modify P2-P5 core modules EXCEPT the sanctioned additive items named above (`render_state_block` pass-through rendering; a `prompt_version`/`prompt_hash` accessor, see T1 interface). Frontend: additive components/store fields only; theme tokens untouched.
- Baseline at plan time: 885 offline passed / 21 env-dependent skips; pg suite 31; frontend 20 vitest. Zero failures is the bar throughout.

## File Map

```
backend/migrations/versions/0003_run_log.py         # turn_run / llm_calls / tool_calls (doc 06 §1)
backend/poseidon/core/runlog.py                     # RunLogWriter (never-raises discipline)
backend/poseidon/core/chat/__init__.py
backend/poseidon/core/chat/state.py                 # ConversationStateStore (in-memory, per-conversation slots)
backend/poseidon/core/chat/events.py                # SseEnvelopeSink: loop events + parts -> doc 01 §5 frames
backend/poseidon/core/chat/dev_router.py            # DevDeterministicRouter (LLMProvider impl)
backend/poseidon/core/chat/orchestrator.py          # execute_turn(...)
backend/poseidon/api/live_chat.py                   # POST /api/conversations/{id}/messages (SSE) + GET /api/skills
backend/poseidon/api/app.py                         # CHAT_MODE mount switch (additive)
backend/poseidon/core/config.py                     # + chat_mode: Literal["mock","live"] = "mock"
backend/poseidon/core/llm/prompts.py                # + prompt_version()/prompt_hash helpers (additive, sanctioned)
backend/tests/test_runlog_writer.py                 # offline shape + pg goldens
backend/tests/test_chat_state_devrouter.py
backend/tests/test_chat_orchestrator.py             # execute_turn offline (fake data client + writer double)
backend/tests/test_live_chat_sse.py                 # endpoint + envelope + idempotency (httpx ASGI)
backend/tests/test_chat_e2e_scripted.py             # THE doc-08 scripted conversation (pg-marked; row inspection)
frontend/src/api/client.ts                          # GET /api/skills (additive)
frontend/src/features/chat/*                        # chips click-to-send; picker on real registry (additive)
infra/docker-compose.yml                            # T5 ONLY: CHAT_MODE=live + backend image rebuild note
docs/architecture/01-frontend.md                    # two one-line reconciliation notes (§4 proof + artifact rows)
```

---

### Task 1: Run-log migration + writer (+ prompt version/hash accessors)

**Files:** `migrations/versions/0003_run_log.py`; `poseidon/core/runlog.py`; `core/llm/prompts.py` (additive); test `test_runlog_writer.py`.

**Interfaces (exact):**

```python
# runlog.py
@dataclass(frozen=True)
class TurnHandle:
    turn_run_id: str        # uuid str
    created: bool           # False when (user_sub, client_turn_key) already existed (client retry)

class RunLogWriter:
    def __init__(self, engine): ...   # sqlalchemy engine; every public method full-wrapped, never raises
    def start_turn(self, *, user_sub: str, conversation_id: str | None, client_turn_key: str | None,
                   turn_index: int | None, question: str | None, mode: str, parsed: dict,
                   kind: str = "chat_turn", trace_id: str | None = None) -> TurnHandle | None: ...
    def append_llm_call(self, *, turn_run_id: str, user_sub: str, seq: int, provider: str, model_id: str,
                        role: str, prompt_version: str, prompt_hash: str, input_tokens: int,
                        output_tokens: int, latency_ms: int | None, status: str, error: dict | None = None) -> None: ...
    def append_tool_call(self, *, turn_run_id: str, user_sub: str, seq: int, tool: str, server: str | None,
                         args: dict, result_digest: dict | None, status: str, latency_ms: int | None,
                         error: dict | None = None) -> None: ...
    def finalize(self, *, turn_run_id: str, status: str, message_id: str | None, answer_summary: str | None,
                 input_tokens: int, output_tokens: int, latency_ms: int, error: dict | None = None) -> None: ...

# prompts.py (additive, sanctioned)
def prompt_version(prompts_dir: Path, name: str) -> str: ...
    # first line "<!-- version: X -->" of the template file -> "X"; absent -> "v0" (documented)
def prompt_hash(rendered: str) -> str: ...   # sha256 hexdigest of the rendered text
```

Migration 0003: doc 06 §1 SQL verbatim (three tables, all checks/uniques/indexes; UUID columns as `UUID` type, jsonb, timestamptz) following 0002's structure incl. the sqlite no-op guard pattern and module-level column lists. `router/system.md` and `utility/title.md` gain a `<!-- version: v1 -->` first line (rendered output UNCHANGED — Jinja comment? NO: an HTML comment passes through Jinja and would land in the prompt. Use `{# version: v1 #}` Jinja comment syntax instead; `prompt_version` parses that form; document why).

Behavior pinned in tests: `start_turn` idempotency — same `(user_sub, client_turn_key)` twice → second returns same `turn_run_id`, `created=False`, NO second row (pg test); writer never raises — a writer built on a broken engine returns `None`/no-ops and logs ERROR (offline test with a raising fake engine, assert log record + no exception); finalize roll-up sums child `llm_calls` tokens (pg); `unique (turn_run_id, seq)` conflict on children logged-not-raised (pg); `kind='memory_update'` accepted with null conversation/message (pg); status check constraints enforced (pg, expect IntegrityError swallowed by wrapper + ERROR log). Offline tests: SQL text generation shapes via a recording fake engine (no sqlite dialect games). Markers: pg per house pattern.

- [ ] Tests FIRST (RED) → migration → writer → GREEN offline + `-m pg` → ruff. **Commit** — `feat(runlog): run-log migration and never-raises writer with idempotent turn start`

---

### Task 2: Conversation state store + DevDeterministicRouter

**Files:** `core/chat/{__init__,state,dev_router}.py`; test `test_chat_state_devrouter.py`.

**Interfaces:**

```python
# state.py
class ConversationStateStore:
    """In-memory per-conversation slots. Phase 10 replaces with persisted state — same surface."""
    def get(self, conversation_id: str) -> ConversationSlots: ...      # default empty slots
    def put(self, conversation_id: str, slots: ConversationSlots) -> None: ...
    def next_turn_index(self, conversation_id: str) -> int: ...        # 1-based, monotonic per conversation
# thread-safety: a plain dict + threading.Lock (uvicorn workers=1 in dev; documented)

# dev_router.py
class DevDeterministicRouter:   # implements LLMProvider protocol
    def invoke(self, *, system, messages, tools, model, params) -> LLMResponse: ...
```

DevDeterministicRouter contract (per Global Constraints bullet; all pinned): reads ONLY its invoke arguments; state facts come from the CONVERSATION STATE block inside `system` (the same text a real model reads) — parse the block with the same deterministic discipline as everything else (regex on the block's pinned line formats from `render_state_block`). Behavior table tested case by case: resolved period + metric_query-leading hints (hint names present in the system prompt's skill lines — NO: hints are not in the system prompt; the ParsedTurn context block carries hints per doc 03 §3 item 5 — parse them from there); "top" in the user message (last user message in `messages`) → group_by CUST_NM limit 5; port filter from resolved port line; comparison → compare args; second invoke (after a toolResult message is present) → end_turn with the deterministic narrative f"Certified answer for {entity_label} — {period_label}." ; no resolved period → the pinned capability message. Tool call ids: "dev-1", "dev-2", ... deterministic.

- [ ] Tests FIRST (RED) → implement → GREEN → ruff. **Commit** — `feat(chat): conversation state store and deterministic dev router`

---

### Task 3: SSE envelope sink + execute_turn orchestrator

**Files:** `core/chat/{events,orchestrator}.py`; `core/llm/prompts.py` additive `render_state_block` pass-through rendering IF absent; test `test_chat_orchestrator.py`.

**Interfaces:**

```python
# events.py
class SseEnvelopeSink:            # implements the P5 EventSink protocol AND accepts part pushes
    def __init__(self, *, turn_id: str, message_id: str, send: Callable[[str], Awaitable[None] | None]): ...
    # translates: llm_call -> (no frame; recorded only), tool_start/tool_done -> `tool` events w/ tool_seq
    #   + human label from SKILL_META, turn_error -> `error` event; plus push_part(kind, payload) -> `part`,
    #   push_token(text) -> `token`, accepted(turn_index), done(usage). Every frame: envelope + id: line;
    #   event_seq monotonic from 1; frames rendered exactly as doc 01 §5 (event:, id:, data: lines).

# orchestrator.py
@dataclass(frozen=True)
class TurnOutcome:
    status: str                   # ok | clarify | error
    message_id: str
    turn_run_id: str | None

def execute_turn(*, conversation_id: str, text: str, client_turn_key: str | None, settings: Settings,
                 registry: SkillRegistry, data: DataClient, state: ConversationStateStore,
                 writer: RunLogWriter | None, role_client: RoleClient, prompt_registry: PromptRegistry,
                 sink: SseEnvelopeSink, reference_date: date) -> TurnOutcome: ...
```

`execute_turn` order (each step pinned): (1) slots = state.get; turn_index = state.next_turn_index; writer.start_turn (parsed=ParsedTurn rendered to dict AFTER step 2 — insert carries `{}` then finalize... NO: keep doc 06 simple — parse FIRST, then start_turn with the parsed dict; accepted event AFTER start_turn so turn_id is real). Precisely: parse_turn → start_turn(parsed=dataclass-to-dict) → sink.accepted(turn_index) → clarify short-circuit OR run_turn → parts emission (skill parts pushed at tool_done time by the sink from the loop's tool_done payloads; proof field → proof part; ArtifactRef → artifact part) → final text as ONE token event then done(usage) → writer.append_* per records → writer.finalize → state.put(new slots incl. pass_through repopulation per Global Constraints). Clarify path: chips part (candidates), text part, finalize status clarify, slots STILL carry-updated (period/port may have resolved). Error path: error event + finalize error + slots unchanged. `answer_summary` = final text capped 500 chars. `message_id`/`turn_run_id` = UUIDv7-ish via uuid4 (uuid7 unavailable in stdlib — document; P10 revisits ids).
Records→rows mapping: LLMRecord → append_llm_call (prompt_version/prompt_hash from T1 helpers — version of `router/system`, hash of the rendered system text; the orchestrator renders/possesses it); ToolRecord → append_tool_call (digest string → `result_digest={"digest": ...}`; server=None).
Tests (offline, fake DataClient + recording writer double + DevDeterministicRouter + a frame-capturing send): the flagship scripted turn (Singapore top-GP) → frame sequence pinned event-by-event (accepted, tool start/done, part table, part proof, token, done) with event_seq 1..N and envelope on every data JSON; carry-over turn ("and for May?") → period replaced, customer carried, state.put proven; ambiguous turn → chips + clarify status + no dispatch; error turn (unknown-skill script via a scripted StubProvider) → error event + finalize error; writer-double call-shape assertions per row contract; pass-through repopulated from the table part (labels+values, capped 10, replace-wholesale). If `render_state_block` lacks pass_through rendering: add it (additive, one pinned line format + test) — disclose.

- [ ] Tests FIRST (RED) → implement → GREEN → ruff. **Commit** — `feat(chat): sse envelope sink and execute_turn orchestrator`

---

### Task 4: HTTP surface + frontend wiring

**Files:** `api/live_chat.py`; `api/app.py` (additive mount switch); `core/config.py` (+ `chat_mode`); frontend `api/client.ts` + `features/chat/*` (additive); tests `test_live_chat_sse.py` + frontend vitest additions.

Backend: `POST /api/conversations/{id}/messages` — StreamingResponse(text/event-stream) driving `execute_turn` with a queue-bridged async send; body `{text, client_turn_key}`; the same path/shape the mock serves. `GET /api/skills` — registry-backed `[{id, label, description}]` from SKILL_META (the picker's real source). App factory: `settings.chat_mode == "live"` mounts live_chat, else mock_chat (default "mock" — NOTHING changes for existing envs; both routers never mounted together). Construction wiring in the live branch: ConversationStateStore, RunLogWriter (engine from DATABASE_URL when configured, else writer=None disclosed in a boot log line), RoleClient with `{"stub": DevDeterministicRouter()}` + bedrock per M4 shapes, PromptRegistry from settings.
Backend tests (httpx ASGI, live mode, fake data client): envelope frame parsing (reuse the SSE parse discipline from mock tests), client_turn_key retry → same turn_id + no duplicate rows (writer double), GET /api/skills shape, mock-mode default still serves mock (regression: existing mock tests untouched and green).
Frontend (additive): SkillsPicker fetches `GET /api/skills` (falls back to current static list on failure — non-breaking); chips part renderer gains click-to-send (sends chip value as a user message via the existing send path); no store contract changes (the live backend speaks the same envelope). AMENDED (post-T3 review): also (a) widen `ToolEventPayload.server` to `string | null` (the live sink sends null for in-process dispatches; zero consumers today, verified); (b) minimal `TablePart` and `ProofPart` renderers registered for kinds `table` and `proof` (the flagship turn emits both; today they fall through to the raw-JSON FallbackPart) — a theme-tokened table element and a collapsible line list, nothing more; `metric_grid`/`artifact` stay on FallbackPart until Phase 8 (first producer), disclosed. Vitest: picker fetch + fallback; chip click dispatches send; table + proof render from a captured flagship part payload.
AMENDED (post-T3 review, closing the turn-id seam + retry contract — sanctioned edits to T1/T3 files): `RunLogWriter.start_turn` gains optional `turn_run_id: str | None = None` (None → mint internally, preserving every T1 test; provided → use verbatim), and `execute_turn` threads the sink's `turn_id` into `start_turn` so `turn_run.id` IS the SSE `turn_id` (doc 06 §1's own comment requires it; Phase 11's reconciliation endpoint needs the lookup). Retry short-circuit lives IN `execute_turn`: when `start_turn` returns `created=False`, emit a pinned error frame (code "duplicate_turn", message "this turn was already processed — refresh to load the conversation") and finalize NOTHING (the original turn owns the row) — no re-dispatch, no child-row collisions; Phase 11 upgrades this to true replay per doc 01 §5. Known accepted residual: `next_turn_index` consumed before the check (dense numbering gap on retry, harmless).

- [ ] Backend tests FIRST (RED) → implement → GREEN; frontend vitest RED → implement → GREEN; ruff + eslint/tsc clean. **Commit** — `feat(chat): live chat http surface behind CHAT_MODE with real skills picker`

---

### Task 5: Scripted E2E, Playwright smoke, cutover

**Files:** `tests/test_chat_e2e_scripted.py` (pg-marked); `api/live_chat.py` (AMENDED post-T4: + minimal live bootstrap routes); `infra/docker-compose.yml` (CHAT_MODE=live for backend); runbook note; Playwright verification (evidence in report, not committed test).

AMENDED (post-T4 disclosure — closing a plan gap): live mode must serve the frontend's full bootstrap flow before cutover can honor the "localhost stays demo-able" rule. Add to `live_chat.py`: the mock's conversation create/list/transcript/feedback route shapes, backed by a minimal in-memory conversation+message store alongside `ConversationStateStore` (assistant messages appended from the turn's emitted parts at done-time; feedback accepted + logged; Phase 10 replaces all of it — same-surface note in the docstring). Also add the `data_backend == "snowflake"` guard `dev_runner.py` already has (fail loudly pre-Phase-15, never silently query the wrong schema) + a test for both.

The doc-08 validation, executed literally: scripted conversation over the LIVE path against seeded Postgres + SyntheticDataClient — (1) "Top GP customers for Port of Singapore in April 2026" → table + proof parts, status ok; (2) "and for May?" → carry-over (customer/port carried, period replaced); (3) "same for Rotterdam" → port replaced, period carried; (4) an ambiguous customer question ("gp for meridiann in april 2026" — NOTE lowercase 'meridiann' double-n bands per P4/P5 evidence) → chips + clarify. THEN inspect the run-log rows per doc 08: one `turn_run` per turn, terminal statuses (ok/ok/ok/clarify), token roll-ups match summed `llm_calls`, one `llm_calls` row per DevRouter invocation (2 per dispatching turn: tool_use + end_turn), one `tool_calls` row per dispatch with validated args, seq matching the SSE tool_seq. Container: rebuild the backend dev image FIRST (`docker compose build backend` — picks up rapidfuzz/jinja2 via pip install -e; the ledgered carry closes here), restart, seed intact (checksum). Compose flips `CHAT_MODE=live`. Playwright: drive localhost:5173 through the scripted conversation (send, watch tool steps render, table part, chips on the ambiguous turn, chip click resolves) — screenshots + notes in the report. Mock stays in the tree (tests keep running it via factory-direct construction).

- [ ] E2E tests FIRST against the live-mode app (RED because compose still mock → run against a locally-constructed live app first, then flip compose, re-run against the container) → cutover → Playwright evidence → runbook + morning-notes gate commands. **Commit** — `feat(chat): scripted end-to-end conversation live, CHAT_MODE cutover to live`

---

## Phase Gate (human validation)

1. `docker compose up` → open localhost:5173 → type the Singapore question → watch tool steps stream, table + proof render; "and for May?" carries; "gp for meridiann in april 2026" → chips; click a chip → resolves.
2. `pytest tests/test_chat_e2e_scripted.py -m pg -v` → 4-turn script green incl. row inspection.
3. `SELECT status, input_tokens, output_tokens FROM turn_run ORDER BY created_at` → the scripted turns visible, statuses ok/ok/ok/clarify.

## Self-Review Notes

- Doc-08 P6 coverage: default flow live ✓, streamed parts + proof + tool steps ✓, chips ✓, carry-over ✓, picker on real registry ✓, run-log migrations + writer with provisional/append/finalize ✓, scripted-conversation validation incl. row inspection ✓, E2E pytest stubbed-LLM ✓ (DevDeterministicRouter IS the stub), Playwright smoke ✓ (evidence-based).
- Deliberate scope: no research skill (P7), no briefs (P8), no auth (P9 — dev|local constant), no conversations/messages persistence or resume (P10 — in-memory state store with the same surface), no reconnect reconciliation endpoint (P11), no memory distillation (P13). Parked decisions untouched.
- Carries closed here: pass_through wiring (doc 02 §5), Settings.prompts_dir/agent_max_iterations consumption, proof/artifact part reconciliation (P3), container rebuild for rapidfuzz/jinja2 (P4/P5).
- Type consistency: ParsedTurn/ConversationSlots/SlotUpdates from P4; TurnResult/ToolRecord/LLMRecord/EventSink from P5; SkillResult parts/proof from P3; doc 06 column names verbatim in writer kwargs.
