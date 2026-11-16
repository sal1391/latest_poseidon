import { salesOr403 } from "./request-identity";

export type DocumentFile = { bytes: Buffer; fileName: string; contentType: string };
export type DocumentReader = (sub: string, runId: string, kind: "pdf" | "html") => Promise<DocumentFile | null>;

export async function serveDocument(
  request: Request,
  params: Promise<{ runId: string }>,
  kind: "pdf" | "html",
  read: DocumentReader,
  notFoundTitle: string,
): Promise<Response> {
  const gate = salesOr403(request.headers);
  if (!gate.ok) return gate.response;
  const { runId } = await params;
  const file = await read(gate.sub, runId, kind);
  if (!file) {
    return Response.json(
      { type: "about:blank", title: notFoundTitle, detail: `no ${kind} for run '${runId}'`, status: 404 },
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
