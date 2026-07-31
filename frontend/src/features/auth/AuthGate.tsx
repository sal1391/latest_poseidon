import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { ApiError, getMe } from "../../api/client";
import type { Identity, ProblemDetail } from "../../api/client";
import { Auth0Boundary } from "./Auth0Boundary";
import type { Auth0Config } from "./auth0Config";
import { readAuth0Config } from "./auth0Config";
import { IdentityContext } from "./identityContext";
import { ProblemScreen } from "./ProblemScreen";
import { RoleGuard } from "./RoleGuard";

const REQUIRED_ROLE = "Poseidon:Sales";

type Status =
  | { kind: "checking" }
  | { kind: "ready"; identity: Identity; logout: () => void }
  | { kind: "needs-login"; config: Auth0Config }
  | { kind: "misconfigured"; problem: ProblemDetail | null }
  | { kind: "unreachable" };

/**
 * The SPA's boot-time identity check (doc 05 section 2's frontend seam):
 * calls `GET /api/me` once per mount/retry and branches on the result --
 * the Controller's binding resolution (task-4-brief.md) for this task's
 * main ambiguity:
 *
 * - `200` -> render the app immediately; identity AND `identity_mode`
 *   come from the payload (`disabled` and `spcs_ingress` providers always
 *   admit, so those modes always take this path).
 * - `401` -> only reachable in `auth0` mode (the only provider that ever
 *   raises `AuthError` from `current_user`). `VITE_AUTH0_*` present ->
 *   mount `Auth0Boundary`, which drives the login gate / re-check cycle
 *   itself; absent -> "identity misconfigured", never a blank screen and
 *   never the login gate.
 * - anything else (network failure, 5xx, ...) -> not one of the four
 *   pinned branches, but the boot sequence must not hang or blank-screen
 *   when the backend is simply not up yet (a common local-dev state) --
 *   a retry-able card mirrors `ChatScreen`'s own `.error-card` convention.
 *
 * The fourth pinned branch (403, "authenticated but no Poseidon:Sales
 * role") never arrives as an HTTP 403 from THIS endpoint: `GET /api/me`
 * depends on `current_user` alone, never `require_sales`
 * (`api/auth.py`'s own docstring), specifically so a role-less caller can
 * still discover who they are. `RoleGuard` is what turns a 200 response
 * whose `roles` omits `Poseidon:Sales` into the same problem-shaped "no
 * access" screen a real 403 from a role-gated route would show.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>({ kind: "checking" });

  const check = useCallback(() => {
    setStatus({ kind: "checking" });
    getMe()
      .then((identity) => {
        setStatus({ kind: "ready", identity, logout: () => undefined });
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          const config = readAuth0Config();
          if (config) {
            setStatus({ kind: "needs-login", config });
          } else {
            setStatus({ kind: "misconfigured", problem: err.problem });
          }
          return;
        }
        setStatus({ kind: "unreachable" });
      });
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  if (status.kind === "checking") {
    return (
      <div role="status" aria-live="polite" className="auth-loading">
        Checking your identity…
      </div>
    );
  }

  if (status.kind === "unreachable") {
    return (
      <div className="error-card" role="alert">
        {
          "Can't reach the Poseidon backend. Check that it's running (see infra/runbooks/local.md), then retry. "
        }
        <button type="button" onClick={check}>
          Retry
        </button>
      </div>
    );
  }

  if (status.kind === "misconfigured") {
    return (
      <ProblemScreen
        heading="Identity misconfigured"
        problem={status.problem}
        fallbackDetail="No Auth0 configuration was found for this deployment (VITE_AUTH0_DOMAIN / VITE_AUTH0_CLIENT_ID / VITE_AUTH0_AUDIENCE)."
      />
    );
  }

  if (status.kind === "needs-login") {
    return (
      <Auth0Boundary
        config={status.config}
        onAuthenticated={(identity, logout) => setStatus({ kind: "ready", identity, logout })}
      />
    );
  }

  return (
    <IdentityContext.Provider value={{ identity: status.identity, logout: status.logout }}>
      <RoleGuard roles={status.identity.roles} requiredRole={REQUIRED_ROLE}>
        {children}
      </RoleGuard>
    </IdentityContext.Provider>
  );
}
