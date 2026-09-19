# Morning review — 2026-09-18 (Phase 15 execution run)

Branch: **`phase-15-migration-foundation`**, NOT pushed, no PR. Nothing merged, nothing deployed.
Ledger with every ruling: `.superpowers/sdd/2026-09-07-phase-15-migration-foundation/progress.md`.

> Final state: all 7 tasks complete, final whole-branch review clean after one documentation fix
> wave. HEAD `d77f083`. The final scoped re-review confirmed every fix.

## Decisions needing your eyes (in priority order)

1. **SECURITY — the internal Next.js→Python route has no auth of its own (Task 5).** Any caller
   that can reach port 8000 can POST `{sub, roles}` and run a skill as any user. The plan never
   asked for an auth dependency; the implementer documented the gap in the module docstring.
   Nothing is deployed, but this must be closed before Phase 16 ships anything: an internal
   shared-secret header, or network isolation so only the Next.js process can reach the route.
   My lean: shared secret (`POSEIDON_INTERNAL_TOKEN`), checked by a FastAPI dependency, injected
   into both containers by compose/SPCS. Your call.
2. **SECURITY — no boot-time privilege probe on the web side (Task 4, ruling R21).** Python
   refuses to boot when the DSN is a superuser and no app role is set (`db.py:529-538`). The
   Next.js client silently runs with RLS inert in that configuration. Parked as a Phase 16 item.
   The compose default (`DATABASE_APP_ROLE` unset → `poseidon_app`) is safe.
3. **Task 7 placeholder — three questions before the real stored-procedure work:**
   (a) what is "the Entra email" today, given SPCS mode delivers no email at all; if the
   Snowflake login name is an Entra UPN, `sanitize_username` rejects `@` and `.` and that user
   is refused outright; (b) procedure signature — username in, email out?; (c) who calls it —
   my recommendation is Python, with Next.js getting the email through the internal contract.
4. **Ruling R16 — unwired `auth0` mode now returns 500 "identity misconfigured", not 401 and
   not the plan's stated 501.** Dead branch this phase (M5). Say if you want 501 back.
5. **Ruling R18 — the Next.js conversation list shows the 50 newest, newest first**, mirroring
   Python's ordering; the plan said oldest-first and unbounded (2,551 rows for dev|local).

## Environment facts you should know

- **Port 5432 and 9000/9001 are owned by another project's containers** (`hodl_underwriting-db-1`,
  `hodl-preflight-minio-1`) that were up when I started. I did not stop them. Poseidon's db runs
  on **host port 5434** via a scratch compose override in the SDD workspace. To bring the stack
  up yourself:
  `docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-09-07-phase-15-migration-foundation/compose.db-5434.yml up -d`
  The Next.js tests read `DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon`.
- **drizzle-kit 0.31.10 has an introspection bug**: it renders `user_profile.system_instruction`'s
  `''::text` default as invalid syntax. The generated `web/src/db/schema.ts` header documents the
  one-line hand fix to reapply after any re-pull.
- `npm audit` reports 4 moderate dev-only vulnerabilities from drizzle-kit. Not investigated.

## Deferred minors (final review triages which block merge)
See every `minor (deferred)` line in the ledger. None is security-relevant.

## Follow-ups I created by ruling (not decisions, just so you know they exist)
- **R24:** the Task 5 implementer added a 501 guard so a non-synthetic `DATA_BACKEND` could not
  silently query `DATABASE_URL` through the internal route. I had it removed (untested, blocked
  skills that never touch data). The underlying hazard is real and belongs in the data layer; it
  is a Phase 16 item alongside the route auth.
- **R26:** the TypeScript skill client reads `ANALYTICS_ORIGIN`, falling back to `BACKEND_ORIGIN`
  (the name `next.config.ts` already uses). If you want one name, say which.

## Status at end of run (written while the final whole-branch review was in flight)

| Task | Result |
|---|---|
| 1 Scaffold | complete (prior session) |
| 2 Identity | complete; fix round closed 4 findings (Python's problem body, asset matcher, config faults → 500, 11 proxy tests) |
| 3 Drizzle pull | complete; header documents the drizzle-kit hand fix and what the pull really captured |
| 4 RLS client | complete; `createRlsClient` factory, `is_local` regression-tested with a demonstrated failure when broken |
| 5 Internal contract | complete; live seam proven Node → FastAPI; blank subs rejected; untested 501 guard removed |
| 6 Conversation pages | complete; HTTP gate passed; missing header now throws; malformed id → 404 |
| 7 Email-source placeholder | complete; not wired, awaiting your procedure code |

Suites at HEAD `d77f083` (fix wave was docs/comments only; counts unchanged): Python 1693 passed / 13 skipped / 116 deselected (baseline 1679), ruff
clean; web offline 60 passed + 9 skipped, live 69/69; frontend 171 passed / 18 files, unchanged.

**Your click gate (Step 6 of Task 6) is still yours to run.** Everything below was verified over
HTTP by a worker, not in a browser:
1. Docker Desktop up, then the compose command from "Environment facts" above.
2. `cd web` and run `DRIZZLE_DATABASE_URL=postgresql://poseidon:poseidon@localhost:5434/poseidon npm run dev`
   (PowerShell: `$env:DRIZZLE_DATABASE_URL="postgresql://poseidon:poseidon@localhost:5434/poseidon"; npm run dev`).
3. Open http://localhost:3000 — you should see "Conversations for dev|local" and a list. Click one.
4. Open http://localhost:5173 in another tab — the Vite app still works.
5. Try http://localhost:3000/c/not-a-uuid — a 404 page, not an error.

**Then the PR.** Branch is not pushed. When you say the word I run `git push -u origin
phase-15-migration-foundation` and `gh pr create` with a full description; you merge in the GitHub
UI. The branch also carries the September 7 docs-reconcile commits, so the PR will include them.

## Final whole-branch review (Opus) — "ready with fixes", one fix wave applied

The reviewer verified the frozen surfaces intact and both spec deviations justified. Findings:

- **Critical (fixed in the wave, comment-only):** the generated `web/src/db/schema.ts` header
  claimed introspection captured the RLS policies. drizzle-kit 0.31.10 captured the predicate on
  only the four single-policy tables and DROPPED it on five (`tool_calls`, `memory_outbox`,
  `turn_run`, `llm_calls`, `message_feedback`), and rotated index operator classes. The header now
  says so and forbids generating anything from that file. **Phase 16's Drizzle handover must
  re-derive every policy and index from the Alembic migrations.**
- **Recorded in the tracked plan** (new "Carried into Phase 16" section) so they survive merge:
  the internal-route auth (R25), the boot privilege probe (R21), the data-backend guard the route
  now lacks (the reviewer showed the failure is silent zero rows, not a loud error, once alembic
  has created the empty `synthetic` schema), and two questions below.
- **Two more questions for you** (added to the list at the top):
  6. Should the Next.js pages require the `Poseidon:Sales` role, as every comparable Python route
     does? Today they check only the sub; an authenticated but non-allowlisted Snowflake user sees
     an empty list, never another user's rows.
  7. The spec writes the internal route as `.../skills/{id}:dispatch` (colon); the plan and code
     use `/dispatch` (slash). Pick one; the other document changes.
- **Also in the wave:** `web/README.md` rewritten (how to run, test, every env var with its
  default), `npm test` script added, the web suite added to the root CLAUDE.md commands, decision
  log rows D55 (Phase 15 simplifications) and D56 (email-source placeholder seam), stale comments,
  the phantom `app` schema filter, scaffold metadata title.

## PR and what to test tomorrow

**PR #4:** https://github.com/sal1391/latest_poseidon/pull/4 — branch pushed, PR open against
`main`, description written. You merge it in the GitHub UI after the checks below.

### Test checklist (about 15 minutes)

Setup, once:
1. Start Docker Desktop. If another project's containers are still holding 5432/9000, leave them;
   the override below avoids the clash.
2. PowerShell, repo root:
   `docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-09-07-phase-15-migration-foundation/compose.db-5434.yml up -d`
   If MinIO fails on 9000/9001, add a second `-f` with the minio override the Task 5 worker left
   in that same folder (look for `compose.minio-*.yml`), or stop just MinIO: it is not needed for
   this test.
3. Second PowerShell: `cd web`, then
   `$env:DRIZZLE_DATABASE_URL="postgresql://poseidon:poseidon@localhost:5434/poseidon"; npm run dev`

Checks (tick each):
- [ ] http://localhost:5173 loads the OLD Vite app. Send one chat turn so a fresh conversation exists.
- [ ] http://localhost:3000 shows "Conversations for dev|local" with the conversation you just
      created at the TOP of the list (newest first, capped at 50).
- [ ] Click it. The page shows the messages with their role labels and the raw parts JSON. Plain
      styling is expected; this phase proves the seam, not the UI.
- [ ] Stop the Next dev server (Ctrl+C), start it again, reload. History still there.
- [ ] http://localhost:3000/c/not-a-uuid → a 404 page, not an error page.
- [ ] Copy a conversation id from the Vite app that belongs to a different dev user (any
      `dev|alice-…` row; or ask the next agent to give you one) and open
      http://localhost:3000/c/<that id> → 404, because RLS hides it from dev|local.
- [ ] Both tabs (5173 and 3000) work at the same time.
- [ ] Optional, terminal: `cd web; npm test` → 60 passed, 9 skipped. With
      `$env:DRIZZLE_DATABASE_URL` set → 69 passed.

If every box ticks, merge PR #4. Then answer the seven decisions at the top of this file when you
have a minute; none blocks the merge.
