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
