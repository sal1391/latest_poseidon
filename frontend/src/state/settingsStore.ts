import { create } from "zustand";
import type { MemoryCreatedBy, MemoryEntry } from "../api/types";
import * as api from "../api/client";

/**
 * Phase 13 Task 5 (doc 01 section 9): the settings surface's own zustand
 * store -- matches `chatStore.ts`'s established pattern (zustand, NOT
 * TanStack Query -- Global Constraints: introducing that dependency's first
 * real usage here would pre-empt a separately-tracked "wire or drop"
 * decision).
 *
 * F6 (owner decision, 2026-08-05 walkthrough): Version History is removed
 * from the settings UI -- "business users don't work like that -- no
 * version history, just memory they can delete." This store therefore
 * carries no `versions` slice and no `loadVersions`/`restoreVersion`
 * actions any more; the backend's append-only versioning (`api/me.py`'s
 * `/api/me/memory/versions*` routes) stays as invisible audit/undo,
 * untouched -- `api/client.ts`'s `listMemoryVersions`/`restoreMemoryVersion`
 * remain exported for that reason, simply with no caller in this store.
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
  saveMemoryEntries: (entries: MemoryEntry[]) => Promise<void>;
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
  "loadSettings" | "saveInstruction" | "loadMemory" | "saveMemoryEntries"
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

  // Optimistic-write-then-rollback for the PUT itself: `entries` is the
  // caller's own edited local working list (SettingsPanel.tsx owns that
  // list). F6 (owner decision, 2026-08-05): this action used to also
  // refresh a `versions` slice in a separate, best-effort step after a
  // successful save -- that refresh (and the `versions` slice it fed) is
  // gone now that Version History is removed from the UI; the backend
  // still creates a new version on every PUT (`api/me.py`'s own contract),
  // it is simply never surfaced here any more.
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
}));

/** Test helper: return the store to a cold start -- mirrors `chatStore.ts`'s
 * own `resetChatStore`. */
export function resetSettingsStore(): void {
  useSettingsStore.setState(initialState());
}
