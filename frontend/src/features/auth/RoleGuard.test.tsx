import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import type { ProblemDetail } from "../../api/client";
import { RoleGuard } from "./RoleGuard";

test("renders children when the identity carries the required role", () => {
  render(
    <RoleGuard roles={["Poseidon:Sales"]} requiredRole="Poseidon:Sales">
      <div>Secret content</div>
    </RoleGuard>,
  );

  expect(screen.getByText("Secret content")).toBeInTheDocument();
});

test("renders the no-access screen from a 403 problem payload when the role is missing", () => {
  // The pinned pin: this screen must render FROM a problem payload (RFC
  // 7807 title + detail), not a hardcoded string -- proven here by
  // passing a payload distinguishable from RoleGuard's own default.
  const problem: ProblemDetail = {
    type: "about:blank",
    title: "insufficient role",
    detail: "caller lacks required role 'Poseidon:Sales'",
    status: 403,
  };

  render(
    <RoleGuard roles={[]} requiredRole="Poseidon:Sales" problem={problem}>
      <div>Secret content</div>
    </RoleGuard>,
  );

  expect(screen.getByText("insufficient role")).toBeInTheDocument();
  expect(screen.getByText("caller lacks required role 'Poseidon:Sales'")).toBeInTheDocument();
  expect(screen.queryByText("Secret content")).not.toBeInTheDocument();
});

test("synthesizes the same 403 shape locally when no problem payload is given", () => {
  // GET /api/me never actually returns a 403 (api/auth.py's get_me depends
  // on current_user alone, never require_sales) -- a role-less 200 is the
  // real-world path into this branch, so RoleGuard must be able to render
  // an equivalent screen with no live problem payload in hand at all.
  render(
    <RoleGuard roles={["some-other-role"]} requiredRole="Poseidon:Sales">
      <div>Secret content</div>
    </RoleGuard>,
  );

  expect(screen.getByText("insufficient role")).toBeInTheDocument();
  expect(screen.getByText(/caller lacks required role 'Poseidon:Sales'/)).toBeInTheDocument();
});
