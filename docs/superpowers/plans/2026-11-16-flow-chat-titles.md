# Flow Chat Titles (F2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A conversation gets its sidebar title on its first successful answer, whichever turn that is, so report-flow and brief-flow chats stop reading "New chat" forever.

**Architecture:** Today `api/live_chat.py`'s `_finalize_turn` titles only when `turn_index == 1 and status == "ok"`; a flow that opens with clarify turns (report: perspective, office, month; brief: customer) never hits that. The gate becomes "the conversation is still untitled and this turn is `ok`", read from the store through a new `UserHistory.get_title`. The title model also gets the turn's one-line answer summary (already computed for the run log) alongside the question, so a report chat titles from "Report generated: Houston (Customer Broker Office), June 2026 vs May 2026." rather than from "June 2026". Both the public `/api/conversations/{cid}/messages` route and the internal route the Next app calls share `stream_turn`, so one change covers both.

**Tech Stack:** Python 3.14 in `backend/.venv`, FastAPI, SQLAlchemy text SQL, pytest (pg-marked tests against the compose `db`). No web change: `web/src/app/c/[id]/Chat.tsx` already refreshes the sidebar on every finish and `turn-stream.ts` already forwards `title`.

**Spec:** `docs/superpowers/plans/2026-11-16-live-anthropic-findings.md`, finding F2 (observed live 2026-11-16: conversations 1 and 3 of that session).

**Model split:** Sonnet implements both tasks (pg test harness has real subtleties; Haiku is a rung too low). Sonnet reviews each diff; Fable does the final whole-branch review. Fable writes no code.

## Global Constraints

- Branch `git checkout -b fix/flow-chat-titles main`; before every commit run `git rev-parse --abbrev-ref HEAD` and confirm; after, `git log --oneline main..HEAD` shows only this work. Anything else: stop and report.
- Commit messages via a scratch file and `git commit -F <path>` (PowerShell 5.1 mangles quotes).
- Interpreter is always `backend/.venv/Scripts/python.exe`; never bare `python`.
- Offline suite: `cd backend && env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live'` redirected to a file, never piped. Expected baseline 2462 passed, 15 skipped (CLAUDE.md, 2026-10-23).
- pg suite (chat output text changes, so it is mandatory): compose `db` up and `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon` (port 5434 via the scratch override `.superpowers/sdd/2026-11-16-live-anthropic/compose.live-anthropic.yml`, or 5432 with the tracked file), then `cd backend && .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg` serially, never parallel. Baseline 355 passed, 1 skipped.
- `ruff check .` clean; never `ruff format`.
- Contracts corpus untouched: `cd backend && .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/contracts -q` passes and `git status --short contracts/` is empty. The done frame's `title` field is additive and not pinned in the corpus (grep of `contracts/turns/*.json` finds no done-frame `title`), so no regeneration is expected; if the corpus test fails, stop and report rather than regenerate.
- Never Bedrock; no `-m router_live`.
- No `gh pr create` until Carlos says so.

## Review Focus

1. A second `ok` turn in an already-titled conversation must not retitle it (the old `turn_index == 1` guard did this for free; the new guard reads the store). Test: Task 2 `test_title_is_computed_once_and_not_recomputed_on_later_ok_turns`.
2. A replayed turn (duplicate `client_turn_key`) in an untitled conversation must not title it, even though its status is `ok`. Guard stays `not outcome.replayed`; Task 2 step 3 keeps it and the docstring says why.
3. `get_title` on a conversation another user owns must return `None` (RLS filters the SELECT), so the gate never titles across users. Test: Task 1 `test_get_title_is_none_for_another_users_conversation`.
4. A title-model failure on the report turn must fall back to the question, not to the summary line, so the existing fallback contract (`question[:60]`) and its test hold. Task 2 step 3 pins the fallback.
5. The default-chat path where turn 1 is a clarify (ambiguous customer) and turn 2 answers now titles on turn 2. Covered by Task 2 `test_title_set_on_the_first_ok_turn_after_a_clarify` (brief flow, same mechanism).

---

### Task 1: `TurnOutcome.answer_summary` and `UserHistory.get_title`

**Files:**
- Modify: `backend/poseidon/core/chat/orchestrator.py:460-479` (dataclass), `:897` (default path outcome), `:1584` (brief ok outcome), `:1901` (report ok outcome)
- Modify: `backend/poseidon/core/chat/history.py:308` (SQL constants), `:407-441` (`create_conversation`), after `:637` (`set_title`)
- Test: `backend/tests/test_history_store.py` (next to `test_set_title_updates_the_row`, line 806), `backend/tests/test_chat_orchestrator.py`

**Interfaces:**
- Produces: `TurnOutcome.answer_summary: str | None = None` (additive, last field, default `None`; the same string the run log's `turn_run.answer_summary` receives on that path, already capped by `_capped`). Set on the three `status="ok"` constructions: default path (`_capped(turn_result.text)` when `turn_result.status == "ok"`, else `None`), brief (`"Brief generated for {subject}."`), report (`report_flow.answer_summary(payload)`). Clarify, error and replay constructions leave the default.
- Produces: `history.UNTITLED: str = "New chat"` module constant, used by `create_conversation` for both the INSERT and the returned dict.
- Produces: `UserHistory.get_title(cid: str) -> str | None`: the row's `title`, or `None` for an absent, malformed or RLS-invisible `cid` (never raises). SQL constant `_GET_TITLE_SQL = text("SELECT title FROM conversations WHERE id = :id")`, same shape as `_READ_STATE_SQL`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_history_store.py`, beside `test_set_title_updates_the_row` and using its fixtures and markers:

```python
def test_get_title_returns_new_chat_for_a_fresh_conversation(history_store):
    user = history_store.for_user("dev|alice")
    conversation, _opener = user.create_conversation()
    assert user.get_title(conversation["id"]) == UNTITLED
    user.set_title(conversation["id"], "Houston June 2026")
    assert user.get_title(conversation["id"]) == "Houston June 2026"


def test_get_title_is_none_for_unknown_and_malformed_ids(history_store):
    user = history_store.for_user("dev|alice")
    assert user.get_title("not-a-uuid") is None
    assert user.get_title(str(uuid7())) is None


def test_get_title_is_none_for_another_users_conversation(history_store):
    alice = history_store.for_user("dev|alice")
    conversation, _opener = alice.create_conversation()
    assert history_store.for_user("dev|bob").get_title(conversation["id"]) is None
```

In `backend/tests/test_chat_orchestrator.py` (offline):

```python
def test_turn_outcome_answer_summary_defaults_to_none():
    outcome = TurnOutcome(status="clarify", message_id="m", turn_run_id=None)
    assert outcome.answer_summary is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_history_store.py -k get_title -v` (pg; db up) and `... tests/test_chat_orchestrator.py -k answer_summary_defaults -v`
Expected: FAIL with `AttributeError: 'UserHistory' object has no attribute 'get_title'` / `ImportError: UNTITLED` and `AttributeError: ... 'answer_summary'`.

- [ ] **Step 3: Implement**

`history.py`: add `UNTITLED`, `_GET_TITLE_SQL`, `get_title` (mirror `read_state`: `_parse_uuid`, `self._transaction()`, `.first()`, `None` when no row). Replace the two `"New chat"` literals in `create_conversation` with `UNTITLED`.

`orchestrator.py`: add the field; at line 897 pass `answer_summary=_capped(turn_result.text) if turn_result.status == "ok" else None` (the same expression line 859 already uses for the writer); at 1584 pass the local `answer_summary`; at 1901 pass `_capped(report_flow.answer_summary(payload))` (the value line 1880 passes to `finalize`; bind it once to a local and use it in both places).

- [ ] **Step 4: Run the tests to verify they pass, then the offline suite and ruff**

Run the two commands from step 2. Expected: PASS.
Run the offline suite to a file and `ruff check .`. Expected: 2462 passed, 15 skipped (plus the one new offline test), ruff clean.

- [ ] **Step 5: Commit**

Scratch-file message: `feat(chat): carry answer_summary on TurnOutcome; UserHistory.get_title and UNTITLED`.

---

### Task 2: Title on the first `ok` turn, with the answer summary as input

**Files:**
- Modify: `backend/poseidon/api/live_chat.py:224-248` (module docstring paragraph "Titles at turn one"), `:884-900` (`turn_index_holder` and the `accepted` read in `send`), `:957-1000` (`_finalize_turn`)
- Modify: `backend/tests/test_history_cutover.py:338-347` (docstring only: the gate is no longer `turn_index == 1`)
- Test: `backend/tests/test_history_cutover.py` (pg, beside the three existing title tests)

**Interfaces:**
- Consumes: `UserHistory.get_title`, `history.UNTITLED`, `TurnOutcome.answer_summary` (Task 1).
- Produces: unchanged wire shape. The `done` frame's additive `"title"` is a string on the first `ok`, non-replayed turn of an untitled conversation and `null` on every other turn.

- [ ] **Step 1: Write the failing tests**

All three are `@pytest.mark.pg @pytest.mark.anyio`, built exactly like `test_title_set_after_first_successful_turn_persisted_and_in_done_frame` (same `_headers`, `_dev_user`, `_app`, `read_sse`, listing). For the recording client, copy the shape of `_UtilityErrorRoleClient` in `test_title_fallback_to_first_60_chars_when_title_for_returns_empty` but record instead of failing: `invoke` appends `(role, messages)` to a list and delegates every role to the real client. Wire it the way that test wires its wrapper.

```python
async def test_title_set_on_the_first_ok_turn_after_a_clarify(pg_database_url):
    # turn 1: "start an existing-customer brief" -> clarify, title None, listing "New chat"
    # turn 2: "Atlas Bunkering" -> brief runs (stub synthesis, fixture research), status ok
    assert done_payloads_turn1[0]["title"] is None
    assert done_payloads_turn2[0]["title"] == _STUB_TITLE
    assert items[0]["title"] == _STUB_TITLE


async def test_title_is_computed_once_and_not_recomputed_on_later_ok_turns(pg_database_url):
    # "hello" then "hello again", both ok, recording role client
    assert done_turn1["title"] == _STUB_TITLE
    assert done_turn2["title"] is None
    assert [role for role, _m in recorded].count("utility") == 1


async def test_title_input_carries_question_and_answer_summary(pg_database_url):
    # "hello", recording role client; the one utility call's user content
    content = utility_messages[0]["content"]
    assert content.startswith("hello")
    assert "I can answer certified metric questions" in content  # the stub router's reply, i.e. the answer summary
```

Use the customer name the existing brief e2e tests in `tests/test_chat_e2e_scripted.py` use for the existing-customer brief if it is not "Atlas Bunkering"; `test_title_is_not_set_for_a_turn_index_one_clarify`'s docstring records that "Atlas" resolves to "Atlas Bunkering" against the seeded pool.

- [ ] **Step 2: Run them to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_history_cutover.py -k "first_ok_turn_after or computed_once or carries_question" -v`
Expected: the first fails on `done_payloads_turn2[0]["title"] is None` (turn 2 is not turn 1); the second passes its first two assertions but is still worth keeping as the regression pin, and it must fail on the third assertion if the implementer first tries a gate without `get_title` (verify this by temporarily gating on `status == "ok"` alone before writing the real gate, then restore); the third fails because the utility content is exactly `"hello"` with no summary.

- [ ] **Step 3: Implement in `_finalize_turn`**

Gate: `outcome.status == "ok" and not outcome.replayed and user_history.get_title(cid) == UNTITLED`.
Title input: `text` when `outcome.answer_summary is None`, else `f"{text}\n\n{outcome.answer_summary}"`.
Fallback unchanged: `title = computed if computed else text[:60]`.
Delete `turn_index_holder` and the `accepted`-frame read in `send` (the gate no longer uses the index); keep the `try/except` and its `logger.error` exactly as they are, so a `get_title` failure is also "no title".
Rewrite the module docstring paragraph "Titles at turn one" (lines 224-248) and `_finalize_turn`'s docstring to describe the new gate in the same register; keep the `replayed` rationale. Fix the `test_title_is_not_set_for_a_turn_index_one_clarify` docstring's first sentence to say the gate is "untitled and `ok`".

- [ ] **Step 4: Prove the guard by breaking it, then run the suites**

Temporarily change the gate to `outcome.status == "ok" and not outcome.replayed` (drop the `get_title` clause): `test_title_is_computed_once_and_not_recomputed_on_later_ok_turns` must fail on the utility-call count. Restore.
Run the full pg suite serially to a file. Expected: 355 + 3 new = 358 passed, 1 skipped; the three pre-existing title tests still pass unchanged.
Run the offline suite to a file, `ruff check .`, and `tests/contracts` with `git status --short contracts/` empty.

- [ ] **Step 5: Commit**

Scratch-file message: `fix(chat): title a conversation on its first ok turn, not only turn one (F2)` with a body naming the three pg tests and the broken-guard check.

---

## Self-review notes

- Spec coverage: F2 names the report flow and the brief flow; Task 2's tests cover the brief flow directly and the report flow by the same gate. A report-flow pg test was considered and left out: the existing brief test exercises the identical "clarify turns then ok" path with one fewer seeded-data dependency (office chip texts). If the reviewer wants the report case pinned too, it is one more test of the same shape using the chip texts from the live session: "start a monthly report", "customer office report", "Houston", "June 2026".
- Types: `get_title` returns `str | None`; `UNTITLED` is compared with `==`; `answer_summary` is `str | None` throughout.
- No web task: verified `Chat.tsx` calls `router.refresh()` on finish and `turn-stream.ts` forwards `p.title`; nothing on the web side keys off turn index.

## Handoff prompt for the execution chat (paste into a fresh chat)

> You are Fable, supervising Poseidon in C:\Users\carlo\github\latest_poseidon (design,
> dispatch, review; never implementation code). Read `CLAUDE.md` (gotchas), then
> `docs/superpowers/plans/2026-11-16-flow-chat-titles.md` (the plan, approved by Carlos) and
> finding F2 in `docs/superpowers/plans/2026-11-16-live-anthropic-findings.md`. State: `main`
> @ 1e47a74, nothing deployed. Confirm with `git checkout main && git pull --ff-only`, then
> `git checkout -b fix/flow-chat-titles main`. Execute with
> superpowers:subagent-driven-development: one Sonnet implementer per task with the task's
> text as its spec, a fresh Sonnet reviewer per diff who never re-runs the suite, fix rounds
> until clean, then Fable's own whole-branch read. The implementer runs the pg suite (compose
> `db` on 5434 via `.superpowers/sdd/2026-11-16-live-anthropic/compose.live-anthropic.yml`
> and `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon`) and the
> offline suite with `env -u PERPLEXITY_API_KEY` and the full marker exclusion, each to a
> file. Commit via scratch file and `git commit -F`. When both tasks are green, write the PR
> description (what, why, how verified) to a scratch file and STOP: `gh pr create` only on
> Carlos's word; he merges in the GitHub UI. Never Bedrock; never bare `python`; never pipe
> pytest; never `ruff format`.

---

## Execution status (2026-11-16)

**MERGED as PR #16 (https://github.com/sal1391/latest_poseidon/pull/16), merge commit 92b091c on main, 2026-11-16. SDD workspace deleted; local and remote branch gone.** Commits on top of main @ 1e47a74:
`7c4cad0` plumbing (Task 1), `c7c85f4` the gate (Task 2), `5eb990c` final-review fix (store read inside the guard + two cosmetics).
Suites: pg 362 passed / 1 skipped; offline 2463 / 15; contracts 9 passed, corpus untouched; ruff clean.
PR description and ledger were in the SDD workspace, now deleted; git history is the record.
Deferred to Carlos: no test pins `not outcome.replayed`; brief/report outcomes lack a direct `answer_summary` assertion; computed-once test uses plain "hello" turns.

## Handoff prompt for the next chat (paste into a fresh chat)

> You are Fable, supervising Poseidon in C:\Users\carlo\github\latest_poseidon (design, dispatch,
> review; never implementation code). Read `CLAUDE.md` (gotchas) and the "Execution status" at the
> bottom of `docs/superpowers/plans/2026-11-16-flow-chat-titles.md`. State: F2 is MERGED as PR #16
> (main @ 92b091c, 2026-11-16); nothing deployed. Confirm with `git checkout main && git pull --ff-only`.
> Pick the next finding from
> `docs/superpowers/plans/2026-11-16-live-anthropic-findings.md` (F1 needs the (a)/(b) call; F5 run-log
> coverage is the next sized PR). Never Bedrock; never bare `python`; never pipe pytest; never `ruff format`.
