import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { AuthError, IdentityConfigError, resolveIdentity } from "./lib/identity";

/**
 * The one RFC-7807 body shape this codebase emits, ported field for field --
 * and in the same key ORDER, since that is what reaches the wire -- from
 * `core/skills/result.py:72-79`'s `problem()`. Python renders every identity
 * failure through it (`api/auth.py:171-179`, whose docstring calls out
 * "byte-identical shape, not a second hand-rolled dict"); a proxy that invented
 * its own would make this seam the one place a client sees a different error
 * contract depending on which runtime answered.
 *
 * `type` defaults to `about:blank`, RFC 7807's "the status code is the whole
 * story", exactly as `problem()`'s `type_` does.
 */
function problem(status: number, title: string, detail: string) {
  return { type: "about:blank", title, detail, status };
}

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
    // Checked FIRST, and the two classes are unrelated by design: an operator's
    // misconfiguration is a 500, never a 401. A 401 here would tell the user to
    // re-authenticate against a fault no credential of theirs can fix, and
    // would bury the real cause in a wall of authentication failures. Python
    // keeps the same split (`RuntimeError` vs `AuthError`); see
    // `IdentityConfigError`.
    if (err instanceof IdentityConfigError) {
      return NextResponse.json(problem(500, "identity misconfigured", err.message), {
        status: 500,
      });
    }
    if (err instanceof AuthError) {
      // The status and title come from the error, not from this call site:
      // `require_sales`'s 403 already exists on the Python side, and auth0 will
      // bring more, so hard-coding 401 here would be a bug waiting for the next
      // raiser rather than a simplification.
      return NextResponse.json(problem(err.status, err.title, err.detail), {
        status: err.status,
      });
    }
    // Anything else is a real defect, not a failure mode with a wire contract.
    // Rethrowing hands it to Next's error handling instead of laundering it
    // into a problem body that would imply it was expected.
    throw err;
  }

  // `set`, never `append`: whatever the caller sent under these names is
  // REPLACED, so a client cannot smuggle its own identity past this seam.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-poseidon-sub", identity.sub);
  requestHeaders.set("x-poseidon-roles", identity.roles.join(","));
  return NextResponse.next({ request: { headers: requestHeaders } });
}

/**
 * Which paths the proxy runs on.
 *
 * Without a `matcher` it runs on **every** request, "including static files
 * (`_next/static`), image optimizations (`_next/image`), and assets in the
 * `public/` folder ... otherwise auth logic or redirects can unintentionally
 * block CSS, JS, or images from loading"
 * (`next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md:75`).
 * In `disabled` mode that was only wasted work; in `spcs_ingress` mode every
 * stylesheet and image would have been 401-gated alongside the pages.
 *
 * The negative-lookahead form is the docs' own (`proxy.md:653`), with two
 * deliberate differences from the example printed there:
 *
 *  - **`api` is NOT excluded.** The docs' example drops it because many apps
 *    authenticate inside their route handlers. Here `next.config.ts` rewrites
 *    `/api/:path*` to the FastAPI backend, so `/api/*` is precisely the
 *    traffic that must carry a resolved sub; excluding it would send it past
 *    this seam unresolved. Every app route -- pages and `/api/*` alike -- still
 *    runs the proxy.
 *  - **A static-file extension clause is added**, composed from the same
 *    page's other example (`'/((?!api|_next/static|_next/image|.*\\.png$).*)'`,
 *    `proxy.md:90`), because `web/public/`'s assets are served from the site
 *    root and the metadata-file list alone would not cover them.
 */
export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt|.*\\.(?:svg|png|jpg|jpeg|gif|webp|avif|ico|css|js|map|woff|woff2)$).*)",
  ],
};
