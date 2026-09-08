# Codex review for Claude: monthly performance reports

Date: 2026-09-05  
Reviewed checkout HEAD: `6477fc3ff18c1a8b639925b3cdb7579603b45fdb`  
Status: Review only; no implementation changes. Existing working-tree changes were left alone.

## Scope and assessment

Reviewed the revised `2026-09-04-monthly-performance-reports-design.md`, its accompanying `2026-09-04-monthly-performance-reports-review.md`, and the repository seams they depend on: parsing/carry, persisted conversation state, query construction, skill contracts, providers, artifacts, authentication, and the memory worker. An independent read-only reviewer checked jobs, sends, and definition consistency.

The deterministic analytics/report payload approach remains useful, but the proposed implementation does not satisfy the owner's confirmed stack migration requirement. The revised design also has correctness and delivery gaps that should be resolved before Claude turns it into an implementation plan. The previous review's adopted rulings are useful history, not proof that their proposed remedies work.

The findings below concern the proposed design and its integration with existing code. Missing report modules are expected at this stage and are not defects by themselves. P1 means a correctness/reliability issue to resolve before implementing the affected path; P2 means a concrete contract or integration gap to resolve in its phase.

**Owner clarification during this review:** "Treat the pasted stack as a migration requirement." The target is therefore **Next.js 16 App Router, AI SDK 6 by Vercel, Tailwind CSS, PostgreSQL, Auth.js and Drizzle ORM**, with the directory and prompt conventions supplied by the owner. Asked whether Python analytics/rendering may remain behind the new application or must also move to TypeScript, the owner requested the pros and cons; no option has been approved. Existing-code findings below identify behavior to correct or avoid carrying into the migration, rather than requiring further investment in the old stack. The top-level prompt-directory/XML requirement also needs explicit treatment.

External mom-comparison/wfs/Triton source implementations and live Snowflake, Graph, Postgres and provider behavior were not validated in this review. No live queries, sends, deployments or migrations were performed. The existing modified HTML mockup is not treated as an independently verified financial oracle.

## Findings

### Overall concern requested by the owner: too much change in one delivery program

The largest overall risk is the combined scope: monthly reporting, report-grounded chat, email distribution, Snowflake/Cortex deployment, and the now-required application-stack migration are substantial workstreams. Each changes different contracts, while the current phases make them depend on one another. Implementing them together makes regressions harder to isolate and delays the first trustworthy user workflow.

**Recommendation for Claude:** Resolve the target architecture and Python/TypeScript boundary first. Preserve the existing certified skills and their tests as migration reference behavior. Establish the new application's login, history ownership, tool execution and streaming, then deliver one reliable report → grounded follow-up workflow. Add email distribution and complete the account-gated platform rollout after that workflow is verified. Issue the Snowflake data-policy and access questions early so external answers do not become a late blocker. Each stage needs an independently demonstrable acceptance gate; do not treat the attractive final mockup as evidence that all work should ship in one step.

This concern supports the product direction; it calls for smaller delivery stages and explicit migration boundaries, not removal of the report or account-context features. The owner requested that this concern be recorded, but has not selected the Python-versus-full-TypeScript boundary.

### C00 — P1: The design extends the old stack instead of planning the required migration

**Owner requirement:** Confirmed during this review. **Design:** §5.1, §5.4, §6.1, §8.4 and the Phase 15–18 table.

The proposal adds FastAPI routes and dependencies, a React Router split view in the Vite SPA, Auth0 role/redirect work, Alembic migrations, and extensions to the custom Python model loop. Those choices match the current checkout (`backend/pyproject.toml`, `frontend/package.json`, `backend/poseidon/core/llm/roles.py`) but do not implement the required Next.js 16, AI SDK 6, Auth.js and Drizzle migration. Following the phase table literally would spend multiple phases expanding application seams that the owner now requires replacing. No target Tailwind/component migration is specified either.

**Requested revision:** Rework the architecture and phase dependencies around the confirmed target stack before approving an implementation plan. Specify App Router public/authenticated/API boundaries, server-versus-client component responsibilities, AI SDK tool/stream contracts, Auth.js session/role enforcement, Drizzle schema/migration ownership and Tailwind adoption. Resolve whether Python remains solely for internal deterministic analytics/rendering or is also migrated. Preserve the no-model-authored-SQL and shared deterministic-math requirements across that boundary.

The migration must explicitly map existing identity subjects and ownership/RLS, conversation history/slots, tool/message parts, streaming, report artifacts and worker access. Auth.js versus Auth0 should not be treated as an automatic requirement to replace the upstream identity provider: session/auth library and identity provider are separate choices to document. Likewise, select one migration authority for shared database tables rather than letting Alembic and Drizzle evolve them independently.

**Acceptance check:** A revised design maps each required technology and existing behavior to its target owner, documents the coexistence/cutover sequence, and replaces obsolete phase deliverables such as the Vite history fallback. Migration gates cover login/roles, existing history ownership, deterministic tools and streaming before the report feature depends on them. No migration code was requested or written in this review.

### C01 — P1: The proposed attempt counter does not survive a worker crash

**Design:** §5.2, lines 268–273. **Earlier review:** adopted findings 2 and 9.

The design increments `attempts` in the transaction that holds the row lock for the entire job. A process death rolls back that increment along with the unfinished work. The row becomes claimable again with the same attempt count. Repeated hard crashes can therefore retry indefinitely despite `REPORT_MAX_ATTEMPTS=2`.

The stated precedent is inaccurate: `backend/poseidon/scripts/memory_worker.py:827–841` closes the claim transaction before `_process_one`. Its crash test explicitly asserts that attempts remain zero (`backend/tests/test_memory_worker.py:1069–1099`, especially 1097). It does not establish the crash cap promised here.

**Requested revision:** Specify a durable attempt/claim mechanism that survives process death, such as a committed lease and attempt count with expiry recovery, or a separately committed attempt ledger. Define who marks an exhausted claim `error`. Explain which database connection holds the claim and which reads definitions/writes telemetry; the proposed worker-role grant only covers `report_run`.

**Acceptance check:** Kill the worker after each claim on the same run. After the configured number of attempts, it becomes terminal, no longer occupies the pending unique index, and a new request for the month can proceed. Include process death, not only exceptions caught inside the job.

### C02 — P1: Recording the send after delivery cannot make email idempotent

**Design:** §5.3, lines 287–298.

The proposed order is call the mailer, then record the outcome. Two requests with the same key can both send before one loses the unique-insert race. A crash or timeout after transport acceptance but before the database write also leaves a retry free to deliver a duplicate. The two-state `sent`/`failed` schema cannot represent this uncertain outcome.

**Requested revision:** Reserve the key durably before attempting delivery, associate it with the complete request (run, normalized recipients and attachment choice), and serialize competing requests. Define in-progress and unknown-outcome behavior. A local unique constraint cannot guarantee exactly-once external delivery; an unknown outcome must not be treated as an ordinary safe retry. Specify whether a retry of a known failure reuses the key and how intentional resends get new keys.

**Acceptance check:** Concurrent identical requests make at most one transport call. Reusing a key with different contents is rejected. A crash after simulated transport acceptance produces an explicit uncertain result and does not automatically resend.

### C03 — P1: Existing period carry loses both the year window and the report comparison

**Design:** §7.1, §7.3 and §7.6, especially lines 451–455 and 528–536.

The proposed follow-ups require more than adding office/report fields. `ConversationSlots` stores first-of-period dates, not full windows. `backend/poseidon/core/parsing/period_parser.py:462–467` reconstructs a carried date as one month and never carries comparison B. `backend/poseidon/core/parsing/pipeline.py:507–510` persists only period starts and explicitly clears B when the next message does not mention a comparison.

A read-only execution of the existing parser produced:

```text
"for 2025 by month" -> [2025-01-01, 2026-01-01)
"then by port" with its carried start -> [2025-01-01, 2025-02-01)
"why did GP drop?" with July/August report slots -> July window, comparison None
```

Bare-year parsing already exists; adding a bare-year parser test alone does not fix the lost window. This breaks both an explicit Phase 15 gate and the default report month-pair promise.

**Requested revision:** Define persisted full period windows or equivalent explicit granularity/end bounds, plus report-aware comparison carry and clearing rules. Specify A/B orientation when moving between reports and the query skill. Update the explicit serialization in `backend/poseidon/core/chat/history.py:836–869`; adding dataclass fields alone will not persist office/report/window state.

**Acceptance check:** Exercise the actual parser → state write → database reload → next-turn path for a year, quarter and report comparison. Verify full-year "then by port", July/August default comparison, "compare to June instead", and intentional period clearing.

### C04 — P2: Share-of-total instructions reapply the filter the denominator must remove

**Design:** §7.3, lines 478–481 and 496–500; expected router case at 531.

The scope rule says to stamp the office filter onto *both queries of a share*. The share rule says to drop the office column for the denominator. An implementation that follows the first rule after constructing the denominator returns the office as its own denominator, yielding 100% instead of the office share of the customer's book.

**Requested revision:** State the construction order explicitly: build the effective selection filters including carried office; validate the requested removals; derive denominator filters by removing those columns; do not restamp the removed office filter. Identity-based row scope must remain independently enforced on both queries.

**Acceptance check:** A customer with office GP 20 and book GP 100 returns 20%, and the proof shows the office predicate only on the numerator while retaining the customer predicate on both.

### C05 — P2: A queued run has no immutable definition snapshot

**Design:** definition fields/updates at lines 60–71 and 325; run fields at 259–264; conversation identity at 307–310.

Queue a Gibraltar run while the worker is down, then edit the same definition to another office. The run stores a definition FK but no version or generation arguments, so the worker can produce a different office than the requester queued. The definition copied into the finished payload (§4.10) is too late to prevent this. Later regeneration can also change the office behind an existing definition/month conversation while its carried office remains Gibraltar.

**Requested revision:** Atomically snapshot definition version and generation inputs when enqueuing. Decide whether office identity is immutable after a definition has runs or whether changing it creates a new version/conversation identity. State which recipient list a preview/send uses; preserve the final selected addresses in the send record.

**Acceptance check:** Enqueue → edit definition → execute still generates the originally requested scope. A scope-changing edit cannot silently switch an existing conversation to a different report office.

### C06 — P2: The split-view URL and report lookup can refer to different runs

**Design:** §6.1 and §6.2 (`/reports/:runId`), §5.4 and §7.5 (conversation follows current `ok`).

Open run R1 from an old email after R2 supersedes it. The panel route identifies R1, while `report_lookup` deliberately returns R2. A warning in a tool proof says the run changed but does not make the report beside the chat match the numbers being discussed. This also occurs when another admin regenerates while a user has the page open.

**Requested revision:** Complete the already-chosen current-run behavior at the UI boundary: redirect or visibly switch the panel to the current run, reconcile the conversation reference and tags, and indicate that the emailed version was superseded. If historical viewing is required, define it explicitly and separate it from a current-run conversation. Capture one resolved run per chat turn so multiple lookup calls cannot mix versions if regeneration completes mid-turn.

**Acceptance check:** Open a superseded email link and regenerate during an open chat. Panel, lookup result, proof and active run identifier must agree; historical chat messages remain identifiable as historical.

### C07 — P2: Phase 15 report persistence and byte delivery have no complete storage path

**Design:** §5.1, lines 248–251; §6.5, lines 429–436; §7.5; §8.4.

Phase 15 creates a report without a definition/run row, carries a payload ID into later turns, and returns a PDF artifact. `report_run` and the authenticated byte routes arrive in Phase 16. The design adds generic `ArtifactStore.put/get`, but does not specify where that Phase 15 ID is stored, how it survives restart, how ownership is checked, or how its PDF is served once SPCS removes the object store.

The current store is concretely S3/MinIO-backed and returns expiring presigned links (`backend/poseidon/core/artifacts.py`, `ArtifactStore.__init__` and `put_pdf`). Startup constructs that store (`backend/poseidon/api/app.py:571`). Merely adding methods does not provide the promised Postgres-backed path for reports that have no `report_run` row.

**Requested revision:** Name the durable Phase 15 payload/byte backing store, lookup key, authorization and retrieval route, and its Phase 16/18 migration or shared use. Persist `report_ref` through the explicit history serializer. Avoid relying on process-local "in-turn" state for next-turn lookup.

**Acceptance check:** Generate through the chat flow, restart the API, reload the conversation, ask for report figures, and download the same PDF. Repeat without an object store in the SPCS configuration.

### C08 — P2: An API PDF link cannot reuse the existing artifact anchor in Auth0 mode

**Design:** §5.5, line 329; §6.3 and §6.5 PDF downloads.

Moving bytes behind Sales-authenticated API routes changes the browser download contract. `frontend/src/ui/message-parts/ArtifactPart.tsx:12–21` is a plain anchor. Bearer tokens are attached by the API client's request builder (`frontend/src/api/client.ts:83–91`), and the backend Auth0 identity provider expects an Authorization header (`backend/poseidon/core/identity_auth0.py:271` onward). A normal link navigation does not invoke that builder or attach the SPA's token, so substituting `/api/reports/runs/{id}/pdf` into the artifact URL will fail authentication.

**Requested revision:** Include an authenticated binary-fetch/download path in the frontend contract, using the existing token injector and an appropriate browser download lifecycle. Apply it to the in-chat artifact card and panel download; do not place bearer tokens in URLs.

**Acceptance check:** With bearer-authenticated API requests enforced, both PDF controls download bytes after login and token refresh; an unauthenticated direct request remains denied. A disabled-auth smoke test is insufficient.

### C09 — P1: Numeric-token membership does not establish narrative grounding

**Design:** D34 and §4.7, particularly lines 173–180.

The proposed check accepts a number if it appears anywhere in the payload's display strings. It cannot detect assigning a valid number to the wrong customer, month or metric, or calling a decrease an increase when its magnitude appears elsewhere. Exempting ranks 1–10 also leaves arbitrary small quantities unchecked. A narrative such as "customer A GP is 200" passes numeric membership when A's GP is 100 and B's is 200.

This is a limitation of the specified algorithm, not a claim that an unimplemented checker was executed. A must-pass mockup fixture only proves acceptance of that fixture and does not test rejection of false statements.

**Requested revision:** Either narrow the documented guarantee to numeric vocabulary checking or use structured fact references that bind entity, metric, period, value and direction, then render numeric claims deterministically. Check all model-authored narrative fields, including insights, context notes and question chips, rather than just `narrative_md`.

**Acceptance check:** Reject swapped customer values, swapped A/B values, wrong units/directions and false claims containing numbers 1–10. Keep a correct narrative fixture too. Template fallback must remain available.

### C10 — P2: Forced narrative tool calls require a provider seam change in Phase 15

**Design:** §4.7 layer 2 and §5.1 narrate subskill; §8.1 provider translation deferred to Phase 18.

The current `RoleClient.invoke` accepts tools but no per-call tool choice (`backend/poseidon/core/llm/roles.py:191–198`). Bedrock's request builder creates a `toolConfig` containing only the tool list (`backend/poseidon/core/llm/bedrock.py:280–302`). Passing a single schema therefore does not implement the promised forced tool call. The Phase 15 framework-seam list does not include this change.

**Requested revision:** Define a per-invocation tool-choice contract in the target AI SDK integration and account for any transitional Python RoleClient path that remains. The existing RoleClient/wrappers/stubs and Bedrock encoder would need explicit changes if used during coexistence. Specify later Cortex parity using that same contract. Validate the returned tool name, count and schema before accepting narrative output; preserve default router behavior when no choice is supplied.

**Acceptance check:** Offline request fixtures prove the narrative call requests the designated tool, ordinary router calls retain their existing behavior, and missing/wrong/malformed narrative calls fall back as designed.

### C11 — P2: Time-series results can inherit a top-five cap

**Design:** §7.3, lines 487–495; §7.6's full-year monthly example.

The design retains `top_n` while promising chronological monthly tables and "GP for 2025 by month." The existing argument defaults to five rows (`backend/poseidon/tasks/data_qa/skills/metric_query/schema.py:39`). No rule exempts time buckets from that default. Chronological ordering with a five-row cap still silently omits seven months; ordering by GP first selects a different incomplete set. Mixed `[LOC_NM, MONTH]` also needs a defined distinction between top ports and a cap on individual port-month rows.

**Requested revision:** Define complete bucket coverage for a requested time window and an explicit truncation/pagination policy for mixed dimensions. Define deterministic precedence between chronological ordering and `order_by`. Specify alignment of buckets when `compare_period` is combined with MONTH/QUARTER/YEAR, or reject that combination until its meaning is implemented; an outer join on different calendar dates does not align comparable periods.

**Acceptance check:** A seeded full year yields twelve chronological monthly buckets, with a documented missing-month policy. A port/month result does not silently chop a selected port's series. Any intentional truncation is visible.

## Questions for the owner and Claude

These are decisions or unresolved contracts, not additional confirmed implementation bugs. Claude can resolve code-level details directly and bring only product choices back to the owner.

1. **Migration boundary:** The owner has confirmed that the pasted stack is required. May Python analytics and report rendering remain as an internal service behind Next.js/AI SDK 6, or must the backend also move entirely to TypeScript? The owner requested the tradeoffs below and has not selected an option. This affects the math-port, worker and PDF implementation plan; do not assume either answer.
2. **Prompt policy:** The user requires all AI prompts in top-level `prompts/` and XML system-prompt structure. The design places `narrate.md` inside a skill and creates a Snowflake agent prompt in `docs/snowflake/`. How will those artifacts comply? Record the canonical prompt location and loading path; do not silently copy the repository's older convention over the current instruction.
3. **Who may generate in chat?** D37 gives report writes to ReportAdmin, but Phase 15 exposes a Monthly report flow before the new role arrives in Phase 16. Is ad hoc in-chat generation deliberately available to every Sales user, with only shared definitions/runs/sends admin-only? State the distinction and enforce it at dispatch, not just through hidden chips.
4. **Definition identity:** Can an existing definition change office after it has runs, or should that require a new definition? This determines the snapshot/conversation remedy in C05.
5. **Historical reports:** Under the chosen current-run chat policy, should an old email link immediately open the latest run, or first offer a clearly marked historical view? The panel and active chat need a consistent answer (C06).
6. **Periods:** Are partial current months allowed? What happens for the earliest available month when its prior month is absent, or for gaps in the data? `DataClient.available_periods` currently returns a MIN/MAX `PeriodRange`, not an enumeration of months present (`backend/poseidon/core/data/client.py`, `PeriodRange` and `DataClient.available_periods`). Define what the picker and validation mean by "available."
7. **Parity versus policy:** If the Snowflake handoff chooses a row filter different from mom-comparison's unfiltered behavior, what becomes the authoritative reconciliation baseline? The spec currently demands both the newly selected filter and cent-for-cent equality with mom-comparison. Record whether that reference must be run with the same filter or whether expected differences require owner approval.
8. **Top-ten totals:** Should the table footer say "Whole office total" with a separate top-ten subtotal, or should there be an "Other" row? With more than ten customers, the shown rows do not sum to the whole-office KPI even though §4.4 requires the footer to equal it. Also define how one-time display rounding differences are explained.
9. **Report lookup size:** §7.5 promises paging by `top_n`, but its Args schema contains only `section`; there is no limit or cursor/offset. What retrieval mechanism makes the tail accessible under the existing 4,000-character tool-result cap (`backend/poseidon/core/llm/loop.py:126`)? Define it rather than relying on silent truncation.
10. **Data consistency during generation:** Must all six queries see the same source snapshot? A load between the office frame and its control can trigger a false reconciliation failure; a load after the controls but before book context can produce inconsistent offsets without that control detecting it. Specify a shared snapshot/as-of strategy where supported or an explicit detection/retry policy, including the view's capabilities during the Snowflake handoff.

## Migration tradeoffs requested by the owner

| Option | Advantages for this repository | Costs and risks |
|---|---|---|
| Next.js application with a narrow internal Python analytics/rendering service | Retains existing deterministic query/math infrastructure and Python test coverage; avoids rewriting the existing WeasyPrint integration; provides a smaller cutover while migrating UI, sessions, application data access and LLM orchestration to the required stack. | Two runtimes and deployment units; requires a versioned internal request/result contract, authenticated service calls, timeout/retry behavior and tracing across that boundary. Ownership must be clear so both services do not independently manage the same schema or user authorization. |
| Entire application and backend in TypeScript | One implementation language; easier sharing of application types; fewer language/service boundaries if deployed together; one application schema/migration owner through Drizzle. | Rewrites existing Python query building, parsing/carry, orchestration and workers as applicable; revalidates calculations and source-query parity; requires a replacement PDF implementation to remove Python. A single language does not remove the need for durable background execution or native/browser PDF dependencies. |

**Codex recommendation, not an owner decision:** Use Next.js/AI SDK 6/Auth.js/Drizzle/Tailwind for the application and retain Python only for deterministic analytics and report rendering initially. This reduces the amount of existing financial logic and rendering infrastructure changed during the migration. Keep user-facing authentication/authorization, shared application schema ownership and LLM orchestration on the new application side; give the Python side an explicit internal contract. The monthly reporting math is still proposed work, so retention is not a claim that its implementation already exists or passes tests.

Next.js explicitly supports integration with an existing backend ([official custom-server guidance](https://nextjs.org/docs/app/guides/custom-server), [backend-for-frontend guidance](https://nextjs.org/docs/app/guides/backend-for-frontend)). The existing PDF dependency is a Python HTML/CSS rendering engine ([official WeasyPrint documentation](https://doc.courtbouillon.org/weasyprint/stable/index.html)). Those capabilities support the option; the recommendation and relative migration-risk assessment are repository-based engineering judgments, not vendor claims.

Choose full TypeScript if eliminating Python is itself a long-term requirement and the owner accepts the wider rewrite and parity-validation scope. It is not intrinsically more accurate for the report's arithmetic. Whichever option is selected, preserve golden fixtures and verify identical outputs at the migration boundary.

## Verification performed and limits

- Read the two requested documents and relevant implementation files; checked the previous review's crash-recovery claim against the actual worker and its existing crash test.
- Executed the existing period parser using `backend/.venv/Scripts/python.exe -B` and an in-memory fixture, with no database access or bytecode writes. It confirmed both the year-to-January carry and discarded comparison described in C03.
- Checked the actual provider request builder, artifact renderer and bearer-token injector for the integration findings.
- Consulted official Next.js and WeasyPrint documentation to answer the owner's migration tradeoff question; this does not constitute live deployment or provider validation.
- Did not run a full test suite, write tests, modify code, edit either input document, or validate external APIs/accounts. This is a design review, not a claim that the future feature has been tested.

**Follow-up verification requested through the owner's question about existing skills:** Ran the focused offline registry, brief, metric-query and web-research tests with bytecode/cache writes disabled. Result: **94 passed, 1 skipped, 2 deselected**. The first attempt had 18 fixture setup errors because pytest's temporary directory was inaccessible; an approved rerun with access to that directory passed. Command: `python -B -m pytest -p no:cacheprovider tests/test_skill_registry.py tests/test_brief_skills.py poseidon/tasks/data_qa/skills/metric_query/tests poseidon/tasks/research/skills/web_research/tests -m 'not pg and not minio and not pdf and not router_live and not research_live' --tb=short`, from `backend/`. This supports reuse of the tested existing skill contracts; it does not certify live LLM, database, research or PDF services, or the proposed reporting skills.

## Additional owner-requested review: table onboarding

Review table onboarding before finalizing the migration plan. Distinguish what is implemented in this repository, what depends on the external WFS workspace, and what is only planned. This is a review request, not authorization to implement onboarding or build an onboarding UI.

Current evidence to verify:

- `docs/architecture/04-data-ontology.md` §6 describes investigate → propose → certify, vendoring, synthetic profiles, skill integration and tests.
- `ontology/SOURCE.md` identifies the external WFS ontology source and upgrade procedure; its presence does not prove that the upstream investigation/certification tooling exists in this repository.
- `ontology/ontology.yml` includes two certified entities and an `AR_INVOICES` planned placeholder. The ontology model refuses queries against planned entities.
- `backend/poseidon/tasks/data_qa/skills/metric_query/schema.py` explicitly enumerates the two currently supported entities. A new ontology entry alone does not expose another table through that skill.
- The detailed `docs/reference/adding-a-table.md` guide is proposed in the monthly-report design §8.7/Phase 18 but was absent when checked.

**Review assignment for Claude:**

1. Trace adding one table from source investigation and business-definition approval through ontology loading, skill availability, deterministic SQL generation, synthetic schema/data, and query/routing tests. Use `AR_INVOICES` as a candidate walkthrough only; do not invent its missing source schema or join semantics.
2. Identify every required manual or code change, including hardcoded table names and any assumptions in the parser, data clients, query builder, synthetic generator and tests. Reconcile these with the README's claim that adding a table is a certification step rather than a code change.
3. Explain how the required stack migration will preserve certified formulas, table access rules and deterministic query behavior. Clearly assign ownership of ontology updates and database migrations across any Python/TypeScript boundary.
4. Recommend the smallest complete onboarding workflow, including human approval of business definitions and acceptance checks. Determine whether a documented developer procedure is sufficient or specific tooling is needed; do not assume a UI is necessary.
5. Record findings, evidence, unresolved dependencies and owner questions in Claude's own review file. Do not change implementation code or the source design as part of this review.

**Expected outcome:** A concrete checklist showing how an approved new table becomes safely queryable in chat, which files/contracts change, who approves its semantics, and which checks establish readiness. Mark account-gated or externally owned steps explicitly rather than presenting them as completed.

## Confirmed owner decision: customer and supplier broker perspectives

The owner confirmed that supplier-office reports must primarily rank and analyze **suppliers**, with **customers available as a secondary breakdown**. Customer-office reports retain their customer focus. This is a confirmed product requirement, not an optional Codex recommendation.

| Report perspective | Office filter in the current certified view | Primary entity |
|---|---|---|
| Customer broker | `CUSTOMER_TEAM_NAME` | Customer: `CUST_NM` |
| Supplier broker | `PRIMARY_SUPPLY_TEAM_OFFICE` | Supplier: `SUPPLIER_NM` |

The current source view is `MARINE_SALES_PLANNING_V`. These are two reporting perspectives over that view; this decision does not require creating separate database views. Both can share certified calculations, deterministic query construction, payload/rendering infrastructure and delivery. The perspective must explicitly determine the office filter and primary entity rather than relying on the LLM to reinterpret a customer report.

**Required design revision for Claude:** The existing design supports two office filters but still specifies customer rankings, customer movers and customer account context for both. Revise the contract so supplier-office reports use supplier rankings and supplier-focused analysis. Carry the perspective and supplier identity into grounded chat, labels, suggestions, and equivalent email/PDF content. Customers must remain available as a secondary breakdown. Review which customer-specific sections (including new/lost and whole-book context) need supplier equivalents, and define their business meaning before porting them mechanically.

Supplier-grouped GP means **our certified sales GP associated with that supplier**, not the supplier's own profitability. Preserve certified metric definitions unless a separately reviewed business definition changes them. Office scope and perspective must survive persistence, report lookup and regeneration consistently with C03/C05/C06.

**Acceptance checks:** A customer-office fixture produces customer rankings; a supplier-office fixture produces supplier rankings using `SUPPLIER_NM` and the supplier-office predicate. A supplier-report follow-up about "this supplier" preserves its identity and office/month context; a request for its customer breakdown groups by `CUST_NM` while retaining the supplier selection. The rendered report and its chat must agree on perspective. Existing customer workflows must continue to pass their tests.

**Remaining design details, not reasons to reopen the confirmed decision:** Specify where the secondary customer breakdown appears (report, chat, or both), how whole-book definitions select their perspective, and which supplier-specific analyses are supported by the certified data. Do not infer supplier reliability, spend or payment metrics from sales GP/volume alone.

## Suggested Claude response

For C00–C11, record **accept / refute with repository evidence / owner decision needed**, then state the exact design contract and phase gate to change. Start with the confirmed migration requirement and unresolved Python boundary. Incorporate the confirmed customer/supplier broker perspective requirement above and complete the table-onboarding review. Reopen the previous review's crash-cap ruling rather than treating it as settled. Incorporate accepted revisions before writing the implementation plan. Preserve the current owner's scope and visibility decisions unless the owner explicitly changes them.
