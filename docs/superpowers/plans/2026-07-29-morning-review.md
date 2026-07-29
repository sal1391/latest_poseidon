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

- **Phase 4 (deterministic parsing pipeline): ALL 4 TASKS COMPLETE** on the same branch — final whole-branch review in progress at the stamp below. The stack: text normalization + slot carry-over (T1) → period parser (T2) → three-tier fuzzy customer/port resolver (T3) → `parse_turn`, the one function Phase 5's router will call (T4: skill hinter + pipeline + masking). Suites: **704 offline + 31 live-Postgres**, zero failures, every task per-task reviewed (T2/T3 one fix round each; T4 zero).
  - **Your Phase 4 gate (~3 min, stack already up):** from `backend/` with DATABASE_URL set as in the Phase 2 gate: `.\.venv\Scripts\python.exe -m pytest tests -k parsing` (the four parsing suites), `.\.venv\Scripts\python.exe -m pytest -m pg -v` (live goldens), and the REPL spot-check in the Task 4 report — "Top GP customers for Port of Singapore in April 2026" returns the resolved port + April window + metric_query hint with zero issues; "Northstar Linez" auto-resolves; "Meridiann" returns clarification chips (note: bare "meridian" legitimately auto-applies — the seeded pool has a Meridian Bunkering it matches at 1.0; the double-n typo is the chips case).
  - **Two NEW decisions for you** (both parked deliberately, one-line changes if you flip them):
    3. **Port carry symmetry — now with fuller information:** on a follow-up turn that doesn't restate the port, the carried CUSTOMER is re-resolved and lands in the parse result, but the carried PORT stays only in the slots (the plan's own wording omitted ports from carry re-resolution). The task reviewer leaned "make port symmetric" — but the whole-branch review then found the customer-side re-resolution itself has three costs you should weigh before copying it: (a) if a carried customer isn't in the current value pool (pool drift, resumed chat), the user gets an "unknown customer" complaint about a name they never typed this turn; (b) if the pool has a near-neighbour instead, the slot is silently rewritten (e.g. Northstar Lines → "Northstar Linez" at 0.93) with no issue raised; (c) every turn re-fetches the customer list once a customer is set. So the real decision is which DIRECTION symmetry goes: re-resolve both (freshest, but the three costs double), re-resolve neither (slots are trusted memory — quietest), or keep the asymmetry deliberately. One-line change either way; decide before Phase 5/6 consumers. *Related cue-vocabulary question surfaced by the final fix wave:* "take a look **at** Northstar Lines gp for april 2026" now parses with neither port nor customer, silently — "at" is a port cue (weak, silent on a miss) but not a customer cue (those are for/about/on). Whether "at" should also be a customer cue, or whether this sentence becomes a Phase 5 clarification, belongs to the same decision.
    4. **MODE_SLOT_ALIASES sign-off:** the plan named no skill for "brief"-type words, so the implementer built the minimal two-table design the plan implies (brief words tie both brief skills; mode words break the tie) plus a small third lookup table letting a remembered conversation mode contribute the same tiebreak. Verified inert today (nothing sets mode yet) and well-documented — the implementer and reviewer both want your explicit yes rather than silence.
  - Earlier Task 2 items (for the record):
    1. **Bare "may" false positive** — "may I see top customers for Singapore" would have silently scoped the answer to a May window. FIXED and re-verified: "may" alone no longer counts as a month unless a preposition precedes it ("in may", "for may"). Deliberate cost: "show me may" stops parsing (recoverable) — a silent wrong answer is worse than a re-ask. Company names containing month words ("May Shipping") get handled at the pipeline layer in Task 4.
    2. **A v1 decision that needs YOUR eyes:** if you resolve a quarter or a year ("gp for q1 2026") and then ask a bare follow-up ("how about volume"), the carried period silently narrows to the FIRST MONTH (January 2026) — conversation slots only store year+month today. Fixing it properly means widening slot state, which belongs to the phase that wires real chat state (Phase 6). It's loudly documented in code; flag it if you want it sooner.

- **Phase 5 (LLM provider layer + routing, stub-first): Tasks 1-3 of 4 complete and review-clean; Task 4 (the agent loop + router-decision suite) executing.** What exists: the config-driven role→model map (doc 03's `models.yml` verbatim, env-overridable), the stub/live seam (`LLM_MODE=stub` is the default — everything runs green with zero AWS credentials; when your keys arrive, flipping the mode changes no code), the versioned router system prompt whose guardrails (certified metric definitions + the observed-hallucination negative constraints) render from the ontology at runtime, and the Bedrock provider (Converse + streaming) with every API shape verified against AWS's own bundled schema rather than memory.
  - **Catch of the night:** Bedrock's tool-naming rules forbid the dots in our skill ids (`data_qa.metric_query`) — invisible to every offline test, and it would have broken your first live tool call. Fixed two ways: skill discovery now rejects at startup any id that can't translate safely to Bedrock's alphabet (one legible error, never a silent misroute), and the provider translates names at its own boundary so nothing else in the system ever learns Bedrock's restriction exists.
  - Suites at the stamp: 808 offline, zero failures; the live smoke is marker-gated and skips with "no AWS credentials" until your keys arrive (item 1 on your needs-keys list).

**As of: afternoon — Phases 2, 3, AND 4 all complete and frozen (`b92cc14`). Phase 4 closed the full loop: per-task reviews, an Opus whole-branch final review, its fix wave (three cross-task catches: a stale comparison-period lingering in conversation memory, the word "at" over-claiming ports on ordinary sentences, and a test file whose bytes contradicted its own encoding promise — all fixed with RED-first proof), and a clean re-verification. Final suites: 717 offline + 31 live-Postgres, lint clean. Highlights: the "may" false positive is gated, clarification chips only ever show plausible names (≥ 0.60), customer names are masked before period parsing so "May Shipping" can never be misread as a month, and a follow-up turn can never silently inherit a comparison you didn't re-ask for. Merge order recommendation unchanged: pass Phase 2 → merge it → then review `phase-3-8-overnight` (Phases 3+4) as one unit. Phase 5 (LLM provider layer + router, stub mode — no AWS keys needed) proceeding next per your overnight authorization.**
