# Handoff: first live Anthropic browser session (written 2026-11-16)

**State when written:** `main` @ 1e47a74. UI design pass complete (#12, #13, #14, #15 all
merged; the last three on 2026-11-16 by Fable via `gh pr merge --merge` on Carlos's "do
everything"). `NUM_LOST` KPI colour inverted (P3-R2 amendment). The other five overnight design
calls stand. Anthropic key rotated; the new key lives only in Carlos's own shell. Next work chosen
by Carlos on 2026-11-16: the live Anthropic browser session (option 1 of the four candidates).

**Model split for this session:** Fable drives the browser, reads logs and judges. No
implementation work is planned. If the session finds bugs, each fix becomes its own small PR in
a fresh chat (Haiku/Sonnet implement, Opus only for a risky diff), never fixed inline here.

## Paste-ready prompt for the next chat

> You are Fable, supervising Poseidon in C:\Users\carlo\github\latest_poseidon (design,
> dispatch, review, live verification; never implementation code). Read `CLAUDE.md` (gotchas:
> never `-m router_live` bare, never bare `python`, never pipe pytest, never `ruff format`),
> `.claude/CLAUDE.md`, the decision log in `docs/architecture/00-overview.md` through D82, and
> `docs/superpowers/plans/2026-11-16-live-anthropic-session-handoff.md`. State: `main` @
> 1e47a74, UI pass complete, Phase 17 complete, nothing deployed. Confirm with
> `git checkout main && git pull --ff-only`.
>
> Job: the first end-to-end run of the app in the browser against the real Anthropic provider
> (Claude 5.5 ids from `backend/config/models.yml`; never Bedrock, the shell has AWS creds).
> Carlos holds the key in his own shell; never ask him to paste it into chat. Before touching
> infra, ask him 3-4 multiple-choice questions, recommendation first: (1) which flows to walk:
> default chat only, or also Existing Customer and New Customer Prospect (the prospect flow calls
> Perplexity live and needs `PERPLEXITY_API_KEY`; recommend default chat + Existing Customer
> first, prospect only if he wants to spend on it); (2) a rough spend cap for the session;
> (3) whether to record a GIF of each flow for his review; (4) whether to write findings as a
> bug list only or also open the first fix PR at the end (recommend bug list; fixes go to fresh
> chats). Then give him the setup one step at a time, click-by-click, waiting for his confirmation
> after each (he is not techy on infra):
>
> 1. A scratch compose override (NOT tracked, under `.superpowers/sdd/2026-11-16-live-anthropic/`)
>    that sets, on the `backend` service, `LLM_PROFILE: anthropic`, `LLM_MODE: live`,
>    `ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}` (read from his shell, never written to disk), keeps
>    `CHAT_MODE: live` and `POSEIDON_INTERNAL_TOKEN: dev-internal-token`, and overrides ports the
>    way `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/compose.a2.yml` does: db
>    `!override ["5434:5432"]`, minio `!override ["9002:9000", "9003:9001"]` (9000 is held by another
>    project). Config refuses to boot `llm_profile=anthropic` with `llm_mode=live` and no key
>    (`core/config.py` validator), so a missing key fails loudly, which is the intended check.
> 2. He sets `$env:ANTHROPIC_API_KEY` in his PowerShell, then runs
>    `docker compose -f infra/docker-compose.yml -f <override> up` from the repo root. Expected:
>    migrate, seed, uvicorn on :8000; show him the log line to look for.
> 3. In a second PowerShell: `cd web`, `$env:POSEIDON_INTERNAL_TOKEN="dev-internal-token"`,
>    `$env:DRIZZLE_DATABASE_URL="postgresql://poseidon:poseidon@localhost:5434/poseidon"`,
>    `npm run dev` (serves on :3000).
> 4. Drive http://localhost:3000 with the chrome-devtools MCP tools (take screenshots, read console
>    and network), per `docs/architecture/00-overview.md` "The three conversation flows". Walk the
>    agreed flows, including the monthly report ("how did we do last month"), a metric query with
>    a time-bucket group-by, a follow-up that carries the period (D62), and one deliberately
>    ambiguous ask to see a clarify turn. Compare the live router's choices with the dev-router
>    divergence list in `core/chat/dev_router.py`'s docstring.
> 5. Write the findings to `docs/superpowers/plans/2026-11-16-live-anthropic-findings.md`
>    (untracked is fine): per flow, what was asked, what the model routed to, what rendered, what
>    was wrong, screenshots paths. Rank bugs by what Carlos would notice first. End with the
>    open judgment calls in one short message, recommendation first, and a handoff for the first
>    fix PR.
>
> Rules: no Bedrock ever; every pytest run carries `env -u PERPLEXITY_API_KEY` and
> `-m 'not pg and not minio and not pdf and not router_live and not research_live'`;
> `backend/.venv/Scripts/python.exe` only; branch from main explicitly, feature branch, PR,
> Carlos merges in the GitHub UI unless he says otherwise; `gh pr create` only on his word;
> commit messages via scratch file and `git commit -F`; model split in every plan header. Stop
> the containers and the Next dev server at the end and say so.

## Open items carried, not part of this session

- Spec §8 follow-ons: feedback thumbs, skills popover, settings and user menu, dark mode,
  narrative heading levels, Vite retirement, root `error.tsx`, `role="alert"` on the error line.
- PR C carry-forwards: provider-aware live-test executor, `report_lookup` in the dev router,
  `SecretStr` settings, Bedrock `_consume_stream` tool-input check, narrate retry.
- Phase 17b retrieval (optional, `docs/architecture/08-build-phases.md`).
