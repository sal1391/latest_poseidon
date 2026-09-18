import type { ReadonlyHeaders } from "next/dist/server/web/spec-extension/adapters/headers";

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
