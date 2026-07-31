import { useIdentitySession } from "./identityContext";

/**
 * The Sidebar's user-menu slot (doc 01 section 3's ASCII layout -- this
 * task is the first to put anything in it; the full settings surface doc
 * 01 section 9 describes is a later phase). Renders nothing outside an
 * `AuthGate` (see `identityContext`'s own docstring) and shows a working
 * logout control only in `auth0` mode -- `disabled`/`spcs_ingress`
 * identities have no client-side session actually worth ending (the
 * fixed dev default; the platform ingress edge's own session).
 */
export function UserMenu() {
  const session = useIdentitySession();
  if (!session) return null;
  const { identity, logout } = session;
  return (
    <div className="user-menu">
      <span className="user-menu-name">{identity.name ?? identity.sub}</span>
      {identity.identity_mode === "auth0" ? (
        <button type="button" className="user-menu-logout" onClick={logout}>
          Log out
        </button>
      ) : null}
    </div>
  );
}
