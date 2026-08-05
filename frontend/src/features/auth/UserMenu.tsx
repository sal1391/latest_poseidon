import { useRef, useState } from "react";
import { SettingsPanel } from "../settings/SettingsPanel";
import { useIdentitySession } from "./identityContext";

/**
 * The Sidebar's user-menu slot (doc 01 section 3's ASCII layout). Renders
 * nothing outside an `AuthGate` (see `identityContext`'s own docstring) and
 * shows a working logout control only in `auth0` mode -- `disabled`/
 * `spcs_ingress` identities have no client-side session actually worth
 * ending (the fixed dev default; the platform ingress edge's own session).
 *
 * Phase 13 Task 5 adds the "Settings" trigger doc 01 section 9 calls for --
 * `SettingsPanel` is its own component; this file's only job is opening it
 * and returning focus to this trigger on close (`closeSettings` below,
 * mirroring `SkillsPicker.tsx`'s own `closeAndReturnFocus`). Reuses
 * `.user-menu-logout`'s existing styling verbatim for the new button (no
 * new CSS -- this task does no styling pass, the same "reuse an existing
 * row class" precedent `Sidebar.tsx`'s own `load-more` control already
 * set).
 */
export function UserMenu() {
  const session = useIdentitySession();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const settingsTriggerRef = useRef<HTMLButtonElement>(null);

  function closeSettings() {
    setSettingsOpen(false);
    settingsTriggerRef.current?.focus();
  }

  if (!session) return null;
  const { identity, logout } = session;
  return (
    <div className="user-menu">
      <span className="user-menu-name">{identity.name ?? identity.sub}</span>
      <button
        ref={settingsTriggerRef}
        type="button"
        className="user-menu-logout user-menu-settings"
        onClick={() => setSettingsOpen(true)}
      >
        Settings
      </button>
      {identity.identity_mode === "auth0" ? (
        <button type="button" className="user-menu-logout" onClick={logout}>
          Log out
        </button>
      ) : null}
      <SettingsPanel open={settingsOpen} onClose={closeSettings} />
    </div>
  );
}
