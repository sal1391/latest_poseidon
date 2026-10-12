# Clarify chips re-run the original question — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan. Per Carlos's rules: Fable writes specs and reviews diffs only; the implementation goes to the Opus worker named below. The implementer runs the tests and its report is the evidence; the reviewer does NOT re-run the suite.

**Goal:** Clicking a clarify chip re-asks the user's ORIGINAL question with the chosen name filled in, instead of sending only a bare "Port of Singapore" / "for Meridian Marine" that drops the rest of the question.

**Observed bug (live walkthrough 2026-10-12):** "top customers by gross profit in singpore in April 2026" -> chip "Singapore" -> click -> answer was one Gross Profit total (244643), not the top-customers table. The chip sent only `Port of Singapore`; "top customers" lived only in the first message, which the carried slots do not hold. Customer chips have the same shape (`for <name>`).

**Architecture:** Stateless rewrite. When the orchestrator builds a chip, it takes the turn's normalized message text and replaces the first occurrence of the unresolved phrase with the candidate. The chip's `send_text` becomes the full corrected sentence. No new conversation state, no new SSE frame or part kind (the chips part shape is unchanged; only the `send_text` string differs). If the phrase cannot be found in the text, the chip falls back to today's short `send_text`.

**Tech Stack:** Python 3.14 (`backend/.venv`), pytest. No frontend change.

**Spec:** No separate spec. Decision source: Carlos, 2026-10-12 ("i agree with you"); both port and customer chips unless Carlos says ports only (see "Open decision").

## Global Constraints

- Frozen, never edited: `ontology/ontology.yml`, metric formulas and query builder, Alembic 0001-0010, the SSE envelope frame kinds and the ten part kinds, RLS/identity wiring.
- `contracts/turns/clarify_ambiguous_customer.json` pins the current customer chip text (`"send_text": "for Meridian Tankers"` etc.). This task changes that text on purpose, so regenerate with `POSEIDON_UPDATE_CONTRACTS=1` in the SAME commit and report the exact diff. The only acceptable diff is the `send_text` values (in the frames and in the folded parts). Any other change in the corpus: stop and report. `web/src/lib/turn-contracts.test.ts` also reads this corpus; the web suite must stay green.
- Repo gotchas are binding (CLAUDE.md): `.venv/Scripts/python.exe`; never pipe pytest; full marker exclusion plus `env -u PERPLEXITY_API_KEY`; never `ruff format` repo-wide.
- Baseline before this task: offline suite 1889 passed / 13 skipped / 119 deselected; web 142 passed / 13 skipped offline.
- Commit: local only, on `phase-16-chat-parity`, `git add <named paths>`, trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`, never `--no-verify`, never push.
- Do not change `_finish_subject_clarify` (the Existing-customer brief flow). Its chips already name a customer outright and the user's reply there IS the subject.

## Review Focus

1. Replacement hits the right span: the phrase appears inside a longer word ("singpore" inside nothing, but "Meridian" inside "Meridian Marine" if the user already typed more), or twice in the message. Replace the first whole-phrase, case-insensitive occurrence only.
2. The rewritten sentence must itself parse cleanly: re-running it through `parse_turn` resolves the chosen entity and raises no new issue for that entity.
3. A quoted customer ("Meridian") keeps its quotes in the rewrite.
4. Two issues in one turn (customer ambiguous AND port near-miss): the customer chip rewrites only the customer phrase; the port issue is asked on the next turn. No double rewrite.
5. Fallback: phrase not found in `normalized_text` (carry-derived, normalisation difference) -> today's short `send_text`, and a test pins it.
6. Long messages: `send_text` is the whole sentence; the chip LABEL stays the short candidate name.

## File Structure

| File | Responsibility |
|---|---|
| `backend/poseidon/core/parsing/pipeline.py` | customer issues also carry `phrase=<detected phrase>` (port issues already do) |
| `backend/poseidon/core/chat/orchestrator.py` | build chip `send_text` by rewriting `parsed.normalized_text`; short-form fallback |
| `backend/tests/test_chat_orchestrator.py` | orchestrator tests for rewritten chips and fallback |
| `backend/tests/test_parsing_hinter_pipeline.py` | assert customer issues now carry `phrase` |
| `backend/tests/contracts/scenarios.py` | only if the regenerated scenario needs it (report if touched) |
| `contracts/turns/clarify_ambiguous_customer.json` | regenerated, send_text values only |

Sanctioned modifications are exactly these rows. A need for any other path: stop and report.

---

### Task 3: Chips carry the full corrected question

**Worker:** Opus. **Depends on:** Task 2 committed (994bbd0).

**Interfaces:**
- Consumes `ParseIssue.phrase` and `ParseIssue.kind` (Task 1), `parsed.normalized_text`.
- Produces `_rewrite_for_chip(text: str, phrase: str, candidate: str) -> str | None` in `orchestrator.py`: returns `text` with the first whole-phrase, case-insensitive occurrence of `phrase` replaced by `candidate`, or `None` when `phrase` is empty or not found. "Whole-phrase" means the match is not flanked by an alphanumeric character on either side.
- Chip `send_text` = the rewritten text when not `None`; otherwise today's value (`for {candidate}` for customer chips, `PORT_CHIP_SEND_TEXT.format(candidate=...)` for port chips). The `id` and `label` stay the bare candidate.

**Behaviour spec:**
- In `pipeline.py`, set `phrase` on every customer-kind issue the customer plan keeps (the detected phrase, exactly as the port side already does via `dataclasses.replace`). Customer issues from carry have no span and never raise issues, so no special case.
- In `orchestrator.py`, both chip builders (`_push_customer_chips` and the port chips branch of `_finish_clarify`) call `_rewrite_for_chip` with `parsed.normalized_text`. Thread `parsed` or `normalized_text` into them with the smallest signature change.
- Unknown-port text-only question: same behaviour (text part only, no chips), but the COPY changes so a bare answer is not the natural reply. `PORT_UNKNOWN_TEXT` becomes `'I don't recognise "{phrase}" as a port. Which port do you mean? For example: "in Singapore".'` (a pure string constant change in `orchestrator.py`; the existing Task 2 test that checks the wording must be updated to the new constant value and still name the phrase).

**Tests (write first; show them failing; say why that failure is right):**

| Original message | Candidate | Expected `send_text` |
|---|---|---|
| `top customers by gross profit in singpore in April 2026` | Singapore | `top customers by gross profit in Singapore in April 2026` |
| `top customers by gross profit lifted at signpore in April 2026` | Singapore | `top customers by gross profit lifted at Singapore in April 2026` |
| `top customers by gross profit for Meridian in April 2026` | Meridian Marine | `top customers by gross profit for Meridian Marine in April 2026` |
| `top customers by gross profit for "Meridian" in April 2026` | Meridian Marine | `top customers by gross profit for "Meridian Marine" in April 2026` |
| phrase not present in text (construct an issue by hand) | Singapore | `Port of Singapore` (fallback) |
| customer issue with empty `phrase` (hand-built) | Meridian Tankers | `for Meridian Tankers` (fallback) |

Plus:
- **Round trip, port:** feed the rewritten `send_text` back through `parse_turn`: `port.value` is Singapore, no issues, the period is unchanged.
- **Round trip, customer:** same for `customer.value == "Meridian Marine"`.
- **Round trip, end to end:** in the orchestrator test harness, send the rewritten text as the next turn and assert it dispatches the metric with the top-customers grouping that the first message asked for (use the existing scripted pattern in `test_chat_e2e_scripted.py` or `test_chat_orchestrator.py`; whichever already dispatches `top` queries).
- **Two issues in one turn:** customer ambiguity plus port near-miss: customer chips rewrite only the customer phrase and the text still contains the unresolved port phrase.
- **Whole-phrase match:** a phrase that is a substring of another word is not replaced there (construct a hand-built case).
- **Copy:** the unknown-port question text equals the new `PORT_UNKNOWN_TEXT.format(phrase="Narnia")`, contains `For example: "in Singapore".`, and the text part is still the ONLY part (no chips).
- Existing orchestrator clarify tests that pinned `for <name>` chips must be updated to the new expected text only where the original message contains the phrase; list every changed assertion in the report.
- Contracts: regenerate once, paste the `git diff --stat contracts/` and the changed lines in the report.

- [ ] **Step 1:** Write the failing tests above; run them; confirm they fail for the right reason (chips still send the short text).
- [ ] **Step 2:** Add `phrase` to customer issues in `pipeline.py`; parser tests green.
- [ ] **Step 3:** Implement `_rewrite_for_chip` and wire both chip builders.
- [ ] **Step 4:** Regenerate `contracts/turns/` once; inspect the diff.
- [ ] **Step 5:** Run the offline suite (full exclusion) to a file; `ruff check .`; `tests/contracts`; from `web/`: `npm test` and `npx tsc --noEmit`.
- [ ] **Step 6:** Commit: `feat(chat): clarify chips re-ask the original question with the chosen name (P16-R24)`.

**Report must include:** failing-first output, final counts vs. baselines, the contracts diff, every changed pre-existing assertion with the reason, `git show --stat HEAD`.

---

## Phase gate: live check (Fable drives, then Carlos clicks)

| Do | Expect |
|---|---|
| `top customers by gross profit in singpore in April 2026`, click Singapore | The transcript shows the full corrected sentence as your message; the top-customers table for Singapore comes back |
| `top customers by gross profit for Meridian in April 2026`, click Meridian Marine | Full corrected sentence; top-customers table filtered to Meridian Marine |
| Existing customer flow, `Meridian`, click a chip | Unchanged: the brief runs (this flow is not touched) |
| `top customers by gross profit in Narnia in April 2026` | The question now ends with `For example: "in Singapore".`; reply with `in Singapore` and the table for Singapore comes back |

## Decisions (Carlos, 2026-10-12)

- Ports AND customers (decided). Customer chips change the saved golden file, which is reviewed in the diff.
- The bare-answer gap gets the copy fix inside this task (decided). The pending-question state option is NOT built.

## Noted, not in this task

- A bare answer (`Singapore` with no cue) to the unknown-port question is still not understood by the parser; the copy fix steers users to a cued reply. Remembering a pending port question (new state) is a possible later task.
- Lowercase customer names after "for" are never detected (carry-forward from the port plan).

## Self-Review

- **Spec coverage:** port chips (table rows 1-2), customer chips (rows 3-4), fallback (rows 5-6), the live bug (end-to-end round trip), the golden-file consequence (contracts step).
- **Placeholder scan:** none.
- **Type consistency:** `_rewrite_for_chip(text, phrase, candidate) -> str | None` is the only new name; `ParseIssue.phrase` exists from Task 1.
- **Sanction-line check:** every path named in the task is in the File Structure table, including the regenerated golden and `scenarios.py` if touched. Symmetric pair checked: port chips and customer chips are both covered; the Existing-customer brief flow is explicitly excluded.
