import { eq } from "drizzle-orm";
import { withUser } from "../db/client";
import { reportRun } from "../db/schema";
import { reportFileName } from "./report-files";

export const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function readReportFile(
  sub: string,
  runId: string,
  kind: "pdf" | "html",
): Promise<{ bytes: Buffer; fileName: string; contentType: string } | null> {
  if (!RUN_ID_PATTERN.test(runId)) return null;
  const row = await withUser(sub, async (tx) => {
    const [r] = await tx
      .select({
        officeValue: reportRun.officeValue,
        periodCurrentStart: reportRun.periodCurrentStart,
        windowKind: reportRun.windowKind,
        bytes: kind === "pdf" ? reportRun.pdf : reportRun.html,
      })
      .from(reportRun)
      .where(eq(reportRun.id, runId));
    return r;
  });
  if (!row || row.bytes == null) return null;
  return {
    bytes: Buffer.from(row.bytes),
    fileName: reportFileName(row, kind),
    contentType: kind === "pdf" ? "application/pdf" : "text/html; charset=utf-8",
  };
}
