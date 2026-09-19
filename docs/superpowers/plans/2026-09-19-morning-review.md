# Morning review — 2026-09-19 (Phase 16 brainstorm + plan)

Branch: **`phase-16-chat-parity`**, cut from `main` at `a07b5cc` (PR #4 merged 00:53Z). NOT pushed.
No implementer has been dispatched. Ledger: `.superpowers/sdd/2026-09-19-phase-16-chat-parity/progress.md`.

## What needs your eyes

1. **The plan.** `docs/superpowers/plans/2026-09-19-phase-16-chat-parity.md` (commit `82df167`), 15 tasks.
   Read at least: Global Constraints (the sanctioned file list), Task 7 Step 6 (the grants SQL, the
   security-critical transcription), Task 9 (the shared `stream_turn` extraction), Task 11 (the
   frame translation the contract test pins). Say "go" and which execution shape you want
   (task-by-task subagent-driven is my recommendation; Tasks 1, 3, 4, 5, 6, 12 can run in parallel).
2. **Four plan-level refinements of the spec you approved**, all small, all flagged inline in the plan:
   - `phase` frames become transient AI SDK parts because Python never persists them (spec §6 said a
     reconciled data part).
   - The proxy answers the 403, not a page, because Next 16.3's `forbidden()` is still experimental
     (spec §7.5). Pages still call `requireSales` as defence in depth.
   - Three Drizzle migrations, not "baseline + one custom": roles must exist before the generated
     policies reference them, so `0000_roles`, `0001_baseline`, `0002_grants_force_rls`.
   - The contract corpus recorder is the golden test itself in update mode
     (`POSEIDON_UPDATE_CONTRACTS=1`), one JSON per scenario, not a separate script writing two files.
3. **AI SDK version.** You chose the 6.x line (`ai@6.0.286`). npm's `latest` is 7.0.107. The plan
   pins 6 exactly and lists the 7 upgrade as a later small task.
4. **Task 8 needs Docker** and recreates the local database (`docker compose ... down -v`). Nothing
   deployed, nothing to lose, but it is the one destructive step in the plan.

## Decisions you made tonight (recorded in the spec, §2)

Shared-secret route auth; email stub stays unwired (Snowflake colleague owns the procedure);
R16/R18 kept; Sales role required on the Next pages; `/dispatch` spelling kept, spec edited;
full parity scope; bridge (Python keeps the loop); one hand-written Drizzle baseline; lightly
styled UI; reconcile-on-drop out; Alembic retired from boot with 0001–0010 untouched; AI SDK 6.x.

## Commits on the branch

- `4147031` docs(phase-16): chat-parity design spec
- `82df167` docs(phase-16): implementation plan

## Environment notes

- Poseidon's db is still on host port 5434 via the Phase 15 compose override; the other project's
  containers hold 5432 and 9000/9001.
- The five untracked August docs under `docs/superpowers/plans/` are untouched and unstaged.
