import { create } from "zustand";
import type { MemoryCreatedBy, MemoryEntry, MemoryVersionSummary } from "../api/types";
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
  // Same optimistic-write-then-rollback shape as saveInstruction. `entries`
  // is the caller's own edited local working list (SettingsPanel.tsx owns
  // that list; deleting an entry is "edit the list, then Save the whole
  // thing" -- there is no delete-one-entry endpoint, Task 3's own contract).
  saveMemoryEntries: (entries: MemoryEntry[]) => Promise<void>;
  loadVersions: () => Promise<void>;
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

  saveMemoryEntries: async (entries) => {
    const prevEntries = get().memoryEntries;
    set({ memoryEntries: entries });
    try {
      const body = await api.putMemory(entries);
      set({
        memoryVersion: body.version,
        memoryEntries: body.entries,
        memoryCreatedBy: body.created_by,
        memoryCreatedAt: body.created_at,
        memoryLoaded: true,
      });
    } catch (err) {
      set({ memoryEntries: prevEntries });
      throw err;
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
  restoreVersion: async (version) => {
    const body = await api.restoreMemoryVersion(version);
    set({
      memoryVersion: body.version,
      memoryEntries: body.entries,
      memoryCreatedBy: body.created_by,
      memoryCreatedAt: body.created_at,
      memoryLoaded: true,
    });
    await get().loadVersions();
  },
}));

/** Test helper: return the store to a cold start -- mirrors `chatStore.ts`'s
 * own `resetChatStore`. */
export function resetSettingsStore(): void {
  useSettingsStore.setState(initialState());
}
