# 04 — Data Layer and Ontology

## 1. The ontology is the single source of truth

The certified semantic layer `ontology.yml` (from `wfs_work_structure/app-workspace/ontology/`,
592 lines) governs the entire data layer: schemas, metric SQL, guardrails, and synthetic data all
derive from it. Its lifecycle is *machine proposes → human certifies → agents consume*; presence
in the file **is** certification. The app repo vendors a pinned copy under `ontology/ontology.yml`
and records its source revision (decision D13: vendored + pinned, so the app builds are
reproducible and ontology upgrades are explicit diffs).

### Certified entities (as of 2026-07)

**`MARINE_SALES_PLANNING_V`** (`SANDBOX.MCA`, view) — flat sales fact, grain: one row per marine
fuel transaction (POI), date column `LIFT_ETA_DATE`, identifier `POI_ID`.

- 22 columns: measures `FIXED_TONS`, `GROSS_PROFIT`, `"#_FIXTURES"`, `"#_INQUIRIES"` (the last
  two require double-quoting); dimensions `CUST_NM`, `SUPPLIER_NM`, `LOC_NM` (port, default
  breakdown), supply-team/broker hierarchy columns (`SUPP_BRKR → PRIMARY_SUPPLY_TEAM_OFFICE →
  ..._REGION`; `PRIMARY_BRKR → OFFICE → REGION`; `CUSTOMER_BRKR → CUSTOMER_TEAM_NAME →
  CBO_REGION`), `DEAL_CLASSIFICATION_TRADE_CUT`, and the two ship-type group columns.
- 7 certified metrics with exact SQL: `VOLUME`=SUM(FIXED_TONS) · `GP`=SUM(GROSS_PROFIT) ·
  `MARGIN`=SUM(GP)/NULLIF(SUM(FIXED_TONS),0) (**never** sum/average a margin column) ·
  `NUM_WON` · `NUM_INQUIRIES` · `NUM_LOST` (derived) · `WIN_RATE` (diagnostic, small-sample
  guard `HAVING SUM("#_INQUIRIES") >= 5`).
- 21 **observed** `negative_constraints` — real hallucinated identifiers from shipped apps
  (`PORT_NM/PORT_NAME/PORT → LOC_NM`, `TONS/TONNAGE → FIXED_TONS`, `FIXTURES → "#_FIXTURES"`,
  `PROFIT/GP_AMOUNT → GROSS_PROFIT`, ...), plus 8 business rules and disambiguations.

**`W_MARINE_GL_SOURCE_AI`** (table) — TM1 GL export, grain: one row per GL string per period
(~21.7k strings). 7-level `CLASS6_Calc → ... → CLASS1` hierarchy + 6 orthogonal dims (`COMPANY`,
`OFFICE`, `DEPARTMENT`, `ACCOUNT`, `BROKER`, `FUTURE`) qualifying a single dual-purpose
`AMOUNT_USD` measure. Mandatory guard: monetary aggregation must exclude
`CLASS4 = 'Volume'` (tons/gallons rows). `PERIOD_DATE` is VARCHAR (wrap in `TO_DATE`). Known
hierarchy cycle hazards documented ("Profit and Loss" at two levels) — traversal requires a
visited set. 17 anticipated negative constraints, 13 business rules.

**`AR_INVOICES`** — `status: planned` stub, zero columns. Exists so bindings can name it; it is
the template for how future tables arrive.

**Relationships: zero certified.** The two real entities have incompatible grains and do not
join; the loader must not invent joins. (The harness README quantifies why: 30–70 % of inferred
FK proposals are false positives.)

## 2. Ontology loader

`backend/core/ontology/loader.py` parses the YAML into typed objects:

```
Ontology
  entities: {name -> Entity(fqn, grain, date_column, columns, metrics,
                            negative_constraints, business_rules, hierarchy?)}
  Column(name, type, role: identifier|measure|dimension|date, friendly, quoted, agg)
  Metric(name, sql, guards, description)
```

Consumers: query builder (metric SQL), parsing pipeline (dimension values, period bounds),
router prompts (metric definitions + negative constraints), synthetic generator (schemas),
skill schemas (`tasks/_shared` fragments). A loader contract test pins the certified entity/
metric inventory so an ontology upgrade is a conscious, test-visible event.

## 3. Data-access interface (the adapter seam)

```python
class DataClient(Protocol):
    def list_dimension_values(self, entity: str, column: str,
                              search: str | None = None) -> list[str]: ...
    def available_periods(self, entity: str) -> PeriodRange: ...
    def run_metric_query(self, spec: MetricQuerySpec) -> MetricResult: ...
    def run_breakdown_query(self, spec: BreakdownQuerySpec) -> BreakdownResult: ...
```

- `MetricQuerySpec` = entity + metric names + period window + validated dim filters + optional
  group-by/top-n. Specs are built by skills from parsed slots; **the LLM never authors SQL**.
- One shared `query_builder` renders specs to SQL from the ontology's certified metric
  expressions, parameterized (no string-interpolated literals — fixes the `'' `-escaping pattern
  in all three source repos). Snapshot tests pin the rendered SQL byte-for-byte (TM1
  `test_query_builder_snapshot.py` pattern), including error strings.
- Dialect: the builder emits an ANSI core with a small dialect hook (identifier quoting,
  `TO_DATE` handling) per adapter. The certified metric SQL is valid in both target engines.

Adapters (selected by `DATA_BACKEND` env, decision D4):

| Adapter | Engine | Use |
|---------|--------|-----|
| `SyntheticDataClient` | local Postgres schema `synthetic` (same instance as app data) | default for development, demos, CI, and all deterministic tests — standard local-development practice |
| `SnowflakeDataClient` | **Snowflake** — the production data platform; the certified ontology entities are Snowflake views/tables (`SANDBOX.MCA.MARINE_SALES_PLANNING_V`, `W_MARINE_GL_SOURCE_AI`) | production; comes online at the user-testing phase (doc 08) |

`SnowflakeDataClient` follows the proven wfs_core session pattern: one `get_session()` whose
connection strategy is keyed by `DEPLOY_MODE` (doc 07 §6) — password config locally, Secrets
Manager on EC2, and inside SPCS the auto-mounted OAuth token read fresh from
`/snowflake/session/token` on every connection. All execution passes a SELECT/WITH-only guard
(the wfs `execute_query` rule) — the app's Snowflake role is read-only by construction and by
grant.

TM1 proved this dual-backend seam (`make_test_tools` vs `make_snowflake_tools`); the improvement
here is that both adapters share one query builder, so there is no hand-synchronized logic pair.

## 4. Synthetic data adapter (local/synthetic pattern)

Purpose: a realistic, self-contained dataset conforming exactly to the certified schemas — the
standard local-development practice: the full application runs and is testable anywhere with no
Snowflake connectivity, and flipping `DATA_BACKEND=snowflake` later touches no skill code.

Design:

- `ontology/synthetic/profiles.yml` — per-entity generation profile: row counts, date window
  (rolling: prior year + YTD so the period logic always has data), value pools and distributions.
- Generator (`scripts/generate_synthetic.py`) is **seeded and deterministic** (default seed
  committed): same seed → same dataset → stable snapshot tests and reproducible demos.
- Realism rules for `MARINE_SALES_PLANNING_V`: a curated pool of plausible shipping-company
  names, real port names for `LOC_NM`, per-customer port affinity (a customer concentrates in
  3–8 ports), log-normal-ish `FIXED_TONS`, `GROSS_PROFIT` = tons × margin drawn per
  customer/port band, `"#_INQUIRIES"`=1 per row with `"#_FIXTURES"` Bernoulli by a per-customer
  win-rate — so metric cards, top-5 ports, and win rates look and behave like the real view.
- For `W_MARINE_GL_SOURCE_AI`: hierarchy paths sampled from the certified level columns
  (respecting the documented cycle hazards), `AMOUNT_USD` sign conventions honored, `CLASS4 =
  'Volume'` rows generated so the exclusion guard is exercised by tests.
- Output loads into the `synthetic` Postgres schema at compose-up; regeneration is one command.
- New entity in the ontology → add a profile stanza → generator picks it up from the certified
  columns. Unknown-column drift fails generation loudly.

## 5. Vector store

- Engine: **pgvector** in the same Postgres, identical in every habitat — local, SPCS, EC2
  (decision D14: dev/prod parity beats managed alternatives at this scale; OpenSearch
  Serverless or Bedrock Knowledge Bases are documented alternatives if retrieval scope grows).
- Embeddings: config-driven `embeddings` role like every LLM role (doc 03) — Bedrock Titan Text
  Embeddings on the Bedrock profile, Cortex embedding functions on the Cortex profile.
- Initial corpus (thin, later phase): generated briefs and research syntheses, keyed by
  `(user_sub visibility, customer, created_at)` — enables "what did we conclude about X last
  time" retrieval as a tool. Table `embeddings(id, owner_sub, kind, ref_id, customer, chunk,
  vector, created_at)` with an ivfflat/hnsw index.

## 6. Extensibility: how a new table arrives

1. Certify the entity in `ontology.yml` (harness lifecycle: investigate → propose → certify).
2. Update the vendored copy; the loader contract test diff shows exactly what changed.
3. Add a synthetic profile stanza; regenerate.
4. New/updated skills reference the entity's certified metrics via the query builder; negative
   constraints automatically join the router guardrail block.
5. Router-decision cases and snapshot tests added with the skill.

No step edits the framework — the ontology is additive, matching the harness's "additive
promise". `AR_INVOICES` will be the first exercise of this path.

## 7. Future path: a guarded text-to-SQL skill (not in this overhaul)

LLM-generated SQL remains excluded. If run-log evidence later shows recurring question shapes
the certified specs cannot express, a **text-to-SQL skill may be added as a new skill** — SQL
validated against the ontology (identifiers whitelisted, negative constraints enforced),
executed read-only through the same guard, and additive to the registry. It would never replace
the deterministic core; it would sit beside it, subject to the same router-decision tests.
