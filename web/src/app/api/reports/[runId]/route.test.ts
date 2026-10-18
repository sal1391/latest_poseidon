import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { randomUUID } from "node:crypto";
import { reportFileName } from "../../../../lib/report-files";

vi.mock("../../../../lib/reports", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../lib/reports")>();
  return { ...actual, readReportFile: vi.fn() };
});

import { readReportFile } from "../../../../lib/reports";
import { GET as getPdf } from "./pdf/route";
import { GET as getHtml } from "./html/route";

const RUN = "11111111-2222-3333-4444-555555555555";
const params = Promise.resolve({ runId: RUN });
const salesHeaders = { "x-poseidon-sub": "dev|local", "x-poseidon-roles": "Poseidon:Sales" };
const read = vi.mocked(readReportFile);

beforeEach(() => read.mockReset());

describe("report download routes", () => {
  it("answers 403 as a problem detail without the role", async () => {
    const res = await getPdf(
      new Request("http://x/api/reports/r/pdf", { headers: { "x-poseidon-sub": "dev|local", "x-poseidon-roles": "" } }),
      { params },
    );
    expect(res.status).toBe(403);
    expect(await res.json()).toEqual({
      type: "about:blank",
      title: "insufficient role",
      detail: "caller lacks required role 'Poseidon:Sales'",
      status: 403,
    });
    expect(read).not.toHaveBeenCalled();
  });

  it("answers 404 for an unknown run", async () => {
    read.mockResolvedValue(null);
    const res = await getPdf(new Request("http://x/", { headers: salesHeaders }), { params });
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.title).toBe("report not found");
    expect(body.detail).toBe(`no pdf for run '${RUN}'`);
  });

  it("serves the pdf with a sanitised filename", async () => {
    const bytes = Buffer.from("%PDF-fake");
    const fileName = reportFileName(
      { officeValue: 'Rotterdam/"x" Office', periodCurrentStart: "2026-06-01", windowKind: "complete" },
      "pdf",
    );
    read.mockResolvedValue({ bytes, fileName, contentType: "application/pdf" });
    const res = await getPdf(new Request("http://x/", { headers: salesHeaders }), { params });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/pdf");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="Rotterdam__x_ Office June 2026.pdf"');
    expect(res.headers.get("content-length")).toBe(String(bytes.length));
    expect(res.headers.get("cache-control")).toBe("private, no-store");
    expect(Buffer.from(await res.arrayBuffer()).equals(bytes)).toBe(true);
  });

  it("serves html as an attachment too", async () => {
    const bytes = Buffer.from("<p>hi</p>");
    read.mockResolvedValue({ bytes, fileName: "Whole book June 2026.html", contentType: "text/html; charset=utf-8" });
    const res = await getHtml(new Request("http://x/", { headers: salesHeaders }), { params });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/html; charset=utf-8");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="Whole book June 2026.html"');
    expect(read).toHaveBeenCalledWith("dev|local", RUN, "html");
  });
});

const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;

live("readReportFile (live)", () => {
  const id = randomUUID();

  afterAll(async () => {
    const { withUser } = await import("../../../../db/client");
    const { reportRun } = await import("../../../../db/schema");
    const { eq } = await import("drizzle-orm");
    try {
      await withUser("dev|local", (tx) => tx.delete(reportRun).where(eq(reportRun.id, id)));
    } catch {
      // the app role has no DELETE on report_run; ruling 5 accepts accumulation
    }
  });

  it("reads the stored bytes through the real readReportFile", async () => {
    const actual = await vi.importActual<typeof import("../../../../lib/reports")>("../../../../lib/reports");
    const { withUser } = await import("../../../../db/client");
    const { reportRun } = await import("../../../../db/schema");
    await withUser("dev|local", (tx) =>
      tx.insert(reportRun).values({
        id,
        perspective: "book",
        periodCurrentStart: "2026-06-01",
        periodCurrentEnd: "2026-06-30",
        periodPriorStart: "2026-05-01",
        periodPriorEnd: "2026-05-31",
        windowKind: "complete",
        status: "ok",
        payloadVersion: 1,
        payload: {},
        pdf: Buffer.from("%PDF-live"),
        narrativeGrounded: false,
        source: "test",
        createdBy: "dev|local",
        generatedAt: new Date().toISOString(),
      }),
    );
    const got = await actual.readReportFile("dev|local", id, "pdf");
    expect(got?.bytes.toString()).toBe("%PDF-live");
    expect(got?.contentType).toBe("application/pdf");
    expect(got?.fileName).toBe("Whole book June 2026.pdf");
    expect(await actual.readReportFile("dev|local", id, "html")).toBeNull();
    expect(await actual.readReportFile("dev|local", "not-a-uuid", "pdf")).toBeNull();
  });
});
