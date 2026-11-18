# Blocked-on-Carlos Pile — Walkthrough Plan (2026-08-03)

## Parked for later

- [ ] **Try `LLM_MODE=live` locally to feel the real chat experience.**
      Stub mode's canned responses are why generic questions ("what can you
      help me with?") feel scripted rather than conversational — this is
      switched off by default, not a missing feature (proven working via
      Bedrock in Group E Track A). To flip it: edit `infra/docker-compose.yml`'s
      `backend` service, change `LLM_MODE: stub` → `LLM_MODE: live`, and add
      `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`/`AWS_DEFAULT_REGION` (copy
      from `backend/.env`) — the container can't see `backend/.env` directly
      (`POSEIDON_ENV_FILE: ""` deliberately blocks it), so those three lines
      have to go directly into the compose file. Do this edit yourself (the
      harness's own safety classifier blocks scripting real credentials into
      a file) — I'll handle the container recreate once it's there. Revert
      afterward, same as the Auth0 test — don't commit real AWS keys into
      tracked config.

Not an SDD implementation plan — a working checklist for clearing everything
that's been parked waiting on Carlos across the build. Live-updated as we
go, same convention as the morning-review log. Mark items `[x]` as they
close, with the answer/outcome noted inline.

## How this goes

Six groups, roughly in the order below. A and B are pure conversation — no
commands, no waiting. C is one command + one word from Carlos. D is the big
one (reviewing 8 phases) and deserves its own dedicated pass rather than
being squeezed in here. E is external account signups on Carlos's own time
— doesn't block anything else, can run in parallel with everything. F is a
quick no-rush sweep.

**Recommended order:** A → B → C → F → E (kick off in parallel) → D (whenever
there's a real block of time for it).

---

## Group A — Quick decisions (yes/no or pick-one, no context-gathering needed)

- [x] **A1. Decision #4 — `MODE_SLOT_ALIASES` sign-off.** → **Ship as built.**
      No code change. Closed.
- [x] **A2. Decision #5 — tool-result proof block shown to the model.**
      → **Approve whole block as built.** No code change. Closed.
- [x] **A3. Brief-flow titling.** → **Fix: title fires on the first
      answered turn regardless of index.** CODE CHANGE NEEDED — route into
      an implementation task (small, targeted; candidate: fold into P12 or a
      standalone quick fix — decide when we get to Group D/routing).
- [x] **A4. Decision #8 — prospect fuzzy-match misattribution.** → **Skip
      customer resolution entirely in prospect mode.** CODE CHANGE NEEDED —
      touches the prospect-brief pivot resolver (Phase 8 code) — route into
      an implementation task.

## Group B — Needs more context or business knowledge

- [x] **B1. Decision #7 — CRM template shape (existing-customer brief).**
      → **Port the legacy 16-field nested Salesforce form** (Executive
      Summary / Account Research / Carbon Reduction Goals / Business Drivers
      & KPIs / Customer Challenges) from the old app's `strategist.py`,
      replacing the flat 7-field placeholder. CODE CHANGE NEEDED — route into
      an implementation task; will need the exact legacy field text
      transcribed carefully (the legacy form has some rough edges: three
      different "no services" wordings and a leftover scratch block — worth
      cleaning those specific rough edges up while porting, confirm with
      Carlos if in doubt rather than guessing).
- [x] **B2. Decision #3 — port-carry symmetry.** → **Re-resolve neither.**
      This is a CHANGE from current behavior: today the carried customer DOES
      re-resolve on a bare follow-up turn (port doesn't) — needs to flip so
      neither re-resolves; slots become trusted memory for both. CODE CHANGE
      NEEDED — route into an implementation task. Also stabilizes the
      Perplexity-egress concern (no re-resolution means no risk of a stale
      port/customer rewrite riding into an outbound research query).
      **B2b. "at" cue word** → **Leave as port-only cue, no change.** Closed,
      no action.

## Group C — One command, one word

- [x] **C1. Phase 2 gate.** Ran the `demo_query` command, output matched
      documented expectations exactly. **Merged** — `phase-2-ontology-synthetic`
      → `main`, fast-forward `9cfd3dc..763c4ee`. Back on `phase-3-8-overnight`
      for continued work. Not pushed (standing rule — `origin/main` stays
      diverged until F4 is decided).

  ```powershell
  cd backend
  $env:DATABASE_URL = "postgresql+psycopg://poseidon:poseidon@localhost:5432/poseidon"
  .\.venv\Scripts\python.exe -m poseidon.scripts.demo_query
  ```

## Bonus — live bugs found and fixed while looking at the app

- [x] **Composer sinks below the fold / sidebar rows squished.** `app-shell`/
      `chat-column` used `min-height: 100vh` (a floor, not a ceiling) so the
      whole page grew with message/conversation count instead of the thread
      and sidebar list scrolling internally; `.conversation-item` also
      lacked `flex-shrink: 0`, so flexbox squished every row to cram the
      list into the visible strip instead of letting it overflow and
      scroll. Fixed in `frontend/src/theme/base.css`, verified live via
      Claude-in-Chrome, committed at `33f8f7f`.
- Also noted: the frontend/backend Docker containers don't reliably pick up
  file changes without a container restart after your Windows restart (the
  known bind-mount quirk) — `docker compose -f infra/docker-compose.yml
  restart frontend|backend` if something you expect to have changed still
  looks stale.

## Group D — The big review (COMPLETE)

Went phase by phase, using each phase's documented gate. Phase 12 is still
in-flight (Task 1 reviewed/complete, Task 2 done-but-unreviewed, Tasks 3-4
not started) — reviewed as "what's done so far," not a finished phase.

**Closing suite run (after the full D1-D10 pass, fresh venv run):**
offline `1692 passed / 0 failed / 0 errors / 9 skipped`; pg
`195 passed / 0 failed / 0 errors / 1 skipped`. All green.

- [x] **D1. Phase 3** (skill registry + `data_qa.metric_query` + PDF
      pipeline) — live curl matched the documented output byte-for-byte
      (same top-5 customers/GP, same proof block); negative control
      (`PORT_NM` instead of `LOC_NM`) returned the exact certified 422.
      MinIO console (localhost:9001, creds `poseidon`/`poseidon123` from
      compose) confirmed the `poseidon-artifacts` bucket has real content
      (`existing-customer-brief-test`, `test-artifact-store` folders).
      Confirmed.
- [x] **D2. Phase 4** (parsing pipeline) — `pytest -k parsing`: 608
      passed/0 failed/1 unrelated skip. `-m pg`: 195 passed/0 failed/1 skip.
      Live spot-checks (through the actual chat, more meaningful than a
      REPL call): Singapore/April query returned the certified top-5 table;
      "Northstar Linez" auto-resolved to Northstar Lines; "Meridiann"
      correctly triggered clarification chips (Meridian Tankers/Lines/
      Shipping) instead of guessing. All confirmed.
- [x] **D3. Phase 5** (LLM provider layer, stub-first) — no user-facing
      gate by design; live suite still waits on AWS keys (Group E).
      Covered by the full offline suite (see closing summary below).
- [x] **D4. Phase 6** (real chat end-to-end) — ran the exact 4-turn gate
      script live: (1) Singapore/April top-5, certified; (2) "and for May
      2026?" correctly did NOT re-filter port (documented asymmetry) —
      single GP total across all customers; (3) "same for Port of
      Rotterdam" replaced port, carried May's period; (4) "gp for Meridiann
      in April 2026" → clarification chips (Meridian Tankers/Lines/
      Shipping) → clicked "Meridian Tankers" → sent the documented
      `"for Meridian Tankers"` text → resolved to the certified answer.
      Exact match throughout. Confirmed.
- [x] **D5. Phase 7** (Perplexity research + MCP) — confirmed `LLM_MODE:
      stub` is hardcoded in compose and `PERPLEXITY_API_KEY` isn't
      referenced anywhere in it (container never sees the real key even
      though it's in the host shell), then asked "Any relevant news on
      Northstar Lines I should be aware of?" live — routed to Web research,
      answered from recorded fixtures (`example.com` URLs), correctly
      topic-scoped. Confirmed, zero live calls.
- [x] **D6. Phase 8** (both brief flows) — **Existing Customer** (Northstar
      Lines): 6 metric cards (prior-year vs YTD), top-4-of-5-ports table, 5
      phase sections (Context/Sustainability & ESG/Market Position/
      Strategic Profile/Strategy) all honestly stub-labeled where synthesis
      requires live mode, proof block shows `Artifact: skipped (no artifact
      store configured)` — the documented honest skip note, not a bug (this
      compose backend doesn't have the artifact-store env wired for live
      brief generation). **New Prospect** (Global Horizon Shipping, a name
      not in the customer pool): correctly research-first ordered (Web
      Research section leads), "Current Services: Prospect — no current
      services", no data-block metrics (no internal history to pull, as
      expected for a prospect). Both confirmed.
- [x] **D7. Phase 9** (identity) — `GET /api/me` with `X-Dev-User` returns
      `identity_mode: disabled`, Dev User, Sales role — dev mode unchanged
      locally, as expected. The Auth0-live round-trip itself is Group E's
      E2, not a local gate.
- [x] **D8. Phase 10** (chat history + RLS) — created the New Prospect
      brief above, restarted the backend container
      (`docker compose restart backend`), reloaded the app: the full brief
      content was still there, identical, after the restart. Confirmed.
- [x] **D9. Phase 11** (observability) — deleted a test conversation via
      the API: `204` with an `x-trace-id` response header, conversation
      confirmed gone from the list (not a page-size coincidence — checked
      the specific id), and fetching its messages now correctly `404`s.
      Reconciliation endpoint not separately re-demoed live (dropped-stream
      recovery needs a slow turn to observe mid-flight, per the P11 gate
      note) — already suite-covered.
- [x] **D10. Phase 12 (partial)** — clicked thumbs-up on a live assistant
      message ("Thanks — this helps us tune Poseidon." confirmation
      appeared), then did a full page reload: the thumbs-up stayed filled,
      confirming GET hydration correctly restores a persisted verdict from
      the database. Opener-withholding already confirmed in D2 (no thumbs
      on the greeting). Tasks 3-4 not built yet, nothing to gate there.
      Confirmed.

**Group D: all 10 items confirmed live.** Nothing found wrong beyond the
composer/sidebar layout bug already fixed at `33f8f7f`.

## Group E — Credentials / accounts (your own time, doesn't block anything else)

- [x] **E1. AWS trial account.** DONE — full step-by-step in
      `2026-08-03-aws-auth0-setup.task.md` Track A. Bedrock live-verified
      (Nova + Claude Sonnet 4.5), IAM policy fixed twice for real
      cross-region-inference gotchas, `router_live` suite run (5/6, one
      finding parked as a carryforward). Decision #6 (dotted vs. translated
      skill-id spelling) can now actually be tested whenever that's a
      priority — not yet done, just unblocked.
- [x] **E2. Auth0 dev tenant round-trip.** DONE — full step-by-step in the
      same task file, Track B. Real tenant, real login round-trip proven
      working end to end, three real bugs found and fixed live (two config,
      one genuine code bug — JWT leeway). **Hard gate still stands:** Phase
      14 hardening (JWKS-fetch DoS fix) must land before auth0 mode ships
      to any real deploy — today only proved local testing works.
- [x] **E3. Snowflake account.** → **Not happening on this computer.**
      Deferred — no further action here; revisit whenever/wherever Carlos
      sets this up. Detailed todo below stays as reference for that.
      Originally: needed for Phase 14's actual SPCS deploy and
      Phase 15's live data-adapter flip. Detailed todo (grounded in
      `docs/architecture/07-infrastructure.md` §4/§7, `04-data-ontology.md`,
      `ontology/ontology.yml`, `03-llm-routing.md` D33) — not a one-sitting
      task, no rush, but here's the full shape of it:

  1. **Account + database/schema.** A Snowflake account (trial is fine to
     start) with database `SANDBOX`, schema `MCA` — this exact
     `SANDBOX.MCA` pattern is already the certified ontology's target FQN
     (`ontology/ontology.yml`: `MARINE_SALES_PLANNING_V.fqn =
     SANDBOX.MCA.MARINE_SALES_PLANNING_V`, same for `W_MARINE_GL_SOURCE_AI`)
     — this isn't a Poseidon-invented name, it's inherited from the
     certified ontology source (`wfs_work_structure`), so if your real
     Snowflake environment already uses this database/schema for these
     views, this is just pointing at it, not creating something new.
  2. **The certified views themselves loaded/available** — `docs/
     architecture/07-infrastructure.md`'s trial-rehearsal step explicitly
     names "the certified views loaded from the synthetic dataset" as part
     of the rehearsal; whether these already exist in your real account or
     need to be created from the synthetic generator's output is an open
     question worth checking first.
  3. **A read-only Poseidon app role.** Doc 04 says "the app's Snowflake
     role is read-only by construction and by grant" but no specific role
     name is decided yet — worth deciding a name (e.g. `POSEIDON_APP_RO`)
     and granting it SELECT-only on the `SANDBOX.MCA` schema.
  4. **Auth mechanism.** Not yet decided: password (matches the legacy
     app's `.env.example` pattern) vs. key-pair auth (more secure, no
     password rotation). `core/config.py`'s `DEPLOY_MODE`-keyed strategy
     expects: password locally, Secrets Manager on EC2, auto-mounted OAuth
     token in SPCS — so local dev needs SOME credential either way.
  5. **SPCS-specific objects** (Phase 14, not needed for Phase 15's data
     flip alone): an image repository (`SANDBOX.MCA.POSEIDON_REPO`), a
     compute pool (`CONTAINER_BOX_POOL` or a dedicated one, set to
     `SUSPEND` when idle for cost control), an External Access Integration
     (starts as `ALLOW_ALL_EAI`, tightened later), and eventually
     `infra/spcs_spec.yaml` (doesn't exist yet — a P14 deliverable, not
     something you need to hand-write).
  6. **Cortex (the Snowflake-native LLM provider)** is explicitly deferred
     — decision D33 says Cortex arrives during SPCS prep, only after the
     Bedrock seam is proven live first. Nothing to do here yet beyond
     having the account; `CortexProvider` doesn't exist in code yet.
  7. **An internal stage for backups** (doc 07 §5, decision D32) — name
     not yet decided, needed for the scheduled Postgres/MinIO backup
     mirror once that ships.
  8. **When you have the account**: tell me and I'll help pin down the
     open items above (role name, auth mechanism) as an actual decision
     rather than a guess, same as the AWS/Auth0 tracks.

**Group E: E1 + E2 complete (both live-verified end to end). E3 deferred —
not happening on this computer.**

## Group F — Parked, no rush, quick sweep

- [x] **F1. RPO/RTO.** → **RPO = 24 hours (daily backups), RTO = next
      business day.** Reasoning: internal sales tool, not revenue-critical
      minute-to-minute, so generous/cheap-to-operate numbers are the right
      default. Retention window itself (how long chat history / run-log
      data is kept before deletion) is still open — separate from RPO/RTO,
      revisit whenever it comes up.
- [x] **F2. Row-level data scoping by salesperson.** → **Fold into Phase 14.**
      No action now.
- [x] **F3. Personalization surface.** → **Keep at Phase 13**, no reorder.
      No action now.
- [x] **F4. `origin/main` reconciliation.** → **Leave it for now.** Currently
      144 ahead / 1 behind (grew from 119 since Phase 2 merged locally).
      Revisit once there's a natural push point.
- [x] **F5. `codex/eval_only2`.** → **Reset it.** Its one commit (`84d63de`)
      was already an ancestor of `main` (fully merged, nothing unique) —
      deleted the branch pointer with `git branch -d` (safe delete, git
      itself confirmed no unmerged work). Closed.

---

*Not committed yet — this file tracks live as we work through it. We'll
commit it once there's something worth preserving in git history.*
