import { useEffect, useRef, useState, type CSSProperties } from "react";
import type { MemoryCreatedBy, MemoryEntry } from "../../api/types";
import { useSettingsStore } from "../../state/settingsStore";

// This component's own inline-style constants below deliberately reference
// ONLY the existing tokens `theme/tokens.css` already defines (no new
// colors/typography -- Global Constraints: "implement thin"). They are
// inlined rather than added as new rules to `theme/base.css` because that
// stylesheet is not a sanctioned file for this task -- the same situation,
// and the same resolution, `ChatScreen.tsx`'s own `statusRegionStyle`
// already established ("inlined rather than a CSS class since no
// stylesheet is a sanctioned file for this task"). `primaryButtonStyle`/
// `quietButtonStyle` below mirror `.feedback-actions .btn-primary`/
// `.btn-quiet`'s own recipe verbatim (base.css) -- the two established
// button archetypes the brief points at -- and `className="btn-primary"`/
// `"btn-quiet"` are kept on the elements themselves so they still pick up
// that real rule for free if a future task ever adds this feature's own
// scoped ancestor class to base.css.
const overlayStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 20,
  display: "flex",
  justifyContent: "flex-end",
  background: "rgba(0, 0, 0, 0.32)",
};

const panelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "1.25rem",
  width: "min(28rem, 100%)",
  height: "100%",
  overflowY: "auto",
  padding: "1.5rem",
  background: "var(--surface)",
  color: "var(--ink)",
  borderLeft: "1px solid var(--border)",
  boxShadow: "var(--shadow-2)",
  fontFamily: "var(--font-body)",
};

const headerRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
};

const headingStyle: CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-display)",
  color: "var(--ink)",
};

const mutedTextStyle: CSSProperties = {
  margin: 0,
  color: "var(--ink-muted)",
  fontSize: "0.8125rem",
};

// Mirrors `.feedback-comment`'s own recipe (base.css) verbatim.
const textareaStyle: CSSProperties = {
  width: "100%",
  minHeight: "5rem",
  marginTop: "0.5rem",
  padding: "0.5rem 0.625rem",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-s)",
  background: "var(--surface)",
  color: "var(--ink)",
  fontFamily: "var(--font-body)",
  fontSize: "0.875rem",
  resize: "vertical",
};

const actionsRowStyle: CSSProperties = {
  display: "flex",
  gap: "0.5rem",
  marginTop: "0.5rem",
};

// Mirrors `.feedback-actions .btn-primary` (base.css) verbatim.
const primaryButtonStyle: CSSProperties = {
  padding: "0.375rem 0.75rem",
  border: "1px solid var(--accent)",
  borderRadius: "var(--radius-s)",
  background: "var(--accent)",
  color: "var(--accent-ink)",
  fontFamily: "var(--font-body)",
  fontSize: "0.8125rem",
  cursor: "pointer",
};

// Mirrors `.feedback-actions .btn-quiet` (base.css) verbatim.
const quietButtonStyle: CSSProperties = {
  padding: "0.375rem 0.75rem",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-s)",
  background: "transparent",
  color: "var(--ink-muted)",
  fontFamily: "var(--font-body)",
  fontSize: "0.8125rem",
  cursor: "pointer",
};

const entryListStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "0.5rem",
  margin: "0.5rem 0",
  padding: 0,
  listStyle: "none",
};

const entryRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  justifyContent: "space-between",
  gap: "0.75rem",
  padding: "0.625rem 0.75rem",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-m)",
};

const entryTypeBadgeStyle: CSSProperties = {
  display: "inline-block",
  marginBottom: "0.25rem",
  padding: "0.0625rem 0.5rem",
  borderRadius: "var(--radius-s)",
  background: "var(--surface-raised)",
  color: "var(--ink-muted)",
  fontSize: "0.6875rem",
  textTransform: "uppercase",
};

const entryStatementStyle: CSSProperties = {
  margin: "0 0 0.125rem",
  color: "var(--ink)",
  fontSize: "0.875rem",
};

/** The SAME rendered form `core/personalization/memory.py`'s own
 * `_render_entries_markdown` produces (byte-for-byte, verbatim format) --
 * so the character-budget meter below can never disagree with what the
 * server actually measures against `memory_max_chars`. Not exported: this
 * is a display-only mirror for the meter, never itself sent over the wire
 * (the real entries list is what `saveMemoryEntries` PUTs). */
function renderEntriesMarkdown(entries: MemoryEntry[]): string {
  return entries
    .map((e) => `- [${e.type}] ${e.statement} (source: ${e.source_conversation_id}, at: ${e.at})`)
    .join("\n");
}

function formatDate(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleDateString();
}

/** Doc 01 section 9's own copy rule, verbatim: "Updated by Poseidon after
 * your conversation on ..." for a distiller-authored version, "Edited by
 * you" for a user-authored one. */
function attributionLine(createdBy: MemoryCreatedBy, createdAt: string): string {
  return createdBy === "distiller"
    ? `Updated by Poseidon after your conversation on ${formatDate(createdAt)}`
    : "Edited by you";
}

export interface SettingsPanelProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Phase 13 Task 5 (doc 01 section 9): the settings surface -- "My
 * instructions" (the personal system instruction) and "My memory" (the
 * reviewable, versioned, typed memory document), reached from `UserMenu`'s
 * own "Settings" trigger. Always mounted (so `UserMenu`'s trigger ref stays
 * simple); renders nothing while `open` is false, the same "always mounted,
 * closed renders null" shape `SkillsPicker.tsx` already establishes for
 * this codebase's other overlay.
 *
 * Editing never blocks chat (doc 01 section 9): `saveInstruction`/
 * `saveMemoryEntries` are optimistic with rollback on failure
 * (`settingsStore.ts`'s own doc comment). Deleting a memory entry only ever
 * edits this component's OWN local working list (`localEntries`) -- Save
 * commits the whole edited list via `saveMemoryEntries`; there is no
 * delete-one-entry endpoint (Task 3's own contract).
 */
export function SettingsPanel({ open, onClose }: SettingsPanelProps) {
  const systemInstruction = useSettingsStore((s) => s.systemInstruction);
  const memoryMaxChars = useSettingsStore((s) => s.memoryMaxChars);
  const memoryVersion = useSettingsStore((s) => s.memoryVersion);
  const memoryEntries = useSettingsStore((s) => s.memoryEntries);
  const memoryCreatedBy = useSettingsStore((s) => s.memoryCreatedBy);
  const memoryCreatedAt = useSettingsStore((s) => s.memoryCreatedAt);
  const versions = useSettingsStore((s) => s.versions);
  const loadSettings = useSettingsStore((s) => s.loadSettings);
  const saveInstruction = useSettingsStore((s) => s.saveInstruction);
  const loadMemory = useSettingsStore((s) => s.loadMemory);
  const saveMemoryEntries = useSettingsStore((s) => s.saveMemoryEntries);
  const loadVersions = useSettingsStore((s) => s.loadVersions);
  const restoreVersion = useSettingsStore((s) => s.restoreVersion);

  const [instructionDraft, setInstructionDraft] = useState(systemInstruction);
  const [instructionStatus, setInstructionStatus] = useState("");
  // The LOCAL working list Save commits (component docstring above) --
  // seeded from the store's own `memoryEntries` and re-seeded whenever that
  // slice changes (a fresh load, or the store's own post-save/rollback
  // value), but never written back to the store by a delete alone.
  const [localEntries, setLocalEntries] = useState<MemoryEntry[]>(memoryEntries);
  const [memoryStatus, setMemoryStatus] = useState("");
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setInstructionDraft(systemInstruction);
  }, [systemInstruction]);

  useEffect(() => {
    setLocalEntries(memoryEntries);
  }, [memoryEntries]);

  // "Opening the panel ... triggers the initial load" (Step 1's own
  // requirement): every open (not just the first) re-fetches, since a
  // background worker (Task 4) can distill a new memory version between
  // one open and the next.
  useEffect(() => {
    if (!open) return;
    void loadSettings();
    void loadMemory();
    void loadVersions();
  }, [open, loadSettings, loadMemory, loadVersions]);

  // Phase 12's own a11y bar (SkillsPicker.tsx's closeAndReturnFocus):
  // opening moves focus INTO the panel; UserMenu.tsx's own `onClose` is what
  // returns it to the trigger on the way back out.
  useEffect(() => {
    if (open) panelRef.current?.focus();
  }, [open]);

  if (!open) return null;

  async function handleSaveInstruction() {
    setInstructionStatus("");
    try {
      await saveInstruction(instructionDraft);
      setInstructionStatus("Instruction saved.");
    } catch {
      setInstructionStatus("Could not save your instruction. Please try again.");
    }
  }

  function handleDeleteEntry(index: number) {
    // Entries carry no stable id field (api/types.ts's own MemoryEntry
    // shape) -- index is the practical key for a single-session edit list
    // that is never reordered.
    setLocalEntries((entries) => entries.filter((_, i) => i !== index));
  }

  async function handleSaveMemory() {
    setMemoryStatus("");
    try {
      await saveMemoryEntries(localEntries);
      setMemoryStatus("Memory saved.");
    } catch {
      setMemoryStatus("Could not save your memory. Please try again.");
    }
  }

  async function handleRestore(version: number) {
    setMemoryStatus("");
    try {
      await restoreVersion(version);
      setMemoryStatus(`Restored version ${version}.`);
    } catch {
      setMemoryStatus(`Could not restore version ${version}.`);
    }
  }

  const usedChars = renderEntriesMarkdown(localEntries).length;

  return (
    <div
      style={overlayStyle}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        tabIndex={-1}
        style={panelStyle}
        onKeyDown={(event) => {
          if (event.key === "Escape") onClose();
        }}
      >
        <div style={headerRowStyle}>
          <h2 style={headingStyle}>Settings</h2>
          <button
            type="button"
            className="btn-quiet"
            style={quietButtonStyle}
            aria-label="Close settings"
            onClick={onClose}
          >
            Close
          </button>
        </div>

        <section>
          <h3 style={headingStyle}>My instructions</h3>
          <textarea
            aria-label="System instruction"
            style={textareaStyle}
            value={instructionDraft}
            onChange={(event) => setInstructionDraft(event.target.value)}
          />
          <div style={actionsRowStyle}>
            <button
              type="button"
              className="btn-primary"
              style={primaryButtonStyle}
              onClick={() => void handleSaveInstruction()}
            >
              Save instruction
            </button>
          </div>
          <div role="status" aria-live="polite" style={mutedTextStyle}>
            {instructionStatus}
          </div>
        </section>

        <section>
          <h3 style={headingStyle}>My memory</h3>
          {memoryCreatedBy !== null && memoryCreatedAt !== null ? (
            <p style={mutedTextStyle}>{attributionLine(memoryCreatedBy, memoryCreatedAt)}</p>
          ) : null}
          {localEntries.length === 0 ? (
            <p style={mutedTextStyle}>No memory entries recorded yet.</p>
          ) : (
            <ul style={entryListStyle}>
              {localEntries.map((entry, index) => (
                <li
                  key={`${entry.source_conversation_id}-${entry.at}-${index}`}
                  style={entryRowStyle}
                >
                  <div>
                    <span style={entryTypeBadgeStyle}>{entry.type}</span>
                    <p style={entryStatementStyle}>{entry.statement}</p>
                    <p style={mutedTextStyle}>
                      From conversation {entry.source_conversation_id} on{" "}
                      {formatDate(entry.at)}
                    </p>
                  </div>
                  <button
                    type="button"
                    className="btn-quiet"
                    style={quietButtonStyle}
                    aria-label={`Delete entry: ${entry.statement}`}
                    onClick={() => handleDeleteEntry(index)}
                  >
                    Delete
                  </button>
                </li>
              ))}
            </ul>
          )}

          {memoryMaxChars !== null ? (
            <p style={mutedTextStyle}>
              {usedChars} / {memoryMaxChars} characters
            </p>
          ) : null}

          <div style={actionsRowStyle}>
            <button
              type="button"
              className="btn-primary"
              style={primaryButtonStyle}
              onClick={() => void handleSaveMemory()}
            >
              Save memory
            </button>
          </div>
          <div role="status" aria-live="polite" style={mutedTextStyle}>
            {memoryStatus}
          </div>
        </section>

        <section>
          <h3 style={headingStyle}>Version history</h3>
          {versions.length === 0 ? (
            <p style={mutedTextStyle}>No prior versions yet.</p>
          ) : (
            <ul style={entryListStyle}>
              {versions.map((v) => (
                <li key={v.version} style={entryRowStyle}>
                  <div>
                    <p style={entryStatementStyle}>
                      Version {v.version} -- {v.entry_count}{" "}
                      {v.entry_count === 1 ? "entry" : "entries"}
                    </p>
                    <p style={mutedTextStyle}>{attributionLine(v.created_by, v.created_at)}</p>
                  </div>
                  {v.version !== memoryVersion ? (
                    <button
                      type="button"
                      className="btn-quiet"
                      style={quietButtonStyle}
                      onClick={() => void handleRestore(v.version)}
                    >
                      Restore
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
