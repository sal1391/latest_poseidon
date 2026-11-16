import { readReportFile } from "../../../../lib/reports";
import { serveDocument } from "../../../../lib/serve-document";

export async function serveReport(
  request: Request,
  params: Promise<{ runId: string }>,
  kind: "pdf" | "html",
): Promise<Response> {
  return serveDocument(request, params, kind, (s, r, k) => readReportFile(s, r, k), "report not found");
}
