import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";
import { randomUUID } from "node:crypto";

vi.mock("../../../../lib/briefs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../lib/briefs")>();
  return { ...actual, readBriefFile: vi.fn() };
});

import { readBriefFile } from "../../../../lib/briefs";
import { GET as getPdf } from "./pdf/route";
import { GET as getHtml } from "./html/route";

const RUN = "11111111-2222-3333-4444-555555555555";
const params = Promise.resolve({ runId: RUN });
const salesHeaders = { "x-poseidon-sub": "dev|local", "x-poseidon-roles": "Poseidon:Sales" };
const read = vi.mocked(readBriefFile);

beforeEach(() => read.mockReset());

describe("brief download routes", () => {
  it("answers 403 as a problem detail without the role", async () => {
    const res = await getPdf(
      new Request("http://x/api/briefs/r/pdf", { headers: { "x-poseidon-sub": "dev|local", "x-poseidon-roles": "" } }),
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
    expect(body.title).toBe("brief not found");
    expect(body.detail).toBe(`no pdf for run '${RUN}'`);
  });

  it("serves the pdf as an attachment", async () => {
    const bytes = Buffer.from("%PDF-fake");
    read.mockResolvedValue({ bytes, fileName: "Acme Marine Brief.pdf", contentType: "application/pdf" });
    const res = await getPdf(new Request("http://x/", { headers: salesHeaders }), { params });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("application/pdf");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="Acme Marine Brief.pdf"');
    expect(res.headers.get("content-length")).toBe(String(bytes.length));
    expect(res.headers.get("cache-control")).toBe("private, no-store");
    expect(Buffer.from(await res.arrayBuffer()).equals(bytes)).toBe(true);
  });

  it("serves html as an attachment too", async () => {
    const bytes = Buffer.from("<p>hi</p>");
    read.mockResolvedValue({ bytes, fileName: "Acme Marine Brief.html", contentType: "text/html; charset=utf-8" });
    const res = await getHtml(new Request("http://x/", { headers: salesHeaders }), { params });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("text/html; charset=utf-8");
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="Acme Marine Brief.html"');
    expect(read).toHaveBeenCalledWith("dev|local", RUN, "html");
  });
});

const live = process.env.DRIZZLE_DATABASE_URL ? describe : describe.skip;

live("readBriefFile (live)", () => {
  const id = randomUUID();

  afterAll(async () => {
    const { withUser } = await import("../../../../db/client");
    const { briefRun } = await import("../../../../db/schema");
    const { eq } = await import("drizzle-orm");
    try {
      await withUser("dev|local", (tx) => tx.delete(briefRun).where(eq(briefRun.id, id)));
    } catch {
      // the app role has no DELETE on brief_run; accumulation is accepted
    }
  });

  it("reads the stored bytes through the real readBriefFile", async () => {
    const actual = await vi.importActual<typeof import("../../../../lib/briefs")>("../../../../lib/briefs");
    const { withUser } = await import("../../../../db/client");
    const { briefRun } = await import("../../../../db/schema");
    await withUser("dev|local", (tx) =>
      tx.insert(briefRun).values({
        id,
        kind: "existing_customer",
        subject: 'A/S "Rederi"',
        title: 'A/S "Rederi" Brief',
        anchorDate: "2026-07-01",
        status: "ok",
        markdown: "# x",
        pdf: Buffer.from("%PDF-live"),
        createdBy: "dev|local",
        generatedAt: new Date().toISOString(),
      }),
    );
    const got = await actual.readBriefFile("dev|local", id, "pdf");
    expect(got?.bytes.toString()).toBe("%PDF-live");
    expect(got?.contentType).toBe("application/pdf");
    expect(got?.fileName).toBe("A_S _Rederi_ Brief.pdf");
    expect(await actual.readBriefFile("dev|local", id, "html")).toBeNull();
    expect(await actual.readBriefFile("dev|local", "not-a-uuid", "pdf")).toBeNull();
  });
});
