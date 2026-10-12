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
      <span className="tabular-nums font-semibold">{formatValue(value)}</span>
      {unit ? <span className="text-xs font-normal text-neutral-500 dark:text-neutral-500">{unit}</span> : null}
    </span>
  );
}

/** Renders `metric_grid` parts as a card per metric, each showing the two
 * compared periods' values side by side. */
export function MetricGridPart({ part }: PartProps) {
  const { periods, metrics } = part.payload as MetricGridPayload;
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {metrics.map((metric) => (
        <div className="rounded-md border border-neutral-200 p-3 dark:border-neutral-800" key={metric.name}>
          <span className="block text-sm font-medium text-neutral-600 dark:text-neutral-400">{metric.friendly}</span>
          <div className="mt-1 flex justify-between gap-4">
            <div className="flex flex-col">
              <MetricValue value={metric.a} unit={metric.unit} />
              <span className="text-xs text-neutral-500 dark:text-neutral-500">
                {periods.a.start} - {periods.a.end}
              </span>
            </div>
            <div className="flex flex-col">
              <MetricValue value={metric.b} unit={metric.unit} />
              <span className="text-xs text-neutral-500 dark:text-neutral-500">
                {periods.b.start} - {periods.b.end}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
