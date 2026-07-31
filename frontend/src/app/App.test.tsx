import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import App from "./App";

// App now boots behind AuthGate (Phase 9 Task 4): the shell only renders
// once GET /api/me resolves. This mocks the disabled-mode 200 every real
// deployment of this demo takes by default -- the "the demo must not
// regress" pin, at the unit level (Playwright covers it end to end; see
// this task's own report).
const server = setupServer(
  http.get("/api/me", () =>
    HttpResponse.json({
      sub: "dev|local",
      name: "Dev User",
      email: "dev@local",
      roles: ["Poseidon:Sales"],
      identity_mode: "disabled",
    }),
  ),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("renders the app shell with brand and composer placeholder", async () => {
  render(<App />);
  expect(await screen.findByText("Poseidon")).toBeInTheDocument();
  expect(screen.getByPlaceholderText(/message poseidon/i)).toBeInTheDocument();
});
