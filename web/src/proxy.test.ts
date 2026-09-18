import { afterEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { config, proxy } from "./proxy";

/**
 * Tests for the Next 16 proxy itself, not just the resolver behind it. Three
 * of the things this file pins are invisible from `identity.test.ts`:
 *
 *  - the wire body a failure renders (it must be the SAME RFC-7807 shape
 *    `core/skills/result.py`'s `problem()` produces, not a second hand-rolled
 *    dict -- see `api/auth.py:171-179`),
 *  - that a caller cannot smuggle its own `x-poseidon-sub` past this seam,
 *  - and which paths the proxy runs on at all (`config.matcher`).
 *
 * Env hygiene: every case stubs what it reads and `vi.unstubAllEnvs` restores
 * it, so no test leaks `IDENTITY_MODE` into the next one or depends on the
 * shell that launched vitest.
 */
afterEach(() => {
  vi.unstubAllEnvs();
});

const req = (path = "/", headers: Record<string, string> = {}) =>
  new NextRequest(new URL(`http://localhost:3000${path}`), { headers: new Headers(headers) });

/**
 * `NextResponse.next({ request: { headers } })` does not mutate the request in
 * place -- it encodes the rewritten request headers ONTO the response, one
 * `x-middleware-request-<name>` per header plus an `x-middleware-override-
 * headers` index (`next/dist/server/web/spec-extension/response.js`,
 * `handleMiddlewareField`). That encoding is what the runtime replays onto the
 * onward request, so it is what these tests read.
 */
const forwarded = (res: Response, name: string) => res.headers.get(`x-middleware-request-${name}`);

describe("proxy header forwarding", () => {
  it("forwards the resolved sub and roles when IDENTITY_MODE/DEPLOY_MODE are unset", async () => {
    vi.stubEnv("IDENTITY_MODE", undefined);
    vi.stubEnv("DEPLOY_MODE", undefined);

    const res = proxy(req("/chat"));

    expect(res.status).toBe(200);
    expect(res.headers.get("x-middleware-next")).toBe("1");
    expect(forwarded(res, "x-poseidon-sub")).toBe("dev|local");
    expect(forwarded(res, "x-poseidon-roles")).toBe("Poseidon:Sales");
  });

  it("REPLACES a forged inbound x-poseidon-sub/-roles instead of appending to it", async () => {
    // The whole security claim of `set` over `append`. With `append`, the
    // onward header would read "sf|attacker, dev|local" and whichever consumer
    // split on the comma first would decide who the caller is.
    vi.stubEnv("IDENTITY_MODE", undefined);
    vi.stubEnv("DEPLOY_MODE", undefined);

    const res = proxy(
      req("/chat", {
        "x-poseidon-sub": "sf|attacker",
        "x-poseidon-roles": "Poseidon:Sales,Poseidon:Admin",
      }),
    );

    expect(forwarded(res, "x-poseidon-sub")).toBe("dev|local");
    expect(forwarded(res, "x-poseidon-roles")).toBe("Poseidon:Sales");
    expect(forwarded(res, "x-poseidon-sub")).not.toContain("attacker");
    expect(forwarded(res, "x-poseidon-roles")).not.toContain("Admin");
    // Named once in the override index, not twice.
    const overridden = (res.headers.get("x-middleware-override-headers") ?? "").split(",");
    expect(overridden.filter((name) => name === "x-poseidon-sub")).toHaveLength(1);
  });

  it("forwards the act-as sub in disabled mode", async () => {
    vi.stubEnv("IDENTITY_MODE", "disabled");
    vi.stubEnv("DEPLOY_MODE", "local");

    const res = proxy(req("/chat", { "x-dev-user": "ALICE" }));

    expect(forwarded(res, "x-poseidon-sub")).toBe("dev|alice");
  });

  it("forwards an empty roles header for an authenticated but unlisted spcs user", async () => {
    vi.stubEnv("IDENTITY_MODE", "spcs_ingress");
    vi.stubEnv("DEPLOY_MODE", "spcs");
    vi.stubEnv("SPCS_SALES_USERS", "");

    const res = proxy(req("/chat", { "sf-context-current-user": "MALLORY" }));

    expect(forwarded(res, "x-poseidon-sub")).toBe("sf|mallory");
    expect(forwarded(res, "x-poseidon-roles")).toBe("");
  });
});

describe("proxy failure rendering", () => {
  it("renders an AuthError as the same four-field problem Python renders", async () => {
    // Python: api/auth.py:179 -> core/skills/result.py:72-79 produces
    // {"type","title","detail","status"} in that key order, and for THIS
    // failure identity_spcs.py:113-114,155 pins the title/detail pair.
    vi.stubEnv("IDENTITY_MODE", "spcs_ingress");
    vi.stubEnv("DEPLOY_MODE", "spcs");

    const res = proxy(req("/chat"));

    expect(res.status).toBe(401);
    const body = await res.json();
    expect(body).toEqual({
      type: "about:blank",
      title: "missing spcs identity header",
      detail: "no valid Sf-Context-Current-User header",
      status: 401,
    });
    // Key order too: `problem()` emits type, title, detail, status, and
    // "byte-identical" is the stated contract for this port.
    expect(Object.keys(body)).toEqual(["type", "title", "detail", "status"]);
  });

  it("renders a misconfigured deploy mode as a 500 problem, never a 401", async () => {
    // Python raises RuntimeError here (identity_spcs.py:131-137), at BOOT, and
    // reserves AuthError for a credential/trust failure (api/auth.py:21). A
    // 401 would tell the user to log in again for an operator's mistake.
    vi.stubEnv("IDENTITY_MODE", "spcs_ingress");
    vi.stubEnv("DEPLOY_MODE", "local");

    const res = proxy(req("/chat", { "sf-context-current-user": "carlos" }));

    expect(res.status).toBe(500);
    const body = await res.json();
    expect(body.type).toBe("about:blank");
    expect(body.title).toBe("identity misconfigured");
    expect(body.detail).toMatch(/deploy mode/i);
    expect(body.status).toBe(500);
    expect(Object.keys(body)).toEqual(["type", "title", "detail", "status"]);
  });

  it("renders an unrecognised IDENTITY_MODE as a 500 problem naming the bad value", async () => {
    // IDENTITY_MODE is an unvalidated env string this file casts into the
    // union, so an operator's typo really does arrive in the resolver.
    vi.stubEnv("IDENTITY_MODE", "diabled");
    vi.stubEnv("DEPLOY_MODE", "local");

    const res = proxy(req("/chat"));

    expect(res.status).toBe(500);
    const body = await res.json();
    expect(body.title).toBe("identity misconfigured");
    expect(body.detail).toContain("diabled");
  });

  it("rethrows an error that is neither an auth nor a config fault", () => {
    // An unexpected failure must NOT be laundered into a 401/500 problem body;
    // it belongs to Next's error handling. Forcing one from inside the try
    // block: the resolver's own throws are all typed, so the honest way to
    // reach the rethrow branch is to make reading the headers fail.
    vi.stubEnv("IDENTITY_MODE", "disabled");
    vi.stubEnv("DEPLOY_MODE", "local");
    const request = req("/chat");
    Object.defineProperty(request, "headers", {
      configurable: true,
      get() {
        throw new TypeError("headers exploded");
      },
    });

    expect(() => proxy(request)).toThrow(TypeError);
    expect(() => proxy(request)).toThrow(/headers exploded/);
  });
});

describe("proxy matcher", () => {
  /**
   * `config.matcher` is a path-to-regexp source string, and Next splices the
   * parenthesised group into a path regex. Anchoring it here is a faithful
   * enough approximation to pin WHICH paths the negative lookahead excludes;
   * `next build` printing the `ƒ Proxy` line is the separate proof that Next
   * itself accepts the pattern (an invalid matcher fails the build).
   */
  const pattern = new RegExp(`^${config.matcher[0]}$`);
  const runsOn = (pathname: string) => pattern.test(pathname);

  it("excludes Next's static and image pipelines", () => {
    expect(runsOn("/_next/static/chunks/main-abc123.js")).toBe(false);
    expect(runsOn("/_next/static/css/app.css")).toBe(false);
    expect(runsOn("/_next/image")).toBe(false);
  });

  it("excludes favicon, the metadata files and public/ assets", () => {
    expect(runsOn("/favicon.ico")).toBe(false);
    expect(runsOn("/robots.txt")).toBe(false);
    expect(runsOn("/sitemap.xml")).toBe(false);
    // Everything currently in web/public/.
    expect(runsOn("/file.svg")).toBe(false);
    expect(runsOn("/globe.svg")).toBe(false);
    expect(runsOn("/next.svg")).toBe(false);
    expect(runsOn("/vercel.svg")).toBe(false);
    expect(runsOn("/window.svg")).toBe(false);
  });

  it("still runs on every app route, including the /api/* rewrite to FastAPI", () => {
    expect(runsOn("/")).toBe(true);
    expect(runsOn("/chat")).toBe(true);
    expect(runsOn("/chat/abc-123")).toBe(true);
    // next.config.ts rewrites /api/:path* to the Python backend. Excluding
    // `api` -- as the docs' own example does -- would send backend-bound
    // requests past the identity seam unresolved, which is exactly the traffic
    // that needs a sub.
    expect(runsOn("/api/conversations")).toBe(true);
    expect(runsOn("/api/me")).toBe(true);
  });
});
