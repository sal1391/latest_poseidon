import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { SkillsPicker } from "./SkillsPicker";

// No default handlers here (unlike mocks/handlers.ts, shared by the
// ChatScreen suite): every test below defines its OWN GET /api/skills
// response, since fetch success vs. failure is exactly what each proves.
const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

test("fetches the real skill list on open and renders it", async () => {
  server.use(
    http.get("/api/skills", () =>
      HttpResponse.json([
        {
          id: "data_qa.metric_query",
          label: "Metric query",
          description: "Query certified metrics.",
        },
      ]),
    ),
  );

  render(<SkillsPicker onPick={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));

  await waitFor(() => expect(screen.getByText("Query certified metrics.")).toBeInTheDocument());
  expect(screen.getByText("Metric query")).toBeInTheDocument();
  // The curated static example is gone -- this is the FETCHED list, not the
  // fallback (that distinction is the whole point of this test).
  expect(screen.queryByText(/web research/i)).not.toBeInTheDocument();
});

test("falls back to the current static list when the fetch fails", async () => {
  server.use(http.get("/api/skills", () => new HttpResponse(null, { status: 500 })));

  render(<SkillsPicker onPick={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));

  await waitFor(() =>
    expect(
      screen.getByText(/Top GP customers for Port of Singapore in April 2026/),
    ).toBeInTheDocument(),
  );
  expect(screen.getByText("Metric query")).toBeInTheDocument();
});

test("falls back to the static list when GET /api/skills does not exist (mock mode's own 404)", async () => {
  server.use(http.get("/api/skills", () => new HttpResponse(null, { status: 404 })));

  render(<SkillsPicker onPick={vi.fn()} />);
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));

  await waitFor(() => screen.getByText("Metric query"));
  expect(
    screen.getByText(/Top GP customers for Port of Singapore in April 2026/),
  ).toBeInTheDocument();
});

test("picking a registry-backed skill reuses the static list's curated example (final-review wave item 12)", async () => {
  // "data_qa.metric_query" is namespaced (GET /api/skills' own wire shape),
  // but its bare name "metric_query" matches FALLBACK_SKILLS' own curated
  // entry -- picking it must insert that RUNNABLE example, not just its
  // label, even though this list came from the registry fetch, not the
  // static fallback.
  server.use(
    http.get("/api/skills", () =>
      HttpResponse.json([
        { id: "data_qa.metric_query", label: "Metric query", description: "d" },
      ]),
    ),
  );
  const onPick = vi.fn();
  render(<SkillsPicker onPick={onPick} />);
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));
  await waitFor(() => screen.getByText("Metric query"));

  await userEvent.click(screen.getByText("Metric query"));

  expect(onPick).toHaveBeenCalledWith("Top GP customers for Port of Singapore in April 2026");
});

test("picking a registry-backed skill with no curated example inserts its label", async () => {
  // A bare name ("something_new") with no match in FALLBACK_SKILLS at all --
  // the fallback-to-label path item 12 keeps for a genuinely uncurated skill.
  server.use(
    http.get("/api/skills", () =>
      HttpResponse.json([
        { id: "data_qa.something_new", label: "Something new", description: "d" },
      ]),
    ),
  );
  const onPick = vi.fn();
  render(<SkillsPicker onPick={onPick} />);
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));
  await waitFor(() => screen.getByText("Something new"));

  await userEvent.click(screen.getByText("Something new"));

  expect(onPick).toHaveBeenCalledWith("Something new");
});
