# Table onboarding review — what it actually takes to add a table

Date: 2026-09-07
Assigned by: `2026-09-05-monthly-performance-reports-codex-review.md`, "Additional owner-requested
review: table onboarding"
Scope: review only. No implementation code was written, no onboarding tooling was built, and
`AR_INVOICES`'s missing source schema and join semantics were **not** invented.

---

## Headline: the README's claim is false

`README.md:33` states:

> **Certified queries only.** A vendored YAML ontology defines the entities, dimensions and
> measures the query builder is allowed to touch. **Adding a table is a certification step, not a
> code change.**

Certification is **necessary but not sufficient**. A new entity added to `ontology/ontology.yml` and
certified is invisible to the product until at least five Python edits are made. The most decisive
one is a closed `Literal` the model cannot escape:

`backend/poseidon/tasks/data_qa/skills/metric_query/schema.py:20–22`
```python
entity: Literal[
    "MARINE_SALES_PLANNING_V", "W_MARINE_GL_SOURCE_AI"
] = "MARINE_SALES_PLANNING_V"
```

`Args` is, by its own docstring, *"the only thing the model is allowed to author"* — so this
`Literal` is the entire surface through which any table becomes askable. A third certified entity
that is not in this list cannot be named by the router, cannot be validated, and cannot be queried.
The ontology would know about it; the product would not.

**Recommendation: fix the README sentence.** It is the first thing a reader learns about
extensibility and it is wrong. Suggested replacement: *"Adding a table is a certification step plus
a bounded, checklisted code change — see `docs/reference/adding-a-table.md`."* (That guide is
proposed in the reports design §8.7 / Phase 18 and does not exist yet.)

---

## The end-to-end path

Traced against the current checkout. Stages marked **CODE** require a Python edit.

| # | Stage | What happens | Where |
|---|---|---|---|
| 1 | Source investigation | Profile the source object: grain, date column, distinct counts, nulls, candidate dimensions and measures. External to this repo — the WFS workspace owns it. | `ontology/SOURCE.md` names the upstream source and upgrade procedure |
| 2 | Business definition approval | A human agrees what each metric *means*. `certified_by:` records who. | `ontology.yml`, e.g. `certified_by: carlos` |
| 3 | Ontology entry | Add the entity with grain, `date_column`, columns, metrics, negative constraints, business rules. Presence **is** certification; only `planned`/`retired` carry an explicit `status`. | `ontology/ontology.yml` |
| 4 | Loader acceptance | The typed loader validates it. `Ontology.active()` filters out `status: planned`, so a planned slot is loaded but not queryable. | `core/ontology/models.py:194–196` |
| 5 | **Null-placeholder policy** | **CODE.** Per-entity placeholder lives in a Python dict, not the YAML. | `core/ontology/loader.py:84` — `_NULL_PLACEHOLDERS = {"W_MARINE_GL_SOURCE_AI": "Unassigned"}` |
| 6 | **Skill availability** | **CODE.** Add the entity to the closed `Literal`, or it is unreachable. | `metric_query/schema.py:20–22` |
| 7 | **Query-builder behaviour** | **CODE, conditionally.** Entity-specific rules are hardcoded: volume mode (*"currently only `W_MARINE_GL_SOURCE_AI`"*), the `MONETARY_TOTAL` dual-purpose handling, hierarchy roll-up (`CLASS4` → `CLASS3`), date-column typing. A new table needs whichever apply. | `core/data/query_builder.py:37, 53, 84, 267, 284, 307, 347` |
| 8 | **Router guardrail / dev router** | **CODE.** Both pin a default entity by name. | `core/llm/loop.py:107` `ROUTER_GUARDRAIL_ENTITY`; `core/chat/dev_router.py:468` `_DEFAULT_ENTITY` |
| 9 | **Synthetic schema + data** | **CODE.** The generator has hand-written per-entity row builders — there is no generic path. | `scripts/generate_synthetic.py:141–142` — `_generate_sales_rows(...)`, `_generate_gl_rows(...)` |
| 10 | Tests | Query/routing/golden tests for the new entity, plus the generator's determinism checksum. | co-located per the folder law |

**So: two YAML stages, one validation stage, five code stages, one test stage.**

---

## The change checklist

For an approved new table, in order:

1. `ontology/ontology.yml` — entity block with grain, `date_column`, columns, metrics, negative
   constraints, business rules, `certified_by`.
2. `core/ontology/loader.py` — add a `_NULL_PLACEHOLDERS` entry **if** the entity's certified rule
   differs from the `"Unknown"` default.
3. `metric_query/schema.py` — extend the `entity` `Literal`. **Without this, nothing else matters.**
4. `core/data/query_builder.py` — only if the table needs volume mode, a hierarchy roll-up, a
   dual-purpose measure, or non-DATE date handling. Skip if it is an ordinary additive table.
5. `scripts/generate_synthetic.py` — a row generator for the new entity, wired into the profile map;
   expect the determinism checksum to change.
6. Tests: SQL snapshot tests for the new entity, at least one router-decision case proving the model
   selects it, and a synthetic-data golden.
7. Registry↔schema parity test — confirm it still passes; it exists to catch exactly this kind of
   drift.

**Two hazards worth naming.** The `Literal` in step 3 fails *closed* and *silently from the user's
point of view* — the table is simply never chosen, with no error saying why. And step 5's checksum
change is expected but looks alarming; it should be called out in the guide so nobody treats a
legitimate regeneration as a regression.

---

## `AR_INVOICES` specifically

Present in `ontology.yml:525` as a **planned registry slot**, `status: planned`, deliberately not
onboarded. `Ontology.active()` excludes it, so the ontology model refuses queries against it — the
gating works as documented.

Its source schema, grain, date column, measures and join semantics are **not defined in this
repository**. I did not invent them, per the review's instruction. Onboarding it starts at stage 1
against the real source, which is external work.

Its value as a walkthrough is that it is a *realistic* candidate, not that it is ready.

---

## Effect of the migration

The certified formulas, table access rules and deterministic query construction all live in
`core/ontology/`, `core/data/` and `tasks/` — every one of which **stays in Python** under M2 of the
migration design. So the onboarding path above is essentially unaffected by the migration, which is
a real argument in favour of the retained-Python decision: the extensibility mechanism does not have
to be rebuilt or revalidated.

Ownership across the boundary:

| Concern | Owner after migration |
|---|---|
| Ontology YAML, loader, certification | **Python.** Unchanged. |
| Query construction, metric formulas, null placeholders | **Python.** Unchanged. |
| Skill argument schema (the `Literal`) | **Python** — but its JSON Schema is now consumed by AI SDK tool definitions rather than the custom loop. The schema must be *exported* across the internal contract (migration design §7), not duplicated in TypeScript. |
| Synthetic data generation | **Python.** Unchanged. |
| Database migrations for *application* tables | Drizzle, if Q3 lands there — but note the ontology and the synthetic schema are **not** application tables. They should not migrate to Drizzle just because Drizzle wins Q3. |

**One thing the migration must not do:** re-declare the entity list in TypeScript. If the tool
definition given to AI SDK hardcodes entity names, there are then *two* closed lists to update and
they will drift. The tool schema must be generated from the Python contract.

---

## Recommended onboarding workflow

The review asked whether a documented developer procedure suffices or tooling is needed, and told me
not to assume a UI is necessary. **A documented procedure plus one guard test is enough. No UI, and
no bespoke tooling.**

Reasoning: onboarding is rare, requires human business-definition approval that cannot be automated
anyway, and the code changes are small and bounded. Tooling would be built for an event that happens
a few times a year, and a UI would imply the semantic approval step can be self-service, which it
cannot.

**Deliverables:**

1. **`docs/reference/adding-a-table.md`** — the checklist above, in order, with the two hazards
   called out and a worked example against a real certified entity.
2. **One guard test** closing the silent-failure gap: assert that every entity in
   `Ontology.active()` appears in the `metric_query` `Literal`, and vice versa. Today a certified
   entity can sit in the YAML unreachable forever with nothing complaining. This test converts that
   into a loud CI failure, and is the single highest-value item in this review.
3. **Human approval stays a gate.** `certified_by:` is the record. Do not automate it.

**Acceptance:** an approved table is ready when its ontology entry loads, the guard test passes, one
router-decision case proves the model selects it, its synthetic data generates deterministically,
and a SQL snapshot test pins its query shape.

---

## Marked explicitly

- **External / account-gated:** source investigation and profiling (WFS workspace), real business
  definition approval, and anything requiring a live Snowflake session. None of it was performed.
- **Planned, not built:** `docs/reference/adding-a-table.md` and the guard test are recommendations,
  not existing artifacts.
- **Not validated here:** whether the WFS upstream investigation and certification tooling referred
  to by `ontology/SOURCE.md` exists in usable form. `SOURCE.md`'s presence documents a procedure; it
  does not prove the tooling behind it. That gap should be confirmed before the onboarding guide
  promises anything about upstream steps.
