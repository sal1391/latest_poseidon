import { eq } from "drizzle-orm";
import { withUser } from "../db/client";
import { briefRun } from "../db/schema";
import { sanitizeFilename } from "./report-files";
import { RUN_ID_PATTERN } from "./reports";
import type { DocumentFile } from "./serve-document";

export async function readBriefFile(
  sub: string,
  runId: string,
  kind: "pdf" | "html",
): Promise<DocumentFile | null> {
  if (!RUN_ID_PATTERN.test(runId)) return null;
  const row = await withUser(sub, async (tx) => {
    const [r] = await tx
      .select({ title: briefRun.title, bytes: kind === "pdf" ? briefRun.pdf : briefRun.html })
      .from(briefRun)
      .where(eq(briefRun.id, runId));
    return r;
  });
  if (!row || row.bytes == null) return null;
  return {
    bytes: Buffer.from(row.bytes),
    fileName: `${sanitizeFilename(row.title)}.${kind}`,
    contentType: kind === "pdf" ? "application/pdf" : "text/html; charset=utf-8",
  };
}
