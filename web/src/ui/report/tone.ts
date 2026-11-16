/**
 * The colour of a change line, from the payload's numeric change (ruling
 * P3-R2): the number is on the payload already, so no reading of a display
 * string's first character is needed and a currency symbol or a "+" prefix
 * cannot mislead it. Zero is plain ink: neither good nor bad news.
 *
 * For the metrics in INVERTED a rise is bad news (more lost deals), so the
 * colour is flipped (P3-R2 amendment, Carlos 2026-11-16).
 */
const INVERTED: ReadonlySet<string> = new Set(["NUM_LOST"]);

export function toneFor(change: number, metric?: string): "text-positive" | "text-negative" | "text-ink" {
  const signed = metric !== undefined && INVERTED.has(metric) ? -change : change;
  if (signed > 0) return "text-positive";
  if (signed < 0) return "text-negative";
  return "text-ink";
}
