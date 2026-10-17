"""Render one section of a stored monthly report payload as message parts.

Pure functions over the payload dict; no I/O. Every display string comes from the
payload's own ``display`` blocks, so the numbers here are the ones the report showed.
"""

from __future__ import annotations

from typing import Any

from poseidon.core.skills.result import metric_grid_part, table_part, text_part

KPI_FRIENDLY = {
    "VOLUME": ("Volume", "tons"),
    "GP": ("Gross Profit", "USD"),
    "MARGIN": ("Margin", "USD/t"),
    "NUM_WON": ("# Won", None),
    "NUM_INQUIRIES": ("# Inquiries", None),
    "NUM_LOST": ("# Lost", None),
    "WIN_RATE": ("Win Rate", None),
}

_TABLE_SECTIONS = {
    "entities_by_gp",
    "entities_by_volume",
    "ports_by_gp",
    "ports_by_volume",
    "deal_class",
}
_METRIC_COLUMNS = (
    ("GP", "GP"),
    ("VOLUME", "Volume"),
    ("MARGIN", "Margin"),
)

_Table = tuple[list[str], list[list]]


def render(
    section: str, payload: dict[str, Any], limit: int, offset: int
) -> tuple[list[dict], list[str]]:
    periods = payload["periods"]
    proof = [
        f"Report: run {payload['run_id']} - {payload['scope']['label']} - "
        f"{periods['current']['label']} vs {periods['prior']['label']}"
    ]
    if section == "kpis":
        return [_kpis(payload)], [*proof, "Section: kpis"]
    if section in _TABLE_SECTIONS:
        return _paged(section, _ranked_table(section, payload), limit, offset, proof)
    if section == "movers":
        return _paged(section, _movers_table(payload), limit, offset, proof)
    if section == "new_lost":
        return _paged(section, _new_lost_table(payload), limit, offset, proof)
    if section == "narrative":
        return _narrative(payload, proof)
    if section == "context":
        context = payload.get("entity_context")
        if not context:
            text = "A whole-book report has no entity context."
            return [text_part(text)], [*proof, "Section: context"]
        return _paged(section, _context_table(payload, context), limit, offset, proof)
    raise ValueError(f"unknown section: {section}")


def _kpis(payload: dict[str, Any]) -> dict:
    periods = payload["periods"]
    window = {
        "a": {"start": periods["prior"]["start"], "end": periods["prior"]["end"]},
        "b": {"start": periods["current"]["start"], "end": periods["current"]["end"]},
    }
    metrics = []
    for name, kpi in payload["kpis"].items():
        friendly, unit = KPI_FRIENDLY.get(name, (name.replace("_", " ").title(), None))
        metrics.append(
            {
                "name": name,
                "friendly": friendly,
                "a": kpi["prior"],
                "b": kpi["current"],
                "unit": unit,
            }
        )
    return metric_grid_part(periods=window, metrics=metrics)


def _row_cells(row: dict[str, Any]) -> list[str]:
    cells: list[str] = []
    for metric, _ in _METRIC_COLUMNS:
        d = row["display"][metric]
        cells += [d["prior"], d["current"], d["change"], d["pct"]]
    return cells


def _ranked_table(section: str, payload: dict[str, Any]) -> _Table:
    if section.startswith("entities"):
        first = payload["entity"]["label"]
    elif section.startswith("ports"):
        first = "Port"
    else:
        first = "Deal class"
    columns = [first]
    for _, label in _METRIC_COLUMNS:
        columns += [f"{label} prior", f"{label} current", f"{label} change", f"{label} %"]
    table = payload["tables"][section]
    source = [*table["rows"]]
    if table.get("other"):
        source.append(table["other"])
    source.append(table["footer"])
    return columns, [[row["key"], *_row_cells(row)] for row in source]


def _movers_table(payload: dict[str, Any]) -> _Table:
    columns = [
        payload["entity"]["label"],
        "Direction",
        "Driver",
        "GP prior",
        "GP current",
        "GP change",
        "GP %",
    ]
    rows = []
    for direction, key in (("up", "increases"), ("down", "decreases")):
        for row in payload["movers"][key]:
            d = row["display"]["GP"]
            rows.append(
                [
                    row["key"],
                    direction,
                    row.get("driver", ""),
                    d["prior"],
                    d["current"],
                    d["change"],
                    d["pct"],
                ]
            )
    return columns, rows


def _new_lost_table(payload: dict[str, Any]) -> _Table:
    columns = [payload["entity"]["label"], "Status", "GP", "Volume", "Margin"]
    rows = []
    for status, side in (("new", "current"), ("lost", "prior")):
        for row in payload["new_lost"][status]:
            d = row["display"]
            rows.append([row["key"], status, d["GP"][side], d["VOLUME"][side], d["MARGIN"][side]])
    return columns, rows


def _joined(items: list[dict[str, Any]]) -> str:
    return "; ".join(f"{item['key']} {item['display']}" for item in items)


def _context_table(payload: dict[str, Any], context: dict[str, Any]) -> _Table:
    columns = [
        payload["entity"]["label"],
        "Book GP prior",
        "Book GP current",
        "Book change",
        "Share prior",
        "Share current",
        "Office change",
        "Other offices net",
        "Offsets",
        "Ports (rest of book)",
    ]
    rows = []
    for key, entry in context.items():
        book = entry["book"]["GP"]["display"]
        share = entry["share_of_book"]["display"]
        rec = entry["reconciliation"]["display"]
        rows.append(
            [
                key,
                book["prior"],
                book["current"],
                rec["book_change"],
                share["prior"],
                share["current"],
                rec["office_change"],
                rec["other_offices_net"],
                _joined(entry["offsets"]),
                _joined(entry["ports_rest_of_book"]),
            ]
        )
    return columns, rows


def _paged(
    section: str, table: _Table, limit: int, offset: int, proof: list[str]
) -> tuple[list[dict], list[str]]:
    columns, rows = table
    total = len(rows)
    page = rows[offset : offset + limit]
    end = offset + len(page)
    parts = [table_part(columns=columns, rows=page)]
    if end < total:
        parts.append(text_part(f"{total - end} rows remaining; ask again with offset={end}."))
    return parts, [*proof, f"Section: {section} rows {offset + 1}-{end} of {total}"]


def _narrative(payload: dict[str, Any], proof: list[str]) -> tuple[list[dict], list[str]]:
    narrative = payload["narrative"]
    use_model = narrative.get("grounded") and narrative.get("llm_md")
    markdown = narrative["llm_md"] if use_model else narrative["template_md"]
    flags = "; ".join(narrative.get("flags") or []) or "none"
    return [text_part(markdown)], [*proof, "Section: narrative", f"Flags: {flags}"]
