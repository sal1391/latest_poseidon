import type { ProblemDetail } from "../../api/client";

/**
 * Renders an RFC-7807 problem payload as a full-screen card -- shared by
 * `AuthGate`'s "identity misconfigured" state and `RoleGuard`'s "no
 * access" state (doc 01 section 10: "auth errors (401/403) route to ...
 * with the reason stated plainly"). `problem` is the real backend body
 * when one exists; `fallbackDetail` covers the one case with no HTTP
 * response to read from at all (a 401 with no Auth0 config configured
 * client-side -- there was never a request to retry with a different
 * config, so there is no SECOND server round trip that could supply a
 * body).
 */
export function ProblemScreen({
  heading,
  problem,
  fallbackDetail,
}: {
  heading: string;
  problem: ProblemDetail | null;
  fallbackDetail: string;
}) {
  return (
    <div className="app-shell auth-problem" role="alert">
      <h1>{heading}</h1>
      {problem ? (
        <>
          <p className="auth-problem-title">{problem.title}</p>
          <p className="auth-problem-detail">{problem.detail}</p>
        </>
      ) : (
        <p className="auth-problem-detail">{fallbackDetail}</p>
      )}
    </div>
  );
}
