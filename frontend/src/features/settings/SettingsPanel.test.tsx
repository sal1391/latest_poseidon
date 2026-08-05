import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import type { MemoryEntry } from "../../api/types";
import { resetSettingsStore, useSettingsStore } from "../../state/settingsStore";
import { SettingsPanel } from "./SettingsPanel";

const sampleEntry: MemoryEntry = {
  type: "preference",
  statement: "prefers Excel exports",
  source_conversation_id: "c-123",
  at: "2026-08-01T00:00:00Z",
};

const secondEntry: MemoryEntry = {
  type: "fact",
  statement: "second entry",
  source_conversation_id: "c-2",
  at: "2026-08-02T00:00:00Z",
};

/** Every test seeds the store's three load actions as no-op stubs (the
 * panel calls them on open, per Step 1's own "opening the panel ... triggers
 * the initial load" requirement) so no test here depends on a real network
 * layer -- mirrors `Sidebar.test.tsx`'s own `useChatStore.setState({...})`
 * override technique for a component driven entirely by store state. */
function seedNoopLoaders(): void {
  useSettingsStore.setState({
    loadSettings: vi.fn(async () => undefined),
    loadMemory: vi.fn(async () => undefined),
    loadVersions: vi.fn(async () => undefined),
  });
}

beforeEach(() => {
  resetSettingsStore();
});

test("renders nothing when closed", () => {
  act(seedNoopLoaders);

  const { container } = render(<SettingsPanel open={false} onClose={() => undefined} />);

  expect(container).toBeEmptyDOMElement();
});

test("opening the panel calls loadSettings, loadMemory, and loadVersions", () => {
  const loadSettings = vi.fn(async () => undefined);
  const loadMemory = vi.fn(async () => undefined);
  const loadVersions = vi.fn(async () => undefined);
  act(() => {
    useSettingsStore.setState({ loadSettings, loadMemory, loadVersions });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(loadSettings).toHaveBeenCalledTimes(1);
  expect(loadMemory).toHaveBeenCalledTimes(1);
  expect(loadVersions).toHaveBeenCalledTimes(1);
});

test("renders the instruction textarea pre-filled from the loaded value", () => {
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({ systemInstruction: "always show GP in USD k" });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(screen.getByLabelText(/system instruction/i)).toHaveValue("always show GP in USD k");
});

test("editing the instruction and clicking Save calls saveInstruction with the new text", async () => {
  const saveInstruction = vi.fn(async () => undefined);
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({ saveInstruction });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);
  const textarea = screen.getByLabelText(/system instruction/i);
  await userEvent.clear(textarea);
  await userEvent.type(textarea, "new instruction");
  await userEvent.click(screen.getByRole("button", { name: /save instruction/i }));

  expect(saveInstruction).toHaveBeenCalledWith("new instruction");
});

// Final whole-phase review, finding I-1: the SAME class of race Task 5's
// round-1 fix caught on the entries list, never applied to the instruction
// draft. The draft's resync effect fired on every `systemInstruction`
// change -- including the ROLLBACK a failed save performs -- so a user who
// typed a new instruction, hit Save, and got a 500 watched their text
// silently revert to the old value while the panel told them to "try
// again": there was nothing left to retry.
//
// `saveInstruction` here is a REAL two-phase stub (not the inert
// `vi.fn(async () => undefined)` the happy-path test above uses), driving
// the store's genuine optimistic-set-then-rollback sequence -- the same
// technique the round-1 dirty-guard test uses for `loadMemory`, and for the
// same reason. An inert stub proves nothing (it never touches
// `systemInstruction`, so the resync effect it must defeat never fires),
// and so does a stub that performs both phases in one synchronous burst:
// React batches them into a single commit whose net `systemInstruction` is
// unchanged, so the effect's dependency never changes and the bug hides.
// The two `set()` calls have to land in SEPARATE commits, exactly as they
// do in life with a real in-flight PUT between them -- hence the
// test-controlled `failSave`.
test("a failed instruction save keeps the user's typed text in the textarea", async () => {
  let failSave: (() => void) | undefined;
  const saveInstruction = vi.fn((instruction: string) => {
    const previous = useSettingsStore.getState().systemInstruction;
    // Phase 1 -- the optimistic write, committed while the PUT is in flight.
    useSettingsStore.setState({ systemInstruction: instruction });
    return new Promise<void>((_resolve, reject) => {
      failSave = () => {
        // Phase 2 -- the PUT rejected, so the real store rolls the slice
        // back and rethrows. This is the `set()` that clobbered the draft.
        act(() => {
          useSettingsStore.setState({ systemInstruction: previous });
        });
        reject(new Error("PUT /api/me/settings failed"));
      };
    });
  });
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({ systemInstruction: "the old instruction", saveInstruction });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);
  const textarea = screen.getByLabelText(/system instruction/i);
  await userEvent.clear(textarea);
  await userEvent.type(textarea, "the text the user just typed");
  await userEvent.click(screen.getByRole("button", { name: /save instruction/i }));

  expect(saveInstruction).toHaveBeenCalledWith("the text the user just typed");
  expect(textarea).toHaveValue("the text the user just typed");

  await act(async () => {
    failSave?.();
  });

  expect(screen.getByText(/could not save your instruction/i)).toBeInTheDocument();
  expect(textarea).toHaveValue("the text the user just typed");
});

// The same clobber without any save at all: every open fires a background
// `loadSettings()`, and its resolution used to overwrite whatever the user
// had typed while it was in flight.
test("a background settings load resolving mid-typing does not clobber the draft", async () => {
  let resolveLoad: (() => void) | undefined;
  const loadSettings = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveLoad = () => {
          act(() => {
            useSettingsStore.setState({ systemInstruction: "the server's own value" });
          });
          resolve();
        };
      }),
  );
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({ loadSettings, systemInstruction: "" });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);
  const textarea = screen.getByLabelText(/system instruction/i);
  await userEvent.type(textarea, "half a thought");

  act(() => {
    resolveLoad?.();
  });

  expect(textarea).toHaveValue("half a thought");
});

// Guards against over-correcting the two fixes above -- exactly the way the
// entries pair does: with NO pending edit, a background load must still
// update the textarea, or the dirty guard has become a blanket "ignore
// every load."
test("a background settings load resolving with no pending edit still updates the draft", async () => {
  let resolveLoad: (() => void) | undefined;
  const loadSettings = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveLoad = () => {
          act(() => {
            useSettingsStore.setState({ systemInstruction: "the server's own value" });
          });
          resolve();
        };
      }),
  );
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({ loadSettings, systemInstruction: "" });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  act(() => {
    resolveLoad?.();
  });

  expect(screen.getByLabelText(/system instruction/i)).toHaveValue("the server's own value");
});

// A SUCCESSFUL save clears the dirty flag, so the store's own confirmed,
// server-returned value takes over again from that point on -- the same
// contract `handleSaveMemory` already has for the entries list.
test("a successful instruction save resumes tracking the store's confirmed value", async () => {
  const saveInstruction = vi.fn(async (instruction: string) => {
    useSettingsStore.setState({ systemInstruction: instruction });
  });
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({ systemInstruction: "the old instruction", saveInstruction });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);
  const textarea = screen.getByLabelText(/system instruction/i);
  await userEvent.clear(textarea);
  await userEvent.type(textarea, "a saved instruction");
  await userEvent.click(screen.getByRole("button", { name: /save instruction/i }));
  expect(screen.getByText(/instruction saved/i)).toBeInTheDocument();

  // A later background load (e.g. the next open, or a distiller-side
  // change) must be picked up again now that nothing is unsaved.
  act(() => {
    useSettingsStore.setState({ systemInstruction: "a newer server value" });
  });

  expect(textarea).toHaveValue("a newer server value");
});

// Finding I-2's client half: the textarea bounds its own input by the cap
// the server actually enforces, fetched on the same response the memory
// meter's cap already rides (`instruction_max_chars`) -- never a number
// hardcoded here, the exact gap Task 5's own cap-source amendment closed
// for `memory_max_chars`.
test("the instruction textarea's maxLength reads the fetched instruction cap", () => {
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({ instructionMaxChars: 1234 });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(screen.getByLabelText(/system instruction/i)).toHaveAttribute("maxlength", "1234");
});

test("the instruction textarea carries no maxLength before the cap has been fetched", () => {
  act(seedNoopLoaders);

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(screen.getByLabelText(/system instruction/i)).not.toHaveAttribute("maxlength");
});

test("renders each memory entry's statement, type, source conversation, and date", () => {
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      memoryVersion: 1,
      memoryEntries: [sampleEntry],
      memoryCreatedBy: "user",
      memoryCreatedAt: "2026-08-01T00:00:00Z",
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(screen.getByText("prefers Excel exports")).toBeInTheDocument();
  expect(screen.getByText(/preference/i)).toBeInTheDocument();
  expect(screen.getByText(/c-123/)).toBeInTheDocument();
});

test("shows the distiller attribution copy for a memory version written by the worker", () => {
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      memoryVersion: 1,
      memoryEntries: [sampleEntry],
      memoryCreatedBy: "distiller",
      memoryCreatedAt: "2026-08-01T00:00:00Z",
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(screen.getByText(/updated by poseidon after your conversation on/i)).toBeInTheDocument();
});

test("shows the 'Edited by you' attribution copy for a memory version written by the user", () => {
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      memoryVersion: 1,
      memoryEntries: [sampleEntry],
      memoryCreatedBy: "user",
      memoryCreatedAt: "2026-08-01T00:00:00Z",
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(screen.getByText(/edited by you/i)).toBeInTheDocument();
});

test("deleting an entry removes it from the local list without calling saveMemoryEntries", async () => {
  const saveMemoryEntries = vi.fn(async () => undefined);
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      memoryVersion: 1,
      memoryEntries: [sampleEntry],
      memoryCreatedBy: "user",
      memoryCreatedAt: "2026-08-01T00:00:00Z",
      saveMemoryEntries,
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);
  await userEvent.click(
    screen.getByRole("button", { name: /delete entry: prefers excel exports/i }),
  );

  expect(screen.queryByText("prefers Excel exports")).not.toBeInTheDocument();
  expect(saveMemoryEntries).not.toHaveBeenCalled();
});

// Fix round 1 (review finding Important 1): the panel is always mounted,
// so reopening it shows whatever `memoryEntries` a PRIOR session left in
// the store while this open's own `loadMemory()` is still in flight. If
// that fetch resolves (a real fetch always hands back a brand-new array
// reference, even when the content happens to be unchanged) AFTER the user
// deletes an entry from what's currently shown, the deletion must survive
// -- not be silently reverted. `loadMemory` here is a REAL two-phase stub
// (not the inert `vi.fn(async () => undefined)` every other test in this
// file uses): it stays pending until `resolveLoad` is invoked, and only
// then calls `useSettingsStore.setState(...)` with a fresh array -- the
// exact shape of a real store resolving mid-edit, per the review finding's
// own required test shape.
test("a background load resolving after a local delete does not revert the delete", async () => {
  let resolveLoad: (() => void) | undefined;
  const loadMemory = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveLoad = () => {
          act(() => {
            useSettingsStore.setState({ memoryEntries: [{ ...sampleEntry }, { ...secondEntry }] });
          });
          resolve();
        };
      }),
  );
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      loadMemory,
      memoryVersion: 1,
      memoryEntries: [sampleEntry, secondEntry],
      memoryCreatedBy: "user",
      memoryCreatedAt: "2026-08-01T00:00:00Z",
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);
  // The load kicked off on open is still pending -- delete an entry from
  // the (currently stale-but-displayed) list before it resolves.
  await userEvent.click(
    screen.getByRole("button", { name: /delete entry: prefers excel exports/i }),
  );
  expect(screen.queryByText("prefers Excel exports")).not.toBeInTheDocument();

  // Now let the in-flight load resolve. Before the fix, this would clobber
  // `localEntries` from the freshly-fetched `memoryEntries` and silently
  // bring the deleted entry back.
  act(() => {
    resolveLoad?.();
  });

  expect(screen.queryByText("prefers Excel exports")).not.toBeInTheDocument();
  expect(screen.getByText("second entry")).toBeInTheDocument();
});

// Guards against over-correcting the fix above: with NO pending local
// edit, a background load resolving must still update what's displayed --
// the dirty guard must be scoped to "there is an unsaved edit in flight,"
// never a blanket "ignore every load."
test("a background load resolving with no pending edit still updates the displayed entries", async () => {
  let resolveLoad: (() => void) | undefined;
  const loadMemory = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveLoad = () => {
          act(() => {
            useSettingsStore.setState({ memoryEntries: [secondEntry] });
          });
          resolve();
        };
      }),
  );
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      loadMemory,
      memoryVersion: 1,
      memoryEntries: [sampleEntry],
      memoryCreatedBy: "user",
      memoryCreatedAt: "2026-08-01T00:00:00Z",
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);
  expect(screen.getByText("prefers Excel exports")).toBeInTheDocument();

  act(() => {
    resolveLoad?.();
  });

  expect(screen.queryByText("prefers Excel exports")).not.toBeInTheDocument();
  expect(screen.getByText("second entry")).toBeInTheDocument();
});

test("Save memory commits the whole edited local list via saveMemoryEntries, not a delete-one call", async () => {
  const saveMemoryEntries = vi.fn(async () => undefined);
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      memoryVersion: 1,
      memoryEntries: [sampleEntry, secondEntry],
      memoryCreatedBy: "user",
      memoryCreatedAt: "2026-08-01T00:00:00Z",
      saveMemoryEntries,
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);
  await userEvent.click(
    screen.getByRole("button", { name: /delete entry: prefers excel exports/i }),
  );
  await userEvent.click(screen.getByRole("button", { name: /save memory/i }));

  expect(saveMemoryEntries).toHaveBeenCalledWith([secondEntry]);
});

test("the character-budget meter reads the fetched memory_max_chars, never a hardcoded value", () => {
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      memoryMaxChars: 4321,
      memoryVersion: 1,
      memoryEntries: [sampleEntry],
      memoryCreatedBy: "user",
      memoryCreatedAt: "2026-08-01T00:00:00Z",
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(screen.getByText(/4321/)).toBeInTheDocument();
  expect(screen.queryByText(/8000/)).not.toBeInTheDocument();
});

test("renders the version list with a Restore button on every non-current version, none on the current one", () => {
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      memoryVersion: 2,
      versions: [
        { version: 2, created_by: "user", created_at: "2026-08-02T00:00:00Z", entry_count: 1 },
        { version: 1, created_by: "distiller", created_at: "2026-08-01T00:00:00Z", entry_count: 2 },
      ],
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(screen.getAllByRole("button", { name: /restore/i })).toHaveLength(1);
});

test("clicking Restore calls restoreVersion with that version's number", async () => {
  const restoreVersion = vi.fn(async () => undefined);
  act(() => {
    seedNoopLoaders();
    useSettingsStore.setState({
      memoryVersion: 2,
      versions: [
        { version: 2, created_by: "user", created_at: "2026-08-02T00:00:00Z", entry_count: 1 },
        { version: 1, created_by: "distiller", created_at: "2026-08-01T00:00:00Z", entry_count: 2 },
      ],
      restoreVersion,
    });
  });

  render(<SettingsPanel open={true} onClose={() => undefined} />);
  await userEvent.click(screen.getByRole("button", { name: /restore/i }));

  expect(restoreVersion).toHaveBeenCalledWith(1);
});

test("pressing Escape calls onClose", () => {
  act(seedNoopLoaders);
  const onClose = vi.fn();

  render(<SettingsPanel open={true} onClose={onClose} />);
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });

  expect(onClose).toHaveBeenCalledTimes(1);
});

test("focus moves into the dialog when it opens", () => {
  act(seedNoopLoaders);

  render(<SettingsPanel open={true} onClose={() => undefined} />);

  expect(screen.getByRole("dialog")).toHaveFocus();
});
