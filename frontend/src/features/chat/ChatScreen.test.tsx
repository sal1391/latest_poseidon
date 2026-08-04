import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { vi, beforeAll, beforeEach, afterAll, afterEach, test, expect } from "vitest";
import { handlers, mockOpener } from "../../mocks/handlers";
import { resetChatStore, useChatStore } from "../../state/chatStore";
import { streamTurn } from "../../api/sse";
import type { SseEvent } from "../../api/types";

vi.mock("../../api/sse", () => ({
  // Phase 10 Task 4: `streamTurn` gained a REQUIRED `clientTurnKey` third
  // positional parameter (the `crypto.randomUUID()` call that used to live
  // in `sse.ts` moved up into `chatStore.ts`'s own `sendMessage`) -- this
  // mock's own parameter list has to shift too, or `onEvent` would silently
  // bind to the key string instead of the callback and every event below
  // would go nowhere.
  streamTurn: vi.fn(
    async (_cid: string, _text: string, _clientTurnKey: string, onEvent: (e: SseEvent) => void) => {
      const events: SseEvent[] = [
        { name: "accepted", data: { turn_id: "t1", message_id: "a1", event_seq: 1, turn_index: 1 } },
        { name: "tool", data: { turn_id: "t1", message_id: "a1", event_seq: 2, tool_seq: 1, tool: "top_customers", server: "internal", status: "start", label: "Running skill · top_customers…" } },
        { name: "tool", data: { turn_id: "t1", message_id: "a1", event_seq: 3, tool_seq: 1, tool: "top_customers", server: "internal", status: "done", label: "top_customers · done · 0.3s" } },
        { name: "token", data: { turn_id: "t1", message_id: "a1", event_seq: 4, text: "Three customers drove April." } },
        // title: null -- this suite exercises the streamed-answer/feedback/
        // composer paths, never title refresh (see Sidebar.test.tsx and
        // chatStore.test.ts for that); null keeps it behavior-neutral here.
        { name: "done", data: { turn_id: "t1", message_id: "a1", event_seq: 5, usage: {}, title: null } },
      ];
      events.forEach(onEvent);
    },
  ),
}));

import ChatScreen from "./ChatScreen";

const server = setupServer(...handlers);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// The store is a module singleton (state plus the bootstrap memo): reset both
// so the tests stay order-independent.
beforeEach(resetChatStore);

test("send → streamed answer with visible tool step", async () => {
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "top gp customers singapore{Enter}");
  await waitFor(() =>
    expect(screen.getByText(/top_customers · done · 0.3s/)).toBeInTheDocument());
  expect(screen.getByText(/Three customers drove April./)).toBeInTheDocument();
});

test("thumbs down opens the comment prompt and submits the real verdict+comment body", async () => {
  let seenBody: unknown;
  server.use(
    http.post("/api/messages/:mid/feedback", async ({ request }) => {
      seenBody = await request.json();
      return new HttpResponse(null, { status: 204 });
    }),
  );
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  // Wait for the streamed answer: the opener is an assistant message too, so
  // without this the test would pass against its feedback row even if the send
  // never happened.
  await waitFor(() => screen.getByText(/Three customers drove April./));
  await waitFor(() => screen.getAllByLabelText("Bad response"));
  await userEvent.click(screen.getAllByLabelText("Bad response").at(-1)!);
  const box = await screen.findByPlaceholderText(/what went wrong/i);
  await userEvent.type(box, "numbers look off");
  await userEvent.click(screen.getByRole("button", { name: /send feedback/i }));
  await waitFor(() =>
    expect(screen.queryByPlaceholderText(/what went wrong/i)).not.toBeInTheDocument());
  expect(seenBody).toEqual({ verdict: "down", comment: "numbers look off" });
});

// Phase 12 Task 2 (task-2-brief Step 1): the opener (the first message of
// every conversation) carries no linked turn, so the backend 422s feedback
// on it -- the UI must never even offer thumbs there. Before any turn the
// only assistant message loaded IS the opener, so zero thumbs rows is the
// gating proof; after one real turn exactly one turn-backed assistant
// message exists, so exactly one thumbs row -- never two.
test("thumbs render only on turn-backed assistant messages, never on the opener", async () => {
  render(<ChatScreen />);
  await screen.findByText(/Ask about your data/);
  expect(screen.queryAllByLabelText("Good response")).toHaveLength(0);

  const input = screen.getByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => screen.getByText(/Three customers drove April./));

  expect(screen.getAllByLabelText("Good response")).toHaveLength(1);
});

test("thumbs-up POSTs {verdict: 'up'} and the button reflects the recorded state", async () => {
  let seenBody: unknown;
  server.use(
    http.post("/api/messages/:mid/feedback", async ({ request }) => {
      seenBody = await request.json();
      return new HttpResponse(null, { status: 204 });
    }),
  );
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => screen.getByText(/Three customers drove April./));

  const upButton = screen.getByLabelText("Good response");
  expect(upButton).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(upButton);

  await waitFor(() => expect(upButton).toHaveAttribute("aria-pressed", "true"));
  expect(seenBody).toEqual({ verdict: "up", comment: null });
});

// Un-vote follow-up to Phase 12 (live-testing gap 2): clicking an
// already-active thumb again clears it back to neutral rather than
// re-submitting the same verdict.
test("clicking an already-active thumbs-up again clears the vote (toggle off)", async () => {
  const seenBodies: unknown[] = [];
  server.use(
    http.post("/api/messages/:mid/feedback", async ({ request }) => {
      seenBodies.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
  );
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => screen.getByText(/Three customers drove April./));

  const upButton = screen.getByLabelText("Good response");
  await userEvent.click(upButton);
  await waitFor(() => expect(upButton).toHaveAttribute("aria-pressed", "true"));

  await userEvent.click(upButton);

  await waitFor(() => expect(upButton).toHaveAttribute("aria-pressed", "false"));
  expect(seenBodies).toEqual([
    { verdict: "up", comment: null },
    { verdict: null, comment: null },
  ]);
});

// Down's toggle-off is direct (no re-prompt for a comment just to clear an
// already-recorded down vote) -- distinct from the not-yet-down case, which
// still opens the "what went wrong?" prompt (covered by the thumbs-down
// test above).
test("clicking an already-active thumbs-down again clears the vote directly, with no prompt", async () => {
  const seenBodies: unknown[] = [];
  server.use(
    http.post("/api/messages/:mid/feedback", async ({ request }) => {
      seenBodies.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
  );
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => screen.getByText(/Three customers drove April./));

  const downButton = screen.getByLabelText("Bad response");
  await userEvent.click(downButton);
  const skip = await screen.findByRole("button", { name: /skip/i });
  await userEvent.click(skip);
  await waitFor(() => expect(downButton).toHaveAttribute("aria-pressed", "true"));

  await userEvent.click(downButton);

  await waitFor(() => expect(downButton).toHaveAttribute("aria-pressed", "false"));
  expect(screen.queryByPlaceholderText(/what went wrong/i)).not.toBeInTheDocument();
  expect(seenBodies).toEqual([
    { verdict: "down", comment: null },
    { verdict: null, comment: null },
  ]);
});

// Live-testing gap 1: no way to close the "what went wrong" box without
// voting -- both existing buttons ("Send feedback", "Skip") recorded a down
// vote. Cancel must close the prompt, submit nothing, and leave any
// already-recorded verdict untouched.
test("Cancel closes the comment prompt without submitting anything", async () => {
  let postCount = 0;
  server.use(
    http.post("/api/messages/:mid/feedback", () => {
      postCount += 1;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => screen.getByText(/Three customers drove April./));

  await userEvent.click(screen.getByLabelText("Bad response"));
  const box = await screen.findByPlaceholderText(/what went wrong/i);
  await userEvent.type(box, "numbers look off");
  await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

  expect(screen.queryByPlaceholderText(/what went wrong/i)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Bad response")).toHaveAttribute("aria-pressed", "false");
  expect(postCount).toBe(0);
});

// Cancel must not touch an ALREADY-recorded verdict either -- opening the
// down-prompt from an already-up state, then cancelling, must leave the
// up vote exactly as it was (no second POST, still pressed).
test("Cancel leaves an already-recorded verdict untouched", async () => {
  const seenBodies: unknown[] = [];
  server.use(
    http.post("/api/messages/:mid/feedback", async ({ request }) => {
      seenBodies.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
  );
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => screen.getByText(/Three customers drove April./));

  await userEvent.click(screen.getByLabelText("Good response"));
  await waitFor(() =>
    expect(screen.getByLabelText("Good response")).toHaveAttribute("aria-pressed", "true"));

  await userEvent.click(screen.getByLabelText("Bad response"));
  const box = await screen.findByPlaceholderText(/what went wrong/i);
  await userEvent.type(box, "changed my mind about typing this");
  await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

  expect(screen.queryByPlaceholderText(/what went wrong/i)).not.toBeInTheDocument();
  expect(screen.getByLabelText("Good response")).toHaveAttribute("aria-pressed", "true");
  expect(seenBodies).toEqual([{ verdict: "up", comment: null }]);
});

test("re-voting from up to down amends the verdict and the UI reflects the flip", async () => {
  const seenBodies: unknown[] = [];
  server.use(
    http.post("/api/messages/:mid/feedback", async ({ request }) => {
      seenBodies.push(await request.json());
      return new HttpResponse(null, { status: 204 });
    }),
  );
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => screen.getByText(/Three customers drove April./));

  await userEvent.click(screen.getByLabelText("Good response"));
  await waitFor(() =>
    expect(screen.getByLabelText("Good response")).toHaveAttribute("aria-pressed", "true"));

  await userEvent.click(screen.getByLabelText("Bad response"));
  const skip = await screen.findByRole("button", { name: /skip/i });
  await userEvent.click(skip); // Skip still records the down verdict, with no comment.

  await waitFor(() =>
    expect(screen.getByLabelText("Bad response")).toHaveAttribute("aria-pressed", "true"));
  expect(screen.getByLabelText("Good response")).toHaveAttribute("aria-pressed", "false");
  expect(seenBodies).toEqual([
    { verdict: "up", comment: null },
    { verdict: "down", comment: null },
  ]);
});

test("GET hydrates an existing verdict when a conversation with a prior turn-backed message is opened", async () => {
  server.use(
    http.get("/api/conversations", () =>
      HttpResponse.json({ items: [{ id: "c9", title: "Prior chat" }], next_cursor: null })),
    http.get("/api/conversations/c9/messages", () =>
      HttpResponse.json({
        items: [
          mockOpener,
          { id: "u1", role: "user", parts: [{ kind: "text", payload: { markdown: "hi" } }] },
          {
            id: "a9",
            role: "assistant",
            parts: [{ kind: "text", payload: { markdown: "prior answer" } }],
          },
        ],
        next_cursor: null,
      })),
    http.get("/api/messages/:mid/feedback", ({ params }) =>
      params.mid === "a9"
        ? HttpResponse.json({ verdict: "up", comment: null })
        : new HttpResponse(null, { status: 404 })),
  );

  render(<ChatScreen />);

  await screen.findByText(/prior answer/);
  await waitFor(() =>
    expect(screen.getByLabelText("Good response")).toHaveAttribute("aria-pressed", "true"));
  // The opener still gets no thumbs row at all -- exactly one, for a9.
  expect(screen.getAllByLabelText("Good response")).toHaveLength(1);
});

// Defensive (task-2-brief: "pin both layers"): the UI never offers thumbs on
// a message a 422 is possible for, but if the backend ever disagreed with
// that gating, a rejected POST must still roll the optimistic UI back
// cleanly rather than leaving a stuck "pressed" state or crashing the
// screen.
test("a 422 from POST rolls the optimistic thumbs-up back rather than leaving it stuck pressed", async () => {
  server.use(
    http.post("/api/messages/:mid/feedback", () =>
      HttpResponse.json(
        {
          type: "about:blank",
          title: "feedback_not_applicable",
          detail: "message has no linked turn",
          status: 422,
        },
        { status: 422 },
      )),
  );
  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => screen.getByText(/Three customers drove April./));

  const upButton = screen.getByLabelText("Good response");
  await userEvent.click(upButton);

  await waitFor(() => expect(upButton).toHaveAttribute("aria-pressed", "false"));
});

test("composer is disabled while a send waits on the bootstrap window", async () => {
  // Bootstrap still in flight and the turn never settles, so `streamingByConv`
  // cannot be what disables the input — only ChatScreen's own `sending` flag can.
  // The GET never resolves at all -- a timed `delay()` used to stand in here,
  // but this test asserts and returns long before that timer fires, leaving
  // the delayed response an orphaned continuation that fired seconds later,
  // mid-suite, inside a LATER test's own render: it replayed bootstrap
  // against whatever conversation was active at that moment, restamping the
  // shared store's `conversations`/`activeId`/`messages`. A promise that
  // never settles leaves nothing running past this test's own lifetime.
  //
  // No `streamTurn` mock override here (fix round 1: one used to sit here,
  // pointlessly -- this test's own send() blocks at `await bootstrap()`
  // and never reaches `streamTurn` at all, so the queued one-time override
  // was never consumed by this test; it sat in vitest's mock queue and was
  // silently consumed by whichever LATER test called `streamTurn` next
  // instead, handing that unrelated test a promise that never resolves).
  server.use(http.get("/api/conversations", () => new Promise<never>(() => {})));

  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  expect(input).not.toBeDisabled();
  await userEvent.type(input, "hello{Enter}");
  expect(input).toBeDisabled();
});

test("a send in one conversation leaves another conversation's composer live", async () => {
  // The turn never settles. A boolean `sending` flag would keep the composer
  // disabled everywhere for its whole life — including in a conversation that
  // has no turn of its own running.
  vi.mocked(streamTurn).mockImplementationOnce(() => new Promise<void>(() => {}));

  render(<ChatScreen />);
  // The opener only renders once bootstrap has landed, so waiting on it is what
  // guarantees the send below is tied to c1 rather than to the null window.
  await screen.findByText(/Ask about your data/);
  const input = screen.getByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  expect(input).toBeDisabled();

  act(() => {
    useChatStore.setState((s) => ({
      conversations: [{ id: "c2", title: "Second chat" }, ...s.conversations],
      activeId: "c2",
      messages: { ...s.messages, c2: [] },
    }));
  });
  expect(screen.getByPlaceholderText(/message poseidon/i)).not.toBeDisabled();
});

test("skills picker inserts an example prompt", async () => {
  render(<ChatScreen />);
  await screen.findByPlaceholderText(/message poseidon/i);
  await userEvent.click(screen.getByRole("button", { name: /skills/i }));
  await userEvent.click(screen.getByText(/Metric query/));
  expect(screen.getByPlaceholderText(/message poseidon/i)).toHaveValue(
    "Top GP customers for Port of Singapore in April 2026");
});

test("clicking a chip sends its entry phrase as a user message, not just a composer insert", async () => {
  render(<ChatScreen />);
  // The opener's own flow chips (mocks/handlers.ts's mockOpener) -- waiting
  // for them is what guarantees bootstrap has landed and activeId is set.
  await screen.findByText(/Ask about your data/);

  await userEvent.click(screen.getByRole("button", { name: "Existing customer" }));

  // The pinned D19 entry phrase (P8 whole-branch final-review wave,
  // 2026-07-30, item 9 / M-2), not the bare button label: mockOpener now
  // carries send_text on its opener chips, matching api/live_chat.py's own
  // real opener -- this is what gives D19 a frontend-side pin (before this
  // fix, the mock's own missing send_text let this assertion pass while
  // checking something no real backend click ever sends).
  expect(streamTurn).toHaveBeenCalledWith(
    "c1", "start an existing-customer brief", expect.any(String), expect.any(Function));
  // The composer itself was never populated -- this went straight through
  // the send path, unlike SkillsPicker's insert-only affordance above.
  expect(screen.getByPlaceholderText(/message poseidon/i)).toHaveValue("");
  await waitFor(() =>
    expect(screen.getByText(/Three customers drove April./)).toBeInTheDocument());
});

test("a chip is disabled while its conversation's send is in flight", async () => {
  vi.mocked(streamTurn).mockImplementationOnce(() => new Promise<void>(() => {}));
  render(<ChatScreen />);
  await screen.findByText(/Ask about your data/);

  await userEvent.click(screen.getByRole("button", { name: "Existing customer" }));

  expect(screen.getByRole("button", { name: "Existing customer" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "New customer prospect" })).toBeDisabled();
});

// Phase 12 Task 4 (a11y carry-list, verbatim): the thread used to carry
// `aria-live="polite"` directly, so every streamed TOKEN re-announced the
// whole growing answer to a screen reader. The fix is a dedicated status
// region that only announces the turn's LIFECYCLE (thinking -> done/error),
// never its content -- this test proves the status node's text changes
// EXACTLY twice across a turn with multiple token frames, not once per token.
test("the status region announces turn lifecycle exactly twice per turn, not per token", async () => {
  vi.mocked(streamTurn).mockImplementationOnce(
    async (
      _cid: string,
      _text: string,
      _clientTurnKey: string,
      onEvent: (e: SseEvent) => void,
    ) => {
      const events: SseEvent[] = [
        { name: "accepted", data: { turn_id: "t2", message_id: "a2", event_seq: 1, turn_index: 1 } },
        { name: "token", data: { turn_id: "t2", message_id: "a2", event_seq: 2, text: "One " } },
        { name: "token", data: { turn_id: "t2", message_id: "a2", event_seq: 3, text: "two " } },
        { name: "token", data: { turn_id: "t2", message_id: "a2", event_seq: 4, text: "three." } },
        { name: "done", data: { turn_id: "t2", message_id: "a2", event_seq: 5, usage: {}, title: null } },
      ];
      events.forEach(onEvent);
    },
  );

  render(<ChatScreen />);
  const input = await screen.findByPlaceholderText(/message poseidon/i);
  const status = screen.getByRole("status");

  const observed: string[] = [];
  const observer = new MutationObserver(() => observed.push(status.textContent ?? ""));
  observer.observe(status, { characterData: true, childList: true, subtree: true });

  await userEvent.type(input, "hello{Enter}");
  await waitFor(() => expect(screen.getByText(/One two three\./)).toBeInTheDocument());

  observer.disconnect();
  expect(observed).toEqual(["Poseidon is thinking...", "Poseidon has replied."]);
});

// The thread itself must NOT carry its own aria-live -- that is the exact
// anti-pattern the status region above replaces (module docstring above).
test("the message thread carries no aria-live of its own", async () => {
  render(<ChatScreen />);
  await screen.findByText(/Ask about your data/);

  const thread = document.querySelector(".thread");
  expect(thread).not.toBeNull();
  expect(thread).not.toHaveAttribute("aria-live");
});

// Phase 12 Task 4 (page-order amendment, 2026-08-04): "Load earlier
// messages" -- appears only when the loaded conversation's own
// messagesNextCursor is non-null, fetches the older page on click, and
// PREPENDS it above what is already shown (never appended, never
// reordered -- get_messages now walks strictly backward in time).
test("Load earlier messages appears only when next_cursor is non-null, fetches the older page, and prepends it", async () => {
  server.use(
    http.get("/api/conversations", () =>
      HttpResponse.json({ items: [{ id: "c9", title: "Long chat" }], next_cursor: null })),
    http.get("/api/conversations/c9/messages", ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get("cursor")) {
        return HttpResponse.json({
          items: [
            {
              id: "old1",
              role: "assistant",
              parts: [{ kind: "text", payload: { markdown: "ancient reply" } }],
            },
          ],
          next_cursor: null,
        });
      }
      return HttpResponse.json({
        items: [
          {
            id: "recent1",
            role: "assistant",
            parts: [{ kind: "text", payload: { markdown: "recent reply" } }],
          },
        ],
        next_cursor: "opaque-cursor-1",
      });
    }),
  );

  render(<ChatScreen />);
  await screen.findByText(/recent reply/);
  const loadEarlier = screen.getByRole("button", { name: /load earlier/i });

  await userEvent.click(loadEarlier);

  await waitFor(() => expect(screen.getByText(/ancient reply/)).toBeInTheDocument());
  const texts = screen.getAllByText(/reply/).map((el) => el.textContent);
  expect(texts).toEqual(["ancient reply", "recent reply"]); // older ABOVE newer, not below
  // The just-fetched page's own next_cursor was null -- nothing further back
  // to load, so the control is gone.
  expect(screen.queryByRole("button", { name: /load earlier/i })).not.toBeInTheDocument();
});

// Final-review finding (Phase 12 whole-phase review, Finding 1): before this
// fix, `openerId === undefined` (the ROUTINE state for any conversation over
// `limit` messages, until "Load earlier" walks all the way back) withheld
// thumbs from EVERY loaded message, including the most recent answer on
// screen -- and `hydrateFeedback` filtered through the same predicate, so it
// fired zero GETs too, hiding any already-recorded verdict. `next_cursor`
// being non-null on this conversation's very FIRST page is what proves the
// true opener is NOT "recent1" -- so both a thumbs row AND its hydration GET
// must fire here, before the user ever clicks "Load earlier."
test("thumbs render and hydration fires on a loaded message before the opener is known, proven safe by a non-null next_cursor (long conversation)", async () => {
  const getCalls: string[] = [];
  server.use(
    http.get("/api/conversations", () =>
      HttpResponse.json({ items: [{ id: "c9", title: "Long chat" }], next_cursor: null })),
    http.get("/api/conversations/c9/messages", ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get("cursor")) {
        return HttpResponse.json({
          items: [
            {
              id: "old1",
              role: "assistant",
              parts: [{ kind: "text", payload: { markdown: "ancient reply" } }],
            },
          ],
          next_cursor: null,
        });
      }
      return HttpResponse.json({
        items: [
          {
            id: "recent1",
            role: "assistant",
            parts: [{ kind: "text", payload: { markdown: "recent reply" } }],
          },
        ],
        next_cursor: "opaque-cursor-1", // proves the true opener is further back, not "recent1"
      });
    }),
    http.get("/api/messages/:mid/feedback", ({ params }) => {
      getCalls.push(params.mid as string);
      return new HttpResponse(null, { status: 404 });
    }),
  );

  render(<ChatScreen />);
  await screen.findByText(/recent reply/);

  // Withheld before this fix: openerIdByConv.c9 is genuinely undefined at
  // this point (the opener hasn't been walked back to), so only
  // `messagesNextCursor.c9 !== null` makes this row -- and its hydration GET
  // -- safe to offer.
  await waitFor(() => expect(screen.getAllByLabelText("Good response")).toHaveLength(1));
  await waitFor(() => expect(getCalls).toEqual(["recent1"]));
});

test("no Load earlier control renders when the loaded conversation's next_cursor is null", async () => {
  render(<ChatScreen />);
  await screen.findByText(/Ask about your data/);

  expect(screen.queryByRole("button", { name: /load earlier/i })).not.toBeInTheDocument();
});

test("Load earlier is disabled and busy while its own fetch is in flight, and a second click is a no-op", async () => {
  let releaseOlderPage!: () => void;
  const olderPage = new Promise<Response>((resolve) => {
    releaseOlderPage = () =>
      resolve(
        HttpResponse.json({
          items: [
            {
              id: "old1",
              role: "assistant",
              parts: [{ kind: "text", payload: { markdown: "ancient reply" } }],
            },
          ],
          next_cursor: null,
        }),
      );
  });
  let cursoredCalls = 0;
  server.use(
    http.get("/api/conversations", () =>
      HttpResponse.json({ items: [{ id: "c9", title: "Long chat" }], next_cursor: null })),
    http.get("/api/conversations/c9/messages", ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get("cursor")) {
        cursoredCalls += 1;
        return olderPage;
      }
      return HttpResponse.json({
        items: [
          {
            id: "recent1",
            role: "assistant",
            parts: [{ kind: "text", payload: { markdown: "recent reply" } }],
          },
        ],
        next_cursor: "opaque-cursor-1",
      });
    }),
  );

  render(<ChatScreen />);
  await screen.findByText(/recent reply/);
  const loadEarlier = screen.getByRole("button", { name: /load earlier/i });

  await userEvent.click(loadEarlier);
  expect(loadEarlier).toBeDisabled();
  expect(loadEarlier).toHaveAttribute("aria-busy", "true");

  await userEvent.click(loadEarlier); // fired mid-fetch, must not issue a second request

  releaseOlderPage();
  await waitFor(() => expect(screen.getByText(/ancient reply/)).toBeInTheDocument());
  expect(cursoredCalls).toBe(1);
});

// Review fix round 1, Important #2: a FAILED "Load earlier" fetch must not
// leave the scroll-anchor suppression armed for the next, wholly unrelated
// messages change (e.g. the next chat turn arriving) -- that change would
// otherwise have its normal scroll-to-newest silently skipped once, using a
// long-stale anchor measurement. `messages` state never changes on this
// failure path, so nothing else would ever clear the leaked refs.
test("a failed Load earlier does not leak scroll-anchor suppression into the next unrelated messages change", async () => {
  server.use(
    http.get("/api/conversations", () =>
      HttpResponse.json({ items: [{ id: "c9", title: "Long chat" }], next_cursor: null })),
    http.get("/api/conversations/c9/messages", ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get("cursor")) {
        return new HttpResponse(null, { status: 500 }); // "Load earlier" fails
      }
      return HttpResponse.json({
        items: [
          {
            id: "recent1",
            role: "assistant",
            parts: [{ kind: "text", payload: { markdown: "recent reply" } }],
          },
        ],
        next_cursor: "opaque-cursor-1",
      });
    }),
  );
  const scrollSpy = vi.spyOn(Element.prototype, "scrollIntoView");

  render(<ChatScreen />);
  await screen.findByText(/recent reply/);
  const loadEarlier = screen.getByRole("button", { name: /load earlier/i });

  await userEvent.click(loadEarlier);
  // The failed fetch settled: the control returns to its normal (non-busy)
  // state since messagesNextCursor never changed on a rejected fetch.
  await waitFor(() => expect(loadEarlier).not.toBeDisabled());
  scrollSpy.mockClear(); // ignore whatever scrolling happened up to this point

  // An UNRELATED messages change: a brand new chat turn.
  const input = screen.getByPlaceholderText(/message poseidon/i);
  await userEvent.type(input, "hello{Enter}");
  await waitFor(() =>
    expect(screen.getByText(/Three customers drove April./)).toBeInTheDocument());

  // Normal scroll-to-newest fired for this new turn -- proving the earlier
  // failure did not leave `suppressAutoScrollRef` stuck true.
  expect(scrollSpy).toHaveBeenCalled();
  scrollSpy.mockRestore();
});

test("backend-unreachable bootstrap surfaces a retry banner instead of a silent no-op", async () => {
  // Both requests bootstrap can issue (list, then create-if-empty) fail, so the
  // banner has to come from the catch path rather than from a specific request.
  server.use(
    http.get("/api/conversations", () => new HttpResponse(null, { status: 500 })),
    http.post("/api/conversations", () => new HttpResponse(null, { status: 500 })),
  );

  render(<ChatScreen />);

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/can't reach the poseidon backend/i);
  const retry = screen.getByRole("button", { name: /retry/i });

  // Back to the happy-path handlers before retrying, same as the backend coming
  // back up.
  server.resetHandlers();
  await userEvent.click(retry);

  await screen.findByText(/Ask about your data/);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
