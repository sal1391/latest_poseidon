# 02 — Backend: Deterministic Skill Framework

Philosophy (from TM1-Finance-Agent-V2): the LLM's entire output surface is `(tool_name, args)`
plus narrative prose. Everything else — data access, math, validation, formatting, provenance —
is deterministic Python, locked by tests.

## 1. Folder convention (the law of the repo)

Every **task** (business capability) is a vertical slice under `backend/tasks/`. Every task owns
its **skills**; every skill owns its **tools**, **subskills**, **subtools**, **prompts**, and
**tests**.

```
backend/tasks/
  _shared/                       # shared schema fragments (period spec, dim filters) — nothing else
  customer_insight/              # TASK: snake_case business capability
    task.yml                     # manifest: id, title, description, owner, enabled
    skills/
      existing_customer_brief/   # SKILL: one user-facing outcome, router-visible
        skill.py                 # run(ctx: SkillContext, args: Args) -> SkillResult
        schema.py                # class Args(BaseModel); SKILL_META (description, examples)
        prompts/                 # versioned prompt templates for this skill's subskills
          contextualizer.md
          strategist.md
        tools/                   # TOOLS: pure deterministic functions, no LLM calls
          fetch_metrics.py       #   ontology metric queries (prior-year vs YTD)
          fetch_top_ports.py
          build_brief_pdf.py
        subskills/               # SUBSKILLS: internal steps that may call LLM tiers
          contextualize/
            subskill.py          # run(ctx, inputs) -> SubskillResult
            tools/               #   SUBTOOLS: deterministic helpers private to this subskill
              format_data_block.py
          research/
            subskill.py          # Perplexity calls + synthesis
            schemas/             #   structured-output JSON schemas (ported from agents/schemas/)
            tools/
              recover_truncated_json.py
          strategize/
            subskill.py
        tests/
          test_tools.py          # deterministic: golden/snapshot tests on tool outputs
          test_skill.py          # skill run with stubbed LLM + synthetic data
          test_routing.py        # router-decision cases for this skill (doc 03 §6)
      new_prospect_brief/
        ...same shape...
  data_qa/                       # TASK (core): ask-anything Q&A over the certified ontology
    skills/
      metric_query/              # metrics, breakdowns, top-N, period comparisons — one general
        ...same shape...         #   skill; comparison is an argument, not a product shape
  research/                      # TASK (core): external web research
    skills/
      web_research/              # Perplexity with the marine-industry lens (today's researcher
        ...same shape...         #   agent), router-visible in every flow; tools call through
                                 #   the MCP/tool-adapter layer (§7)
  reporting/                     # TASK: the monthly report engine (Phase 17)
    skills/
      monthly_performance/       # internal: dispatched by the report flow, never by the router
        skill.py  schema.py
        tools/                   # periods.py frames.py context.py narrative_template.py
                                 #   grounding.py questions.py narrate.py payload.py render.py
        tests/
      report_lookup/             # router-visible: reads a section of the open report, runs no query
        skill.py  schema.py  tools/  tests/
```

Naming rules: snake_case directories; skill id is `"<task>.<skill>"` (e.g.
`customer_insight.existing_customer_brief`); one skill = one router-exposed capability.

Visibility rules (decision D3): **only `skills/*` are exposed to the LLM router.** Subskills are
invoked in code by their parent skill in a fixed, deterministic order. Tools and subtools never
call the LLM; subskills may, via `ctx.llm` roles (doc 03). This keeps the three-agent sequence a
tested code path, not a model behavior.

A skill whose `SKILL_META["internal"]` is true is registered but withheld from `tool_schemas`, the
router prompt and `/api/skills` (`SkillRegistry.router_skill_ids` is the list the router sees);
`dispatch` and `get` still see it, so a flow can call it by id (Phase 17 PR A1).

## 2. Registration and discovery

`SkillRegistry.discover()` runs at startup:

1. Walk `backend/tasks/*/task.yml`; skip `enabled: false`.
2. For each `skills/<name>/`, import `schema.py` and `skill.py`.
3. Validate fail-fast: unique skill ids; `Args` is a Pydantic model; `SKILL_META.description`
   present and under a length cap; `internal` is a bool; referenced prompts exist.
4. Produce two artifacts:
   - `TOOL_SCHEMAS: list[dict]` — JSON Schemas generated from each `Args` model plus
     description/examples, handed to the router (TM1's canonical-schema pattern; shared fragments
     from `tasks/_shared/` keep wording byte-identical across skills).
   - `SKILL_FNS: dict[str, SkillFn]` — dispatch table.

A registry contract test asserts schema/dispatch parity (every schema has a function and vice
versa) — the drift bug class TM1 guards with `test_snowflake_mode_exposes_render_chart`.

## 3. Contracts between router and skills

```
SkillContext:  user (UserContext, doc 05) · profile (user system instruction + memory doc,
               doc 05 §5) · conversation state (slots, mode) · data (DataClient, doc 04) ·
               llm (role-based model client, doc 03) · tools (external tool adapters via
               backend/poseidon/mcp, §7) · artifacts (object-store client; absent on SPCS per
               D39 — report bytes go to Postgres, see doc 07 §4) · run (run-log recorder,
               doc 06) · settings · report_store (ReportStore over report_run, doc 05 §4) ·
               prompt_tree (PromptTree over backend/prompts/, doc 03 §4)
SkillResult:   ok · parts: list[MessagePart]   (typed parts of doc 01 §4)
               proof: list[str]                (deterministic provenance block)
               artifacts: list[ArtifactRef] · usage: TokenUsage · error: ProblemDetail | None
```

Dispatch rules (TM1's `_execute_tool` pattern, hardened):

- Router emits `ToolCall(name, arguments)`. The dispatcher validates `arguments` against the
  skill's `Args` model **before** execution; validation failure returns a structured error to the
  router loop (never an exception to the user).
- Unknown tool name → structured `{ok: false, error}` back to the router.
- Every dispatch is recorded in the run log with args and outcome, whether it succeeds or not.
- Skills return `ProblemDetail` (RFC 7807 shape) for business failures; the API layer maps them
  to HTTP problem responses.
- Every LLM call — router or subskill — assembles its prompt in the fixed order of doc 03 §3:
  base system prompt → user system instruction → user memory document → conversation state.

## 4. The three flows, mapped from the current apps

**Entry orchestration rule (decision D19).** The conversation's flow decides only how the first
deliverable is produced. Bubble entries dispatch their brief skill **deterministically** on the
first turn (mode + subject are already known — no router call needed); the default flow routes
from turn one. From the second turn onward all flows are identical: the router sees the full
registry (`data_qa.metric_query`, `research.web_research`, both briefs), with carry-over context
(active customer, port, period) injected from conversation state (§5).

**`data_qa.metric_query`** (core; conversational mechanics from mom-comparison, deterministic
implementation): the parsed turn (periods, resolved dimension values, metric names) becomes a
`MetricQuerySpec`/`BreakdownQuerySpec` executed by the query builder (doc 04). "What are my top
GP customers for Port of Singapore in April 2026" → breakdown spec (metric `GP`, group-by
`CUST_NM`, filter `LOC_NM`, period April 2026) → `table` part + proof block, values stored for
exact pass-through. Ambiguity produces clarification chips, never guesses.

**`data_qa.metric_query` extensions (Phase 17 PR A2).** The skill takes `scope: "office" | "book"`
(default `"office"`). With a carried office (section 5) and `scope="office"`, `metric_query`'s `skill.run`
stamps the office filter on the office column, replacing any model-supplied filter on that
column; it is not applied when the column is not a certified dimension of the entity, and the
proof says so. `scope="book"` drops the carried office and says that too. The proof's last line
names the outcome: `Office scope: {col} = {value} (carried; scope=office)`, `Office scope: book
(carried office not applied)` or `Office scope: {col} is not a dimension of {entity}; carried
office not applied` (D72). A breakdown with a comparison runs each window's top-50 breakdown and
merges them; when GP and VOLUME are both requested every comparison table gains a Driver column
(D73).

**`reporting.report_lookup`** (router-visible; Phase 17 PR A2): reads one section of the report
open in the conversation (`kpis`, `entities_by_gp`, `entities_by_volume`, `ports_by_gp`,
`ports_by_volume`, `deal_class`, `movers`, `new_lost`, `narrative`, `context`) from the stored
`report_run` row named by `slots.report_ref` through `SkillContext.report_store`, with `limit`/`offset`
paging. It runs no query, so every figure it returns is one the report already showed. With no
open report it answers a text part ("open a report first", `ok=true`) that the router relays
(D72).

**`reporting.monthly_performance`** (internal, D70) is the third flow's engine and is never
chosen by the router; the report flow dispatches it deterministically (below).

**The monthly report flow** (decisions D63, D64, D71, D74; `core/chat/report_flow.py` is the
pure step module). The opener chip "Monthly report" sends `start a monthly report`
(`ENTRY_PHRASE_REPORT`); a second click resets the flow slots. Three steps, each validated
before any dispatch, each re-asking as a clarify turn (a text part plus chips, no tool frames)
when the answer is not usable (D71):

1. **Perspective:** `customer`, `supplier` or `book` (chips `customer office report`,
   `supplier office report`, `whole book report`). `book` skips the office step.
2. **Office:** chips from the certified office dimension; typed answers match
   case-insensitively; an unknown office re-asks with the chips.
3. **Month:** chips of the latest complete months, or a typed month ("June 2026"). Month to
   date is recognised by the step itself (D64 phrases, or naming the running month), never by
   the period parser; a month that is not complete or has no data re-asks.

On the last step the orchestrator dispatches `reporting.monthly_performance` and the turn's
wire order is `accepted`, `tool` start and done, the `report` part, the `proof` part, the
`artifact` part (omitted when the PDF could not be rendered; the HTML is then the durable
artifact and a flag says why), one `chips` part with three suggested questions (D74), `done`.
A failed dispatch keeps the flow at the month step. The report run is stored once
(`report_run`, with `conversation_id`), the mode returns to `default`, and the report's
perspective, office and run id are carried in the slots so follow-ups answer from them.

**`research.web_research`** (core; today's researcher agent, generalized): a focused Perplexity
query with the marine-fuels/shipping-services lens and the existing structured-output schemas,
callable in any flow — e.g. after the Singapore answer, "any relevant news on customer X I
should be aware of?" routes here with `customer` carried from state. Calls go through the tool
adapter layer (§7) and stream verbose `tool_event` steps (doc 01 §4).

The two brief flows live in task `customer_insight` and stream each phase as it completes
(today's progressive display, preserved). After the brief, pivots are first-class: a prospect
brief naming a port invites "which existing customers do we already serve at that port?"
(→ `data_qa.metric_query`); an existing-customer brief invites port/lane/metric drill-downs and
real-time external questions (→ `research.web_research`).

**`existing_customer_brief`** (source: `app.py` lines 146–323, `agents/*`):

1. Tool `fetch_metrics` + `fetch_top_ports` — the six certified metrics (VOLUME, GP, MARGIN,
   NUM_WON, NUM_INQUIRIES, NUM_LOST) for prior calendar year vs YTD, plus top-5 ports, built from
   the ontology (doc 04). Emits `metric_grid` + `table` parts immediately.
2. Subskill `contextualize` — Sonnet synthesis over the internal data block + the ontology field
   dictionary (prompt ported from `agents/contextualizer.py::agent_contextualizer`).
3. Subskill `research` — three Perplexity structured calls (sustainability/ESG on
   `sonar-deep-research`, market position on `sonar`, strategic profile on `sonar-pro`) with the
   existing JSON schemas (`agents/schemas/*`) and truncated-JSON recovery, then Sonnet synthesis
   (ported from `agents/researcher.py`).
4. Subskill `strategize` — Sonnet fills the exact Salesforce CRM field template (ported from
   `agents/strategist.py`), consuming 2 + 3 + the internal data summary.
5. Tool `build_brief_pdf` — brief rendered to PDF, stored to S3, emitted as an `artifact` part
   (markdown fallback preserved).

**`new_prospect_brief`** (source: `app.py` lines 329–412, `agents/orchestrator.py`):

1. Subskill `research` first (no internal data exists for a prospect).
2. Subskill `contextualize` in prospect mode — Perplexity operational profile (voyages, vessel
   types, IMO numbers, preferred ports; schema `operational_profile`) plus the research output.
3. Subskill `strategize` with "Prospect — no current services" rules.
4. `build_brief_pdf` artifact.

Decision D10: prospect ordering follows `orchestrator.py` (research feeds contextualize), not
`app.py` (which passes `None`) — the prospect contextualizer has no internal data and is strictly
better informed by research; this matches the already-tested orchestrator path.

Concurrency: for existing customers, `contextualize` and `research` run concurrently (as
`orchestrator.py::run_agents_existing_account` already does); `strategize` awaits both.

## 5. Deterministic parsing pipeline (before any LLM call)

Runs on every inbound chat message; output is a `ParsedTurn` attached to the router request and
recorded in the run log. Functional semantics come from mom-comparison; the implementation is
deterministic Python (the upgrade — mom-comparison delegated extraction to the LLM and regex-
parsed sentinel blocks out of its replies).

| Stage | Behavior | Provenance |
|-------|----------|------------|
| `normalize` | trim, unicode NFC, collapse whitespace | new |
| `period_parser` | resolve date phrases ("March 2025", "vs last year", "YTD", quarters) into `{period_a, period_b}` half-open windows (stored as first-of-period start plus exclusive end, D62); **carry-over**: an unspecified side inherits its stored window from conversation state, the comparison included, until a single period or a new comparison is named; result **validated against available periods** from the data layer, with a ranged "available data" message on miss | semantics: `mom-comparison/app/agent.py` rules 10–12 + `_extract_new_dates` (:407) + membership test against `get_available_months` (`app/snowflake_client.py:25`) |
| `customer_resolver` | resolve customer mentions against the customer dimension via a 3-tier match: exact/alias → token-set → fuzzy (rapidfuzz), with confidence thresholds `>=0.80` auto-apply, `0.60–0.80` → clarification chips, `<0.60` → no match | mechanism: TM1 `services/dimension_service.py` (tested by `test_dimension_service.py`); fixes mom-comparison's verbatim `CUST_NM = '<user text>'` exact-match fragility |
| `skill_hinter` | keyword/intent lexicon produces a ranked candidate-skill shortlist and mode hints; hints are advisory context for the router, never a hard dispatch | replaces mom-comparison's prose "routing tables" (`skills/SKill.md` pattern-selection) with a deterministic shortlist |

Slot carry semantics across turns (TM1 `ConversationBuffer`, adopted verbatim): omitted slot →
carry previous; explicit empty → clear; new value → **replace, never merge**. Stored in
`conversations.state` (doc 05) so a resumed chat parses identically.

Five report slots ride in `ConversationSlots` and `conversations.state` (Phase 17 PR A2):
`perspective` (`customer | supplier | book`), `office_column`, `office_value`, `report_ref`
(the open report's run id) and `report_label` (its scope and months, e.g. `Gibraltar Office, June 2026 vs May 2026`). The report flow writes them; `data_qa.metric_query` reads the office
pair for `scope` stamping and `reporting.report_lookup` reads `report_ref`. They follow the
carry rules above and are rendered into the router's state block (doc 03 section 3).

Cross-turn value pass-through (mom-comparison's strongest idea, kept): when a skill returns
ranked entities (e.g. top movers), the exact values are stored in conversation state and injected
into the next router call as structured context — the model copies values, it never re-derives
them from prose history (`_extract_top_values`, `app/agent.py:640`).

Guardrails at the SQL boundary (defense in depth even though the LLM writes no SQL): dimension
values whitelisted against `DataClient.list_dimension_values` (TM1 `_validate_dim_args`), and the
ontology's `negative_constraints` (21 observed hallucinated column names, doc 04) drive both
router prompt warnings and a final identifier lint on any generated query spec.

## 6. Failure design (anti-happy-path)

Each skill documents and tests at minimum: (a) empty result sets — proof block states
"Result: empty" with a did-you-mean from the resolver, never a hallucinated narrative; (b)
upstream API failure (Perplexity/LLM provider) — the phase fails, previously streamed
deterministic parts stand, the run log records the partial turn; (c) concurrent duplicate
submission — turn creation is idempotent on `(conversation_id, client_turn_key)`; (d)
invalid/ambiguous parse — clarification chips, not guesses.

## 7. External tool servers (the MCP layer)

External tools get a first-class integration pattern so new servers can be added over time
without touching skills:

```
backend/poseidon/mcp/   # NOTE (P7 build): shipped inside the poseidon package (import poseidon.mcp.*),
                        #   not top-level backend/mcp/ — a top-level `mcp` package would shadow the
                        #   PyPI `mcp` SDK if a later phase installs it. Same layout otherwise.
  registry.py          # ToolServerRegistry: config-driven discovery, health, timeouts
  perplexity/          # first integration
    adapter.py         # direct REST adapter (wfs_core PerplexityClient pattern: json_schema
                       #   response_format, truncated-JSON recovery, degrade-to-None)
    mcp_client.py      # MCP-transport client for Perplexity's MCP server
    schemas/           # structured-output schemas shared by both transports
```

- Skills never import a vendor SDK; they call a typed tool interface
  (`ctx.tools.research.search(...)`) that the registry resolves to a transport.
- **Decision D23:** transport per server is config (`TOOL_TRANSPORT_PERPLEXITY=direct|mcp`);
  the **direct API adapter is the default** — it is the proven in-house path (schema-pinned
  structured output plus truncated-JSON recovery already in production), while the MCP client
  ships as the standing pattern for future servers where MCP is the native surface.
- Every tool invocation emits a `tool_event` (start/done/error with a human-readable label,
  doc 01 §4) and a `tool_calls` row (doc 06 §1) regardless of transport.
- **Egress constraint (decision D30, doc 05 §7):** the research adapter composes its query from
  parsed entity slots — customer, port, region, topic — plus the user's own question text, and
  never from a metric result. No internal value computed from the certified views may appear in an
  outbound research call; a contract test asserts it.
