# Morning review — 2026-09-19 (Phase 16 execution run)

## Status as of 2026-10-12

Phase 16 merged as PR #5 (merge commit 0e0db19). Decision items below:

- P16-R20 (composer after an error): kept as built.
- P16-R14: the runtime now throws when `DRIZZLE_DATABASE_URL` is unset (commit 3fa39ad).
- Item 3 (chip-click trace gap): explained. Normal chip turns log full spans; only the opener flow chips ("Existing customer", "New customer prospect") return before the parse/route spans, so they log none. Carried forward.
- Item 4 (PDF round trip): verified live in the browser on 2026-10-12 after adding `127.0.0.1 minio` to the Windows hosts file.
- P16-R13: no action.

Branch: **`phase-16-chat-parity`**, 30 commits on top of `main` (`a07b5cc`), HEAD `ed4efd2`. **NOT pushed, no PR.**
Ledger with every dispatch, verdict and ruling: `.superpowers/sdd/2026-09-19-phase-16-chat-parity/progress.md`.

> All 15 tasks complete. Final whole-branch review (Opus): "ready with fixes"; one fix wave applied and
> re-reviewed clean. Frozen surfaces verified intact by git diff (SSE envelope, part kinds, Alembic
> 0001–0010, RLS wiring, subs). The phase gate's live walk was run by the Task 14 worker in headless
> Chromium; your own click gate is below.

## Suites at HEAD
| Suite | Result |
|---|---|
| Python offline | 1718 passed / 13 skipped / 119 deselected (baseline 1693); ruff clean |
| Python `-m pg` (Drizzle-only database) | 337 passed / 1 skipped (stale figure was 334) |
| Web offline | 142 passed / 13 skipped; with `DRIZZLE_DATABASE_URL`: 152 passed (Task 14 run) |
| `npx tsc --noEmit` | clean |
| Frontend (Vite) | untouched |

## Decisions needing your eyes (priority order)
1. **P16-R20 — UI behaviour:** the composer and chips are disabled only while a turn is `submitted`/`streaming`. After a failed turn (`error` status) the UI stays usable. The plan said "disabled unless ready", which would have locked the page after any error. Say if you want the stricter form.
2. **P16-R14 — runtime database default:** `web/src/db/boot.ts` and `client.ts` still default to `localhost:5432` when `DRIZZLE_DATABASE_URL` is unset (drizzle-kit's fallback was removed, P16-R9). The boot probe now fails closed against a foreign Postgres, so this is not urgent. Recommend making the runtime throw too, for symmetry. Your call.
3. **Undiagnosed:** in the live walk, the chip-click turn logged a Python access line but no span lines, so its trace id could not be compared (the metric turn's ids matched end to end). Worth one look with the servers up.
4. **One MinIO-up click:** the artifact (PDF) part was not exercised live (MinIO down). Renderer and part shape are unit-tested; only the presigned-URL round trip is unproven.
5. **P16-R13 — a test lost proof it can no longer give:** 0009's Alembic round-trip can no longer prove `CREATE ROLE poseidon_worker` / `GRANT … TO CURRENT_USER` because roles are cluster-wide and Drizzle creates them first. The Drizzle migrations and `test_rls_policies.py` cover the same objects on the app database. Documented in the test; no action unless you disagree.

## Rulings made on your behalf (all in the ledger with cost-if-wrong)
P16-R1 probe-skill registration in a test · R2 keep a stale 501 detail string · R3 no worktree ·
R4 `POSEIDON_INTERNAL_TOKEN` bound by alias (later tightened by the final fix wave) · R5 boot probe
only treats an unreachable server as "unchecked" · R6 `NULLS FIRST` on the recency index (Alembic
parity) · R7 schema tests pin predicates/indexes/roles · R8 `web/.gitignore` unignore `drizzle/` ·
R9 drizzle-kit requires `DRIZZLE_DATABASE_URL` · R10 Alembic round-trips on a throwaway database ·
R11 fourth db.py hint string · R12/R13 per-database assertions only · R15 error parts live-only,
contract test filters them · R16 test-only private `phase` producer · R17 per-run text part ids ·
R18 pulse cue restored · R19 `/api/*` rewrite moved to `fallback` (the plan's precedence claim was
wrong) · R20 busy semantics · R21 final fix wave scope.

## Your click gate (spec §12)
1. Docker Desktop up. `docker compose -f infra/docker-compose.yml -f .superpowers/sdd/2026-09-07-phase-15-migration-foundation/compose.db-5434.yml down -v` then `up -d`. Watch `migrate` finish, `backend` seed, no Alembic in the logs. (MinIO clashes on 9000 with the other project; add the Phase 15 `compose.minio-9100.yml` override as a third `-f` if you want artifacts.)
2. `cd web`; PowerShell: `$env:DRIZZLE_DATABASE_URL="postgresql://poseidon:poseidon@localhost:5434/poseidon"; $env:POSEIDON_INTERNAL_TOKEN="dev-internal-token"; $env:ANALYTICS_ORIGIN="http://localhost:8000"; npm run dev`
3. http://localhost:3000 → **New conversation** → send `top customers by gross profit in Singapore in April 2026` (the offline stub router needs a resolved period; without one it answers with the capability line). Tool line, table, proof, sentence stream in.
4. Send an ambiguous customer name; click a chip.
5. New conversation → click **Existing customer** → give a customer; phase sections and tool lines appear.
6. Reload: same parts from history. http://localhost:5173 still works.
7. 403 check as `web/README.md` describes.

## Carry-forwards (not blocking; triaged by the final reviewer as "carry")
Every `minor (deferred)` and `carry-forward` line in the ledger, plus: a public `SseEnvelopeSink.phase()`
producer; `migrations.test.ts` generating into the tracked folder; the ec2 compose and runbooks are
marked stale rather than rewritten; `npm ci` sits on the compose boot path.

## Then the PR
When you say the word: `git push -u origin phase-16-chat-parity` and `gh pr create` with a full description
(what/why/how verified). You merge in the GitHub UI.
