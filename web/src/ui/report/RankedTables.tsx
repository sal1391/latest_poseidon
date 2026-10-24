import { useState } from "react";
import { TABLE, TD, TD_NUM, TH, TH_NUM, TR } from "../primitives/data-table";
import { toneFor } from "./tone";
import type { MetricKey, RankedTable, ReportPayload, Row } from "./types";

const METRICS: MetricKey[] = ["GP", "VOLUME", "MARGIN"];
const METRIC_LABEL: Record<MetricKey, string> = { GP: "GP", VOLUME: "Volume", MARGIN: "Margin" };
const COLUMNS = ["Prior", "Current", "Change", "%"] as const;
/** The spanning metric header: centred over its four columns, hairline beneath. */
const TH_GROUP = "border-b border-border px-2 pb-1 text-center text-[12px] font-medium uppercase tracking-wide text-ink-muted";
/** Footer cells: bold, rule above, no rule beneath (the table ends there). */
const TF = "border-t border-border px-2 py-1.5 font-semibold";
const TF_NUM = `${TF} text-right tabular-nums`;

/** The twelve metric cells of one row. The Change cell takes the sign's
 * colour (ruling P3-R5) except on the muted Other row. */
function cells(row: Row, numClass: string, muted = false) {
  return METRICS.flatMap((m) => {
    const d = row.display[m];
    return [d.prior, d.current, d.change, d.pct].map((text, i) => (
      <td key={`${m}${i}`} className={i === 2 && !muted ? `${numClass} ${toneFor(row.change[m])}` : numClass}>
        {text}
      </td>
    ));
  });
}

function DataRow({ row, muted = false }: { row: Row; muted?: boolean }) {
  return (
    <tr className={muted ? `${TR} text-ink-muted` : TR}>
      <td className={TD}>{row.key}</td>
      {cells(row, TD_NUM, muted)}
    </tr>
  );
}

/** Five ranked tables behind a segmented control (spec 5): the track on the
 * raised surface, the active segment lifted white in accent. The table has
 * a spanning header per metric over Prior / Current / Change / %, the Other
 * row muted, the footer bold with a rule above. Roles and aria-selected are
 * unchanged. */
export function RankedTables({ tables, entityLabel }: { tables: ReportPayload["tables"]; entityLabel: string }) {
  const tabs: { id: string; label: string; first: string; table: RankedTable }[] = [
    { id: "egp", label: `${entityLabel}s by GP`, first: entityLabel, table: tables.entities_by_gp },
    { id: "evol", label: `${entityLabel}s by volume`, first: entityLabel, table: tables.entities_by_volume },
    { id: "pgp", label: "Ports by GP", first: "Port", table: tables.ports_by_gp },
    { id: "pvol", label: "Ports by volume", first: "Port", table: tables.ports_by_volume },
    { id: "deal", label: "Deal class", first: "Deal class", table: tables.deal_class },
  ];
  const [active, setActive] = useState(tabs[0].id);
  const tab = tabs.find((t) => t.id === active) ?? tabs[0];
  const { rows, other, footer } = tab.table;
  return (
    <section className="space-y-3">
      <div role="tablist" aria-label="Ranked tables" className="inline-flex flex-wrap gap-1 rounded-card bg-surface-raised p-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={t.id === active}
            onClick={() => setActive(t.id)}
            className={`rounded-chip px-3 py-1 text-[13px] transition-colors duration-[120ms] ${
              t.id === active ? "bg-surface font-medium text-accent shadow-1" : "text-ink-muted hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="overflow-x-auto">
        <table className={TABLE}>
          <thead>
            <tr>
              <th rowSpan={2} className={`${TH} align-bottom`}>
                {tab.first}
              </th>
              {METRICS.map((m) => (
                <th key={m} colSpan={4} className={TH_GROUP}>
                  {METRIC_LABEL[m]}
                </th>
              ))}
            </tr>
            <tr>
              {METRICS.flatMap((m) =>
                COLUMNS.map((c) => (
                  <th key={`${m}${c}`} className={`${TH_NUM} pt-1`}>
                    {c}
                  </th>
                )),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <DataRow key={row.key} row={row} />
            ))}
            {other ? <DataRow row={{ ...other, key: other.key || "Other" }} muted /> : null}
          </tbody>
          <tfoot>
            <tr>
              <td className={TF}>{footer.key}</td>
              {cells(footer, TF_NUM)}
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
