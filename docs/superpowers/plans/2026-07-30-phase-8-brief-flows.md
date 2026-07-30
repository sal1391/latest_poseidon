# Poseidon Phase 8: The Two Brief Flows End to End Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The two brief flows live (doc 08 P8): subskills `contextualize`/`research`/`strategize` with prompts-as-config, deterministic first-turn dispatch for bubble entries (D19), concurrent contextualize+research for existing mode, phase streaming into `phase_section`/`metric_grid`/`table`/`artifact` parts, the prospect flow (D10 ordering), and post-brief pivots with carried entities. **All offline: stub-mode synthesis is a deterministic template (honest, labeled), research runs on fixtures, the PDF pipeline is the real P3 one.** Carlos's keys upgrade synthesis + research to live with zero code changes.

**Architecture:** Doc 02 §4 verbatim. The P3-shipped tools (`fetch_metrics`, `fetch_top_ports`, `build_brief_pdf`) finally get their skills. Subskills are code-invoked in fixed order (D3 — never router-visible). Stub-mode synthesis: when `settings.llm_mode == "stub"`, subskills render a deterministic template over their real inputs, opening with the pinned line "Stub-mode synthesis — flip LLM_MODE=live for model narrative." (the flow, streaming, data, and PDF are all real; only the prose is templated — honest offline demo). Progressive display gets a real seam: `SkillContext.emit_part` (additive, optional callback) lets a skill push parts mid-dispatch; the SSE sink streams them immediately and skips re-emitting at tool_done. D19 entry: the opener's flow chips gain `send_text` phrases; the orchestrator matches them, sets `slots.mode`, prompts for the subject; the next turn resolves the subject and dispatches the brief skill DETERMINISTICALLY (no router call); afterwards the full registry resumes.

**Tech Stack:** Existing backend + frontend. No new deps.

## Global Constraints

- **Stub/live discipline:** `llm_mode=="stub"` → template synthesis + FixtureResearchTool (per-schema fixtures); `"live"` → real role calls via `ctx.llm` + configured research transport. ONE switch, everything follows. Withhold PERPLEXITY_API_KEY (env -u) on every suite run; ZERO live calls this phase.
- **Sanctioned additive edits to earlier-phase modules** (each with WHY, each disclosed): `core/skills/context.py` gains `llm: object | None = None` (subskills need role-based synthesis — doc 02 §3's fuller context names it) and `emit_part: object | None = None` (callable; the progressive-display seam); `core/llm/loop.py` `_dispatch_one`'s tool_done payload gains `"artifacts": result.artifacts` (the P5 gap ledgered since P6 — one line; the sink's conversion path is already coded+tested); `core/chat/events.py` sink: incremental-part protocol (emit_part pushes immediately, tool_done emits only `parts[n_streamed:]`; artifact parts after proof, order pinned); `core/chat/orchestrator.py`: D19 entry branch + subject-prompt turn + mode set via `dataclasses.replace` (P6 pass_through precedent) + wiring `llm`/`emit_part` into SkillContext; `core/chat/dev_router.py`: brief-hints branch (hints lead a brief skill + resolved customer → tool_use for that brief — closes `existing_brief_by_name` offline); `api/live_chat.py`: flow chips gain send_text phrases; frontend: MetricGridPart + ArtifactPart renderers + SkillsPicker example entries for the two briefs; the five-or-so "exactly N skills" tests get their mechanical bump to 4 (named ids, never >=).
- **D19 (entry orchestration):** flow-chip send_texts are pinned phrases ("start an existing-customer brief" / "start a new-prospect brief"). Orchestrator matches EXACTLY those (casefolded) → sets `slots.mode` ("existing_customer"/"new_prospect") → emits a text part asking for the subject ("Which customer is this brief for?" / "What company should I research?") + finalize `clarify`. Next turn with a mode set and no brief yet run: existing → customer resolver (ambiguous → chips, same contract as ever); prospect → the raw text is the subject (no resolver — prospects aren't in the dimension). Subject resolved → dispatch the brief skill DETERMINISTICALLY (no router/dev-router call; llm_calls rows = only the subskills' synthesis calls in live mode, zero in stub). After the brief completes, mode STAYS in slots (advisory context) and subsequent turns route normally (full registry).
- **D10 (prospect ordering):** research FIRST, then contextualize (consuming research), then strategize. Existing: fetch tools first (metric_grid + table parts emitted immediately via emit_part), then contextualize ∥ research CONCURRENTLY (ThreadPoolExecutor, 2 workers; results consumed in FIXED order regardless of completion), strategize awaits both, then build_brief_pdf → artifact part.
- **Failure design (doc 02 §6):** a failed subskill (degraded research, LLM error in live mode) fails ITS phase honestly — previously emitted parts stand, the phase_section for the failed phase carries the pinned failure text, the brief continues where doc 02 §4 permits (strategize consumes what exists), the PDF renders what completed. Pinned per branch.
- **Egress law D30 extends to brief research:** the research subskill's queries compose from the subject + fixed lens phrases ONLY (same whitelist discipline, same sentinel contract test shape — plant metric values in the context, assert absence outbound).
- Parked decisions stay parked. ASCII .py; frozen dataclasses; byte-pinned messages; deterministic (concurrency never affects output order); docstrings explain WHY; ruff clean; conventional commits on `phase-3-8-overnight`; trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` every commit.
- Baselines at plan time: 1161 offline / 41 pg / 31 frontend; zero failures the bar. Stack up (CHAT_MODE=live, llm_mode stub).

## File Map

```
backend/poseidon/core/skills/context.py            # + llm, emit_part (additive, sanctioned)
backend/poseidon/core/llm/loop.py                  # + artifacts in tool_done payload (one line, sanctioned)
backend/poseidon/core/chat/events.py               # incremental-part protocol (sanctioned)
backend/poseidon/core/chat/orchestrator.py         # D19 entry + subject turn + context wiring (sanctioned)
backend/poseidon/core/chat/dev_router.py           # brief-hints branch (sanctioned)
backend/poseidon/api/live_chat.py                  # flow-chip send_texts (sanctioned)
backend/poseidon/mcp/perplexity/schemas/{sustainability,market_position,strategic_profile,operational_profile}.json
backend/poseidon/mcp/perplexity/fixtures/{sustainability,market_position,strategic_profile,operational_profile}_clean.json
backend/poseidon/mcp/perplexity/fixture_tool.py    # schema-name → fixture routing (additive)
backend/poseidon/tasks/customer_insight/task.yml   # enabled: true (T4)
backend/poseidon/tasks/customer_insight/skills/existing_customer_brief/{schema.py,skill.py}
backend/poseidon/tasks/customer_insight/skills/existing_customer_brief/prompts/{contextualizer,strategist}.md
backend/poseidon/tasks/customer_insight/skills/existing_customer_brief/subskills/{contextualize,research,strategize}/subskill.py (+tools where needed)
backend/poseidon/tasks/customer_insight/skills/new_prospect_brief/  # full folder, same law
frontend/src/ui/message-parts/{MetricGridPart,ArtifactPart}.tsx + registry.tsx
backend/tests/{test_emit_seam_loop_events,test_brief_subskills,test_brief_skills,test_entry_orchestration}.py
backend/tests/test_chat_e2e_scripted.py            # + the two brief-flow scripts (pg)
backend/tests/routing_cases.yml                    # existing_brief_by_name un-gated
```

---

### Task 1: The plumbing — emit seam, artifact forward, incremental sink, frontend renderers

**Files:** `context.py` (+2 fields), `loop.py` (+1 line + docstring), `events.py` (incremental protocol), frontend `MetricGridPart.tsx`/`ArtifactPart.tsx`/`registry.tsx`; tests `test_emit_seam_loop_events.py` + vitest.

- `SkillContext.emit_part: object | None = None` — a callable `(part: dict) -> None`; skills call it per completed phase. `SkillContext.llm: object | None = None` — the RoleClient (typed object to avoid import cycles, same pattern as `tools`).
- `loop.py` `_dispatch_one` payload gains `"artifacts": result.artifacts` (the ledgered one-liner; cite the P6/P7 carry in the commit body). The sink's existing artifact conversion becomes REACHABLE — its synthetic test gets a real-path sibling.
- `events.py`: the sink exposes `part_emitter(tool_seq)` → callable that pushes a `part` frame immediately and counts; `_handle_tool_done` emits only `parts[n_streamed:]` then proof then artifacts (order pinned). The orchestrator wires `ctx.emit_part` to it per dispatch.
- Frontend: `MetricGridPart` (payload periods + metrics[] → the card grid, theme tokens, tabular-nums) and `ArtifactPart` (name/url/mime → download card) registered for their kinds; vitest renders each from a captured payload shape (fetch_metrics' metric_grid part shape — read the P3 tool's parts output for the real shape).
- Tests: emit_part streams mid-dispatch (frame order pinned: tool_start, part(early), tool_done, part(late-only), proof); no emit_part → identical to today (regression); artifacts forwarded end-to-end (loop → sink → artifact frame); double-emission impossible (count-based skip pinned).

- [ ] Tests FIRST (RED) → implement → GREEN both stacks → ruff + tsc/oxlint. **Commit** — `feat(chat): part-streaming seam, artifact forwarding, and grid/artifact renderers`

---

### Task 2: Research schemas, fixtures, and the brief research subskill

**Files:** the 4 schemas + 4 clean fixtures; `fixture_tool.py` schema-name routing (additive); `subskills/research/subskill.py` (+ its tests inside the skill tree later — this task tests it standalone in `test_brief_subskills.py`).

- Schemas (author fully, minimal v1, same shape discipline as web_research.json): `sustainability` {items[], summary}, `market_position` {items[], summary}, `strategic_profile` {items[], summary}, `operational_profile` {vessel_types[], preferred_ports[], notes, summary}.
- Fixtures: one clean per schema, marine-domain content, ASCII.
- `fixture_tool.py`: routes `schema_name` → `fixtures/{schema_name}_clean.json` (web_research keeps its existing file; document the naming rule); unknown schema fixture → degrade "no fixture for schema" (byte-pinned) — NEVER FileNotFoundError from the fixture tool (it is the demo path; contrast with the loaded-schema scoping of the real transports, document why the fixture tool is softer).
- `research` subskill: `run(ctx, mode, subject) -> SubskillResult(parts, synthesis_inputs, failed: bool)`. Existing mode: three sequential `ctx.tools.research.search` calls (sustainability, market_position, strategic_profile) with queries composed from subject + fixed lens phrases (D30 discipline + sentinel test); prospect mode: operational_profile + web_research. Each call's outcome → one `phase_section` part (markdown from summary + items; degraded → the pinned failure text for that section). `failed` true only when ALL calls degraded.
- Stub-mode note: the subskill itself is LLM-free (search + format); the "then Sonnet synthesis" of doc 02 §4.3 folds into contextualize/strategize consumption (v1 scoping — document).

- [ ] Tests FIRST (RED): schema/fixture round-trips through FixtureResearchTool; both modes' part shapes; per-call degrade honesty; the egress sentinel test; ASCII guards extended. **Commit** — `feat(research): brief research subskill with four structured schemas and fixtures`

---

### Task 3: contextualize + strategize subskills, prompts, stub synthesis

**Files:** `subskills/{contextualize,strategize}/subskill.py`; `prompts/{contextualizer,strategist}.md` (Jinja, `{# version: v1 -#}` first line per T1-P6 precedent); tests in `test_brief_subskills.py`.

- `contextualize.run(ctx, mode, subject, data_block, research_inputs)`: live → `ctx.llm.invoke(role="synthesis", system=rendered prompt, ...)` (RoleClient — read roles.py; the synthesis role exists in models.yml since P5); stub → deterministic template: the pinned opening line + a structured digest of its ACTUAL inputs (subject, metric count, research section count — real data, honest label). Returns one `phase_section` part + text for downstream.
- `strategize.run(ctx, mode, subject, context_text, research_inputs, data_summary)`: the Salesforce CRM field template lives IN the prompt file (AMENDED post-T3-review — ARBITRATION PENDING, morning decision 7: this plan's original bullet instructed authoring a fresh 7-header list (Account Name, Industry, Current Services, Opportunity Summary, Key Contacts Strategy, Risk Factors, Next Steps), contradicting doc 02 §4's "the exact Salesforce CRM field template (ported from agents/strategist.py)" — the legacy template is a nested 5-section/16-field taxonomy incl. Carbon Reduction Goals and Business Drivers/KPIs sections the fresh list lacks. Which list matches the sales team's actual Salesforce schema is Carlos's call; the shipped fresh list stands as documented-divergent v1 until he rules; a contained fix round ports the legacy fields if he picks them — a prompt contract test pins every header present in the rendered prompt); live → synthesis role; stub → template fill with real values where deterministic (subject, services from data) and the pinned placeholder "[requires live synthesis]" elsewhere. One `phase_section` part.
- Failure: live-mode LLM error (stop_reason "error") → the phase's pinned failure text, `failed=True`, brief continues.
- Prompt contract tests: contextualizer prompt mentions the field dictionary + data block placeholders; strategist prompt contains every CRM header byte-exact.

- [ ] Tests FIRST (RED) → implement → GREEN → ruff. **Commit** — `feat(briefs): contextualize and strategize subskills with prompts-as-config and honest stub synthesis`

---

### Task 4: The two brief skills + registry goes to four

**Files:** `existing_customer_brief/{schema.py,skill.py}`; `new_prospect_brief/` full folder (task.yml already covers the task; the new skill dir); `customer_insight/task.yml` → `enabled: true`; the "exactly N skills" test bumps; skill-tree tests `test_brief_skills.py` + co-located tests.

- `existing_customer_brief` Args: `customer: str` (certified value — the orchestrator resolves before dispatch; the skill trusts it), `recency_days: int = 365`. skill.py order (doc 02 §4.1 + concurrency): fetch_metrics + fetch_top_ports (emit metric_grid + table parts IMMEDIATELY via ctx.emit_part) → contextualize ∥ research (ThreadPoolExecutor(2); FIXED consumption order) → strategize → build_brief_pdf (artifact via the T1-forwarded path; artifact store may be None → skip PDF with a pinned proof line, doc 07 dev reality). Proof lines: subject, periods, phases completed/failed, transport, artifact status.
- `new_prospect_brief` Args: `prospect_name: str`, `recency_days: int = 365`. Order (D10): research → contextualize(prospect, consuming research) → strategize("Prospect — no current services" rule pinned in its prompt rendering) → PDF. No internal data tools (nothing exists for a prospect — document).
- SKILL_META for both: descriptions naming the flows + pivot affordances (the router prompt contract test auto-covers them — registry now discovers FOUR skills).
- Concurrency determinism test: a deliberately slow research fake + fast contextualize (and inverted) → byte-identical part order both ways.
- The mechanical test bumps ("exactly two" → four, named ids) — same discipline as P7's, no weakenings.

- [ ] Tests FIRST (RED) → implement → GREEN offline (+ pg where the tools already have pg goldens — re-run) → ruff. **Commit** — `feat(briefs): existing-customer and new-prospect brief skills, registry at four`

---

### Task 5: D19 entry orchestration, dev-router brief branch, E2E, Playwright

**Files:** `orchestrator.py` (entry + subject turns), `live_chat.py` (flow-chip send_texts), `dev_router.py` (brief branch), `routing_cases.yml` (existing_brief_by_name un-gated), `test_entry_orchestration.py`, `test_chat_e2e_scripted.py` (+2 flow scripts), runbook + plan-gate updates, Playwright evidence.

- Flow chips: `{"id": "existing_customer", "label": "Existing customer", "send_text": "start an existing-customer brief"}` (and prospect twin) — the P6 send_text mechanism, now on flow chips too (its scoping note updates honestly).
- Orchestrator entry branch (BEFORE parse_turn's normal path): pinned-phrase match → mode set + subject prompt + clarify finalize (no dispatch, no router). Subject turn: mode set + no completed brief this conversation → existing: resolve via the customer resolver (full ambiguity contract); prospect: text = subject. Then DETERMINISTIC dispatch (registry.dispatch directly; tool events + run-log rows exactly as a routed dispatch — tool_calls row present, llm_calls only from live-mode subskills). A `brief_completed` marker rides ConversationSlots.pass_through? NO — additive would violate parked shapes; use the in-memory ConversationStateStore: a per-conversation `brief_done: bool` alongside slots (the store is P6's, P10 replaces — additive method, sanctioned; document).
- Dev-router brief branch: hints lead `customer_insight.existing_customer_brief` (brief lexemes + mode hints) + resolved customer → tool_use for it ("Run the brief for Maersk" in default mode — the routed path, distinct from D19's deterministic entry). Prospect twin only when prospect mode hint present. `existing_brief_by_name` routing case un-gated with a stub script.
- E2E script A (existing): chip send_text → mode prompt → "Northstar Lines" → brief turn (metric_grid, table, 3+ phase_sections, artifact-or-skip proof, run-log rows: 1 tool_calls for the brief, 0 llm_calls in stub) → pivot "top GP customers for Port of Singapore in April 2026" (routed, works). Script B (prospect): chip → "Meridian Global Shipping" (not in dimension — prospect path proves no resolver) → brief (D10 order visible in part sequence) → research pivot. Both pg-marked, own-ids.
- Playwright: drive both flows in the browser; screenshots (chips, phase sections, the grid, the artifact card or its honest skip) into the workspace; honest capture rules as before.
- Runbook + plan Phase Gate updated with the two flow scripts.

- [ ] Tests FIRST (RED; E2E against local live app first) → implement → GREEN offline + pg → Playwright evidence → ruff. **Commit** — `feat(briefs): deterministic bubble entry, brief routing, and full-flow E2E`

---

## Phase Gate (human validation)

**Verified live at localhost:5173 via Playwright (Task 5); text below updated to match what was actually observed, not the plan's own earlier guess.**

1. localhost:5173 → click "Existing customer" → asked which customer → type "Northstar Lines" → watch the brief stream: metric cards (styled grid, correct) and top-ports table (styled, correct) stream in immediately, then FIVE phase sections (Context, Sustainability & ESG, Market Position, Strategic Profile, Strategy — not three; `existing_customer_brief` always produces one contextualize + three research-lens + one strategize section), then the collapsible "How this was computed" proof ending in its honest skip line (`execute_turn` never wires a real `ArtifactStore` on any path, so a PDF card never renders here, live or offline) → then ask a data question and a news question — both still work with the customer carried.
   - **Honest capture:** the five phase sections do NOT render as formatted cards. `frontend/src/ui/message-parts/registry.tsx` has no renderer for the `phase_section` part kind (Task 1's own sanctioned scope built `metric_grid`/`artifact` only; no task in this phase's plan was ever scoped to add one) — each shows as a collapsed "Unsupported part: phase_section" disclosure that dumps raw `{title, markdown}` JSON when expanded. The data itself is correct (verified via the raw JSON and via the pg E2E scripts' own direct SSE assertions); this is a frontend presentation gap outside Task 5's own edit surface (no frontend files), not a pipeline bug. Screenshots (collapsed and expanded) in `.superpowers/sdd/2026-07-30-phase-8-brief-flows/task-5-screenshots/`.
2. New chat → "New prospect" → type any company (e.g. "Meridian Global Shipping") → research-first brief per D10 — confirmed: the streamed part order is Operational Profile, Web Research (the two prospect-mode research calls), THEN Context, THEN Strategy, matching doc 02's own research-before-contextualize rule exactly (same rendering caveat as item 1 above). A follow-up research question ("any relevant news on Meridian Global Shipping?") still routes normally — but resolves to the WRONG entity: its own "on X" cue fuzzy-matches the typed company name to "Meridian Shipping," a real, unrelated seeded customer (pre-existing `customer_resolver.py` behavior, not a Task 5 bug; live-verified and pinned in `test_new_prospect_brief_flow_scripted_against_live_seeded_postgres`'s own judgment call 5). Picking a prospect name with no fuzzy neighbor in the seeded pool avoids this for a clean demo.
3. `pytest tests/test_chat_e2e_scripted.py -m pg -v` → both flow scripts green incl. run-log row inspection (confirmed: 43 passed, 1 skipped, run three times for stability).

## Self-Review Notes

- Doc-08 P8 coverage: subskills w/ prompts-as-config ✓, D19 deterministic first-turn dispatch ✓ (2-step entry: mode then subject — doc 01 §3's picker affordance noted as a later polish; text prompt v1), concurrent contextualize+research ✓ (deterministic order), phase streaming ✓ (the emit seam — REAL progressive display), prospect D10 ✓, post-brief pivots ✓, artifact part ✓ (the P5-gap one-liner finally lands), progressive display verified ✓ (E2E frame order + Playwright).
- Deliberate scope: live synthesis untested until keys (stub templates honest + labeled); customer_picker part kind unused v1 (text subject prompt); nested per-call tool events → P11; the three sonar model tiers matter only live (fixture path ignores model names — documented).
- Type consistency: SubskillResult local to the subskill package; SkillContext additive fields object-typed per house pattern; parts flow through the P6 emitter contract unchanged.
