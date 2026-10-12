# Port cues and "what do you mean?" clarification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Per Carlos's rules: Fable writes specs and reviews diffs only; implementation goes to the worker model named on each task. Implementers run the tests and their report is the evidence; reviewers do NOT re-run the suite.

**Goal:** "top customers by gross profit in Singapore" applies the Singapore port filter, and a misspelled or unknown place ("in singpore", "lifted at signpore", "in Narnia") brings back a question instead of a silent all-ports answer.

**Architecture:** The deterministic parser (`backend/poseidon/core/parsing/`) gains an "in" port cue, lowercase port detection and a strict resolution mode that asks before applying a fuzzy match. `ParseIssue` gains two additive fields so the orchestrator can tell a port clarification from a customer one. The orchestrator's existing clarify short-circuit learns port chips and a text-only "which port?" question. No new SSE frame or part kind.

**Tech Stack:** Python 3.14 (`backend/.venv`), rapidfuzz, pytest. No frontend change.

**Spec:** No separate spec. Decision source: Carlos, 2026-10-12 conversation. Diagnosis: `parse_turn` only detects a port after the cues "port of X" or "at X" and only for a capitalised name (`pipeline.py` `_PORT_OF_RE`, `_PORT_AT_RE`, `_TITLECASE_RUN`). A weak "at X" miss deliberately emits no issue (module docstring "Strong and weak port cues"). Task A (P16-R22) already made `resolve()` ask when several customers tie.

## Global Constraints

- Frozen, never edited: `ontology/ontology.yml`, metric formulas and query builder, Alembic 0001-0010, the SSE envelope frame kinds and the ten part kinds, `contracts/turns/` goldens (do NOT set `POSEIDON_UPDATE_CONTRACTS`; if a golden fails, stop and report), RLS/identity wiring.
- Repo gotchas are binding (CLAUDE.md): always `.venv/Scripts/python.exe`; never pipe pytest; full marker exclusion plus `env -u PERPLEXITY_API_KEY`; never `ruff format` repo-wide.
- Offline suite baseline before this plan: 1718 passed / 13 skipped / 119 deselected, plus Task A's additions. Each task must report counts against the previous task's final count.
- Commits: local only, on `phase-16-chat-parity`, `git add <named paths>` (never `-A`; unrelated docs files are dirty), trailer `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`, never `--no-verify`, never push.
- User-visible copy lives in named constants (see Task 2) so Carlos can change wording in one place.
- Port issues keep the existing issue CODES (`customer_ambiguous` / `customer_unknown`, documented in `types.py`); the new `kind` field is what distinguishes them.

## Decisions taken (Carlos 2026-10-12, plus two stated assumptions)

1. Several customers tie on a short name -> ask (Task A, separate commit).
2. "in <port>" applies the port. Unknown or misspelled places bring back a question.
3. **Assumption 1 (not yet explicitly confirmed):** for the NEW detection paths (the "in" cue, and any lowercase name after "in", "at" or "port of"), only an exact or word-set match applies automatically; a fuzzy match asks "did you mean" even when one port scores high. The existing capitalised "at X" / "Port of X" paths keep today's behaviour (a single fuzzy match >= 0.80 still applies).
4. **Assumption 2:** a weak-cue ("in"/"at") phrase that matches NOTHING raises the unknown-port question only when it is a capitalised phrase that is not period-like and not customer-like. Lowercase unknowns stay silent ("in general", "at risk"). This keeps the documented protection that "gp at Q3" and "take a look at Northstar Lines" are not failed port lookups.
5. Out of scope, noted as carry-forward: customer names typed in lowercase after "for" are also never detected (`_CUSTOMER_CUE_RE` needs a capitalised run). Symmetric to this work but not requested.

## Review Focus

Inputs most likely to bite, each pinned by a test named in the owning task:

1. Period phrases after the new cue: "in April", "in Q1 2026", "in FY2026", "in 2026", "in the last quarter", "in H2" must raise no port issue and must not change the parsed period.
2. Ordinary English after "in"/"at": "in total", "in general", "in detail", "at risk", "at the moment", "at Q3" must raise no port issue.
3. A customer name after "at": "take a look at Northstar Lines" / "at Meridian Marine" must not become a port question.
4. Multi-word ports: "in New Orleans", "in new orleans", "at Las Palmas", "in jebel ali", "in hong kong" apply the right port.
5. A clicked port chip must resolve the port when sent back as text ("Port of Singapore" -> port applied, no customer read from "Port").
6. Pool-fetch laziness: `parse_turn` must not fetch the port pool for messages with no surviving candidate phrase (existing invariant, `FakeDataClient.calls`).

---

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `backend/poseidon/core/parsing/types.py` | `ParseIssue` gains `kind: str = "customer"`, `phrase: str = ""` | 1 |
| `backend/poseidon/core/parsing/customer_resolver.py` | `resolve(...)` gains keyword-only `auto_apply_fuzzy: bool = True` | 1 |
| `backend/poseidon/core/parsing/pipeline.py` | cues, lowercase detection, guards, issue-retention rules, docstring rewrite | 1 |
| `backend/tests/test_parsing_resolver.py` | tests for `auto_apply_fuzzy` | 1 |
| `backend/tests/test_parsing_hinter_pipeline.py` | cue, guard, noise-corpus and laziness tests | 1 |
| `backend/poseidon/core/chat/orchestrator.py` | kind-aware clarify: port chips, text-only unknown question | 2 |
| `backend/tests/test_chat_orchestrator.py` | orchestrator tests (worker may add a new test file beside it if cleaner) | 2 |
| `docs/architecture/00-overview.md` | one decision-log row (D60) | 2 |

Sanctioned modifications per task are exactly the rows above. A task that needs any other path stops and reports.

---

### Task 1: Parser — "in" cue, lowercase ports, strict mode, issue retention

**Worker:** Opus. **Depends on:** Task A committed (`customer_resolver.py` is touched by both; Task 1 starts from Task A's HEAD).

**Files:**
- Modify: `backend/poseidon/core/parsing/types.py`, `customer_resolver.py`, `pipeline.py`
- Test: `backend/tests/test_parsing_resolver.py`, `backend/tests/test_parsing_hinter_pipeline.py`

**Interfaces:**
- Produces `ParseIssue(code, message, candidates=(), kind="customer", phrase="")`. Existing constructor calls and equality keep working because both new fields default.
- Produces `resolve(phrase, values, kind="customer", *, auto_apply_fuzzy=True) -> Resolution`. With `auto_apply_fuzzy=False`, a fuzzy best score >= `AUTO_APPLY_THRESHOLD` (even a unique one) returns `entity=None`, `candidates` = up to 3 values scoring >= `CANDIDATE_THRESHOLD` best-first, and issue code `customer_ambiguous`. Exact and token tiers still apply automatically. Default behaviour is identical to Task A's.
- Every port issue the pipeline emits carries `kind="port"` and `phrase=<the detected phrase>`; customer issues keep `kind="customer"`.

**Behaviour spec (the worker implements; the tests below pin it):**

Cues, in priority order: "port of" (STRONG), "at" (weak), "in" (weak, NEW). The cue word is case-insensitive.

Phrase capture after a cue:
- Capitalised run: existing `_TITLECASE_RUN` behaviour, unchanged.
- Lowercase (or mixed) run: take up to 3 words after the cue, stopping at the first stop token. Stop tokens: punctuation `, . ? ! ;`; the words `in on at for of and by during from to vs versus with than the a an this last next previous past ytd mtd qtd`; quarter/half/fiscal tokens matching `q[1-4]`, `h[1-2]`, `fy\d*`; any 4-digit year; any month word (reuse `_MONTH_WORDS`). If the first word is a stop token there is no candidate phrase and the port pool is NOT fetched.
- Resolution of a lowercase phrase: try the full captured phrase, then shorter prefixes (3 words, 2, 1), using strict mode (`auto_apply_fuzzy=False`). The first prefix that resolves exact/token wins and its span is the one masked. If none resolves exact/token, use the full phrase's fuzzy result (candidates or unknown).
- Capitalised run after "in": strict mode. Capitalised run after "at"/"port of": legacy mode (`auto_apply_fuzzy=True`), exactly today's behaviour.

Issue retention (replaces the blanket "weak miss emits no issue" rule):
- Auto-resolved: no issue, mask span, slot updated (unchanged).
- Candidates present (a near-miss) on ANY cue: keep the `customer_ambiguous` issue with `kind="port"`. Never masked, slot UNSET.
- Unknown (no candidates):
  - strong cue ("port of"): keep the issue (today's behaviour) with `kind="port"`.
  - weak cue, capitalised phrase: keep the issue ONLY IF the phrase is not period-like (first word is not `q[1-4]`, `h[1-2]`, `fy\d*`, a 4-digit year, a month word, `week`, `quarter`, `year`, `ytd`, `mtd` or `qtd`) AND the phrase does not match the customer pool (exact/token, or fuzzy >= `CANDIDATE_THRESHOLD`). Fetch the customer pool for this check only when the other conditions already passed.
  - weak cue, lowercase phrase: drop the issue (silent).
- Rewrite the pipeline module docstring sections "Strong and weak port cues" and "Masking" so they describe the new rules truthfully, including the carry-forward in Decision 5.

**Tests (write first, show them failing, say why that failure is the right one).** Table-driven with `FakeDataClient` and the file's `REF` date. If a needed port ("New Orleans", "Las Palmas", "Jebel Ali", "Hong Kong") is missing from the fake's `PORTS`, construct `FakeDataClient(ports=...)` inside the test rather than editing the shared constant.

Applies the port (assert `parsed.port.value`, no issues, and the parsed period equals the period of the same message with the place text removed):

| message | expected port |
|---|---|
| `top customers by gross profit in Singapore in April 2026` | Singapore |
| `top customers by gross profit in singapore in April 2026` | Singapore |
| `gross profit at singapore in April 2026` | Singapore |
| `gross profit in new orleans in April 2026` | New Orleans |
| `gross profit in Jebel Ali in April 2026` | Jebel Ali |
| `gross profit in hong kong in April 2026` | Hong Kong |
| `gross profit for Port of Singapore in April 2026` | Singapore (regression) |
| `gross profit at Singapore in April 2026` | Singapore (regression) |

Asks "did you mean" (assert `port is None`, exactly one issue, `code == "customer_ambiguous"`, `kind == "port"`, first candidate as listed):

| message | expected first candidate |
|---|---|
| `gross profit in singpore in April 2026` | Singapore |
| `gross profit lifted at signpore in April 2026` | Singapore |
| `gross profit in Singpore in April 2026` | Singapore |

Legacy path pinned unchanged: `gross profit at Singpore in April 2026` (capitalised, "at") still auto-applies Singapore exactly as today. Run it on the pre-change code first to record the true current result, then pin that.

Unknown question (assert `port is None`, one issue, `code == "customer_unknown"`, `kind == "port"`, `phrase == "Narnia"` or `"Atlantis"`):

| message |
|---|
| `gross profit in Narnia in April 2026` |
| `gross profit for Port of Atlantis in April 2026` |

Stays silent (assert zero port-kind issues, `port is None`, and the parsed period equals the period of the same message with the place text removed):

| message |
|---|
| `gross profit in total in April 2026` |
| `show me the numbers in general for April 2026` |
| `gross profit in detail for April 2026` |
| `what is our exposure at risk in April 2026` |
| `gross profit at the moment in April 2026` |
| `gp at Q3` |
| `gross profit in Q1 2026` |
| `gross profit in FY2026` |
| `gross profit in 2026` |
| `gross profit in the last quarter` |
| `gross profit in H2 2026` |
| `take a look at Northstar Lines` |
| `gross profit at Meridian Marine in April 2026` (a customer, not a port) |

Plus:
- **Sweep test:** run every message already used by this file's parameterised pipeline cases (and the message strings in `tests/test_parsing_periods.py` if cheaply importable) through `parse_turn`; for every case whose existing expectations name no port issue, assert the new code emits no `kind == "port"` issue. This is the regression net for Review Focus 1-3.
- **Laziness:** `parse_turn("gross profit in the last quarter", ...)` leaves `client.calls` without a `LOC_NM` fetch; `parse_turn("gross profit in singapore in April 2026", ...)` fetches it exactly once.
- **Resolver tests** in `test_parsing_resolver.py`: `auto_apply_fuzzy=False` with a unique 0.9 fuzzy match returns candidates and the issue; the same input with the default still applies; exact and token tiers apply under both settings.
- **Round trip:** the text `Port of Singapore` alone (what a port chip will send in Task 2) resolves `parsed.port.value == "Singapore"` and `parsed.customer is None`.
- If any "stays silent" row trips the fuzzy floor, raise the floor used for UNPROMPTED lowercase fuzzy candidates (a documented module constant) until every silent row passes while every "asks" row still asks. Report the final value and the scores that justified it.

- [ ] **Step 1:** Add the tables above as failing tests; run them; confirm the failures are the expected ones (missing behaviour, not import errors).
- [ ] **Step 2:** Add the `ParseIssue` fields and the `resolve` keyword; resolver tests green.
- [ ] **Step 3:** Implement cue, capture, guard and retention rules in `pipeline.py` until the tables pass.
- [ ] **Step 4:** Rewrite the two docstring sections.
- [ ] **Step 5:** Run the offline suite (full exclusion) to a file, `ruff check .`, and `tests/contracts`. Expected: no failures, no golden diffs.
- [ ] **Step 6:** Commit: `feat(parsing): "in <port>" cue, lowercase ports and port clarification issues (P16-R23)`.

**Report must include:** failing-first output, final counts against the baseline, any pre-existing test changed and why, the floor decision if one was made, `git show --stat HEAD`.

---

### Task 2: Orchestrator — port chips and the "which port?" question

**Worker:** Opus. **Depends on:** Task 1 committed.

**Files:**
- Modify: `backend/poseidon/core/chat/orchestrator.py`, `docs/architecture/00-overview.md`
- Test: `backend/tests/test_chat_orchestrator.py` (or a new `backend/tests/test_chat_port_clarify.py` beside it)

**Interfaces:**
- Consumes: `ParseIssue.kind`, `ParseIssue.phrase`, `ParseIssue.candidates` from Task 1.
- Produces module constants in `orchestrator.py`:
  - `PORT_CHIP_SEND_TEXT = "Port of {candidate}"`
  - `PORT_AMBIGUOUS_TEXT = "Which port did you mean?"` (shown with the chips; the resolver's kind-neutral message is NOT reused for ports)
  - `PORT_UNKNOWN_TEXT = 'I don\'t recognise "{phrase}" as a port. Which port do you mean?'`

**Behaviour spec:**
- Today's clarify trigger takes the first `customer_ambiguous` issue. Extend the selection order to: first customer-kind ambiguous issue (unchanged: chips send `for {candidate}`); else first port-kind ambiguous issue (chips send `PORT_CHIP_SEND_TEXT`, text part `PORT_AMBIGUOUS_TEXT`); else first port-kind `customer_unknown` issue (NO chips part, one text part `PORT_UNKNOWN_TEXT`).
- Same lifecycle as today's clarify: no LLM call, no dispatch, `done`, `finalize(status="clarify")`, `state.put(parsed.slots)`, outbox touch. A customer-kind `customer_unknown` is NOT newly surfaced (unchanged).
- The unknown-port question emits only a `text` part; never an empty `chips` part.
- Do not modify `_finish_subject_clarify` (the brief flow's customer clarify) or any SSE or part definition.
- `docs/architecture/00-overview.md` decision log: add one row, D60, stating: port cues "port of"/"at"/"in"; lowercase detection; strict mode for new paths; port clarification via `ParseIssue.kind`; and that it "revises" the docstring claim that a weak-cue miss emits no issue. Follow the file's existing row format exactly.

**Tests (write first):**
- Port near-miss turn (`gross profit in singpore in April 2026`) -> status `clarify`, one `chips` part whose Singapore option has `send_text == "Port of Singapore"`, one `text` part equal to `PORT_AMBIGUOUS_TEXT`, no LLM call (use the file's existing LLM-call counter pattern), `state.put` called with the carried slots, run-log row status `clarify`.
- Unknown-port turn (`gross profit in Narnia in April 2026`) -> status `clarify`, exactly one part and it is `text` with the `PORT_UNKNOWN_TEXT` wording naming Narnia, no `chips` part, no LLM call.
- Customer ambiguity still wins when both are present, and its chips still send `for {candidate}` (regression).
- A customer-kind `customer_unknown` still does NOT short-circuit (regression, pins today's behaviour).
- **Chip round trip:** take the `send_text` from the port chip and run it through `parse_turn` with the carried slots: port resolves to Singapore, customer unchanged, carried period retained.
- `tests/contracts` unchanged and green.

- [ ] **Step 1:** Write the failing orchestrator tests; run; confirm they fail for the right reason (today a port near-miss silently runs and an unknown port short-circuits nothing).
- [ ] **Step 2:** Implement the selection order, the constants and the text-only branch.
- [ ] **Step 3:** Add D60 to the decision log.
- [ ] **Step 4:** Offline suite to a file, `ruff check .`, `tests/contracts`.
- [ ] **Step 5:** Commit: `feat(chat): port near-misses and unknown ports ask a clarification (P16-R23)`.

**Report must include:** failing-first output, final counts, regression-test results, `git show --stat HEAD`.

---

### Phase gate: live walkthrough (Fable drives the browser, then Carlos clicks)

Not a worker task. The compose stack and `npm run dev` are already running as of 2026-10-12. The offline stub router needs a resolved period in every message.

| Send | Expect |
|---|---|
| `top customers by gross profit in Singapore in April 2026` | Singapore filter applied; the proof or label shows Singapore |
| `top customers by gross profit in singpore in April 2026` | No metric run; chip "Singapore"; click it; metric runs for Singapore |
| `top customers by gross profit lifted at signpore in April 2026` | Same chip behaviour |
| `top customers by gross profit in Narnia in April 2026` | One text question, no chips, no metric run |
| `top customers by gross profit in the last quarter` | The usual period behaviour, NO port question |
| Existing customer flow, reply `Meridian` | Chips (3 of 10, with "and 7 more" in the text), not a silent pick (Task A) |
| `Meridian Marine` in that flow | Resolves directly |

To verify rather than assume: whether a clicked port chip ("Port of Singapore") re-runs the original metric question or only sets the slot. The existing customer chips have the same shape. Record what happens and let Carlos decide whether it needs follow-up.

---

## Self-Review

- **Spec coverage:** "in <port>" applies (Task 1 first table); typos ask (second table, Task 2 chips); unknown asks (third table, Task 2 text branch); Carlos's testing requirement is met by a named failing-first test for every behaviour, plus the sweep and the noise corpus.
- **Placeholder scan:** none. Where the worker must decide (the fuzzy floor), the plan says how to decide and what to report.
- **Type consistency:** `ParseIssue.kind` and `phrase`, `resolve(..., auto_apply_fuzzy=)` and the Task 2 constants match Task 1's Produces block.
- **Sanction-line check:** every path named in the task steps (`types.py`, `customer_resolver.py`, `pipeline.py`, `orchestrator.py`, the three test files, `00-overview.md`) is in the File Structure table. Symmetric pair checked: customer and port issue kinds are both covered; lowercase customers are explicitly out of scope (Decision 5).
