# Dev Router Chat Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Offline (dev router) chats get a sensible sidebar title instead of the router's capability message cut to 60 characters.

**Architecture:** `title_for` (`backend/poseidon/core/llm/titles.py`) calls the `utility` role. Offline, that role is `DevDeterministicRouter`, which matches none of its state-block gates for a title prompt and returns `_CAPABILITY_MESSAGE`. The fix adds one new first-checked case to the router: recognise a title request and answer with a deterministic title derived from the user message it was given. Nothing above the provider seam changes; live providers are untouched.

**Tech Stack:** Python 3.14 (`backend/.venv`), pytest 9.1.1.

**Spec:** this file. Diagnosis: `backend/tests/test_history_cutover.py` module docstring, "The stub title" (it documents the current behaviour as intended-at-the-time).

**Model split:** Fable supervises and does the final diff read. One Sonnet implementer (single task, full spec). One fresh Sonnet reviewer, forbidden from re-running the suite. No Opus.

**Execution approach:** one worker with this full spec (a single task; nothing to split).

## Global Constraints

- Branch `fix/dev-router-titles`, created with `git checkout -b fix/dev-router-titles main` (main @ 0e228bf). Before any commit run `git rev-parse --abbrev-ref HEAD`; after committing, `git log --oneline main..HEAD` must show only this work. Anything else: stop and report.
- Always `.venv/Scripts/python.exe`, never bare `python`. Never pipe pytest: redirect to a file, then read the file. Never `ruff format`. Never `--no-verify`.
- Offline suite uses the full four-marker exclusion and `env -u PERPLEXITY_API_KEY` (commands in `CLAUDE.md`). Never run `-m router_live`.
- Commit messages go in a scratch file and `git commit -F <path>`.
- Backend files are CRLF in the working tree.
- Do not touch: `titles.py`, `live_chat.py`, `orchestrator.py`, the `utility/title` prompt, `contracts/turns/`, anything under `web/` or `frontend/`. If the fix seems to need one of them, stop and report.
- The SSE `done` frame shape is frozen: `"title"` stays `str | null`; only its offline value changes.
- No push, no `gh pr create`. Carlos authorises those separately.

## Behaviour decided

The router receives, as the user message, either `question` or `question + "\n\n" + answer_summary` (built in `live_chat.py` `_finalize_turn`).

1. **Recognising a title request:** `system` contains the literal `Write a chat title` and does not contain `=== CONVERSATION STATE ===`. Checked before every other case in `invoke`.
2. **Source text:** split the user text on the first `"\n\n"`. If there is a second part, it is non-empty after stripping, and it does not start with `_CAPABILITY_MESSAGE`, use the second part (the answer summary). Otherwise use the first part (the question).
3. **Shaping:** first line only; collapse whitespace runs to single spaces; strip trailing `.`; if longer than 60 characters, cut at the last space at or before position 60 (hard slice at 60 when there is no space). 60 is pinned in the router as its own constant with a comment naming `TITLE_MAX_CHARS`; `dev_router.py` does not import `titles.py`.
4. **Nothing usable** (no user text, or empty after shaping): fall through to the existing `_capability_response()`.

Expected results: `"hello"` (whose offline answer is the capability message) is titled `hello`; the brief flow's second turn (`"Northstar Lines"`) is titled `Brief generated for Northstar Lines`.

## Review Focus

- A prompt edit that drops the phrase `Write a chat title` silently restores the old behaviour. Pinned by the Step 1 test that renders the real prompt.
- User-message `content` shape: `title_for` passes `content` as a plain `str`, while `_last_user_text` reads a list of `{"text": ...}` blocks. The new helper must read whichever shape actually reaches the router; the end-to-end test through `title_for` is what proves it.
- A summary longer than 60 characters must cut on a word boundary, never mid-word.
- A multi-line answer summary must title from its first line only.
- An ordinary routed turn whose system prompt carries the state header must never be mistaken for a title request.

---

### Task 1: Title case in the dev router

**Files:**
- Modify: `backend/poseidon/core/chat/dev_router.py` (new case first in `DevDeterministicRouter.invoke`, ~line 721; helpers beside `_capability_response`, ~line 1190; one new row/paragraph in the module docstring's behaviour table)
- Test: `backend/tests/test_chat_state_devrouter.py` (offline unit tests)
- Modify: `backend/tests/test_history_cutover.py` (pg; `_STUB_TITLE` at line 305, its uses at 326, 331, 410, 411, 431, the module docstring's "The stub title" paragraph at lines 39-46, and the mention near line 673)

**Interfaces:**
- Produces: `_is_title_request(system: str) -> bool` and `_title_response(messages: list[dict]) -> LLMResponse | None` in `dev_router.py` (`None` means "nothing usable", so `invoke` falls through). `LLMResponse` is built exactly as `_capability_response` builds it (`tool_calls=()`, `stop_reason="end_turn"`, zero tokens).

- [ ] **Step 1: Write the failing offline tests** in `backend/tests/test_chat_state_devrouter.py`, following that file's existing way of calling `DevDeterministicRouter().invoke(...)`. Build `system` for these by rendering the real prompt: `PromptRegistry(DEFAULT_PROMPTS_DIR).render("utility/title", max_chars=60)`.

  | Test name | User text | Expected `response.text` |
  |---|---|---|
  | `test_title_request_uses_the_answer_summary` | `"Northstar Lines\n\nBrief generated for Northstar Lines."` | `"Brief generated for Northstar Lines"` |
  | `test_title_request_falls_back_to_the_question_without_a_summary` | `"hello"` | `"hello"` |
  | `test_title_request_ignores_a_capability_message_summary` | `"hello\n\n" + <the router's capability message>` | `"hello"` |
  | `test_title_request_takes_the_first_line_and_collapses_whitespace` | `"q\n\nGross  profit rose.\nSecond line."` | `"Gross profit rose"` |
  | `test_title_request_cuts_long_text_on_a_word_boundary` | `"q\n\n" + "alpha " * 20` | at most 60 chars, equals `("alpha " * 20)[:60].rsplit(" ", 1)[0]`, does not end with a space |
  | `test_title_request_with_no_usable_text_gets_the_capability_message` | `""` | starts with `"I can answer certified metric questions"` |
  | `test_a_state_block_system_is_never_a_title_request` | system = `"Write a chat title\n=== CONVERSATION STATE ===\n"`, user `"hello"` | starts with `"I can answer certified metric questions"` |

  Plus one end-to-end test, `test_title_for_through_the_stub_role_client_is_not_the_capability_message`: call the real `title_for("hello", role_client)` with a stub-mode `RoleClient` built the way this suite (or `test_history_cutover.py`'s `_app`) already builds one, and assert the result is `"hello"`. This is the test that proves the real `content` shape is handled.

- [ ] **Step 2: Run them and confirm they fail for the right reason**

  `cd backend && .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_chat_state_devrouter.py -k "title" > <scratch>/step2.txt 2>&1`
  Expected: the title tests fail with the capability message as the actual text (not an import or fixture error). The last two table rows pass already; say so in the report.

- [ ] **Step 3: Implement** `_is_title_request` and `_title_response` per "Behaviour decided", and call them first in `invoke`. Update the module docstring's behaviour table with the new case.

- [ ] **Step 4: Run the file again**; all new tests pass, and every pre-existing test in the file still passes.

- [ ] **Step 5: Prove the guard by breaking it.** Temporarily change the marker constant to a string the prompt does not contain; confirm `test_title_request_uses_the_answer_summary` and the `title_for` end-to-end test fail; restore; confirm green. Report both outputs.

- [ ] **Step 6: Re-pin the pg tests** in `backend/tests/test_history_cutover.py`. Replace `_STUB_TITLE` with two constants: `_STUB_TITLE_HELLO = "hello"` (lines 326, 331, 431) and `_STUB_TITLE_BRIEF = "Brief generated for Northstar Lines"` (lines 410, 411). Rewrite the "The stub title" docstring paragraph and the comment above the constants to describe the new behaviour. Leave the line-454 capability-message assertion alone unless it fails.

- [ ] **Step 7: Run the suites**, each once, each to its own file:
  - Offline (command in `CLAUDE.md`). Expected: 2471 + the new tests passed, 14 skipped, 153 deselected.
  - `ruff check .` clean.
  - pg suite, serially, against the compose `db` on host port 5434 (start it with the port-override file described in `CLAUDE.md`; the `migrate` service must have run). Expected: 368 passed, 0 skipped.
  - `tests/contracts -q` without `POSEIDON_UPDATE_CONTRACTS`: 9 passed, `git status --short contracts/` empty.

  If a pg test shows a brief title other than `Brief generated for Northstar Lines`, or any test outside the two files above changes outcome, **stop and report the actual value**; do not re-pin to match.

- [ ] **Step 8: Commit** (`fix(dev-router): answer title requests with a deterministic title`), after the branch check in Global Constraints.

## After the task

- Fresh Sonnet reviewer on the diff (judges code and the implementer's evidence; does not re-run suites).
- Fable final read, then a Playwright walk of the brief flow offline to see the sidebar title (the Chrome extension cannot reach localhost on this machine).
- Push and PR only on Carlos's word. If the offline/pg counts change, `CLAUDE.md`'s verified figures are refreshed in the same PR.

---

## Execution record (2026-11-17)

- Branch `fix/dev-router-titles` from main @ 0e228bf. Commits: `c52ef59` fix(dev-router): answer title requests with a deterministic title; `8cf31a7` docs: CLAUDE.md offline figure 2471 -> 2479.
- Model split as planned: Sonnet implementer (one task), Sonnet task reviewer (spec ✅, Approved, 0 Critical/Important, 4 Minor parked), Fable final read.
- Suites (implementer, each once, to a file): offline 2479 passed / 14 skipped / 153 deselected; `ruff check` clean; pg 368 passed / 0 skipped (compose db on 5434); `tests/contracts` 9 passed, `contracts/` unchanged. TDD RED 6 failed / 2 passed with the capability message as actual text; guard proven by breaking the marker (6 failed, incl. the end-to-end test) then restoring (94 passed).
- Live check through the running stack (HTTP, X-Dev-User; Chrome extension cannot reach localhost, Playwright MCP down): brief flow titled `Brief generated for Northstar Lines` on its second turn, `hello` titled `hello`, both in the `/api/conversations` listing.
- Parked minors: `startswith(_CAPABILITY_MESSAGE)` on the unstripped summary; word-boundary test misses the exactly-60 case; unused `head` slice when `cut > 0`; pre-existing Starlette deprecation warning.
- Ledger: `.superpowers/sdd/2026-11-16-dev-router-titles/progress.md` (kept on disk).

## PR description (ready to paste)

**fix(dev-router): deterministic chat titles offline**

What changed
- `DevDeterministicRouter` gains a first-checked case: when the system prompt is the `utility/title` prompt (and carries no conversation-state block), it answers with a title derived from the user message instead of the capability message. Source is the answer summary when present and usable, else the question; first line only, whitespace collapsed, trailing periods stripped, cut on a word boundary at 60 characters. Nothing usable falls through to the existing cases.
- `_TITLE_MAX_CHARS = 60` is pinned in the router (mirrors `titles.TITLE_MAX_CHARS`; the router still does not import `titles.py`).
- pg tests in `test_history_cutover.py` re-pinned: `hello` chats are titled `hello`, the brief flow `Brief generated for Northstar Lines`.
- `CLAUDE.md` offline figure refreshed (2471 -> 2479).

Why
- Offline, the utility title call reached the dev router, which answered with its capability message; cut to 60 characters that became every chat's sidebar title (seen in the PR #18 browser walk). Live providers are untouched; only the offline value changes. The SSE `done` frame shape is unchanged.

How it was verified
- 8 new offline tests (7 router unit tests using the real rendered prompt, 1 end-to-end through `title_for` with a stub `RoleClient`); RED-first, and the marker guard proven by breaking it.
- Offline 2479 passed / 14 skipped / 153 deselected; ruff clean; pg 368 passed / 0 skipped; contracts 9 passed, corpus unchanged.
- Running stack: brief-flow chat and hello chat titled as above in the conversations listing.

## Handoff prompt (paste into a new chat)

> You are Fable, supervising Poseidon in C:\Users\carlo\github\latest_poseidon (design, dispatch, review; never implementation code). Read `CLAUDE.md`. State: branch `fix/dev-router-titles` (2 commits over main @ 0e228bf, head 8cf31a7) fixes offline chat titles; run `gh pr view --json state,number --head fix/dev-router-titles` (or `git branch -a`) to see whether it was pushed, opened or merged. If merged: sync main, delete the local branch, stop the compose db (`docker compose -f infra/docker-compose.yml stop db`). Baselines after it: offline 2479/14/153, pg 368/0, web 241/15, contracts 9. Open items for Carlos: `backend/.env.example` S3 lines (his edit; agents cannot read `.env*`). Next candidate work, unchosen: F5 run-log coverage (synthesis/utility calls + latency_ms); F3/F4 part formatting and the "withheld" label (product calls first); `brief_run`/`report_run` retention (D84 open item; needs a retention period); whether to drop the Bedrock provider (ask first); SPCS deploy. Rules: never Bedrock; never bare `python`; never pipe pytest; never `ruff format`; four-marker offline exclusion with `env -u PERPLEXITY_API_KEY`; branch from main explicitly; commit via scratch file and `git commit -F`; `gh pr create` only on Carlos's word; he merges in the GitHub UI.

PR #19 opened 2026-11-17: https://github.com/sal1391/latest_poseidon/pull/19 (Carlos merges in the GitHub UI).
