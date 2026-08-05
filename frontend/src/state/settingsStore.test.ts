import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from "vitest";
import type { MemoryEntry } from "../api/types";
import { handlers } from "../mocks/handlers";
import { resetSettingsStore, useSettingsStore } from "./settingsStore";

/**
 * Mirrors `chatStore.feedback.test.ts`'s own approach: drives every action
 * against the REAL `mocks/handlers.ts` fixtures over MSW, not a hand-rolled
 * `vi.mock("../api/client")` stub -- proves both this store's own logic AND
 * the real wire shapes the same handlers a component test renders behind,
 * so the two layers cannot silently drift from each other.
 */
const server = setupServer(...handlers);
beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

beforeEach(() => {
  resetSettingsStore();
});

// ===========================================================================
// loadSettings / saveInstruction
// ===========================================================================

test("loadSettings GETs /api/me/settings and populates instruction/updatedAt/both caps", async () => {
  server.use(
    http.get("/api/me/settings", () =>
      HttpResponse.json({
        system_instruction: "always show GP in USD k",
        updated_at: "2026-08-01T00:00:00Z",
        memory_max_chars: 4321,
        instruction_max_chars: 1234,
      })),
  );

  await useSettingsStore.getState().loadSettings();

  const state = useSettingsStore.getState();
  expect(state.systemInstruction).toBe("always show GP in USD k");
  expect(state.updatedAt).toBe("2026-08-01T00:00:00Z");
  // Never a hardcoded 8000 -- proves the cap comes from the fetched
  // response, not a client-side constant (Task 5's own cap-source amendment).
  expect(state.memoryMaxChars).toBe(4321);
  // The final review's finding I-2 carries the instruction's own cap the
  // same way, and it is a genuinely SEPARATE number from the memory cap --
  // asserted with a different value here so a fix that wired one field to
  // the other's value could not pass.
  expect(state.instructionMaxChars).toBe(1234);
});

test("saveInstruction PUTs the real {system_instruction} body and updates the store from the response", async () => {
  let seenBody: unknown;
  server.use(
    http.put("/api/me/settings", async ({ request }) => {
      seenBody = await request.json();
      return HttpResponse.json({
        system_instruction: "new instruction",
        updated_at: "2026-08-02T00:00:00Z",
      });
    }),
  );

  await useSettingsStore.getState().saveInstruction("new instruction");

  expect(seenBody).toEqual({ system_instruction: "new instruction" });
  const state = useSettingsStore.getState();
  expect(state.systemInstruction).toBe("new instruction");
  expect(state.updatedAt).toBe("2026-08-02T00:00:00Z");
});

// Optimistic-write-then-rollback -- mirrors chatStore.submitFeedback's own
// shape (Global Constraints: "reuse it, don't invent a second one").
test("saveInstruction rolls back to the previous instruction/updatedAt on failure", async () => {
  useSettingsStore.setState({
    systemInstruction: "old instruction",
    updatedAt: "2026-07-01T00:00:00Z",
  });
  server.use(http.put("/api/me/settings", () => new HttpResponse(null, { status: 500 })));

  await expect(
    useSettingsStore.getState().saveInstruction("attempted new instruction"),
  ).rejects.toThrow();

  const state = useSettingsStore.getState();
  expect(state.systemInstruction).toBe("old instruction");
  expect(state.updatedAt).toBe("2026-07-01T00:00:00Z");
});

// ===========================================================================
// loadMemory / saveMemoryEntries
// ===========================================================================

test("loadMemory GETs /api/me/memory and populates the memory slice", async () => {
  const entries = [
    {
      type: "fact",
      statement: "prefers Excel exports",
      source_conversation_id: "c1",
      at: "2026-08-01T00:00:00Z",
    },
  ];
  server.use(
    http.get("/api/me/memory", () =>
      HttpResponse.json({
        version: 3,
        entries,
        created_by: "distiller",
        created_at: "2026-08-01T00:00:00Z",
      })),
  );

  await useSettingsStore.getState().loadMemory();

  const state = useSettingsStore.getState();
  expect(state.memoryVersion).toBe(3);
  expect(state.memoryEntries).toEqual(entries);
  expect(state.memoryCreatedBy).toBe("distiller");
  expect(state.memoryCreatedAt).toBe("2026-08-01T00:00:00Z");
  expect(state.memoryLoaded).toBe(true);
});

// A brand-new user's normal state (Task 3's own contract) -- not an error
// to throw or toast about.
test("loadMemory treats a 404 as no memory yet, not an error", async () => {
  server.use(http.get("/api/me/memory", () => new HttpResponse(null, { status: 404 })));

  await expect(useSettingsStore.getState().loadMemory()).resolves.toBeUndefined();

  const state = useSettingsStore.getState();
  expect(state.memoryVersion).toBeNull();
  expect(state.memoryEntries).toEqual([]);
  expect(state.memoryCreatedBy).toBeNull();
  expect(state.memoryCreatedAt).toBeNull();
  expect(state.memoryLoaded).toBe(true);
});

test("loadMemory rethrows a non-404 failure", async () => {
  server.use(http.get("/api/me/memory", () => new HttpResponse(null, { status: 500 })));

  await expect(useSettingsStore.getState().loadMemory()).rejects.toThrow();
});

test("saveMemoryEntries PUTs the real {entries} body and updates the store from the response", async () => {
  let seenBody: unknown;
  const entries: MemoryEntry[] = [
    {
      type: "preference",
      statement: "USD thousands",
      source_conversation_id: "c1",
      at: "2026-08-01T00:00:00Z",
    },
  ];
  server.use(
    http.put("/api/me/memory", async ({ request }) => {
      seenBody = await request.json();
      return HttpResponse.json({
        version: 2,
        entries,
        created_by: "user",
        created_at: "2026-08-02T00:00:00Z",
      });
    }),
  );

  await useSettingsStore.getState().saveMemoryEntries(entries);

  expect(seenBody).toEqual({ entries });
  const state = useSettingsStore.getState();
  expect(state.memoryVersion).toBe(2);
  expect(state.memoryEntries).toEqual(entries);
  expect(state.memoryCreatedBy).toBe("user");
  expect(state.memoryCreatedAt).toBe("2026-08-02T00:00:00Z");
});

// Optimistic-write-then-rollback, same shape as saveInstruction above --
// the LOCAL edited list (e.g. after a delete) is what's optimistic; on
// failure it reverts to whatever was there before this call.
test("saveMemoryEntries rolls back memoryEntries to the previous list on failure", async () => {
  const prevEntries: MemoryEntry[] = [
    {
      type: "fact",
      statement: "old fact",
      source_conversation_id: "c1",
      at: "2026-08-01T00:00:00Z",
    },
  ];
  useSettingsStore.setState({ memoryEntries: prevEntries });
  server.use(http.put("/api/me/memory", () => new HttpResponse(null, { status: 500 })));

  await expect(
    useSettingsStore.getState().saveMemoryEntries([
      {
        type: "fact",
        statement: "attempted edit",
        source_conversation_id: "c1",
        at: "2026-08-02T00:00:00Z",
      },
    ]),
  ).rejects.toThrow();

  expect(useSettingsStore.getState().memoryEntries).toEqual(prevEntries);
});
