import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { AuthError, resolveIdentity } from "./lib/identity";

/**
 * Next 16 renamed this convention from `middleware` to `proxy`. Two things
 * about this file are load-bearing and fail SILENTLY when got wrong -- it is
 * never picked up, and no error is reported:
 *
 *  1. The export must be named `proxy` (or be the default export). A function
 *     named `middleware` in a file named `proxy.ts` simply never runs.
 *  2. The file must sit next to `app`, so with this project's `src/` layout it
 *     belongs at `web/src/proxy.ts`, NOT `web/proxy.ts`. Next resolves it from
 *     `path.join(pagesDir || appDir, "..")` (`next/dist/build/index.js`), which
 *     here is `web/src`; a `proxy.ts` at the repo-app root is never even read.
 */
export function proxy(request: NextRequest) {
  const mode = (process.env.IDENTITY_MODE ?? "disabled") as
    | "disabled"
    | "spcs_ingress"
    | "auth0";
  const deployMode = process.env.DEPLOY_MODE ?? "local";

  let identity;
  try {
    identity = resolveIdentity(mode, deployMode, request.headers);
  } catch (err) {
    if (err instanceof AuthError) {
      return NextResponse.json(
        { type: "about:blank", title: "unauthorized", detail: err.message },
        { status: 401 },
      );
    }
    throw err;
  }

  // `set`, never `append`: whatever the caller sent under these names is
  // REPLACED, so a client cannot smuggle its own identity past this seam.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-poseidon-sub", identity.sub);
  requestHeaders.set("x-poseidon-roles", identity.roles.join(","));
  return NextResponse.next({ request: { headers: requestHeaders } });
}
