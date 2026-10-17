import { readReportFile } from "../../../../lib/reports";
import { salesOr403 } from "../../../../lib/request-identity";

export async function serveReport(
  request: Request,
  params: Promise<{ runId: string }>,
  kind: "pdf" | "html",
): Promise<Response> {
  const gate = salesOr403(request.headers);
  if (!gate.ok) return gate.response;
  const { runId } = await params;
  const file = await readReportFile(gate.sub, runId, kind);
  if (!file) {
    return Response.json(
      { type: "about:blank", title: "report not found", detail: `no ${kind} for run '${runId}'`, status: 404 },
      { status: 404 },
    );
  }
  return new Response(new Uint8Array(file.bytes), {
    status: 200,
    headers: {
      "content-type": file.contentType,
      "content-length": String(file.bytes.length),
      "content-disposition": `attachment; filename="${file.fileName}"`,
      "cache-control": "private, no-store",
    },
  });
}
