# Morning review — 2026-09-08

Overnight run of 2026-09-07. Branch: **`docs-reconcile-d39`**, four commits, **not pushed**.
Docs only — no implementation code was written, nothing was merged, nothing was pushed.

---

## Read this first: five questions block the next step

These are in `2026-09-07-nextjs-migration-design.md` §9. Q1 and Q3 change what actually gets built,
so no implementation should start until they are answered.

| # | Question | My lean |
|---|---|---|
| **Q1** | Where does the parsing pipeline live? 1,687 lines of deterministic Python that runs on *every* turn before routing. Keeping it in Python means a network hop per turn; moving it means porting logic that C03 just proved is buggy. | Stays Python for now. Porting buggy logic doubles the debugging surface. Revisit after C03 is fixed. |
| **Q2** | Where does background work run? Next.js and AI SDK give you no durable background execution. | Workers stay Python containers. Fewest moving parts. |
| **Q3** | Migration authority — Drizzle or Alembic? Both evolving one schema is a corruption path, not a style question. | Drizzle, **but** migrations 0009/0010 encode Postgres grants and roles that Drizzle will not reproduce by itself. Those must be carried across deliberately. |
| **Q4** | Does Auth0 stay as the identity provider behind Auth.js? They are separate choices — Auth.js is a session library, Auth0 is a provider. | Keep Auth0. It preserves your tenant, the role claims, and the Phase 9 tenant-day work. |
| **Q5** | Coexistence or hard cutover? Keep the Vite app running while Next.js is built beside it, or go dark for a window? | Coexistence, so each seam can be proven against the running system. |

**Also waiting on you:** the Codex review's nine owner questions (product decisions — definition
identity, who may generate reports in chat, historical report links, partial months, and so on), and
whether the five untracked August walkthrough docs belong in the repo.

**And:** PR #3 from earlier in the session is still open and unmerged.

---

## What I did

### 1. Reconciled the architecture docs (commit `1477d7b`)

Docs 00, 01, 02, 05, 07, 08 described D20's world — Postgres and MinIO as containers inside the SPCS
service on block volumes — while D39 had already replaced that with managed Snowflake Postgres. That
staleness is exactly what made me answer the SPCS storage question wrong twice in one conversation.

Changes worth your eye:

- **I added D34–D45 to doc 00's decision log.** This is a structural change, not just an edit. The
  root cause of the drift is that decisions were living only inside a feature spec. Doc 00 is now
  the single index of *what was decided*; the spec stays authoritative for detail. Tell me if you'd
  rather they stayed in the spec.
- **I marked D20, D32 and D33 as revised rather than rewriting them.** The old text stays visible
  with a note on what changed and what still stands. Decision logs should show their own history.
- **I flagged the Snowflake Postgres backup guarantees as UNVERIFIED** against your RPO 24h / RTO
  next-business-day, rather than asserting they're met. I can't check that without a Snowflake
  session. It's written as a gate item for whoever has account access.
- Local MinIO and EC2 S3 are untouched — D39 only narrows SPCS, and brief PDFs keep an object store
  where one exists.

### 2. Verified the Triton pattern against the real repo

Your local clone was **113 commits stale**. I fetched `origin/main` without touching your working
tree and read the live `deploy/spec.yaml`. It confirms your instinct exactly: one container, no `db`
and no `minio`, `DATABASE_URL` injected from a Snowflake secret, Snowflake authenticated by the
platform's OAuth token with no stored credential.

Two scars from that deployment now recorded in doc 07: the token file rotates so it must be read
fresh per connection, and the role must **not** be set on an OAuth connection or the token breaks.

**One gap I want you to see:** Triton stores no binary in Postgres — no `LargeBinary` or `BYTEA`
column exists anywhere in its schema. So D39's "report HTML and PDF as bytes in Postgres" has **no
precedent in your estate**. It's ordinary Postgres and it will work, but sizing and retrieval
latency are unmeasured. I wrote it up as something to measure at a gate, not as settled.

### 3. Updated memory

Recorded the three confirmed decisions. Two things worth knowing:

- **I corrected a memory that had gone backwards.** It recorded an external reviewer's Next.js /
  Tailwind / Drizzle suggestion as "refuted, no repo basis." You've since confirmed that migration
  as a requirement. The memory now says so, and notes that reviewer was early rather than wrong.
- Added a new memory on checking for decision supersession before asserting infrastructure facts —
  the lesson from my two wrong answers.

### 4. Wrote the migration design (commit `d573f2c`)

`docs/superpowers/specs/2026-09-07-nextjs-migration-design.md`. This is the C00 deliverable — the
review's only P1 against the design as a whole. It maps every seam to its target owner, grounded in
measurements of what's actually there (~19,000 lines of Python, sized per area) rather than generic
migration advice.

**The part I'd most want you to read is §4, the streaming contract.** It's the highest-risk seam,
and three things in your current implementation have no automatic AI SDK counterpart: the `tool_seq`
correlation that replay depends on, the per-dispatch de-duplication inside `SseEnvelopeSink`, and
your ten custom part kinds. I've required a recorded-turn contract test before any UI work, because
the de-duplication bug is invisible in a browser — it only shows up on specific dispatch shapes.

It also proposes a replacement phase plan (15–19) following Codex's advice to settle architecture,
then ship *one* report → grounded-follow-up workflow, then email, then platform.

### 5. Triaged four review findings (commit `4526345`)

`docs/superpowers/specs/2026-09-07-codex-findings-triage.md`. Verdicts backed by running code, not
by re-reading the review.

**C01 — confirmed, and the design cited a precedent that doesn't exist.** The design claimed your
memory worker as evidence for a crash-safe attempt cap. It's the opposite: `memory_worker.py` closes
its claim transaction *before* doing the work, and its own crash test asserts `attempts == 0` after
a process death. So the cited precedent proves there is *no* crash cap. Under the design as written,
`REPORT_MAX_ATTEMPTS=2` would not survive hard crashes.

**C03 — confirmed by running your actual parser.** Offline, no database, no model calls:

```
"GP for Maersk for 2025 by month"  ->  [2025-01-01, 2026-01-01)   correct
"then by port"                     ->  [2025-01-01, 2025-02-01)   January only

"GP for Maersk in July vs June"    ->  A=July  B=June
"why did GP drop?"                 ->  A=July  B=None             comparison gone
```

That second case is precisely the reports scenario: your report shows July vs June, the user asks
why GP dropped, and the comparison silently vanishes.

**A refinement the review missed:** this is documented in your own code as a deliberate v1
limitation, with the fix already named and assigned to "whichever phase revisits
`ConversationSlots`." The reports feature *is* that phase. So the finding stands, restated — the
design inherited a known debt without noticing it had come due.

**This gates Phase 17.** Two items in that phase's own gate fail today.

---

## Corrections I owe you

I got the SPCS storage question wrong twice in one conversation, in opposite directions. First I
said SPCS removes the object store (right). You said you didn't follow, I re-checked against the
architecture docs and `artifacts.py` — both stale — and confidently retracted a correct statement.
The retraction was the error.

That's why step 1 was reconciling those docs, and why the lesson is now in memory. Retracting a
right answer costs more than the original uncertainty would have.

---

## Still open

Eight findings not yet worked: C02, C04, C05, C06, C08 (partially handled), C09, C10 (needs
restating post-migration), C11. Plus the table-onboarding review Codex assigned, and the
supplier-perspective requirement you confirmed — supplier-office reports must rank suppliers with
customers as a secondary breakdown, which no current design document covers.

---

## Suggested next step

Answer Q1–Q5, then I can write the Phase 15 implementation plan and present it for your go. If you'd
rather I keep working the remaining findings first, that's fine too — they don't depend on your
answers, and none of them are blocked.

Nothing is pushed. Say the word and I'll push the branch and open a PR so you can read it from your
phone.
