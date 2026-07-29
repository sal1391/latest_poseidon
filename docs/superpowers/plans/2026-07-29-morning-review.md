# Morning Review — 2026-07-29 🔱

*Everything that happened overnight, what needs your eyes, and the recommended order.*
*(Live-updated through the night; the "as of" stamp at the bottom tells you where the run ended.)*

## TL;DR

- **Phase 2 (ontology + synthetic data): COMPLETE** on branch `phase-2-ontology-synthetic` — all 6 tasks, per-task reviews, an Opus whole-branch review, and its fix wave. 59 offline + 17 live-Postgres tests green, lint clean. **Awaiting your gate + merge decision.**
- The certified ontology now runs end to end: vendored + pinned by contract tests, spec-based SQL with byte-pinned snapshots, a deterministic synthetic dataset (your three demo customers guaranteed in Singapore), and ground-truth-verified metrics against live Postgres.
- Two mid-phase design decisions were adjudicated from the certified rules themselves (details below): **volume mode** and the **GL 'Unassigned' placeholder**. Review these two especially — they encode business semantics.
- Phases 3+ status: see the bottom section (updated as the night progressed).

## 1. Your Phase 2 gate (~5 minutes, stack is already up and seeded)

```powershell
cd backend
$env:DATABASE_URL = "postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon"
.\.venv\Scripts\python.exe -m poseidon.scripts.demo_query
```
You should see: the available period range (2025-01-01 → 2026-06-30), the prior-year vs YTD six-metric summary, **top-5 customers by GP for Port of Singapore, April 2026** (Blue Anchor Marine and Northstar Lines rank in it; Crestline Freight is legitimately #6 — a 4th block shows all three named customers at Singapore YTD), and a GL sanity block.

Optional extras:
- `.\.venv\Scripts\python.exe -m pytest -m pg -v` → 17 ground-truth tests: SQL results vs pure-Python math over the same generated rows.
- `.\.venv\Scripts\python.exe -m poseidon.scripts.seed_synthetic` → prints "already seeded … use --force" (idempotence).
- Determinism: `--force --seed 2` then `--force --seed 1391` → checksum returns to `886dd91a…`.

**If it all looks right, say "pass phase 2" and I merge it to main.**

## 2. The two design decisions to review (adjudicated overnight from certified rules)

**Volume mode** (GL queries): when a query explicitly filters `CLASS4 = 'Volume'`, the dollar-exclusion guard drops (the ontology's own rule: "Volume queries drop the exclusion"), single aggregates are REJECTED (tons+gallons don't sum — "never emit a single aggregate"), and breakdowns are forced to CLASS3 (the level below the pivot — "force a per-CLASS3 breakdown"). All data-driven from the ontology's `unit_pivot`; no hardcoded strings; every branch snapshot-pinned. *Why it exists: the naive rendering produced a self-contradicting WHERE clause (CLAS4='Volume' AND <>'Volume' → always empty) — caught by an implementer who stopped and escalated instead of guessing.*

**GL null placeholder 'Unassigned'**: the certified rules say GL group-bys use `COALESCE(col,'Unassigned')` (CLASS1 is ~76% null), while the sales view uses 'Unknown'. The builder originally hardcoded 'Unknown' for both — the whole-branch review caught it (a filter for the canonical value 'Unassigned' would silently match ZERO rows). Now per-entity `null_placeholder`, threaded through group-by and filters, proven end to end with a live CLASS1 test. *Honest caveat: the mapping is a hand transcription of prose rules (the ontology has no structured field for it) — flagged for whenever you next re-certify the ontology.*

## 3. Also fixed overnight (worth knowing)

- **Corrected the architecture doc**: the certified sales view has **22 columns / 16 dimensions** (doc 04's prose said 24/17 — an implementer refused to weaken the certification pin, triangulated the truth, and escalated; doc + plan corrected).
- **Container ontology mount**: the compose backend container had no path to `ontology/` — every Phase-3 skill would have crashed at runtime. Found ONLY because Task 6 exercised everything live. Fixed (`../ontology:/ontology:ro`), with a deploy-phase note: the production image must ship the ontology (a `POSEIDON_ONTOLOGY_PATH` override is suggested).
- Spec validation now rejects empty filter lists (was invalid SQL) and inverted date windows (was silently empty results).
- Alembic migration logging now visible in container logs (closed a Phase-0 leftover).
- Cross-platform determinism proven: identical dataset checksum on Windows host and Linux container.

## 4. Needs YOUR keys (nothing blocks until Phase 5/7 wiring goes live)

1. **AWS trial account** → Bedrock model access (us-east-1, Claude + Nova) + an IAM dev user's keys → unlocks live router smoke tests (`-m router_live`). Everything else runs in stub mode.
2. **Perplexity API key** → unlocks live web-research calls (Phase 7). Recorded-fixture tests cover the rest.
3. When ready, I'll walk you through both step by step (the trial-path checklist is doc 07 §7).

## 5. Decisions parked for you (no rush)

- Retention windows + RPO/RTO numbers (docs have sensible defaults marked owner-decision).
- Row-level scoping of data by salesperson (hook exists, unused — Phase 14-ish decision).
- Whether to pull the personalization surface (My instructions / My memory) earlier than Phase 13 (needs P9 identity + P10 storage first regardless).
- Reconciling your diverged `origin/main` (local is 119 ahead / 1 behind; nothing was ever pushed).
- `codex/eval_only2` still carries one stray docs commit from yesterday (harmless; say the word to reset it).

## 6. Merge order when you're ready

1. `pass phase 2` → I merge `phase-2-ontology-synthetic` → `main` (fast-forward, tree already verified).
2. Then review the Phase 3+ work on `phase-3-8-overnight` (status below) and merge what you approve.

---

## Phase 3+ overnight progress

*(This section reflects the state at the "as of" stamp below.)*

- **Phase 2: COMPLETE and verified** (final commit `763c4ee`) — fix-wave re-review passed with zero open findings. Branch frozen awaiting your gate + merge.
- **Phase 3: COMPLETE and verified** (final commit `831323f`) — whole-branch Opus review + 8-item fix wave + re-review, zero open findings. Suites: host 118 offline + 23 pg; in-container 144. Your Phase-3 gate when ready (stack is up): the runbook's dev-runner curl returns the live Singapore top-5 through the real skill machinery, and the MinIO console (localhost:9001) shows rendered PDF briefs. What exists now:
  - The **skill registry** — every future skill plugs into it; fail-fast discovery, structured never-crash dispatch, router-ready JSON schemas.
  - **`data_qa.metric_query`** — the first real skill. Ask it the Singapore question over HTTP and it returns the real top-5 table with a proof block. Live-proven: `curl -X POST localhost:8000/api/dev/skills/data_qa.metric_query/run ...` (exact command in `infra/runbooks/local.md`).
  - **Customer-insight tools** (prior-year vs YTD six-metric pull, top ports) — built and ground-truth-tested, gated off until Phase 8 wires the brief flows.
  - **PDF pipeline** — markdown → WeasyPrint → MinIO with presigned links; renders in-container (the dev image gained the needed native libs); test PDFs are visible in your MinIO console (localhost:9001).
  - Suites at the stamp: host 115 offline + 23 pg; in-container 141; lint clean everywhere.
- **A process incident worth your attention (resolved, and the system worked):** a reviewer accused an implementer of fabricating evidence about FastAPI internals. The implementer refused to sign a retraction and re-proved its observation; I ran the tiebreaker myself — **the implementer was right** (FastAPI 0.140's internals genuinely changed), the reviewer's "empirical" refutation was the unverified claim, and my own misstep was ordering the retraction before running the ten-second check. The ledger records the full sequence, and the standing rule now is: integrity-level allegations get controller-verified before anyone acts. Honest machines arguing honestly — and the audit trail never took a false statement.

- **Phase 4 (deterministic parsing pipeline): IN PROGRESS** on the same branch.
  - Task 1 complete and review-clean: Unicode normalization + conversation slot carry-over (the tri-state "keep / clear / replace" rule), 49 new tests.
  - Task 2 — the period parser ("april 2026", "q1 vs q2 2026", "ytd", "vs last year", carry-over when you don't repeat the period) — built with 299 new tests, including a 55-case exact-answer table and a sweep across 731 reference dates. Review verdict: Approved, zero Critical, two Important:
    1. **Bare "may" false positive** — "may I see top customers for Singapore" would have silently scoped the answer to a May window. FIXED and re-verified: "may" alone no longer counts as a month unless a preposition precedes it ("in may", "for may"). Deliberate cost: "show me may" stops parsing (recoverable) — a silent wrong answer is worse than a re-ask. Company names containing month words ("May Shipping") get handled at the pipeline layer in Task 4.
    2. **A v1 decision that needs YOUR eyes:** if you resolve a quarter or a year ("gp for q1 2026") and then ask a bare follow-up ("how about volume"), the carried period silently narrows to the FIRST MONTH (January 2026) — conversation slots only store year+month today. Fixing it properly means widening slot state, which belongs to the phase that wires real chat state (Phase 6). It's loudly documented in code; flag it if you want it sooner.

**As of: late morning — Phases 2 AND 3 complete and frozen for your review; Phase 4 Tasks 1-3 complete and review-clean (period parser incl. the "may" gate; three-tier fuzzy customer/port resolver — one review round tightened the clarification chips so only plausible names, scoring ≥ 0.60, are ever suggested). Task 4 (skill hinter + pipeline assembly + live goldens) executing — its spec now also masks resolved customer names before period parsing so a customer like "May Shipping" can never be misread as a month. Merge order recommendation: pass Phase 2 → merge it → then review the `phase-3-8-overnight` branch (Phase 3 + whatever Phase 4 reached) as one unit.**
