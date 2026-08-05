import { http, HttpResponse } from "msw";
import type { Conversation, MemoryEntry, Message } from "../api/types";

/** Fixed ids keep component assertions readable: one conversation, one opener. */
export const mockConversation: Conversation = { id: "c1", title: "New chat" };

/** Task-6 opener shape: an intro line plus the two flow-entry chips.
 *
 * `send_text` (P8 whole-branch final-review wave, 2026-07-30, item 9 /
 * M-2): the pinned D19 entry phrases, matching `api/live_chat.py`'s own
 * real `TranscriptStore.create_conversation` opener byte-for-byte (that
 * module's own `ENTRY_PHRASE_EXISTING`/`ENTRY_PHRASE_PROSPECT`, imported
 * from `orchestrator.py`) -- this mock used to omit the field entirely,
 * which is what let ChatScreen.test.tsx's own chip-click test keep
 * passing while asserting the WRONG thing (the bare label, never sent by
 * a real backend) for the D19 entry click it exercises. */
export const mockOpener: Message = {
  id: "m0",
  role: "assistant",
  parts: [
    { kind: "text", payload: { markdown: "Ask about your data, or pick a flow:" } },
    {
      kind: "chips",
      payload: {
        options: [
          {
            id: "existing_customer",
            label: "Existing customer",
            send_text: "start an existing-customer brief",
          },
          {
            id: "new_prospect",
            label: "New customer prospect",
            send_text: "start a new-prospect brief",
          },
        ],
      },
    },
  ],
};

/**
 * MSW v2 handlers for the mock chat API.
 *
 * The conversation list starts empty so a mounting client takes the
 * create-first-conversation path and lands on the opener above. The SSE turn
 * route (`POST /api/conversations/:cid/messages`) is deliberately absent —
 * component tests stub `api/sse` instead of streaming over the network.
 *
 * Phase 10 Task 4: the list/messages envelopes changed shape (real backend,
 * `poseidon.api.live_chat`) from a bare `{conversations: [...]}`/
 * `{messages: [...]}` array wrapper to `{items: [...], next_cursor}` --
 * these handlers now mirror that byte-for-byte so this mock layer cannot
 * drift from the real contract the way it did across Task 3's cutover (that
 * task's own report: "the vitest pass is not a green light" -- see
 * task-3-report.md). `next_cursor: null` throughout: no fixture here has a
 * second page, so `Sidebar`'s load-more control never renders under these
 * mocks.
 */
export const handlers = [
  http.post("/api/conversations", () =>
    HttpResponse.json({ conversation: mockConversation, opener: mockOpener }, { status: 201 })),

  http.get("/api/conversations", () => HttpResponse.json({ items: [], next_cursor: null })),

  http.get("/api/conversations/:cid/messages", () =>
    HttpResponse.json({ items: [mockOpener], next_cursor: null })),

  http.post("/api/messages/:mid/feedback", () => new HttpResponse(null, { status: 204 })),

  // Phase 12 Task 2: GET /api/messages/{mid}/feedback's real shape --
  // 200 with the recorded verdict, or 404. This mock is stateless (unlike a
  // real backend, it never actually remembers what the POST handler above
  // was just sent) and defaults to "nothing recorded" for every id -- the
  // correct default for this file's own single-opener fixture, since a
  // freshly-created mock conversation has no turn-backed messages at all to
  // have recorded feedback on yet. A test that needs hydration to find
  // something overrides this handler with `server.use(...)` for the
  // specific message id it cares about, the same convention every other
  // per-test override in this codebase already follows.
  http.get("/api/messages/:mid/feedback", () => new HttpResponse(null, { status: 404 })),

  // GET /api/skills is live-chat-only (poseidon.api.live_chat's own module
  // docstring); mock mode genuinely has no such route, so the mock handler
  // set answers 404 here too -- what makes SkillsPicker's fallback-to-
  // static-list path deterministic and fast under these mock-backed tests,
  // rather than an unhandled request falling through to a real network call.
  http.get("/api/skills", () => new HttpResponse(null, { status: 404 })),

  // Phase 13 Task 5: the settings surface's own routes (poseidon.api.me).
  // Defaults mirror a brand-new user (Task 3's own contract): an empty
  // instruction, and no memory version yet at all (a 404, not an empty
  // list -- `GET /api/me/memory`'s own null-vs-404 distinction). Per-test
  // overrides use `server.use(...)`, the same convention every other
  // override in this file already follows.
  http.get("/api/me/settings", () =>
    HttpResponse.json({
      system_instruction: "",
      updated_at: null,
      memory_max_chars: 8000,
      instruction_max_chars: 8000,
    })),

  http.put("/api/me/settings", async ({ request }) => {
    const body = (await request.json()) as { system_instruction: string };
    return HttpResponse.json({
      system_instruction: body.system_instruction,
      updated_at: "2026-08-04T12:00:00Z",
    });
  }),

  http.get("/api/me/memory", () => new HttpResponse(null, { status: 404 })),

  http.put("/api/me/memory", async ({ request }) => {
    const body = (await request.json()) as { entries: MemoryEntry[] };
    return HttpResponse.json({
      version: 1,
      entries: body.entries,
      created_by: "user",
      created_at: "2026-08-04T12:00:00Z",
    });
  }),

  http.get("/api/me/memory/versions", () => HttpResponse.json([])),

  http.post("/api/me/memory/versions/:version/restore", () => new HttpResponse(null, { status: 404 })),
];
