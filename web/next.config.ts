import type { NextConfig } from "next";

/** Exported separately so it can be unit-tested without booting Next. */
export async function apiRewrites(target: string) {
  return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
}

const nextConfig: NextConfig = {
  async rewrites() {
    return apiRewrites(process.env.BACKEND_ORIGIN ?? "http://localhost:8000");
  },
};

export default nextConfig;
