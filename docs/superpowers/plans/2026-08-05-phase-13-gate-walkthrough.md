# Phase 13 Gate Walkthrough — Personalization

> **RESULT (2026-08-05, run live with Carlos, Path A): ALL SIX GATES
> PASSED.** Gate 1 suites green; Gate 2 instruction injection proven (USD-k
> styling followed) though it exposed F1; Gate 3 real live distillation
> produced exactly the planted scope entry and nothing from tool output;
> Gate 4 restart-survival + re-arm proven live; Gate 5 delete-save-reopen
> holds, meter tracks (restore half skipped per F6); Gate 6 all four
> isolation proofs passed. Six findings captured (F1-F6 below) — F1/F3/F4
> are live-synthesis bugs in pre-P13 seams, F2 is test hygiene, F5/F6 are
> Carlos's product directives on the settings surface. None indict Phase
> 13's own machinery, which did everything its gates asked of it.

Live-validation script for the six Phase Gate items in
`docs/superpowers/plans/2026-08-04-phase-13-personalization.md`. Work top to
bottom; each gate has exact commands and an expected result. Check the boxes
as you go — this doc is the live record, same convention as the pile
walkthrough.

> **State: PHASE CLOSED, ready for your gate.** All 5 tasks reviewed clean;
> final opus review's 4 Important findings fixed in one wave, re-review
> clean. **HEAD: `ff9a1fa`** (16 commits, `28f6185..ff9a1fa`). Final suites:
> **offline 1600 passed/12 skipped · pg 320 passed/1 skipped · vitest 171
> passed**, all lints clean. Nothing merged, nothing pushed.

---

## Choose your path first

The dev stack runs `LLM_MODE: stub` by default. The stub distiller
**deliberately answers "no memory changes"** (it refuses to fabricate
preferences you never stated — that's a feature), so:

- **Path A — LLM_MODE=live (recommended, ~20 min):** full validation of all
  six gates, including watching a real distilled memory version appear and a
  real answer obeying your instruction. Needs your AWS creds pasted into
  compose (same manual step as Aug 3 — I can't script real credentials into
  files).
- **Path B — stub mode (~15 min):** gates 1, 4, 5, 6 fully; gates 2 and 3
  validate the *machinery* (prompt provably changes, distillation cycle
  provably completes) but you won't see answer style change or new memory
  entries appear. Fine for a first pass; redo 2/3 under Path A later.

**Path A setup (skip for Path B):** in `infra/docker-compose.yml`, on BOTH
the `backend` and `worker` services, set `LLM_MODE: live` and add your
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION`
(us-east-1) to the `environment:` block. (The containers deliberately do NOT
read `backend/.env` — compose env is the only way in.) Then:
`docker compose -f infra/docker-compose.yml up -d backend worker`
**Revert these edits before any commit** — same as Aug 3.

---

## Pre-flight (5 min)

- [ ] Docker Desktop running, then from the repo root:
  ```powershell
  docker compose -f infra/docker-compose.yml up -d
  docker compose -f infra/docker-compose.yml ps
  ```
  Expect: `db`, `minio`, `backend`, `frontend`, `worker` all Up. **Note the
  `worker` service is new this phase** — if it's missing, you're on a stale
  compose file.
- [ ] Windows-restart quirk (known since Aug 3): if the machine rebooted
  since the stack last ran, bind mounts can serve stale files —
  `docker compose -f infra/docker-compose.yml restart backend frontend worker`
  clears it.
- [ ] Migration at 0008:
  ```powershell
  docker exec infra-db-1 psql -U poseidon -d poseidon -c "SELECT version_num FROM alembic_version;"
  ```
  Expect: `0008` (personalization tables live).
- [ ] Worker heartbeat:
  ```powershell
  docker logs --tail 5 infra-worker-1
  ```
  Expect: JSON lines, `"event": "memory_worker_started"` then
  `"event": "memory_worker_cycle"` roughly every 60s, no tracebacks.

---

## Gate 1 — Suites green

You can trust the wave's recorded runs (ledger bottom +
`final-wave-report.md`) or re-run yourself:

```powershell
cd backend
# offline (PERPLEXITY_API_KEY withheld as always)
.\.venv\Scripts\python.exe -m pytest -m "not pg and not minio and not pdf and not router_live and not research_live"
# pg
$env:DATABASE_URL = "postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon"
.\.venv\Scripts\python.exe -m pytest -m pg
cd ..\frontend
npm run test
```

- [ ] Offline suite green (1600 passed, 12 skipped)
- [ ] pg suite green (320 passed, 1 skipped — now covers all 3
      personalization tables' RLS, the worker's claim/retry/durability, and
      the /api/me routes)
- [ ] vitest green (171 passed — now covers the settings surface)

---

## Gate 2 — Set an instruction, see it reach the prompt

- [ ] Open http://localhost:5173 → click your name (bottom of sidebar) →
      **Settings**. The panel should open with an empty instruction box.
- [ ] Type: `Always show GP figures in USD thousands (k).` → **Save**.
      Expect: no error, the box keeps your text. (The fix wave specifically
      added: if a save fails, your typed text must survive — worth one
      deliberate check later if you're curious: stop the backend container,
      hit Save, confirm the text stays put with an error message.)
- [ ] Ask in chat: `top GP customers for Port of Singapore in April 2026`

**Path A:** the answer's synthesis should present GP in USD k. That's the
gate.

**Path B (stub — prove the prompt changed):** the stub router ignores style,
but the prompt hash it records can't lie. Ask the SAME question once
*before* setting the instruction and once *after*, then:

```powershell
docker exec infra-db-1 psql -U poseidon -d poseidon -c "SELECT role, left(prompt_hash, 16) AS hash, created_at FROM llm_calls WHERE role = 'router' ORDER BY created_at DESC LIMIT 4;"
```

- [ ] The hash for the after-instruction turn **differs** from the
      before-instruction turn (same question, same prompt version — the only
      changed input is your injected instruction). Bonus proof: clear the
      instruction in Settings, save, ask again → the hash returns to the
      original value byte-for-byte.

---

## Gate 3 — An idle conversation becomes a memory version

The worker claims conversations idle for `MEMORY_IDLE_MINUTES` (default 30).
For the rehearsal, run the worker at 1 minute:

```powershell
docker compose -f infra/docker-compose.yml stop worker
docker compose -f infra/docker-compose.yml run --rm --no-deps -e MEMORY_IDLE_MINUTES=1 worker
```

(leave this terminal open — it streams the worker's JSON logs live)

- [ ] In the browser, have a short real conversation — **at least 2 of your
      own messages** (one-question conversations are deliberately skipped as
      too trivial to distill), e.g. the Singapore question plus a follow-up
      (`what about March?`).
- [ ] Wait ~2 minutes watching the worker terminal. Expect a
      `memory_distill_done` log line carrying your conversation's id.

**Path A:**
- [ ] Reopen Settings → the memory list shows new entries, attributed
      "Updated by Poseidon after your conversation on <date>", and the
      version list gained a version.

**Path B (stub):** no entries will appear (stub answers "nothing worth
remembering" by design). Instead prove the cycle:
```powershell
docker exec infra-db-1 psql -U poseidon -d poseidon -c "SELECT status, attempts FROM memory_outbox ORDER BY updated_at DESC LIMIT 3;"
docker exec infra-db-1 psql -U poseidon -d poseidon -c "SELECT kind, status, mode FROM turn_run WHERE kind = 'memory_update' ORDER BY created_at DESC LIMIT 3;"
```
- [ ] Outbox row `done`, attempts `0`; a `turn_run` row with
      `kind=memory_update`, `status=ok` — the run-log trail the plan requires.

When done, restore the normal worker: Ctrl-C the `run` terminal, then
`docker compose -f infra/docker-compose.yml up -d worker`.

---

## Gate 4 — Worker restart survival (decision D31's whole point)

- [ ] Stop the worker: `docker compose -f infra/docker-compose.yml stop worker`
- [ ] Have another 2-message conversation (creates a fresh `pending` outbox
      row while no worker is alive).
- [ ] Confirm the pending row exists:
  ```powershell
  docker exec infra-db-1 psql -U poseidon -d poseidon -c "SELECT status, attempts, last_turn_at FROM memory_outbox WHERE status = 'pending' ORDER BY last_turn_at DESC LIMIT 3;"
  ```
- [ ] Wait until the newest row is >1 minute old, then start a 1-minute
      worker again:
  `docker compose -f infra/docker-compose.yml run --rm --no-deps -e MEMORY_IDLE_MINUTES=1 worker`
- [ ] Expect: within ~1 cycle the row is claimed and completes
      (`memory_distill_done` in the logs; row flips to `done` in SQL). The
      work queued while the worker was dead survived the outage and
      completed after restart — nothing was lost, nothing wedged.
- [ ] Optional harder variant: Ctrl-C the worker *mid-cycle* and re-run it —
      the row must still end `done` (no half-claimed limbo; the schema has no
      "processing" state on purpose).
- [ ] Restore the normal worker when done (`up -d worker`).

---

## Gate 5 — Edit, save, restore, and the character meter

Path B only — seed two entries first (Path A already has distilled ones).
This curl runs as the same `dev|local` user your browser is:

```powershell
curl.exe -s -X PUT http://localhost:8000/api/me/memory -H "Content-Type: application/json" -d '{\"entries\":[{\"type\":\"preference\",\"statement\":\"Show GP in USD thousands\",\"source_conversation_id\":\"00000000-0000-0000-0000-000000000001\",\"at\":\"2026-08-05T12:00:00Z\"},{\"type\":\"scope\",\"statement\":\"Focus on Singapore and Rotterdam ports\",\"source_conversation_id\":\"00000000-0000-0000-0000-000000000001\",\"at\":\"2026-08-05T12:00:00Z\"}]}'
```

- [ ] Reopen Settings → both entries visible, attributed "Edited by you",
      character meter shows a small fraction of the 8,000 cap (the meter
      reads the cap from the server — it is not hardcoded in the frontend).
- [ ] Delete one entry (it disappears from the list but nothing is saved
      yet), then **Save**. Expect: list now has 1 entry; the Version History
      gained a new version *immediately* (a fix-wave-adjacent fix — the list
      must not go stale after save).
- [ ] In Version History, **Restore** the earlier version. Expect: both
      entries return, as ANOTHER new version (append-only — restoring never
      rewrites history), attributed "Edited by you".
- [ ] Confirm append-only in SQL if you like:
  ```powershell
  docker exec infra-db-1 psql -U poseidon -d poseidon -c "SELECT version, created_by, jsonb_array_length(entries) AS entries FROM user_memory WHERE user_sub = 'dev|local' ORDER BY version;"
  ```
  Expect: versions 1, 2, 3 — all still present, entry counts 2, 1, 2.

---

## Gate 6 — Two users never see each other's personalization

Your browser is the default dev user (`dev|local`). Pose as a second user
with the act-as header (dev-mode feature, Phase 9):

```powershell
# bob's settings: default empty shape, NOT your instruction
curl.exe -s http://localhost:8000/api/me/settings -H "X-Dev-User: bob"
# bob's memory: 404 (bob has none), NOT your entries
curl.exe -s -o NUL -w "%{http_code}" http://localhost:8000/api/me/memory -H "X-Dev-User: bob"
```

- [ ] First call returns `"system_instruction": ""` (empty default) — not
      your saved instruction.
- [ ] Second call prints `404`.
- [ ] Now write as bob and confirm it never reaches you:
  ```powershell
  curl.exe -s -X PUT http://localhost:8000/api/me/settings -H "Content-Type: application/json" -H "X-Dev-User: bob" -d '{\"system_instruction\":\"bob was here\"}'
  ```
  Reopen your Settings in the browser → still YOUR instruction, no "bob was
  here".
- [ ] Belt-and-braces (the RLS divergence you asked to be re-verified): there
      is deliberately NO admin path into these tables. The migration grants
      `poseidon_admin` nothing on any of the three — re-provable any time:
  ```powershell
  docker exec infra-db-1 psql -U poseidon -d poseidon -c "SELECT has_table_privilege('poseidon_admin', 'user_memory', 'SELECT') AS admin_can_read_memory;"
  ```
  Expect: `f`.

---

## Findings from the live run (2026-08-05, Carlos + controller)

- **F1 — CRITICAL (live-only): LLM narratives fabricate data.** All five
  Path-A answers in the gate conversation (`019fd215-23f9-...`) show it:
  the deterministic table + proof block are correct (e.g. real top customer
  Meridian Marine 70,119), while the model's prose table invents customers
  and values ("MAERSK LINE $127.4k", recurring $127.x k motif; answer 2
  invents volume/margin/fixture metrics nobody queried). Instruction
  styling (USD k) IS obeyed — on fabricated content. Hypothesis: the
  synthesis step doesn't receive (or receives only a digest of) the tool
  results in live mode; stub mode masks it because DevDeterministicRouter's
  synthesis is programmatic. First-ever full live chat turn — Aug 3's live
  proof was a provider smoke, not a turn. Owner: systematic-debugging fix
  round after the walkthrough (P5/P6 run_turn seam, not P13 code).
- **F2 — pg-suite writes leak into the real `dev|local` user.** Some pg
  test(s) hit the live API without an `X-Dev-User` header, landing as the
  default dev user: every full pg run added a 1-entry + 0-entry
  `created_by='user'` version pair to dev|local's real memory (20 junk
  versions found and wiped during the gate) and re-armed dev|local outbox
  rows (18 retired). Fix: find the header-less test(s), give them
  run-unique subs per the suite's own convention.
- **F4 — markdown tables in text parts render as raw pipe characters.**
  The live model emits GFM tables in its prose; the frontend's text-part
  renderer shows them as literal `| Rank | Customer |` lines (stub-mode
  narratives never emitted tables, so this was invisible until live).
  Candidate fixes, decide in the F1 pass: prompt-rule forbidding the model
  from duplicating the deterministic table part in prose (preferred — the
  real table already renders properly), and/or GFM table support in the
  text renderer.
- **F5 — UX (Carlos, Gate 3): a no-op Save Memory still creates a new,
  identical version** — repeated clicks stack junk versions in the history.
  Fix: disable Save until the local working list actually differs
  (the `entriesDirty` flag from this phase's fix wave is exactly the
  signal), and consider labeling it "Save changes". Same treatment for the
  instruction Save via `instructionDirty`.
- **F6 — PRODUCT DECISION (Carlos, Gate 3): remove Version History from
  the settings UI.** "Business users don't work like that — no version
  history, just memory they can delete." Panel becomes: entries list +
  per-entry delete + save. Recommended shape (pending Carlos's nod on the
  split): backend keeps append-only versioning invisibly (audit/undo for
  support; already built+tested; zero user-facing surface), UI drops the
  Version History section and Restore buttons entirely. Supersedes doc 01
  §9's settings-surface spec and part of Task 5 — docs need amending when
  implemented. Subsumes F5's restore-side concerns (Save-disable-when-clean
  still applies).
- **F3 — live model freelances clarification.** Gate turn 3 ("how did
  meridian do...") produced an LLM-authored "which Meridian?" prose answer
  (text part only) instead of the deterministic clarify-chips flow. Same
  grounding family as F1; fold into the same debugging pass.

## If something misbehaves

- **Settings button 404s everything** → backend isn't in `CHAT_MODE: live`
  (the /api/me routes mount only there; compose sets it, but check).
- **Worker logs nothing** → `docker logs -f infra-worker-1`; if it exited,
  `docker compose -f infra/docker-compose.yml up -d worker` and check the
  startup banner for config it printed.
- **Distillation never fires** → the conversation needs ≥2 of YOUR messages
  and must be idle past the threshold; check
  `SELECT * FROM memory_outbox ORDER BY updated_at DESC LIMIT 5;` — a
  `done` row with your conversation and 0 attempts + no `turn_run` row means
  it was skipped as trivial (1 user message).
- **Anything looks wrong** → don't debug live; note it here with what you
  saw and I'll take it through systematic-debugging next session.

## After the gate

1. If you ran Path A: **revert the compose edits** (creds + `LLM_MODE`) —
   `git diff infra/docker-compose.yml` should be empty afterward.
2. Deferred-minors list (21 items, all triaged, none merge-blocking) lives
   in the ledger — the final review's triage table is the read on those.
3. When you're satisfied: say the word (**"pass phase 13"**) and the phase
   closes out for merge whenever you choose; Phase 14's plan will be waiting
   for your review either way.
