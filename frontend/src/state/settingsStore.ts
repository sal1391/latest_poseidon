import { create } from "zustand";
import type { MemoryCreatedBy, MemoryEntry, MemoryVersion, MemoryVersionSummary } from "../api/types";
import * as api from "../api/client";

/**
 * Phase 13 Task 5 (doc 01 section 9): the settings surface's own zustand
 * store -- matches `chatStore.ts`'s established pattern (zustand, NOT
 * TanStack Query -- Global Constraints: introducing that dependency's first
 * real usage here would pre-empt a separately-tracked "wire or drop"
 * decision).
 */
export interface SettingsState {
  systemInstruction: string;
  updatedAt: string | null;
  // Phase 13 Task 5 amendment (cap-source gap, commit 5130fee): `null`
  // until a real `GET /api/me/settings` response lands -- the ONE place
  // this number ever comes from (`SettingsPanel.tsx`'s character-budget
  // meter is the one consumer), never a hardcoded guess.
  memoryMaxChars: number | null;
  // Final whole-phase review, finding I-2: the instruction's own
  // server-enforced cap, carried on the SAME response and held to the same
  // discipline as `memoryMaxChars` above -- `null` until a real
  // `GET /api/me/settings` lands, never a hardcoded guess. A SEPARATE
  // number from `memoryMaxChars`, not an alias of it: the two cap different
  // documents and the backend derives them from different places (see
  // `core/personalization/profile.py`'s module docstring). Consumed by
  // `SettingsPanel.tsx`'s instruction textarea as its `maxLength`.
  instructionMaxChars: number | null;
  memoryVersion: number | null;
  memoryEntries: MemoryEntry[];
  memoryCreatedBy: MemoryCreatedBy | null;
  memoryCreatedAt: string | null;
  // Distinguishes "GET /api/me/memory hasn't resolved yet" from "resolved,
  // and this user genuinely has no memory version" (the real 404 case,
  // Task 3's own contract) -- both states otherwise look identical
  // (memoryVersion: null, memoryEntries: []).
  memoryLoaded: boolean;
  versions: MemoryVersionSummary[];
  loadSettings: () => Promise<void>;
  // Optimistic-write-then-rollback -- mirrors `chatStore.submitFeedback`'s
  // already-established shape (Global Constraints: this codebase has one
  // canonical pattern for this, reuse it, don't invent a second one).
  saveInstruction: (instruction: string) => Promise<void>;
  loadMemory: () => Promise<void>;
  // Optimistic-write-then-rollback for the PUT itself only. `entries` is
  // the caller's own edited local working list (SettingsPanel.tsx owns
  // that list; deleting an entry is "edit the list, then Save the whole
  // thing" -- there is no delete-one-entry endpoint, Task 3's own contract).
  // A successful save also refreshes `versions` (fix round 1, Important 2)
  // as a SEPARATE, best-effort step that cannot roll back the save or make
  // this call reject (fix round 2 -- see this action's own implementation
  // comment for the failure mode round 1's shape had).
  saveMemoryEntries: (entries: MemoryEntry[]) => Promise<void>;
  loadVersions: () => Promise<void>;
  // Appends a new version carrying `version`'s entries (Task 3's contract),
  // then refreshes `versions` as a SEPARATE, best-effort step that cannot
  // make this call reject -- the same shape `saveMemoryEntries` above has,
  // applied to the other half of the pair (final review fold-in A; see this
  // action's own implementation comment).
  restoreVersion: (version: number) => Promise<void>;
}

// A factory, not a literal object, so `resetSettingsStore` below can reuse
// it without a second hand-maintained field list that could drift from the
// store's own initial state (unlike `chatStore.ts`'s `resetChatStore`,
// which lists its fields twice -- this store has more slices, so a factory
// keeps the two in permanent sync instead of relying on discipline). Also
// hands each caller its OWN fresh arrays -- a shared literal's `[]` would be
// the SAME array reference reused across every reset.
function initialState(): Omit<
  SettingsState,
  | "loadSettings"
  | "saveInstruction"
  | "loadMemory"
  | "saveMemoryEntries"
  | "loadVersions"
  | "restoreVersion"
> {
  return {
    systemInstruction: "",
    updatedAt: null,
    memoryMaxChars: null,
    instructionMaxChars: null,
    memoryVersion: null,
    memoryEntries: [],
    memoryCreatedBy: null,
    memoryCreatedAt: null,
    memoryLoaded: false,
    versions: [],
  };
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  ...initialState(),

  loadSettings: async () => {
    const body = await api.getSettings();
    set({
      systemInstruction: body.system_instruction,
      updatedAt: body.updated_at,
      memoryMaxChars: body.memory_max_chars,
      instructionMaxChars: body.instruction_max_chars,
    });
  },

  saveInstruction: async (instruction) => {
    const prevInstruction = get().systemInstruction;
    const prevUpdatedAt = get().updatedAt;
    set({ systemInstruction: instruction });
    try {
      const body = await api.putSettings(instruction);
      set({ systemInstruction: body.system_instruction, updatedAt: body.updated_at });
    } catch (err) {
      set({ systemInstruction: prevInstruction, updatedAt: prevUpdatedAt });
      throw err;
    }
  },

  // GET /api/me/memory 404s for a user with no memory version yet (Task 3's
  // own contract, a normal state for a new user) -- treated the same way
  // `chatStore.ts`'s own `hydrateFeedback` treats a 404 for a message with
  // no recorded verdict: expected, not exceptional. Any OTHER failure still
  // throws, so a real outage surfaces to the caller instead of silently
  // looking like "no memory yet."
  loadMemory: async () => {
    try {
      const body = await api.getMemory();
      set({
        memoryVersion: body.version,
        memoryEntries: body.entries,
        memoryCreatedBy: body.created_by,
        memoryCreatedAt: body.created_at,
        memoryLoaded: true,
      });
    } catch (err) {
      const status = err instanceof api.ApiError ? err.status : undefined;
      if (status !== 404) throw err;
      set({
        memoryVersion: null,
        memoryEntries: [],
        memoryCreatedBy: null,
        memoryCreatedAt: null,
        memoryLoaded: true,
      });
    }
  },

  // Fix round 1 (review finding Important 2): PUT /api/me/memory always
  // creates a new version too (Task 3's own contract, identical to
  // restoreVersion's own reasoning below) -- so the version list is stale
  // the instant a save resolves, exactly like a restore.
  //
  // Fix round 2 (review finding, correcting round 1's own claim): round 1
  // put the loadVersions() refresh INSIDE the same try that wraps the
  // rollback catch, and claimed that was "unguarded, matching
  // restoreVersion's own precedent" -- true of its POSITION in the
  // sequence, false of its CONTROL FLOW: restoreVersion has no enclosing
  // try/catch at all, so nothing about a failed refresh there could ever
  // roll anything back. Here, by contrast, api.putMemory can succeed (the
  // server has already committed the new version) and the FOLLOWING
  // loadVersions() call can independently fail (a network blip, a
  // transient 5xx, a token expiring between the two calls) -- with the
  // round-1 shape, that failure fired the SAME catch the PUT's own
  // failure uses, reverting `memoryEntries` to `prevEntries` even though
  // the save genuinely succeeded, leaving `memoryVersion`/`memoryCreatedBy`/
  // `memoryCreatedAt` (already set to the new values) inconsistent with a
  // reverted `memoryEntries`, AND making this whole call reject so the
  // panel reported "Could not save your memory" for a save that had
  // already landed server-side -- a user-driven retry would then PUT the
  // same list again, creating a duplicate version.
  //
  // Fixed shape: the try/catch below wraps ONLY api.putMemory itself (the
  // one call whose failure means nothing was saved, so rolling back is
  // correct); the confirmed post-save state is committed unconditionally
  // once that call succeeds; loadVersions() then runs in its OWN
  // best-effort try/catch that never rethrows -- a refresh failure here
  // leaves `versions` merely stale (acceptable; the version list still
  // sits behind an already-successful save and refreshes the next time
  // this panel opens or acts) rather than corrupting already-committed
  // state or reporting a false failure. Mirrors `chatStore.ts`'s own
  // `hydrateFeedback`: best-effort, warn on failure, never throw.
  saveMemoryEntries: async (entries) => {
    const prevEntries = get().memoryEntries;
    set({ memoryEntries: entries });
    let body: MemoryVersion;
    try {
      body = await api.putMemory(entries);
    } catch (err) {
      set({ memoryEntries: prevEntries });
      throw err;
    }
    set({
      memoryVersion: body.version,
      memoryEntries: body.entries,
      memoryCreatedBy: body.created_by,
      memoryCreatedAt: body.created_at,
      memoryLoaded: true,
    });
    try {
      await get().loadVersions();
    } catch (err) {
      console.warn(
        "saveMemoryEntries: the save succeeded but refreshing the version list failed",
        err,
      );
    }
  },

  loadVersions: async () => {
    const versions = await api.listMemoryVersions();
    set({ versions });
  },

  // Restoring APPENDS a brand-new version (Task 3's own contract: restore
  // never rewrites the version being restored), so the version list is
  // stale the instant this resolves -- refetched here rather than hand-
  // patched into `versions` locally, keeping `list_versions`'s own response
  // the single source of truth for that list's shape.
  //
  // Final whole-phase review fold-in A: that refresh runs in its OWN
  // best-effort, never-rethrowing try/catch, the identical shape
  // `saveMemoryEntries` above got in fix round 2 -- the two halves of this
  // pair must not diverge. Unguarded, an independently-failing
  // `GET /api/me/memory/versions` (a network blip, a transient 5xx, a token
  // expiring between the two calls) made this whole call reject for a
  // restore the server had ALREADY committed, so the panel reported "Could
  // not restore version N" for a restore that succeeded -- and a user told
  // that will retry, appending a duplicate version. Unlike the save path
  // there is no rollback here for a late failure to corrupt, which is why
  // this was the narrower of the two; a stale `versions` list is the whole
  // remaining cost, and it refreshes on this panel's next open or action.
  restoreVersion: async (version) => {
    const body = await api.restoreMemoryVersion(version);
    set({
      memoryVersion: body.version,
      memoryEntries: body.entries,
      memoryCreatedBy: body.created_by,
      memoryCreatedAt: body.created_at,
      memoryLoaded: true,
    });
    try {
      await get().loadVersions();
    } catch (err) {
      console.warn(
        "restoreVersion: the restore succeeded but refreshing the version list failed",
        err,
      );
    }
  },
}));

/** Test helper: return the store to a cold start -- mirrors `chatStore.ts`'s
 * own `resetChatStore`. */
export function resetSettingsStore(): void {
  useSettingsStore.setState(initialState());
}
