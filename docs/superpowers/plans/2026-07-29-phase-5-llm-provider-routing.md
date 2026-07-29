# Poseidon Phase 5: LLM Provider Layer + Routing (stub-first) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the config-driven LLM layer (doc 03): normalized provider types, `BedrockProvider` (Converse/ConverseStream) + stub provider, `RoleClient` over `models.yml`, `PromptRegistry` with the fixed assembly order, the agent loop (validation, structured error return, iteration cap, tool-event emission), utility title role, and the router-decision suite (stub default, `-m router_live` gated). **No live credentials required for anything green tonight; the live smoke is marker-gated and skips cleanly.**

**Architecture:** Doc 03 §1-§6 verbatim where it specifies; decision D33 scopes Phase 5 to Bedrock + stub only (CortexProvider is Phase 14; its config profile is declared and resolvable, its invocation fails with a structured, tested error). The loop consumes Phase 3's `SkillRegistry`/`TOOL_SCHEMAS` and Phase 4's `ParsedTurn`; it emits tool events through an `EventSink` seam that Phase 6 binds to SSE, and returns per-turn records shaped for Phase 6's run-log writer. Prompt assembly takes user-instruction/memory as plain string arguments (Phase 9/13 populate them; empty strings tonight).

**Tech Stack:** Existing backend. New deps: `jinja2>=3.1` (prompt rendering), `boto3>=1.34` (bedrock-runtime; already transitively present via artifact store — pin explicitly in `[project]`). New pytest marker: `router_live`.

## Global Constraints

- **Offline by default:** no test outside `-m router_live` may open a network connection or construct a real boto3 client against AWS. Bedrock normalization is tested against recorded/hand-authored Converse response dicts and ConverseStream event sequences. `router_live` tests carry a skip-guard (creds/env probe, same register-in-pyproject discipline as `pg`).
- **Config paths deviate from doc 03's literal `config/`** (deliberate, P3 ontology-mount lesson: repo-root paths need container mounts): models and prompts live at `backend/poseidon/config/models.yml` and `backend/poseidon/config/prompts/router/system.md`, resolved relative to the package (`Path(__file__)`), overridable via Settings fields `models_path` / `prompts_dir`. Ships with every image automatically; no new mounts.
- `models.yml` content: doc 03 §2's YAML **verbatim** including its "illustrative — verify current ids" caveat comment. Code never names a model id; only roles. Env overrides `LLM_MODEL_<ROLE>` / `LLM_PROVIDER_<ROLE>` win over the file.
- `LLM_MODE` Settings field: `"stub"` (default) | `"live"`. Stub mode substitutes the stub provider behind the same `RoleClient.invoke` — **no calling code changes** (doc 08's validation criterion; prove by substitution test).
- Determinism everywhere outside live calls; ASCII source+tests; frozen dataclasses; issue/error messages byte-pinned; docstrings explain WHY (house register).
- Do not modify Phase 2-4 modules, mock_chat, dev_runner, frontend, legacy root, or docs/architecture. Additive only: `core/config.py` Settings fields, `pyproject.toml` deps + marker.
- Iteration cap default **10**, configurable via Settings `agent_max_iterations`.
- Errors in the loop: a failed tool dispatch returns to the model ONCE as structured content (self-correction), a second failure fails the turn with the structured error result (RFC-7807 shape from the registry) — pinned both branches.
- Conventional commits on `phase-3-8-overnight`; every commit ends with trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Supervisor role: config-declared (`enabled: false`) only. No supervisor code in Phase 5 (D12).

## File Map

```
backend/poseidon/core/llm/
  __init__.py       # exports: ToolCall, LLMResponse, RoleClient, PromptRegistry, run_turn, StubProvider
  types.py          # ToolCall(id,name,arguments) . LLMResponse(text,tool_calls,stop_reason,input_tokens,output_tokens)
  bedrock.py        # BedrockProvider (Converse + ConverseStream normalization; lazy client)
  stub.py           # StubProvider: replays scripted LLMResponse sequences; records requests
  roles.py          # load_model_profiles(), RoleClient.resolve(role) / invoke(role, ...)
  prompts.py        # PromptRegistry: load/render Jinja prompt files; assemble_system(...) fixed order
  loop.py           # run_turn(...): agent loop + EventSink protocol + TurnRecord shapes
  titles.py         # title_for(text, role_client) via utility role
backend/poseidon/config/models.yml
backend/poseidon/config/prompts/router/system.md
backend/tests/test_llm_types_roles.py
backend/tests/test_llm_prompts.py
backend/tests/test_llm_bedrock.py
backend/tests/test_llm_loop.py          # loop + stub router-decision execution
backend/tests/routing_cases.yml         # doc 03 §6 case format (five doc cases + loop cases)
backend/pyproject.toml                  # + jinja2, boto3 pins, router_live marker
backend/poseidon/core/config.py         # + llm_mode, models_path, prompts_dir, agent_max_iterations
```

---

### Task 1: Types, models.yml, RoleClient (config resolution + env overrides + stub/live seam)

**Files:** `llm/{__init__,types,roles,stub}.py`; `poseidon/config/models.yml`; `core/config.py` (additive); test `test_llm_types_roles.py`; pyproject (deps+marker).

**Interfaces (exact):**

```python
# types.py
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str                     # skill id, e.g. "data_qa.metric_query"
    arguments: dict[str, object]  # parsed JSON args (validation happens at dispatch)

@dataclass(frozen=True)
class LLMResponse:
    text: str                     # "" when pure tool_use
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str              # "tool_use" | "end_turn" | "error"
    input_tokens: int
    output_tokens: int

# roles.py
class LLMProvider(Protocol):
    def invoke(self, *, system: str, messages: list[dict], tools: list[dict],
               model: str, params: dict) -> LLMResponse: ...

@dataclass(frozen=True)
class RoleConfig:
    provider: str                 # "bedrock" | "cortex"
    model: str
    params: dict[str, object]     # max_tokens/temperature/enabled etc. (passthrough)

def load_model_profiles(path: Path) -> dict[str, dict[str, RoleConfig]]: ...
class RoleClient:
    def __init__(self, settings: Settings, providers: Mapping[str, LLMProvider] | None = None): ...
    def resolve(self, role: str) -> RoleConfig: ...   # profile from settings.llm_profile; env overrides applied
    def invoke(self, role: str, *, system: str, messages: list[dict], tools: list[dict] = []) -> LLMResponse: ...

# stub.py
class StubProvider:
    def __init__(self, script: Sequence[LLMResponse]): ...
    calls: list[dict]             # each recorded invoke kwargs (system/messages/tools/model/params)
    # invoke() pops the script in order; raises AssertionError("stub script exhausted") past the end
```

Settings additions (all defaulted, crash-free locally): `llm_mode: str = "stub"`, `llm_profile: str = "bedrock"`, `models_path: Path | None = None` (None → packaged default), `prompts_dir: Path | None = None`, `agent_max_iterations: int = 10`.

Resolution rules (pin each): unknown role → `KeyError` with message `f"unknown LLM role {role!r} — roles: {sorted(names)}"`; env override `LLM_MODEL_ROUTER=x` replaces model only, `LLM_PROVIDER_ROUTER=cortex` replaces provider only; `llm_mode="stub"` → `invoke()` uses the stub provider registry entry regardless of RoleConfig.provider (RoleConfig still reports the configured provider — mode is an invoke-time substitution, proving the seam); `llm_mode="live"` + provider `"cortex"` → `RuntimeError` byte-pinned: `"provider 'cortex' is configured but not available until the SPCS deployment phase (decision D33) — use profile 'bedrock' or LLM_MODE=stub"`.

`models.yml`: doc 03 §2 YAML verbatim (both profiles, six roles each, supervisor `enabled: false`).

- [ ] Tests FIRST: profile load (both profiles, six roles, exact model strings from doc 03); env-override table (model-only, provider-only, both, unset); unknown-role message pin; stub-mode substitution (RoleClient with llm_mode=stub + a StubProvider script → invoke returns scripted response AND records the request; same call in live+cortex → pinned RuntimeError); StubProvider exhaustion. RED → implement → GREEN → ruff. **Commit** — `feat(llm): normalized types, model profiles, and role client with stub seam`

---

### Task 2: PromptRegistry + router system prompt + assembly order + contract tests

**Files:** `llm/prompts.py`; `poseidon/config/prompts/router/system.md`; test `test_llm_prompts.py`.

**Interfaces:**

```python
class PromptRegistry:
    def __init__(self, prompts_dir: Path): ...
    def render(self, name: str, /, **context) -> str: ...   # name like "router/system"; Jinja2, StrictUndefined
def assemble_system(base: str, user_instruction: str, memory_doc: str, state_block: str) -> str: ...
    # fixed order (doc 03 §3): base, then instruction, then memory, then state — labeled sections,
    # empty inputs contribute NOTHING (no empty headers)
def render_state_block(slots: ConversationSlots, parsed: ParsedTurn | None) -> str: ...
    # deterministic plain-text rendering of slots + ParsedTurn (periods ISO, entities with tier/confidence,
    # issues verbatim) — the "structured context block" of assembly item 5
```

`router/system.md` (author fully): charter (deterministic-first: skills do the work, the model only routes), routing rules (always the full registry; conversation mode is advisory context never a filter — D19; prefer clarification over guessing when ParsedTurn carries issues), and two Jinja placeholders `{{ metric_definitions }}` + `{{ negative_constraints }}` filled at render time from the ontology (doc 03 §4) — plus `{{ skill_lines }}`, one line per registered skill (id + description from SKILL_META).

Contract tests (TM1 pattern, doc 03 §4): rendered router system prompt names EVERY skill the registry discovers (assert dynamically against `SkillRegistry.discover()`, not a hardcoded list); contains every certified metric name from the ontology and at least one negative-constraint string verbatim (pick one from the ontology and pin it); `StrictUndefined` → missing placeholder raises (pinned); assembly order test (all four sections present in order with pinned headers; empty instruction/memory produce no headers); `render_state_block` goldens for: empty slots, carried customer+period, ParsedTurn with issues.

- [ ] Tests FIRST (RED at import) → implement → GREEN → ruff. **Commit** — `feat(llm): prompt registry, router system prompt, and assembly order with contract tests`

---

### Task 3: BedrockProvider — Converse + ConverseStream normalization (offline recorded; live smoke gated)

**Files:** `llm/bedrock.py`; test `test_llm_bedrock.py`.

**Interface:**

```python
class BedrockProvider:
    def __init__(self, region: str = "us-east-1", client=None): ...  # client injectable; real client built lazily on first use
    def invoke(self, *, system, messages, tools, model, params) -> LLMResponse: ...      # Converse
    def invoke_stream(self, *, system, messages, tools, model, params,
                      on_text: Callable[[str], None]) -> LLMResponse: ...                # ConverseStream; on_text per delta; returns final normalized response
```

Normalization contract (pin every branch from hand-authored response dicts matching the Converse API shape): `stopReason "tool_use"` → tool_calls from `toolUse` blocks (id/name/input); `"end_turn"`/`"max_tokens"` → end_turn with text joined from text blocks; usage → input/output tokens (missing usage → 0s, documented); tools list passed as `toolConfig` (wrap TOOL_SCHEMAS entries: `{"toolSpec": {"name", "description", "inputSchema": {"json": ...}}}`); params map max_tokens→`inferenceConfig.maxTokens`, temperature→`temperature`; `enabled` and unknown params are dropped with a comment. Stream: hand-authored event sequences (`contentBlockStart` toolUse, `contentBlockDelta` text/toolUse input json fragments, `messageStop`, `metadata` usage) → same LLMResponse as non-stream equivalent + `on_text` called per text delta (record and pin the call list). Client errors (botocore ClientError, injectable fake raising) → `LLMResponse(stop_reason="error", text=<pinned f"bedrock error: {code}">)` — never raises.
`router_live` marker: one smoke test constructing the real client (skip-guard: `AWS_ACCESS_KEY_ID`/`AWS_PROFILE` present else skip with reason "no AWS credentials"), calling the utility role's model with a trivial prompt, asserting only shape (non-empty text, tokens > 0). Register `router_live` in pyproject markers.

- [ ] Tests FIRST (recorded dicts; RED) → implement → GREEN → ruff; verify `pytest -m router_live` SKIPS cleanly on this machine (no creds) and the skip reason is the pinned string. **Commit** — `feat(llm): bedrock provider with converse and stream normalization`

---

### Task 4: Agent loop + EventSink + utility titles + router-decision suite (stub + live-gated)

**Files:** `llm/{loop,titles}.py`; tests `test_llm_loop.py`; `tests/routing_cases.yml`.

**Interfaces:**

```python
class EventSink(Protocol):
    def emit(self, kind: str, payload: dict) -> None: ...   # kinds: "tool_start" | "tool_done" | "llm_call" | "turn_error"
@dataclass(frozen=True)
class ToolRecord:   # P6 run-log seam — shapes only, no persistence here
    tool_seq: int; skill_id: str; arguments: dict; status: str; duration_ms: int; result_digest: str
@dataclass(frozen=True)
class LLMRecord:
    call_seq: int; role: str; provider: str; model: str; stop_reason: str; input_tokens: int; output_tokens: int
@dataclass(frozen=True)
class TurnResult:
    text: str; status: str            # "ok" | "error"
    tool_records: tuple[ToolRecord, ...]; llm_records: tuple[LLMRecord, ...]
    problem: dict | None              # RFC-7807 dict on status "error"

def run_turn(*, role_client: RoleClient, registry: SkillRegistry, context: SkillContext,
             prompt_registry: PromptRegistry, user_instruction: str, memory_doc: str,
             parsed: ParsedTurn | None, window: list[dict], sink: EventSink,
             max_iterations: int) -> TurnResult: ...
```

Loop rules (doc 03 §3, pin each): assemble system via Task 2 exactly once per turn; first provider call includes full `TOOL_SCHEMAS`; `tool_use` → for EACH tool call: emit `tool_start`, dispatch through `registry.dispatch` (its structured 422/404/500 never raises), emit `tool_done`, append `toolResult` message (digest-only content: parts stripped to kind+row-count summary + proof line — context hygiene, bulk rows never re-enter the model); failed dispatch (problem result) → the problem JSON is the tool result ONCE (self-correction); the SAME skill failing a SECOND time → turn fails: `TurnResult(status="error", problem=<that problem>)` + `turn_error` emitted; `end_turn` → text is the reply; iteration cap reached → status "error", problem `{"title": "agent loop exceeded max iterations", "detail": f"cap {cap}"}` pinned; every provider call appends an `LLMRecord`, every dispatch a `ToolRecord` (digest = first proof line or part-kind summary).
`titles.py`: `title_for(text, role_client) -> str` — utility role, prompt inline-file `prompts/utility/title.md` (one-liner template), strip quotes/whitespace, hard-cap 60 chars (pin).
`RecordingSink` test double (list of (kind, payload)).

`routing_cases.yml`: doc 03 §6's five cases verbatim (ids `existing_brief_by_name`, `ambiguous_customer_clarifies`, `default_data_qa_breakdown`, `pivot_to_research_with_carry`, `post_brief_pivot_to_internal`) + field `execution:` per case — `live_only: true` on the four whose expected skills are not yet registered/enabled (existing_customer_brief disabled until P8; research.web_research P7; clarify-path needs P6 chips) with comment naming the owning phase; `default_data_qa_breakdown` additionally carries a stub script. Stub executor: loads cases where `live_only` is absent, builds StubProvider script (recorded tool_use → metric_query with the case's args_subset → end_turn), runs `run_turn` against the REAL registry + fake DataClient from P4's test fixtures, asserts dispatched skill + args subset + a `table`/`metric_grid` part present + both records populated. Live executor: `@pytest.mark.router_live`, iterates ALL cases against the live router role (skips on this machine).
Loop unit tests (stub scripts, enumerate): single-tool happy path (events exactly tool_start,tool_done; records 2 llm + 1 tool); two-tool chain; self-correction (invalid args 422 → second call corrected → ok; assert problem JSON went back as tool result); double-failure → error turn; unknown-skill 404 → same self-correction contract; iteration cap (script of 11 tool_uses → pinned problem); end_turn-immediately (no tools); stub-seam substitution proof (same run_turn code path with llm_mode=stub vs a fake "live" provider registry entry — only the provider registry differs).

- [ ] Tests FIRST (RED) → implement loop → titles → GREEN → ruff; full offline suite green; `-m router_live` skips clean. **Commit** — `feat(llm): agent loop with tool events, utility titles, and router-decision suite`

---

## Phase Gate (human validation)

1. Offline: `python -m pytest tests/test_llm_*.py -v` green; full suite green; `pytest -m router_live -v` → every live test SKIPPED with "no AWS credentials".
2. Seam: `python - <<'PY'` snippet in the report — run_turn with a StubProvider script answering the Singapore question end-to-end through the REAL metric_query skill against synthetic data, printing the reply text + tool records.
3. When AWS keys arrive (your morning list): `pytest -m router_live` runs the Bedrock smoke + live routing cases — no code changes, only env.

## Self-Review Notes

- Doc-08 P5 coverage: provider contract ✓ (Bedrock+stub; cortex config-declared, invocation error pinned per D33), RoleClient+models.yml ✓, PromptRegistry+assembly ✓, agent loop (validation/structured errors/cap/tool events) ✓, StubRouter ✓ (StubProvider + stub executor — the doc's name covers the mechanism), utility titles ✓, router-decision suite stub+live ✓, prompt contract tests ✓.
- Deliberate scope: no run-log persistence (P6 owns writer; records returned shaped), no SSE binding (EventSink seam; P6), no supervisor code (D12), no CortexProvider (D33/P14), user instruction/memory as arguments (P9/P13 populate).
- Type consistency: ToolCall.name == skill ids from P3 registry; SkillContext/ParsedTurn/ConversationSlots consumed from P3/P4 unchanged; TOOL_SCHEMAS wrapped, not redefined.
