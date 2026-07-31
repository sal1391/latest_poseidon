import { useEffect, useState } from "react";
import { ApiError, getMe, setAuthTokenProvider } from "../../api/client";
import type { Identity } from "../../api/client";
import type { Auth0Config } from "./auth0Config";

type Auth0Module = typeof import("@auth0/auth0-react");

/**
 * The ONE place `@auth0/auth0-react` is ever referenced, and only through
 * a dynamic `import()` inside an effect -- so the SDK's own module-scope
 * code never runs unless a component actually renders THIS component,
 * which `AuthGate` only does after a real 401 from `GET /api/me` with
 * `VITE_AUTH0_*` configured (see that module's own docstring). Disabled/
 * spcs_ingress mode never reaches this file at all -- pinned by
 * `AuthGate.test.tsx`'s own lazy-import spy, which never fires as long as
 * no test in that file exercises this branch.
 */
export function Auth0Boundary({
  config,
  onAuthenticated,
}: {
  config: Auth0Config;
  onAuthenticated: (identity: Identity, logout: () => void) => void;
}) {
  const [mod, setMod] = useState<Auth0Module | null>(null);

  useEffect(() => {
    let cancelled = false;
    void import("@auth0/auth0-react").then((imported) => {
      if (!cancelled) setMod(imported);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!mod) {
    return (
      <div role="status" aria-live="polite" className="auth-loading">
        Loading sign-in…
      </div>
    );
  }

  const { Auth0Provider } = mod;
  return (
    <Auth0Provider
      domain={config.domain}
      clientId={config.clientId}
      authorizationParams={{ audience: config.audience, redirect_uri: window.location.origin }}
      useRefreshTokens
      // D15 (Global Constraints): tokens NEVER in localStorage -- SDK
      // memory only. Explicit rather than trusting the SDK's own default
      // to always stay "memory".
      cacheLocation="memory"
    >
      <Auth0Inner mod={mod} onAuthenticated={onAuthenticated} />
    </Auth0Provider>
  );
}

function Auth0Inner({
  mod,
  onAuthenticated,
}: {
  mod: Auth0Module;
  onAuthenticated: (identity: Identity, logout: () => void) => void;
}) {
  const { useAuth0 } = mod;
  const { isLoading, isAuthenticated, loginWithRedirect, getAccessTokenSilently, logout } = useAuth0();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!isAuthenticated) return;
    setAuthTokenProvider(() => {
      // Called with no options: the SDK's own overloads (auth0-context.d.ts)
      // resolve this exact call shape to `Promise<string>`, never the
      // verbose `{access_token, ...}` response (that shape only comes back
      // when `detailedResponse: true` is explicitly passed, which this
      // call site never does).
      return getAccessTokenSilently();
    });
    getMe()
      .then((identity) => {
        onAuthenticated(identity, () =>
          logout({ logoutParams: { returnTo: window.location.origin } }),
        );
      })
      .catch((err: unknown) => {
        const detail =
          err instanceof ApiError && err.problem ? err.problem.detail : "sign-in did not complete";
        setError(detail);
      });
  }, [isAuthenticated, getAccessTokenSilently, logout, onAuthenticated]);

  if (isLoading) {
    return (
      <div role="status" aria-live="polite" className="auth-loading">
        Loading…
      </div>
    );
  }

  if (!isAuthenticated) {
    return (
      <div className="app-shell auth-login-gate">
        <h1>Sign in to Poseidon</h1>
        <p>Sign in with your Poseidon account to continue.</p>
        <button type="button" onClick={() => void loginWithRedirect()}>
          Log in
        </button>
      </div>
    );
  }

  return (
    <div className="app-shell auth-login-gate" role="status" aria-live="polite">
      <p>Signing you in…</p>
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}
