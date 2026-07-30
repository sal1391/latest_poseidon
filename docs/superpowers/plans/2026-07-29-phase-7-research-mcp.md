# Poseidon Phase 7: Web Research Skill + MCP Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The external-research layer (doc 08 P7): `backend/poseidon/mcp/` registry + Perplexity direct adapter (json_schema response_format, truncated-JSON recovery, degrade-to-None) + MCP-transport client behind `TOOL_TRANSPORT_PERPLEXITY` (D23, direct default), the `research.web_research` skill with the marine-industry lens, verbose tool events, and pivot routing ("any relevant news on customer X?" after a data answer). **No Perplexity key exists tonight: everything green runs on recorded fixtures; live calls sit behind a `research_live` marker that skips cleanly** (the key is item 2 on Carlos's needs-keys list).

**Architecture:** Doc 02 §7 verbatim where it specifies. AMENDED (post-T1): the package lives at `backend/poseidon/mcp/` (import path `poseidon.mcp.*`), NOT doc 02's literal top-level `backend/mcp/` — a top-level package named `mcp` would shadow the real PyPI `mcp` SDK the moment any later phase installs it (P14/SPCS plausibly will), an in-venv name collision that would surface at the worst possible time; moving inside the poseidon package kills the collision permanently, keeps the "mcp" naming intent, and ships with the package (no extra packaging entry). Doc 02 §7 carries a one-line note. Skills never import a vendor SDK — they call a typed interface `ctx.tools.research.search(...)` that a `ToolServerRegistry` resolves to a transport (direct REST adapter default; MCP client as the standing pattern). `SkillContext` gains a `tools` member (additive, doc 02 §4's own context list names it). The egress constraint D30 is law: outbound research queries compose ONLY from parsed entity slots (customer, port, region, topic) — never from metric results — and a contract test asserts it. The dev router's case (c) upgrades to dispatch `research.web_research` when hints lead with it and a subject slot exists, so the offline demo answers research pivots with fixture content.

**Tech Stack:** Existing backend. New runtime dep: `httpx>=0.27` (already a test dep — promote to `[project]`). New pytest marker: `research_live`.

## Global Constraints

- **Offline by default:** no test outside `-m research_live` opens a network connection. The direct adapter is tested against recorded/hand-authored Perplexity response payloads (including truncated-JSON cases); the MCP client against a scripted fake wire. `research_live` = one smoke, skip-guard on `PERPLEXITY_API_KEY`, pinned reason "no Perplexity API key".
- **D23:** `TOOL_TRANSPORT_PERPLEXITY: Literal["direct","mcp"] = "direct"` in Settings (defaulted). The transport-flip contract test proves both transports produce the IDENTICAL typed result shape from equivalent recorded inputs (doc 08's validation criterion).
- **D30 egress (contract-tested):** the skill builds its outbound query from a whitelist — `customer`, `port`, `region`, `topic`, plus the user's own question text — and NOTHING else. No number, no metric name + value pair, no table row content may reach the outbound payload. The contract test feeds slots + a fake prior metric result through the skill and asserts the outbound query (captured by a recording transport) contains none of the planted sentinel values.
- **Tool events + rows regardless of transport (doc 02 §7):** every research invocation emits `tool_event` start/done/error with a human label ("Searching the web — marine industry lens…") through the EXISTING loop/tool_done machinery (the skill runs inside `registry.dispatch` like any skill; its INTERNAL Perplexity call additionally surfaces as a labeled step — v1: the skill's proof lines carry transport + query + result count; a dedicated nested tool_event for the HTTP call itself is P11 observability polish, documented).
- **Degrade-to-None discipline (wfs_core pattern):** adapter failures (timeout, HTTP error, unparseable-after-recovery) return None/empty-typed results with a reason — the SKILL then produces an honest "research unavailable" text part + proof line; the turn NEVER errors because an external vendor hiccuped. Pinned both layers.
- **Truncated-JSON recovery:** the adapter repairs the classic truncation (close open strings/brackets/braces in order) before parsing; unrecoverable → degrade. Pin with at least 3 truncation shapes (mid-string, mid-array, mid-object) + 1 unrecoverable.
- **Skill framework law (P3):** `tasks/research/task.yml` (enabled: true) + `skills/web_research/{schema,skill}.py + tools/ + tests/` — same folder law, SKILL_META, pydantic Args, parts as dicts, proof lines, exception-safe dispatch untouched.
- **Parked decisions stay parked.** P6 later-phase routings stay routed (artifact forward P8 etc.). Do not modify P2-P6 modules EXCEPT the sanctioned additive items: `SkillContext.tools` field (defaulted None), `core/config.py` Settings additions, dev_router case-(c) research upgrade + its tests, `tests/routing_cases.yml` pivot case un-gated to stub execution.
- ASCII .py; frozen dataclasses; byte-pinned messages; deterministic; docstrings explain WHY; ruff clean; conventional commits on `phase-3-8-overnight`; trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` every commit.
- Baselines at plan time: backend 1003 offline / 41 pg / frontend 31; zero failures the bar. The live compose stack is up (CHAT_MODE=live).

## File Map

```
backend/poseidon/mcp/__init__.py
backend/poseidon/mcp/registry.py                   # ToolServerRegistry + typed ResearchTool protocol + ToolInvocation result
backend/poseidon/mcp/perplexity/__init__.py
backend/poseidon/mcp/perplexity/adapter.py         # PerplexityDirectAdapter (httpx, json_schema response_format,
                                          #   truncated-JSON recovery, degrade-to-None, timeouts)
backend/poseidon/mcp/perplexity/mcp_client.py      # PerplexityMcpClient (same typed surface, scripted-wire tested)
backend/poseidon/mcp/perplexity/schemas/web_research.json   # structured-output schema (shared by both transports)
backend/poseidon/mcp/perplexity/fixtures/*.json    # recorded/hand-authored response payloads (incl. truncated)
backend/poseidon/tasks/research/task.yml  # enabled: true
backend/poseidon/tasks/research/skills/web_research/{schema.py,skill.py}
backend/poseidon/tasks/research/skills/web_research/tools/build_query.py   # D30 whitelist composer
backend/poseidon/tasks/research/skills/web_research/tools/format_parts.py  # sources table + summary text + proof
backend/poseidon/tasks/research/skills/web_research/tests/test_skill.py
backend/poseidon/core/skills/context.py   # + tools: object | None = None (additive, sanctioned)
backend/poseidon/core/config.py           # + tool_transport_perplexity, perplexity_api_key (both defaulted)
backend/poseidon/core/chat/dev_router.py  # case (c) research upgrade (sanctioned)
backend/poseidon/api/app.py               # live wiring: ToolServerRegistry + fixture transport in dev
backend/tests/test_mcp_registry.py
backend/tests/test_perplexity_adapter.py  # recorded payloads + truncation + degrade + research_live smoke
backend/tests/test_perplexity_mcp_client.py + transport-flip contract test
backend/tests/routing_cases.yml           # pivot_to_research_with_carry un-gated (stub script added)
backend/pyproject.toml                    # + httpx runtime pin + research_live marker
```

---

### Task 1: ToolServerRegistry + typed interface + SkillContext.tools + Settings

**Files:** `backend/poseidon/mcp/{__init__,registry}.py`; `core/skills/context.py` (additive); `core/config.py` (additive); pyproject (marker + httpx promote); test `test_mcp_registry.py`.

**Interfaces (exact):**

```python
# backend/poseidon/mcp/registry.py
@dataclass(frozen=True)
class ResearchResult:
    items: tuple[dict, ...]          # schema-validated result objects (title/url/snippet/…)
    raw_digest: str                  # count + transport summary for proof lines, never payload
    transport: str                   # "direct" | "mcp" | "fixture"
    degraded: bool = False
    degrade_reason: str | None = None

class ResearchTool(Protocol):
    def search(self, *, query: str, schema_name: str, recency_days: int | None = None) -> ResearchResult: ...

class ToolServerRegistry:
    def __init__(self, settings: Settings, overrides: Mapping[str, object] | None = None): ...
    @property
    def research(self) -> ResearchTool: ...   # resolves per TOOL_TRANSPORT_PERPLEXITY; overrides win (tests/dev)
```

Rules pinned: unknown transport value → RuntimeError f"unknown research transport {value!r} — expected 'direct' or 'mcp'"; resolution is lazy (no adapter construction until first `.research` access — offline boots never build HTTP clients); `overrides={"research": obj}` injects fixtures/fakes (the dev/test seam, mirroring RoleClient's provider registry pattern). Settings additions (defaulted): `tool_transport_perplexity: Literal["direct","mcp"] = "direct"`, `perplexity_api_key: str | None = None`. `SkillContext` gains `tools: object | None = None` — additive, frozen-compatible, documented (typed as object to avoid a core→mcp import cycle; skills cast via the protocol).

- [ ] Tests FIRST (RED): registry resolution table (direct/mcp/unknown/override), laziness (no construction on init), SkillContext.tools defaulted None + existing tests untouched, marker registered. GREEN → ruff. **Commit** — `feat(mcp): tool server registry with typed research interface and transport config`

---

### Task 2: Perplexity direct adapter — recovery, degrade, fixtures, live smoke

**Files:** `backend/poseidon/mcp/perplexity/{__init__,adapter}.py`; `schemas/web_research.json`; `fixtures/*.json`; test `test_perplexity_adapter.py`.

Adapter contract (wfs_core PerplexityClient pattern, pinned branch by branch):
- `PerplexityDirectAdapter(api_key, model="sonar", timeout_s=30.0, client=None)` — httpx client injectable; real one lazy.
- Request: POST `https://api.perplexity.ai/chat/completions` with `response_format={"type":"json_schema","json_schema":{"schema": <web_research.json>}}`, the marine-lens system line ("You research the marine fuels and shipping-services industry. Answer strictly in the requested JSON schema."), user content = the query. `recency_days` → `search_recency_filter` mapping (7→"week", 30→"month", 365→"year", None→omit; pin the mapping).
- Response: `choices[0].message.content` → JSON parse → on failure, TRUNCATION RECOVERY: scan-and-close (in order: unterminated string gets a closing quote, then unclosed brackets/braces closed innermost-first), re-parse; still failing → degrade. Validate against the schema's required keys; items normalized to the ResearchResult shape.
- Degrade-to-None: timeout / non-2xx / unrecoverable parse → `ResearchResult(items=(), raw_digest=..., transport="direct", degraded=True, degrade_reason=<pinned short reason>)` — NEVER raises.
- `web_research.json` schema (author fully): object {"items": [{"title": str, "url": str, "snippet": str, "relevance": str}], "summary": str} — minimal v1, versioned by filename.
- Fixtures: one clean response, three truncated variants (mid-string/mid-array/mid-object — each recoverable), one unrecoverable, one HTTP-500, one timeout (raised by fake client). Each pinned end-to-end through the adapter.
- `research_live` smoke: skip-guard `PERPLEXITY_API_KEY` env, pinned reason "no Perplexity API key"; asserts shape only.

- [ ] Tests FIRST (RED with recorded payloads) → implement → GREEN → ruff; verify `-m research_live` skips with the pinned reason. **Commit** — `feat(mcp): perplexity direct adapter with truncation recovery and degrade-to-none`

---

### Task 3: MCP-transport client + transport-flip contract test

**Files:** `backend/poseidon/mcp/perplexity/mcp_client.py`; test `test_perplexity_mcp_client.py`.

`PerplexityMcpClient(wire, schema_dir)` — `wire` is a callable/protocol `send(method: str, params: dict) -> dict` (the JSON-RPC seam; a real stdio/websocket wire is deploy-phase work — the CLIENT owns request shaping + response normalization, tested against a scripted fake wire; document this boundary honestly in the module docstring). Calls `tools/call` with `{"name": "perplexity_search", "arguments": {...}}` per the MCP tool-call shape; normalizes the MCP content envelope (text content → JSON parse → same schema validation path as the direct adapter — REUSE the adapter's parse/validate/recover helpers by importing them, no duplication) into the SAME ResearchResult. Degrade rules identical.
THE TRANSPORT-FLIP CONTRACT TEST (D23's proof): equivalent recorded inputs through both transports → `ResearchResult` objects that are EQUAL except the `transport` field (parametrized over the clean fixture + one truncated + one degraded case).

- [ ] Tests FIRST (RED) → implement → GREEN → ruff. **Commit** — `feat(mcp): perplexity mcp-transport client with transport-flip contract`

---

### Task 4: research.web_research skill + dev-router pivot + routing case + live-path E2E

**Files:** `tasks/research/**` (task.yml, schema.py, skill.py, tools/{build_query,format_parts}.py, tests/test_skill.py); `core/chat/dev_router.py` (sanctioned); `tests/routing_cases.yml`; `api/app.py` (wire ToolServerRegistry into SkillContext construction — fixture transport override in local dev when no key); `tests/test_chat_e2e_scripted.py` (one pivot turn appended).

Skill contract:
- Args (pydantic): `question: str` (the user's research question, required); `customer: str | None`; `port: str | None`; `region: str | None`; `topic: str | None`; `recency_days: int | None = 30`. SKILL_META description names the marine lens and the pivot pattern.
- `build_query.py`: D30 whitelist composer — f-strings ONLY over the six Args fields; subject clause from customer/port/region ("about {customer}" etc., joined deterministically), lens suffix fixed. The egress contract test (in the skill's tests): plant sentinel values in a fake prior context (metric numbers, table rows) → recording ResearchTool captures the outbound query → assert sentinels absent, whitelisted fields present.
- `skill.py`: `ctx.tools` cast to the protocol; absent tools or degraded result → honest parts (text "External research is unavailable right now — {reason}." + proof line "Research: degraded ({reason})"); success → parts: text (the summary), table (columns Title/Source/Relevance, rows from items, url as source), proof lines ("Query: {query}", "Transport: {transport}", "Results: {n}").
- Dev-router case (c) upgrade (its docstring's endgame pattern continues): hints lead with `research.web_research` AND (customer OR port resolved-or-carried in the state block) → ONE tool_use for research.web_research with question = the user message + the subject args from the block; second invoke → end_turn "Research summary for {subject} — {n} sources." Hints NOT leading research → existing behavior unchanged. Tests both directions + the existing capability-message case updated only where the research path now fires.
- `routing_cases.yml`: `pivot_to_research_with_carry` gains a stub script + loses `live_only` (the stub executor now runs it end-to-end through the REAL registry with a fixture ResearchTool override).
- E2E: append turn 5 to the scripted conversation — "any relevant news on Northstar Lines I should be aware of?" (after turn 1-3 context) → research skill dispatched, sources table + summary parts, run-log rows (2 llm_calls, 1 tool_calls naming research.web_research), fixture transport digest in proof. pg-marked, own-ids discipline.
- app.py wiring: local dev with no `PERPLEXITY_API_KEY` → ToolServerRegistry override installs a FixtureResearchTool (reads the clean fixture; transport "fixture") so the LIVE CHAT answers research pivots offline — boot log line discloses which transport is active.

- [ ] Tests FIRST (RED) → implement skill → dev-router → routing case → E2E → GREEN offline + pg → ruff. **Commit** — `feat(research): web_research skill with marine lens, egress contract, and offline pivot`

---

## Phase Gate (human validation)

1. localhost:5173 (stack up): after the Singapore turn, type "any relevant news on Northstar Lines I should be aware of?" → verbose step ("Searching the web — marine industry lens…"), a sources table + summary render (fixture content tonight — real Perplexity the moment your key lands in .env as PERPLEXITY_API_KEY).
2. `pytest tests/test_perplexity_adapter.py -v` → truncation-recovery and degrade branches visible by name; `pytest -m research_live` → skips "no Perplexity API key".
3. The egress proof: `pytest backend/poseidon/tasks/research -k egress -v` → the D30 contract test green.

## Self-Review Notes

- Doc-08 P7 coverage: mcp registry ✓, direct adapter (schemas, truncated-JSON recovery) ✓, MCP client behind TOOL_TRANSPORT_PERPLEXITY ✓ (wire seam documented as deploy-phase), web_research skill w/ lens ✓, verbose labels ✓ (proof-line transport visibility; nested per-HTTP tool_event → P11 note), pivot routing ✓ (dev router + routing case + E2E), transport-flip contract ✓, recorded-fixture validation ✓.
- Deliberate scope: no live Perplexity call without the key (research_live gated); no brief flows (P8 consumes these same adapters via subskills); MCP wire transport implementation deferred to the phase that has a real MCP server to talk to; egress classification beyond D30's whitelist (doc 05 §7 redaction) is P11.
- Type consistency: ResearchResult/ResearchTool flow T1→T4; SkillContext.tools object-typed to avoid core→mcp cycle; adapter parse helpers reused by mcp_client (no duplication).
