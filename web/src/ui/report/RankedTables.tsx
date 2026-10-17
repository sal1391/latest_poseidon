import { useState } from "react";
import type { MetricKey, RankedTable, ReportPayload, Row } from "./types";

const METRICS: MetricKey[] = ["GP", "VOLUME", "MARGIN"];
const METRIC_LABEL: Record<MetricKey, string> = { GP: "GP", VOLUME: "Volume", MARGIN: "Margin" };
const TH = "border-b border-neutral-300 px-2 py-1 text-left font-medium dark:border-neutral-700";
const TD = "border-b border-neutral-200 px-2 py-1 text-right tabular-nums dark:border-neutral-800";

function cells(row: Row) {
  return METRICS.flatMap((m) => {
    const d = row.display[m];
    return [d.prior, d.current, d.change, d.pct].map((text, i) => (
      <td key={`${m}${i}`} className={TD}>
        {text}
      </td>
    ));
  });
}

function DataRow({ row }: { row: Row }) {
  return (
    <tr>
      <td className="border-b border-neutral-200 px-2 py-1 dark:border-neutral-800">{row.key}</td>
      {cells(row)}
    </tr>
  );
}

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
    <section className="space-y-2">
      <div role="group" aria-label="Ranked tables" className="flex flex-wrap gap-1">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            aria-selected={t.id === active}
            onClick={() => setActive(t.id)}
            className={`rounded-md border px-2 py-1 text-sm ${t.id === active ? "border-neutral-500 font-medium" : "border-neutral-200 dark:border-neutral-800"}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              <th className={TH}>{tab.first}</th>
              {METRICS.flatMap((m) =>
                ["prior", "current", "change", "%"].map((c) => (
                  <th key={`${m}${c}`} className={TH}>{`${METRIC_LABEL[m]} ${c}`}</th>
                )),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <DataRow key={row.key} row={row} />
            ))}
            {other ? <DataRow row={{ ...other, key: other.key || "Other" }} /> : null}
          </tbody>
          <tfoot>
            <tr className="font-medium">
              <td className="px-2 py-1">{footer.key}</td>
              {cells(footer)}
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
