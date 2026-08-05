import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import type { Identity } from "../../api/client";
import { resetSettingsStore, useSettingsStore } from "../../state/settingsStore";
import { IdentityContext } from "./identityContext";
import { UserMenu } from "./UserMenu";

beforeEach(() => {
  resetSettingsStore();
});

test("renders nothing outside an AuthGate (no IdentityContext provider)", () => {
  const { container } = render(<UserMenu />);
  expect(container).toBeEmptyDOMElement();
});

test("shows a working logout button in auth0 mode", () => {
  const identity: Identity = {
    sub: "auth0|abc",
    name: "Alice",
    email: "alice@example.com",
    roles: ["Poseidon:Sales"],
    identity_mode: "auth0",
  };
  const logout = vi.fn();

  render(
    <IdentityContext.Provider value={{ identity, logout }}>
      <UserMenu />
    </IdentityContext.Provider>,
  );

  expect(screen.getByText("Alice")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /log out/i }));
  expect(logout).toHaveBeenCalled();
});

test("shows identity but no logout control in disabled mode", () => {
  const identity: Identity = {
    sub: "dev|local",
    name: "Dev User",
    email: "dev@local",
    roles: ["Poseidon:Sales"],
    identity_mode: "disabled",
  };

  render(
    <IdentityContext.Provider value={{ identity, logout: () => undefined }}>
      <UserMenu />
    </IdentityContext.Provider>,
  );

  expect(screen.getByText("Dev User")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /log out/i })).not.toBeInTheDocument();
});

test("falls back to sub when name is null (spcs_ingress carries no display name)", () => {
  const identity: Identity = {
    sub: "sf|alice",
    name: null,
    email: null,
    roles: ["Poseidon:Sales"],
    identity_mode: "spcs_ingress",
  };

  render(
    <IdentityContext.Provider value={{ identity, logout: () => undefined }}>
      <UserMenu />
    </IdentityContext.Provider>,
  );

  expect(screen.getByText("sf|alice")).toBeInTheDocument();
});

// Phase 13 Task 5: the settings surface's own entry point (doc 01 section
// 9). Stubs the store's load actions rather than standing up MSW here --
// this file's own established style is a pure presentational/interaction
// test with no network layer (see the three tests above), and
// `SettingsPanel.test.tsx` already covers the panel's own load-on-open wiring
// against the real store; this test's only job is proving the TRIGGER opens
// it and that opening still reaches those same actions end to end.
//
// F6 (owner decision, 2026-08-05 walkthrough) touched this test file
// incidentally, outside that fix's own sanctioned file list: removing
// `loadVersions`/`restoreVersion` from `settingsStore.ts` (Version History
// is gone from the settings UI) left this test's `loadVersions` stub/
// assertion referencing an action that no longer exists on `SettingsState`
// -- a compile error otherwise. Disclosed here and in that task's own
// report rather than silently expanded scope.
test("clicking the Settings trigger opens the settings panel and triggers its initial load", async () => {
  const loadSettings = vi.fn(async () => undefined);
  const loadMemory = vi.fn(async () => undefined);
  act(() => {
    useSettingsStore.setState({ loadSettings, loadMemory });
  });
  const identity: Identity = {
    sub: "auth0|abc",
    name: "Alice",
    email: "alice@example.com",
    roles: ["Poseidon:Sales"],
    identity_mode: "auth0",
  };

  render(
    <IdentityContext.Provider value={{ identity, logout: () => undefined }}>
      <UserMenu />
    </IdentityContext.Provider>,
  );
  expect(screen.queryByRole("dialog", { name: /settings/i })).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /^settings$/i }));

  expect(await screen.findByRole("dialog", { name: /settings/i })).toBeInTheDocument();
  expect(loadSettings).toHaveBeenCalledTimes(1);
  expect(loadMemory).toHaveBeenCalledTimes(1);
});
