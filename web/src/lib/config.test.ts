import { describe, expect, it } from "vitest";
import { apiRewrites } from "../../next.config";

describe("apiRewrites", () => {
  it("forwards /api/* to the FastAPI backend", async () => {
    const rules = await apiRewrites("http://localhost:8000");
    expect(rules).toEqual([
      { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
    ]);
  });
});
