import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
import { setAuthTokenProvider } from "../../api/client";

// Pinned lazy-import test double: the factory itself (not just its return
// value) records that `@auth0/auth0-react` was actually resolved. Vitest
// intercepts BOTH static and dynamic `import()` of a mocked specifier, but
// only ever INVOKES this factory the first time something really imports
// it -- so if every test in this file stays on a non-auth0 branch, the spy
// proves the SDK module never loaded at all, not merely that its exports
// went unused.
const auth0ImportSpy = vi.fn();
vi.mock("@auth0/auth0-react", () => {
  auth0ImportSpy();
  return {
    Auth0Provider: ({ children }: { children: React.ReactNode }) => children,
    useAuth0: () => ({
      isLoading: false,
      isAuthenticated: false,
      loginWithRedirect: vi.fn(),
      getAccessTokenSilently: vi.fn(),
      logout: vi.fn(),
    }),
  };
});

import { AuthGate } from "./AuthGate";

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

beforeEach(() => {
  setAuthTokenProvider(null);
  // Absent by default in this environment (no real tenant configured
  // anywhere in this repo/CI) -- stubbed explicitly anyway so these tests
  // never depend on ambient environment state.
  vi.stubEnv("VITE_AUTH0_DOMAIN", "");
  vi.stubEnv("VITE_AUTH0_CLIENT_ID", "");
  vi.stubEnv("VITE_AUTH0_AUDIENCE", "");
});

afterEach(() => {
  vi.unstubAllEnvs();
});

function meHandler(body: Record<string, unknown>, status = 200) {
  return http.get("/api/me", () => HttpResponse.json(body, { status }));
}

test("a 200 from GET /api/me renders the app immediately with the payload's identity (disabled mode)", async () => {
  server.use(
    meHandler({
      sub: "dev|local",
      name: "Dev User",
      email: "dev@local",
      roles: ["Poseidon:Sales"],
      identity_mode: "disabled",
    }),
  );

  render(
    <AuthGate>
      <div>Protected content</div>
    </AuthGate>,
  );

  expect(await screen.findByText("Protected content")).toBeInTheDocument();
});

test("disabled-mode rendering never imports the Auth0 SDK (lazy import pin)", async () => {
  server.use(
    meHandler({
      sub: "dev|local",
      name: "Dev User",
      email: "dev@local",
      roles: ["Poseidon:Sales"],
      identity_mode: "disabled",
    }),
  );

  render(
    <AuthGate>
      <div>Protected content</div>
    </AuthGate>,
  );
  await screen.findByText("Protected content");

  expect(auth0ImportSpy).not.toHaveBeenCalled();
});

test("a 200 for spcs_ingress mode also renders immediately (both non-auth0 providers always admit)", async () => {
  server.use(
    meHandler({
      sub: "sf|alice",
      name: null,
      email: null,
      roles: ["Poseidon:Sales"],
      identity_mode: "spcs_ingress",
    }),
  );

  render(
    <AuthGate>
      <div>Protected content</div>
    </AuthGate>,
  );

  expect(await screen.findByText("Protected content")).toBeInTheDocument();
  expect(auth0ImportSpy).not.toHaveBeenCalled();
});

test("a 401 with no VITE_AUTH0_* config renders identity-misconfigured, not a blank screen or the login gate", async () => {
  server.use(
    meHandler(
      {
        type: "about:blank",
        title: "missing bearer token",
        detail: "no Authorization header",
        status: 401,
      },
      401,
    ),
  );

  render(
    <AuthGate>
      <div>Protected content</div>
    </AuthGate>,
  );

  expect(await screen.findByText(/identity misconfigured/i)).toBeInTheDocument();
  expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /log in/i })).not.toBeInTheDocument();
  expect(auth0ImportSpy).not.toHaveBeenCalled();
});

test("a role-less 200 identity renders the no-access screen instead of the protected content", async () => {
  server.use(
    meHandler({
      sub: "dev|bob",
      name: "Bob",
      email: "bob@local",
      roles: [],
      identity_mode: "disabled",
    }),
  );

  render(
    <AuthGate>
      <div>Protected content</div>
    </AuthGate>,
  );

  expect(await screen.findByText(/no access/i)).toBeInTheDocument();
  expect(screen.getByText(/insufficient role/i)).toBeInTheDocument();
  expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
});

test("an unreachable backend shows a retry-able connection error, not a blank screen", async () => {
  server.use(http.get("/api/me", () => HttpResponse.error()));

  render(
    <AuthGate>
      <div>Protected content</div>
    </AuthGate>,
  );

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/can't reach the poseidon backend/i);
  expect(auth0ImportSpy).not.toHaveBeenCalled();
});
