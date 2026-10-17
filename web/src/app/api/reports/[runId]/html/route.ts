import { serveReport } from "../serve";

export const dynamic = "force-dynamic";

export async function GET(request: Request, { params }: { params: Promise<{ runId: string }> }) {
  return serveReport(request, params, "html");
}
