import type { MetricGridPayload, PartProps } from "./registry";

/** One metric's value for one compared period, formatted for display.
 * `null` (one side of the comparison returned no rows) renders as an em
 * dash, never a blank cell that could be mistaken for a genuine zero.
 * `toLocaleString` supplies the thousands separators the certified value
 * itself never carries. The unit (when present) is rendered in its own
 * element -- not fused into this string -- so the bare value stays queryable
 * on its own. */
function formatValue(value: number | null): string {
  if (value === null) return "—"; // em dash
  return value.toLocaleString("en-US");
}

function MetricValue({ value, unit }: { value: number | null; unit: string | null }) {
  return (
    <span className="flex items-baseline gap-1">
      <span className="text-lg font-semibold tabular-nums">{formatValue(value)}</span>
      {unit ? <span className="text-[12px] text-ink-muted">{unit}</span> : null}
    </span>
  );
}

/** Renders `metric_grid` parts as a card per metric, each showing the two
 * compared periods' values side by side; two cards across from 640px
 * (spec 4). */
export function MetricGridPart({ part }: PartProps) {
  const { periods, metrics } = part.payload as MetricGridPayload;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {metrics.map((metric) => (
        <div className="rounded-card border border-border p-3" key={metric.name}>
          <span className="block text-[12px] uppercase tracking-wide text-ink-muted">{metric.friendly}</span>
          <div className="mt-1 grid grid-cols-2 gap-4">
            <div className="flex flex-col">
              <MetricValue value={metric.a} unit={metric.unit} />
              <span className="text-[12px] text-ink-muted tabular-nums">
                {periods.a.start} - {periods.a.end}
              </span>
            </div>
            <div className="flex flex-col">
              <MetricValue value={metric.b} unit={metric.unit} />
              <span className="text-[12px] text-ink-muted tabular-nums">
                {periods.b.start} - {periods.b.end}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
