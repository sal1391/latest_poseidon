import type { ReactNode } from "react";
import type { ProblemDetail } from "../../api/client";
import { ProblemScreen } from "./ProblemScreen";

/**
 * Mirrors `api/auth.py`'s `require_sales` 403 body byte-for-byte (same
 * title/detail/status a real role-gated route answers with) so a
 * role-less identity sees an identical "no access" story whether it was
 * caught here -- `GET /api/me` itself never 403s, see `AuthGate`'s own
 * docstring -- or by a real 403 from any other role-gated route this app
 * calls later.
 */
function defaultInsufficientRoleProblem(requiredRole: string): ProblemDetail {
  return {
    type: "about:blank",
    title: "insufficient role",
    detail: `caller lacks required role '${requiredRole}'`,
    status: 403,
  };
}

export function RoleGuard({
  roles,
  requiredRole,
  problem,
  children,
}: {
  roles: readonly string[];
  requiredRole: string;
  /** A problem payload sourced from a real HTTP 403, when the caller has
   * one. Defaults to the locally-synthesized equivalent above -- the
   * common case, since the one caller that matters today (`AuthGate`,
   * reading `GET /api/me`'s 200-but-role-less response) never has a real
   * 403 to forward. */
  problem?: ProblemDetail;
  children: ReactNode;
}) {
  if (roles.includes(requiredRole)) return <>{children}</>;
  return (
    <ProblemScreen
      heading="No access"
      problem={problem ?? defaultInsufficientRoleProblem(requiredRole)}
      fallbackDetail={`caller lacks required role '${requiredRole}'`}
    />
  );
}
