import { describe, expect, it } from "vitest";
import { apiRewrites } from "../../next.config";

describe("apiRewrites", () => {
  it("forwards /api/* to the FastAPI backend", async () => {
    const rules = await apiRewrites("http://localhost:8000");
    expect(rules).toEqual({
      fallback: [{ source: "/api/:path*", destination: "http://localhost:8000/api/:path*" }],
    });
  });

  /**
   * The phase is the whole point, and getting it wrong is silent. Next's own
   * docs (`node_modules/next/dist/docs/01-app/03-api-reference/05-config/
   * 01-next-config-js/rewrites.md`): an ARRAY is applied "after checking the
   * filesystem (pages and `/public` files) and **before dynamic routes**",
   * while `fallback` is "checked after both pages/public files and dynamic
   * routes are checked".
   *
   * `app/api/chat/[cid]/route.ts` is a DYNAMIC route, so as a bare array this
   * rewrite swallowed every chat turn and handed it to FastAPI, which answered
   * `{"detail":"Not Found"}` -- observed live before this changed. There is no
   * error and no warning: the handler simply never runs.
   *
   * `fallback` also states the migration's actual shape -- Python serves what
   * Next has not taken over yet -- so each route handler added from here on
   * wins by existing, with no exclusion list to keep in step.
   */
  it("is a fallback, not a bare array, so Next's own route handlers win over it", async () => {
    const rules = await apiRewrites("http://localhost:8000");
    expect(Array.isArray(rules)).toBe(false);
    expect(Object.keys(rules)).toEqual(["fallback"]);
  });
});
