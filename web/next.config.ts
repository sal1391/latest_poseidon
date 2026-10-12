import type { NextConfig } from "next";

/**
 * Python serves whatever `/api/*` this app has not taken over yet.
 *
 * Exported separately so it can be unit-tested without booting Next.
 *
 * **`fallback`, never a bare array.** Next's rewrite phases are not
 * interchangeable (`node_modules/next/dist/docs/01-app/03-api-reference/
 * 05-config/01-next-config-js/rewrites.md`): an array is applied "after
 * checking the filesystem (pages and `/public` files) and **before dynamic
 * routes**", whereas `fallback` is "checked after both pages/public files and
 * dynamic routes are checked".
 *
 * `app/api/chat/[cid]/route.ts` is a dynamic route. As an array this rule
 * matched first and proxied every chat turn to FastAPI, which has no such
 * path and answered `{"detail":"Not Found"}` -- with no error, no warning,
 * and the handler simply never running. `fallback` is also the honest
 * statement of where the migration is: each route handler added here wins by
 * existing, and there is no exclusion list to keep in step with it.
 */
export async function apiRewrites(target: string) {
  return { fallback: [{ source: "/api/:path*", destination: `${target}/api/:path*` }] };
}

const nextConfig: NextConfig = {
  async rewrites() {
    return apiRewrites(process.env.BACKEND_ORIGIN ?? "http://localhost:8000");
  },
};

export default nextConfig;
