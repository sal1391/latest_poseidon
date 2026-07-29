# Poseidon Phase 2: Ontology Loader + Synthetic Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor the certified `ontology.yml` as the single source of truth, build the typed loader, the spec-based query builder (Postgres + Snowflake dialects, SQL snapshot-tested), and a seeded deterministic synthetic dataset served through `SyntheticDataClient` — so the six certified metrics and a Singapore-style breakdown return correct, reproducible values from local Postgres.

**Architecture:** Per `docs/architecture/04-data-ontology.md` — ontology drives everything: loader → typed objects; specs (never LLM-authored SQL) → one shared `query_builder` with a small dialect hook; `DataClient` protocol with the synthetic adapter first (Snowflake adapter is Phase 15 — only its *dialect* is built now). Synthetic data loads into the `synthetic` schema of the compose Postgres at seed time. Deterministic by construction: one committed default seed.

**Tech Stack:** Python 3.11+ (existing `backend/` project), PyYAML, pydantic v2 (already present), psycopg 3 (already present), Alembic migration 0002, pytest with a `pg` marker for DB-integration tests.

## Global Constraints

- **Ontology fidelity is absolute.** Entity, column, and metric names come verbatim from the vendored `ontology/ontology.yml` — including `"#_FIXTURES"`/`"#_INQUIRIES"` (YAML key parses to `#_FIXTURES`; the `quoted: true` attribute drives SQL double-quoting), `LOC_NM` (never PORT), metric SQL exactly as certified. Do not invent columns, joins (zero certified relationships — the two entities never join), or metrics.
- `AR_INVOICES` has `status: planned` — the loader models it but excludes it from the *active* inventory; nothing queries it.
- **No LLM anywhere in this phase.** Deterministic Python only.
- **Determinism:** the generator uses `random.Random(seed)` exclusively (no global `random`, no time-based anything); default seed `1391` committed. Same seed ⇒ byte-identical dataset checksum.
- **Offline-by-default suite:** plain `python -m pytest` must stay green with no database. DB-integration tests carry `@pytest.mark.pg` and skip (with reason) when `DATABASE_URL` is unreachable; the phase gate runs them against the compose db. Register the marker in `pyproject.toml` (no unknown-marker warnings).
- SQL is parameterized — specs render to `(sql: str, params: list)`; no string-interpolated literals. Dimension *columns* are whitelisted against the ontology; dimension *values* go in params.
- Snapshot tests pin rendered SQL **byte-for-byte** (both dialects) including validation-error strings, per the TM1 pattern.
- Tests committed, never gitignored; `ruff check` stays green; full backend suite green before every commit.
- Branch `phase-2-ontology-synthetic` off `main`; conventional commits; every commit body ends with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Do not touch `frontend/`, legacy root files, `docs/architecture/`, or the Phase-1 mock chat API. Compose file may ONLY gain the seed step in its backend command (Task 6).
- Windows dev: `backend/.venv/Scripts/python.exe -m ...` invocations; new deps added to `pyproject.toml` (`pyyaml>=6` to `dependencies`).
- DRY. YAGNI: no vector store, no Snowflake *client*, no skills — those are later phases.

## File Map (created/modified in this plan)

```
ontology/
  ontology.yml                    # vendored, pinned copy (verbatim from source)
  SOURCE.md                       # provenance: source path, date, sha256
  synthetic/profiles.yml          # per-entity generation profiles
backend/
  pyproject.toml                  # + pyyaml dep, + pg marker, + [tool.poseidon] nothing (unchanged otherwise)
  poseidon/core/ontology/__init__.py
  poseidon/core/ontology/models.py     # Ontology/Entity/Column/Metric (pydantic)
  poseidon/core/ontology/loader.py     # load() + cached get_ontology()
  poseidon/core/data/__init__.py
  poseidon/core/data/specs.py          # PeriodWindow, MetricQuerySpec, BreakdownQuerySpec, results
  poseidon/core/data/query_builder.py  # build_metric_query/build_breakdown_query/... + dialects
  poseidon/core/data/client.py         # DataClient protocol
  poseidon/core/data/synthetic_client.py
  poseidon/scripts/__init__.py
  poseidon/scripts/generate_synthetic.py   # pure row generation (no DB)
  poseidon/scripts/seed_synthetic.py       # idempotent load into Postgres `synthetic` schema
  poseidon/scripts/demo_query.py           # phase-gate CLI: prints the Singapore table
  migrations/versions/0002_synthetic_schema.py
  tests/test_ontology_loader.py
  tests/test_query_builder_snapshots.py
  tests/test_synthetic_generator.py
  tests/test_synthetic_client_pg.py        # @pytest.mark.pg integration
  tests/test_schema_ontology_drift.py
infra/docker-compose.yml          # backend command gains seed step (Task 6 only)
infra/runbooks/local.md           # + synthetic regeneration section (Task 6)
```

---

### Task 1: Vendor the ontology + typed loader + inventory contract test

**Files:**
- Create: `ontology/ontology.yml` (copy verbatim from `C:\Users\carlo\github\wfs_work_structure\app-workspace\ontology\ontology.yml`), `ontology/SOURCE.md`, `backend/poseidon/core/ontology/__init__.py`, `models.py`, `loader.py`
- Modify: `backend/pyproject.toml` (add `"pyyaml>=6"` to `dependencies`)
- Test: `backend/tests/test_ontology_loader.py`

**Interfaces (exact — later tasks depend on these):**

```python
# models.py (pydantic BaseModel throughout)
class Column(BaseModel):
    name: str                    # e.g. "#_FIXTURES" (unquoted form; YAML key)
    type: str
    role: Literal["identifier", "measure", "dimension", "date"]
    friendly: str
    quoted: bool = False         # True -> render as "..." in SQL
    agg: str | None = None
    unit: str | None = None
    description: str = ""

class Metric(BaseModel):
    name: str                    # e.g. "MARGIN"
    sql: str                     # certified expression, verbatim
    kind: str                    # sum | ratio | derived
    rule: str | None = None
    depends_on: list[str] = []

class NegativeConstraint(BaseModel):
    wrong: str
    right: str
    observed: bool = False

class Entity(BaseModel):
    name: str
    fqn: str
    status: str = "certified"    # "planned" for AR_INVOICES (presence == certified otherwise)
    grain: str = ""
    date_column: str | None = None
    columns: dict[str, Column] = {}
    metrics: dict[str, Metric] = {}
    negative_constraints: list[NegativeConstraint] = []
    business_rules: list[str] = []
    hierarchy_levels: list[str] = []     # W_MARINE_GL: the 7 CLASS level_columns
    dual_purpose_exclusion: str | None = None  # "COALESCE(CLASS4,'') <> 'Volume'"

    def dimensions(self) -> list[str]: ...   # column names with role == "dimension", file order
    def measures(self) -> list[str]: ...

class Ontology(BaseModel):
    version: int
    entities: dict[str, Entity]
    def active(self) -> dict[str, Entity]: ...   # excludes status == "planned"
    def entity(self, name: str) -> Entity: ...   # KeyError with a clear message if unknown/planned

# loader.py
def load(path: Path | None = None) -> Ontology: ...   # default path: repo ontology/ontology.yml
@lru_cache
def get_ontology() -> Ontology: ...
```

Parsing notes: the YAML's `hierarchies.level_columns` populates `hierarchy_levels`; `hierarchies.dual_purpose_measures[0].exclusion_clause` populates `dual_purpose_exclusion`; `metrics` entries get `name` injected from their key; unknown top-level keys (`apps`, `relationships`, provenance blocks) are ignored by the models (`model_config = ConfigDict(extra="ignore")` at the file-parse layer, but *within* an entity the loader must not silently drop `columns`/`metrics` — parse them explicitly).

- [ ] **Step 1: Vendor the file + provenance**

Copy the source YAML byte-for-byte to `ontology/ontology.yml`. Write `ontology/SOURCE.md`:

```markdown
# Vendored ontology — provenance

- Source: wfs_work_structure/app-workspace/ontology/ontology.yml
- Vendored: 2026-07-28
- SHA-256: <compute with: python -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" ontology/ontology.yml>
- Upgrade procedure: docs/architecture/04-data-ontology.md §6 — replace the file, run the
  loader contract test, review the diff it reports, update synthetic profiles if columns changed.
```

(Compute the real hash and inline it.)

- [ ] **Step 2: Write the failing contract test**

`backend/tests/test_ontology_loader.py` — this test IS the certification pin; exact expected values:

```python
from poseidon.core.ontology.loader import load


def test_inventory_is_pinned():
    ont = load()
    assert ont.version == 2
    assert sorted(ont.entities) == ["AR_INVOICES", "MARINE_SALES_PLANNING_V", "W_MARINE_GL_SOURCE_AI"]
    assert sorted(ont.active()) == ["MARINE_SALES_PLANNING_V", "W_MARINE_GL_SOURCE_AI"]

    msp = ont.entity("MARINE_SALES_PLANNING_V")
    assert msp.fqn == "SANDBOX.MCA.MARINE_SALES_PLANNING_V"
    assert msp.date_column == "LIFT_ETA_DATE"
    assert len(msp.columns) == 22
    assert sorted(msp.metrics) == [
        "GP", "MARGIN", "NUM_INQUIRIES", "NUM_LOST", "NUM_WON", "VOLUME", "WIN_RATE"]
    assert msp.metrics["MARGIN"].sql == "SUM(GROSS_PROFIT) / NULLIF(SUM(FIXED_TONS), 0)"
    assert msp.columns["#_FIXTURES"].quoted is True
    assert msp.columns["#_FIXTURES"].role == "measure"
    assert msp.columns["LOC_NM"].friendly == "Port"
    assert len(msp.negative_constraints) == 21
    assert all(nc.observed for nc in msp.negative_constraints)

    gl = ont.entity("W_MARINE_GL_SOURCE_AI")
    assert gl.date_column == "PERIOD_DATE"
    assert len(gl.columns) == 15
    assert sorted(gl.metrics) == ["MONETARY_TOTAL"]
    assert gl.hierarchy_levels == [
        "CLASS6_Calc", "CLASS6", "CLASS5", "CLASS4", "CLASS3", "CLASS2", "CLASS1"]
    assert gl.dual_purpose_exclusion == "COALESCE(CLASS4,'') <> 'Volume'"
    assert len(gl.negative_constraints) == 17


def test_planned_entity_is_not_queryable():
    ont = load()
    import pytest
    with pytest.raises(KeyError, match="planned"):
        ont.entity("AR_INVOICES")


def test_dimension_order_preserved():
    ont = load()
    dims = ont.entity("MARINE_SALES_PLANNING_V").dimensions()
    assert dims[0] == "CUST_NM" and "LOC_NM" in dims and len(dims) == 16
```

- [ ] **Step 2a: Run to verify failure** — `python -m pytest tests/test_ontology_loader.py -v` → FAIL (module missing). (`pip install -e ".[dev]"` after the pyproject edit.)

- [ ] **Step 3: Implement models + loader** per the Interfaces block. `entity()` raises `KeyError(f"{name} is a planned entity — not yet certified for querying")` for planned, `KeyError(f"unknown entity {name!r}")` otherwise.

- [ ] **Step 4: Run tests → PASS.** Full suite still green.

- [ ] **Step 5: Commit** — `feat(ontology): vendor certified ontology.yml with typed loader and inventory pin`

---

### Task 2: Specs + query builder, Postgres dialect, snapshot tests

**Files:**
- Create: `backend/poseidon/core/data/__init__.py`, `specs.py`, `query_builder.py`
- Test: `backend/tests/test_query_builder_snapshots.py`

**Interfaces (exact):**

```python
# specs.py
@dataclass(frozen=True)
class PeriodWindow:
    start: date          # inclusive
    end: date            # exclusive (half-open — renders as >= start AND < end)

@dataclass(frozen=True)
class MetricQuerySpec:
    entity: str
    metrics: tuple[str, ...]              # certified metric names
    period: PeriodWindow
    filters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # dim col -> values (OR within col, AND across)

@dataclass(frozen=True)
class BreakdownQuerySpec:
    entity: str
    metrics: tuple[str, ...]
    period: PeriodWindow
    group_by: str                         # dimension column
    order_by_metric: str                  # must be in metrics
    top_n: int = 5
    filters: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

# query_builder.py
class SpecValidationError(ValueError): ...   # message strings are part of the snapshot contract

def build_metric_query(spec: MetricQuerySpec, dialect: str) -> tuple[str, list]: ...
def build_breakdown_query(spec: BreakdownQuerySpec, dialect: str) -> tuple[str, list]: ...
def build_dimension_values_query(entity: str, column: str, search: str | None, dialect: str) -> tuple[str, list]: ...
def build_period_range_query(entity: str, dialect: str) -> tuple[str, list]: ...
```

**Rendering rules (the contract — snapshots pin all of this):**
- Validation before rendering, each with an exact message: unknown entity (loader's), unknown metric → `f"unknown metric {m!r} for entity {entity} — certified: {sorted(...)}"`, non-dimension filter/group_by column → `f"{col!r} is not a dimension of {entity}"`, unknown filter column, `order_by_metric` not in `metrics`, `top_n < 1`.
- Table name: dialect `postgres` → `synthetic.marine_sales_planning_v` (lowercased entity under the `synthetic` schema); dialect `snowflake` → the certified `fqn`.
- Date filter: `postgres` → `{date_col} >= %s AND {date_col} < %s`; `snowflake` with `W_MARINE_GL_SOURCE_AI` → `TO_DATE(PERIOD_DATE) >= %s AND TO_DATE(PERIOD_DATE) < %s` (the VARCHAR rule); snowflake with a DATE column → plain comparison.
- Quoted columns render as `"#_FIXTURES"` in BOTH dialects (valid in Postgres and required in Snowflake).
- Metric expressions are the certified `sql` verbatim, aliased: `SUM(FIXED_TONS) AS "VOLUME"`.
- Filters: `COALESCE({col}, 'Unknown') IN (%s, ...)` — the COALESCE business rule; values parameterized.
- Group-by: `COALESCE({col}, 'Unknown') AS {col}` in SELECT, `GROUP BY 1`, `ORDER BY "{order_by_metric}" DESC NULLS LAST` (postgres) / `ORDER BY "{order_by_metric}" DESC` (snowflake), `LIMIT %s` (postgres) / `LIMIT %s` (snowflake — same).
- `WIN_RATE` in the metric list appends `HAVING SUM("#_INQUIRIES") >= 5` (the certified small-sample guard) — in both query shapes.
- `W_MARINE_GL_SOURCE_AI` + `MONETARY_TOTAL` always appends the dual-purpose guard `AND COALESCE(CLASS4,'') <> 'Volume'` to the WHERE clause (from `dual_purpose_exclusion` — never hardcode it in the builder).
- Layout: single-line clauses joined by `\n` (`SELECT ...\nFROM ...\nWHERE ...`) — deterministic, snapshot-friendly.

- [ ] **Step 1: Write failing snapshot tests** — table-driven; each case asserts the EXACT sql string and params list. Cover at minimum (12 cases):

```python
import datetime as dt
import pytest
from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.data import query_builder as qb

APRIL = PeriodWindow(dt.date(2026, 4, 1), dt.date(2026, 5, 1))


def test_six_metric_summary_postgres():
    spec = MetricQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST"),
        period=APRIL)
    sql, params = qb.build_metric_query(spec, "postgres")
    assert sql == (
        'SELECT SUM(FIXED_TONS) AS "VOLUME", SUM(GROSS_PROFIT) AS "GP", '
        'SUM(GROSS_PROFIT) / NULLIF(SUM(FIXED_TONS), 0) AS "MARGIN", '
        'SUM("#_FIXTURES") AS "NUM_WON", SUM("#_INQUIRIES") AS "NUM_INQUIRIES", '
        'SUM("#_INQUIRIES") - SUM("#_FIXTURES") AS "NUM_LOST"\n'
        "FROM synthetic.marine_sales_planning_v\n"
        "WHERE LIFT_ETA_DATE >= %s AND LIFT_ETA_DATE < %s")
    assert params == [dt.date(2026, 4, 1), dt.date(2026, 5, 1)]
```

…plus, same pattern (write each expected string out fully in the test file — the implementer computes them from the rendering rules above and they become the frozen contract): top-5-ports-by-GP breakdown for one customer (postgres); the Singapore top-customers-by-GP breakdown (postgres — group_by CUST_NM, filter LOC_NM=("Singapore",)); WIN_RATE metric adding the HAVING guard; snowflake six-metric summary (FQN table name); snowflake GL MONETARY_TOTAL with the Volume-exclusion guard AND TO_DATE date filter; dimension-values query with and without search (ILIKE %s, postgres / ILIKE in snowflake too); period-range query for both entities (snowflake GL wraps TO_DATE, and MIN/MAX); and three error cases asserting `SpecValidationError` exact messages (unknown metric, non-dimension group_by e.g. `GROSS_PROFIT`, filter on unknown column e.g. the hallucinated `PORT_NM`).

- [ ] **Step 2: RED** — run, watch fail. **Step 3:** implement `specs.py` + `query_builder.py` per the rules. **Step 4: GREEN** + full suite + ruff. **Step 5: Commit** — `feat(data): spec-based query builder with postgres dialect and byte-pinned snapshots`

---

### Task 3: Snowflake dialect completion + DataClient protocol

**Files:**
- Create: `backend/poseidon/core/data/client.py`
- Modify: `backend/poseidon/core/data/query_builder.py` (only if Task 2 left snowflake gaps — Task 2's tests already pin the snowflake shapes; this task closes anything unimplemented)
- Test: extend `backend/tests/test_query_builder_snapshots.py`

**Interfaces:**

```python
# client.py
@dataclass(frozen=True)
class MetricResult:
    entity: str
    period: PeriodWindow
    values: dict[str, float | None]      # metric name -> value (None when no rows)

@dataclass(frozen=True)
class BreakdownRow:
    key: str                             # dimension value (post-COALESCE)
    values: dict[str, float | None]

@dataclass(frozen=True)
class BreakdownResult:
    entity: str
    group_by: str
    rows: list[BreakdownRow]             # ordered as returned

@dataclass(frozen=True)
class PeriodRange:
    start: date | None
    end: date | None                     # None/None when the entity has no rows

class DataClient(Protocol):
    def list_dimension_values(self, entity: str, column: str, search: str | None = None) -> list[str]: ...
    def available_periods(self, entity: str) -> PeriodRange: ...
    def run_metric_query(self, spec: MetricQuerySpec) -> MetricResult: ...
    def run_breakdown_query(self, spec: BreakdownQuerySpec) -> BreakdownResult: ...
```

- [ ] Steps: failing snapshot additions for any snowflake shape not already pinned (GL breakdown by CLASS3 under CLASS4='Volume' filter — the unit-mixed per-CLASS3 rule — and a GL dimension-values query) → RED → implement → GREEN → full suite → **Commit** — `feat(data): dataclient protocol and completed snowflake dialect`

---

### Task 4: Migration 0002 (synthetic schema) + schema↔ontology drift test

**Files:**
- Create: `backend/migrations/versions/0002_synthetic_schema.py`
- Test: `backend/tests/test_schema_ontology_drift.py`

Migration creates schema `synthetic` and both tables with EVERY certified column (lowercased identifiers are fine for Postgres — the builder emits unquoted uppercase names which Postgres folds to lowercase; quoted `"#_FIXTURES"` columns must be created quoted exactly). Types: VARCHAR→`text`, FLOAT/DOUBLE/NUMBER→`double precision`, DATE→`date`; GL `PERIOD_DATE` is `text` (the VARCHAR rule — but the **postgres** dialect treats it as… note: the synthetic GL table stores `PERIOD_DATE` as `date` instead, and the postgres dialect renders plain comparisons; the TO_DATE wrapping is a snowflake-dialect concern only. Record this in the migration docstring.) Indexes: `(LIFT_ETA_DATE)`, `(CUST_NM)`, `(LOC_NM)` on the sales table; `(PERIOD_DATE)`, `(CLASS4)` on GL.

The drift test is OFFLINE — it imports the migration module and asserts its column lists against `get_ontology()` (names and count for both entities), so an ontology upgrade that adds a column fails this test until the migration is extended:

```python
def test_migration_columns_match_ontology():
    from migrations.versions import _0002_synthetic_schema as mig  # module exposes SALES_COLUMNS/GL_COLUMNS dicts
    ont = load()
    assert set(mig.SALES_COLUMNS) == set(ont.entity("MARINE_SALES_PLANNING_V").columns)
    assert set(mig.GL_COLUMNS) == set(ont.entity("W_MARINE_GL_SOURCE_AI").columns)
```

(Name the revision file so it is importable — `0002_synthetic_schema.py` with a leading-underscore import alias via `importlib` if needed; the implementer picks the clean mechanism and keeps the test's intent.) Also extend the existing sqlite migrations test expectation if it asserts head revision. Note: schema creation must be dialect-aware — sqlite (used by `test_migrations.py`) has no schemas; guard with `if bind.dialect.name == "postgresql"` and make the migration a no-op on sqlite, documented in its docstring.

- [ ] RED (drift test) → migration → GREEN → full suite → **Commit** — `feat(db): synthetic schema migration pinned to the ontology`

---

### Task 5: Synthetic profiles + deterministic generator (offline)

**Files:**
- Create: `ontology/synthetic/profiles.yml`, `backend/poseidon/scripts/__init__.py`, `backend/poseidon/scripts/generate_synthetic.py`
- Test: `backend/tests/test_synthetic_generator.py`

**profiles.yml (author in full — the shape):**

```yaml
seed_default: 1391
window:
  # rolling: prior full calendar year + current year-to-date, relative to anchor
  anchor: 2026-07-01        # regenerate bumps this consciously; NOT "today" (determinism)
MARINE_SALES_PLANNING_V:
  rows: 24000
  customers:
    pool_size: 40           # first three MUST be, for demo continuity:
    named: [Northstar Lines, Blue Anchor Marine, Crestline Freight]
    # generator composes the rest: <prefix> <suffix> pools below
    prefixes: [Pacific, Meridian, Atlas, Harbor, Crescent, Aegean, Baltic, Nordic, Solent, Cormorant,
               Ligurian, Bosphorus, Andes, Coral, Monsoon, Trade Wind, Beacon, Compass, Anchorline, Halvard]
    suffixes: [Shipping, Marine, Bunkering, Navigation, Lines, Maritime, Carriers, Tankers, Freight, Shipmanagement]
  ports:                    # real port names; LOC_NM pool
    [Singapore, Rotterdam, Fujairah, Houston, Santos, Hamburg, Piraeus, Busan, Hong Kong,
     Gibraltar, Malta, Istanbul, Durban, Lagos, Colombo, Jebel Ali, Antwerp, New Orleans,
     Los Angeles, Panama City, Balboa, Algeciras, Zhoushan, Kaohsiung, Port Louis, Suez,
     Las Palmas, Vancouver, Callao, Tanjung Pelepas]
  ports_per_customer: [3, 8]        # uniform int range: each customer's affinity set
  tons_lognormal: {mu: 6.2, sigma: 0.6}     # ≈ 500t median, long tail
  margin_usd_per_ton: {low: 4.0, high: 14.0}  # per customer/port band, uniform draw
  win_rate_beta: {alpha: 5, beta: 4}          # per-customer Bernoulli p for "#_FIXTURES"
  suppliers: [Vitol, Peninsula, Bunker Holding, Minerva, TFG Marine, Monjasa, Integr8, Dan-Bunkering]
  deal_class: {TRADED: 0.7, INVENTORY: 0.3}
  ship_types: {Tanker: 0.4, Bulker: 0.35, Container: 0.25}
  # broker/team/office columns: small fixed pools with per-customer sticky assignment
W_MARINE_GL_SOURCE_AI:
  rows_per_period: 900
  periods: all_months_in_window
  volume_share: 0.10        # fraction of rows with CLASS4 = Volume (exercises the guard)
  hierarchy_paths:          # ~24 curated full paths (CLASS6_Calc..CLASS1, null-padded), incl.:
    - [Profit and Loss, Profit and Loss, Operating Income, Gross Profit, Trade GP, Cargo GP, null]
    - [Profit and Loss, Profit and Loss, Operating Income, Volume, Tonnage - Cargo, null, null]
    # ... (author the full curated list: P&L revenue/cost/OpEx branches with negative-amount
    #      cost paths, 2 Balance Sheet branches, >=3 Volume paths whose ACCOUNT starts "Tonnage-")
  amount_bands: {revenue: [50000, 900000], cost: [-800000, -40000], volume_tons: [500, 40000]}
```

**Generator contract (`generate_synthetic.py`):**

```python
@dataclass(frozen=True)
class Dataset:
    sales_rows: list[dict]     # keys == certified column names (exact, incl. "#_FIXTURES")
    gl_rows: list[dict]

def generate(seed: int | None = None, profiles_path: Path | None = None) -> Dataset: ...
def dataset_checksum(ds: Dataset) -> str:   # sha256 over canonical json (sorted keys, ISO dates)
```

Realism rules (from doc 04 §4, all deterministic from the seeded RNG): customer port-affinity sets; per-customer margin band and win rate; `"#_INQUIRIES"` = 1.0 every row; `"#_FIXTURES"` = Bernoulli(p_customer); GROSS_PROFIT = tons × margin (fixtures only; lost inquiries get tons > 0 but GP = 0? — **No**: real semantics: every row is an inquiry; only won rows carry delivered tons and GP; lost rows get FIXED_TONS = 0 and GROSS_PROFIT = 0 — record this rule in the module docstring); dates uniform over the window with mild month seasonality; broker/team/office sticky per customer; GL rows sample hierarchy paths, Volume rows draw tonnage amounts and `ACCOUNT` beginning `Tonnage-`, monetary rows honor the sign convention.

- [ ] **Step 1: failing tests:**

```python
def test_same_seed_same_checksum():
    a, b = generate(seed=1391), generate(seed=1391)
    assert dataset_checksum(a) == dataset_checksum(b)

def test_different_seed_differs():
    assert dataset_checksum(generate(seed=1391)) != dataset_checksum(generate(seed=2))

def test_rows_conform_to_ontology():
    ds = generate(seed=1391)
    ont = load()
    assert set(ds.sales_rows[0]) == set(ont.entity("MARINE_SALES_PLANNING_V").columns)
    assert set(ds.gl_rows[0]) == set(ont.entity("W_MARINE_GL_SOURCE_AI").columns)

def test_named_customers_present_with_singapore_activity():
    ds = generate(seed=1391)
    sing = [r for r in ds.sales_rows if r["LOC_NM"] == "Singapore"]
    assert {"Northstar Lines", "Blue Anchor Marine", "Crestline Freight"} <= {r["CUST_NM"] for r in sing}

def test_inquiry_fixture_semantics():
    ds = generate(seed=1391)
    assert all(r["#_INQUIRIES"] == 1.0 for r in ds.sales_rows)
    lost = [r for r in ds.sales_rows if r["#_FIXTURES"] == 0.0]
    assert lost and all(r["FIXED_TONS"] == 0.0 and r["GROSS_PROFIT"] == 0.0 for r in lost)

def test_gl_volume_rows_exercise_guard():
    ds = generate(seed=1391)
    vol = [r for r in ds.gl_rows if r["CLASS4"] == "Volume"]
    assert vol and all(str(r["ACCOUNT"]).startswith("Tonnage-") for r in vol)
    assert 0.05 < len(vol) / len(ds.gl_rows) < 0.15
```

Ensure the named-customers test is guaranteed by construction (the generator FORCES each named customer to include Singapore in its affinity set — encode that rule, don't hope the RNG lands it).

- [ ] RED → implement → GREEN → full suite → **Commit** — `feat(synthetic): seeded deterministic generator conforming to the certified schemas`

---

### Task 6: Seed loader + SyntheticDataClient + pg-gated integration tests + compose wiring + demo CLI

**Files:**
- Create: `backend/poseidon/scripts/seed_synthetic.py`, `backend/poseidon/core/data/synthetic_client.py`, `backend/poseidon/scripts/demo_query.py`
- Modify: `infra/docker-compose.yml` (backend command: `alembic upgrade head && python -m poseidon.scripts.seed_synthetic && uvicorn ...`), `infra/runbooks/local.md` (synthetic section), `backend/pyproject.toml` (`markers = ["pg: requires a reachable Postgres (DATABASE_URL)"]`)
- Test: `backend/tests/test_synthetic_client_pg.py`

**Contracts:**
- `seed_synthetic.py`: reads `DATABASE_URL`; skips (prints "already seeded (N rows); use --force") when the sales table has rows; `--force` truncates and reloads; loads via psycopg `COPY` or batched `executemany`; prints row counts and the dataset checksum. Idempotent by construction.
- `SyntheticDataClient(dsn)`: implements `DataClient` with the postgres dialect; `list_dimension_values` caps at 200, ordered, `ILIKE %search%`; connections short-lived per call (pooling arrives with a later phase).
- `demo_query.py` (the human gate): prints (a) the available period range, (b) the six-metric summary for prior-year and YTD side by side, (c) top-5 customers by GP for Port of Singapore, April 2026 — via the REAL client, formatted as plain aligned text tables.

**Integration tests (`@pytest.mark.pg`, module-level skip when the DB is unreachable within 2s):** seed a dedicated schema copy? No — tests run against the compose db AFTER `seed_synthetic` (document in module docstring); they regenerate expectations in pure Python:

```python
@pytest.mark.pg
def test_six_metrics_match_python_ground_truth():
    ds = generate(seed=1391)          # same committed seed the seeder used
    window = PeriodWindow(dt.date(2026, 4, 1), dt.date(2026, 5, 1))
    rows = [r for r in ds.sales_rows if window.start <= r["LIFT_ETA_DATE"] < window.end]
    expected_volume = sum(r["FIXED_TONS"] for r in rows)
    expected_gp = sum(r["GROSS_PROFIT"] for r in rows)
    res = client().run_metric_query(MetricQuerySpec(
        entity="MARINE_SALES_PLANNING_V",
        metrics=("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST"),
        period=window))
    assert res.values["VOLUME"] == pytest.approx(expected_volume)
    assert res.values["GP"] == pytest.approx(expected_gp)
    assert res.values["MARGIN"] == pytest.approx(expected_gp / expected_volume)
    assert res.values["NUM_INQUIRIES"] == pytest.approx(len(rows))
```

…plus: prior-year window equivalent; the Singapore top-5-by-GP breakdown compared to a pure-Python groupby of the generated rows (same ordering, same top-5 keys and values); `list_dimension_values("MARINE_SALES_PLANNING_V", "CUST_NM", search="north")` returns `["Northstar Lines"]`-containing list; `available_periods` matches the profile window; GL `MONETARY_TOTAL` for one month equals the python sum EXCLUDING Volume rows (proving the guard end-to-end).

- [ ] RED (pg tests skip offline — verify the skip reason renders; then with compose db up they fail against missing modules) → implement seeder + client + demo CLI → GREEN offline AND with the db (`python -m pytest -m pg` with compose up) → compose + runbook edits → full suite + ruff → **Commit** — `feat(synthetic): seeded postgres adapter with ground-truth-verified metrics`

---

## Phase Gate (human validation — after Task 6)

1. `docker compose -f infra/docker-compose.yml up --build` → backend logs show migration 0002, then "seeded synthetic: 24000 sales rows / N GL rows, checksum <hex>".
2. `cd backend && ./.venv/Scripts/python.exe -m poseidon.scripts.demo_query` (with `DATABASE_URL` pointing at localhost compose db) → prints the period range, the prior-year vs YTD six-metric summary, and **top-5 customers by GP for Port of Singapore, April 2026** — with Northstar Lines, Blue Anchor Marine, and Crestline Freight appearing in Singapore results (demo continuity with the Phase-1 mock).
3. `python -m pytest -m pg -v` → all integration tests pass against the seeded db (SQL results == pure-Python ground truth).
4. Re-run the seeder → "already seeded" skip proves idempotence; `--force --seed 2` then `--force --seed 1391` proves regeneration and determinism round-trip.
5. Offline check: `python -m pytest` with compose down stays green (pg tests skip with a clear reason).

## Self-Review Notes

- Spec coverage vs doc 08 Phase 2: vendored ontology + loader ✓ (T1), inventory contract test ✓ (T1), synthetic profiles + seeded generator ✓ (T5), `synthetic` schema at compose-up ✓ (T4/T6), SyntheticDataClient with dimension values/periods/metric/breakdown ✓ (T3/T6), query builder with snapshot tests on both dialect hooks ✓ (T2/T3), six-certified-metrics prior-year-vs-YTD + Singapore breakdown validation ✓ (T6 + gate).
- Type consistency: `PeriodWindow`/spec/result names match across T2/T3/T6; column-name fidelity enforced by T1's pin + T4's drift test + T5's conformance test.
- Deliberate scope notes: Snowflake *client* is Phase 15 (only the dialect is built); GL `PERIOD_DATE` is `date` in the synthetic store with TO_DATE confined to the snowflake dialect (documented in the migration); no pooling; no vector store; profiles anchor date is static by design (determinism) and bumping it is a conscious edit.
