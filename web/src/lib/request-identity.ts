import type { ReadonlyHeaders } from "next/dist/server/web/spec-extension/adapters/headers";
import { SALES_ROLE } from "./identity";

/**
 * `web/src/proxy.ts` is the ONLY thing that sets `x-poseidon-sub` on a
 * forwarded request (Ruling R28). A request reaching a server component
 * without that header means the proxy did not run for it -- a configuration
 * fault, never a real absent identity. That is why this throws instead of
 * defaulting to `"dev|local"`: a silent default would make a misconfigured
 * proxy indistinguishable from a genuine local-dev caller, and answering
 * with `notFound()` further downstream would disguise the fault as "this
 * conversation doesn't exist" instead of surfacing it as the wiring bug it
 * is. Blank/whitespace-only counts as absent for the same reason -- it is
 * not a value any real identity resolution in `resolveIdentity` ever
 * produces.
 */
export function requireSub(h: Headers | ReadonlyHeaders): string {
  const sub = h.get("x-poseidon-sub");
  if (!sub || sub.trim() === "") {
    throw new Error("x-poseidon-sub missing: the proxy did not run for this request");
  }
  return sub;
}

/**
 * The role half, for handlers and pages. The proxy already answered 403 to
 * any request lacking the role, so reaching here without it is the same
 * class of fault as a missing sub: the proxy did not run. Throw, never render.
 */
export function requireSales(h: Headers | ReadonlyHeaders): { sub: string; roles: string[] } {
  const sub = requireSub(h);
  const roles = (h.get("x-poseidon-roles") ?? "").split(",").filter((r) => r !== "");
  if (!roles.includes(SALES_ROLE)) {
    throw new Error(
      `x-poseidon-roles lacks ${SALES_ROLE}: the proxy should have answered 403 before this request reached a handler`,
    );
  }
  return { sub, roles };
}
