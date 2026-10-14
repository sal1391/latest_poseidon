"""Run the monthly performance report engine once, end to end, and print what it made.

The report flow does not exist yet (Phase 17 PR A2); this is the engine's smoke
test. It dispatches ``reporting.monthly_performance`` through the real
``SkillRegistry`` against the real database, stores one ``report_run`` row, writes
the PDF (or the HTML, when the host cannot render a PDF: WeasyPrint needs Pango,
which the Windows host lacks and the container has), and prints a plain-text
summary so a person can check the numbers without opening the file.

Stub mode is forced, so the narrative is the deterministic template and no model
call is ever made. The summary also checks, per ranked table, that the footer row
equals the KPI tile it must reconcile to (``footer == tile``).

Formatting is plain aligned ASCII, like ``demo_query.py`` (whose table renderer is
reused, not copied).

Usage::

    DATABASE_URL=postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon \\
        python -m poseidon.scripts.demo_report --perspective customer \\
        --office Rotterdam --month 2026-06 --out demo/rotterdam-2026-06.pdf
"""

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from poseidon.core.config import Settings
from poseidon.core.data.synthetic_client import SyntheticDataClient
from poseidon.core.db import build_engine
from poseidon.core.identity import UserContext
from poseidon.core.llm.prompt_tree import PromptTree
from poseidon.core.skills.context import ArtifactRef, SkillContext
from poseidon.core.skills.registry import SkillRegistry
from poseidon.reports.store import ReportStore
from poseidon.scripts.demo_query import _render_table

SKILL_ID = "reporting.monthly_performance"
_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_TILE_METRICS = ("GP", "VOLUME", "MARGIN")
_KPI_ORDER = ("GP", "VOLUME", "MARGIN", "NUM_WON", "NUM_INQUIRIES", "NUM_LOST", "WIN_RATE")


def _month(value: str) -> str:
    if not _MONTH.match(value):
        raise argparse.ArgumentTypeError(f"--month must be YYYY-MM, got {value!r}")
    return value


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="demo_report", description="Run the monthly performance report engine once."
    )
    parser.add_argument("--perspective", required=True, choices=["customer", "supplier", "book"])
    parser.add_argument("--office", default=None, help="office value (omit for --perspective book)")
    parser.add_argument("--month", required=True, type=_month, help="YYYY-MM")
    parser.add_argument(
        "--out", required=True, help="the PDF path (the .html sibling is written when no PDF)"
    )
    return parser.parse_args(argv)


def _footer_matches_tile(payload: Mapping[str, Any], table: Mapping[str, Any]) -> bool:
    footer = table["footer"]["display"]
    kpis = payload["kpis"]
    return all(footer[m] == kpis[m]["display"] for m in _TILE_METRICS)


def _row_cells(row: Mapping[str, Any]) -> list[str]:
    d = row["display"]
    return [
        str(row["key"]),
        d["GP"]["current"],
        d["GP"]["change"],
        d["VOLUME"]["current"],
        d["MARGIN"]["current"],
    ]


def render_summary(payload: Mapping[str, Any], artifacts: Sequence[ArtifactRef]) -> str:
    periods = payload["periods"]
    out: list[str] = [
        f"Report: {payload['scope']['label']} ({payload['perspective']})",
        f"Periods: {periods['current']['label']} vs {periods['prior']['label']} "
        f"[{periods['window_kind']}]",
        f"Source: {payload['source']}   Run: {payload['run_id']}",
        "",
        "KPIS",
    ]
    kpi_rows = [
        [
            m,
            k["display"]["prior"],
            k["display"]["current"],
            k["display"]["change"],
            k["display"]["pct"],
        ]
        for m in _KPI_ORDER
        if (k := payload["kpis"].get(m)) is not None
    ]
    out.append(
        _render_table(
            ["METRIC", "PRIOR", "CURRENT", "CHANGE", "PCT"],
            kpi_rows,
            right_align=[False, True, True, True, True],
        )
    )
    out += ["", "RANKED TABLES"]
    for name, table in payload["tables"].items():
        rows = [_row_cells(r) for r in table["rows"]]
        if table.get("other"):
            rows.append(_row_cells(table["other"]))
        rows.append(_row_cells(table["footer"]))
        verdict = "yes" if _footer_matches_tile(payload, table) else "no"
        out.append(f"{name}  (footer == tile: {verdict})")
        out.append(
            _render_table(
                ["KEY", "GP", "GP CHANGE", "VOLUME", "MARGIN"],
                rows,
                right_align=[False, True, True, True, True],
            )
        )
        out.append("")
    out.append("MOVERS")
    for label in ("increases", "decreases"):
        for mover in payload["movers"][label]:
            out.append(
                f"  {label[:-1]:8} {mover['key']}  GP {mover['display']['GP']['change']}"
                f"  driver: {mover['driver']}"
            )
    out += ["", "NEW / LOST"]
    for label in ("new", "lost"):
        for entry in payload["new_lost"][label]:
            gp = entry["display"]["GP"]
            out.append(f"  {label:5} {entry['key']}  GP {gp['current']} (prior {gp['prior']})")
    out += ["", "FLAGS"]
    flags = payload["narrative"]["flags"]
    out += [f"  {f}" for f in flags] if flags else ["  (none)"]
    out += ["", "SUGGESTED QUESTIONS"]
    out += [f"  - {q}" for q in payload["suggested_questions"]]
    out += ["", "PROOF"]
    out += [f"  {line}" for line in payload["proof"]]
    out += ["", "ARTIFACTS"]
    out += [f"  {a.name}  {a.url}  {a.mime}" for a in artifacts] or ["  (none: no PDF rendered)"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    url = os.environ.get("DATABASE_URL", "")
    if not url.strip():
        print(
            "DATABASE_URL is required to run the demo report "
            "(e.g. postgresql+psycopg://poseidon:poseidon@localhost:5434/poseidon)",
            file=sys.stderr,
        )
        return 2
    args = parse_args(argv)

    settings = Settings(
        _env_file=None,
        database_url=url,
        s3_bucket="poseidon-artifacts",
        data_backend="synthetic",
        llm_mode="stub",
    )
    store = ReportStore(build_engine(settings.database_url), app_role=settings.database_app_role)
    user = UserContext(sub="dev|demo", email=None, name="Demo", roles=("Poseidon:Sales",))
    ctx = SkillContext(
        data=SyntheticDataClient(settings.database_url),
        artifacts=None,
        settings=settings,
        user=user,
        report_store=store,
        prompt_tree=PromptTree(),
    )
    raw = {"perspective": args.perspective, "month": f"{args.month}-01"}
    if args.office is not None:
        raw["office_value"] = args.office
    result = SkillRegistry.discover().dispatch(SKILL_ID, raw, ctx)
    if not result.ok:
        print(f"report failed: {result.error}", file=sys.stderr)
        return 1

    payload = result.parts[0]["payload"]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if result.artifacts:
        data = store.get_bytes(user.sub, payload["run_id"], "pdf")
    else:
        data = store.get_bytes(user.sub, payload["run_id"], "html")
        out = out.with_suffix(".html")
    if data is None:
        print("report stored but its bytes could not be read back", file=sys.stderr)
        return 1
    out.write_bytes(data)
    print(render_summary(payload, result.artifacts), end="")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
