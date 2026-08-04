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

// Phase 12 Task 4 (a11y carry-list, verbatim): the trigger must advertise
// that it opens a popup, so a screen reader user knows what `aria-expanded`
// refers to before ever opening it.
test("the trigger button advertises aria-haspopup", () => {
  render(<SkillsPicker onPick={vi.fn()} />);
  expect(screen.getByRole("button", { name: /skills/i })).toHaveAttribute(
    "aria-haspopup",
    "true",
  );
});

// Clicking outside the open popover must close it -- the carried-forward gap
// this task closes (no such handling existed before this task).
test("clicking outside the open popover closes it", async () => {
  render(
    <div>
      <button type="button">Outside</button>
      <SkillsPicker onPick={vi.fn()} />
    </div>,
  );
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));
  expect(screen.getByRole("group", { name: /skills/i })).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Outside" }));

  expect(screen.queryByRole("group", { name: /skills/i })).not.toBeInTheDocument();
});

// Closing without picking anything (outside click, or Escape) must return
// focus to the trigger -- otherwise a keyboard/screen-reader user backing
// out of the popover loses their place on the page entirely.
test("closing via outside click returns focus to the trigger", async () => {
  render(
    <div>
      <SkillsPicker onPick={vi.fn()} />
    </div>,
  );
  const trigger = screen.getByRole("button", { name: /skills/i });
  await userEvent.click(trigger);
  // Move focus off the trigger, into the popover -- the realistic keyboard
  // state this test needs to distinguish "focus already stayed on trigger"
  // (a vacuous pass) from "focus was actively RETURNED to the trigger".
  await userEvent.tab();
  expect(trigger).not.toHaveFocus();

  // A non-focusable click target: clicking it does not itself move focus
  // anywhere, so any observed focus change is this component's own doing.
  await userEvent.click(document.body);

  expect(trigger).toHaveFocus();
});

// Review fix round 1, Important #1: the composer's own `<input>` is a
// SIBLING of SkillsPicker (Composer.tsx), not a descendant, so it counts
// as "outside" the popover's own container -- but it is a REAL focusable
// control, not inert page chrome. A click meant to move on to typing must
// not get silently redirected back onto the Skills trigger.
test("clicking a different focusable control closes the popover and lets THAT control receive focus, not the trigger", async () => {
  render(
    <div>
      <SkillsPicker onPick={vi.fn()} />
      <input aria-label="Message Poseidon" />
    </div>,
  );
  const trigger = screen.getByRole("button", { name: /skills/i });
  await userEvent.click(trigger);
  expect(screen.getByRole("group", { name: /skills/i })).toBeInTheDocument();

  const composerInput = screen.getByRole("textbox", { name: /message poseidon/i });
  await userEvent.click(composerInput);

  expect(screen.queryByRole("group", { name: /skills/i })).not.toBeInTheDocument();
  expect(composerInput).toHaveFocus();
  expect(trigger).not.toHaveFocus();
});

test("closing via Escape returns focus to the trigger", async () => {
  render(<SkillsPicker onPick={vi.fn()} />);
  const trigger = screen.getByRole("button", { name: /skills/i });
  await userEvent.click(trigger);
  await userEvent.tab();
  expect(trigger).not.toHaveFocus();

  await userEvent.keyboard("{Escape}");

  expect(screen.queryByRole("group", { name: /skills/i })).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
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
