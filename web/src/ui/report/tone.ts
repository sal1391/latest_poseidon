/**
 * The colour of a change line, from the payload's numeric change (ruling
 * P3-R2): the number is on the payload already, so no reading of a display
 * string's first character is needed and a currency symbol or a "+" prefix
 * cannot mislead it. Zero is plain ink: neither good nor bad news.
 */
export function toneFor(change: number): "text-positive" | "text-negative" | "text-ink" {
  if (change > 0) return "text-positive";
  if (change < 0) return "text-negative";
  return "text-ink";
}
