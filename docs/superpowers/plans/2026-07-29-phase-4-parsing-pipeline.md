# Poseidon Phase 4: Deterministic Parsing Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic pre-LLM parsing pipeline (doc 02 §5): `normalize` → `period_parser` (date phrases, carry-over, availability validation) → `customer_resolver` (3-tier fuzzy against the customer dimension) → `skill_hinter` (advisory candidate ranking) → a `ParsedTurn` object with slot carry semantics (omit/carry, explicit-clear, replace) and the exact-value pass-through store. **No LLM anywhere. All core tests offline; live resolution goldens pg-marked.**

**Architecture:** Semantics ported from mom-comparison's rules 10–12 (carry-over, availability membership) and TM1's `ConversationBuffer`/dimension-service patterns — reimplemented as pure Python (the whole point: the judgment layer becomes testable code). Pipeline lives in `backend/poseidon/core/parsing/`; consumes `DataClient` (dimension values, available periods) and the ontology (dimension whitelist); produces `ParsedTurn` which Phase 5 attaches to router requests and Phase 6 persists in conversation state. `ConversationSlots` grows by DEFAULTED fields only (P3 final-review rule: never reshape).

**Tech Stack:** Existing backend. New dep: `rapidfuzz>=3.9`. All stdlib dates (no dateutil — deterministic, explicit grammar).

## Global Constraints

- **No LLM, no network beyond DataClient.** Pure functions; a single `parse_turn(...)` orchestrator.
- **Determinism:** same inputs (message, slots, reference date, dimension values, available periods) → identical `ParsedTurn`. No wall clock — "today" is an explicit `reference_date: date` parameter threaded from the caller (tests pin it; Phase 6 passes date.today()).
- Slot carry semantics verbatim from doc 02 §5 (TM1 ConversationBuffer): omitted → carry previous; explicit clear phrase → clear; new value → **replace, never merge**. Encoded once, table-tested.
- Period parsing: resolved periods are **validated against `DataClient.available_periods`**; a miss produces a structured `ParseIssue` carrying the available range (message text pinned) — never a silent empty window. Half-open `PeriodWindow` everywhere; `PeriodRange.end` is INCLUSIVE (add 1 day when converting — the inherited P2 note).
- Customer resolution tiers and thresholds are law: exact (casefold) → token-set → fuzzy; `>= 0.80` auto-apply, `0.60–0.80` → clarification candidates (max 3, scored order), `< 0.60` → no match. Thresholds are named constants; rapidfuzz `token_set_ratio`/100.0.
- The resolver matches ONLY against `DataClient.list_dimension_values(entity, "CUST_NM", search=None)` values (capped list is fine — synthetic has 40); never invents names. Ports resolve identically against `LOC_NM` when a port phrase is detected.
- `skill_hinter` is ADVISORY: a ranked list of `(skill_id, score)` + mode hints from a keyword lexicon in a data file (`parsing/lexicon.py` constants — not YAML; YAGNI); it never dispatches.
- Offline-by-default: core suites use in-test fake DataClients with fixed dimension/period data; pg-marked goldens resolve against the seeded db (misspelled "Northstar Linez" → auto-apply; "meridian" → multi-candidate clarification since seed has several Meridian *; etc.).
- Tests committed; ruff clean; conventional commits on `phase-3-8-overnight`; trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` on every commit.
- Do not touch frontend/, legacy root, docs/architecture/, mock_chat, dev_runner, or Phase-2/3 modules except: `context.py` (ConversationSlots defaulted-field additions ONLY, Task 1) and `pyproject.toml` (rapidfuzz).

## File Map

```
backend/poseidon/core/parsing/
  __init__.py
  types.py          # ParsedTurn, ParseIssue, ResolvedEntity, CandidateSkill, PassThroughValue
  normalize.py      # normalize(text) -> str
  carry.py          # apply_carry(slots, updates: SlotUpdates) -> ConversationSlots
  period_parser.py  # parse_periods(text, slots, reference_date, available: PeriodRange) -> PeriodParse
  customer_resolver.py  # resolve(name_phrase, values) -> Resolution (+ port variant via param)
  skill_hinter.py   # hint(text, slots) -> list[CandidateSkill]
  lexicon.py        # keyword→skill/mode tables (constants)
  pipeline.py       # parse_turn(message, slots, reference_date, data: DataClient, entity=...) -> ParsedTurn
backend/poseidon/core/skills/context.py   # ConversationSlots += region/topic/pass_through (defaulted)
backend/tests/test_parsing_normalize_carry.py
backend/tests/test_parsing_periods.py
backend/tests/test_parsing_resolver.py
backend/tests/test_parsing_hinter_pipeline.py   # incl. pg-marked live goldens
backend/pyproject.toml                    # + rapidfuzz>=3.9
```

---

### Task 1: Types, normalize, ConversationSlots growth, carry semantics

**Files:** `parsing/{__init__,types,normalize,carry}.py`; `context.py` (additive); test `test_parsing_normalize_carry.py`.

**Interfaces (exact):**

```python
# context.py — ConversationSlots gains ONLY (frozen, all defaulted):
    region: str | None = None
    topic: str | None = None
    pass_through: tuple[tuple[str, str], ...] = ()   # ((label, exact_value), ...) — doc 02 §5 value pass-through

# types.py
@dataclass(frozen=True)
class ParseIssue:
    code: str            # "period_unavailable" | "customer_ambiguous" | "customer_unknown"
    message: str         # human text, pinned in tests
    candidates: tuple[str, ...] = ()

@dataclass(frozen=True)
class ResolvedEntity:
    value: str           # the certified dimension value
    source_text: str
    confidence: float    # 1.0 exact/token, else fuzzy score
    tier: str            # "exact" | "token" | "fuzzy"

@dataclass(frozen=True)
class CandidateSkill:
    skill_id: str
    score: float

@dataclass(frozen=True)
class ParsedTurn:
    normalized_text: str
    slots: ConversationSlots          # POST-carry slots (what the turn resolved to)
    period_a: PeriodWindow | None
    period_b: PeriodWindow | None     # comparison window when the text asks for one
    customer: ResolvedEntity | None
    port: ResolvedEntity | None
    hints: tuple[CandidateSkill, ...]
    issues: tuple[ParseIssue, ...)    # empty = clean parse

# carry.py
@dataclass(frozen=True)
class SlotUpdates:                    # tri-state per slot: UNSET sentinel = omit/carry
    customer: str | None | _Unset = UNSET
    port: str | None | _Unset = UNSET
    period_a: date | None | _Unset = UNSET
    period_b: date | None | _Unset = UNSET
    # (implement _Unset as a module-level sentinel class/instance; None means EXPLICIT CLEAR)
def apply_carry(slots: ConversationSlots, updates: SlotUpdates) -> ConversationSlots: ...
```

`normalize`: strip, NFC, collapse internal whitespace runs to one space, preserve case (resolvers casefold themselves).

- [ ] Tests first: normalize table (unicode NFC case, tabs/newlines, leading/trailing); the carry TRUTH TABLE — for each slot: (UNSET, prior)→prior; (None, prior)→None; (new, prior)→new; never-merge proven with pass_through tuples (replace wholesale). RED → implement → GREEN → ruff. **Commit** — `feat(parsing): parsed-turn types, normalize, and slot carry semantics`

---

### Task 2: Period parser

**Files:** `parsing/period_parser.py`; test `test_parsing_periods.py`.

**Interface:**

```python
@dataclass(frozen=True)
class PeriodParse:
    period_a: PeriodWindow | None
    period_b: PeriodWindow | None
    a_source: str        # "text" | "carry" | "none"
    b_source: str
    issue: ParseIssue | None    # period_unavailable

def parse_periods(text: str, slots: ConversationSlots, reference_date: date,
                  available: PeriodRange) -> PeriodParse: ...
```

**Grammar (explicit, tested case by case — implement with regexes over the normalized casefolded text; NO dateutil):**
- Month-year: "april 2026", "apr 2026", "2026-04" → that month's window.
- Bare month: "in april" → that month in `reference_date`'s year if start <= reference month else previous year (i.e., most recent occurrence not in the future) — document and pin. AMBIGUOUS-MONTH GATE (fix round 1, post-review): a bare-month match whose month word is "may" (`AMBIGUOUS_BARE_MONTHS = frozenset({"may"})`, module-level extension point) is accepted ONLY when the immediately preceding word token (scanning back over whitespace/punctuation, case-insensitive) is one of {in, for, during, of, since, through}; otherwise the match is rejected and the text falls through to carry/none exactly as if no period phrase were present. Month-year ("may 2026") and all other months are unaffected. Deliberate cost: "show me may" no longer parses (recoverable non-parse beats silent wrong answer). Known residual: month-named customers ("May Shipping") still collide when preceded by a preposition — pipeline-level customer-span masking is Task 4's concern.
- Year: "2025", "last year" (reference year − 1 full calendar), "this year"/"ytd" ([Jan 1, reference_date) — Jan-1 reference produces the `period_unavailable`-style issue? NO: YTD with reference Jan 1 is an EMPTY window → return issue code "period_unavailable" with message `f"no year-to-date range on {iso}"`? Keep simpler: reuse the P3 contract — treat as unavailable-with-message `"year-to-date has no days before January 2"`. Pin it.)
- Quarters: "q1 2026", "q3" (same most-recent rule as bare month).
- Comparison: "vs"/"versus"/"compared to" splits two period phrases → period_a (left), period_b (right); "vs last year" with a resolved period_a maps period_b to the same window shifted −1 year; "prior year vs ytd" → (prior full calendar year, YTD).
- Carry-over: NO period phrase in text → period_a from slots (source "carry") if present, else None ("none"); a NEW phrase replaces BOTH (period_b only set when the new text asks for comparison — never carried alone).
- Validation: any resolved window that does not INTERSECT `available` (inclusive-end converted properly) → `ParseIssue("period_unavailable", f"no data for {a_iso}..{b_iso} — available {avail_start}..{avail_end}")`, windows still returned (caller decides).

- [ ] Table-driven tests FIRST (≥25 cases covering every bullet + carry + validation, each an (input text, slots, reference, available) → exact PeriodParse); reference_date pinned to 2026-07-15 in most cases. RED → implement → GREEN → ruff. **Commit** — `feat(parsing): deterministic period parser with carry-over and availability validation`

---

### Task 3: Customer/port resolver

**Files:** `parsing/customer_resolver.py`; test `test_parsing_resolver.py`; pyproject `rapidfuzz>=3.9`.

**Interface:**

```python
@dataclass(frozen=True)
class Resolution:
    entity: ResolvedEntity | None
    candidates: tuple[str, ...]       # populated for the 0.60–0.80 band (each candidate individually >= 0.60; max 3, best-first)
    issue: ParseIssue | None          # customer_ambiguous (with candidates) | customer_unknown

AUTO_APPLY_THRESHOLD = 0.80
CANDIDATE_THRESHOLD = 0.60

def resolve(phrase: str, values: Sequence[str], kind: str = "customer") -> Resolution: ...
```

Tiers in order, first hit wins: (1) exact casefold equality → confidence 1.0 tier "exact"; (2) token-set equality (casefolded word sets equal, order/punctuation-insensitive) → 1.0 "token"; (3) `rapidfuzz.fuzz.token_set_ratio(phrase, value)/100` best-scored → ≥0.80 auto ("fuzzy"), 0.60–0.80 → candidates (each filtered to score >= 0.60 individually, then capped at 3, best-first) + `ParseIssue("customer_ambiguous", f"did you mean one of: {', '.join(top3)}?", candidates)`, <0.60 → `ParseIssue("customer_unknown", f"no {kind} matching {phrase!r}")`. Ties broken alphabetically (determinism). Issue codes stay `customer_*` for both kinds; `kind` only alters message wording — document.

- [ ] Tests FIRST: exact/casefold; token-set ("lines northstar" → Northstar Lines); fuzzy auto ("Northstar Linez"); candidate band with a crafted values list proving max-3 + ordering + tie-break; unknown; empty phrase → unknown; determinism (same inputs twice → equal Resolutions). RED → implement → GREEN → ruff. **Commit** — `feat(parsing): three-tier customer and port resolver`

---

### Task 4: Skill hinter, pipeline assembly, live goldens

**Files:** `parsing/{skill_hinter,lexicon,pipeline}.py`; test `test_parsing_hinter_pipeline.py`.

**Interfaces:**

```python
# lexicon.py — constants: KEYWORDS: dict[str, tuple[tuple[str, float], ...]] mapping
#   lexeme -> ((skill_id, weight), ...); MODE_HINTS for prospect/existing phrases.
def hint(text: str, slots: ConversationSlots) -> tuple[CandidateSkill, ...]: ...
#   sum weights of matched lexemes per skill; sort by (-score, skill_id); empty tuple when nothing matches.

def parse_turn(message: str, slots: ConversationSlots, reference_date: date,
               data: DataClient, entity: str = "MARINE_SALES_PLANNING_V") -> ParsedTurn: ...
```

`parse_turn` order: normalize → detect customer phrase (heuristic: quoted spans, "for/about/on <TitleCase run>", or slot carry) → resolve customer (and port when a port cue like "port of X"/"at X" matches a LOC_NM value) → parse periods (with `data.available_periods(entity)`) → apply carry (updates derived from resolutions: resolved value → replace; explicit "clear"/"reset" phrase → None; no mention → UNSET) → hints → assemble ParsedTurn with all issues collected. Phrase-detection heuristics must be conservative: an unmatched TitleCase run produces NO issue unless a customer cue-word was present (avoid false "unknown customer" on ordinary sentences) — pin both directions.

Lexicon starter (author fully): metric words (gp, gross profit, volume, tons, margin, win rate, top, breakdown, compare) → `data_qa.metric_query`; research words (news, article, esg, sustainability, market, competitor) → `research.web_research` (skill doesn't exist yet — hinter may reference future ids; document); brief words (brief, report, profile, overview + mode hints for "prospect"/"new customer"/"existing customer").

- [ ] Tests FIRST: hinter table (weights/order/empty); pipeline offline with fake DataClient (fixed 6 customers incl. the demo trio, fixed available range): the flagship case — message "Top GP customers for Port of Singapore in April 2026" + empty slots → period_a April, customer None (no cue), port Singapore resolved, hints lead with data_qa.metric_query, zero issues; carry case — prior slots customer Northstar + message "and for May?" → customer carried, period replaced; clarification case; unavailable-period case. `@pytest.mark.pg` goldens (established skip pattern): resolver against live seeded CUST_NM values ("Northstar Linez" auto-applies; "meridian" yields the multi-Meridian candidate band) and full `parse_turn` with `SyntheticDataClient`. RED → implement → GREEN offline + pg → ruff. **Commit** — `feat(parsing): skill hinter and parse_turn pipeline with live resolution goldens`

---

## Phase Gate (human validation)

1. Offline: `python -m pytest tests/test_parsing_*.py -v` — the four suites green, table counts visible.
2. Live: `python -m pytest -m pg -v` — resolution goldens against the seeded customers.
3. Spot-check REPL (commands in the report): `parse_turn("Top GP customers for Port of Singapore in April 2026", ...)` printed ParsedTurn shows the resolved port, April window, metric_query hint, no issues; the "Northstar Linez" misspelling resolves; "meridian" returns the candidate chips list.

## Self-Review Notes

- Doc-08 P4 coverage: normalize ✓, period_parser (carry + availability) ✓, customer_resolver (3-tier, thresholds) ✓, skill_hinter ✓, ParsedTurn ✓, slot carry semantics ✓, exact-value pass-through (slot field + carry rules; population happens when skills return ranked values — Phase 6 wires it) ✓ noted.
- Deliberate scope: no router attachment (P5), no persistence (P6/P10), hinter references future skill ids by design, ConversationSlots grown additively only.
- Type consistency: ParsedTurn/ParseIssue/ResolvedEntity flow T1→T4; PeriodWindow/PeriodRange semantics per the inherited P2 note.
