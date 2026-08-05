import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test, vi } from "vitest";
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

// Fix round 1 (review finding Important 2): PUT /api/me/memory always
// creates a new version (same reasoning as restoreVersion's own test
// below) -- a successful save must refresh the version list too, or the
// just-created version stays invisible until the panel is closed and
// reopened (the exact gap phase-gate item 5's "delete an entry, then
// save" flow would otherwise hit).
test("saveMemoryEntries refreshes the version list after a successful save", async () => {
  const entries: MemoryEntry[] = [
    {
      type: "preference",
      statement: "USD thousands",
      source_conversation_id: "c1",
      at: "2026-08-01T00:00:00Z",
    },
  ];
  let saveCalled = false;
  let versionsFetchedAfterSave = false;
  server.use(
    http.put("/api/me/memory", () => {
      saveCalled = true;
      return HttpResponse.json({
        version: 2,
        entries,
        created_by: "user",
        created_at: "2026-08-02T00:00:00Z",
      });
    }),
    http.get("/api/me/memory/versions", () => {
      versionsFetchedAfterSave = saveCalled;
      return HttpResponse.json([
        { version: 2, created_by: "user", created_at: "2026-08-02T00:00:00Z", entry_count: 1 },
        { version: 1, created_by: "user", created_at: "2026-08-01T00:00:00Z", entry_count: 1 },
      ]);
    }),
  );

  await useSettingsStore.getState().saveMemoryEntries(entries);

  expect(versionsFetchedAfterSave).toBe(true);
  expect(useSettingsStore.getState().versions).toEqual([
    { version: 2, created_by: "user", created_at: "2026-08-02T00:00:00Z", entry_count: 1 },
    { version: 1, created_by: "user", created_at: "2026-08-01T00:00:00Z", entry_count: 1 },
  ]);
});

// Fix round 2 (review finding): the PUT itself succeeding must NOT be
// undone by an independent failure of the follow-up versions refresh --
// round 1's shape put both calls in the same try/catch, so a
// GET /api/me/memory/versions failure AFTER a successful PUT rolled
// memoryEntries back to its pre-save value (while memoryVersion/
// memoryCreatedBy/memoryCreatedAt stayed at the new post-save values --
// an internally inconsistent store) and made this whole call reject, so
// the caller (SettingsPanel's handleSaveMemory) reported the save itself
// as failed even though it had already landed server-side.
test("a versions-refresh failure after a successful save does not roll back the save or reject the call", async () => {
  const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const savedEntries: MemoryEntry[] = [
    {
      type: "preference",
      statement: "USD thousands",
      source_conversation_id: "c1",
      at: "2026-08-01T00:00:00Z",
    },
  ];
  server.use(
    http.put("/api/me/memory", () =>
      HttpResponse.json({
        version: 2,
        entries: savedEntries,
        created_by: "user",
        created_at: "2026-08-02T00:00:00Z",
      })),
    http.get("/api/me/memory/versions", () => new HttpResponse(null, { status: 500 })),
  );

  await expect(useSettingsStore.getState().saveMemoryEntries(savedEntries)).resolves.toBeUndefined();

  const state = useSettingsStore.getState();
  // The save's own confirmed state stands -- no rollback, and every field
  // the PUT response carried stays mutually consistent.
  expect(state.memoryEntries).toEqual(savedEntries);
  expect(state.memoryVersion).toBe(2);
  expect(state.memoryCreatedBy).toBe("user");
  expect(state.memoryCreatedAt).toBe("2026-08-02T00:00:00Z");
  // versions itself is simply left stale (never touched by this failed
  // refresh) -- acceptable per the reviewer's own framing.
  expect(state.versions).toEqual([]);
  expect(warnSpy).toHaveBeenCalledTimes(1);
  warnSpy.mockRestore();
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

// ===========================================================================
// loadVersions / restoreVersion
// ===========================================================================

test("loadVersions GETs /api/me/memory/versions and populates versions", async () => {
  const versions = [
    { version: 2, created_by: "user", created_at: "2026-08-02T00:00:00Z", entry_count: 1 },
    { version: 1, created_by: "distiller", created_at: "2026-08-01T00:00:00Z", entry_count: 2 },
  ];
  server.use(http.get("/api/me/memory/versions", () => HttpResponse.json(versions)));

  await useSettingsStore.getState().loadVersions();

  expect(useSettingsStore.getState().versions).toEqual(versions);
});

test("restoreVersion POSTs to the restore route, updates the memory slice, and refreshes the version list", async () => {
  let restoreCalled = false;
  let versionsFetchedAfterRestore = false;
  server.use(
    http.post("/api/me/memory/versions/1/restore", () => {
      restoreCalled = true;
      return HttpResponse.json({
        version: 3,
        entries: [
          {
            type: "fact",
            statement: "restored",
            source_conversation_id: "c1",
            at: "2026-08-01T00:00:00Z",
          },
        ],
        created_by: "user",
        created_at: "2026-08-03T00:00:00Z",
      });
    }),
    http.get("/api/me/memory/versions", () => {
      versionsFetchedAfterRestore = restoreCalled;
      return HttpResponse.json([
        { version: 3, created_by: "user", created_at: "2026-08-03T00:00:00Z", entry_count: 1 },
      ]);
    }),
  );

  await useSettingsStore.getState().restoreVersion(1);

  const state = useSettingsStore.getState();
  expect(state.memoryVersion).toBe(3);
  expect(state.memoryEntries).toEqual([
    {
      type: "fact",
      statement: "restored",
      source_conversation_id: "c1",
      at: "2026-08-01T00:00:00Z",
    },
  ]);
  expect(state.memoryCreatedBy).toBe("user");
  expect(versionsFetchedAfterRestore).toBe(true);
  expect(state.versions).toEqual([
    { version: 3, created_by: "user", created_at: "2026-08-03T00:00:00Z", entry_count: 1 },
  ]);
});

// Final whole-phase review fold-in A: the exact asymmetry round 2 left
// behind. `saveMemoryEntries` got its follow-up `loadVersions()` moved into
// its own best-effort, never-rethrowing try/catch; `restoreVersion`'s
// identical follow-up call kept no guard at all, so a versions-refresh
// failure after a restore the SERVER had already committed rejected this
// action and made the panel say "Could not restore version N" for a restore
// that had in fact succeeded. Unlike the save path there is no rollback to
// corrupt here (nothing in this action reverts), so the damage was purely
// the false failure report -- but the two halves of a pair must not stay
// asymmetric, and a user told a restore failed will retry it, appending a
// duplicate version.
test("a versions-refresh failure after a successful restore does not reject or surface an error", async () => {
  const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => undefined);
  const restoredEntries: MemoryEntry[] = [
    {
      type: "fact",
      statement: "restored",
      source_conversation_id: "c1",
      at: "2026-08-01T00:00:00Z",
    },
  ];
  server.use(
    http.post("/api/me/memory/versions/1/restore", () =>
      HttpResponse.json({
        version: 3,
        entries: restoredEntries,
        created_by: "user",
        created_at: "2026-08-03T00:00:00Z",
      })),
    http.get("/api/me/memory/versions", () => new HttpResponse(null, { status: 500 })),
  );

  await expect(useSettingsStore.getState().restoreVersion(1)).resolves.toBeUndefined();

  const state = useSettingsStore.getState();
  // The restore's own confirmed state stands, every field mutually
  // consistent; `versions` is merely left stale, exactly as it is on the
  // save path's equivalent failure.
  expect(state.memoryVersion).toBe(3);
  expect(state.memoryEntries).toEqual(restoredEntries);
  expect(state.memoryCreatedBy).toBe("user");
  expect(state.memoryCreatedAt).toBe("2026-08-03T00:00:00Z");
  expect(state.versions).toEqual([]);
  expect(warnSpy).toHaveBeenCalledTimes(1);
  warnSpy.mockRestore();
});
