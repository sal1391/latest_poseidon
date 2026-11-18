# Phase 17 PR A2 — Report in Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A sales user clicks **Monthly report**, answers three deterministic questions (perspective, office, month), and gets the A1 report in the thread as a rendered `report` part with its PDF card, proof and three suggested-question chips; follow-up questions carry the office, perspective and month pair; the report's own figures are readable through `reporting.report_lookup`; the PDF downloads from a Next.js route after a restart.

**Architecture:** The orchestrator gains a third D19 flow (`mode = "report"`) whose step logic lives in a new pure module `core/chat/report_flow.py`; the report turn dispatches the A1 internal skill by hand, exactly like the brief subject turn does. `ConversationSlots` grows by four fields that the state block renders for the router. `data_qa.metric_query` learns an office `scope` and breakdown-with-comparison; a new router-visible `reporting.report_lookup` reads a stored run. The web app gets a `report` renderer and two route handlers that serve run bytes behind the Sales role. The synthetic generator gets a deterministic multi-office post-pass. Twelve A1 carry-forwards are fixed in the first two tasks.

**Tech Stack:** Python 3.14 in `backend/.venv`, pytest 9.1.1, FastAPI, SQLAlchemy plain SQL, Postgres 16 on port 5434 for `pg` tests; Next.js 16, React 18, Drizzle, vitest, react-markdown, Tailwind v4. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §6.1–6.5, 6.7–6.9, 7.2, §10, §11 row A, §12, §13. The A1 plan `docs/superpowers/plans/2026-10-12-phase-17-prA1-report-engine.md` (its Interfaces blocks) is the contract this plan consumes. Owner rulings of 2026-10-16 (binding here): one PR; the step wording in this plan's `report_flow.py` constants; the model insights are appended to `llm_md` in Python so every surface shows them; rulings 1–6 on the A1 YOUR CALL items as the morning-review file records them.

**Branch:** `phase-17-report-in-chat`, created from `main` at `1b2ae40` with `git checkout -b phase-17-report-in-chat main`. Work in this checkout (the `pg` suite and the reseed need the compose database on port 5434). Before every commit: `git rev-parse --abbrev-ref HEAD` must print `phase-17-report-in-chat`; after every commit `git log --oneline main..HEAD` must show only this plan's commits. Anything else: stop and report.

**Model split (owner rule, recorded here so it is reviewable):** Sonnet implements Tasks 1, 2, 3, 5, 7, 9, 10, 11; Opus implements Tasks 4 (breakdown-with-comparison and the scope stamp), 6 (the flow) and 8 (the overlay and the golden re-pins). No task carries complete code, so Haiku is not used. Reviews: Sonnet reviews Sonnet work; Opus reviews Tasks 4, 6, 8 and the whole branch at the end. Fable supervises only.

## Global Constraints

- Always `backend/.venv/Scripts/python.exe`, never a bare `python`.
- **Every pytest command in this plan, including path-targeted ones, carries `env -u PERPLEXITY_API_KEY` and the full marker exclusion** `-m 'not pg and not minio and not pdf and not router_live and not research_live'`. A1's Task 7 command lacked it and fired one paid Bedrock call. The pg suite is the one exception and is always run as the whole `-m pg` selection, serially.
- Never pipe pytest. Redirect to a file under `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/` and `tail` the file. No `-q` (pyproject already sets it).
- Offline suite (from `backend/`, bash):
  `env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live'`
- Path-targeted run (from `backend/`, bash), the shape every task's RED/GREEN step uses:
  `env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' <paths> > ../.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/<task>-<red|green>.txt; tail -n 8 ../.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/<task>-<red|green>.txt`
- pg suite (from `backend/`, bash; never parallelised):
  `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg > ../.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/<task>-pg.txt; tail -n 8 ...`
- Contracts corpus: `env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/contracts` after every backend task. Exactly three tasks regenerate it, each naming the one file it expects to change: Task 5 (`error_mid_turn.json`, the unknown-skill text gains `reporting.report_lookup`), Task 6 (`opener.json`), Task 7 (the two new report files). A diff anywhere else, or in any other task, means STOP and report.
- Lint: `.venv/Scripts/python.exe -m ruff check .` from `backend/`. Never `ruff format` repo-wide; `ruff format <path>` only on files this plan creates.
- Web: `cd web && npm test` and `npx tsc --noEmit` after every web task; `npm run lint` before the final commit.
- Source files stay pure ASCII on disk (A1 convention); display strings use the ASCII hyphen-minus.
- Frozen surfaces untouched: the SSE envelope, the existing part kinds (`report` is additive and already exists), the certified ontology and metric formulas, Alembic 0001–0010, migrations 0000–0003 (A2 adds no migration).
- `core/chat` and `core/llm` never import `poseidon.tasks`; skill ids in the orchestrator stay bare string literals (the file's own convention).
- Additive dataclass growth only: `ConversationSlots` and `SkillContext` grow by appending fields with defaults; never reorder.
- Any change to chat output text runs the pg suite, not only the offline suite (CLAUDE.md gotcha). Tasks 6, 8 and 11 run it.
- Commit after every task with the message given; every commit ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Commit messages with quotes go through a scratch file and `git commit -F`.
- Implementers run the tests and show the failing-first output in their report; reviewers never re-run the suite. Guards are proven by breaking the code ("revert X, confirm test Y fails, restore"), and the plan names which guard each task proves that way.

## Execution model (owner rule, applies to every Phase 17 PR)

- This plan is PR A2 and runs in one fresh chat. Fable dispatches one implementer per task and a fresh reviewer per diff, judges the reports, keeps the morning-review list, writes the PR description and the PR B handoff prompt. It never writes implementation code; the one exception is an implementer stuck after two or three real attempts.
- Ledger directory: `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/` (self-ignored). `morning-review.md` lives there.
- Tasks run in the order written. Tasks 1–8 are backend and leave the offline suite, `tests/contracts` and ruff green at every commit; Tasks 9–10 are web; Task 11 closes with the pg flow test, docs, figures and the final gates. Do not run `npm test` and the pg suite concurrently with another implementer's work in the same tree; there is one tree.

## Rulings made in this plan (for the morning-review list)

1. **The flow validates before it dispatches.** The month step checks presence and completeness itself (`list_months`, `reference_date`) and re-asks as a `clarify` turn with no tool frames; the skill's own 422s remain as belt and braces and surface as an `error` frame if ever reached.
2. **Month to date is parsed by the step, not the period parser.** The step recognises the D64 phrases (`month to date`, `mtd`, `so far this month`, `this month so far`) and the running month by name; everything else goes through `parse_periods` unchanged. The parser's grammar is not touched.
3. **A re-ask re-sends the chips.** Every clarify in the flow pushes the step's text then its chips again (text part first, chips second, matching the opener's order).
4. **Perspective re-ask copy** (not in the owner-approved drafts): `Pick a perspective: Customer office, Supplier office or Whole book.`
5. **`report_lookup` without an open report answers with a `text` part and `ok=True`** (the router relays it), proof line `Report: none open`. A 4xx would spend the loop's one self-correction on a question the user has to answer.
6. **Office scope stamping wins.** With a carried office and `scope == "office"`, the carried value replaces any model-supplied filter on the office column; the proof line names it. `scope == "book"` drops the carried office and says so in the proof.
7. **Breakdown with comparison runs each window's breakdown at `top_n = 50`** (the `Args` ceiling) and merges the union; entities outside both top-50 sets are not shown and the proof says `merged over the top 50 per window`. PR B's multi-column group-by may lift this.
8. **`Driver` is the last column of every comparison table** when GP and VOLUME are both requested (the spec's "plus Driver" read as "on the tables", not "one more table").
9. **The report turn's suggested questions are one `chips` part after the report.** The `report` renderer has no second question surface (spec §6.4's `SuggestedQuestions` would duplicate §6.3's chips; the chips part also disables itself while a send is in flight like every other chip).
10. **`pass_through` after a report is `((entity_column, key), ...)`** over the top ten of `entities_by_gp`, so the state block reads `CUST_NM=Northstar Lines, ...`.
11. **A2 adds no migration.** `conversation_id` already exists on `report_run`; the flow fills it through a new additive `SkillContext.conversation_id`.
12. **The `report` HTML route serves `Content-Disposition: attachment` too** (spec §7.2 says both routes do). Phase 18's panel can switch to inline when it owns the sanitiser.
13. **File names are sanitised on both sides with one rule:** keep `[A-Za-z0-9 ._-]`, replace anything else with `_`, collapse whitespace runs to one space, trim; empty becomes `report`. The Python artifact name and the TypeScript `Content-Disposition` filename use it, pinned by the same hostile office value in both suites.
14. **Visible-text polish.** A change or percentage whose rounded display is zero renders `flat` (`-0`, `+0`, `+0.0%`, `-0.00` can no longer appear); a value whose rounded display is `-0` renders `0`. `Other (1 customers)` becomes `Other (1 customer)` (`deal classes` -> `deal class`).
15. **Insights appended, prompt bumped.** `narrate` appends `## Insights` bullets to `llm_md` after the narrative and before `## Notes`; `narrate.xml` drops its Insights sentence and becomes `version="2"`.
16. **The multi-office overlay is a post-pass with its own seeded stream** (`Random(seed + seed_offset)`), rewriting `CUSTOMER_TEAM_NAME` and `PRIMARY_SUPPLY_TEAM_OFFICE` on a share of rows for a share of customers (named customers always). Customer totals, ports, suppliers and every other column are untouched, so the pinned per-customer goldens in `test_chat_e2e_scripted.py` hold.
17. **The contracts report scenarios pin non-deterministic values through module hooks**, not by normalising JSON: `skill._now`, `skill.uuid7`, `frames.monotonic` and `render.render_pdf` are monkeypatched to fixed values. The README lists them.
18. **No office-chip cap in A2.** Synthetic data has four offices; the certified office columns hold a handful of values on Snowflake. If PR B's live run shows more than a dozen, cap then.
19. **The dev router cannot route to `report_lookup`** (its skill ids are hard-coded, and it treats the word "report" as a brief cue). The lookup's router-decision case is a stub script; the pg flow test's follow-up avoids the word. Parked with the PR C router drift.
20. **Month-to-date is not reachable on the synthetic data from today's clock** (rows end 2026-06-30; the running month is October 2026, which has no rows, so the step answers `No data for October 2026.`). The mtd path is pinned offline with a fixed `reference_date`; the browser gate uses complete months.

## Review Focus

Inputs the spec implies but did not spell out, each pinned to a test in the task that owns the code:

1. **The office typed in a different case** (`rotterdam` for `Rotterdam`) resolves to the certified value; an unknown office re-asks with the chips and dispatches nothing. (Task 6 `test_office_step_matches_case_insensitively_and_reasks_unknown`.)
2. **A month outside the data, or the running month on its first day** re-asks with the reason, with no tool frames and no `report_run` row. (Task 6 `test_month_step_reasks_for_missing_month_and_mtd_on_day_one`.)
3. **The report turn fails** (reconciliation): an `error` frame, slots unchanged, still in report mode, and the next message is read as another month answer. (Task 6 `test_report_dispatch_failure_keeps_the_flow_at_the_month_step`.)
4. **A follow-up that names another office with `scope="office"`**: the carried office wins and the proof names it; with `scope="book"` no office filter is applied. (Task 4 `test_carried_office_replaces_a_model_filter_on_the_same_column` and `test_scope_book_drops_the_carried_office`.)
5. **A hostile office value in a file name** (`Rotterdam/"x" Office`) cannot break the artifact name or the `Content-Disposition` header. (Task 2 `test_artifact_name_is_sanitised`; Task 9 `serves the pdf with a sanitised filename`.)

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `backend/poseidon/reports/store.py` | `get_bytes` kind validation | 1 |
| `backend/poseidon/core/analytics/mom.py` | zero-display polish, `Other (1 customer)` | 1 |
| `.../monthly_performance/tools/context.py` | `ports_rest_of_book` drops flat ports | 1 |
| `.../monthly_performance/tools/render.py`, `backend/poseidon/config/templates/report.html.j2` | `KPI_LABELS` from `KPI_METRICS`, explicit ranked-table header, dead CSS and `data-run` removed | 1 |
| `backend/poseidon/core/data/query_builder.py` | months query applies the dual-purpose exclusion | 1 |
| `backend/tests/test_report_store.py`, `test_analytics_mom.py`, `test_query_builder_snapshots.py`; `.../monthly_performance/tests/test_render.py`, `test_skill.py` | pins for Task 1 | 1 |
| `backend/prompts/reporting/narrate.xml` (v2), `.../tools/narrate.py`, `.../skill.py`, `backend/poseidon/core/skills/context.py` | insights in `llm_md`; stored HTML carries the PDF flag; sanitised artifact name; `SkillContext.conversation_id` threaded to the run row; `_now` hook | 2 |
| `backend/tests/test_prompt_tree.py`, `.../tests/test_skill.py` | pins for Task 2 | 2 |
| `backend/poseidon/core/skills/context.py`, `backend/poseidon/core/chat/history.py` | four new slots, serialised | 3 |
| `backend/poseidon/core/llm/prompts.py` | the three state-block lines | 3 |
| `backend/poseidon/core/llm/loop.py` | internal-skill guard; `report` part summary for the model | 3 |
| `backend/tests/test_chat_history.py` (or the file that pins `slots_to_json`), `test_llm_prompts.py`, `test_llm_loop.py` | pins for Task 3 | 3 |
| `backend/poseidon/tasks/data_qa/skills/metric_query/schema.py`, `skill.py`, `tools/compare_breakdown.py` (new) | `scope`, stamping, breakdown with comparison | 4 |
| `.../metric_query/tests/test_skill.py`, `test_tools.py`; `backend/tests/routing_cases.yml`, `test_llm_loop.py` | pins and three router cases | 4 |
| `backend/poseidon/tasks/reporting/skills/report_lookup/{__init__,schema,skill}.py`, `tools/{__init__,sections}.py`, `tests/{__init__,test_lookup}.py` (new) | the lookup skill | 5 |
| `backend/tests/test_skill_registry.py`, `test_live_chat_sse.py`, `routing_cases.yml`, `test_llm_loop.py` | registry pins, `/api/skills`, the fourth router case | 5 |
| `backend/poseidon/core/chat/report_flow.py` (new) | pure step logic and copy | 6 |
| `backend/poseidon/core/chat/orchestrator.py`, `history.py`, `backend/poseidon/api/live_chat.py`, `app.py`, `internal.py`, `mock_chat.py` | the flow, the opener chip, store and prompt-tree threading | 6 |
| `contracts/turns/opener.json`, `web/src/lib/opener.ts` | the third opener chip (regenerated / transcribed) | 6 |
| `backend/tests/test_report_flow.py`, `test_report_orchestration.py` (new); `.../monthly_performance/tests/fake_client.py` (`FakeReportStore`) | flow tests | 6 |
| `backend/tests/contracts/scenarios.py`, `contracts/turns/report_entry.json`, `report_turn.json`, `contracts/turns/README.md` | the corpus report turns | 7 |
| `backend/poseidon/scripts/generate_synthetic.py`, `ontology/synthetic/profiles.yml` | the multi-office overlay | 8 |
| `backend/tests/test_synthetic_generator.py`, `.../monthly_performance/tests/test_skill_pg.py` | overlay test; goldens re-pinned | 8 |
| `web/src/lib/request-identity.ts`, `web/src/lib/report-files.ts` (new), `web/src/lib/reports.ts` (new), `web/src/app/api/reports/[runId]/serve.ts`, `pdf/route.ts`, `html/route.ts` (new) | the download routes | 9 |
| `web/src/lib/report-files.test.ts`, `web/src/app/api/reports/[runId]/route.test.ts` (new); `web/README.md` | route tests; README section | 9 |
| `web/src/ui/report/{types,ReportPart,KpiTiles,RankedTables,MoverCards,NewLostTables,Narrative,ProofBlock}.tsx|ts` (new), `web/src/ui/message-parts/registry.tsx` | the renderer | 10 |
| `web/src/ui/report/ReportPart.test.tsx` (new), `web/src/ui/message-parts/registry.test.tsx` | renderer tests | 10 |
| `backend/tests/test_report_flow_pg.py` (new) | the pg flow test over HTTP | 11 |
| `docs/architecture/00-overview.md`, `01-frontend.md`, `02-backend-skills.md`, `03-llm-routing.md`, `08-build-phases.md`, `CLAUDE.md` | decision rows D71–D74, docs, verified figures | 11 |

---

### Task 1: Engine polish from the A1 reviews (store, math, context, template, months query)

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Modify: `backend/poseidon/reports/store.py:173-180` (`get_bytes`)
- Modify: `backend/poseidon/core/analytics/mom.py:229-279` (`ranked`, `fmt_value`, `fmt_change`, `fmt_pct`)
- Modify: `backend/poseidon/tasks/reporting/skills/monthly_performance/tools/context.py:47-49` (`_items`)
- Modify: `backend/poseidon/tasks/reporting/skills/monthly_performance/tools/render.py`, `backend/poseidon/config/templates/report.html.j2:19-20, 38-39, 93, 99-103`
- Modify: `backend/poseidon/core/data/query_builder.py:569-595` (`build_months_query`)
- Test: `backend/tests/test_report_store.py`, `backend/tests/test_analytics_mom.py`, `backend/tests/test_query_builder_snapshots.py`, `.../monthly_performance/tests/test_render.py`, `.../monthly_performance/tests/test_skill.py`

**Interfaces:**
- Consumes: `ReportStore.get_bytes(user_sub, run_id, kind)`, `mom.KPI_METRICS`, `mom.ranked(merged, by, n, noun)`, `mom.fmt_value/fmt_change/fmt_pct`, `context._items`, `render.render_html(payload)`, `build_months_query(entity, filters, dialect, scope_value)`.
- Produces:
  ```python
  # reports/store.py
  # get_bytes raises ValueError(f"kind must be 'html' or 'pdf', got {kind!r}") BEFORE parsing the uuid.

  # core/analytics/mom.py
  def singular(noun: str) -> str          # "customers"->"customer", "deal classes"->"deal class", "ports"->"port"
  # ranked(): the Other row reads f"Other ({len(rest)} {noun if len(rest) != 1 else singular(noun)})"
  # fmt_value: a result of "-0", "-0.00" or "-0.0%" becomes "0", "0.00", "0.0%"
  # fmt_change: a result whose digits are all zero ("+0", "-0", "+0.00", "-0.00", "+0.0 pts", "-0.0 pts") returns "flat"
  # fmt_pct:    a result of "+0.0%" or "-0.0%" returns "flat"

  # tools/context.py
  # _items drops entries whose change == 0 before ranking (ports_rest_of_book never lists a flat port;
  # offsets are unchanged because they never contained one).

  # tools/render.py
  KPI_LABELS: dict[str, str] = {
      "VOLUME": "Volume (tons)", "GP": "Gross profit", "MARGIN": "Margin (per ton)",
      "NUM_WON": "# Won", "NUM_INQUIRIES": "# Inquiries", "NUM_LOST": "# Lost", "WIN_RATE": "Win rate",
  }
  # render_html passes kpi_tiles=[(m, KPI_LABELS[m]) for m in mom.KPI_METRICS] to the template;
  # the template iterates kpi_tiles instead of its own literal list.

  # report.html.j2
  # macro ranked(title, table, header) -- the third argument is the first-column header;
  # callers pass entity.label, entity.label, "Port", "Port", "Deal class". No title sniffing.
  # .pos/.neg CSS rules removed; data-run attribute removed from the ranked table.

  # core/data/query_builder.py
  # build_months_query appends entity_obj.dual_purpose_exclusion to conditions when the entity has one
  # (the GL entity), so a month that has only Volume rows is not listed. Both dialect snapshots pinned.
  ```

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_report_store.py` add (no database needed; put it outside the pg-marked tests, mirroring how the module skips):
```python
def test_get_bytes_rejects_an_unknown_kind_before_touching_the_uuid():
    store = ReportStore(engine=object())  # never used: the kind check comes first
    with pytest.raises(ValueError, match="kind must be 'html' or 'pdf'"):
        store.get_bytes("test|alice", "not-a-uuid", "txt")
```
In `backend/tests/test_analytics_mom.py` add:
```python
def test_singular_noun():
    assert mom.singular("customers") == "customer"
    assert mom.singular("suppliers") == "supplier"
    assert mom.singular("ports") == "port"
    assert mom.singular("deal classes") == "deal class"

def test_other_row_with_one_entity_reads_singular():
    # three merged rows ranked with n=2 leave exactly one in the Other row
    ...build three MergedRow via mom.merge_breakdowns as the file's existing fixtures do...
    assert mom.ranked(merged, "GP", 2, "customers").other.key == "Other (1 customer)"

def test_zero_displays_never_show_a_signed_zero():
    assert mom.fmt_value(-0.3, "money") == "0"
    assert mom.fmt_value(-0.001, "margin") == "0.00"
    assert mom.fmt_change(0.3, "money") == "flat"
    assert mom.fmt_change(-0.3, "money") == "flat"
    assert mom.fmt_change(-0.004, "margin") == "flat"
    assert mom.fmt_change(0.0004, "rate") == "flat"
    assert mom.fmt_pct(0.04, 0.3) == "flat"
    assert mom.fmt_pct(-0.04, -0.3) == "flat"
    assert mom.fmt_change(0.5, "money") == "+1"   # rounding still rounds; only a zero display is polished
```
In `.../monthly_performance/tests/test_skill.py` (offline fake client, `multi_office_rows`), add a test that gives one mover a port with identical GP in both months outside the office and asserts that port is absent from `ports_rest_of_book` (`test_ports_rest_of_book_never_lists_a_flat_port`). Build the rows by extending `multi_office_rows()` in the test body (do not change the fixture).
In `.../monthly_performance/tests/test_render.py` add:
```python
def test_kpi_tiles_follow_kpi_metrics():
    assert list(render.KPI_LABELS) == list(mom.KPI_METRICS)
    html = render.render_html(minimal_payload())
    assert html.index("Volume (tons)") < html.index("Gross profit") < html.index("Win rate")

def test_ranked_table_headers_are_explicit_and_dead_markup_is_gone():
    html = render.render_html(minimal_payload())
    assert "data-run=" not in html and ".pos {" not in html and ".neg {" not in html
    assert html.count("<th>Port</th>") == 2 and "<th>Deal class</th>" in html and "<th>Name</th>" not in html
```
(Check the exact `<th>` markup the `head` macro emits and match it; the assertion is on the rendered header text, not on a guessed attribute set.)
In `backend/tests/test_query_builder_snapshots.py` add two byte-pinned snapshots `test_months_query_applies_the_dual_purpose_exclusion_on_gl[postgres|snowflake]` for `W_MARINE_GL_SOURCE_AI` with no filters, and one `test_months_query_has_no_exclusion_on_sales` for `MARINE_SALES_PLANNING_V` asserting the SQL carries no `CLASS4`. Read the ontology's `dual_purpose_exclusion` text from `get_ontology().entity(...)` in the test rather than retyping it.

- [ ] **Step 2: Run the tests to verify they fail**

Run the path-targeted command over the five test files; expected: the new tests fail (`AttributeError: singular`, `KeyError` instead of `ValueError`, `AssertionError` on the strings), everything else passes. Save as `t1-red.txt`.

- [ ] **Step 3: Implement**

Follow the Interfaces block. Notes: `singular` strips `es` when the noun ends in `sses` or `ses` preceded by a consonant... keep it to the four nouns the engine uses: `if noun.endswith("classes"): return noun[:-2]`; `elif noun.endswith("s"): return noun[:-1]`; else return noun. For the zero polish, format first, then test the formatted string with `re.fullmatch(r"[+-]?0(?:[.,]0+)?(?:%| pts)?", s)`; `fmt_value` strips the sign, `fmt_change` and `fmt_pct` return `"flat"`. `render_html` already builds a Jinja environment; add `kpi_tiles` to the render kwargs. In `build_months_query`, add the exclusion right after `_filter_conditions` and before the scope condition, guarded by `if entity_obj.dual_purpose_exclusion:`.

- [ ] **Step 4: Run the tests to verify they pass; run the offline suite, contracts and ruff**

Save `t1-green.txt`, `t1-offline.txt`, `t1-contracts.txt`. `git status --short contracts/` must be empty.

- [ ] **Step 5: Prove one guard by breaking it**

Revert the `_items` zero filter, confirm `test_ports_rest_of_book_never_lists_a_flat_port` fails, restore. Report the failing line.

- [ ] **Step 6: Commit**

`fix(reporting): A1 review carry-forwards -- store kind check, zero displays, singular Other, flat ports, template drift pin, GL months exclusion`

---

### Task 2: Insights in the narrative, prompt v2, stored-HTML flag, sanitised artifact name, `conversation_id`

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Modify: `backend/prompts/reporting/narrate.xml` (line 2 `version="2"`, line 10)
- Modify: `.../monthly_performance/tools/narrate.py:138-162` (`_parse`)
- Modify: `.../monthly_performance/skill.py:214-215, 307-362`
- Modify: `backend/poseidon/core/skills/context.py:171-182` (append `conversation_id`)
- Test: `backend/tests/test_prompt_tree.py:21`, `.../monthly_performance/tests/test_skill.py`

**Interfaces:**
- Consumes: Task 1's `render_html`; `_Reply`, `Narrative`, `NARRATE_TOOL` as A1 defines them; `with_flag`.
- Produces:
  ```python
  # core/skills/context.py (appended after prompt_tree)
  conversation_id: str | None = None       # the chat conversation a dispatch belongs to; the report flow sets it

  # tools/narrate.py -- _parse builds
  llm_md = narrative_md
  if insights: llm_md += "\n\n## Insights\n" + "\n".join(f"- {i}" for i in insights)
  if notes:    llm_md += "\n\n## Notes\n" + "\n".join(f"- {n}" for n in notes)
  grounding_text = llm_md                  # insights are inside it now; no separate join

  # prompts/reporting/narrate.xml: version="2"; the sections sentence ends at "Account context" (...)
  # and the "Insights" clause is removed; a new sentence: "Return three to five insights through the
  # tool's insights field, each a single sentence carrying a number." Nothing else changes.

  # skill.py
  def _now() -> datetime                   # module hook beside _today(); run() uses it for generated_at
  def safe_filename(name: str) -> str      # ruling 13; public, imported by nothing else in A2
  # run(): html is re-rendered after with_flag when the PDF fails, so the stored HTML carries the
  # "PDF not rendered: <Type>" notice; the stored html is the re-rendered one.
  # run(): conversation_id=ctx.conversation_id on NewRun.
  # run(): ArtifactRef name = safe_filename(f"{scope_name} {period_name}") + ".pdf"
  ```

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_prompt_tree.py:21`: the pinned `PromptSpec` version becomes `"2"`; add `assert "Insights" not in spec.system.split("Account context")[1].split("Return three")[0]` so the sections sentence no longer asks for the section, and `assert "insights field" in spec.system`.

In `.../monthly_performance/tests/test_skill.py` (its `_tool_reply(narrative_md, questions, insights=None)` helper and the live-mode test exist):
```python
def test_model_insights_render_as_an_insights_section_in_llm_md(monkeypatch):
    # live mode with a well-formed reply carrying three insights that ground
    ... reuse the existing live-mode test's setup ...
    llm_md = payload["narrative"]["llm_md"]
    assert "\n\n## Insights\n- " in llm_md
    assert llm_md.index("## Insights") > llm_md.index("Overall summary") and (
        "## Notes" not in llm_md or llm_md.index("## Insights") < llm_md.index("## Notes"))

def test_stored_html_carries_the_pdf_not_rendered_flag(monkeypatch):
    monkeypatch.setattr(render, "render_pdf", lambda html: (_ for _ in ()).throw(OSError("no pango")))
    ... run ...
    html = store.runs[-1].html.decode()  # _FakeStore keeps the NewRun objects it received
    assert "PDF not rendered: OSError" in html

def test_artifact_name_is_sanitised(monkeypatch):
    # rows for an office named 'Rotterdam/"x" Office' (extend multi_office_rows in the test body)
    ... pdf monkeypatched to b"%PDF-fake" ...
    assert result.artifacts[0].name == "Rotterdam__x_ Office June 2026.pdf"
    assert payload["scope"]["office_value"] == 'Rotterdam/"x" Office'   # the payload is verbatim

def test_conversation_id_reaches_the_run_row():
    ctx = _ctx(...); ctx = dataclasses.replace(ctx, conversation_id="conv-42")
    ... run ...
    assert store.runs[-1].conversation_id == "conv-42"
    # and the default context still writes None
```
Also update any existing assertion on `grounding_text` or on the `Insights` wording of the prompt that the change breaks; the test that pins `_tool_reply` insights being grounding-checked must still pass (they are, inside `llm_md`).

- [ ] **Step 2: Run to verify they fail** (`t2-red.txt`). Expected: version pin fails on `"1"`, the four skill tests fail on their asserts.

- [ ] **Step 3: Implement** per the Interfaces block. `_FakeStore` in `test_skill.py` gains a `runs: list` if it does not keep the `NewRun` already.

- [ ] **Step 4: Run the five test files, then the offline suite, contracts, ruff** (`t2-green.txt`, `t2-offline.txt`, `t2-contracts.txt`).

- [ ] **Step 5: Prove the guard:** revert the HTML re-render after `with_flag`, confirm `test_stored_html_carries_the_pdf_not_rendered_flag` fails, restore.

- [ ] **Step 6: Commit**

`feat(reporting): insights section in the model narrative (prompt v2); stored HTML carries the PDF flag; sanitised artifact name; conversation_id on the run row`

---

### Task 3: Slots, the state block and the loop guard

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Modify: `backend/poseidon/core/skills/context.py:116-155` (`ConversationSlots`)
- Modify: `backend/poseidon/core/chat/history.py:836-875` (`slots_to_json`, `slots_from_json`)
- Modify: `backend/poseidon/core/llm/prompts.py:356-373` (`_render_slots`)
- Modify: `backend/poseidon/core/llm/loop.py:448-522` (`_dispatch_one`), `:578-603` (`_part_content`)
- Test: `backend/tests/test_history_store.py` (the `slots_to_json`/`slots_from_json` pins; `backend/tests/test_c03_period_carry.py` shows the D62 precedent for an added slot pair), `backend/tests/test_llm_prompts.py:457-700`, `backend/tests/test_llm_loop.py`

**Interfaces:**
- Consumes: `ConversationSlots`, `label_window`, `slot_window`, `SkillRegistry.get` (raises `KeyError`), `RegisteredSkill.internal`, `problem`, `SkillResult`.
- Produces:
  ```python
  # core/skills/context.py -- appended after period_b_end, in this order
  perspective: str | None = None        # customer | supplier | book
  office_column: str | None = None      # CUSTOMER_TEAM_NAME | PRIMARY_SUPPLY_TEAM_OFFICE
  office_value: str | None = None
  report_ref: str | None = None         # report_run.id of the last report in this conversation

  # history.py: the four keys serialised as plain strings or null; absent keys read back as None.

  # core/llm/prompts.py -- _render_slots, after the period B line and before Region:
  _REPORT_ENTITY_COLUMN = {"customer": "CUST_NM", "supplier": "SUPPLIER_NM", "book": "CUST_NM"}
  # perspective set, office_column None:
  #   "Carried perspective: book (ranks CUST_NM; no office column)"
  # perspective set, office_column set:
  #   f"Carried perspective: {p} (ranks {_REPORT_ENTITY_COLUMN[p]}; office column {office_column})"
  # office_column and office_value set:
  #   f'Carried office: {office_column} = {office_value}. data_qa.metric_query applies it when '
  #   f'scope="office" (the default). Use scope="book" for the whole book, the full account, other '
  #   f'locations or offices, or a share of the total.'
  # report_ref set:
  #   f"Report context: {office_value or 'Whole book'}, {label_a} vs {label_b}. Quote the report's own "
  #   f"figures only through reporting.report_lookup."
  #   where label_a/label_b are label_window(slot_window(period_a, period_a_end)) etc.; when
  #   period_a or period_b is None the line is f"Report context: {scope}. Quote the report's own ..."
  # These lines must not match dev_router.py's _CARRIED_CUSTOMER_RE / _CARRIED_PORT_RE (they do not:
  # both are anchored on their full label).

  # core/llm/loop.py
  # _dispatch_one: before registry.dispatch --
  #   try: internal = registry.get(call.name).internal
  #   except KeyError: internal = False
  #   result = (SkillResult(ok=False, error=problem(403, "internal skill",
  #             f"'{call.name}' is dispatched by a deterministic flow, never by the router"))
  #             if internal else registry.dispatch(call.name, dict(call.arguments), context))
  # tool_start/tool_done frames, the record and the toolResult block are unchanged in shape.
  # _part_content: kind == "report" ->
  #   [f"{tag} report: run {payload.get('run_id')}; {scope.label}; {current.label} vs {prior.label}; "
  #    "read sections through reporting.report_lookup"]   (never the JSON dump)
  ```

- [ ] **Step 1: Write the failing tests**

Slots, in `backend/tests/test_history_store.py` beside the existing `slots_to_json` pins: `test_slots_round_trip_carries_the_report_fields` (all four set; `slots_from_json(slots_to_json(s)) == s` and the JSON carries the four keys as strings) and `test_slots_from_json_without_report_keys_reads_none` (a pre-A2 dict without the keys reads back with the four fields `None`).

Prompts, in `backend/tests/test_llm_prompts.py` next to the existing goldens: `test_render_state_block_report_lines_golden` with slots `ConversationSlots(mode="default", period_a=date(2026,6,1), period_a_end=date(2026,7,1), period_b=date(2026,5,1), period_b_end=date(2026,6,1), perspective="supplier", office_column="PRIMARY_SUPPLY_TEAM_OFFICE", office_value="MED SUPPLY OFFICE", report_ref="run-1")` asserting the exact multi-line golden (Mode, period A, period B, the three report lines, in that order); `test_render_state_block_book_report_has_no_office_line` (perspective `book`, no office) asserting the `book` perspective line and no `Carried office:`; and extend the dev-router collision check: `assert dev_router._CARRIED_CUSTOMER_RE.search(rendered) is None and dev_router._CARRIED_PORT_RE.search(rendered) is None` on the supplier golden.

Loop, in `backend/tests/test_llm_loop.py`: `test_a_stub_router_naming_an_internal_skill_is_refused_without_dispatch` using the file's `_run` with a script whose tool call names `reporting.monthly_performance` (any arguments), asserting `result.tool_records[0].status == "error"`, the sink's `tool_done` problem title is `"internal skill"`, and a spy on `registry.dispatch` (wrap `REGISTRY` in a subclass or monkeypatch the method) was never called; and `test_report_part_content_is_a_one_line_summary` calling `_part_content(1, {"kind": "report", "payload": minimal_payload()})` and asserting one line containing `run-test-0001`, `Rotterdam (Customer Broker Office)` and `September 2026 vs August 2026`, with `"kpis"` absent.

- [ ] **Step 2: Run to verify they fail** (`t3-red.txt`).

- [ ] **Step 3: Implement** per the Interfaces block.

- [ ] **Step 4: Run the three test files, the offline suite, contracts, ruff** (`t3-green.txt`, `t3-offline.txt`, `t3-contracts.txt`). The dev-router goldens in `tests/test_chat_state_devrouter.py` must be untouched (no report slots there).

- [ ] **Step 5: Prove the guard:** revert the `internal` check in `_dispatch_one`, confirm the refusal test fails (the skill is dispatched and returns a 500 `report store unavailable`), restore.

- [ ] **Step 6: Commit**

`feat(chat): report slots, state-block lines for the carried perspective/office/report, loop guard for internal skills`

---

### Task 4: `metric_query` office scope and breakdown with comparison

**Model:** Opus implements; Opus reviews.

**Files:**
- Modify: `backend/poseidon/tasks/data_qa/skills/metric_query/schema.py:17-57`
- Modify: `backend/poseidon/tasks/data_qa/skills/metric_query/skill.py:92-160`
- Create: `backend/poseidon/tasks/data_qa/skills/metric_query/tools/compare_breakdown.py`
- Modify: `backend/tests/routing_cases.yml`, `backend/tests/test_llm_loop.py:1478-1620` (`DOC_CASE_IDS`, the live/offline count tests, `_slots_for`)
- Test: `.../metric_query/tests/test_skill.py`, `.../metric_query/tests/test_tools.py:185`

**Interfaces:**
- Consumes: `BreakdownQuerySpec`, `ctx.data.run_breakdown_query`, `format_parts._header_proof/_round/_friendly_and_unit/_dimension_friendly/table_part`, `mom.merge_breakdowns`, `mom.driver`, Task 3's slots.
- Produces:
  ```python
  # schema.py
  scope: Literal["office", "book"] = Field(default="office", description=(
      'Office scope. "office" (default) applies the conversation\'s carried office filter when one is '
      'set; "book" runs over the whole book: use it for other offices or locations, the full account, '
      'or a share of the total.'))
  # _reject_breakdown_with_compare removed; the two descriptions no longer say "Cannot be combined".
  # compare_period description: "Second window for side-by-side comparison. With group_by, both
  # windows run and the result is one comparison table per metric."

  # skill.py -- run()
  def _stamp_office(spec, state) -> spec     # replace(spec, filters={**spec.filters, col: (value,)})
  # applied to every spec when args.scope == "office" and state.office_column and state.office_value
  # are set; proof gains, as the LAST line:
  #   f"Office scope: {col} = {value} (carried; scope=office)"     when stamped
  #   "Office scope: book (carried office not applied)"            when carried but scope == "book"
  #   (no line when no office is carried)
  # group_by + compare_period: two BreakdownQuerySpec (period, compare_period) with top_n=50,
  # both scoped and stamped, then compare_breakdown_parts(...).

  # tools/compare_breakdown.py
  def compare_breakdown_parts(
      spec_current: BreakdownQuerySpec, result_current: BreakdownResult,
      spec_prior: BreakdownQuerySpec, result_prior: BreakdownResult,
      settings: Settings, top_n: int,
  ) -> tuple[list[dict], list[str]]
  # One table part per metric in spec_current.metrics, columns
  #   [dimension friendly, f"{friendly} prior", f"{friendly} current", "Change", "%"] (+ "Driver")
  # rows: [key, _round(m, prior), _round(m, current), _round(m, change), pct] where a missing side
  # is 0.0, pct = "new" when prior == 0 else round(change / abs(prior) * 100, 1).
  # Row order: by abs(change of metrics[0]) desc then key asc, capped at top_n; identical in every table.
  # Driver (present when {"GP","VOLUME"} <= set(metrics)): mom.driver over mom.merge_breakdowns(
  #   {key: {"GP":..,"VOLUME":..}} prior, current) -- the A1 rule, not a reimplementation.
  # Empty on both sides: [text_part(NO_DATA_TEXT)], proof + [EMPTY_RESULT_PROOF].
  # Proof: _header_proof(spec_current, settings, compare_period) + [f"Rows: {n} (merged over the top 50 per window)"].
  ```

- [ ] **Step 1: Write the failing tests**

In `.../metric_query/tests/test_tools.py:185` the 422 pin flips: `Args(... group_by=..., compare_period=...)` validates; assert `args.scope == "office"` by default and that `Args(scope="book")` validates. Add `test_compare_breakdown_parts_shape_and_driver` with hand-built `BreakdownResult`s (prior: A 100/10, B 200/20; current: A 150/10, C 50/5) for metrics `["GP","VOLUME"]`, asserting: two table parts; columns of the first `["Customer", "Gross Profit prior", "Gross Profit current", "Change", "%", "Driver"]` (use the real friendly names the module produces for `CUST_NM`/GP); rows ordered B (-200), A (+50), C (+50) with ties by key; C's pct is `"new"`; B's driver is `"lost"`, A's `"margin-driven"`, C's `"new"`; the second table lists the same keys in the same order; proof ends with `Rows: 3 (merged over the top 50 per window)`.

In `.../metric_query/tests/test_skill.py` (use its existing fake data client and `_ctx` helpers, extending the fake with `run_breakdown_query` call recording if needed):
- `test_carried_office_is_stamped_on_every_spec`: slots carry `CUSTOMER_TEAM_NAME = Rotterdam`; a plain metric query, a compare query and a breakdown each produce specs whose `filters["CUSTOMER_TEAM_NAME"] == ("Rotterdam",)`; proof's last line is the stamped line.
- `test_carried_office_replaces_a_model_filter_on_the_same_column`: the model passes `filters=[{"column": "CUSTOMER_TEAM_NAME", "values": ["Houston"]}]` with the default scope; the spec carries `("Rotterdam",)`.
- `test_scope_book_drops_the_carried_office`: `scope="book"`; no office filter; proof's last line is the book line.
- `test_no_carried_office_means_no_scope_line`.
- `test_group_by_with_compare_period_runs_both_windows_and_returns_comparison_tables`: assert two breakdown calls with `top_n == 50`, parts come from `compare_breakdown_parts`, and `_repopulate_pass_through`-compatible shape (first table's first column holds keys).

Routing cases: append three cases to `backend/tests/routing_cases.yml` with `stub_script`s (each one `tool_use` step then one `end_turn`), ids `report_followup_why_gp_dropped` (setup `mode: default`, `state: {perspective: customer, office_column: CUSTOMER_TEAM_NAME, office_value: Rotterdam, report_ref: run-1}`; user "why did GP drop for Northstar Lines"; expect skill `data_qa.metric_query`, args_subset `{customer: "Northstar*", group_by: LOC_NM}` and a non-null `compare_period`), `report_followup_where_else` (user "where else does Northstar Lines lift fuel"; expect `data_qa.metric_query` with `scope: book`), `supplier_report_break_down_by_customer` (state with `perspective: supplier`, `pass_through: [[SUPPLIER_NM, Vitol]]`; user "break that down by customer"; expect `group_by: CUST_NM` and a `SUPPLIER_NM` filter containing `Vitol`). Extend `_slots_for` to read `perspective`, `office_column`, `office_value`, `report_ref` and a `pass_through` list of pairs from `setup.state`; extend `assert_args_subset`/`_candidate_values` so `group_by`, `scope`, `compare_period` and a `supplier` key (`SUPPLIER_NM`) are checkable. Update `DOC_CASE_IDS` (append the three ids) and the "exactly two live-only / three offline" test to "two live-only / six offline". The stub cases run through the real registry, so the metric_query dispatch must succeed against `test_llm_loop.py`'s data client; check its `run_breakdown_query` returns rows for `LOC_NM`, and add rows if it returns none.

- [ ] **Step 2: Run to verify they fail** (`t4-red.txt`): the targets are `.../metric_query/tests`, `tests/test_llm_loop.py`.

- [ ] **Step 3: Implement** per the Interfaces block. Keep `format_parts.py` untouched except for importing nothing new; the comparison lives in its own module and reuses `format_parts`' helpers by import.

- [ ] **Step 4: Run the targets, the offline suite, contracts, ruff** (`t4-green.txt`, `t4-offline.txt`, `t4-contracts.txt`).

- [ ] **Step 5: Prove the guard:** revert the stamp (make `_stamp_office` return the spec unchanged), confirm `test_carried_office_is_stamped_on_every_spec` and `test_carried_office_replaces_a_model_filter_on_the_same_column` fail, restore.

- [ ] **Step 6: Commit**

`feat(metric_query): office scope stamping from the carried office; breakdown with comparison; three report follow-up router cases`

---

### Task 5: `reporting.report_lookup`

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Create: `backend/poseidon/tasks/reporting/skills/report_lookup/__init__.py`, `schema.py`, `skill.py`, `tools/__init__.py`, `tools/sections.py`, `tests/__init__.py`, `tests/test_lookup.py`
- Modify: `backend/tests/test_skill_registry.py:199-216` (the id lists), `backend/tests/test_live_chat_sse.py:881-915` (`/api/skills`), `backend/tests/routing_cases.yml`, `backend/tests/test_llm_loop.py` (`DOC_CASE_IDS`, counts, `_context` wiring a fake store)

**Interfaces:**
- Consumes: `ctx.state.report_ref`, `ctx.report_store.get_run(user_sub, run_id) -> ReportRun | None` (`.payload`), `ctx.user.sub`, `text_part`, `table_part`, `metric_grid_part`, `SkillResult`, `minimal_payload()` (tests).
- Produces:
  ```python
  # schema.py
  SECTIONS = ("kpis", "entities_by_gp", "entities_by_volume", "ports_by_gp", "ports_by_volume",
              "deal_class", "movers", "new_lost", "narrative", "context")
  class Args(BaseModel):
      section: Literal[SECTIONS...]
      limit: int = Field(default=10, ge=1, le=50)
      offset: int = Field(default=0, ge=0)
  SKILL_META = {
      "description": ("Read a section of the monthly report open in this conversation: KPIs, the five "
                      "ranked tables, movers, new and lost entities, the narrative or the entity "
                      "context. Use it for any question about the report's own figures. Runs no query."),
      "examples": ["what were the top ports in the report", "show me the report's movers"],
  }
  # (description <= 300 chars; no "internal" key: router-visible)

  # skill.py
  NO_REPORT_TEXT = "Open a report first: click Monthly report, then pick a perspective, an office and a month."
  GONE_TEXT = "The report this conversation refers to is no longer available."
  def run(ctx, args) -> SkillResult
  #   report_ref None -> SkillResult(ok=True, parts=[text_part(NO_REPORT_TEXT)], proof=["Report: none open"])
  #   store None      -> 500 "report store unavailable"
  #   get_run None    -> SkillResult(ok=True, parts=[text_part(GONE_TEXT)], proof=[f"Report: run {ref} not found"])
  #   else parts, proof = sections.render(args.section, payload, args.limit, args.offset)

  # tools/sections.py
  KPI_FRIENDLY = {"VOLUME": ("Volume", "tons"), "GP": ("Gross Profit", "USD"), "MARGIN": ("Margin", "USD/t"),
                  "NUM_WON": ("# Won", None), "NUM_INQUIRIES": ("# Inquiries", None),
                  "NUM_LOST": ("# Lost", None), "WIN_RATE": ("Win Rate", None)}
  def render(section, payload, limit, offset) -> tuple[list[dict], list[str]]
  # proof[0] = f"Report: run {run_id} - {scope.label} - {periods.current.label} vs {periods.prior.label}"
  # kpis     -> metric_grid_part(periods={"a": prior start/end, "b": current start/end},
  #             metrics=[{name, friendly, a: prior, b: current, unit} for m in payload["kpis"]]) ; proof "Section: kpis"
  # tables   -> table_part(columns=[<entity.label | "Port" | "Deal class">, "GP prior", "GP current", "GP change",
  #             "GP %", "Volume prior", "Volume current", "Volume change", "Volume %", "Margin prior",
  #             "Margin current", "Margin change", "Margin %"], rows=display strings) over the list
  #             rows + ([other] if other) + [footer], paged [offset:offset+limit];
  #             proof f"Section: {section} rows {offset+1}-{offset+len(page)} of {total}";
  #             when offset+len(page) < total append text_part(f"{total-offset-len(page)} rows remaining; ask again with offset={offset+len(page)}.")
  # movers   -> table [entity label, "Direction", "Driver", "GP prior", "GP current", "GP change", "GP %"],
  #             increases ("up") then decreases ("down"), paged the same way
  # new_lost -> table [entity label, "Status", "GP", "Volume", "Margin"]: new rows use current, lost rows prior
  # narrative-> text_part(llm_md if grounded and llm_md else template_md); proof adds f"Flags: {'; '.join(flags) or 'none'}"
  # context  -> entity_context None: text_part("A whole-book report has no entity context.")
  #             else table [entity label, "Book GP prior", "Book GP current", "Book change", "Share prior",
  #             "Share current", "Office change", "Other offices net", "Offsets", "Ports (rest of book)"]
  #             with offsets/ports as "Key display; Key display" strings, paged.
  ```

- [ ] **Step 1: Write the failing tests**

`tests/test_lookup.py` (offline): a `_FakeStore` with `get_run` returning `SimpleNamespace(payload=minimal_payload())` and a `_ctx(slots, store)`. Tests: `test_no_report_ref_answers_open_a_report_first` (ok, the text, proof), `test_missing_run_answers_gone`, `test_kpis_is_a_metric_grid_with_prior_as_a`, `test_entities_by_gp_pages_rows_and_reports_remaining` (limit=1 over the fixture's 2 rows + footer: first page one row, remaining text names `offset=1`; last page has no remaining text), `test_movers_direction_and_driver`, `test_new_lost_uses_current_for_new_and_prior_for_lost`, `test_narrative_prefers_a_grounded_model_narrative` (grounded True with llm_md set -> llm_md; grounded False -> template_md), `test_context_for_a_book_report_is_a_sentence`, `test_context_table_joins_offsets_and_ports`, and `test_no_data_client_call_is_ever_made` (the fake data client records calls; assert none).

Registry pins: `test_skill_registry.py` id lists gain `reporting.report_lookup` (sorted after `reporting.monthly_performance`; router list gets it after `data_qa.metric_query`); `test_schema_dispatch_parity`'s internal set is unchanged. `/api/skills` golden gains the fifth entry `{"id": "reporting.report_lookup", "label": "Report lookup", "description": <the SKILL_META description>}` between data_qa and research. `test_llm_prompts.py`'s "every router skill in the prompt" test is dynamic and needs no edit.

Routing case: append `report_lookup_top_ports` to `routing_cases.yml` (state with `report_ref: run-1`, user "what were the top ports in the report", expect `reporting.report_lookup` with `section: ports_by_gp`), update `DOC_CASE_IDS` and the counts (two live-only / seven offline). The stub case dispatches the real skill, so `test_llm_loop.py`'s `_context` must carry a `report_store` whose `get_run` returns the `minimal_payload()` run when `slots.report_ref` is set; add that as a small fake in the test module.

- [ ] **Step 2: Run to verify they fail** (`t5-red.txt`): targets `tasks/reporting/skills/report_lookup/tests`, `tests/test_skill_registry.py`, `tests/test_llm_loop.py` (the sse test is pg-marked; it runs in Task 6).

- [ ] **Step 3: Implement** per the Interfaces block. `task.yml` already registers the `reporting` task; the new skill directory is discovered by the registry's convention.

- [ ] **Step 4: Run the targets, the offline suite, ruff; then regenerate the one golden this task changes** (`t5-green.txt`, `t5-offline.txt`). `contracts/turns/error_mid_turn.json` carries the unknown-skill text `registered: customer_insight.existing_customer_brief, customer_insight.new_prospect_brief, data_qa.metric_query, research.web_research` twice; with the new router-visible skill that list gains `reporting.report_lookup` between `data_qa.metric_query` and `research.web_research`. Run `POSEIDON_UPDATE_CONTRACTS=1 env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/contracts`, then `git diff --stat contracts/` must list exactly `error_mid_turn.json`, and `git diff contracts/` must show only those two `registered:` lines changing. Paste that diff into the task report; anything else in the diff means STOP. Run `tests/contracts` once more without the flag (`t5-contracts.txt`) and `cd web && npm test` (`t5-web.txt`: the translator replays the regenerated file).

- [ ] **Step 5: Commit** (the regenerated golden in the same commit, per `contracts/turns/README.md`)

`feat(reporting): report_lookup, router-visible, reads the open run's payload section by section; error_mid_turn golden lists it`

---

### Task 6: The report flow (entry, three steps, the report turn, the opener chip, store wiring)

**Model:** Opus implements; Opus reviews.

**Files:**
- Create: `backend/poseidon/core/chat/report_flow.py`
- Modify: `backend/poseidon/core/chat/orchestrator.py:340-373, 445-465, 546-586, 1162-1224` (+ new `_finish_report_step`)
- Modify: `backend/poseidon/core/chat/history.py:335-361` (`_build_opener_parts`), `backend/poseidon/api/mock_chat.py:58-59`
- Modify: `backend/poseidon/api/live_chat.py:1005-1050`, `backend/poseidon/api/app.py` (`_wire_live_chat`, after line 576), `backend/poseidon/api/internal.py:160-175`
- Modify: `contracts/turns/opener.json` (regenerated), `web/src/lib/opener.ts`
- Modify: `.../monthly_performance/tests/fake_client.py` (`FakeReportStore` moved from `test_skill.py`'s `_FakeStore`, with `runs`, `get_run`, `get_bytes`), `.../monthly_performance/tests/test_skill.py` (imports `FakeReportStore`; no test changes)
- Test: `backend/tests/test_report_flow.py`, `backend/tests/test_report_orchestration.py` (new), `backend/tests/test_entry_orchestration.py` (opener pins), `backend/tests/test_live_chat_sse.py` (pg: opener shape vs mock)

**Interfaces:**
- Consumes: Tasks 2–3 (`SkillContext.conversation_id`, the slots), `ReportStore`, `PromptTree`, `parse_periods(text, slots, reference_date, available)`, `DataClient.list_months/list_dimension_values/available_periods`, `period_labels.month_window/label_window/window_kind`, `_begin_turn`, `_append_records`, `_touch_outbox`, `_capped`, `tool_result_digest`, `ToolRecord`, `TurnResult`, the A1 skill's `Args` and payload.
- Produces:
  ```python
  # core/chat/report_flow.py (pure; imports core.parsing, core.skills.context, core.data.client only)
  ENTITY = "MARINE_SALES_PLANNING_V"
  MODE_REPORT = "report"
  ENTRY_PHRASE_REPORT = "start a monthly report"
  REPORT_SKILL_ID = "reporting.monthly_performance"
  OFFICE_COLUMN = {"customer": "CUSTOMER_TEAM_NAME", "supplier": "PRIMARY_SUPPLY_TEAM_OFFICE", "book": None}
  ENTITY_COLUMN = {"customer": "CUST_NM", "supplier": "SUPPLIER_NM", "book": "CUST_NM"}
  PERSPECTIVE_PROMPT = "Monthly performance report. Which perspective?"
  PERSPECTIVE_REASK = "Pick a perspective: Customer office, Supplier office or Whole book."
  PERSPECTIVE_CHIPS = [{"id": "customer", "label": "Customer office", "send_text": "customer office report"},
                       {"id": "supplier", "label": "Supplier office", "send_text": "supplier office report"},
                       {"id": "book", "label": "Whole book", "send_text": "whole book report"}]
  OFFICE_PROMPT = {"customer": "Which customer office?", "supplier": "Which supply office?"}
  OFFICE_REASK = "I don't recognise that office. Pick one:"
  MONTH_PROMPT = "Which month? Pick one or type it, for example 'June 2026' or 'month to date'."
  MONTH_NOT_COMPLETE = "That month is not complete yet."
  MONTH_NO_DATA = "No data for {label}."
  MONTH_UNREADABLE = "I couldn't read that as a month. Pick one or type it like 'June 2026'."
  MTD_DAY_ONE = "No complete days in this month yet."
  MTD_RE = re.compile(r"\b(month to date|mtd|so far this month|this month so far)\b")

  def flow_slots_reset(slots) -> ConversationSlots    # mode=report, perspective/office_column/office_value/report_ref=None
  def step_for(slots) -> Literal["perspective", "office", "month"]
  def match_perspective(text) -> str | None           # casefolded exact: the three send_texts, "customer", "supplier",
                                                      # "book", "whole book", "customer office", "supplier office"
  def match_office(text, values: Sequence[str]) -> str | None   # casefolded exact against certified values
  @dataclass(frozen=True)
  class MonthChoice: month: date; window_kind: str    # "complete" | "mtd"
  def resolve_month(text, data, reference_date) -> MonthChoice | str   # a str is the re-ask reason (ruling 2)
  def month_chips(data, reference_date) -> list[dict] # last three complete months present, newest first;
                                                      # {"id": iso first day, "label": "June 2026 (latest)" | "May 2026", "send_text": "June 2026"}
  def office_chips(data, office_column) -> list[dict] # {"id": v, "label": v, "send_text": v} over list_dimension_values(ENTITY, office_column)
  def step_parts(step, slots, data, reference_date, reask: str | None = None) -> list[dict]
                                                      # [text part, chips part]; text is the prompt or the reask
  def report_arguments(slots, choice) -> dict         # {"perspective", "office_value", "month": iso, "window_kind"}
  def slots_after_report(slots, payload) -> ConversationSlots
                                                      # mode="default", periods from payload["periods"] (ISO -> date),
                                                      # report_ref=payload["run_id"], pass_through=((ENTITY_COLUMN[p], row["key"]) ...)
                                                      # over payload["tables"]["entities_by_gp"]["rows"][:10]
  def answer_summary(payload) -> str                  # f"Report generated: {scope.label}, {current.label} vs {prior.label}."

  # orchestrator.py
  #   _ENTRY_MODE_BY_PHRASE gains ENTRY_PHRASE_REPORT -> MODE_REPORT (imported from report_flow; the
  #   report_flow module is the one place the copy lives). ENTRY_PHRASE_REPORT re-exported in __all__.
  #   _finish_entry: when entry_mode == MODE_REPORT, new_slots = flow_slots_reset(prior_slots) and the
  #   parts are step_parts("perspective", ...) (text then chips); brief_done untouched for this mode.
  #   execute_turn: a third pre-parse branch, after the subject branch:
  #     if prior_slots.mode == MODE_REPORT: return _finish_report_step(...)
  #   execute_turn gains keyword parameters report_store: object | None = None, prompt_tree: object | None = None
  #   (appended after outbox_store).
  #   _finish_report_step(...): _begin_turn(mode=MODE_REPORT, parsed_loggable={"report_step": step, "text": text});
  #     perspective step: match -> replace(slots, perspective=p, office_column=OFFICE_COLUMN[p]) then the next
  #       step's parts (office, or month for book) as a clarify turn with state.put; no match -> same step re-asked, no put.
  #     office step: match_office over office_chips' values -> replace(slots, office_value=v), month parts, put;
  #       no match -> office re-ask with chips, no put.
  #     month step: resolve_month -> str: month re-ask (reason text, then chips), no put;
  #       MonthChoice: the report turn below.
  #   The report turn mirrors _finish_subject_turn's dispatch block exactly (SkillContext with
  #     report_store, prompt_tree, conversation_id=conversation_id, llm=role_client, user, tools, artifacts,
  #     emit_part=sink.part_emitter(1); tool_start; registry.dispatch(REPORT_SKILL_ID, report_arguments(...), ctx);
  #     tool_done with parts/proof/artifacts/problem; ToolRecord; fake TurnResult; _append_records).
  #     ok: sink.push_part("chips", {"options": [{"id": f"q{i+1}", "label": q, "send_text": q} for i, q in
  #         enumerate(payload["suggested_questions"])]}) only when non-empty; sink.done; finalize ok with
  #         answer_summary(payload); state.put(slots_after_report(prior_slots, payload)); _touch_outbox.
  #     error: turn_error, finalize error, no put (still mode report at the month step).
  #   A clarify in this flow finalizes status="clarify" with answer_summary=_capped(text part).

  # history._build_opener_parts: third chip {"id": "monthly_report", "label": "Monthly report", "send_text": ENTRY_PHRASE_REPORT}
  # mock_chat.create_conversation: third chip {"id": "monthly_report", "label": "Monthly report"} (its style)
  # web/src/lib/opener.ts: the same third chip with send_text "start a monthly report"
  # app.py _wire_live_chat: app.state.report_store = ReportStore(engine, app_role=settings.database_app_role);
  #   app.state.prompt_tree = PromptTree()
  # live_chat.py: report_store=getattr(app_state, "report_store", None), prompt_tree=getattr(app_state, "prompt_tree", None)
  # internal.py SkillContext: report_store=getattr(request.app.state, "report_store", None), prompt_tree=..., conversation_id=None
  ```
  Wire-order contract for the report turn, pinned in tests: `accepted, tool(start), tool(done), part report, part proof, [part artifact], part chips, done`.

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_report_flow.py` (pure functions, the reporting `FakeDataClient` from `fake_client.py` with `reference_date = date(2026, 7, 15)`): `test_step_for`, `test_match_perspective_variants_and_rejects_noise`, `test_match_office_is_case_insensitive`, `test_month_chips_last_three_complete_months_newest_first_latest_marked` (expects `June 2026 (latest)`, `May 2026`, `April 2026`, send_texts without the suffix), `test_resolve_month_reads_a_typed_month_and_the_chip_text`, `test_resolve_month_mtd_phrases_and_running_month_by_name` (reference 2026-06-15 with June rows -> `MonthChoice(2026-06-01, "mtd")` for "month to date", "mtd", "June 2026" and "june"), `test_resolve_month_reasks` (future month -> MONTH_NOT_COMPLETE; a month absent from the data -> `No data for January 2026.`; "banana" -> MONTH_UNREADABLE; day one -> MTD_DAY_ONE), `test_slots_after_report_seeds_periods_ref_and_pass_through` over `minimal_payload()` (period_a 2026-09-01..2026-10-01, period_b 2026-08-01..2026-09-01, mode default, pass_through `(("CUST_NM", "Meridian Bunkering"), ("CUST_NM", "Northstar Lines"))`), `test_flow_slots_reset_clears_the_four_fields_and_keeps_the_rest`.

`backend/tests/test_report_orchestration.py` (turn by turn through `execute_turn`, modelled on `test_entry_orchestration.py`'s `_run`, `_sink`, `_parse_frame`, with `data=FakeDataClient()` from the reporting fake, `report_store=FakeReportStore()`, `prompt_tree=PromptTree()`, stub settings, `reference_date=date(2026, 7, 15)`, `render.render_pdf` monkeypatched to `b"%PDF-fake"`):
- `test_entry_resets_the_flow_and_asks_the_perspective_with_chips`: frames `accepted, part(text PERSPECTIVE_PROMPT), part(chips == PERSPECTIVE_CHIPS), done`; slots mode `report`; finalize `clarify`.
- `test_customer_perspective_asks_the_office_with_certified_chips`: after "customer office report", chips are the fake's sorted `CUSTOMER_TEAM_NAME` values; slots perspective/office_column set.
- `test_book_perspective_skips_the_office_step`.
- `test_office_step_matches_case_insensitively_and_reasks_unknown` (Review Focus 1): "gibraltar office" advances to the month step; "Atlantis" re-asks with OFFICE_REASK and the chips; no tool frames; slots unchanged on the re-ask.
- `test_month_step_reasks_for_missing_month_and_mtd_on_day_one` (Review Focus 2): "January 2026" -> `No data for January 2026.`; with `reference_date=date(2026, 6, 1)`, "month to date" -> MTD_DAY_ONE; no tool frames; `store.runs == []`.
- `test_month_step_dispatches_the_report_and_seeds_the_slots`: "June 2026" -> the wire order above; the `report` part payload has `scope.office_value == "Gibraltar Office"`; the artifact url is `/api/reports/{run_id}/pdf`; the chips carry the three suggested questions verbatim as `send_text`; slots after: mode `default`, report_ref == run_id, period_a == 2026-06-01, office carried; `store.runs[-1].conversation_id == conversation_id`; finalize `ok` with `Report generated: Gibraltar Office (Customer Broker Office), June 2026 vs May 2026.`
- `test_month_to_date_dispatches_with_window_kind_mtd` (reference 2026-06-15): arguments recorded on the tool frame carry `window_kind == "mtd"` and `month == "2026-06-01"`.
- `test_report_dispatch_failure_keeps_the_flow_at_the_month_step` (Review Focus 3): `FakeDataClient(tamper_total=1.0)` -> `error` frame with title `reconciliation failed`, slots unchanged (mode report, no report_ref), a following "June 2026" with the tamper removed succeeds.
- `test_second_entry_click_starts_over`: after a finished report, the entry phrase resets the four fields and asks the perspective again.
- `test_a_question_after_the_report_routes_with_the_carried_office`: a fifth turn "GP for June 2026" through the dev router in stub mode reaches `data_qa.metric_query` and its proof's last line is `Office scope: CUSTOMER_TEAM_NAME = Gibraltar Office (carried; scope=office)` (the dev router's `_parse_state` ignores the new lines; the stamp comes from the skill).

Opener: `test_entry_orchestration.py`'s opener pin (and `history._build_opener_parts` consumers) gain the third chip; `web/src/lib/opener.test.ts` passes once `opener.ts` and `opener.json` both carry it.

- [ ] **Step 2: Run to verify they fail** (`t6-red.txt`): targets `tests/test_report_flow.py`, `tests/test_report_orchestration.py`, `tests/test_entry_orchestration.py`, `tests/contracts`.

- [ ] **Step 3: Implement** per the Interfaces block. Then regenerate the opener golden only: `POSEIDON_UPDATE_CONTRACTS=1 ... tests/contracts` and confirm `git status --short contracts/` shows exactly `contracts/turns/opener.json` (and, only if Task 5 stopped on it, `error_mid_turn.json`); transcribe the chip into `web/src/lib/opener.ts`.

- [ ] **Step 4: Run the targets, the offline suite, contracts, ruff; `cd web && npm test`; then the pg suite** (`t6-green.txt`, `t6-offline.txt`, `t6-contracts.txt`, `t6-web.txt`, `t6-pg.txt`). The pg suite covers `test_live_chat_sse.py`'s opener-vs-mock and `/api/skills` pins and `test_chat_e2e_scripted.py`.

- [ ] **Step 5: Prove two guards:** (a) make `resolve_month` return a `MonthChoice` for an absent month, confirm `test_month_step_reasks_for_missing_month_and_mtd_on_day_one` fails on `store.runs == []`, restore; (b) remove `conversation_id=conversation_id` from the report turn's `SkillContext`, confirm the seeds test fails, restore.

- [ ] **Step 6: Commit**

`feat(chat): the monthly report flow -- opener chip, perspective/office/month steps, the report turn, carried slots; report store wired into the app`

---

### Task 7: Contracts corpus report turns

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Modify: `backend/tests/contracts/scenarios.py:407-413` (+ two scenario functions)
- Create (generated): `contracts/turns/report_entry.json`, `contracts/turns/report_turn.json`
- Modify: `contracts/turns/README.md`
- Test: `backend/tests/contracts/test_turn_contracts.py` (unchanged harness), `web/src/lib/turn-contracts.test.ts` (replays every file; no edit expected)

**Interfaces:**
- Consumes: Task 6's flow and `FakeReportStore`; `skill._now`, `skill.uuid7`, `skill._today`, `frames.monotonic`, `render.render_pdf`.
- Produces: two scenarios registered in `SCENARIOS` as `"report_entry"` and `"report_turn"`:
  ```python
  # report_entry: one execute_turn with text ENTRY_PHRASE_REPORT on a fresh ConversationStateStore,
  #   turn-1/msg-1, stub settings -> frames accepted, part text, part chips, done.
  # report_turn: three preparatory turns (entry, "customer office report", "Gibraltar Office") on
  #   throwaway sinks, then the recorded fourth turn "June 2026" with turn-1/msg-1 and
  #   reference_date=date(2026, 7, 15), with these hooks monkeypatched for determinism (ruling 17):
  #   skill_module.uuid7 -> lambda: "0199a000-0000-7000-8000-000000000001"
  #   skill_module._now  -> lambda: datetime(2026, 7, 15, 9, 0, tzinfo=UTC)
  #   skill_module._today-> lambda: date(2026, 7, 15)
  #   frames_module.monotonic -> itertools.count(0, 0.001).__next__ (each query reads 1 ms)
  #   render.render_pdf  -> lambda html: b"%PDF-1.4 fake"
  #   -> frames accepted, tool start, tool done, part report, part proof, part artifact, part chips, done.
  ```

- [ ] **Step 1: Write the scenarios and run the corpus test to see the two new goldens missing**

`test_every_golden_file_has_a_scenario` and `test_scenario_matches_its_golden_file` report the missing files (`t7-red.txt`).

- [ ] **Step 2: Regenerate**

`POSEIDON_UPDATE_CONTRACTS=1 env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/contracts`, then `git status --short contracts/` must list exactly the two new files (plus nothing else). Run the corpus test twice more without the flag to prove the hooks make it byte-stable (`t7-green.txt`). Inspect `report_turn.json`: the report part's `run_id`, `generated_at`, every `queries[].ms` and the artifact url are the fixed values; the proof's `Run:` line names the fixed id.

- [ ] **Step 3: README**

Add a paragraph: the two report scenarios pin `run_id`, `generated_at`, query timings and the PDF bytes through the four hooks named above; a change to any of them is a contract change.

- [ ] **Step 4: Web replay** `cd web && npm test` (`t7-web.txt`): `turn-contracts.test.ts` replays the new files through the translator unchanged.

- [ ] **Step 5: Commit**

`test(contracts): report_entry and report_turn goldens, pinned through deterministic hooks`

---

### Task 8: The multi-office synthetic overlay and the pg goldens

**Model:** Opus implements; Opus reviews.

**Files:**
- Modify: `backend/poseidon/scripts/generate_synthetic.py:124-143, 310-373`, `ontology/synthetic/profiles.yml` (under `MARINE_SALES_PLANNING_V`)
- Test: `backend/tests/test_synthetic_generator.py`, `.../monthly_performance/tests/test_skill_pg.py:156-232`

**Interfaces:**
- Consumes: `generate(seed, profiles_path)`, `_generate_sales_rows`, `dataset_checksum`; `mom.share`, `rollup`-equivalent hand sums in the pg test.
- Produces:
  ```yaml
  # profiles.yml, under MARINE_SALES_PLANNING_V
  multi_office:
    customer_share: 0.3      # share of the customer pool that trades under extra offices; named customers always
    extra_offices: [1, 2]    # inclusive range of extra office values per chosen customer, per column
    row_share: 0.35          # probability that one of a chosen customer's rows moves to one of its extra offices
    seed_offset: 7
  ```
  ```python
  # generate_synthetic.py
  def _apply_multi_office_overlay(rows: list[dict], cfg: dict, named: list[str], team_offices: list[str], seed: int) -> None
  # Random(seed + cfg["seed_offset"]); customers = sorted({r["CUST_NM"]}); chosen = named + rng.sample(
  # sorted(set(customers) - set(named)), max(0, round(len(customers) * customer_share) - len(named)));
  # for each chosen customer and each column in ("CUSTOMER_TEAM_NAME", "PRIMARY_SUPPLY_TEAM_OFFICE"):
  #   home = that customer's current value; extras = rng.sample([o for o in team_offices if o != home],
  #   rng.randint(lo, hi)); for each of the customer's rows (in list order): if rng.random() < row_share:
  #   row[column] = rng.choice(extras). Only those two columns change. Called by generate() after
  # _generate_sales_rows and before _generate_gl_rows, with the resolved seed. Absent `multi_office`
  # in the profile -> no overlay (the function is skipped), so the fake-profile tests stay valid.
  ```

- [ ] **Step 1: Write the failing tests**

`backend/tests/test_synthetic_generator.py`: `test_multi_office_overlay_gives_named_customers_several_offices` (each named customer has >= 2 distinct `CUSTOMER_TEAM_NAME` and >= 2 distinct `PRIMARY_SUPPLY_TEAM_OFFICE` values; at least one generated customer still has exactly one), `test_overlay_changes_only_the_two_office_columns` (generate with the overlay and with a profile copy lacking `multi_office`: same row count, every column except the two is identical row by row), `test_same_seed_same_checksum` unchanged.

`test_skill_pg.py`: replace the two per-mover assertions at lines 185-189 with recomputed goldens: for each mover `name`, `share_of_book.display.current == f"{mom.share(office GP, book GP):.1f}%"` where both sums come from `DATASET.sales_rows` for that customer in the current window (office GP filtered on the office, book GP unfiltered), `prior` likewise; `offsets == ` the hand-computed list (other offices' GP change for that customer with sign opposite to the office change, top 5 by abs, same `{"key","change","display"}` shape via `mom.fmt_change`). Add `test_overlay_is_visible_in_the_golden_run`: at least one mover in the customer golden has `share_of_book.display.current != "100.0%"`. Update the module docstring (lines 3-5).

- [ ] **Step 2: Run the generator tests to verify they fail** (`t8-red.txt`). The pg test cannot be run red before the reseed; its red evidence is the first pg run after implementation if the recomputation is wrong, so write the recomputation carefully and read it back against `tools/context.py:56-128` before running.

- [ ] **Step 3: Implement, then reseed the compose database**

From the repo root: `docker compose -f infra/docker-compose.yml down -v` then `docker compose -f infra/docker-compose.yml up -d` and wait for the backend log line `skills registered:`; the `migrate` service applies 0000–0003 and the backend seeds the overlaid dataset.

- [ ] **Step 4: Run the generator tests, the offline suite, contracts, ruff, then the pg suite** (`t8-green.txt`, `t8-offline.txt`, `t8-contracts.txt`, `t8-pg.txt`). `test_chat_e2e_scripted.py`'s per-customer figures must still hold (the overlay never moves GP between customers); if one changes, STOP and report the row.

- [ ] **Step 5: Prove the guard:** set `row_share` to `0.0` in a copy of the profile passed to `generate`, confirm `test_multi_office_overlay_gives_named_customers_several_offices` fails, restore.

- [ ] **Step 6: Commit**

`feat(synthetic): deterministic multi-office overlay; pg report goldens recomputed for share and offsets`

---

### Task 9: The download routes (Next.js)

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Modify: `web/src/lib/request-identity.ts`
- Create: `web/src/lib/report-files.ts`, `web/src/lib/reports.ts`, `web/src/app/api/reports/[runId]/serve.ts`, `web/src/app/api/reports/[runId]/pdf/route.ts`, `web/src/app/api/reports/[runId]/html/route.ts`
- Test: `web/src/lib/report-files.test.ts`, `web/src/app/api/reports/[runId]/route.test.ts` (new)
- Modify: `web/README.md`

**Interfaces:**
- Consumes: `withUser(sub, fn)` from `web/src/db/client.ts`, `reportRun` from `web/src/db/schema.ts`, `SALES_ROLE`, `requireSub`, `conversations.ts`'s `UUID_PATTERN` (copy the regex; it is module-private).
- Produces:
  ```ts
  // request-identity.ts (added; requireSales unchanged)
  export function salesOr403(h: Headers): { ok: true; sub: string; roles: string[] } | { ok: false; response: Response }
  // ok:false carries Response.json({ type: "about:blank", title: "insufficient role",
  //   detail: "caller lacks required role 'Poseidon:Sales'", status: 403 }, { status: 403 }).
  // A missing sub still throws (requireSub): that is a proxy wiring fault, not a 403.

  // report-files.ts
  export function sanitizeFilename(name: string): string   // ruling 13
  export function monthLabel(isoFirstDay: string): string  // "2026-06-01" -> "June 2026" (en-US, UTC)
  export function reportFileName(run: { officeValue: string | null; periodCurrentStart: string; windowKind: string }, ext: "pdf" | "html"): string
  //   sanitizeFilename(`${officeValue ?? "Whole book"} ${monthLabel(start)}${windowKind === "mtd" ? " month to date" : ""}`) + "." + ext

  // reports.ts
  export const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  export async function readReportFile(sub: string, runId: string, kind: "pdf" | "html"):
    Promise<{ bytes: Buffer; fileName: string; contentType: string } | null>
  //   non-matching runId -> null without a query; selects {officeValue, periodCurrentStart, windowKind, pdf|html}
  //   by id through withUser; null row or null bytes -> null; contentType "application/pdf" | "text/html; charset=utf-8".

  // serve.ts
  export async function serveReport(request: Request, params: Promise<{ runId: string }>, kind: "pdf" | "html"): Promise<Response>
  //   salesOr403 -> 403 response; readReportFile null -> Response.json(problem 404 "report not found",
  //   `no ${kind} for run '${runId}'`); else 200 with headers content-type, content-length,
  //   `content-disposition: attachment; filename="${fileName}"`, cache-control: private, no-store.
  // pdf/route.ts and html/route.ts: export const dynamic = "force-dynamic";
  //   export async function GET(request, { params }) { return serveReport(request, params, "pdf" | "html"); }
  ```

- [ ] **Step 1: Write the failing tests**

`report-files.test.ts`: `sanitizeFilename('Rotterdam/"x" Office') === 'Rotterdam__x_ Office'`, collapses `"a   b"` to `"a b"`, empty -> `"report"`; `monthLabel("2026-06-01") === "June 2026"`; `reportFileName({officeValue: null, periodCurrentStart: "2026-10-01", windowKind: "mtd"}, "pdf") === "Whole book October 2026 month to date.pdf"`.

`route.test.ts` (offline; `vi.mock("../../../../lib/reports", ...)` so `readReportFile` is a `vi.fn()`; headers as `web/src/app/api/chat/[cid]/route.test.ts` builds them): `answers 403 as a problem detail without the role` (and `readReportFile` not called), `answers 404 for an unknown run` (mock returns null; body title `report not found`), `serves the pdf with a sanitised filename` (mock returns bytes `%PDF-fake` and fileName from `reportFileName` for `Rotterdam/"x" Office`; asserts status 200, `content-type: application/pdf`, `content-disposition` equals `attachment; filename="Rotterdam__x_ Office June 2026.pdf"`, body bytes equal), `serves html as an attachment too`. Plus a `live` describe (skips without `DRIZZLE_DATABASE_URL`): insert one `reportRun` row through `withUser` with `pdf: Buffer.from("%PDF-live")`, GET it through the real `readReportFile`, assert the bytes, and delete the row in `afterAll` (the test role may lack DELETE; if the delete fails with a permission error, leave the row and say so in the report: ruling 5 of the A1 morning review accepted accumulation).

- [ ] **Step 2: Run to verify they fail** (`cd web && npx vitest run src/lib/report-files.test.ts "src/app/api/reports"`; save as `t9-red.txt`).

- [ ] **Step 3: Implement** per the Interfaces block. Note `web/next.config.ts`'s `fallback` rewrite already lets these routes take precedence over Python (P16-R19); `web/src/proxy.ts`'s matcher already covers `/api/reports/...`.

- [ ] **Step 4: `npm test`, `npx tsc --noEmit`** (`t9-green.txt`). Then README: a "Report downloads" section after "Running with the backend": the two routes, that the proxy's 403 and the handler's 403 both apply, that a run with no PDF (host without Pango) 404s on `/pdf` and serves `/html`.

- [ ] **Step 5: Prove the guard:** make `salesOr403` return ok for an empty roles header, confirm the 403 test fails, restore.

- [ ] **Step 6: Commit**

`feat(web): /api/reports/[runId]/pdf and /html route handlers behind the Sales role, sanitised filenames`

---

### Task 10: The `report` renderer and the registry

**Model:** Sonnet implements; Sonnet reviews.

**Files:**
- Create: `web/src/ui/report/types.ts`, `ReportPart.tsx`, `KpiTiles.tsx`, `RankedTables.tsx`, `MoverCards.tsx`, `NewLostTables.tsx`, `Narrative.tsx`, `ProofBlock.tsx`, `ReportPart.test.tsx`
- Modify: `web/src/ui/message-parts/registry.tsx:80-96`, `web/src/ui/message-parts/registry.test.tsx:71-72`

**Interfaces:**
- Consumes: `PartProps` (`part`, `onChipSelect`, `disabled`), `TablePart`'s classes, `react-markdown`, the payload v1 shape (read it from `contracts/turns/report_turn.json`'s `report` part for the fixture, never retyped by hand).
- Produces:
  ```ts
  // types.ts: ReportPayload, Delta {prior, current, change, pct_change, display:{prior,current,change,pct}},
  //   Row {key, status, prior, current, change, pct, display}, RankedTable {rows, other, footer},
  //   Mover (Row & {driver}), EntityContext {...}, Narrative {template_md, llm_md, grounded, flags}
  // ReportPart({ part, onChipSelect, disabled }: PartProps)  -- registered as kind "report"
  //   header: scope.label (h3), `${periods.current.label} vs ${periods.prior.label}`, a "Month to date" badge
  //   when window_kind === "mtd", `Generated ${generated_at} | Source: ${source}`; then KpiTiles,
  //   RankedTables, MoverCards, NewLostTables, Narrative, ProofBlock. No suggested-questions section (ruling 9).
  // KpiTiles: seven tiles in payload key order; each shows display.current, `${display.change} (${display.pct})`,
  //   and "prior {display.prior}"; tiles are <dl> groups with the metric id as data-metric.
  // RankedTables: five tabs (buttons with aria-selected) "Customers|Suppliers by GP", "... by volume",
  //   "Ports by GP", "Ports by volume", "Deal class"; one real <table> at a time with <thead>, body rows,
  //   the Other row when present, and <tfoot> with the footer; cells are the display strings; columns
  //   [entity.label | "Port" | "Deal class", GP prior, GP current, GP change, GP %, Volume prior, Volume
  //   current, Volume change, Volume %, Margin prior, Margin current, Margin change, Margin %].
  // MoverCards: two columns "Increases"/"Decreases"; each card: key, driver badge, GP prior -> current,
  //   change and pct; when entity_context[key] exists, a reconciliation line
  //   `Office ${office_change} | other offices ${other_offices_net} | book ${book_change}` from the
  //   display strings and `share of book ${prior} -> ${current}`.
  // NewLostTables: two tables "New" (GP/Volume/Margin current) and "Lost" (prior); "none" when empty.
  // Narrative: <Markdown skipHtml disallowedElements={["a", "img"]} unwrapDisallowed>{md}</Markdown> where
  //   md = grounded && llm_md ? llm_md : template_md; a flags list rendered as <ul> beneath; when
  //   !grounded and llm_md === null show the "AI narrative withheld" flag as the first line. This is the
  //   TypeScript mirror of A1's escape-first, no-links rule: react-markdown never renders raw HTML with
  //   skipHtml, and anchors/images are unwrapped to their text.
  // ProofBlock: a closed <details><summary>Proof</summary> over payload.proof then payload.queries as
  //   `${label} ${sql_hash} rows=${rows} ms=${ms}` lines (same classes as ProofPart).
  // registry.tsx: report: ReportPart; the comment's count becomes "ten registered kinds plus FallbackPart".
  ```

- [ ] **Step 1: Write the failing tests**

`registry.test.tsx:71-72`: change the fallback probe's kind from `"report"` to `"hologram"`. `ReportPart.test.tsx` (`// @vitest-environment jsdom`; fixture = the `report` part payload read from `contracts/turns/report_turn.json` via `fs` and `path`, mirroring how `turn-contracts.test.ts` locates the corpus): `renders the scope and the period labels`, `renders seven kpi tiles in payload order` (`[data-metric]` list equals the payload's `kpis` keys), `switches ranked tables and shows a tfoot equal to the gp tile` (click "Ports by GP"; the tfoot's GP current cell text equals `kpis.GP.display.current`), `renders movers with drivers and reconciliation lines`, `narrative never renders links or html` (render with an overridden narrative `template_md: 'see [x](javascript:alert(1)) and <img src=x onerror=alert(1)>'`, grounded false; assert no `<a>` and no `<img>` in the container and the literal text `see x and` present), `proof is collapsed`, `an mtd payload shows the badge`.

- [ ] **Step 2: Run to verify they fail** (`cd web && npx vitest run src/ui`; `t10-red.txt`).

- [ ] **Step 3: Implement** per the Interfaces block. Tailwind classes in the existing neutral style (`rounded-md border border-neutral-200 ... dark:border-neutral-800`, `tabular-nums` on numeric cells); no new dependencies; no second live region.

- [ ] **Step 4: `npm test`, `npx tsc --noEmit`, `npm run lint`** (`t10-green.txt`).

- [ ] **Step 5: Prove the guard:** remove `disallowedElements`, confirm `narrative never renders links or html` fails on the anchor, restore.

- [ ] **Step 6: Commit**

`feat(web): the report part renderer -- tiles, ranked tabs, movers, new/lost, narrative, proof`

---

### Task 11: The pg flow test over HTTP, docs, verified figures, final gates

**Model:** Sonnet implements; Opus reviews the whole branch.

**Files:**
- Create: `backend/tests/test_report_flow_pg.py`
- Modify: `docs/architecture/00-overview.md` (rows D71–D74 after D70), `01-frontend.md:90-101` (the `report` row; the artifact row's D39 caveat resolved for report PDFs: a plain anchor to the Next.js route, D65), `02-backend-skills.md` (§4 the third flow and its steps; §5 the four slots; the `report_lookup` skill; `metric_query`'s `scope`), `03-llm-routing.md` (§3 or §4: the three state-block lines; §6: the four new router cases), `08-build-phases.md` (a "Phase 17 (as executed) — Report in chat" section after Phase 16, listing PR 0, A1, A2 as shipped and B, C as next; the retrieval slice stays where it is, renamed "Phase 17b — Retrieval (thin slice, optional)"), `CLAUDE.md` (Commands block figures)
- Test: the whole gate set

**Interfaces:**
- Consumes: everything above.
- Produces:
  ```
  D71 | The report flow validates each step before any dispatch and re-asks as a clarify turn with no tool frames; month to date is recognised by the step (D64 phrases or the running month by name), never by the period parser. | spec §6.2–6.3, §10
  D72 | reporting.report_lookup without an open report answers with a text part (ok=true) that the router relays; office scope stamping replaces a model-supplied filter on the office column when scope="office", and scope="book" drops the carried office, both named in the proof. | spec §6.5, §6.8
  D73 | Breakdown with comparison merges each window's top-50 breakdown and adds Driver to every comparison table when GP and VOLUME are both requested. | spec §6.8; PR B may lift the cap
  D74 | The report turn's suggested questions are one chips part after the report; the report renderer has no second question surface. File names on both sides use one sanitiser. The synthetic multi-office overlay is a seeded post-pass that changes only the two office columns. | spec §6.3–6.4, §6.9
  ```

- [ ] **Step 1: Write the pg flow test**

`backend/tests/test_report_flow_pg.py` (`pytestmark = pytest.mark.pg`, helpers imported from `test_chat_e2e_scripted.py`: `_settings`, `_headers`, `_dev_user`, `read_sse`, the app factory and `_drop_artifact_store`): one test `test_monthly_report_flow_over_http_then_a_scoped_follow_up`:
1. create a conversation; turn 1 `start a monthly report` -> names `["accepted", "part", "part", "done"]`, the chips part equals `PERSPECTIVE_CHIPS`.
2. turn 2 `customer office report` -> office chips; take the first chip's `send_text`.
3. turn 3 that office -> month chips; take the first chip's `send_text` (the latest complete month on the real clock: June 2026 on this dataset).
4. turn 4 that month -> names start `["accepted", "tool", "tool", "part", "part"]`, then `"part"` for the artifact only when no flag starts with `PDF not rendered:`, then `"part"` (chips), `"done"`; the report part's `scope.office_value` is the office; the chips carry three questions; `payload["narrative"]["flags"][0] == "model narrative skipped: stub mode"`.
5. `GET /api/reports/{run_id}/pdf` is NOT served by Python (that route is Next.js); instead read the row through `ReportStore(build_engine(url)).get_run("dev|...", run_id)` and assert `conversation_id == conversation_id` of the test.
6. turn 5 `top GP customers for Port of Singapore in June 2026` -> a `tool` frame for `data_qa.metric_query`; the proof part's lines end with `Office scope: CUSTOMER_TEAM_NAME = {office} (carried; scope=office)` and include `CUSTOMER_TEAM_NAME IN ({office})`.
7. the conversation's state row (`history` read through the app's history store, or a second `GET /api/conversations/{cid}` if the route returns state) carries `report_ref == run_id` and `mode == "default"`.

- [ ] **Step 2: Run the pg suite** (`t11-pg.txt`): the new test and everything else green.

- [ ] **Step 3: Docs** per the Files list; `docs/architecture/02-backend-skills.md` also gets the `reporting.report_lookup` row wherever the file lists skills.

- [ ] **Step 4: Final gates, in this order, each to its own file under the ledger**: offline suite (`final-offline.txt`), ruff (`final-ruff.txt`), `tests/contracts` (`final-contracts.txt`; `git status --short contracts/` empty), pg suite (`final-pg.txt`), `cd web && npm test` (`final-web.txt`), `npx tsc --noEmit`, `npm run lint`, `cd frontend && npm test -- --run` and `npm run lint` (the Vite app is untouched; the figures are refreshed). Record the figures in `CLAUDE.md`'s Commands block with today's date and "Phase 17 PR A2".

- [ ] **Step 5: Commit**

`docs(phase-17): report flow in docs 00/01/02/03/08; pg flow test over HTTP; verified figures for PR A2`

---

## Owner gate (Carlos, in the browser, after Task 11)

Setup (PowerShell, repo root): `docker compose -f infra/docker-compose.yml up -d` (the overlaid dataset is already seeded by Task 8's `down -v`; if not, run that first). In `web/`: `$env:POSEIDON_INTERNAL_TOKEN="dev-internal-token"; $env:DRIZZLE_DATABASE_URL="postgresql://poseidon:poseidon@localhost:5434/poseidon"; npm run dev`, then open `http://localhost:3000`. For the live follow-ups, the compose backend must run with `LLM_MODE=live` and AWS credentials (the compose file's defaults); the report narrative itself stays template-only unless the same is true.

1. New chat -> **Monthly report** -> **Supplier office** -> an office chip -> the latest month chip. The report renders with supplier rankings; every `tfoot` GP and volume equals the matching tile.
2. Click the PDF card: the file downloads with the office and month in its name.
3. Ask the three suggested questions by clicking them; each answers from the carried office (the proof names `Office scope: ...`).
4. **Monthly report** -> **Customer office** -> an office -> a month: customer rankings; at least one mover shows a share of book below 100% and an offsets line.
5. `docker compose -f infra/docker-compose.yml restart`, reload the chat, click the PDF card again: it still downloads.
6. Type `start a monthly report` then `whole book report` then `banana`: the month step re-asks with its chips.

## Self-review (writing-plans checklist, run 2026-10-16)

**1. Spec coverage, PR A2 scope.** §6.1 entry and the second-click reset: Task 6 (`_finish_entry`, `flow_slots_reset`, opener chip in `history`, `mock_chat`, `opener.ts`, `opener.json`). §6.2 the three steps, chips with certified `send_text`, re-asks, case-insensitive office: Task 6 (`report_flow.py`, Review Focus 1–2). §6.3 the report turn's wire order, the artifact name and url, the slot writes including `pass_through`, mode back to default: Task 6 (`slots_after_report`, rulings 9–10); `conversation_id` on the run row: Task 2. §6.4 the `report` part renderer with real table semantics, the Vite fallback untouched: Task 10 (ruling 9 for `SuggestedQuestions`). §6.5 `report_lookup` with paging and the "open a report first" reply: Task 5 (ruling 5). §6.7 the three state-block lines: Task 3. §6.8 `scope` stamping, breakdown with comparison, the four router cases in stub mode: Tasks 4 and 5 (rulings 6–8). §6.9 the overlay and the re-pinned goldens: Task 8 (ruling 16). §7.2 the two routes, 403 in the handler, 404s, `Content-Disposition`: Task 9 (ruling 12–13). §10 rows that belong to A2: month not complete / not present (Task 6), lookup without a report (Task 5), download of unknown run (Task 9), caller lacking the role (Task 9). §11 row A items in A2: route handler tests incl. 403/404 (Task 9), renderer vitest (Task 10), contracts corpus report turn (Task 7), the four router cases (Tasks 4–5), overlay goldens (Task 8); the owner gate above. §12 docs 00/01/02/03/08 and CLAUDE.md: Task 11. The A1 carry-forward list from the handoff prompt: loop guard and `_part_content` (Task 3), filename sanitising (Tasks 2 and 9), `get_bytes` kind (Task 1), stored HTML flag (Task 2), the renderer's no-links rule (Task 10), GL months exclusion (Task 1), visible-text polish (Task 1), flat ports (Task 1), template drift test (Task 1), header label (Task 1), dead CSS and `data-run` (Task 1), the `report_run` accumulation (ruled: no cleanup; Task 9's live test says so), the pytest command rule (Global Constraints, every task).

**2. Placeholder scan.** No TBD/TODO. Tests are named and their assertions stated; where a test body says "reuse the existing setup" it names the file and the helper. Copy strings are verbatim in Task 6's Interfaces. The three places an implementer must read code before writing (the `head` macro's exact markup in Task 1, `test_llm_loop.py`'s data client rows in Task 4, `context.py:56-128` for Task 8's recomputation) are named with line numbers.

**3. Type and name consistency.** `ConversationSlots.perspective/office_column/office_value/report_ref` (Task 3) are what `report_flow.py` writes (Task 6), `_render_slots` reads (Task 3), `_slots_for` reads (Tasks 4–5) and `metric_query.run` reads (Task 4). `SkillContext.conversation_id` (Task 2) is set in Task 6 and `internal.py`. `FakeReportStore` with `runs`/`get_run`/`get_bytes` (Task 6) is what Tasks 6 and 7 use; Task 2 and Task 5 keep their own module-private fakes because they predate the move (Task 6 moves `test_skill.py`'s `_FakeStore` and updates its imports). `safe_filename` (Python, Task 2) and `sanitizeFilename` (TypeScript, Task 9) implement ruling 13 and are pinned with the same input and output. `KPI_LABELS` (Task 1) feeds `render_html`; `KPI_FRIENDLY` (Task 5) is the lookup's own metric-grid vocabulary and deliberately matches `format_parts`' friendly names, not the HTML labels. The wire order in Task 6 matches `events._handle_tool_done` (parts, proof, artifacts) followed by the orchestrator's chips push, and Task 7's scenario and Task 11's pg test assert the same order. `mom.driver` exists today (`mom.py:144`) and Task 4 calls it rather than reimplementing the rule.

**4. Review Focus.** 1 -> Task 6 `test_office_step_matches_case_insensitively_and_reasks_unknown`; 2 -> Task 6 `test_month_step_reasks_for_missing_month_and_mtd_on_day_one`; 3 -> Task 6 `test_report_dispatch_failure_keeps_the_flow_at_the_month_step`; 4 -> Task 4 `test_carried_office_replaces_a_model_filter_on_the_same_column` and `test_scope_book_drops_the_carried_office`; 5 -> Task 2 `test_artifact_name_is_sanitised` and Task 9 `serves the pdf with a sanitised filename`.

**Things Fable will watch in review:** Task 5's `error_mid_turn` regeneration must change only the two `registered:` lines (verified today: the golden carries the four-skill list twice); Task 8's `down -v` wipes dev conversations in the compose database (dev data, accepted); Task 11's pg test depends on the real clock choosing June 2026 as the latest complete month, which holds until the synthetic profile's anchor moves.

---

## Paste this into the PR A2 execution chat

> You are Fable, executing Phase 17 PR A2 (report in chat) of Poseidon in C:\Users\carlo\github\latest_poseidon. The approved plan is `docs/superpowers/plans/2026-10-16-phase-17-prA2-report-in-chat.md`; read it first, then the spec `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §6–7, `.superpowers/sdd/2026-10-12-phase-17-prA1-report-engine/morning-review.md` (the A1 rulings), `CLAUDE.md`, and `.claude/CLAUDE.md` "Model delegation" and "Git & PRs". Main is at `1b2ae40` (PR #8 merged). Create the branch exactly as the plan's Branch line says. Use superpowers:subagent-driven-development: one implementer per task with the plan's model split (Sonnet for 1, 2, 3, 5, 7, 9, 10, 11; Opus for 4, 6, 8), a fresh reviewer per diff (Opus for 4, 6, 8 and the whole branch), fix rounds until clean. The implementer runs the tests and its report is the evidence; the reviewer never re-runs the suite. Every pytest command carries `env -u PERPLEXITY_API_KEY` and the full marker exclusion; never pipe pytest. Keep the ledger in `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/` (progress.md, per-task reports, red/green outputs, morning-review.md). At the end: write the PR description (what changed, why, how verified, with the final figures) and the PR B handoff prompt at the bottom of this plan file; then STOP. Do not run `gh pr create` and do not push unless Carlos says so in that chat. If Carlos has said "keep running until you're done", push the branch and open the PR with `gh pr create`, still never merging.

---

## PR description (as opened, 2026-10-16)

## Phase 17 PR A2 - Report in chat

Spec: `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §6-7, §8.1. Plan: `docs/superpowers/plans/2026-10-16-phase-17-prA2-report-in-chat.md`. Builds on PR A1 (#8, the report engine).

### What changed

**The flow (backend).** A third deterministic conversation flow, `mode = "report"`, with its step logic in a new pure module `core/chat/report_flow.py`. A sales user clicks the new opener chip **Monthly report**, answers three questions (perspective, office, month; every step re-asks with its chips when the answer resolves nothing), and the orchestrator dispatches the A1 internal skill by hand. The report turn emits `accepted, tool(start), tool(done), part report, part proof, [part artifact], part chips, done`, seeds the slots (periods, perspective, office, `report_ref`, the top-ten pass-through) and returns the mode to `default`. Month to date is recognised by the step (the D64 phrases or the running month by name); the step validates presence and completeness before any dispatch.

**Slots and the router's view.** `ConversationSlots` gains `perspective`, `office_column`, `office_value`, `report_ref`, `report_label` (additive, serialised; the label pins the report's own months for the state block so follow-ups cannot move them). The state block renders three new lines (carried perspective, carried office with the `scope` guidance, report context). The tool loop refuses any router attempt to call an internal skill with a 403 `internal skill` problem, and summarises a `report` part for the model in one line instead of a JSON dump.

**`data_qa.metric_query`.** New `scope: "office" | "book"` (default `office`). With a carried office and `scope="office"` the skill stamps the office onto every spec it builds, replacing any model-supplied filter on that column; `scope="book"` drops it; when the column is not a certified dimension of the entity (GL) nothing is stamped. Each case is named in the proof's last line. `group_by` + `compare_period` is now legal: both windows run at top 50, the union is merged, one comparison table per metric, with `Driver` (the A1 rule) when GP and VOLUME are both requested.

**`reporting.report_lookup`** (router-visible, new). Reads a section of the open report from its stored payload (KPIs as a metric grid, the five ranked tables, movers, new/lost, narrative, entity context), paged with a "rows remaining" line. Runs no query. Without an open report it answers with a text part and `ok=true` so the router relays it.

**Web.** `web/src/ui/report/` renders the `report` part (header, KPI tiles, five ranked tables behind tabs, mover cards with reconciliation lines, new/lost tables, the narrative through react-markdown with `skipHtml` and links/images unwrapped, a collapsed proof block). `/api/reports/[runId]/pdf` and `/html` route handlers serve the stored bytes behind the Sales role (checked in the handler as well as the proxy) with `Content-Disposition: attachment` and a sanitised file name; 403/404 as problem bodies. The opener gains the third chip.

**Synthetic data.** A deterministic multi-office overlay (own seeded stream, rewrites only the two office columns on a share of rows for a share of customers, named customers always) so `share_of_book < 100%` and offsets are non-empty; the pg goldens are recomputed by hand sums over the raw rows.

**A1 carry-forwards fixed (Tasks 1-2).** `get_bytes` kind check; zero displays never show a signed zero (`flat`); `Other (1 customer)`; flat ports dropped from `ports_rest_of_book`; KPI tiles follow `KPI_METRICS`; explicit ranked-table headers; dead CSS and `data-run` removed; the GL months query applies the dual-purpose exclusion; model insights appended to `llm_md` as `## Insights` (narrate prompt v2); stored HTML carries the `PDF not rendered` flag; sanitised artifact name (same rule as the TypeScript side); `conversation_id` on the run row.

**Contracts corpus.** `error_mid_turn.json` lists the new router-visible skill (two `registered:` lines); `opener.json` carries the third chip; `report_entry.json` and `report_turn.json` are new, pinned through five deterministic hooks (`skill.uuid7`, `skill._now`, `skill._today`, the frames module's `time.perf_counter`, `render.render_pdf`), documented in `contracts/turns/README.md`.

**Docs.** Decision log rows D71-D74; docs 01/02/03/08 updated; CLAUDE.md figures refreshed. No migration (`conversation_id` already existed on `report_run`).

### Why

Spec §6 (the flow, the part, the lookup), §7.2 (download routes), §8.1 (`metric_query` scope and breakdown with comparison), §6.9 (synthetic overlay) and the A1 morning-review carry-forwards. Rulings taken during execution are in `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/morning-review.md` (untracked; summarised in the PR thread below).

### How it was verified

Every task ran red-then-green with the implementer's output as evidence and a fresh reviewer per diff (Opus for Tasks 4, 6, 8 and the whole branch); one task fix round (Task 4: the office stamp follows the entity) and one final-review fix wave (a whole-book report crashed the Next.js renderer; a brief chip mid-flow left a half-filled office filter behind; the state block's report months drifted with follow-ups, now pinned by a fifth slot `report_label`; plus four fix-before-merge minors), and a one-byte docs encoding repair. Each guard was proven by breaking it. Final gates on `c527e99` (the last commit, `584a4a7`, changes one byte in a docs file):

| Gate | Result |
|---|---|
| Offline Python suite (full marker exclusion) | 2259 passed, 15 skipped, 130 deselected |
| `ruff check` | clean |
| `tests/contracts` | 9 passed, `contracts/` clean after the three planned regenerations |
| pg suite (serial, compose db on 5434, reseeded with the overlay) | 354 passed, 1 skipped |
| Web `npm test` / `npx tsc --noEmit` | 170 passed, 14 skipped / clean (the web package has no lint script) |
| Vite frontend `npm test -- --run` / `npm run lint` | 171 passed / clean (untouched; figures refreshed) |

New pinned behaviours: the pg flow test over HTTP (`tests/test_report_flow_pg.py`: entry, three steps, the report turn, the stored row's `conversation_id`, a scoped follow-up whose proof names the carried office, the state row), the orchestration tests (Review Focus 1-3: case-insensitive office with re-ask, month re-asks with no tool frames and no run row, a failed report turn staying at the month step), the stamping tests (Review Focus 4), the sanitiser pinned on `Rotterdam/"x" Office` in both suites (Review Focus 5), the renderer's no-links test and whole-book case, the route tests (403 before any read, 404, headers) plus a live describe against the real store, four router-decision cases in stub mode, the overlay's "only two columns change" test.

**Owner gate (Carlos, in the browser)** is in the plan's "Owner gate" section: supplier and customer reports, tfoot equals tile, the PDF card before and after a compose restart, the three suggested questions, the `banana` re-ask.

### Open product calls (see the morning review)

1. No way out of report mode mid-flow except an opener chip (spec §6.2's re-ask rule, kept as built; a Cancel chip is the obvious alternative).
2. A GL question after a report runs unstamped across every office, disclosed in the proof (ruled; the alternative is a clarify).
3. The breakdown-with-comparison header still prints `top 50` while the table is capped at `top_n`.
4. "1 rows remaining" has no singular form.

---

## Paste this into the PR B planning chat

> You are Fable, planning Phase 17 PR B (`metric_query` extensions) of Poseidon in C:\Users\carlo\github\latest_poseidon. Read first: `CLAUDE.md`; `.claude/CLAUDE.md` "Model delegation", "Plans get reviewed before execution" and "Git & PRs"; the spec `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §8.2 (multi-column group-by with the MONTH/QUARTER/YEAR pseudo-dimensions, `order_by`/`order`, share of total with the C04 order and no restamp, the scope hinter, "slice it another way", the remaining router-decision cases including the `router_live` ones); `docs/architecture/00-overview.md`'s decision log through D74 (D72-D73 are the A2 scope and breakdown rules PR B builds on); `docs/architecture/02-backend-skills.md` (the `scope` paragraph and the third flow) and `03-llm-routing.md` (the four A2 router cases); the A2 plan `docs/superpowers/plans/2026-10-16-phase-17-prA2-report-in-chat.md` (its rulings 6-8 and 18-19 and its "Review Focus"); and `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/morning-review.md` (the A2 rulings and the YOUR CALL items; its "Close-out" section records the provisional rulings: items 3, 4 and 5 are PR B work and the plan must list them explicitly for Carlos's go). Main is at `2f28751` (PR A2 merged as #9, https://github.com/sal1391/latest_poseidon/pull/9, on 2026-10-18). What A2 leaves for PR B, in `backend/poseidon/tasks/data_qa/skills/metric_query/`: `scope` and the entity-aware office stamp in `skill.py` (`_office_applies`, `_stamp_office`, `_office_proof`); `tools/compare_breakdown.py` with the top-50-per-window merge (D73; multi-column group-by may lift the cap); the routing harness in `backend/tests/test_llm_loop.py` whose `_slots_for` reads the four report slots and a `pass_through` list, and whose `assert_args_subset` checks `group_by`, `scope`, `compare_period`, `supplier`, `section`; the dev router (`core/chat/dev_router.py`) cannot route to `reporting.report_lookup` and treats the word "report" as a brief cue (parked with the PR C router drift). Carry-forwards to weigh: the compare-table header prints `top 50` while the table is capped at `top_n`; a key ranked 51st in one window reads lost/new; `office_chips` ignores the D16 row scope; the duplicated dispatch block in `orchestrator.py` (`_finish_report_turn` mirrors `_finish_subject_turn`). (The tab ARIA roles were fixed in the A2 final fix wave; not a carry-forward.) Use superpowers:brainstorming first, then superpowers:writing-plans; present the plan and STOP for Carlos's go. Model split: Sonnet for routine tasks, Opus for the share-of-total order and the scope hinter; Fable supervises only. Every pytest command carries `env -u PERPLEXITY_API_KEY` and the full marker exclusion; never pipe pytest; the `router_live` cases run only when Carlos says so (they are paid calls). Compose: another project may hold port 9000; the A2 override `.superpowers/sdd/2026-10-16-phase-17-prA2-report-in-chat/compose.a2.yml` puts the db on 5434 with MinIO unexposed.
