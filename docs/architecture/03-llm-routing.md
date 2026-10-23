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
  messages_api.py  # shared Messages API translation (Converse dialect <-> Messages body), the
                   #   httpx invoke, ProviderCredentialsError (D79)
  anthropic.py     # AnthropicProvider: api.anthropic.com/v1/messages, x-api-key (spec 9.1, D68)
  cortex.py        # CortexProvider: <account>/api/v2/cortex/v1/messages, bearer token or the
                   #   SPCS token file; native tool calling (D40, D79)
  roles.py         # RoleClient: resolve(role) -> {provider, model, params}; invoke(role, ...)
  prompts.py       # PromptRegistry: load/render versioned prompt files
  prompt_tree.py   # PromptTree: resolve("reporting/narrate") -> versioned XML system prompt (D46, first step)
```

- Decision D11: on Bedrock, use the `Converse` API rather than per-model `InvokeModel` payloads
  — one request/response shape across Anthropic and Nova models makes tiers genuinely swappable.
- Decision D21: provider is resolved **per role** from config. Defaults per deploy target
  (doc 07): SPCS → `cortex` (zero external credentials inside the platform; the current
  Poseidon already runs Cortex `claude-sonnet-4-5`); local/EC2 → `bedrock` (the natural AWS
  pairing). Either can be forced anywhere — Bedrock works inside SPCS through an External
  Access Integration (doc 07 §4).
- The router-decision suite (§6) runs against whichever provider config selects, so the
  Cortex path is pinned by the same evidence as native tool calling.
- **Decision D33 — implementation order, not interface.** The abstraction and the config contract
  above are final and unchanged; the implementations arrived at different times. Phase 5 (doc 08)
  shipped `BedrockProvider` and the stub provider only (reason: prove the seam with one live
  provider before paying for two). D40 then moved `CortexProvider` earlier and fixed its transport,
  and the **provider-parity contract** shipped in Phase 17 PR C as `tests/test_provider_parity.py`:
  the same recorded tool-calling scenarios (fifteen fixtures under `tests/fixtures/providers/`)
  asserted to normalize to identical `ToolCall`/`LLMResponse` shapes across four providers: stub,
  bedrock, anthropic and cortex (D68, D79).
- `tool_choice` on `RoleClient.invoke` / `LLMProvider.invoke` forces a named tool (Bedrock
  `toolConfig.toolChoice`); the report narrative is the first forced call. On the Claude 5.5
  generation native forcing is a 400; the `anthropic` profile sets `tool_choice_mode: instruct`
  and the provider sends `tool_choice: auto` plus a system instruction naming the tool (D80).
  The `cortex` profile sets the same mode, so the two Messages API providers behave alike.
- `LLMResponse.reasoning` carries the model's reasoning blocks from a tool_use turn as Converse
  `reasoningContent` blocks (empty unless a provider fills it). The agent loop echoes them,
  deep-copied, ahead of the `toolUse` blocks in the assistant history, which the Claude 5.5
  generation requires; stub and dev router emit none (D80).

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
# config/models.yml (verify current ids per provider console)
# Phase 17 PR C (D68/D79): every model-specific rule lives HERE as params the
# provider forwards or reads, never in provider code. Unknown keys are dropped
# by every provider. Keys with meaning:
#   max_tokens, temperature -> forwarded (omit temperature where the model 400s on it)
#   thinking                -> forwarded verbatim (Messages API providers only)
#   tool_choice_mode        -> native (default) | instruct; instruct turns a forced
#                              tool call into tool_choice auto + a system instruction
#                              for models that 400 on forced tool_choice
#   enabled                 -> RoleClient's on/off gate
profiles:
  bedrock:   # default for local / EC2 (region us-east-1)
    router:     { provider: bedrock, model: us.anthropic.claude-sonnet-4-5-20250929-v1:0, max_tokens: 4096, temperature: 0.2 }
    synthesis:  { provider: bedrock, model: us.anthropic.claude-sonnet-4-5-20250929-v1:0, max_tokens: 8192, temperature: 0.3 }
    supervisor: { provider: bedrock, model: us.anthropic.claude-opus-4-1-20250805-v1:0,   enabled: false }
    memory:     { provider: bedrock, model: us.anthropic.claude-sonnet-4-5-20250929-v1:0, max_tokens: 2048, temperature: 0.1 }
    utility:    { provider: bedrock, model: us.amazon.nova-lite-v1:0,  max_tokens: 512, temperature: 0.0 }
    micro:      { provider: bedrock, model: us.amazon.nova-micro-v1:0, max_tokens: 256, temperature: 0.0 }
  # Cortex Messages endpoint inside Snowflake (D40/D79). Same 5.5 rules as the anthropic profile (owner ruling 2026-10-23); the two profiles differ only in the provider key.
  cortex:
    router:     { provider: cortex, model: claude-sonnet-5-5, max_tokens: 4096, thinking: { type: between_tools }, tool_choice_mode: instruct }
    synthesis:  { provider: cortex, model: claude-sonnet-5-5, max_tokens: 8192, thinking: { type: between_tools }, tool_choice_mode: instruct }
    supervisor: { provider: cortex, model: claude-opus-5-5, enabled: false }
    memory:     { provider: cortex, model: claude-sonnet-5-5, max_tokens: 2048, thinking: { type: between_tools }, tool_choice_mode: instruct }
    utility:    { provider: cortex, model: claude-haiku-5-5, max_tokens: 512, thinking: { type: disabled } }
    micro:      { provider: cortex, model: claude-haiku-5-5, max_tokens: 256, thinking: { type: disabled } }
  anthropic: # direct Claude API over httpx (D68). Claude 5.5 generation, owner ruling 2026-10-23:
             # no temperature (400), thinking off (between_tools on Sonnet 5.5, disabled on Haiku 5.5),
             # forced calls as instructions on Sonnet 5.5 (native forcing is a 400 there; Haiku accepts it)
    router:     { provider: anthropic, model: claude-sonnet-5-5, max_tokens: 4096, thinking: { type: between_tools }, tool_choice_mode: instruct }
    synthesis:  { provider: anthropic, model: claude-sonnet-5-5, max_tokens: 8192, thinking: { type: between_tools }, tool_choice_mode: instruct }
    supervisor: { provider: anthropic, model: claude-opus-5-5, enabled: false }
    memory:     { provider: anthropic, model: claude-sonnet-5-5, max_tokens: 2048, thinking: { type: between_tools }, tool_choice_mode: instruct }
    utility:    { provider: anthropic, model: claude-haiku-5-5, max_tokens: 512, thinking: { type: disabled } }
    micro:      { provider: anthropic, model: claude-haiku-5-5, max_tokens: 256, thinking: { type: disabled } }
```

Params beyond `max_tokens`/`temperature` are provider-specific knobs the profile owns; see the header comment in `models.yml`.

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
   values (doc 02 §5). After an in-chat report (Phase 17 PR A2) the block gains up to three
   lines, each anchored on its own label so none can be read as a carried customer or port:
   `Carried perspective: {p} (ranks {column}; office column {c})`, `Carried office: {col} =
   {value}. data_qa.metric_query applies it when scope="office" (the default). Use
   scope="book" ...` and `Report context: {report_label}. Quote the report's own figures only through
   reporting.report_lookup.` The `report_label` slot carries the report's own months, so a later follow-up that moves the carried periods never changes this line. Internal skills are not in the router's tool list, and the loop
   refuses a call to one with a 403 `internal skill` problem.
5. `ParsedTurn` rendered as a structured context block + conversation window. The block lists `Skill hints` and, directly after it, `Scope hint: book|share` when the scope hinter (doc 02 section 5, D77) fired; both are advisory.

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
backend/prompts/<area>/<name>.xml          # D46 canonical tree, resolved by PromptTree
```

The D46 tree lives under `backend/` because that is the runtime root in every habitat.
Skill-owned `prompts/*.md` migrate into it later (Phase 17 spec section 13); until then
both locations are valid.

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

Phase 17 PR A2 adds four report cases to `backend/tests/routing_cases.yml` (stub mode), beyond
the five above: `report_followup_why_gp_dropped` (a "why" over a carried customer becomes a
breakdown with a comparison window), `report_followup_where_else` (leaves the carried office
with `scope="book"`), `supplier_report_break_down_by_customer` (keeps the supplier filter) and
`report_lookup_top_ports` (a question about the report's own figures goes to
`reporting.report_lookup`).

Phase 17 PR B adds the remaining reports-design §7.6 rows: six stub-run cases (`report_followup_full_account_gp`, `report_followup_share_of_total`, `report_followup_gp_by_month_2025`, `report_followup_then_by_port`, `report_followup_by_port_by_month`, `report_followup_break_down_by_deal_class`) and two pointer entries whose decision is deterministic (`report_followup_compare_to_june` → the C03 suite; `slice_it_another_way` → the orchestrator suite). Pointer entries carry `parser_case`/`orchestrator_case` instead of a script and run in neither executor. The stub-mode dev router now carries the PR B cues (group-by phrases, share phrases on the carried office column, the carried customer under a cue; D78), so these cases and the browser gate run without Bedrock. Phase 17 PR C closes the drift from the live router (D81): `dev_router.py` documents the remaining divergences in its module docstring and fixes three (first bucket wins, the `% of` boundary, plural cues); routing to `reporting.report_lookup` stays out of the dev router because the recorded stub scripts cover the report cases.

Execution modes:

1. **Stub mode** (default, no network): a `StubRouter` replays recorded `LLMResponse` sequences —
   verifies the loop, dispatch, validation, and memory carry deterministically (TM1
   `test_agent_loop.py` pattern).
2. **Live mode** (`pytest -m router_live`, requires credentials for the configured provider —
   Bedrock credentials, `ANTHROPIC_API_KEY`, or a Cortex token): sends each case to the real `router` role
   and asserts the chosen tool name and a **subset** of critical arguments (never exact-match on
   free-text args). Ambiguity cases assert the clarify path. Run live tests file-scoped (`-m router_live tests/test_llm_anthropic.py`); a bare `-m router_live` also sends the thirteen routing cases to the configured provider and runs the Bedrock smoke when AWS credentials are present.
3. Recorded live transcripts are checked in as fixtures and refreshed deliberately, so drift
   between recordings and reality is a reviewed diff.

Seeding: `scripts/export_router_cases.py` converts real run-log rows (question → tool call) into
candidate YAML cases — production questions become regression tests, and thumbs-down feedback
rows are exported first (doc 06 §7): every complaint becomes a candidate regression case.

Decision D6 (restated): all of the above is committed to the repository and runnable from day
one. Live-mode tests are marker-gated, never gitignored.
