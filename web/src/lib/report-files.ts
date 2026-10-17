/** Keep [A-Za-z0-9 ._-], replace the rest with "_", collapse whitespace, trim (ruling 13). */
export function sanitizeFilename(name: string): string {
  const cleaned = name
    .replace(/[^A-Za-z0-9 ._-]/g, "_")
    .replace(/ {2,}/g, " ")
    .trim();
  return cleaned === "" ? "report" : cleaned;
}

/** "2026-06-01" -> "June 2026", independent of the host timezone. */
export function monthLabel(isoFirstDay: string): string {
  return new Date(`${isoFirstDay}T00:00:00Z`).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

export function reportFileName(
  run: { officeValue: string | null; periodCurrentStart: string; windowKind: string },
  ext: "pdf" | "html",
): string {
  const label = `${run.officeValue ?? "Whole book"} ${monthLabel(run.periodCurrentStart)}`;
  return `${sanitizeFilename(label + (run.windowKind === "mtd" ? " month to date" : ""))}.${ext}`;
}
