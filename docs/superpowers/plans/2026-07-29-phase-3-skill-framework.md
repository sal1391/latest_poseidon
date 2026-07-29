# Poseidon Phase 3: Skill Framework + Deterministic Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the task/skill framework (folder law, registry discovery, router-facing contracts) with the first real skill — `data_qa.metric_query` — returning typed parts + proof blocks from the synthetic data, plus the `customer_insight` deterministic tools (metrics, top ports, PDF-to-MinIO) and a dev-only skill runner. **No LLM anywhere in this phase.**

**Architecture:** Per `docs/architecture/02-backend-skills.md` — tasks are vertical slices under `backend/poseidon/tasks/`; only `skills/*` are router-visible (subskills/tools never are); `SkillRegistry.discover()` walks task manifests at startup with fail-fast validation and produces `TOOL_SCHEMAS` (from pydantic `Args`) + `SKILL_FNS` (dispatch). Skills consume `SkillContext` (data client, artifact store, settings, conversation-state slots) and return `SkillResult` (typed `MessagePart` dicts per doc 01 §4 + deterministic `proof` lines). The doc's `backend/tasks/` path maps to `backend/poseidon/tasks/` (established package-mapping convention, `backend/README.md`).

**Tech Stack:** Existing backend project. New deps: `boto3>=1.34` (MinIO/S3 artifacts), `weasyprint>=62` + `markdown>=3.6` (PDF; container-only runtime — see Task 4). New pytest markers: `minio`, `pdf` (registered; skip cleanly when unavailable).

## Global Constraints

- **No LLM calls, imports, or placeholders-that-pretend.** `SkillContext` in this phase carries ONLY: `data` (DataClient), `artifacts` (ArtifactStore), `settings` (Settings), `state` (ConversationSlots). The `llm`/`tools`/`user`/`profile`/`run` fields of doc 02 §3 arrive with their owning phases — do NOT add them as None-typed stubs (YAGNI; adding a field later is trivial).
- Only `skills/*` are router-visible (decision D3). Phase 3 registers exactly ONE skill: `data_qa.metric_query`. The `customer_insight` task ships `enabled: false` in its `task.yml` (its TOOLS are built and unit-tested; its skills land Phase 8) — discovery must honor `enabled: false` by skipping registration while the tools remain importable for tests.
- Part `kind` strings come verbatim from doc 01 §4: `text`, `table`, `metric_grid`, `error` (this phase emits `table`, `metric_grid`, `text`). Parts are plain dicts `{"kind": str, "payload": dict}` — the backend has no renderer; the frontend contract governs shape.
- Proof blocks are deterministic provenance: entity FQN-in-context (synthetic schema name in local), spec summary (metrics, period ISO range, filters, group_by), row/value counts, data-backend name. Snapshot-pinned in tests.
- Every error surface is a `ProblemDetail` dict (`{"type","title","detail","status"}` — RFC-7807 shape) inside `SkillResult.error`; skills never raise to callers for business failures. Args validation failures are the DISPATCHER's job (structured error return), tested.
- Offline-by-default suite stays green (`python -m pytest` with pg/minio/pdf marked tests skipping, clear reasons). Live gates: `-m pg` and `-m minio` against the compose stack.
- Tests committed; ruff clean (backend scope); conventional commits on branch `phase-3-8-overnight`; every commit body ends with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Do not touch `frontend/`, legacy root files, `docs/architecture/`, the Phase-1 mock chat API (`mock_chat.py` untouched — the dev runner is a NEW router), or Phase-2 modules except where a task explicitly says so.
- Inherited cautions (Phase-2 notes): `PeriodRange.end` is INCLUSIVE — when a tool derives a window from it, use `end + 1 day` for `PeriodWindow.end`; in-container ontology resolution relies on the compose `/ontology` mount; postgres dialect is synthetic-store-only.
- Windows dev: `backend/.venv/Scripts/python.exe -m ...`; WeasyPrint will NOT import on the host (no Pango) — that is expected; `pdf`-marked tests skip on ImportError and run in the container.

## File Map

```
backend/poseidon/core/skills/
  __init__.py
  context.py        # SkillContext, ConversationSlots, ArtifactRef
  result.py         # SkillResult, ProblemDetail helpers, part constructors
  registry.py       # SkillRegistry: discover(), TOOL_SCHEMAS, SKILL_FNS, dispatch()
backend/poseidon/core/artifacts.py    # ArtifactStore (boto3, ensure-bucket, put/presign)
backend/poseidon/tasks/
  __init__.py
  _shared/__init__.py
  _shared/fragments.py                # PeriodArg, DimFilterArg pydantic fragments
  data_qa/
    task.yml                          # enabled: true
    __init__.py
    skills/__init__.py
    skills/metric_query/
      __init__.py
      schema.py                       # Args + SKILL_META
      skill.py                        # run(ctx, args) -> SkillResult
      tools/__init__.py
      tools/build_spec.py             # Args -> MetricQuerySpec | BreakdownQuerySpec
      tools/format_parts.py           # results -> table/metric_grid parts + proof lines
      tests/__init__.py
      tests/test_tools.py
      tests/test_skill.py             # pg-marked goldens (Singapore case)
  customer_insight/
    task.yml                          # enabled: false (skills arrive Phase 8)
    __init__.py
    skills/__init__.py
    skills/existing_customer_brief/
      __init__.py
      tools/__init__.py
      tools/fetch_metrics.py          # six metrics, prior-year vs YTD
      tools/fetch_top_ports.py        # top-5 ports by GP
      tools/build_brief_pdf.py        # markdown -> PDF -> ArtifactStore
      tests/__init__.py
      tests/test_tools.py             # pg-marked (fetchers) + pdf/minio-marked (pdf)
backend/poseidon/api/dev_runner.py    # POST /api/dev/skills/{skill_id}/run (local-only)
backend/tests/test_skill_registry.py
backend/tests/test_artifact_store.py  # minio-marked
backend/tests/test_dev_runner.py
backend/pyproject.toml                # + boto3, weasyprint, markdown; + minio/pdf markers
infra/backend.Dockerfile.dev          # + libpango/cairo libs (doc 07 §2 container contract)
infra/runbooks/local.md               # + dev-runner + markers section
```

---

### Task 1: Skill contracts + registry with fail-fast discovery

**Files:**
- Create: `backend/poseidon/core/skills/{__init__,context,result,registry}.py`, `backend/poseidon/tasks/__init__.py`, `backend/poseidon/tasks/_shared/{__init__,fragments}.py`
- Test: `backend/tests/test_skill_registry.py`

**Interfaces (exact — everything downstream depends on these):**

```python
# context.py
@dataclass(frozen=True)
class ConversationSlots:
    customer: str | None = None
    port: str | None = None
    period_a: date | None = None      # first-of-period ISO dates (doc 02 §5)
    period_b: date | None = None
    mode: str = "default"

@dataclass(frozen=True)
class ArtifactRef:
    name: str
    url: str          # presigned GET
    mime: str

@dataclass(frozen=True)
class SkillContext:
    data: DataClient
    artifacts: "ArtifactStore | None"   # None until a skill needs it (metric_query passes None)
    settings: Settings
    state: ConversationSlots = ConversationSlots()

# result.py
def text_part(markdown: str) -> dict: ...          # {"kind":"text","payload":{"markdown":...}}
def table_part(columns: list[str], rows: list[list]) -> dict: ...
def metric_grid_part(periods: dict, metrics: list[dict]) -> dict:
    # payload: {"periods": {"a": {...}, "b": {...}}, "metrics": [{"name","friendly","a","b","unit"}]}
def problem(status: int, title: str, detail: str, type_: str = "about:blank") -> dict: ...

@dataclass(frozen=True)
class SkillResult:
    ok: bool
    parts: list[dict] = field(default_factory=list)
    proof: list[str] = field(default_factory=list)
    artifacts: list[ArtifactRef] = field(default_factory=list)
    error: dict | None = None          # ProblemDetail when ok=False

# registry.py
class SkillDefinitionError(Exception): ...   # fail-fast at discovery

@dataclass(frozen=True)
class RegisteredSkill:
    skill_id: str                      # "data_qa.metric_query"
    args_model: type[BaseModel]
    fn: Callable[[SkillContext, BaseModel], SkillResult]
    description: str
    examples: list[str]

class SkillRegistry:
    @classmethod
    def discover(cls, tasks_pkg: str = "poseidon.tasks") -> "SkillRegistry": ...
    @property
    def tool_schemas(self) -> list[dict]: ...   # [{"name","description","input_schema"}]
    def dispatch(self, skill_id: str, raw_args: dict, ctx: SkillContext) -> SkillResult: ...
    def get(self, skill_id: str) -> RegisteredSkill: ...   # KeyError w/ clear message
    @property
    def skill_ids(self) -> list[str]: ...
```

Discovery rules (doc 02 §2, encode all): walk `poseidon/tasks/*/task.yml` (yaml: `id`, `title`, `description`, `enabled`); `enabled: false` → whole task skipped; for each `skills/<name>/` containing `schema.py`: import `schema` and `skill` modules; validate fail-fast with `SkillDefinitionError` naming the offender: duplicate skill id · `Args` missing or not a BaseModel subclass · `SKILL_META["description"]` missing/empty/over 300 chars · `skill.run` missing or wrong arity. `tool_schemas` entries: `name` = skill_id, `description` from SKILL_META, `input_schema` = `Args.model_json_schema()`.

Dispatch rules (doc 02 §3): validate `raw_args` via `args_model.model_validate` — `ValidationError` → `SkillResult(ok=False, error=problem(422, "invalid arguments", "<first error msg>; ..."))` (never raises); unknown skill_id → `SkillResult(ok=False, error=problem(404, "unknown skill", ...))`; skill exceptions (unexpected) → caught, `SkillResult(ok=False, error=problem(500, "skill failure", str(exc)))` — dispatcher never lets an exception escape.

`_shared/fragments.py`: `class PeriodArg(BaseModel): start: date; end: date` (half-open, doc'd) and `class DimFilter(BaseModel): column: str; values: list[str] = Field(min_length=1)` — reused by skill schemas so wording/shape stays byte-identical across skills.

- [ ] **Step 1: failing tests** — `backend/tests/test_skill_registry.py`:

```python
import pytest
from poseidon.core.skills.registry import SkillDefinitionError, SkillRegistry


def test_discovery_finds_metric_query_only():
    reg = SkillRegistry.discover()
    assert reg.skill_ids == ["data_qa.metric_query"]     # customer_insight is disabled


def test_schema_dispatch_parity():
    reg = SkillRegistry.discover()
    schema_names = {s["name"] for s in reg.tool_schemas}
    assert schema_names == set(reg.skill_ids)
    for s in reg.tool_schemas:
        assert s["description"] and len(s["description"]) <= 300
        assert s["input_schema"]["type"] == "object"


def test_dispatch_validates_args_structurally():
    reg = SkillRegistry.discover()
    res = reg.dispatch("data_qa.metric_query", {"nonsense": True}, _ctx())
    assert res.ok is False and res.error["status"] == 422


def test_dispatch_unknown_skill_is_structured():
    reg = SkillRegistry.discover()
    res = reg.dispatch("no.such_skill", {}, _ctx())
    assert res.ok is False and res.error["status"] == 404


def test_broken_skill_fails_discovery_loudly(tmp_path, monkeypatch):
    # Build a throwaway tasks package with a skill missing SKILL_META; assert
    # SkillDefinitionError naming the skill id. (Write the files under tmp_path,
    # insert into sys.path, discover with tasks_pkg pointing at it.)
    ...
```

(Write `_ctx()` as a helper constructing `SkillContext` with the real `SyntheticDataClient` pointed at `DATABASE_URL` if set else a `_NullDataClient` stub defined in-test that raises if touched — arg-validation tests never reach data. The broken-skill test writes real files; implement it fully, no `...` left.)

- [ ] **Step 2: RED** → **Step 3: implement** (context/result/registry/fragments; note Task 2 supplies the real skill package the first two tests need — write the registry against the CONVENTION, and if implementing before Task 2 exists, drive the discovery tests with the tmp_path fixture package, then the metric_query-specific assertions activate in Task 2. To keep tasks independently green: implement Task 1 WITH a minimal real `data_qa/metric_query` package containing ONLY `schema.py` (full Args, below) and a `skill.py` whose `run` returns `SkillResult(ok=False, error=problem(501, "not implemented", "metric_query lands in Task 2"))` — Task 2 replaces the body. This keeps discovery/parity/dispatch tests real from Task 1.)

The full `Args` (lives in Task 1's minimal schema.py, unchanged by Task 2):

```python
class Args(BaseModel):
    """Ask a metric question over a certified entity."""
    entity: Literal["MARINE_SALES_PLANNING_V", "W_MARINE_GL_SOURCE_AI"] = "MARINE_SALES_PLANNING_V"
    metrics: list[str] = Field(min_length=1, description="Certified metric names, e.g. GP, VOLUME")
    period: PeriodArg
    compare_period: PeriodArg | None = None          # side-by-side comparison window
    filters: list[DimFilter] = Field(default_factory=list)
    group_by: str | None = None                      # dimension column -> breakdown
    top_n: int = Field(default=5, ge=1, le=50)

SKILL_META = {
    "description": "Query certified metrics (GP, VOLUME, MARGIN, NUM_WON, NUM_INQUIRIES, NUM_LOST, WIN_RATE) over sales or GL data: totals, period comparisons, or top-N breakdowns by a dimension.",
    "examples": [
        "Top GP customers for Port of Singapore in April 2026",
        "Total volume prior year vs YTD",
    ],
}
```

- [ ] **Step 4: GREEN** (full suite; expect 59+1skip plus your new tests) + ruff. **Step 5: Commit** — `feat(skills): registry with fail-fast discovery and structured dispatch`

---

### Task 2: `data_qa.metric_query` — the first real skill

**Files:**
- Create: `tools/build_spec.py`, `tools/format_parts.py` under the skill; replace `skill.py` body; `tests/test_tools.py`, `tests/test_skill.py` in the skill's tests dir
- Modify: nothing outside the skill directory

**Interfaces:**
- `build_spec(args: Args) -> MetricQuerySpec | BreakdownQuerySpec` — `group_by` present → Breakdown (order_by = first metric); absent → Metric. Filters list → `{column: tuple(values)}` mapping. `PeriodArg` → `PeriodWindow` directly (both half-open). Raises nothing itself — spec validation errors surface when the query builder runs; the SKILL catches `SpecValidationError` and returns `ok=False` with `problem(422, "invalid query", str(err))` — the certified error strings flow to the user verbatim (they're written for humans).
- `format_parts`: 
  - Breakdown → `table_part(columns=[<group_by friendly>, *metric friendlies], rows=...)`; numbers rounded: money 0dp, MARGIN/WIN_RATE 2dp.
  - Metric without compare → `table_part` two columns (Metric, Value).
  - Metric WITH compare_period → `metric_grid_part(periods={"a": {...iso range}, "b": {...}}, metrics=[{name, friendly, a, b, unit}])` — friendlies/units from the ontology `Column`/metric metadata (`friendly` from the metric's primary depends_on column where sensible; else the metric name).
  - Proof lines (pin in snapshots): `f"Entity: {fqn}"`, `f"Backend: {settings.data_backend}"`, `f"Period: {start}..{end}"` (+ compare line when present), `f"Filters: col IN (v1, v2)" | "Filters: none"`, `f"Group by: {col} (top {n})" | absent`, `f"Rows: {len}"` or `f"Metrics: {n} values"`.
- `skill.run(ctx, args)`: empty result (all-None metric values or zero breakdown rows) → `ok=True` with `text_part("No data for this selection.")` + proof stating `Result: empty` (doc 02 §6a / doc 06 §4 — never a hallucinated narrative). Happy path → parts + proof. `SpecValidationError` → structured 422 as above. *(Corrected in the phase-3 final-review wave: this line originally prescribed `Rows: 0`, which contradicted both architecture docs; the docs' literal won and the goldens were repinned. `Rows: {n}` remains correct for NON-empty breakdowns.)*

- [ ] **Step 1: failing tests.** `tests/test_tools.py` (offline): build_spec mappings (metric/breakdown/compare variants — assert spec field-for-field); format_parts snapshots with HAND-AUTHORED fixture results (a `BreakdownResult` with the three demo customers → exact table part dict + exact proof list; a compare `MetricResult` pair → exact metric_grid payload). `tests/test_skill.py` (`@pytest.mark.pg`): the Singapore golden — dispatch through the REGISTRY (`reg.dispatch("data_qa.metric_query", {raw args for Singapore April top-5 GP}, ctx)`) against the seeded db; assert `ok`, one `table` part whose first row is the seed's true #1 (compute expected from `generate(seed=1391)` pure-python, same discipline as Phase 2), proof contains `Backend: synthetic` and the period line; plus an empty-window case (valid but data-free month → the no-data text part) and a 422 case (hallucinated `PORT_NM` filter → certified error string surfaced).
- [ ] **Step 2: RED** → **Step 3: implement** → **Step 4: GREEN** (offline + `-m pg` live) + ruff. **Step 5: Commit** — `feat(data-qa): metric_query skill with typed parts and proof blocks`

---

### Task 3: `customer_insight` deterministic tools (task disabled until Phase 8)

**Files:**
- Create: the `customer_insight` tree per the File Map (task.yml `enabled: false`; tools + tests; NO skill.py/schema.py yet — the registry skips disabled tasks entirely, so their absence is legal)
- Test: `skills/existing_customer_brief/tests/test_tools.py`

**Interfaces:**
- `fetch_metrics(data: DataClient, customer: str, anchor: date) -> tuple[MetricResult, MetricResult, list[str]]` — six certified metrics (VOLUME, GP, MARGIN, NUM_WON, NUM_INQUIRIES, NUM_LOST) for prior full calendar year and YTD (both derived from `anchor`, windows half-open), filtered `CUST_NM = customer`; returns (prior, ytd, proof_lines).
- `fetch_top_ports(data: DataClient, customer: str, window: PeriodWindow, top_n: int = 5) -> tuple[BreakdownResult, list[str]]` — GP by `LOC_NM` for the customer.
- Both compose specs via the Phase-2 spec/query layer — NO SQL here.

- [ ] Steps: failing tests (pg-marked; ground truth from `generate(seed=1391)` — pick "Northstar Lines", anchor 2026-07-01; assert values + proof) → RED → implement → GREEN → ruff → **Commit** — `feat(customer-insight): deterministic metric and port tools (task gated for phase 8)`

---

### Task 4: ArtifactStore + `build_brief_pdf` + container libs

**Files:**
- Create: `backend/poseidon/core/artifacts.py`, `tools/build_brief_pdf.py` (under existing_customer_brief), `backend/tests/test_artifact_store.py`
- Modify: `backend/pyproject.toml` (deps `boto3>=1.34`, `weasyprint>=62`, `markdown>=3.6`; markers `minio`, `pdf`), `infra/backend.Dockerfile.dev` (add `RUN apt-get update && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 fonts-dejavu-core && rm -rf /var/lib/apt/lists/*` BEFORE the pip install layer), `infra/runbooks/local.md` (markers + artifact section)
- Test: pdf/minio-marked tests + `pdf` ImportError skip proof

**Interfaces:**
- `ArtifactStore(settings)` — boto3 client from `s3_endpoint_url`/`s3_bucket` (path-style for MinIO); `ensure_bucket()` create-if-missing (idempotent); `put_pdf(key: str, content: bytes) -> ArtifactRef` (presigned GET, 1h expiry, mime `application/pdf`); `put` records nothing else (run-log arrives Phase 11).
- `build_brief_pdf(store: ArtifactStore, title: str, markdown_body: str, key_prefix: str) -> tuple[ArtifactRef, list[str]]` — markdown → HTML (`markdown` lib) wrapped in a minimal print stylesheet → WeasyPrint PDF bytes → `store.put_pdf(f"{key_prefix}/{slug(title)}.pdf", ...)`; proof lines: byte size, key, sha256 of the PDF? NO — WeasyPrint embeds creation timestamps, so pdf bytes aren't deterministic: proof = key + page count + byte size only, and the test asserts `%PDF` magic + nonzero pages, never a hash. Import of weasyprint happens INSIDE the function (host-safe module import).
- Tests: `test_artifact_store.py` `@pytest.mark.minio` — ensure_bucket idempotent twice; put_pdf roundtrip (GET the presigned URL via httpx, assert 200 + `%PDF` prefix). `test_tools.py` addition `@pytest.mark.pdf` + `@pytest.mark.minio` — build_brief_pdf end-to-end against MinIO (skips on host without Pango: module-level `pytest.importorskip`? NO — import inside function means the SKIP must probe `weasyprint` availability explicitly: `pytest.mark.pdf` + a module-level `_HAS_WEASYPRINT` try/import with `skipif(not _HAS_WEASYPRINT, reason="weasyprint needs Pango/Cairo — run in the container")`).
- Marker skip mechanics mirror the Phase-2 `pg` pattern (2s reachability probe for MinIO at `s3_endpoint_url`).

- [ ] Steps: failing tests → RED (offline: both skip with reasons; that IS the offline RED shape — then prove GREEN in-container: `docker compose -f infra/docker-compose.yml build backend && docker compose -f infra/docker-compose.yml run --rm backend python -m pytest -m "pdf or minio" -v`) → implement → GREEN (host offline skips + container live passes; paste both outputs) → ruff → **Commit** — `feat(artifacts): minio store and pdf brief rendering with container-gated tests`

---

### Task 5: Dev-only skill runner endpoint + wiring

**Files:**
- Create: `backend/poseidon/api/dev_runner.py`, `backend/tests/test_dev_runner.py`
- Modify: `backend/poseidon/api/app.py` (conditional include), `infra/runbooks/local.md` (usage)

**Interfaces:**
- `POST /api/dev/skills/{skill_id}/run` body = raw args dict → `200 {"ok", "parts", "proof", "artifacts", "error"}` (SkillResult serialized; artifacts as dicts). Router included ONLY when `settings.deploy_mode == "local"` (create_app conditional — the dev surface never exists in spcs/ec2). Registry built once at app startup (`app.state.skill_registry = SkillRegistry.discover()`); ctx assembled per-request: `SyntheticDataClient(settings.database_url)` when `data_backend == "synthetic"`, `ArtifactStore(settings)`, default slots.
- Tests: local-mode app exposes the route; a `deploy_mode="spcs"` app returns 404 for it (build via `create_app(Settings(...))`); dispatch path: invalid args → 200 with `ok: false, error.status == 422` (the endpoint returns 200 — the FAILURE is structured content, mirroring the router loop's contract); `@pytest.mark.pg`: the Singapore breakdown through the HTTP endpoint returns the table part + proof (assert same first-row truth as Task 2's golden).

- [ ] Steps: failing tests → RED → implement → GREEN (offline + pg) → ruff → **Commit** — `feat(api): local-only dev skill runner`

---

## Phase Gate (human validation)

1. Rebuild + restart backend container (`docker compose -f infra/docker-compose.yml up -d --build backend`) → boot logs show discovery: `skills registered: data_qa.metric_query`.
2. `curl -X POST localhost:8000/api/dev/skills/data_qa.metric_query/run -H "Content-Type: application/json" -d '{"metrics":["GP"],"period":{"start":"2026-04-01","end":"2026-05-01"},"filters":[{"column":"LOC_NM","values":["Singapore"]}],"group_by":"CUST_NM"}'` → table part with the seeded Singapore top-5 + proof block.
3. Same call with `"column":"PORT_NM"` → structured 422 carrying the certified did-you-mean error.
4. `python -m pytest -m "pg or minio" -v` live green; `docker compose run --rm backend python -m pytest -m pdf -v` green in-container; plain offline suite green with skips.
5. PDF: the pdf-marked test's artifact visible in MinIO console (localhost:9001, bucket `poseidon-artifacts`).

## Self-Review Notes

- Doc-08 P3 coverage: registry+discovery+fail-fast ✓ (T1), SkillContext/SkillResult ✓ (T1), metric_query tools→parts+proof ✓ (T2), customer_insight fetch_metrics/fetch_top_ports/build_brief_pdf ✓ (T3/T4), registry↔schema parity test ✓ (T1), dev runner ✓ (T5), goldens vs synthetic + Singapore via runner + PDF in MinIO ✓ (gates/T2/T4/T5).
- Deliberate scope: no LLM/router (P5), no parsing pipeline (P4), no real chat wiring (P6), brief SKILLS absent (P8) with their task disabled; `metric_grid` shape matches doc 01 §4's frontend table; PDF non-determinism handled by never hashing PDFs.
- Type consistency: `Args`/fragments (T1) ↔ build_spec (T2); `SkillContext` fields ↔ dev runner assembly (T5); ArtifactRef ↔ SkillResult.artifacts ↔ runner serialization.
