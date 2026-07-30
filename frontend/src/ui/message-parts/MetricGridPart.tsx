import type { MetricGridPayload } from "../../api/types";
import type { PartProps } from "./registry";

/** One metric's value for one compared period, formatted for display.
 * `null` (format_parts.py's own "one side empty is still an answer" rule --
 * a comparison where only one period has rows renders the other as `None`,
 * never a fabricated 0) renders as an em dash, never a blank cell that could
 * be mistaken for a genuine zero. `toLocaleString` supplies the thousands
 * separators the certified value itself never carries. */
function formatValue(value: number | null, unit: string | null): string {
  if (value === null) return "—";
  const formatted = value.toLocaleString("en-US");
  return unit ? `${formatted} ${unit}` : formatted;
}

/** Renders `metric_grid` parts as a card per metric, each showing the two
 * compared periods' values side by side -- theme-tokened, numeric figures
 * set in the tabular-numeral `.data` face (see TablePart's own precedent)
 * so a column of amounts lines up on its decimal point. */
export function MetricGridPart({ part }: PartProps) {
  const { periods, metrics } = part.payload as MetricGridPayload;
  return (
    <div className="metric-grid-part">
      {metrics.map((metric) => (
        <div className="metric-card" key={metric.name}>
          <span className="metric-card-label">{metric.friendly}</span>
          <div className="metric-card-values">
            <div className="metric-card-value-block">
              <span className="metric-card-value data">{formatValue(metric.a, metric.unit)}</span>
              <span className="metric-card-period">
                {periods.a.start} - {periods.a.end}
              </span>
            </div>
            <div className="metric-card-value-block">
              <span className="metric-card-value data">{formatValue(metric.b, metric.unit)}</span>
              <span className="metric-card-period">
                {periods.b.start} - {periods.b.end}
              </span>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
