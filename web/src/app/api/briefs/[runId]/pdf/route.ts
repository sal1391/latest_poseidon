import { readBriefFile } from "../../../../../lib/briefs";
import { serveDocument } from "../../../../../lib/serve-document";

export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ runId: string }> }) {
  return serveDocument(request, params, "pdf", readBriefFile, "brief not found");
}
