/**
 * Identity resolution, ported from the Python providers this app still runs
 * behind: `backend/poseidon/core/identity.py` (`DisabledProvider`) and
 * `backend/poseidon/core/identity_spcs.py` (`SpcsIngressProvider`).
 *
 * **Why byte-identical matters.** `sub` is the key every persisted row and
 * every row-level-security policy is written against. A sub this module mints
 * even one character differently from the sub Python mints for the same
 * request does not fail loudly -- the user silently loses their history and
 * RLS silently stops matching. Every value below is therefore a copy of a
 * pinned Python value, cited to the line that pins it, not a re-derivation.
 *
 * **One accepted divergence.** Python normalises with `str.casefold()`;
 * JavaScript has only `String.prototype.toLowerCase()`. They differ on some
 * non-ASCII input (`"ẞ"` casefolds to `"ss"`, which passes the character
 * class below, but lower-cases to `"ß"`, which does not). Every difference
 * found runs in that direction: TypeScript rejects a value Python would have
 * accepted, so the divergence fails CLOSED -- a request is refused, never
 * resolved to the wrong sub. Deliberately not closed with a case-folding
 * dependency.
 */

export type Identity = {
  sub: string;
  email: string | null;
  name: string | null;
  roles: string[];
};

/**
 * A credential/trust failure: the caller did not present an identity this app
 * can accept.
 *
 * Carries Python's `(status, title, detail)` triple (`identity.py:99-103`) and,
 * exactly as Python does, hands `detail` to `Error`'s message -- so
 * `err.message` here is the string `str(exc)` gives there.
 *
 * The triple is what lets the ONE place that renders this (`proxy.ts`) emit the
 * same RFC-7807 body every other failure in this codebase renders through
 * `problem()` (`core/skills/result.py:72-79`, reached via `api/auth.py:179`),
 * instead of hard-coding a status and a title of its own. A second, hand-rolled
 * problem shape is precisely what `auth_error_response`'s docstring exists to
 * forbid on the Python side.
 */
export class AuthError extends Error {
  readonly status: number;
  readonly title: string;
  readonly detail: string;

  constructor(status: number, title: string, detail: string) {
    super(detail);
    this.name = "AuthError";
    this.status = status;
    this.title = title;
    this.detail = detail;
  }
}

/**
 * A CONFIGURATION fault: the operator's settings are wrong, and no credential
 * the caller could present would change the outcome.
 *
 * Deliberately NOT a subclass of `AuthError`, because Python keeps
 * exactly this split -- `RuntimeError` for a mode that is misconfigured or
 * unimplemented (`identity.py:301-303`, `identity_spcs.py:131-137`), and
 * `AuthError` only for a caller's credential (`api/auth.py:21`). Collapsing the
 * two renders an operator's mistake as a 401, which tells the user to log in
 * again for something only an operator can fix and buries the real fault in a
 * wall of authentication failures. `proxy.ts` renders this as a 500 instead.
 *
 * Python's equivalents fire at BOOT (`resolve_provider` builds the provider
 * once), so a misconfigured server there never accepts traffic at all. This
 * port has no boot-time seam yet, so it fails per request. Both fail closed and
 * neither ever trusts an untrustworthy header; the Python one is louder. If a
 * later task adds boot-time config validation, this class is what it should
 * raise there.
 */
export class IdentityConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IdentityConfigError";
  }
}

/**
 * The one role every route this app gates requires. Python carries two
 * deliberate copies of this literal (`identity.py`'s `DISABLED_DEFAULT_USER`
 * and `identity_spcs.py`'s `_SALES_ROLE`) because its providers may not import
 * the api layer; this port needs only one.
 */
const SALES_ROLE = "Poseidon:Sales";

/**
 * Mirrors `DISABLED_DEFAULT_USER` (identity.py:130) field for field.
 *
 * A function, not a shared constant: Python's `UserContext` is a frozen
 * dataclass holding a `tuple`, so no caller can mutate one resolved identity
 * into another. TypeScript has no cheap equivalent, and handing every caller
 * the same object -- and, worse, the same `roles` array -- means one stray
 * `identity.roles.push(...)` grants that role to every subsequent request for
 * the life of the process. Minting a fresh object per call is the honest port.
 */
function disabledDefaultUser(): Identity {
  return { sub: "dev|local", email: "dev@local", name: "Dev User", roles: [SALES_ROLE] };
}

/**
 * Mirrors `DisabledProvider.resolve`'s act-as branch (identity.py:214-219).
 *
 * `email`/`name` are DERIVED PER USER (`"{X}@local"` / `X`), not inherited
 * from the fixed default above. That is Python's actual behaviour, and its
 * reason (that provider's own docstring): reusing `"dev@local"`/`"Dev User"`
 * for every act-as identity would make every distinct `dev|X` sub render as
 * the same person everywhere a UI shows a name. `roles` stays the fixed sales
 * role -- disabled mode has no role system of its own to differentiate with.
 */
function disabledActAsUser(username: string): Identity {
  return {
    sub: `dev|${username}`,
    email: `${username}@local`,
    name: username,
    roles: [SALES_ROLE],
  };
}

/** Act-as header, lowercase -- see `sanitizeUsername` on header casing. */
const ACT_AS_HEADER = "x-dev-user";

/**
 * The header the SPCS platform ingress edge attaches after authenticating the
 * visitor as a Snowflake user (`identity_spcs.py`'s `_SF_CONTEXT_HEADER`).
 */
const SF_CONTEXT_HEADER = "sf-context-current-user";

/**
 * The title and detail of the one 401 this module raises, character for
 * character from `identity_spcs.py:113-114` (`_MISSING_HEADER_TITLE` /
 * `_MISSING_HEADER_DETAIL`). Both halves reach the wire through the RFC-7807
 * body, so both are part of the same parity contract as the subs themselves.
 */
const MISSING_HEADER_TITLE = "missing spcs identity header";
const MISSING_HEADER_DETAIL = "no valid Sf-Context-Current-User header";

/** `identity_spcs.py`'s `_ALLOW_ALL`: "everyone the edge vouches for". */
const ALLOW_ALL = "*";

/**
 * Port of `sanitize_username` (identity.py:155) -- the ONE rule this codebase
 * applies to any operator- or platform-supplied username-shaped header, shared
 * by both providers there and so by both modes here.
 *
 * Python: `_ACT_AS_PATTERN = re.compile(r"[a-z0-9_-]{1,64}")` applied with
 * `.fullmatch()` to the casefolded value. Two things that character class
 * excludes are easy to add back by accident: there is NO dot (so `"first.last"`
 * is not a username), and there is a 1-64 length cap. `fullmatch` -- anchored
 * here as `^...$` -- is why `"alice!"` is rejected wholesale rather than
 * truncated to its matching `"alice"` prefix, which would silently resolve two
 * different callers to one sub.
 *
 * Header NAMES need no normalising: `Headers.get` is case-insensitive, the
 * same guarantee Python gets from `api/app.py` lowercasing every header name
 * before a provider sees it.
 */
const SAFE_NAME = /^[a-z0-9_-]{1,64}$/;

/**
 * Returns the sanitised username, or null when it does not match. Exported
 * for the same reason Python names it in its own `__all__`: it is the one
 * shared rule, and the next provider to read a username-shaped header should
 * call it rather than re-derive the character class.
 */
export function sanitizeUsername(raw: string): string | null {
  const candidate = raw.toLowerCase();
  return SAFE_NAME.test(candidate) ? candidate : null;
}

/**
 * Reads the sales allowlist the way `Settings.spcs_sales_users` is built and
 * then consumed: `config.py`'s `split_spcs_sales_users` splits on commas and
 * strips each entry (dropping empties), and `SpcsIngressProvider.__init__`
 * casefolds the result -- so neither an operator's spacing nor their casing in
 * `SPCS_SALES_USERS` decides who gets the role. Unset means an EMPTY list, the
 * fail-closed default `config.py` picks on purpose: nobody holds the sales
 * role until an operator names someone (or `"*"`).
 */
function salesAllowlist(): Set<string> {
  return new Set(
    (process.env.SPCS_SALES_USERS ?? "")
      .split(",")
      .map((name) => name.trim().toLowerCase())
      .filter(Boolean),
  );
}

export function resolveIdentity(
  mode: "disabled" | "spcs_ingress" | "auth0",
  deployMode: string,
  headers: Headers,
): Identity {
  if (mode === "disabled") {
    const raw = headers.get(ACT_AS_HEADER);
    const username = raw === null ? null : sanitizeUsername(raw);
    if (username === null) {
      // An absent OR invalid act-as header is IGNORED, never rejected. The
      // whole point of this mode is that it never refuses a request, so the
      // fixed default answers for both -- identically (identity.py:211-213).
      return disabledDefaultUser();
    }
    return disabledActAsUser(username);
  }

  if (mode === "spcs_ingress") {
    // The header below is unsigned and unverifiable. It is trustworthy ONLY
    // because the SPCS platform edge is the one component that can attach it
    // to a request reaching this app. Anywhere else -- a laptop, an EC2 box --
    // any caller could set it to any value and this code could not tell the
    // difference. Python enforces this in `SpcsIngressProvider.__init__`
    // (identity_spcs.py:131-137), so it fails at BOOT; see
    // `IdentityConfigError` for why this is that class and not an `AuthError`,
    // and for what this port gives up by failing per request instead.
    if (deployMode !== "spcs") {
      throw new IdentityConfigError(
        "spcs identity header is not trusted outside spcs deploy mode",
      );
    }
    const raw = headers.get(SF_CONTEXT_HEADER);
    const username = raw === null ? null : sanitizeUsername(raw);
    // A present-but-malformed header raises the SAME error as an absent one:
    // both mean the trusted edge did not deliver what it guarantees, and there
    // is no default identity to fall back to in the mode a real deployment
    // runs under. Python has no separate "malformed" bucket here either
    // (identity_spcs.py's `resolve`).
    if (username === null) {
      throw new AuthError(401, MISSING_HEADER_TITLE, MISSING_HEADER_DETAIL);
    }
    // Roles are ALLOWLIST-GATED, not granted to everyone the platform
    // authenticates -- identity_spcs.py:156,
    // `roles = (_SALES_ROLE,) if self._is_allowed(candidate) else ()`.
    // A user the platform authenticated but who is NOT on the list resolves
    // successfully with an EMPTY role list: authenticated, but not authorised.
    // `require_sales` on the backend is the one place that becomes a 403.
    const allowlist = salesAllowlist();
    const allowed = allowlist.has(ALLOW_ALL) || allowlist.has(username);
    // email/name stay null rather than reusing the username: the header
    // carries a bare username and nothing else, and a production identity path
    // should not assert a display name or email it cannot vouch for. (This is
    // exactly why disabled mode's synthetic dev identities, above, may.)
    return {
      sub: `sf|${username}`,
      email: null,
      name: null,
      roles: allowed ? [SALES_ROLE] : [],
    };
  }

  if (mode === "auth0") {
    // A config fault, not a credential one: Python HAS an auth0 provider, so
    // the only way to reach this in the port is an operator selecting a mode
    // this phase has not built. That is the same fault as the unimplemented-mode
    // tail below (identity.py:301-303's `RuntimeError`), and a caller's
    // credential is irrelevant to it.
    throw new IdentityConfigError("auth0 mode is not wired in this phase (decision M5)");
  }

  // Unreachable through the declared type, but `IDENTITY_MODE` is an
  // unvalidated env string that `proxy.ts` casts into it, so a typo really can
  // arrive here. Naming the offending value beats letting it fall into the
  // auth0 branch and reporting a mode the operator never configured -- the
  // same defensive tail `resolve_provider` keeps in identity.py:301.
  throw new IdentityConfigError(
    `identity_mode=${JSON.stringify(mode)} has no resolver implemented`,
  );
}
