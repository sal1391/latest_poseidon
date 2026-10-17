import type { Delta } from "./types";

export function KpiTiles({ kpis }: { kpis: Record<string, Delta> }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {Object.entries(kpis).map(([metric, d]) => (
        <dl key={metric} data-metric={metric} className="rounded-md border border-neutral-200 p-2 text-sm dark:border-neutral-800">
          <dt className="text-xs text-neutral-500">{metric}</dt>
          <dd className="text-lg font-semibold tabular-nums">{d.display.current}</dd>
          <dd className="tabular-nums">{`${d.display.change} (${d.display.pct})`}</dd>
          <dd className="text-xs text-neutral-500 tabular-nums">{`prior ${d.display.prior}`}</dd>
        </dl>
      ))}
    </div>
  );
}
