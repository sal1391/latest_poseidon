/**
 * `VITE_AUTH0_*` -- the frontend half of doc 05 section 9's tenant config
 * (the backend reads the unprefixed `AUTH0_DOMAIN`/`AUTH0_AUDIENCE`/
 * `AUTH0_CLIENT_ID`; this is the `VITE_`-prefixed mirror Vite actually
 * exposes to client code via `import.meta.env` -- Vite never ships a
 * bare, unprefixed env var to the browser bundle). All three must be
 * present for the SPA to mount the Auth0 wrapper at all: a partially
 * configured tenant (e.g. a domain with no audience) is treated
 * identically to "not configured" (`AuthGate`'s "identity misconfigured"
 * screen), never a half-wired login attempt against an incomplete
 * config the API would just reject anyway.
 */
export interface Auth0Config {
  domain: string;
  clientId: string;
  audience: string;
}

export function readAuth0Config(): Auth0Config | null {
  const env = import.meta.env;
  const domain = env.VITE_AUTH0_DOMAIN;
  const clientId = env.VITE_AUTH0_CLIENT_ID;
  const audience = env.VITE_AUTH0_AUDIENCE;
  if (!domain || !clientId || !audience) return null;
  return { domain, clientId, audience };
}
