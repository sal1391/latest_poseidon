import { createContext, useContext } from "react";
import type { Identity } from "../../api/client";

export interface IdentitySession {
  identity: Identity;
  /** A no-op placeholder outside auth0 mode -- `disabled`/`spcs_ingress`
   * have no client-side session actually worth ending (the fixed dev
   * user; the platform ingress edge's own session). Only `AuthGate`'s
   * auth0 branch (`Auth0Boundary`) ever wires this to the SDK's real
   * `logout()`. */
  logout: () => void;
}

/**
 * `null` outside an `AuthGate` -- every consumer (`UserMenu`) treats that
 * as "nothing to show" rather than throwing, so a component that renders
 * this context's consumer in a test (or a future screen) that never
 * wrapped it in an `AuthGate` still renders cleanly instead of crashing
 * on a missing provider. `ChatScreen.test.tsx`'s own bare
 * `render(<ChatScreen />)` -- with no `AuthGate` above it -- is exactly
 * this case today.
 */
export const IdentityContext = createContext<IdentitySession | null>(null);

export function useIdentitySession(): IdentitySession | null {
  return useContext(IdentityContext);
}
