# Monthly Performance Reports — Adversarial Design Review (record)

Date: 2026-09-04 · Reviewed: the first draft of `2026-09-04-monthly-performance-reports-design.md`
· Method: six independent reviewers, each assigned one lens and instructed to break the design with
evidence from the repository; a completeness critic for blind spots; then, for every strong
challenge, one verifier instructed to refute it against the files and one instructed to estimate
what it would change. Thirty-nine agents in total.

## Verdicts by lens

| Lens | Approach | Architecture | Framework |
|---|---|---|---|
| Architecture fit | right with changes | right with changes | right |
| Analytics correctness | right with changes | right | right |
| LLM and routing | right with changes | right with changes | right |
| Platform and operations | right with changes | right with changes | right |
| Product scope (solo developer) | right with changes | right with changes | right |
| Framework and alternatives | right with changes | right with changes | right with changes |

No lens judged the approach, the architecture or the framework wrong. The framework lens compared
Streamlit in Snowflake (the home of mom-comparison), a Snowflake-native scheduled report with
`SYSTEM$SEND_EMAIL`, Cortex Analyst / Cortex Agents / Snowflake Intelligence, and a BI tool, and
found each fails a hard requirement (LLM-authored SQL, no attachments or non-Snowflake recipients,
no home for the report artifact and admin flow, no grounded follow-up chat) rather than losing on
preference.

Counts: 56 challenges raised (43 from the lenses, 13 from the critic); 16 rated high and verified;
13 survived both verifiers; 3 were refuted in part (severity or the proposed alternative) while
their factual core stood.

## Verified challenges and rulings

| # | Challenge | Outcome | Where it landed in the spec |
|---|---|---|---|
| 1 | Email deep link and `/reports/:runId` 404 in the production image; login redirect drops the path | Adopted | §6.1 history fallback + Auth0 `returnTo` |
| 2 | `running` status without a lease blocks a month forever after a worker crash | Adopted (memory-worker shape: no `running`, lock held for the job, attempts cap) | §5.2, §9 |
| 3 | Record-type/status filter question parked too late; goldens would pin the unfiltered definition | Adopted in sequencing: mechanism in Phase 15, policy by a question-only handoff issued at Phase 15 start | §8.3, §8.6 step 0, §12 |
| 4 | Account-context offsets by port and supply office confounded with the office's own activity; narrative chains marginals into causation | Adopted: offsets only on the disjoint dimension, reconciliation triple, rest-of-book ports, prompt rule | §4.5 |
| 5 | Office-filter widening as a prose rule with no code enforcement | Adopted: explicit `scope` argument, filter stamped by the skill from carried state (D41 revised) | §7.1, §7.3 |
| 6 | Cortex via SQL `COMPLETE` with emulated tools is the wrong 2026 target | Adopted: Cortex REST Messages endpoint with native tools (D40 revised) | §8.1 |
| 7 | SMTP relay undeployable on SPCS (egress ports limited to 22, 80, 443, 1024+) | Adopted: Graph is the production transport; SMTP for local and EC2 (D38 revised) | §5.6, §8.5 |
| 8 | Presigned MinIO links unreachable from browsers on SPCS and locally | Adopted: bytes in Postgres, served through the API; MinIO leaves the SPCS spec (D39 revised) | §5.2, §5.5, §8.4 |
| 9 | Same as 2, raised independently by the platform lens | Adopted with 2 | §5.2 |
| 10 | Three phases before a user sees a report; the thin in-chat path already exists (D19 flows) | Adopted: Phase 15 re-cut so the report renders inside a conversation via a flow chip; the same renderer serves the split view later (owner decision) | §6.5, §12 |
| 11 | Account context (80 queries) is net-new and forces the worker | Refuted in part: account context stays, the worker stays for Phase 16; the query cost is removed by 14 | §4.5, §5.2 |
| 12 | Real data only in Phase 18; pull the Snowflake client forward | Refuted in part: the alternative is account-gated anyway; adopted the executable half, the question-only handoff at Phase 15 start | §8.6, §12 |
| 13 | Same as 6, raised independently by the framework lens | Adopted with 6 | §8.1 |
| 14 | The 86-query pipeline is a query-builder limitation: multi-column group-by makes the report 4–6 queries and keeps account context | Adopted: `FrameQuerySpec`, time buckets, independent total as a reconciliation control (D45) | §4.2, §7.3, §7.4 |
| 15 | Synthetic customers each have one office, so account context is empty in every gate | Adopted: multi-office synthetic overlay plus fake-client unit tests | §10 |
| 16 | No number-for-number reconciliation against mom-comparison on real data | Adopted: `snowflake_live` parity suite and a differential sign-off step (whole book and one office) | §8.6 steps 2 and 5 |

Medium and low findings from the critic that were folded in without separate verification: recipient
domain allow-list and a required reply-to (§5.6); escaping and a no-fetch PDF renderer (§5.7); a
whole-book definition (`slice_value` null) as the reconciliation oracle (§3); grounded conversations
following the current run after a regenerate (§7.5); optimistic concurrency on definitions and a
partial unique index on in-flight runs (§3, §5.2); `QUERY_TAG` and per-query timing (§5.2, §8.2);
accessibility requirements on charts and tables (§4.9, §6.3); formatting once in Python (§4.3);
cross-office visibility recorded as an explicit decision (D44).

## Owner answers to the questions the review raised (2026-09-04)

- First phase ends with a report a user can see in the chat: **yes** (Phase 15 re-cut).
- Definition flexibility: the report is the fixed current view; the flexibility lives in the chat
  ("GP for Maersk for 2025 by month, then by port"). Definitions keep the two office columns and
  the prior-month basis, with a whole-book option; the chat gains time-bucket and multi-column
  breakdowns (D36 clarified, D45).
- Visibility: the whole book is visible to every Sales user, as in mom-comparison (D44).
