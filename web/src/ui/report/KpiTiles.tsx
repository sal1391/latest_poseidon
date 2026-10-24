import { toneFor } from "./tone";
import type { Delta } from "./types";

/** One tile per KPI, in payload order: name, current value, the change line
 * coloured by its sign (ruling P3-R2), the prior value beneath (spec 5). */
export function KpiTiles({ kpis }: { kpis: Record<string, Delta> }) {
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {Object.entries(kpis).map(([metric, d]) => (
        <dl key={metric} data-metric={metric} className="rounded-card border border-border p-3">
          <dt className="text-[12px] uppercase tracking-wide text-ink-muted">{metric}</dt>
          <dd className="text-xl font-semibold tabular-nums">{d.display.current}</dd>
          <dd className={`text-[13px] tabular-nums ${toneFor(d.change)}`}>{`${d.display.change} (${d.display.pct})`}</dd>
          <dd className="text-[12px] text-ink-muted tabular-nums">{`prior ${d.display.prior}`}</dd>
        </dl>
      ))}
    </div>
  );
}
