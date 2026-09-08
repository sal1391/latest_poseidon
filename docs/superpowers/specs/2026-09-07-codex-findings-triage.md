# Codex review findings — triage record

Date: 2026-09-07
Source: `docs/superpowers/specs/2026-09-05-monthly-performance-reports-codex-review.md`
Status: **All twelve findings worked.** C10 is restated rather than resolved, because the migration
changes its premise. What remains outside this document is listed at the end: the nine owner
questions, the table-onboarding review, and the supplier-perspective requirement.

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

## C02 — P1 — Recording the send after delivery cannot make email idempotent

**ACCEPT.** The design's §5.3 order is: render, *call the mailer synchronously*, then record the
outcome — with a client-generated `idempotency_key` carrying a unique constraint. That constraint is
on a row written **after** the send, so it cannot prevent one:

- Two concurrent requests with the same key both pass their existence check, both call the mailer,
  and only then does one lose the insert race. Two emails have already left.
- A crash or timeout after the transport accepts but before the database write leaves a retry free
  to deliver a second copy.

The schema compounds it: `status text check (status in ('sent','failed'))` has **no state for "in
flight" or "outcome unknown"**. A send whose result is genuinely unknown must be recorded as
something, and neither available value is honest.

**Required change:** reserve the key durably *before* attempting delivery, bound to the full request
(run, normalized recipients, attachment choice), and serialize competing requests on it. Add
in-progress and unknown-outcome states. An unknown outcome must not be treated as an ordinary safe
retry — a local unique constraint cannot make an external side effect exactly-once, and the design
should say so rather than implying otherwise. Define whether retrying a known failure reuses the key
and how a deliberate resend gets a new one.

---

## C04 — P2 — Share-of-total reapplies the filter the denominator must remove

**ACCEPT.** Two rules in §7.3 contradict each other for the office column, and the design never
states which runs first.

Scope (D41): *"`skill.run` stamps `{office_column: (office_value,)}` onto every spec it builds (both
windows of a comparison, **both queries of a share**)"*

Share of total: *"`share_of_total: list[str]` — filter columns dropped for the denominator (**the
carried office counts as a filter here**)"*

Rule one stamps the office onto both queries. Rule two drops it from the denominator. An
implementation that applies the scope stamp *after* building the denominator restamps the office,
making the denominator the office's own total — so every share returns 100%.

The intent is clear; only the construction order is missing.

**Required change:** state the order explicitly — build effective filters including the carried
office, validate the requested removals, derive denominator filters by removing those columns, and
do **not** restamp. Identity-based row scope (D16) is separate and must stay enforced on both
queries; only the office filter is dropped.

**Acceptance check:** a customer with office GP 20 and whole-book GP 100 returns 20%, and the proof
block shows the office predicate on the numerator only, with the customer predicate on both.

---

## C05 — P2 — A queued run has no immutable definition snapshot

**ACCEPT, with a precision the review did not draw.** The run row *does* snapshot the period:

```
report_run
  id uuid pk (uuid7), definition_id uuid fk, period_a date, period_b date, basis text, ...
```

`period_a`, `period_b` and `basis` are copied onto the run. **The scope is not.** `slice_column` and
`slice_value` are reachable only through `definition_id`, and the definition carries a `version`
column for optimistic concurrency that the run does not record.

So: queue a Gibraltar run while the worker is down, edit the definition to another office, and the
worker generates the office the definition says *now* — not the one that was requested. The
definition copy embedded in the finished payload (§4.10) happens after generation and cannot prevent
it.

**Required change:** snapshot the definition version and the generation inputs — scope included —
atomically at enqueue. Then answer the owner question underneath it: can a definition change office
once it has runs, or does that require a new definition? Also state which recipient list a preview
or send uses, and preserve the finally-selected addresses on the send record.

---

## C06 — P2 — Split-view URL and report lookup can refer to different runs

**ACCEPT.** The panel route is `/reports/:runId` — it names one specific run. `report_lookup`
deliberately follows the current `ok` run. Those are different selectors over the same conversation.

Open run R1 from an email after R2 superseded it, and the panel shows R1's numbers while the chat
answers from R2's. The design's mitigation is a warning inside a tool proof, which tells the user
the run changed but does not make the report beside the chat match the figures under discussion. The
same split opens if an admin regenerates while a user has the page open.

**Required change:** finish the current-run policy at the UI boundary — redirect or visibly switch
the panel to the current run, reconcile the conversation's reference and tags, and mark the emailed
version as superseded. Resolve **one** run per chat turn so two lookup calls in a turn cannot
straddle a regeneration. If historical viewing is wanted, define it as its own mode rather than
letting it happen by accident — that is one of the review's owner questions.

---

## C08 — P2 — An API PDF link cannot reuse the existing artifact anchor

**ACCEPT — verified in the frontend.** `frontend/src/ui/message-parts/ArtifactPart.tsx` is a plain
anchor, and its own docstring states the assumption the migration breaks:

```tsx
/** ... the file name links straight to `url` -- a presigned GET the browser
 * fetches directly ... the backend is never in the path of the file's bytes),
 * so this needs no backend proxy route ... */
<a className="artifact-link" href={url} target="_blank" rel="noopener noreferrer" download={name}>
```

Bearer tokens are attached by `requestWithAuth` in `frontend/src/api/client.ts`, which every fetch
call site routes through deliberately — the docstring records that unifying them closed a real
carryforward where one call site silently missed the injector. **A browser navigating an `href` does
not go through it.** So substituting `/api/reports/runs/{id}/pdf` into `url` produces an
unauthenticated request, which the backend correctly rejects.

D39 forces this: with no object store on SPCS there is nothing to presign, so report bytes must come
from an authenticated route.

**Required change:** an authenticated binary-fetch-and-download path in the frontend contract, using
the existing token provider, applied to both the in-chat card and the panel download. Never put a
bearer token in a URL. Recorded in doc 01 and doc 05 during the reconciliation (commit `1477d7b`),
but no design change is written yet.

**Note for the migration:** this is worth solving *once*, in the Next.js client, rather than porting
the current anchor and rediscovering the problem.

---

## C09 — P1 — Numeric-token membership does not establish grounding

**ACCEPT.** §4.7's check tokenizes the prose into numeric tokens and requires each to *match a
display string derived from the payload*. That is set membership: "does this number appear anywhere
in the report?" It cannot detect:

- **Right number, wrong entity.** "Customer A's GP is 200" passes when A is 100 and B is 200.
- **Right number, wrong period or metric.** Any figure from any table satisfies any sentence.
- **Wrong direction.** "GP rose" when it fell, whenever the magnitude appears somewhere.

The rank exemption widens it further: *"ranks 1–10 are exempt"* leaves every small quantity
unchecked, so a false claim built from small numbers passes by construction.

The fixture strategy has the same shape of gap. *"pinned against the mockup narrative as a fixture:
it must pass every figure the mockup states"* proves the checker **accepts** a correct narrative. It
proves nothing about **rejection**, which is the only thing a grounding check exists to do.

**Required change:** either narrow the documented guarantee honestly — call it numeric vocabulary
checking, not grounding — or bind claims to structured fact references (entity, metric, period,
value, direction) and render numeric claims deterministically from them. Check every model-authored
field, not just `narrative_md`: insights, context notes and the suggested-question chips are all
model-written and all currently unchecked.

**Acceptance check:** the suite must *reject* swapped customer values, swapped A/B values, wrong
units, wrong directions, and false claims containing only numbers 1–10 — while still accepting a
correct narrative. Template fallback stays available.

---

## C10 — P2 — Forced narrative tool calls need a provider seam change

**RESTATED, not resolved — the migration changes the premise.** The finding was that
`RoleClient.invoke` accepts tools but no per-call tool choice, and Bedrock's request builder emits a
`toolConfig` containing only the tool list, so passing a single schema does not force a call.

Under the migration, per-invocation tool choice becomes AI SDK 6's concern, and the custom
`RoleClient`/Bedrock encoder is on the list of things being replaced (migration design §3). Spending
effort adding tool-choice to a Python provider layer that is being retired would be waste.

**Restated requirement:** the target AI SDK integration must define a per-invocation tool-choice
contract, and Cortex must reach parity on the same contract (D40). Validate the returned tool name,
count and schema before accepting narrative output, and preserve default router behaviour when no
choice is supplied. If Q5 lands on coexistence and the Python loop serves narrative calls during the
transition, the original finding revives unchanged for that window — decide it with Q5, not before.

---

## C11 — P2 — Time-series results can inherit a top-five cap

**ACCEPT — the default is confirmed in code.**
`backend/poseidon/tasks/data_qa/skills/metric_query/schema.py:39`:

```python
top_n: int = Field(default=5, ge=1, le=50, description="Row limit for breakdowns, 1-50.")
```

D45 adds `MONTH`/`QUARTER`/`YEAR` pseudo-dimensions and promises chronological ordering, but no rule
exempts time buckets from `top_n`. So the design's own worked example — "GP for Maersk for 2025 by
month" — returns **five of twelve months** by default, silently. Ordering chronologically returns
the first five; ordering by GP returns a different incomplete five. The ceiling of 50 also cannot
hold a `[LOC_NM, MONTH]` cross of any real width.

There is a second, subtler gap. The current schema **rejects** `group_by` with `compare_period`
outright (`_reject_breakdown_with_compare`), and the design now makes that combination legal. But it
does not define what a comparison means against a time bucket — an outer join on different calendar
dates does not align comparable periods.

**Required change:** define complete bucket coverage for a requested window, with an explicit
missing-bucket policy; make truncation visible rather than silent; state precedence between
chronological ordering and `order_by`; and either specify how `compare_period` aligns with
MONTH/QUARTER/YEAR or keep rejecting that combination until it is implemented. Rejection is a
legitimate answer; silence is not.

---

## What remains outside this document

- The review's **nine owner questions** — product decisions, not technical ones. Several are
  referenced above where a finding depends on one (C05 depends on definition identity; C06 on the
  historical-report policy).
- The **table-onboarding review** Codex assigned: trace one table end to end from source
  investigation to certified queryability in chat, and identify every manual or code change on the
  path.
- The **supplier-perspective** requirement the owner confirmed — supplier-office reports must rank
  suppliers with customers as a secondary breakdown. No current design document covers it, and it
  touches the report contract, grounded chat, labels, and the email/PDF content equally.
