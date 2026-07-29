"""Offline contract tests + live ground-truth tests for the
``customer_insight`` deterministic tools: ``fetch_metrics``,
``fetch_top_ports``, and ``build_brief_pdf``.

The task these tools belong to ships ``enabled: false`` until Phase 8 (see
``customer_insight/__init__.py`` and ``task.yml``), so there is no skill here
yet to dispatch through — these tests call the tools directly. Four things
are pinned:

1. The certified specs each tool builds — entity, metrics, filters, and
   half-open windows derived from an anchor date — captured field-for-field
   by a tiny recording ``DataClient`` stub that never touches a database
   (``_RecordingDataClient``, below).
2. The exact values the seeded Postgres returns, recomputed independently in
   pure Python from ``generate(seed=SEED)`` — same discipline as
   ``backend/tests/test_synthetic_client_pg.py`` and
   ``data_qa.metric_query``'s ``test_skill.py`` — for customer "Northstar
   Lines" anchored on 2026-07-01 (``@pytest.mark.pg``, skipped without a
   reachable, seeded Postgres).
3. That ``customer_insight`` is STILL invisible to
   ``SkillRegistry.discover()`` even though its tools are now real,
   importable modules (imported at the top of this very file) — proving
   ``enabled: false`` in ``task.yml`` is doing its job, not merely that the
   skill doesn't exist yet.
4. ``build_brief_pdf``'s pure ``slug`` helper (offline, no marker needed —
   plain string logic) and its full markdown -> HTML -> PDF -> MinIO pipeline
   end to end (``@pytest.mark.pdf`` + ``@pytest.mark.minio``, skipped unless
   both a reachable MinIO and a WeasyPrint that can actually import are
   present — see ``_HAS_WEASYPRINT`` below).

Categories 1, 3, and the offline half of 4 run in the plain offline suite (no
marker, no ``DATABASE_URL``/``S3_ENDPOINT_URL`` needed). Category 2 is
``@pytest.mark.pg``; the live half of 4 is ``@pytest.mark.pdf`` +
``@pytest.mark.minio``. Because this module mixes offline and marked tests —
unlike ``test_skill.py``/``test_synthetic_client_pg.py``, which are pg-only
top to bottom — the availability probes below cannot call
``pytest.skip(..., allow_module_level=True)``: that would skip the whole
module, offline tests included. Instead each probe (``_pg_skip_reason()``,
``_minio_skip_reason()``) computes its reason once at import time, and the
matching ``_require_*()`` raises it as a per-test skip, called only from
inside the tests that need it.
"""

import datetime as dt
import os
import re

import httpx
import psycopg
import pytest

from poseidon.core.artifacts import ArtifactStore
from poseidon.core.config import Settings
from poseidon.core.data.client import BreakdownResult, MetricResult
from poseidon.core.data.specs import BreakdownQuerySpec, MetricQuerySpec, PeriodWindow
from poseidon.core.data.synthetic_client import SyntheticDataClient, normalize_dsn
from poseidon.core.skills.registry import SkillRegistry
from poseidon.scripts.generate_synthetic import generate
from poseidon.tasks.customer_insight.skills.existing_customer_brief.tools.build_brief_pdf import (
    build_brief_pdf,
    slug,
)
from poseidon.tasks.customer_insight.skills.existing_customer_brief.tools.fetch_metrics import (
    fetch_metrics,
)
from poseidon.tasks.customer_insight.skills.existing_customer_brief.tools.fetch_top_ports import (
    fetch_top_ports,
)

# WeasyPrint links Pango/Cairo/GDK-Pixbuf at import time and raises OSError
# (not ImportError) when they are missing, exactly as it does on this file's
# own host today (see infra/backend.Dockerfile.dev) — so the probe below
# catches Exception broadly, the same "any failure means not available"
# philosophy _pg_skip_reason() and _minio_skip_reason() use.
try:
    import weasyprint  # noqa: F401 - existence probe only, never used directly
except Exception:  # noqa: BLE001 - missing native libs raise OSError, not ImportError
    _HAS_WEASYPRINT = False
else:
    _HAS_WEASYPRINT = True

SALES = "MARINE_SALES_PLANNING_V"
SEED = 1391
CONNECT_TIMEOUT_SECONDS = 2

CUSTOMER = "Northstar Lines"
ANCHOR = dt.date(2026, 7, 1)
SIX_METRICS = ("VOLUME", "GP", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST")

# The two windows `fetch_metrics(..., ANCHOR)` must derive — prior full
# calendar year and YTD, both half-open — pinned here once and reused by
# every test below so a window mistake in one test can't quietly disagree
# with another.
PRIOR_YEAR = PeriodWindow(dt.date(2025, 1, 1), dt.date(2026, 1, 1))
YTD = PeriodWindow(dt.date(2026, 1, 1), dt.date(2026, 7, 1))

# Skip reasons are ASCII-only on purpose: pytest prints them straight to a
# console that may well be Windows cp1252, where an em dash becomes "?".
_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d db`"
_SEED_HINT = "seed it with `python -m poseidon.scripts.seed_synthetic`"


def _pg_skip_reason() -> str | None:
    """``None`` when a reachable, seeded Postgres is available for the
    ``@pytest.mark.pg`` tests below; otherwise the reason they must skip.

    Computed once, at import time — see the module docstring for why this
    RETURNS a reason instead of calling
    ``pytest.skip(allow_module_level=True)`` the way the pg-only modules do.
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return (
            "DATABASE_URL is not set - pg integration tests need a Postgres: "
            f"{_UP_HINT}, migrate it with `python -m alembic upgrade head`, {_SEED_HINT}"
        )
    try:
        with psycopg.connect(normalize_dsn(dsn), connect_timeout=CONNECT_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM synthetic.marine_sales_planning_v")
                seeded_rows = cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001 - any connect/lookup failure means "not available"
        return (
            f"Postgres at DATABASE_URL is not usable within {CONNECT_TIMEOUT_SECONDS}s "
            f"({type(exc).__name__}: {str(exc).strip()}) - {_UP_HINT} and migrate it with "
            "`python -m alembic upgrade head`"
        )
    if seeded_rows == 0:
        return f"synthetic.marine_sales_planning_v is empty - {_SEED_HINT}"
    return None


_DSN = os.environ.get("DATABASE_URL", "")
_PG_SKIP_REASON = _pg_skip_reason()
# Both are expensive-ish to build (generate() draws 24k+ rows; the client
# holds a DSN) so they are built once, at import time, but only when a pg
# test could actually use them — never on a bare offline run.
DATASET = generate(seed=SEED) if _PG_SKIP_REASON is None else None
CLIENT = (
    SyntheticDataClient(_DSN, connect_timeout=CONNECT_TIMEOUT_SECONDS)
    if _PG_SKIP_REASON is None
    else None
)


def _require_pg() -> None:
    if _PG_SKIP_REASON is not None:
        pytest.skip(_PG_SKIP_REASON)


_MINIO_UP_HINT = "start it with `docker compose -f infra/docker-compose.yml up -d minio`"
_MINIO_CONNECT_TIMEOUT_SECONDS = 2


def _minio_skip_reason() -> str | None:
    """``None`` when a reachable MinIO is available for the
    ``@pytest.mark.minio`` test below; otherwise the reason it must skip.
    Same shape as ``_pg_skip_reason`` above, one level down the stack — see
    that function's docstring for why this returns a reason instead of
    skipping the whole module."""
    endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    if not endpoint:
        return (
            "S3_ENDPOINT_URL is not set - minio integration tests need MinIO: "
            f"{_MINIO_UP_HINT}"
        )
    try:
        response = httpx.get(
            f"{endpoint.rstrip('/')}/minio/health/live", timeout=_MINIO_CONNECT_TIMEOUT_SECONDS
        )
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - any connect/health failure means "not available"
        return (
            f"MinIO at {endpoint} is not usable within {_MINIO_CONNECT_TIMEOUT_SECONDS}s "
            f"({type(exc).__name__}: {str(exc).strip()}) - {_MINIO_UP_HINT}"
        )
    return None


_MINIO_SKIP_REASON = _minio_skip_reason()


def _require_minio() -> None:
    if _MINIO_SKIP_REASON is not None:
        pytest.skip(_MINIO_SKIP_REASON)


def _artifact_store() -> ArtifactStore:
    """An :class:`ArtifactStore` built from the same environment variables
    ``backend/tests/test_artifact_store.py`` reads, constructed only after
    ``_require_minio()`` has already confirmed MinIO is reachable."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused:unused@localhost:5432/unused",
        s3_endpoint_url=os.environ.get("S3_ENDPOINT_URL", ""),
        s3_bucket=os.environ.get("S3_BUCKET", "poseidon-artifacts"),
        s3_access_key=os.environ.get("S3_ACCESS_KEY"),
        s3_secret_key=os.environ.get("S3_SECRET_KEY"),
    )
    return ArtifactStore(settings)


# ---------------------------------------------------------------------------
# pure-Python ground truth (no SQL, no database) — mirrors
# test_synthetic_client_pg.py's helpers, narrowed to one customer/port axis
# ---------------------------------------------------------------------------


def sales_in(window: PeriodWindow, **equals: str) -> list[dict]:
    """Generated sales rows inside the half-open ``window``, optionally
    narrowed to exact column values — the pure-Python twin of a spec
    filter."""
    return [
        row
        for row in DATASET.sales_rows
        if window.start <= row["LIFT_ETA_DATE"] < window.end
        and all(row[col] == value for col, value in equals.items())
    ]


def six_metrics(rows: list[dict]) -> dict[str, float | None]:
    """VOLUME/GP/MARGIN/NUM_WON/NUM_INQUIRIES/NUM_LOST over ``rows``, computed
    the way the certified metric SQL defines them (MARGIN is a ratio of the
    aggregates, never an average of per-row margins)."""
    volume = sum(row["FIXED_TONS"] for row in rows)
    gp = sum(row["GROSS_PROFIT"] for row in rows)
    won = sum(row["#_FIXTURES"] for row in rows)
    inquiries = sum(row["#_INQUIRIES"] for row in rows)
    return {
        "VOLUME": volume,
        "GP": gp,
        "MARGIN": gp / volume if volume else None,
        "NUM_WON": won,
        "NUM_INQUIRIES": inquiries,
        "NUM_LOST": inquiries - won,
    }


def gp_by_port(rows: list[dict]) -> list[tuple[str, float]]:
    """``[(port, gp), ...]`` sorted by GP descending — the pure-Python twin
    of ``GROUP BY LOC_NM ... ORDER BY "GP" DESC``."""
    totals: dict[str, float] = {}
    for row in rows:
        totals[row["LOC_NM"]] = totals.get(row["LOC_NM"], 0.0) + row["GROSS_PROFIT"]
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


# ---------------------------------------------------------------------------
# discovery stays clean: customer_insight's tools exist and import cleanly
# (this very module imports them above), but the task is still disabled
# ---------------------------------------------------------------------------


def test_discovery_still_registers_only_metric_query():
    """``customer_insight/task.yml`` ships ``enabled: false`` — discovery
    must keep skipping the whole task, exactly as before this task's tools
    existed. Task 1/2 pin the same invariant in
    ``backend/tests/test_skill_registry.py``; this copy lives with the tools
    it is actually about, so a regression here is caught next to the change
    that would cause it (e.g. an accidental ``schema.py`` dropped into this
    skill directory, which would make it router-visible while still
    unfinished)."""
    reg = SkillRegistry.discover()
    assert reg.skill_ids == ["data_qa.metric_query"]


# ---------------------------------------------------------------------------
# offline spec shape: a recording DataClient stub, no database
# ---------------------------------------------------------------------------


class _RecordingDataClient:
    """A ``DataClient`` (structurally — see ``client.DataClient``) that
    records every spec it is asked to run and returns a shapeless-but-valid
    result, instead of running anything against a database.

    Lets an offline test assert exactly what ``fetch_metrics``/
    ``fetch_top_ports`` ask for — entity, metrics, filters, windows —
    field-for-field, with no database at all.
    """

    def __init__(self) -> None:
        self.metric_specs: list[MetricQuerySpec] = []
        self.breakdown_specs: list[BreakdownQuerySpec] = []

    def list_dimension_values(self, *args: object, **kwargs: object) -> list[str]:
        raise AssertionError("fetch_metrics/fetch_top_ports must never list dimension values")

    def available_periods(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("fetch_metrics/fetch_top_ports must never query available periods")

    def run_metric_query(self, spec: MetricQuerySpec) -> MetricResult:
        self.metric_specs.append(spec)
        return MetricResult(
            entity=spec.entity, period=spec.period, values=dict.fromkeys(spec.metrics)
        )

    def run_breakdown_query(self, spec: BreakdownQuerySpec) -> BreakdownResult:
        self.breakdown_specs.append(spec)
        return BreakdownResult(entity=spec.entity, group_by=spec.group_by, rows=[])


def test_fetch_metrics_builds_the_certified_specs_offline():
    client = _RecordingDataClient()

    prior, ytd, proof = fetch_metrics(client, CUSTOMER, ANCHOR)

    assert len(client.metric_specs) == 2
    prior_spec, ytd_spec = client.metric_specs
    assert prior_spec == MetricQuerySpec(
        entity=SALES, metrics=SIX_METRICS, period=PRIOR_YEAR, filters={"CUST_NM": (CUSTOMER,)}
    )
    assert ytd_spec == MetricQuerySpec(
        entity=SALES, metrics=SIX_METRICS, period=YTD, filters={"CUST_NM": (CUSTOMER,)}
    )
    assert prior.entity == SALES
    assert ytd.entity == SALES
    assert proof == [
        "Customer: Northstar Lines",
        "Prior year: 2025-01-01..2026-01-01",
        "YTD: 2026-01-01..2026-07-01",
        "Metrics: 6",
    ]


def test_fetch_metrics_windows_are_half_open_and_anchor_derived():
    """A different anchor must re-derive both windows from scratch — this
    is what rules out the windows being hardcoded to the brief's own example
    anchor rather than genuinely computed from whatever ``anchor`` is
    passed."""
    client = _RecordingDataClient()

    fetch_metrics(client, CUSTOMER, dt.date(2024, 3, 15))

    prior_spec, ytd_spec = client.metric_specs
    assert prior_spec.period == PeriodWindow(dt.date(2023, 1, 1), dt.date(2024, 1, 1))
    assert ytd_spec.period == PeriodWindow(dt.date(2024, 1, 1), dt.date(2024, 3, 15))


def test_fetch_metrics_rejects_a_january_1_anchor():
    """A January 1 anchor would make the YTD window ``[Jan 1, Jan 1)`` —
    empty by construction. ``PeriodWindow`` itself would already refuse that
    (``start >= end``), but ``fetch_metrics`` must reject it explicitly, up
    front, with a message naming the actual constraint — not let a caller
    hit an unexplained ``ValueError`` raised deep inside window
    construction. Pinning the exact message so this stays a deliberate,
    documented contract rather than an accident of ``PeriodWindow``'s own
    wording."""
    client = _RecordingDataClient()

    with pytest.raises(ValueError) as exc_info:
        fetch_metrics(client, CUSTOMER, dt.date(2026, 1, 1))

    assert str(exc_info.value) == (
        "anchor 2026-01-01 has no year-to-date range — the earliest supported anchor is January 2"
    )
    assert client.metric_specs == [], "the guard must fire before either spec is built or run"


def test_fetch_top_ports_builds_the_certified_breakdown_spec_offline():
    client = _RecordingDataClient()

    result, proof = fetch_top_ports(client, CUSTOMER, YTD, top_n=5)

    assert len(client.breakdown_specs) == 1
    spec = client.breakdown_specs[0]
    assert spec == BreakdownQuerySpec(
        entity=SALES,
        metrics=("GP",),
        period=YTD,
        group_by="LOC_NM",
        order_by_metric="GP",
        top_n=5,
        filters={"CUST_NM": (CUSTOMER,)},
    )
    assert result.entity == SALES
    assert proof == [
        "Customer: Northstar Lines",
        "Window: 2026-01-01..2026-07-01",
        "Top ports: 0 of requested 5",
    ]


def test_fetch_top_ports_defaults_top_n_to_5():
    client = _RecordingDataClient()

    fetch_top_ports(client, CUSTOMER, YTD)

    assert client.breakdown_specs[0].top_n == 5


# ---------------------------------------------------------------------------
# ground truth: the seeded Postgres vs generate(seed=1391), pure Python
# ---------------------------------------------------------------------------


@pytest.mark.pg
def test_fetch_metrics_matches_python_ground_truth_for_both_windows():
    _require_pg()
    expected_prior = six_metrics(sales_in(PRIOR_YEAR, CUST_NM=CUSTOMER))
    expected_ytd = six_metrics(sales_in(YTD, CUST_NM=CUSTOMER))
    assert expected_prior["NUM_INQUIRIES"], "Northstar Lines must have prior-year inquiries"
    assert expected_ytd["NUM_INQUIRIES"], "Northstar Lines must have YTD inquiries"

    prior, ytd, proof = fetch_metrics(CLIENT, CUSTOMER, ANCHOR)

    assert prior.entity == SALES
    assert prior.period == PRIOR_YEAR
    assert ytd.period == YTD
    for metric in SIX_METRICS:
        assert prior.values[metric] == pytest.approx(expected_prior[metric]), metric
        assert ytd.values[metric] == pytest.approx(expected_ytd[metric]), metric
    assert proof == [
        "Customer: Northstar Lines",
        "Prior year: 2025-01-01..2026-01-01",
        "YTD: 2026-01-01..2026-07-01",
        "Metrics: 6",
    ]


@pytest.mark.pg
def test_fetch_top_ports_matches_python_ground_truth_keys_values_and_order():
    _require_pg()
    rows = sales_in(YTD, CUST_NM=CUSTOMER)
    expected = gp_by_port(rows)[:5]
    assert expected, "Northstar Lines must have at least one port with sales in the YTD window"
    gps = [gp for _, gp in expected]
    assert len(set(gps)) == len(gps), "ties would make the SQL ordering ambiguous"

    result, proof = fetch_top_ports(CLIENT, CUSTOMER, YTD, top_n=5)

    assert result.entity == SALES
    assert result.group_by == "LOC_NM"
    assert [row.key for row in result.rows] == [name for name, _ in expected]
    for row, (name, gp) in zip(result.rows, expected, strict=True):
        assert row.values["GP"] == pytest.approx(gp), name
    assert proof == [
        "Customer: Northstar Lines",
        "Window: 2026-01-01..2026-07-01",
        f"Top ports: {len(expected)} of requested 5",
    ]


# ---------------------------------------------------------------------------
# build_brief_pdf: pure offline shape (slug is plain string logic, no
# WeasyPrint/MinIO involved), then the live pdf+minio end-to-end pipeline
# ---------------------------------------------------------------------------


def test_slug_lowercases_and_hyphenates_punctuation():
    assert slug("Northstar Lines: Q3 Brief!") == "northstar-lines-q3-brief"


def test_slug_collapses_runs_and_strips_leading_and_trailing_separators():
    assert slug("  --Multiple   Spaces & Dashes--  ") == "multiple-spaces-dashes"


def test_slug_keeps_digits_as_part_of_a_word():
    assert slug("Q3 2026 Update") == "q3-2026-update"


@pytest.mark.parametrize(
    "title",
    ["", "   ", "---", "日本語"],
    ids=["empty", "whitespace-only", "punctuation-only", "non-latin-only"],
)
def test_slug_falls_back_to_untitled_when_nothing_survives(title: str):
    """Empty, whitespace-only, punctuation-only, and non-Latin-only titles
    all collapse to the empty string under the ASCII-only ``[a-z0-9]``
    pattern — without a fallback they would all silently share the same
    object key (``f"{key_prefix}/.pdf"``) and overwrite one another."""
    assert slug(title) == "untitled"


def test_slug_keeps_the_latin_remainder_of_a_mixed_script_title():
    assert slug("日本語 Brief 2026") == "brief-2026"


@pytest.mark.pdf
@pytest.mark.minio
@pytest.mark.skipif(
    not _HAS_WEASYPRINT, reason="weasyprint needs Pango/Cairo - run in the container"
)
def test_build_brief_pdf_renders_and_uploads_to_minio():
    """End-to-end: a markdown heading + table becomes a real PDF, uploaded to
    MinIO, fetchable at the presigned URL ``build_brief_pdf`` hands back.

    Proof lines are pinned exactly where they are deterministic (the
    artifact key, computed from ``slug(title)``) and by pattern where they
    are not (page count, byte size — see ``build_brief_pdf``'s module
    docstring on why PDF bytes are never hashed).
    """
    _require_minio()
    store = _artifact_store()
    store.ensure_bucket()

    title = "Northstar Lines Q3 Brief"
    markdown_body = (
        "# Overview\n"
        "\n"
        "Northstar Lines grew gross profit year over year.\n"
        "\n"
        "| Port | GP |\n"
        "| --- | --- |\n"
        "| Singapore | 120000 |\n"
        "| Rotterdam | 95000 |\n"
    )

    ref, proof = build_brief_pdf(
        store, title, markdown_body, key_prefix="existing-customer-brief-test"
    )

    expected_key = "existing-customer-brief-test/northstar-lines-q3-brief.pdf"
    assert ref.name == expected_key
    assert ref.mime == "application/pdf"
    assert ref.url.startswith("http")

    response = httpx.get(ref.url, timeout=_MINIO_CONNECT_TIMEOUT_SECONDS)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")

    assert proof[0] == f"Artifact: {expected_key}"
    pages_match = re.fullmatch(r"Pages: (\d+)", proof[1])
    assert pages_match is not None, proof[1]
    assert int(pages_match.group(1)) >= 1
    assert proof[2] == f"Bytes: {len(response.content)}"
