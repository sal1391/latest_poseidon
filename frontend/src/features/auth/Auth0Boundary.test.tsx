import { render, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { setAuthTokenProvider } from "../../api/client";

/** A mutable stand-in for useAuth0()'s return value: reassigned per test
 * (never mutated in place) so each test starts from a known SDK state.
 * The mock's `useAuth0` closure reads this binding fresh on every call --
 * ordinary JS closure semantics over a `let`, nothing framework-specific. */
let auth0State = {
  isLoading: false,
  isAuthenticated: false,
  loginWithRedirect: vi.fn(),
  getAccessTokenSilently: vi.fn(async () => "mock-access-token"),
  logout: vi.fn(),
};

vi.mock("@auth0/auth0-react", () => ({
  Auth0Provider: ({ children }: { children: React.ReactNode }) => children,
  useAuth0: () => auth0State,
}));

import { Auth0Boundary } from "./Auth0Boundary";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => {
  server.resetHandlers();
  setAuthTokenProvider(null);
  auth0State = {
    isLoading: false,
    isAuthenticated: false,
    loginWithRedirect: vi.fn(),
    getAccessTokenSilently: vi.fn(async () => "mock-access-token"),
    logout: vi.fn(),
  };
});
afterAll(() => server.close());

const config = { domain: "test.auth0.local", clientId: "test-client-id", audience: "https://poseidon/api" };

test("shows the login gate when the SDK reports unauthenticated", async () => {
  render(<Auth0Boundary config={config} onAuthenticated={() => undefined} />);

  const button = await screen.findByRole("button", { name: /log in/i });
  expect(button).toBeInTheDocument();

  button.click();
  expect(auth0State.loginWithRedirect).toHaveBeenCalled();
});

test("while the SDK is loading, shows a loading state rather than the login gate", async () => {
  auth0State = { ...auth0State, isLoading: true };

  render(<Auth0Boundary config={config} onAuthenticated={() => undefined} />);

  await screen.findByRole("status");
  expect(screen.queryByRole("button", { name: /log in/i })).not.toBeInTheDocument();
});

test("once authenticated, wires the SDK token getter into the injector and re-fetches identity", async () => {
  server.use(
    http.get("/api/me", ({ request }) => {
      const auth = request.headers.get("Authorization");
      if (auth !== "Bearer mock-access-token") {
        return HttpResponse.json(
          { type: "about:blank", title: "missing bearer token", detail: "x", status: 401 },
          { status: 401 },
        );
      }
      return HttpResponse.json({
        sub: "auth0|user123",
        name: "Alice",
        email: "alice@example.com",
        roles: ["Poseidon:Sales"],
        identity_mode: "auth0",
      });
    }),
  );
  auth0State = { ...auth0State, isAuthenticated: true };
  const onAuthenticated = vi.fn();

  render(<Auth0Boundary config={config} onAuthenticated={onAuthenticated} />);

  await waitFor(() => expect(onAuthenticated).toHaveBeenCalled());
  const [identity] = onAuthenticated.mock.calls[0] as [{ sub: string }, () => void];
  expect(identity.sub).toBe("auth0|user123");
});

test("the logout callback handed to onAuthenticated calls the SDK's own logout with returnTo", async () => {
  server.use(
    http.get("/api/me", () =>
      HttpResponse.json({
        sub: "auth0|user123",
        name: "Alice",
        email: "alice@example.com",
        roles: ["Poseidon:Sales"],
        identity_mode: "auth0",
      }),
    ),
  );
  auth0State = { ...auth0State, isAuthenticated: true };
  const onAuthenticated = vi.fn();

  render(<Auth0Boundary config={config} onAuthenticated={onAuthenticated} />);
  await waitFor(() => expect(onAuthenticated).toHaveBeenCalled());

  const [, logout] = onAuthenticated.mock.calls[0] as [unknown, () => void];
  logout();

  expect(auth0State.logout).toHaveBeenCalledWith({
    logoutParams: { returnTo: window.location.origin },
  });
});
