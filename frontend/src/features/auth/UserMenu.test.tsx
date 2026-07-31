import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { Identity } from "../../api/client";
import { IdentityContext } from "./identityContext";
import { UserMenu } from "./UserMenu";

test("renders nothing outside an AuthGate (no IdentityContext provider)", () => {
  const { container } = render(<UserMenu />);
  expect(container).toBeEmptyDOMElement();
});

test("shows a working logout button in auth0 mode", () => {
  const identity: Identity = {
    sub: "auth0|abc",
    name: "Alice",
    email: "alice@example.com",
    roles: ["Poseidon:Sales"],
    identity_mode: "auth0",
  };
  const logout = vi.fn();

  render(
    <IdentityContext.Provider value={{ identity, logout }}>
      <UserMenu />
    </IdentityContext.Provider>,
  );

  expect(screen.getByText("Alice")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /log out/i }));
  expect(logout).toHaveBeenCalled();
});

test("shows identity but no logout control in disabled mode", () => {
  const identity: Identity = {
    sub: "dev|local",
    name: "Dev User",
    email: "dev@local",
    roles: ["Poseidon:Sales"],
    identity_mode: "disabled",
  };

  render(
    <IdentityContext.Provider value={{ identity, logout: () => undefined }}>
      <UserMenu />
    </IdentityContext.Provider>,
  );

  expect(screen.getByText("Dev User")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /log out/i })).not.toBeInTheDocument();
});

test("falls back to sub when name is null (spcs_ingress carries no display name)", () => {
  const identity: Identity = {
    sub: "sf|alice",
    name: null,
    email: null,
    roles: ["Poseidon:Sales"],
    identity_mode: "spcs_ingress",
  };

  render(
    <IdentityContext.Provider value={{ identity, logout: () => undefined }}>
      <UserMenu />
    </IdentityContext.Provider>,
  );

  expect(screen.getByText("sf|alice")).toBeInTheDocument();
});
