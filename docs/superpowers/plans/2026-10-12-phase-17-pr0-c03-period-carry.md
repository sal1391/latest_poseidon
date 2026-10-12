# Phase 17 PR 0 — C03 Period Carry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carried periods keep their full window (a year stays a year) and a carried comparison survives follow-up turns until the user names a new period, so the Phase 17 report gate can pass.

**Architecture:** `ConversationSlots` grows two additive end-date fields so a slot holds a half-open window instead of a first-of-period date. The period parser carries the stored window (and the comparison) instead of rebuilding a month; the pipeline's write rule becomes "B clears only when a single period is named"; the state block labels windows by name. One new module, `core/parsing/period_labels.py`, owns window classification, the slot-to-window fallback and human labels, so the parser, the prompt renderer and later the report all agree.

**Tech Stack:** Python 3.14 in `backend/.venv`, pytest 9.1.1, SQLAlchemy 2 (plain SQL), Postgres 16 on port 5434 for the `pg` suite. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §4 (PR 0), §2 (D62), §11 row 0, §12.

**Branch:** `fix/c03-period-carry` (already exists; the spec is its first commit). Work in this checkout, not a worktree, because the pg suite needs the running compose database on port 5434.

## Global Constraints

- Always `backend/.venv/Scripts/python.exe`, never a bare `python` (CLAUDE.md gotcha: a different global 3.14 without sqlalchemy).
- Never pipe pytest. Redirect to a file under `.superpowers/sdd/2026-10-12-phase-17-pr0-c03/` and read the file.
- Offline suite command (from `backend/`, bash):
  `env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live'`
- pg suite command (from `backend/`, bash; never parallelised):
  `DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg`
- Lint: `.venv/Scripts/python.exe -m ruff check .` from `backend/`. Never `ruff format` repo-wide.
- Source files touched here stay **pure ASCII on disk** (`test_both_files_are_ascii_on_disk` in `tests/test_parsing_periods.py` enforces it for the parser and its test; keep the convention in the new module too). Write any dash in a pinned string as `—`-style escapes.
- `ConversationSlots` grows **additively and at the end of the dataclass**: never reorder or reshape existing fields (P3 final-review rule, quoted in the class docstring).
- The frozen surfaces are untouched: SSE envelope, part kinds, the `contracts/turns` corpus (if `tests/contracts` shows a diff after these changes, STOP and report it; do not regenerate without the orchestrator's review).
- Any change to prompt text (Task 5) means the pg suite must run, not only the offline suite.
- Commit after every task with the message given; every commit ends with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Implementers run the tests and show the failing-first output in their report; reviewers never re-run the suite.

## Review Focus

Inputs the spec implies but did not spell out, each pinned to a test in the task that owns the code:

1. **A conversation row written before this PR** has `period_a` but no `period_a_end`. Expected: it reloads with `period_a_end = None` and the next turn carries the month containing the start, exactly as before. (Task 1 `test_slots_from_json_without_end_keys_leaves_the_ends_none`; Task 6 case 6.)
2. **Slots built in code with a start and no end** (`ConversationSlots(period_a=date(...))`, as every existing test and the brief flows do). Expected: the parser treats them as a month window, never raises. (Task 3 row `carry_from_a_mid_month_slot_date_yields_the_whole_month` keeps passing; Task 2 `test_slot_window_without_an_end_is_the_containing_month`.)
3. **A stored end at or before the start** (corrupt or hand-edited state). Expected: fall back to the month window; `PeriodWindow`'s inverted-window `ValueError` must never escape a turn. (Task 2 `test_slot_window_with_an_end_before_the_start_falls_back_to_the_month`; Task 3 row `a_stored_end_at_or_before_the_start_falls_back_to_the_month`.)
4. **A comparison connective whose right side resolves to nothing** ("compared to acme corp") while a comparison is carried. Expected: the carried comparison stays; an unresolvable phrase is not a silent reset. (Task 3 row `an_unresolvable_comparison_side_keeps_the_carried_comparison`; Task 4 `test_an_unresolvable_comparison_side_keeps_the_carried_comparison_slot`.)
5. **"vs last year" on a carried year, quarter or day range.** Expected: the shift rebuilds the same shape a year earlier (a carried 2025 compares to 2024; a carried Oct 1 to 11 compares to the same days of the prior year). (Task 3 rows `vs_last_year_shifts_a_carried_year_window_by_a_year`, `vs_last_year_shifts_a_carried_day_range_by_a_year`.)

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `backend/poseidon/core/skills/context.py` | `ConversationSlots` gains `period_a_end`, `period_b_end` (additive, at the end) | 1 |
| `backend/poseidon/core/chat/history.py` | `slots_to_json` / `slots_from_json` carry the two keys; faithful inverse, no fallback logic | 1 |
| `backend/tests/test_history_store.py` | offline round-trip tests for the new keys; one pg round trip through `DbStateStore` | 1 |
| `backend/poseidon/core/parsing/period_labels.py` (new) | `window_kind`, `slot_window`, `label_window`, `month_window` — the one place windows are classified, rebuilt from slots and named | 2 |
| `backend/tests/test_period_labels.py` (new) | table tests for the new module | 2 |
| `backend/poseidon/core/parsing/period_parser.py` | carry the stored window for A and B; shift a range; fourth connective "compare to / compared with"; docstring | 3 |
| `backend/tests/test_parsing_periods.py` | replace the "never carried" row, add nine rows, retire the limitation test | 3 |
| `backend/poseidon/core/parsing/carry.py` | `SlotUpdates` gains the two end fields; `apply_carry` folds them | 4 |
| `backend/poseidon/core/parsing/pipeline.py` | the D62 write rule; docstring | 4 |
| `backend/tests/test_parsing_normalize_carry.py`, `backend/tests/test_parsing_hinter_pipeline.py` | carry tests for the end fields; multi-turn pipeline sequences | 4 |
| `backend/poseidon/core/llm/prompts.py` | `_render_slots` labels windows; docstring | 5 |
| `backend/tests/test_llm_prompts.py` | golden update | 5 |
| `backend/tests/test_c03_period_carry.py` (new) | the spec §4.6 acceptance sequences through parse, serialize, reload, reparse | 6 |
| `docs/architecture/00-overview.md`, `docs/architecture/02-backend-skills.md`, `CLAUDE.md` | D61–D69, the parser row, verified figures | 7 |

---

### Task 1: Slots carry window ends; the serializer round-trips them

**Files:**
- Modify: `backend/poseidon/core/skills/context.py:105-146` (the `ConversationSlots` dataclass)
- Modify: `backend/poseidon/core/chat/history.py:836-869` (`slots_to_json`, `slots_from_json`)
- Test: `backend/tests/test_history_store.py` (offline section around line 88; pg section around line 926)

**Interfaces:**
- Produces: `ConversationSlots.period_a_end: date | None = None`, `ConversationSlots.period_b_end: date | None = None` (both appended **after** `pass_through`). `slots_to_json` emits keys `"period_a_end"`, `"period_b_end"` as ISO strings or `None`; `slots_from_json` reads them with `.get` and `date.fromisoformat`, exactly like `period_a`/`period_b`, with **no** month fallback (the fallback belongs to `period_labels.slot_window`, Task 2, so the serializer stays a faithful inverse and round-trip equality holds).

- [ ] **Step 1: Write the failing offline tests**

In `backend/tests/test_history_store.py`, replace the body of `test_slots_to_json_and_back_round_trips_a_populated_instance` and `test_slots_to_json_and_back_round_trips_the_empty_default_instance` so the expected dicts include the two new keys, and add two tests after `test_slots_from_json_defaults_missing_keys_like_the_dataclass`:

```python
def test_slots_to_json_and_back_round_trips_a_populated_instance():
    """Dates become ISO strings, pass_through becomes a list of pairs -- the
    two shapes that cannot survive a raw dataclasses.asdict() through
    json.dumps unchanged. D62: the window ends ride along as ISO dates."""
    slots = ConversationSlots(
        customer="ACME MARINE",
        port="SGSIN",
        period_a=date(2026, 4, 1),
        period_b=date(2026, 3, 1),
        mode="existing",
        region="APAC",
        topic="gp",
        pass_through=(("customer", "ACME MARINE"), ("port", "SGSIN")),
        period_a_end=date(2026, 5, 1),
        period_b_end=date(2026, 4, 1),
    )

    raw = slots_to_json(slots)

    assert raw == {
        "customer": "ACME MARINE",
        "port": "SGSIN",
        "period_a": "2026-04-01",
        "period_b": "2026-03-01",
        "mode": "existing",
        "region": "APAC",
        "topic": "gp",
        "pass_through": [["customer", "ACME MARINE"], ["port", "SGSIN"]],
        "period_a_end": "2026-05-01",
        "period_b_end": "2026-04-01",
    }
    assert slots_from_json(raw) == slots


def test_slots_to_json_and_back_round_trips_the_empty_default_instance():
    slots = ConversationSlots()

    raw = slots_to_json(slots)

    assert raw == {
        "customer": None,
        "port": None,
        "period_a": None,
        "period_b": None,
        "mode": "default",
        "region": None,
        "topic": None,
        "pass_through": [],
        "period_a_end": None,
        "period_b_end": None,
    }
    assert slots_from_json(raw) == slots


def test_slots_from_json_without_end_keys_leaves_the_ends_none():
    """A row written before Phase 17 PR 0 has starts but no ends. The
    serializer is a faithful inverse: the ends come back None and the
    month fallback happens later, in period_labels.slot_window, at parse
    time -- so this reload must not invent a date the row never held."""
    raw = {"period_a": "2025-01-01", "period_b": "2024-12-01"}

    slots = slots_from_json(raw)

    assert slots.period_a == date(2025, 1, 1)
    assert slots.period_a_end is None
    assert slots.period_b == date(2024, 12, 1)
    assert slots.period_b_end is None


def test_slots_to_json_writes_a_year_window_as_two_iso_dates():
    slots = ConversationSlots(period_a=date(2025, 1, 1), period_a_end=date(2026, 1, 1))

    raw = slots_to_json(slots)

    assert raw["period_a"] == "2025-01-01"
    assert raw["period_a_end"] == "2026-01-01"
    assert raw["period_b"] is None
    assert raw["period_b_end"] is None
```

Then, in the pg section after `test_db_state_store_round_trip_through_real_jsonb`, add:

```python
@pytest.mark.pg
def test_db_state_store_round_trips_period_window_ends_through_real_jsonb(history_store):
    """D62: the window ends survive the jsonb write and read. A full-year A
    and a one-month B, the exact shape a report conversation stores."""
    user_history = history_store.for_user(_fresh_user_sub())
    conversation, _opener = user_history.create_conversation()
    state_store = DbStateStore(user_history)
    slots = ConversationSlots(
        period_a=date(2025, 1, 1),
        period_b=date(2024, 12, 1),
        period_a_end=date(2026, 1, 1),
        period_b_end=date(2025, 1, 1),
    )

    state_store.put(conversation["id"], slots)

    reloaded = state_store.get(conversation["id"])
    assert reloaded == slots
    assert reloaded.period_a_end == date(2026, 1, 1)
```

- [ ] **Step 2: Run the offline tests to verify they fail**

From `backend/`:

```bash
mkdir -p ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_history_store.py -k "slots" -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t1-red.txt 2>&1; tail -20 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t1-red.txt
```

Expected: the four offline slot tests FAIL. The two round-trip tests fail with `TypeError: ConversationSlots.__init__() got an unexpected keyword argument 'period_a_end'`; the no-ends test fails with `AttributeError: 'ConversationSlots' object has no attribute 'period_a_end'`. That is the right failure: the field does not exist yet.

- [ ] **Step 3: Add the fields and the serializer keys**

In `backend/poseidon/core/skills/context.py`, append to the `ConversationSlots` docstring (before the closing `"""`):

```python
    ``period_a_end``/``period_b_end`` (Phase 17 PR 0, D62) are the EXCLUSIVE
    ends of the windows whose first days ``period_a``/``period_b`` hold, so a
    carried year stays a year. They are appended after ``pass_through`` --
    additive growth only -- and default to ``None``: a slot with a start and
    no end is read as the month containing the start by
    ``parsing.period_labels.slot_window``, which is the ONLY place that
    fallback lives. Nothing here or in the serializer invents an end.
```

and add the two fields as the **last** two fields of the dataclass:

```python
    pass_through: tuple[tuple[str, str], ...] = ()
    period_a_end: date | None = None
    period_b_end: date | None = None
```

In `backend/poseidon/core/chat/history.py`, `slots_to_json` returns:

```python
    return {
        "customer": slots.customer,
        "port": slots.port,
        "period_a": slots.period_a.isoformat() if slots.period_a is not None else None,
        "period_b": slots.period_b.isoformat() if slots.period_b is not None else None,
        "mode": slots.mode,
        "region": slots.region,
        "topic": slots.topic,
        "pass_through": [[key, value] for key, value in slots.pass_through],
        "period_a_end": slots.period_a_end.isoformat() if slots.period_a_end is not None else None,
        "period_b_end": slots.period_b_end.isoformat() if slots.period_b_end is not None else None,
    }
```

and `slots_from_json` becomes:

```python
def slots_from_json(raw: dict) -> ConversationSlots:
    """The inverse of :func:`slots_to_json`. Unknown keys are ignored
    (forward compatibility: a newer app version's extra key is simply never
    read); a missing key defaults exactly like the dataclass field it
    represents, via ``.get(key, default)`` -- there is no branch here that
    treats "absent" specially, ``.get`` already does. That includes the
    D62 window ends: a pre-Phase-17 row has none, and they come back
    ``None`` -- the month fallback is ``period_labels.slot_window``'s job
    at parse time, so this function stays a faithful inverse."""
    pass_through_raw = raw.get("pass_through", ())
    return ConversationSlots(
        customer=raw.get("customer"),
        port=raw.get("port"),
        period_a=_iso_date(raw.get("period_a")),
        period_b=_iso_date(raw.get("period_b")),
        mode=raw.get("mode", "default"),
        region=raw.get("region"),
        topic=raw.get("topic"),
        pass_through=tuple((pair[0], pair[1]) for pair in pass_through_raw),
        period_a_end=_iso_date(raw.get("period_a_end")),
        period_b_end=_iso_date(raw.get("period_b_end")),
    )


def _iso_date(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw is not None else None
```

- [ ] **Step 4: Run the offline tests to verify they pass, then the pg test**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_history_store.py -k "slots" -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t1-green.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t1-green.txt
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_history_store.py -m pg -k "period_window_ends" -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t1-pg.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t1-pg.txt
```

Expected: all slot tests PASS; the pg test PASSES (not skipped — if it reports skipped, the database on 5434 is not reachable; stop and report).

- [ ] **Step 5: Run ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/skills/context.py poseidon/core/chat/history.py tests/test_history_store.py
git add poseidon/core/skills/context.py poseidon/core/chat/history.py tests/test_history_store.py
git commit -q -F - <<'EOF'
feat(slots): carry period window ends in ConversationSlots (D62)

Additive period_a_end / period_b_end fields, serialized by slots_to_json
and read back by slots_from_json with no fallback, so a stored year is a
year on reload. Pre-Phase-17 rows reload with None ends.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 2: `period_labels` — classify, rebuild and name windows

**Files:**
- Create: `backend/poseidon/core/parsing/period_labels.py`
- Test: `backend/tests/test_period_labels.py`

**Interfaces:**
- Consumes: `PeriodWindow` from `poseidon.core.data.specs` (frozen dataclass, `start` inclusive, `end` exclusive, raises `ValueError` when `end <= start`).
- Produces (all pure, no clock, no I/O):
  - `WindowKind = Literal["month", "quarter", "year", "ytd", "range"]`
  - `month_window(year: int, month: int) -> PeriodWindow`
  - `window_kind(window: PeriodWindow) -> WindowKind`
  - `slot_window(start: date, end: date | None) -> PeriodWindow` — `PeriodWindow(start, end)` when `end is not None and end > start`, else the month containing `start`.
  - `label_window(window: PeriodWindow) -> str` — `"April 2026"`, `"Q2 2026"`, `"2025"`, `"2026 year to date"`, `"October 1 to 11, 2026"`, `"December 15, 2025 to January 14, 2026"`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_period_labels.py`:

```python
"""Phase 17 PR 0 (D62): ``core.parsing.period_labels`` -- the one module
that classifies a half-open ``PeriodWindow``, rebuilds a window from a
slot's start and optional end, and names a window for humans. The parser's
carry path, the prompt state block and (later) the report all read windows
through here, so a label can never disagree between surfaces.

Pure ASCII on disk, like the parser and its tests.
"""

from datetime import date
from pathlib import Path
from typing import NamedTuple

import pytest

from poseidon.core.data.specs import PeriodWindow
from poseidon.core.parsing import period_labels
from poseidon.core.parsing.period_labels import (
    label_window,
    month_window,
    slot_window,
    window_kind,
)


class KindCase(NamedTuple):
    id: str
    window: PeriodWindow
    kind: str
    label: str


CASES = [
    KindCase("month", PeriodWindow(date(2026, 4, 1), date(2026, 5, 1)), "month", "April 2026"),
    KindCase("december", PeriodWindow(date(2025, 12, 1), date(2026, 1, 1)), "month", "December 2025"),
    KindCase("quarter_q2", PeriodWindow(date(2026, 4, 1), date(2026, 7, 1)), "quarter", "Q2 2026"),
    KindCase("quarter_q4", PeriodWindow(date(2025, 10, 1), date(2026, 1, 1)), "quarter", "Q4 2025"),
    KindCase("year", PeriodWindow(date(2025, 1, 1), date(2026, 1, 1)), "year", "2025"),
    KindCase("ytd", PeriodWindow(date(2026, 1, 1), date(2026, 7, 15)), "ytd", "2026 year to date"),
    KindCase(
        "day_range_in_one_month",
        PeriodWindow(date(2026, 10, 1), date(2026, 10, 12)),
        "range",
        "October 1 to 11, 2026",
    ),
    KindCase(
        "day_range_across_months",
        PeriodWindow(date(2025, 12, 15), date(2026, 1, 15)),
        "range",
        "December 15, 2025 to January 14, 2026",
    ),
    KindCase(
        # Starts on the first but is neither a month, quarter, year nor Jan-1
        # anchored: two months is a range.
        "two_months_is_a_range",
        PeriodWindow(date(2026, 4, 1), date(2026, 6, 1)),
        "range",
        "April 1 to May 31, 2026",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_window_kind(case: KindCase):
    assert window_kind(case.window) == case.kind


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_label_window(case: KindCase):
    assert label_window(case.window) == case.label


def test_month_window_wraps_december():
    assert month_window(2025, 12) == PeriodWindow(date(2025, 12, 1), date(2026, 1, 1))
    assert month_window(2026, 4) == PeriodWindow(date(2026, 4, 1), date(2026, 5, 1))


def test_slot_window_with_a_valid_end_is_that_window():
    assert slot_window(date(2025, 1, 1), date(2026, 1, 1)) == PeriodWindow(
        date(2025, 1, 1), date(2026, 1, 1)
    )


def test_slot_window_without_an_end_is_the_containing_month():
    """Pre-Phase-17 rows and code-built slots have a start and no end; the
    pre-PR behaviour (the month containing the start) is preserved here and
    nowhere else."""
    assert slot_window(date(2026, 4, 17), None) == PeriodWindow(date(2026, 4, 1), date(2026, 5, 1))


def test_slot_window_with_an_end_before_the_start_falls_back_to_the_month():
    """A corrupt or hand-edited end must never let PeriodWindow's inverted-
    window ValueError escape a chat turn."""
    assert slot_window(date(2026, 4, 1), date(2026, 4, 1)) == month_window(2026, 4)
    assert slot_window(date(2026, 4, 1), date(2026, 3, 1)) == month_window(2026, 4)


def test_module_is_ascii_on_disk():
    for path in (Path(period_labels.__file__), Path(__file__)):
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_period_labels.py -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t2-red.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t2-red.txt
```

Expected: collection error `ModuleNotFoundError: No module named 'poseidon.core.parsing.period_labels'`. Right failure: the module does not exist.

- [ ] **Step 3: Write the module**

Create `backend/poseidon/core/parsing/period_labels.py`:

```python
"""Window classification, slot-to-window reconstruction and human labels
for half-open :class:`PeriodWindow` values (Phase 17 PR 0, decision D62).

Three callers share this module so they can never disagree about what a
window IS or how it is named:

* ``period_parser`` -- carries the stored window of a slot (and needs its
  shape to shift it a year back for ``vs last year``);
* ``llm.prompts`` -- renders ``Carried period A: April 2026 (2026-04-01..
  2026-05-01)`` in the state block;
* the Phase 17 report -- labels its month pair and its month-to-date range.

``slot_window`` is the ONLY place the "a start with no end is the month
containing it" fallback lives. ``ConversationSlots`` grew its end fields
additively, so pre-Phase-17 rows and code-built slots legitimately carry a
start alone; they are read exactly as they were before this phase.

Pure functions, no clock, no I/O, pure ASCII on disk.
"""

from datetime import date, timedelta
from typing import Literal

from poseidon.core.data.specs import PeriodWindow

WindowKind = Literal["month", "quarter", "year", "ytd", "range"]

KIND_MONTH: WindowKind = "month"
KIND_QUARTER: WindowKind = "quarter"
KIND_YEAR: WindowKind = "year"
KIND_YTD: WindowKind = "ytd"
KIND_RANGE: WindowKind = "range"

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

_QUARTER_FIRST_MONTHS = (1, 4, 7, 10)


def month_window(year: int, month: int) -> PeriodWindow:
    """``[first of the month, first of the next month)``."""
    return PeriodWindow(date(year, month, 1), _add_months(date(year, month, 1), 1))


def _add_months(first_of_month: date, months: int) -> date:
    """``first_of_month`` moved forward ``months`` whole months (stays on the 1st)."""
    index = first_of_month.year * 12 + (first_of_month.month - 1) + months
    return date(index // 12, index % 12 + 1, 1)


def window_kind(window: PeriodWindow) -> WindowKind:
    """The shape that produced ``window``: checked most specific first, so a
    full year is ``year`` (not ``quarter``, not ``ytd``) and Q1 is
    ``quarter`` (not ``ytd``)."""
    start, end = window.start, window.end
    if start.day != 1:
        return KIND_RANGE
    if start.month == 1 and end == date(start.year + 1, 1, 1):
        return KIND_YEAR
    if start.month in _QUARTER_FIRST_MONTHS and end == _add_months(start, 3):
        return KIND_QUARTER
    if end == _add_months(start, 1):
        return KIND_MONTH
    if start.month == 1 and end.year == start.year:
        return KIND_YTD
    return KIND_RANGE


def slot_window(start: date, end: date | None) -> PeriodWindow:
    """The window a slot holds: ``[start, end)`` when ``end`` is a real
    exclusive end after ``start``; otherwise the month containing ``start``
    -- the pre-Phase-17 reading, kept for rows and code-built slots that
    carry no end, and for a corrupt end that would otherwise make
    ``PeriodWindow`` raise inside a chat turn."""
    if end is not None and end > start:
        return PeriodWindow(start, end)
    return month_window(start.year, start.month)


def label_window(window: PeriodWindow) -> str:
    """A human name for ``window``; see the module docstring for examples."""
    kind = window_kind(window)
    start = window.start
    if kind == KIND_MONTH:
        return f"{_MONTH_NAMES[start.month - 1]} {start.year}"
    if kind == KIND_QUARTER:
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if kind == KIND_YEAR:
        return f"{start.year}"
    if kind == KIND_YTD:
        return f"{start.year} year to date"
    last = window.end - timedelta(days=1)
    if start.year == last.year and start.month == last.month:
        return f"{_MONTH_NAMES[start.month - 1]} {start.day} to {last.day}, {start.year}"
    if start.year == last.year:
        return (
            f"{_MONTH_NAMES[start.month - 1]} {start.day} to "
            f"{_MONTH_NAMES[last.month - 1]} {last.day}, {start.year}"
        )
    return (
        f"{_MONTH_NAMES[start.month - 1]} {start.day}, {start.year} to "
        f"{_MONTH_NAMES[last.month - 1]} {last.day}, {last.year}"
    )


__all__ = [
    "KIND_MONTH",
    "KIND_QUARTER",
    "KIND_RANGE",
    "KIND_YEAR",
    "KIND_YTD",
    "WindowKind",
    "label_window",
    "month_window",
    "slot_window",
    "window_kind",
]
```

- [ ] **Step 4: Run to verify pass**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_period_labels.py -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t2-green.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t2-green.txt
```

Expected: all PASS (23 tests).

- [ ] **Step 5: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/parsing/period_labels.py tests/test_period_labels.py
git add poseidon/core/parsing/period_labels.py tests/test_period_labels.py
git commit -q -F - <<'EOF'
feat(parsing): period_labels classifies, rebuilds and names windows (D62)

One module for window kind, the slot start+end -> window fallback, and
human labels, shared by the parser carry path, the state block and the
Phase 17 report.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 3: The parser carries the stored window and the comparison

**Files:**
- Modify: `backend/poseidon/core/parsing/period_parser.py` — module docstring (lines ~40-75), `_COMPARISON_RE` (line 183), `PeriodParse.b_source` comment (line 204), `_shift_back_one_year` (lines 282-298), `parse_periods` carry block (lines 458-470)
- Test: `backend/tests/test_parsing_periods.py`

**Interfaces:**
- Consumes: `period_labels.slot_window`, `period_labels.window_kind`, `period_labels.KIND_RANGE`; `ConversationSlots.period_a_end` / `period_b_end` (Task 1).
- Produces: `PeriodParse.b_source in {"text", "carry", "none"}`. Carry rule: when the left side names no period and `slots.period_a` is set, A = `slot_window(slots.period_a, slots.period_a_end)`, `a_source="carry"`. When the right side resolves to nothing **and the left side named no period** and `slots.period_b` is set, B = `slot_window(slots.period_b, slots.period_b_end)`, `b_source="carry"`. A fourth connective, `compare to` / `compare with` / `compared with`, joins `vs`, `versus`, `compared to`.

- [ ] **Step 1: Update the test table (failing first)**

In `backend/tests/test_parsing_periods.py`:

(a) Replace the row with id `period_b_is_never_carried` by:

```python
    Case(
        # D62: a carried comparison stays until something replaces it.
        "period_b_is_carried_when_the_text_names_nothing",
        "how about volume",
        ConversationSlots(period_a=date(2026, 4, 1), period_b=date(2025, 4, 1)),
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), _month(2025, 4), "carry", "carry", None),
    ),
```

(b) Add these rows directly after it (before `a_new_phrase_replaces_the_carried_period`):

```python
    Case(
        # The stored window, not the month containing its start (C03).
        "a_carried_year_window_stays_a_year",
        "then by port",
        ConversationSlots(period_a=date(2025, 1, 1), period_a_end=date(2026, 1, 1)),
        REF,
        WIDE,
        PeriodParse(_year(2025), None, "carry", "none", None),
    ),
    Case(
        "a_carried_quarter_window_stays_a_quarter",
        "and margin",
        ConversationSlots(period_a=date(2026, 4, 1), period_a_end=date(2026, 7, 1)),
        REF,
        WIDE,
        PeriodParse(_quarter(2026, 2), None, "carry", "none", None),
    ),
    Case(
        # Only the right side names a period: A carries, B is replaced.
        "a_comparison_side_only_replaces_b_and_keeps_a",
        "compare to may 2026 instead",
        ConversationSlots(
            period_a=date(2026, 7, 1),
            period_a_end=date(2026, 8, 1),
            period_b=date(2026, 6, 1),
            period_b_end=date(2026, 7, 1),
        ),
        REF,
        WIDE,
        PeriodParse(_month(2026, 7), _month(2026, 5), "carry", "text", None),
    ),
    Case(
        "compared_with_is_a_connective_too",
        "april 2026 compared with march 2026",
        NO_SLOTS,
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), _month(2026, 3), "text", "text", None),
    ),
    Case(
        # A single named period: A is replaced; the parser reports no B and
        # the pipeline (not the parser) turns that into a slot clear.
        "a_single_period_phrase_names_no_comparison",
        "volume in may 2026",
        ConversationSlots(
            period_a=date(2026, 4, 1),
            period_a_end=date(2026, 5, 1),
            period_b=date(2026, 3, 1),
            period_b_end=date(2026, 4, 1),
        ),
        REF,
        WIDE,
        PeriodParse(_month(2026, 5), None, "text", "none", None),
    ),
    Case(
        "vs_last_year_shifts_a_carried_year_window_by_a_year",
        "vs last year",
        ConversationSlots(period_a=date(2025, 1, 1), period_a_end=date(2026, 1, 1)),
        REF,
        WIDE,
        PeriodParse(_year(2025), _year(2024), "carry", "text", None),
    ),
    Case(
        "vs_last_year_shifts_a_carried_day_range_by_a_year",
        "vs last year",
        ConversationSlots(period_a=date(2026, 10, 1), period_a_end=date(2026, 10, 12)),
        REF,
        WIDE,
        PeriodParse(
            PeriodWindow(date(2026, 10, 1), date(2026, 10, 12)),
            PeriodWindow(date(2025, 10, 1), date(2025, 10, 12)),
            "carry",
            "text",
            None,
        ),
    ),
    Case(
        # An unresolvable right side is not a silent reset of the comparison.
        "an_unresolvable_comparison_side_keeps_the_carried_comparison",
        "compared to acme corp",
        ConversationSlots(
            period_a=date(2026, 7, 1),
            period_a_end=date(2026, 8, 1),
            period_b=date(2026, 6, 1),
            period_b_end=date(2026, 7, 1),
        ),
        REF,
        WIDE,
        PeriodParse(_month(2026, 7), _month(2026, 6), "carry", "carry", None),
    ),
    Case(
        # A corrupt end never raises; the month containing the start is used.
        "a_stored_end_at_or_before_the_start_falls_back_to_the_month",
        "how about volume",
        ConversationSlots(period_a=date(2026, 4, 1), period_a_end=date(2026, 4, 1)),
        REF,
        WIDE,
        PeriodParse(_month(2026, 4), None, "carry", "none", None),
    ),
```

(c) In `test_sources_are_from_the_declared_vocabulary`, replace the two b-side lines with:

```python
    assert result.b_source in {"text", "carry", "none"}
```

(d) Replace `test_module_documents_the_carried_granularity_limitation` with:

```python
def test_module_documents_window_carry_and_no_longer_claims_a_limitation():
    """D62 retired the v1 'carried granularity' limitation: the carry path
    now reads the stored window. The docstring must describe the new rule
    (both sides carry) and must not still advertise the old limitation."""
    doc = period_parser.__doc__ or ""
    assert "KNOWN v1 LIMITATION" not in doc
    assert "period_b" in doc and "carried" in doc.casefold()
```

(e) Update the module docstring bullet in the test file that says `the "period_b is never carried" rule` to `the D62 carry rule for both sides (a comparison stays until replaced)`.

- [ ] **Step 2: Run to verify the new rows fail**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_parsing_periods.py -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t3-red.txt 2>&1; grep -E "passed|failed" ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t3-red.txt | tail -3
```

Expected: FAIL on `period_b_is_carried_when_the_text_names_nothing` (B comes back `None`), `a_carried_year_window_stays_a_year` (A is January only), `a_carried_quarter_window_stays_a_quarter`, `a_comparison_side_only_replaces_b_and_keeps_a` (no split on "compare to": A becomes None/B None), `compared_with_is_a_connective_too`, both `vs_last_year_shifts_a_carried_*`, `an_unresolvable_comparison_side_keeps_the_carried_comparison`, `test_sources_are_from_the_declared_vocabulary` for the carried rows, and the docstring test. Everything else still passes. These are the right failures: each names the behaviour being added.

- [ ] **Step 3: Implement**

In `backend/poseidon/core/parsing/period_parser.py`:

(a) Add the import next to the other `poseidon.core.parsing` imports:

```python
from poseidon.core.parsing.period_labels import KIND_RANGE, slot_window, window_kind
```

(b) Add `_KIND_RANGE = KIND_RANGE` after `_KIND_YTD = "ytd"` (the existing `_KIND_*` string values already equal `period_labels`' values; do not change them).

(c) Replace `_COMPARISON_RE`:

```python
# ``\bvs\b\.?`` and not ``\bvs\.?\b``: a word boundary cannot follow the dot
# in "vs. march" (dot and space are both non-word), so the boundary has to be
# taken on the ``s`` and the dot consumed afterwards. ``compare(d) to/with``
# (D62) lets "compare to may instead" replace only the comparison side.
_COMPARISON_RE = re.compile(
    r"\bvs\b\.?|\bversus\b|\bcompared?\s+(?:to|with)\b"
)
```

(d) In `PeriodParse`, change the `b_source` annotation comment to `# "text" | "carry" | "none"`.

(e) In `_shift_back_one_year`, insert before the final `shifted_end = ...` line:

```python
    if resolved.kind == _KIND_RANGE:
        return _Resolved(
            PeriodWindow(
                _same_day_prior_year(start), _same_day_prior_year(resolved.window.end)
            ),
            _KIND_RANGE,
        )
```

(f) Replace the carry block in `parse_periods` (from `# period_a: the left-hand phrase ...` through `b_source = ...`) with:

```python
    # period_a: the left-hand phrase, or last turn's WINDOW when the text
    # named none (D62: the stored end makes a carried year stay a year). An
    # explicit phrase suppresses the carry even when it resolved to nothing
    # -- the user asked for a period, not for last turn's.
    resolved_a = left.resolved
    a_source = _SOURCE_TEXT if resolved_a is not None else _SOURCE_NONE
    if not left.matched and slots.period_a is not None:
        window_a = slot_window(slots.period_a, slots.period_a_end)
        resolved_a = _Resolved(window_a, window_kind(window_a))
        a_source = _SOURCE_CARRY

    # period_b: this turn's right-hand phrase; otherwise, when the text named
    # no left-hand period either, last turn's comparison carries (D62). A
    # left-hand phrase with no comparison names a single period, so nothing
    # is carried here and the pipeline clears the slot.
    resolved_b = right.resolved
    if right.prior_year_relative and resolved_a is not None:
        resolved_b = _shift_back_one_year(resolved_a)
    b_source = _SOURCE_TEXT if resolved_b is not None else _SOURCE_NONE
    if resolved_b is None and not left.matched and slots.period_b is not None:
        window_b = slot_window(slots.period_b, slots.period_b_end)
        resolved_b = _Resolved(window_b, window_kind(window_b))
        b_source = _SOURCE_CARRY
```

(g) Module docstring: in the `* **Carry-over**` bullet replace the text from `when the text names no period on the left` to the end of the bullet with:

```
  when the text names no period on the left, ``period_a`` is last turn's
  stored WINDOW (``slots.period_a``..``slots.period_a_end``, read through
  ``period_labels.slot_window`` so a slot with no end is still the month
  containing its start) with source ``"carry"``; a new left-hand phrase
  replaces it (source ``"text"``). ``period_b`` is carried the same way
  (source ``"carry"``) when the text names no left-hand period and no
  resolvable comparison, so "why did GP drop?" after "July vs June" still
  compares. A left-hand phrase alone ("GP for August") reports no
  comparison, and the pipeline clears the slot (decision D62).
```

Delete the whole `CARRIED GRANULARITY -- KNOWN v1 LIMITATION` section (heading, underline and its paragraph). Add `compare to` / `compared with` wherever the docstring lists the comparison connectives.

- [ ] **Step 4: Run the parser tests, then the whole offline suite (the connective change can touch other parsing tests)**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_parsing_periods.py -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t3-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t3-green.txt
env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t3-offline.txt 2>&1; tail -15 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t3-offline.txt
```

Expected: parser tests all PASS. Offline suite: exactly one known failure is acceptable at this point, `tests/test_parsing_hinter_pipeline.py::test_period_b_slot_clears_when_the_next_turn_asks_no_comparison` (its contract is replaced in Task 4). Any other failure must be understood and fixed before committing; report it.

- [ ] **Step 5: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/parsing/period_parser.py tests/test_parsing_periods.py
git add poseidon/core/parsing/period_parser.py tests/test_parsing_periods.py
git commit -q -F - <<'EOF'
fix(parsing): carry the stored period window and the comparison (C03, D62)

The carry path reads slots.period_a..period_a_end through
period_labels.slot_window instead of rebuilding a month, so a carried year
stays a year; period_b carries the same way until a single period or a new
comparison is named. "compare to / compared with" join the connectives so
"compare to May instead" replaces only the comparison side. A carried day
range shifts by a year like the other shapes.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 4: The pipeline write rule and carry of the end fields

**Files:**
- Modify: `backend/poseidon/core/parsing/carry.py:42-85` (`SlotUpdates`, `apply_carry`)
- Modify: `backend/poseidon/core/parsing/pipeline.py:225-260` (docstring "Slot write-back"), `:790-800` (the `SlotUpdates(...)` construction)
- Test: `backend/tests/test_parsing_normalize_carry.py`, `backend/tests/test_parsing_hinter_pipeline.py`

**Interfaces:**
- Consumes: `PeriodParse.a_source`/`b_source` (Task 3), `ConversationSlots.period_a_end`/`period_b_end` (Task 1).
- Produces: `SlotUpdates.period_a_end`, `SlotUpdates.period_b_end` (tri-state like the others, default `UNSET`); `apply_carry` folds them. The D62 write rule in `parse_turn`:

| `a_source` | `b_source` | A, A_end | B, B_end |
|---|---|---|---|
| any | `text` | per A rule | the window's start, end |
| `text` | `none` | the window's start, end | `None`, `None` |
| `carry` / `none` | `carry` / `none` | `UNSET`, `UNSET` | `UNSET`, `UNSET` |

(A's own rule is unchanged: `text` replaces, else `UNSET`.)

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_parsing_normalize_carry.py`, find the existing `apply_carry` tests and add after them:

```python
def test_apply_carry_folds_the_period_window_ends_under_the_tri_state_rule():
    """D62: the end fields are tri-state like their starts. UNSET keeps,
    None clears, a date replaces -- and the starts and ends are
    independent fields, folded one by one."""
    prior = ConversationSlots(
        period_a=date(2025, 1, 1),
        period_a_end=date(2026, 1, 1),
        period_b=date(2024, 1, 1),
        period_b_end=date(2025, 1, 1),
    )

    kept = apply_carry(prior, SlotUpdates())
    assert kept.period_a_end == date(2026, 1, 1)
    assert kept.period_b_end == date(2025, 1, 1)

    cleared = apply_carry(prior, SlotUpdates(period_b=None, period_b_end=None))
    assert cleared.period_a_end == date(2026, 1, 1)
    assert cleared.period_b is None
    assert cleared.period_b_end is None

    replaced = apply_carry(
        prior, SlotUpdates(period_a=date(2026, 4, 1), period_a_end=date(2026, 5, 1))
    )
    assert replaced.period_a == date(2026, 4, 1)
    assert replaced.period_a_end == date(2026, 5, 1)
    assert replaced.period_b_end == date(2025, 1, 1)
```

(Add `from datetime import date` and the `ConversationSlots`/`SlotUpdates`/`apply_carry` imports if the file does not already import them.)

In `backend/tests/test_parsing_hinter_pipeline.py`, replace `test_period_b_slot_clears_when_the_next_turn_asks_no_comparison` with these four tests (same place in the file):

```python
def test_period_b_slot_carries_when_the_next_turn_asks_no_comparison():
    """D62: a comparison is carried until something replaces it. Run as a
    real two-turn sequence so the carried value can only come from turn
    1's own write-back, never from a hand-built prior."""
    client = FakeDataClient()
    first = parse_turn("gp april 2026 vs march 2026", NO_SLOTS, REF, client)
    assert first.period_b == _month(2026, 3)
    assert first.slots.period_b == date(2026, 3, 1)
    assert first.slots.period_b_end == date(2026, 4, 1)

    second = parse_turn("and volume?", first.slots, REF, client)
    assert second.period_a == _month(2026, 4)
    assert second.period_b == _month(2026, 3)
    assert second.slots.period_a == date(2026, 4, 1)
    assert second.slots.period_a_end == date(2026, 5, 1)
    assert second.slots.period_b == date(2026, 3, 1)
    assert second.slots.period_b_end == date(2026, 4, 1)


def test_a_single_named_period_clears_the_carried_comparison():
    """D62: "GP for August" after "July vs June" is a question about August
    alone -- A is replaced and B is cleared, ends included."""
    client = FakeDataClient()
    first = parse_turn("gp april 2026 vs march 2026", NO_SLOTS, REF, client)
    second = parse_turn("gp for may 2026", first.slots, REF, client)

    assert second.period_a == _month(2026, 5)
    assert second.period_b is None
    assert second.slots.period_a == date(2026, 5, 1)
    assert second.slots.period_a_end == date(2026, 6, 1)
    assert second.slots.period_b is None
    assert second.slots.period_b_end is None


def test_a_comparison_side_only_replaces_period_b_and_keeps_period_a():
    client = FakeDataClient()
    first = parse_turn("gp april 2026 vs march 2026", NO_SLOTS, REF, client)
    second = parse_turn("compare to february 2026 instead", first.slots, REF, client)

    assert second.period_a == _month(2026, 4)
    assert second.period_b == _month(2026, 2)
    assert second.slots.period_a == date(2026, 4, 1)
    assert second.slots.period_a_end == date(2026, 5, 1)
    assert second.slots.period_b == date(2026, 2, 1)
    assert second.slots.period_b_end == date(2026, 3, 1)


def test_an_unresolvable_comparison_side_keeps_the_carried_comparison_slot():
    """"compared to acme corp" names a connective but no period: the carried
    comparison is NOT a silent reset (the same principle customer/port carry
    already follows for an unresolved mention)."""
    client = FakeDataClient()
    first = parse_turn("gp april 2026 vs march 2026", NO_SLOTS, REF, client)
    second = parse_turn("compared to acme corp", first.slots, REF, client)

    assert second.period_b == _month(2026, 3)
    assert second.slots.period_b == date(2026, 3, 1)
    assert second.slots.period_b_end == date(2026, 4, 1)


def test_a_full_year_carries_as_a_full_year_into_a_bare_follow_up():
    """The C03 headline case: "for 2025 by month" then "then by port" must
    still be about the whole of 2025, not January."""
    client = FakeDataClient()
    first = parse_turn("gp for 2025 by month", NO_SLOTS, REF, client)
    assert first.period_a == PeriodWindow(date(2025, 1, 1), date(2026, 1, 1))
    assert first.slots.period_a_end == date(2026, 1, 1)

    second = parse_turn("then by port", first.slots, REF, client)
    assert second.period_a == PeriodWindow(date(2025, 1, 1), date(2026, 1, 1))
    assert second.slots.period_a == date(2025, 1, 1)
    assert second.slots.period_a_end == date(2026, 1, 1)
```

Also update the module docstring bullet `the ``period_b`` slot clearing when a follow-up turn asks no comparison` to `the D62 period write rule (a comparison carries; a single named period clears it; a comparison side alone replaces it)`.

- [ ] **Step 2: Run to verify failure**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_parsing_normalize_carry.py tests/test_parsing_hinter_pipeline.py -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t4-red.txt 2>&1; grep -E "FAILED|passed|failed|Error" ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t4-red.txt | tail -12
```

Expected: the carry test fails with `TypeError: SlotUpdates.__init__() got an unexpected keyword argument 'period_b_end'`; the pipeline tests fail because `slots.period_b` is `None` on the follow-up (today's clear rule) and `period_a_end` is never written. Right failures.

- [ ] **Step 3: Implement**

In `backend/poseidon/core/parsing/carry.py`, `SlotUpdates` gains two fields (append after `period_b`) and its docstring's first line changes from "the four slots" to "the six slot fields":

```python
    customer: str | None | _UnsetType = UNSET
    port: str | None | _UnsetType = UNSET
    period_a: date | None | _UnsetType = UNSET
    period_b: date | None | _UnsetType = UNSET
    period_a_end: date | None | _UnsetType = UNSET
    period_b_end: date | None | _UnsetType = UNSET
```

`apply_carry` folds them:

```python
    return replace(
        slots,
        customer=_carried(slots.customer, updates.customer),
        port=_carried(slots.port, updates.port),
        period_a=_carried(slots.period_a, updates.period_a),
        period_b=_carried(slots.period_b, updates.period_b),
        period_a_end=_carried(slots.period_a_end, updates.period_a_end),
        period_b_end=_carried(slots.period_b_end, updates.period_b_end),
    )
```

(Update `apply_carry`'s docstring "Only the four slots" to "Only the six slot fields".)

In `backend/poseidon/core/parsing/pipeline.py`, replace the `updates = SlotUpdates(...)` construction with:

```python
    # Slot write-back for periods (decision D62; see "Slot write-back" in
    # the module docstring). A: a left-hand phrase replaces, anything else
    # carries. B: a right-hand phrase replaces; a left-hand phrase ALONE
    # (a single named period) clears; otherwise carries.
    a_named = period_parse.a_source == "text"
    b_named = period_parse.b_source == "text"
    if b_named:
        period_b_update = period_parse.period_b.start
        period_b_end_update = period_parse.period_b.end
    elif a_named:
        period_b_update = None
        period_b_end_update = None
    else:
        period_b_update = UNSET
        period_b_end_update = UNSET

    updates = SlotUpdates(
        customer=customer_plan.slot_update,
        port=port_plan.slot_update,
        period_a=period_parse.period_a.start if a_named else UNSET,
        period_a_end=period_parse.period_a.end if a_named else UNSET,
        period_b=period_b_update,
        period_b_end=period_b_end_update,
    )
```

Rewrite the two `period_a`/`period_b` bullets of the module docstring's "Slot write-back" section to:

```
* ``period_a``/``period_a_end``: ``a_source == "text"`` REPLACES both with
  the window's start and exclusive end (even when the window is also
  flagged ``period_unavailable``: the user named a period this turn, in
  their own words, and persisting it is more transparent than silently
  reverting to something they did not ask for); ``"carry"``/``"none"`` are
  UNSET, since neither reflects a NEW value from this turn's text. An
  explicit-but-unresolvable phrase (year-to-date at a January 1 reference)
  therefore leaves the slot untouched rather than clearing it -- an issue
  is not a silent reset.
* ``period_b``/``period_b_end`` (decision D62): ``b_source == "text"``
  replaces both; ``a_source == "text"`` with no comparison -- a SINGLE
  named period -- CLEARS both to ``None``, because "GP for August" after
  "July vs June" is a question about August alone; anything else is UNSET,
  so a carried comparison survives "why did GP drop?" and an unresolvable
  "compared to acme corp". ``period_parser`` carries ``period_b`` under
  the same rule, so ``ParsedTurn.period_b`` and ``ParsedTurn.slots.
  period_b`` agree on every turn.
```

- [ ] **Step 4: Run the two test files, then the full offline suite**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_parsing_normalize_carry.py tests/test_parsing_hinter_pipeline.py -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t4-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t4-green.txt
env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t4-offline.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t4-offline.txt
```

Expected: both files PASS; the offline suite is fully green (no failures, skips as before).

- [ ] **Step 5: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/parsing/carry.py poseidon/core/parsing/pipeline.py tests/test_parsing_normalize_carry.py tests/test_parsing_hinter_pipeline.py
git add poseidon/core/parsing/carry.py poseidon/core/parsing/pipeline.py tests/test_parsing_normalize_carry.py tests/test_parsing_hinter_pipeline.py
git commit -q -F - <<'EOF'
fix(parsing): D62 period write rule -- a comparison carries until replaced

SlotUpdates/apply_carry fold the window ends. parse_turn writes A's start
and end on a left-hand phrase, writes B on a right-hand phrase, clears B
only when a single period is named, and carries both otherwise. The
"period_b clears every turn" rule and its test are retired.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 5: The state block labels carried windows

**Files:**
- Modify: `backend/poseidon/core/llm/prompts.py:326-331` (docstring paragraph about slots being single dates), `:351-360` (`_render_slots` period lines)
- Test: `backend/tests/test_llm_prompts.py:461-476` (`test_render_state_block_carried_customer_and_period_golden`)

**Interfaces:**
- Consumes: `period_labels.slot_window`, `period_labels.label_window` (Task 2).
- Produces: slot period lines render as `Carried period A: <label> (<start>..<end>)`, e.g. `Carried period A: April 2026 (2026-04-01..2026-05-01)`. Everything else in the block is byte-identical. This is prompt text, so the pg suite runs in Step 4.

- [ ] **Step 1: Update the golden (failing first)**

In `backend/tests/test_llm_prompts.py`, replace `test_render_state_block_carried_customer_and_period_golden` with:

```python
def test_render_state_block_carried_customer_and_period_golden():
    """D62: carried periods render as a labelled window with its ISO bounds,
    so the router sees "2025" rather than a bare first-of-year date. Slots
    with no stored end (pre-Phase-17 rows, code-built slots) read as the
    month containing the start, exactly as the parser carries them."""
    slots = ConversationSlots(
        customer="MAERSK LINE",
        period_a=date(2026, 4, 1),
        period_b=date(2026, 5, 1),
    )

    result = render_state_block(slots, None)

    assert result == (
        "Mode: default\n"
        "Carried customer: MAERSK LINE\n"
        "Carried period A: April 2026 (2026-04-01..2026-05-01)\n"
        "Carried period B: May 2026 (2026-05-01..2026-06-01)"
    )


def test_render_state_block_carried_year_and_range_windows_golden():
    slots = ConversationSlots(
        period_a=date(2025, 1, 1),
        period_a_end=date(2026, 1, 1),
        period_b=date(2026, 10, 1),
        period_b_end=date(2026, 10, 12),
    )

    result = render_state_block(slots, None)

    assert result == (
        "Mode: default\n"
        "Carried period A: 2025 (2025-01-01..2026-01-01)\n"
        "Carried period B: October 1 to 11, 2026 (2026-10-01..2026-10-12)"
    )
```

- [ ] **Step 2: Run to verify failure**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_llm_prompts.py -k "carried" -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t5-red.txt 2>&1; tail -8 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t5-red.txt
```

Expected: both FAIL on the string comparison (`Carried period A: 2026-04-01` versus the labelled form). Right failure.

- [ ] **Step 3: Implement**

In `backend/poseidon/core/llm/prompts.py`, add the import:

```python
from poseidon.core.parsing.period_labels import label_window, slot_window
```

Replace the two period lines in `_render_slots`:

```python
    if slots.period_a is not None:
        lines.append(_render_slot_window("Carried period A", slots.period_a, slots.period_a_end))
    if slots.period_b is not None:
        lines.append(_render_slot_window("Carried period B", slots.period_b, slots.period_b_end))
```

and add next to `_render_window`:

```python
def _render_slot_window(label: str, start: date, end: date | None) -> str:
    """``<label>: <human label> (<start>..<end>)`` for a slot's stored window
    (decision D62). ``slot_window`` supplies the month fallback for a slot
    with no end, so this line and the parser's carry path can never show
    the router two different windows."""
    window = slot_window(start, end)
    return f"{label}: {label_window(window)} ({window.start.isoformat()}..{window.end.isoformat()})"
```

(Import `date` from `datetime` if the module does not already.) Replace the docstring paragraph that begins ``slots.period_a``/``period_b`` are single first-of-period DATES`` with:

```
    ``slots.period_a``/``period_b`` plus their ``_end`` fields (decision
    D62) are stored half-open windows; they render as a human label and
    the ISO bounds (``Carried period A: 2025 (2025-01-01..2026-01-01)``)
    through ``period_labels``, the same module the parser carries them
    with. ``parsed.period_a``/``period_b`` render as a bare ISO range, as
    before.
```

- [ ] **Step 4: Run the prompt tests, the offline suite and the pg suite**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_llm_prompts.py -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t5-green.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t5-green.txt
env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t5-offline.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t5-offline.txt
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t5-pg.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t5-pg.txt
```

Expected: all three green. If a pg test pins the old `Carried period A: 2026-04-01` text (none was found by grep at planning time, but the suite is the gate), update that assertion to the labelled form in the same commit and say so in the report.

- [ ] **Step 5: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check poseidon/core/llm/prompts.py tests/test_llm_prompts.py
git add poseidon/core/llm/prompts.py tests/test_llm_prompts.py
git commit -q -F - <<'EOF'
feat(prompts): state block labels carried period windows (D62)

Carried period A/B render as a human label with ISO bounds through
period_labels, so the router reads "2025" instead of a bare first-of-year
date and can never see a different window than the parser carries.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 6: Acceptance — parse, serialize, reload, reparse (spec §4.6)

**Files:**
- Create: `backend/tests/test_c03_period_carry.py`

**Interfaces:**
- Consumes: `parse_turn` (pipeline), `slots_to_json`/`slots_from_json` (history), `ConversationSlots`, `PeriodWindow`, `PeriodRange`. Every turn in these tests goes through the serializer round trip between turns, which is exactly what a real conversation does between two messages (the pg jsonb leg itself is proven in Task 1).

- [ ] **Step 1: Write the acceptance module**

```python
"""Phase 17 PR 0 acceptance (spec 2026-10-12 section 4.6, decision D62):
the C03 scenarios run through the REAL path a conversation takes between
two messages -- parse_turn -> slots_to_json -> slots_from_json -> parse_turn
-- so a carried year, a carried comparison and a replaced comparison are
proven across the serializer, not only inside the parser. The jsonb leg
itself is pinned in test_history_store.py (pg); this file is offline.

No customer names appear in the texts: customer resolution is not the
subject here, and keeping the texts period-only keeps every case about the
period rule alone.
"""

from dataclasses import dataclass, field
from datetime import date

from poseidon.core.chat.history import slots_from_json, slots_to_json
from poseidon.core.data.client import PeriodRange
from poseidon.core.data.specs import PeriodWindow
from poseidon.core.parsing.pipeline import ParsedTurn, parse_turn
from poseidon.core.skills.context import ConversationSlots

REF = date(2026, 10, 12)
AVAILABLE = PeriodRange(date(2024, 1, 1), date(2026, 9, 30))


@dataclass
class _Client:
    """A minimal offline DataClient: parse_turn only ever asks it for
    dimension values (none are named here) and the available range."""

    calls: list[str] = field(default_factory=list)

    def list_dimension_values(self, entity: str, column: str, search: str | None = None):
        self.calls.append(column)
        return []

    def available_periods(self, entity: str) -> PeriodRange:
        return AVAILABLE

    def run_metric_query(self, spec):  # pragma: no cover
        raise NotImplementedError

    def run_breakdown_query(self, spec):  # pragma: no cover
        raise NotImplementedError


def _month(year: int, month: int) -> PeriodWindow:
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return PeriodWindow(date(year, month, 1), date(end_year, end_month, 1))


def _reload(slots: ConversationSlots) -> ConversationSlots:
    """The round trip every real turn makes through the conversations.state
    jsonb column."""
    return slots_from_json(slots_to_json(slots))


def _conversation(*texts: str, start: ConversationSlots = ConversationSlots()) -> list[ParsedTurn]:
    slots = start
    turns = []
    for text in texts:
        turn = parse_turn(text, slots, REF, _Client())
        turns.append(turn)
        slots = _reload(turn.slots)
    return turns


def test_1_a_full_year_survives_then_by_port():
    first, second = _conversation("gp for 2025 by month", "then by port")

    assert first.period_a == PeriodWindow(date(2025, 1, 1), date(2026, 1, 1))
    assert second.period_a == PeriodWindow(date(2025, 1, 1), date(2026, 1, 1))
    assert second.slots.period_a == date(2025, 1, 1)
    assert second.slots.period_a_end == date(2026, 1, 1)


def test_2_a_report_comparison_survives_why_did_gp_drop():
    first, second = _conversation("gp in july 2026 vs june 2026", "why did gp drop?")

    assert (first.period_a, first.period_b) == (_month(2026, 7), _month(2026, 6))
    assert (second.period_a, second.period_b) == (_month(2026, 7), _month(2026, 6))
    assert second.slots.period_b == date(2026, 6, 1)
    assert second.slots.period_b_end == date(2026, 7, 1)


def test_3_compare_to_another_month_swaps_only_the_comparison():
    turns = _conversation(
        "gp in july 2026 vs june 2026", "why did gp drop?", "compare to may 2026 instead"
    )
    third = turns[-1]

    assert (third.period_a, third.period_b) == (_month(2026, 7), _month(2026, 5))
    assert third.slots.period_a == date(2026, 7, 1)
    assert third.slots.period_b == date(2026, 5, 1)
    assert third.slots.period_b_end == date(2026, 6, 1)


def test_4_a_single_period_drops_the_comparison():
    turns = _conversation("gp in july 2026 vs june 2026", "why did gp drop?", "gp for august 2026")
    third = turns[-1]

    assert third.period_a == _month(2026, 8)
    assert third.period_b is None
    assert third.slots.period_a == date(2026, 8, 1)
    assert third.slots.period_a_end == date(2026, 9, 1)
    assert third.slots.period_b is None
    assert third.slots.period_b_end is None


def test_5_a_quarter_survives_a_reload():
    first, second = _conversation("gp for q2 2026", "and margin")

    assert first.period_a == PeriodWindow(date(2026, 4, 1), date(2026, 7, 1))
    assert second.period_a == PeriodWindow(date(2026, 4, 1), date(2026, 7, 1))
    assert second.slots.period_a_end == date(2026, 7, 1)


def test_6_a_pre_phase_17_row_reloads_as_the_month_window():
    """A row written before this PR holds starts only. It carries the month
    containing the start -- the pre-PR behaviour -- and the next turn's
    write-back fills the ends in."""
    legacy = slots_from_json({"period_a": "2025-01-01", "period_b": None, "mode": "default"})
    assert legacy.period_a_end is None

    (turn,) = _conversation("then by port", start=legacy)

    assert turn.period_a == _month(2025, 1)
    assert turn.slots.period_a == date(2025, 1, 1)
    # Carried, not re-named this turn: the end stays unset until a phrase
    # names a period; the parser keeps reading it as the month.
    assert turn.slots.period_a_end is None
```

- [ ] **Step 2: Run the module**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/test_c03_period_carry.py -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t6.txt 2>&1; tail -8 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t6.txt
```

Expected: all six PASS on the first run (Tasks 1 to 5 built the behaviour; this module pins the end-to-end sequence). If any fails, that is a real defect in an earlier task: fix it there, re-run that task's tests, and report which task was wrong and why.

- [ ] **Step 3: Confirm the frozen corpus is untouched**

```bash
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider tests/contracts -q > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t6-contracts.txt 2>&1; tail -5 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/t6-contracts.txt
git status --short contracts/
```

Expected: contracts tests PASS and `git status` shows nothing under `contracts/`. If a contract test fails or a file changed, STOP and report the diff; do not set `POSEIDON_UPDATE_CONTRACTS`.

- [ ] **Step 4: Ruff and commit**

```bash
.venv/Scripts/python.exe -m ruff check tests/test_c03_period_carry.py
git add tests/test_c03_period_carry.py
git commit -q -F - <<'EOF'
test: C03 acceptance -- carried windows and comparisons survive the reload path

The spec section 4.6 sequences run parse_turn -> slots_to_json ->
slots_from_json -> parse_turn: a full year survives "then by port", a
July-vs-June comparison survives "why did GP drop?", "compare to May
instead" swaps only B, a single period drops B, a quarter survives, and a
pre-Phase-17 row still reads as its month.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 7: Decision log, architecture doc and verified figures

**Files:**
- Modify: `docs/architecture/00-overview.md` (the D-table after the D60 row, ~line 240; the "Still not folded into any design" paragraph, ~line 251)
- Modify: `docs/architecture/02-backend-skills.md:178` (the `period_parser` row)
- Modify: `CLAUDE.md` (the Commands section's verified figures)

**Interfaces:** none (documentation). The decision text is copied from the spec §2 verbatim.

- [ ] **Step 1: Add D61–D69 to the decision log**

In `docs/architecture/00-overview.md`, after the D60 row of the `Monthly performance reports (D34–D45)` table, add:

```markdown
| D61 | Phase 17 ships as four PRs: C03 fix, report in chat, metric_query extensions, providers. All eleven router cases of the reports design §7.6 are in scope across PRs A and B. (`2026-10-12-phase-17-report-in-chat-design.md`) | migration design §10 row 17 (adds the split) |
| D62 | `ConversationSlots` carries full period windows through two additive fields, `period_a_end` and `period_b_end`. The comparison (`period_b`) is carried until replaced: a single named period replaces A and clears B; a named comparison replaces both; a comparison-only phrase replaces B and keeps A. Orientation: slot A is the period being asked about, slot B is what it is compared to; a report seeds A = report month, B = prior month. | period parser's "B is never carried" contract; pipeline's "B clears every turn" rule |
| D63 | Report perspective is an explicit enum `customer \| supplier \| book`. It alone decides the office column (`CUSTOMER_TEAM_NAME` / `PRIMARY_SUPPLY_TEAM_OFFICE` / none) and the primary entity (`CUST_NM` / `SUPPLIER_NM` / `CUST_NM`). Supplier figures are our certified sales GP on deals that supplier supplied. The customer breakdown of a supplier report is a chat follow-up over pass-through, not a table in the report. | completes the deferred supplier-perspective requirement |
| D64 | Month to date exists only when asked for in words ("month to date", "mtd", "so far this month") or by naming the running month. Chips never offer it. It is chat-only; the payload records `window_kind: complete \| mtd` so Phase 18 refuses to save or email the latter. | refines D50 for ad-hoc chat |
| D65 | `report_run` lands in Phase 17 as Drizzle migration `0003`, shaped for Phase 18 (nullable definition and superseded-by columns). It is a shared org document (no row policy, app-role grants). Report bytes are served by Next.js route handlers reading Postgres behind the Sales-role check, linked from a plain anchor. | resolves C07 and the downgraded C08 for this phase |
| D66 | Charts are deferred to Phase 18. The payload has no `charts` key in `payload_version: 1`; adding it is additive. | reports design §4.9 timing |
| D67 | The grounding check is entity-scoped: each numeric token in a sentence must match a display string belonging to an entity named in that sentence (or a KPI / period-level figure when the sentence names none). The suite carries a must-reject fixture. | resolves C09 |
| D68 | Three live provider profiles: `bedrock` (unchanged), `anthropic` (new, Messages API over the existing HTTP client, no new SDK), `cortex` (REST Messages endpoint with native tool calling, built offline against recorded fixtures). One provider-parity contract test runs against all three plus the stub. Cortex's live proof is a handoff to the Snowflake-side owner. | D33's "Cortex not before SPCS"; D40's timing (was Phase 19) |
| D69 | An ad-hoc in-chat run, including its PDF, is readable by any `Poseidon:Sales` user. | follows D37, D44, D47 |
```

Then replace the paragraph beginning `**Still not folded into any design:** supplier-office reports must rank **suppliers**` with:

```markdown
**Supplier perspective:** folded into Phase 17's design as D63 (`docs/superpowers/specs/2026-10-12-phase-17-report-in-chat-design.md` §5).
```

Also add `D33`'s and `D40`'s rows a trailing note: append ` **(timing revised by D68)**` to the D40 row's decision text and ` **(Cortex availability revised by D68)**` to D33's.

- [ ] **Step 2: Update the parser row in doc 02**

In `docs/architecture/02-backend-skills.md` line 178, change `into `{period_a, period_b}` first-of-period ISO dates; **carry-over**: an unspecified side inherits from conversation state;` to `into `{period_a, period_b}` half-open windows (stored as first-of-period start plus exclusive end, D62); **carry-over**: an unspecified side inherits its stored window from conversation state, the comparison included, until a single period or a new comparison is named;`.

- [ ] **Step 3: Run every gate and record the figures**

From `backend/`:

```bash
env -u PERPLEXITY_API_KEY .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m 'not pg and not minio and not pdf and not router_live and not research_live' > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/final-offline.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/final-offline.txt
DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon .venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -m pg > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/final-pg.txt 2>&1; tail -3 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/final-pg.txt
.venv/Scripts/python.exe -m ruff check . > ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/final-ruff.txt 2>&1; tail -2 ../.superpowers/sdd/2026-10-12-phase-17-pr0-c03/final-ruff.txt
```

Expected: offline all passed (the count rises by the new tests; before this PR it was 1911 passed, 13 skipped, 119 deselected), pg all passed (337 passed, 1 skipped before; now one more), ruff clean. Copy the three exact summary lines into the task report.

- [ ] **Step 4: Refresh `CLAUDE.md`'s verified figures**

In `CLAUDE.md`, in the Commands block comments and the two paragraphs after it, replace the offline figure `1911 passed, 13 skipped, 119 deselected` and the pg figure `337 passed, 1 skipped` with the numbers from Step 3, and change every `verified 2026-10-12` that refers to these two suites to `verified 2026-10-12 (Phase 17 PR 0)`. Leave the frontend and web figures alone (not re-run here).

- [ ] **Step 5: Commit**

```bash
cd ..
git add docs/architecture/00-overview.md docs/architecture/02-backend-skills.md CLAUDE.md
git commit -q -F - <<'EOF'
docs: record D61-D69, fold the supplier perspective, refresh verified figures

Decision log gains Phase 17's nine decisions; the "still not folded" note
becomes a pointer to D63; doc 02's period_parser row describes stored
windows and comparison carry; CLAUDE.md carries this PR's suite figures.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
git log --oneline -8
```

---

## Self-review (done at planning time)

**Spec coverage (§4):** 4.1 slots → Task 1; 4.2 parser → Task 3; 4.3 write rule → Task 4; 4.4 orientation → no code in PR 0 (it is a rule the report seeding in PR A follows; the labels here avoid bare A/B in prose only where this PR renders them, Task 5); 4.5 state block → Task 5; 4.6 acceptance → Task 6 (cases 1 to 6) plus Task 1's pg round trip; §12 decision log → Task 7. The spec's §4.1 wording says `slots_from_json` reconstructs the month window; this plan puts that fallback in `period_labels.slot_window` instead so the serializer stays a faithful inverse (round-trip equality tests) and the behaviour is identical. The spec is updated to say so in Task 7's commit if the reviewer wants the text aligned; the behaviour matches.

**Placeholders:** none; every step carries its code and expected output.

**Type consistency:** `slot_window(start: date, end: date | None) -> PeriodWindow`, `window_kind(window) -> WindowKind`, `label_window(window) -> str` are used with those exact names in Tasks 3 and 5. `SlotUpdates.period_a_end` / `period_b_end` in Task 4 match `ConversationSlots.period_a_end` / `period_b_end` in Task 1. `b_source` vocabulary `{"text","carry","none"}` is asserted in Task 3 and consumed in Task 4.

**Review Focus:** all five lines have the named tests in Tasks 1, 2, 3 and 6.

**Scope:** one subsystem (period carry), seven tasks, one branch, one PR.
