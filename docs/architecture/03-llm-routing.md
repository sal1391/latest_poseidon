# 03 — LLM Routing and Model Tiering (Bedrock + Snowflake Cortex)

## 1. Provider layer

One provider abstraction, modeled on TM1's `LLMRouter`/`LLMProvider` (normalized dataclasses,
proven across multiple providers), with **two first-class implementations behind one
config-driven interface** (decision D21):

```
backend/core/llm/
  types.py         # ToolCall(id, name, arguments) · LLMResponse(text, tool_calls, stop_reason,
                   #   input_tokens, output_tokens) · stop_reason in {tool_use, end_turn, error}
  bedrock.py       # BedrockProvider: boto3 bedrock-runtime Converse/ConverseStream — native
                   #   tool-use (toolConfig) and uniform token usage reporting
  cortex.py        # CortexProvider: Snowflake Cortex via the Snowpark session (doc 04/07) —
                   #   tool use via strict-JSON constrained prompting (the proven TM1/wfs_core
                   #   method), normalized to the same ToolCall/LLMResponse shapes
  roles.py         # RoleClient: resolve(role) -> {provider, model, params}; invoke(role, ...)
  prompts.py       # PromptRegistry: load/render versioned prompt files
```

- Decision D11: on Bedrock, use the `Converse` API rather than per-model `InvokeModel` payloads
  — one request/response shape across Anthropic and Nova models makes tiers genuinely swappable.
- Decision D21: provider is resolved **per role** from config. Defaults per deploy target
  (doc 07): SPCS → `cortex` (zero external credentials inside the platform; the current
  Poseidon already runs Cortex `claude-sonnet-4-5`); local/EC2 → `bedrock` (the natural AWS
  pairing). Either can be forced anywhere — Bedrock works inside SPCS through an External
  Access Integration (doc 07 §4).
- The router-decision suite (§6) runs against whichever provider config selects, so the
  Cortex emulation path is pinned by the same evidence as native tool calling.
- **Decision D33 — implementation order, not interface.** The abstraction and the config contract
  above are final and unchanged; the two implementations arrive at different times. Phase 5 (doc
  08) ships `BedrockProvider` and the stub provider only. `CortexProvider` is built in the
  preparation for the SPCS deployment phase (Phase 15), where Cortex is the default, and lands
  with a **provider-parity contract test**: the same recorded tool-calling scenarios asserted to
  normalize to identical `ToolCall`/`LLMResponse` shapes on both providers. Reason: prove the seam
  with one live provider before paying for two.

## 2. Model tier map (config-driven)

`config/models.yml` maps **roles** to `{provider, model, params}`. Code never names a model id;
it names a role. Every env can override via `LLM_MODEL_<ROLE>` / `LLM_PROVIDER_<ROLE>` variables.

| Role | Default tier | Responsibilities |
|------|--------------|------------------|
| `router` | Claude Sonnet | Tool selection and argument filling over `TOOL_SCHEMAS`; clarification decisions; final conversational replies |
| `synthesis` | Claude Sonnet | Contextualizer/Researcher/Strategist narrative generation inside subskills |
| `supervisor` | Claude Opus (optional, default **off**) | See section 3 |
| `memory` | Claude Sonnet | Distills the per-user memory document at end of conversation (doc 05 §5); config-driven tier like every other role |
| `utility` | small tier (Nova Lite on Bedrock / small Cortex model) | Chat title generation, mode/intent classification fallback, entity-extraction fallback when the deterministic parser abstains |
| `micro` | smallest tier (Nova Micro on Bedrock / smallest Cortex model) | Trivial formatting/normalization jobs (e.g., one-line summaries for the conversation list) |

```yaml
# config/models.yml (illustrative — verify current ids per provider console)
profiles:
  bedrock:   # default for local / EC2 (region us-east-1)
    router:     { provider: bedrock, model: us.anthropic.claude-sonnet-4-5-20250929-v1:0, max_tokens: 4096, temperature: 0.2 }
    synthesis:  { provider: bedrock, model: us.anthropic.claude-sonnet-4-5-20250929-v1:0, max_tokens: 8192, temperature: 0.3 }
    supervisor: { provider: bedrock, model: us.anthropic.claude-opus-4-1-20250805-v1:0,   enabled: false }
    memory:     { provider: bedrock, model: us.anthropic.claude-sonnet-4-5-20250929-v1:0, max_tokens: 2048, temperature: 0.1 }
    utility:    { provider: bedrock, model: us.amazon.nova-lite-v1:0,  max_tokens: 512, temperature: 0.0 }
    micro:      { provider: bedrock, model: us.amazon.nova-micro-v1:0, max_tokens: 256, temperature: 0.0 }
  cortex:    # default for SPCS (models resolved by Cortex inside the account)
    router:     { provider: cortex, model: claude-sonnet-4-5, max_tokens: 4096, temperature: 0.2 }
    synthesis:  { provider: cortex, model: claude-sonnet-4-5, max_tokens: 8192, temperature: 0.3 }
    supervisor: { provider: cortex, model: claude-opus-4-1, enabled: false }
    memory:     { provider: cortex, model: claude-sonnet-4-5, max_tokens: 2048, temperature: 0.1 }
    utility:    { provider: cortex, model: claude-haiku-4-5, max_tokens: 512, temperature: 0.0 }
    micro:      { provider: cortex, model: claude-haiku-4-5, max_tokens: 256, temperature: 0.0 }
```

Tiering principle: **anything a smaller model can do deterministically enough is pushed down;
anything deterministic Python can do is not an LLM job at all.** The deterministic parser (doc 02
§5) runs first; `utility` is its fallback, not its replacement.

## 3. Router design

**Entry orchestration (decision D19).** Bubble conversations dispatch their brief skill
deterministically on the first turn — mode and subject are already known, so no router call is
made. The default flow routes from turn one. From the second turn onward the loop below is
identical in every flow: the full `TOOL_SCHEMAS` registry is always presented; the conversation
mode appears only as advisory context ("this conversation opened with an existing-customer
brief for X"), never as a filter on available skills.

**Prompt assembly (fixed order, every router and synthesis call):**

1. Base system prompt (from `PromptRegistry` — charter, routing rules, ontology guardrails).
2. The user's personal system instruction (doc 05 §5).
3. The user's memory document (markdown, size-capped — doc 05 §5).
4. Conversation state: slots, carried entities (active customer, port, period), pass-through
   values (doc 02 §5).
5. `ParsedTurn` rendered as a structured context block + conversation window.

The agent loop (per turn):

1. Build the router request per the assembly order above + `TOOL_SCHEMAS` from the registry.
2. Provider call → if `stop_reason == tool_use`: validate args (Pydantic), dispatch skill,
   append structured tool result, loop (max-iteration cap, configurable, default 10 — the brief
   skills are single-call; the cap matters for `data_qa`/`research` chains).
3. If `end_turn`: the text is the conversational reply. Structured parts already streamed by
   skills are not re-generated by the model.
4. Errors return to the model once as structured content (self-correction chance), then fail the
   turn with an RFC-7807 error and a run-log record.
5. **Verbose tool visibility:** the dispatcher emits a `tool` SSE event at every dispatch
   boundary — skill start/done and each external call with a human-readable label ("Calling
   Perplexity — marine news search…") — rendered as transcript steps (doc 01 §4) and mirrored
   into run-log `tool_calls` (doc 06).

Context hygiene (TM1 lessons, revised by the 2026-08-05 live-synthesis fix): a tool result reaches
the model as a short digest (part kinds, row counts, the certified proof block) followed by the
result's own values, capped (`RESULT_CONTENT_MAX_ROWS` rows / `RESULT_CONTENT_MAX_CHARS`
characters, explicit truncation marker) — the digest opens the `toolResult` content, it is not the
whole of it. TM1's original "digest only, bulk data never re-enters the context window" rule
starved the model that authors the answer's prose of the rows it was describing; the run-log's
`result_digest` column is unaffected and stays the short digest alone (P11's redaction rule nulls
it on the same terms as before). The conversation window is bounded with utilization warnings;
slot state travels in the system prompt, not as accumulated prose history.

**Supervisor tier (Opus, optional):** enabled per-environment. When on, it intercepts at two
points: (a) *pre-dispatch review* for turns where the router's chosen skill confidence conflicts
with the deterministic `skill_hinter` shortlist, and (b) *escalation* — after N router
self-correction failures the turn is retried once with the supervisor as router. It is a drop-in
because both tiers speak the same `Converse` tool-use contract. Default off: cost is not
justified until run-log evidence shows router error patterns (decision D12).

## 4. Prompts as config

No inline prompt constants (reversal of TM1's 160-line `SYSTEM_PROMPT` module constant). All
prompts are versioned files rendered by `PromptRegistry`:

```
config/prompts/router/system.md            # router charter + routing rules
backend/tasks/<task>/skills/<skill>/prompts/*.md   # skill/subskill prompts (doc 02)
```

- Templates use Jinja placeholders for structured inputs (data blocks, field dictionary,
  ParsedTurn); rendering is pure and unit-testable.
- The router system prompt embeds two ontology-derived blocks at render time: certified metric
  definitions and `negative_constraints` (real observed hallucinations, doc 04) — the guardrail
  text lives with the ontology, not copy-pasted into prompts.
- Prompt contract tests pin critical content (TM1's `test_system_prompt_mentions_render_chart`
  pattern): e.g., the system prompt must name every registered skill; the strategist prompt must
  contain the exact CRM headers. Editing a prompt that breaks a contract fails CI, not production.
- Every prompt file change is a git diff — prompt changes are reviewable and bisectable.

## 5. Cost and latency budgets

- Per-turn token accounting is recorded in the run log (doc 06); budgets are asserted in tests
  (router system prompt + injected user instruction/memory under a combined token ceiling; tool
  results digested).
- Streaming: on Bedrock, `ConverseStream` for `synthesis` narratives (tokens to the UI as they
  arrive) and plain `converse` for short-output roles. On Cortex, narratives arrive whole and
  are emitted as completed `phase_section` parts — the `tool_event` steps keep the UI alive
  either way, and the SSE contract (doc 01 §5) is unchanged.
- Utility/micro roles carry `temperature: 0.0` and tight `max_tokens` — grunt work is cheap and
  clamped on both providers.

## 6. Router-decision tests (first-class, in-repo)

The category TM1 lacked in CI and mom-comparison lacked entirely: tests of the **LLM's routing
behavior**, not the deterministic functions beneath it.

Case format (YAML, colocated per skill in `tests/test_routing.py` data):

```yaml
- id: existing_brief_by_name
  setup: { mode: existing, state: {} }
  user: "Run the brief for Maersk"
  expect: { skill: customer_insight.existing_customer_brief, args_subset: { customer: "MAERSK*" } }
- id: ambiguous_customer_clarifies
  user: "brief for pacific"
  expect: { clarify: true }
- id: default_data_qa_breakdown
  setup: { mode: default, state: {} }
  user: "top GP customers for Port of Singapore in April 2026"
  expect: { skill: data_qa.metric_query, args_subset: { metric: GP, group_by: CUST_NM, port: "SINGAPORE*" } }
- id: pivot_to_research_with_carry
  setup: { mode: default, state: { active_customer: "CUSTOMER_X" } }
  user: "any relevant news on them I should be aware of?"
  expect: { skill: research.web_research, args_subset: { customer: "CUSTOMER_X" } }
- id: post_brief_pivot_to_internal
  setup: { mode: prospect, state: { mentioned_ports: ["ROTTERDAM"] } }
  user: "which existing customers do we serve at that port?"
  expect: { skill: data_qa.metric_query, args_subset: { port: "ROTTERDAM" } }
```

Execution modes:

1. **Stub mode** (default, no network): a `StubRouter` replays recorded `LLMResponse` sequences —
   verifies the loop, dispatch, validation, and memory carry deterministically (TM1
   `test_agent_loop.py` pattern).
2. **Live mode** (`pytest -m router_live`, requires credentials for the configured provider —
   Bedrock keys or a Snowflake session for Cortex): sends each case to the real `router` role
   and asserts the chosen tool name and a **subset** of critical arguments (never exact-match on
   free-text args). Ambiguity cases assert the clarify path.
3. Recorded live transcripts are checked in as fixtures and refreshed deliberately, so drift
   between recordings and reality is a reviewed diff.

Seeding: `scripts/export_router_cases.py` converts real run-log rows (question → tool call) into
candidate YAML cases — production questions become regression tests, and thumbs-down feedback
rows are exported first (doc 06 §7): every complaint becomes a candidate regression case.

Decision D6 (restated): all of the above is committed to the repository and runnable from day
one. Live-mode tests are marker-gated, never gitignored.
