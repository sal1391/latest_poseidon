import type { ReactNode } from "react";

/**
 * A small inline label: the MIME type on an artifact card (spec 4), the
 * mover driver and the month-to-date marker on the report (spec 5). The
 * warn tone is the report's; everything else is neutral.
 */
export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "warn"; children: ReactNode }) {
  const colours = tone === "warn" ? "bg-warn-bg text-warn-ink" : "bg-surface-raised text-ink-muted";
  return <span className={`inline-block rounded-chip px-1.5 py-0.5 text-[12px] ${colours}`}>{children}</span>;
}
