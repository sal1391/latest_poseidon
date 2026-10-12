/**
 * Next.js boot hook: `register()` runs once per server instance. Guarded to
 * the Node runtime because the probe opens a socket. A thrown error here
 * stops the server from starting, which is the point (spec section 9).
 */
export async function register() {
  if (process.env.NEXT_RUNTIME !== "nodejs") return;
  const { bootCheck } = await import("./db/boot");
  await bootCheck();
}
