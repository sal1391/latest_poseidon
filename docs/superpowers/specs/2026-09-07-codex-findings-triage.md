# Codex review findings — triage record

Date: 2026-09-07
Source: `docs/superpowers/specs/2026-09-05-monthly-performance-reports-codex-review.md`
Status: **Partial.** C00, C01, C03 and C07 worked. C02, C04, C05, C06, C08–C11 not yet worked —
listed at the bottom with their current status so the gap is visible rather than implied.

Verdict vocabulary, per the review's own request: **ACCEPT** (confirmed against this repository),
**REFUTE** (contradicted by repository evidence), **OWNER** (a product decision, not a technical
one).

---

## C00 — P1 — The design extends the old stack instead of planning the migration

**ACCEPT.** Addressed in full by `2026-09-07-nextjs-migration-design.md` (commit `d573f2c`), which
maps each seam to its target owner, defines the internal Python contract, and replaces the Phase
15–18 table. Five sub-decisions are left to the owner there (§9) because they change what gets
built: where parsing lives, where background work runs, migration authority, whether Auth0 stays
behind Auth.js, and coexistence versus hard cutover.

---

## C01 — P1 — The attempt counter does not survive a worker crash

**ACCEPT, and the design's stated precedent is inaccurate — verified directly.**

The design proposed incrementing `attempts` inside the transaction that holds the row lock for the
whole job, citing the memory worker as precedent. Both halves are wrong.

`poseidon/scripts/memory_worker.py` closes its claim transaction **before** doing any work:

```python
with engine.begin() as conn:
    claimed = claim_idle_conversations(conn, idle_minutes=..., limit=CLAIM_BATCH_SIZE)
if not claimed:
    return 0
...
for row in claimed:
    _process_one(engine, settings, role_client, registry, prompts_dir, row)
```

The claim commits on its own. That is the opposite of the design's "increment inside the
job-holding transaction".

And the memory worker's own crash test asserts attempts are **not** incremented by a crash
(`tests/test_memory_worker.py`):

```python
crashed = _outbox_row(pg_engine, cid)
assert crashed.status == "pending"
assert crashed.attempts == before.attempts == 0
```

So the cited precedent does not establish a crash cap — it explicitly proves the absence of one. A
row that crashes the process is retried forever, bounded by nothing. Under the design as written,
`REPORT_MAX_ATTEMPTS=2` would not hold across hard crashes, because the increment rolls back with
the unfinished work.

**Required change:** a durable claim whose attempt count commits separately from the job — a
committed lease with expiry recovery, or a separate attempt ledger. The design must also name which
connection holds the claim versus reads definitions and writes telemetry; the proposed worker-role
grant covers only `report_run`.

**Acceptance check (from the review, endorsed):** kill the worker after each claim on the same run;
after the configured number of attempts the run is terminal, no longer occupies the pending unique
index, and a fresh request for that month can proceed. The test must kill the process, not raise a
caught exception — the existing test shows those are different cases.

---

## C03 — P1 — Existing period carry loses the year window and the comparison

**ACCEPT — both halves reproduced against running code**, offline, no database and no model calls.

Repro through the real `parse_periods`, persisting slots exactly as `parsing/pipeline.py:507–510`
does (start date only):

```
TURN 1: "GP for Maersk for 2025 by month"
  period_a = [2025-01-01, 2026-01-01)   source=text     <- correct full year
  -> slot persisted as start-date only: 2025-01-01

TURN 2: "then by port"        (bare follow-up)
  period_a = [2025-01-01, 2025-02-01)   source=carry    <- silently January only
```

```
TURN 1: "GP for Maersk in July 2026 vs June 2026"
  period_a = [2026-07-01, 2026-08-01)   period_b = [2026-06-01, 2026-07-01)

TURN 2: "why did GP drop?"    (bare follow-up)
  period_a = [2026-07-01, 2026-08-01)   period_b = None  <- comparison gone
```

Two independent causes, both explicit in the code:

1. **Granularity.** `period_parser.py:462–467` reconstructs a carried period as
   `_month_window(carried.year, carried.month)` — the slot stores a first-of-period date, so the
   carry path cannot know a year produced it.
2. **Comparison.** `pipeline.py:509–510` writes `period_b` as `None` whenever the turn's text did
   not name a comparison. The comment says so outright: *"period_b CLEARS whenever this turn's text
   did not ask for a comparison."*

**One refinement to the review's framing.** This is not an undiscovered bug. The module docstring
(`period_parser.py:64–74`) documents it as a deliberate v1 limitation, silent by design, and names
the fix and its owner:

> ``ConversationSlots.period_a`` stores a first-of-period DATE, not a window, so the carry path
> cannot know which granularity produced it… Widening the slot to carry a granularity (or the
> window itself) is the fix, and it belongs to **whichever phase revisits ``ConversationSlots``**.

The reports feature *is* that phase — it is the first thing to need year windows and comparison
carry. So the finding stands, restated: the design inherited a known debt without noticing it had
come due, and treated adding office/report fields to the slots as sufficient.

**Required change:** persist full windows (or explicit granularity plus end bound) and report-aware
comparison carry/clearing rules, with A/B orientation defined when moving between a report and the
query skill. Updating the dataclass is not enough — `history.py:836` `slots_to_json` is an explicit
field-by-field serializer and must be changed with it.

**Sequencing consequence:** this gates Phase 17 in the reworked plan. The phase's own gate includes
"GP for X for 2025 by month, then by port" and the default July/August report comparison — both of
which fail today.

---

## C07 — P2 — Phase 15 report persistence has no complete storage path

**ACCEPT.** Under D39 the SPCS service has no object store at all — confirmed in `07-infrastructure.md`
as reconciled, and matching Triton's live `deploy/spec.yaml`, which runs one container with no
`minio`. Report bytes are meant to live in Postgres. But the design creates a report in its Phase 15
*before* the `report_run` table arrives in Phase 16, so those bytes have no row to live in and no
durable key. Restart the API, reload the conversation, ask for the PDF, and nothing can find it.

Recording a correction to my own earlier reading: I briefly concluded this finding was a non-issue
after checking `docs/architecture/07-infrastructure.md` and `core/artifacts.py`, both of which still
described D20's in-service MinIO. They were stale; D39 supersedes them. Those docs are now
reconciled (commit `1477d7b`), and the general lesson is recorded in memory as
`poseidon-decision-supersession-check`.

**Required change:** name the durable backing store, lookup key, authorization and retrieval route
for a Phase-15-era report, and its migration into the Phase 16 tables. In the reworked plan this
mostly dissolves: report generation and the `report_run` table land in the same phase (17), so the
ordering that created the gap is gone. Confirm that is the intended resolution rather than assuming it.

---

## Not yet worked

Listed so the gap is explicit. None of these have been assessed against the repository yet.

| # | Sev | Subject | Status |
|---|---|---|---|
| C02 | P1 | Recording the send after delivery cannot make email idempotent | not started |
| C04 | P2 | Share-of-total reapplies the filter the denominator must remove | not started |
| C05 | P2 | A queued run has no immutable definition snapshot | not started |
| C06 | P2 | Split-view URL and report lookup can refer to different runs | not started |
| C08 | P2 | API PDF link cannot reuse the artifact anchor in Auth0 mode | partially handled — recorded in doc 01 and doc 05 during reconciliation, but no design change written |
| C09 | P1 | Numeric-token membership does not establish narrative grounding | not started |
| C10 | P2 | Forced narrative tool calls need a provider seam change | context changed — the migration moves this to AI SDK's tool-choice contract, so the finding needs restating before it is worked |
| C11 | P2 | Time-series results can inherit a top-five cap | not started |

Also outstanding from the review: the **nine owner questions**, the **table-onboarding review**
assigned to Claude, and the **supplier-perspective** requirement the owner confirmed (supplier-office
reports must rank suppliers, with customers as a secondary breakdown) — none of which are addressed
in any current design document.
