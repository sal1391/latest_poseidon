"""Phase 9 Task 3 (doc 05 section 2, decision D22): SpcsIngressProvider --
the third and final IdentityProvider this phase ships, completing
resolve_provider's mode ladder (core/identity.py) alongside DisabledProvider
(Task 1) and Auth0Provider (Task 2).

**The trust boundary this provider exists to enforce.** SPCS's own public
ingress authenticates the visitor as a Snowflake user AT THE PLATFORM EDGE
and forwards that identity to the application in a plain HTTP header,
``Sf-Context-Current-User`` -- no signature, no token, nothing this
provider can independently verify. The header is trustworthy ONLY because
SPCS's ingress is the one component that can ever attach it to a request
reaching this app; outside that specific deploy target (a developer's
laptop, an EC2 box, anywhere else ``Settings.deploy_mode`` is not exactly
``"spcs"``), any caller could set that header to any value on any request,
and this provider would otherwise have no way to tell the difference. That
is the entire reason this provider raises ``RuntimeError`` from its OWN
constructor -- not merely lazily inside ``resolve()`` -- whenever
``settings.deploy_mode != "spcs"`` (see :class:`SpcsIngressProvider`'s own
docstring): the failure has to happen at BOOT (``resolve_provider`` calls
this constructor once, from ``api/app.py``'s ``create_app`` -- see that
function's own "no half-configured server ever accepts traffic"
discipline, ``core/config.py``'s own module docstring), never quietly
deferred to whichever request happens to be first.

**No FastAPI import, same as every other provider** (``core/identity.py``'s
own "providers never import FastAPI" seam): :meth:`SpcsIngressProvider.
resolve` takes the same lowercase-keyed ``Mapping[str, str]`` every
provider takes, and raises the same plain :class:`~poseidon.core.identity.
AuthError` -- the api layer (``api/auth.py``) is the only place either
becomes an HTTP status code or a response body.

**Header -> sub: casefold + sanitize, reusing Task 1's own rule.** The raw
header value is casefolded then checked against the exact same
``[a-z0-9_-]{1,64}`` character class ``DisabledProvider``'s own ``X-Dev-
User`` act-as header already established (Global Constraints: "sanitize
same rule as act-as"), via :func:`~poseidon.core.identity.
sanitize_username` -- the ONE shared implementation both providers call
(extracted from Task 1's own inlined check specifically so this task would
not duplicate it; see that function's own docstring, and this task's
report, for the extraction). A sanitized value ``X`` becomes ``sub =
f"sf|{X}"``, the provider prefix doc 05 section 2 pins for this mode.

**Missing and malformed both mean "no usable identity" -- this task's own
disclosed resolution of the brief's stated ambiguity.** Global Constraints
pins only "header absent in spcs mode -> 401"; it says nothing about a
header that is PRESENT but fails sanitization (whitespace, punctuation, an
empty string, longer than 64 characters, ...). Unlike ``DisabledProvider``
-- whose whole design point is "never block local dev", so an invalid
act-as value silently falls back to a fixed default -- there is no default
identity to fall back to here: this is the mode a real deployment runs
under, and a header the trusted edge is supposed to guarantee is
well-formed but is not is itself evidence something is wrong (a
misconfigured ingress, a proxy stripping/mangling the header, ...), not a
signal to quietly proceed as anyone in particular. Both cases therefore
raise the exact SAME :class:`~poseidon.core.identity.AuthError` (401,
``"missing spcs identity header"``) -- never a silent fallback, and never
a SEPARATE "malformed" title distinct from "missing" the way ``Auth0
Provider`` distinguishes its own two buckets (that distinction exists
there because Global Constraints explicitly pins BOTH Auth0 buckets
separately; nothing here pins a second SPCS bucket, and collapsing them is
the more honest shape for a header this provider either fully trusts or
does not use at all).

**Roles: a config allowlist, not a Snowflake-role lookup.** Doc 05 section
2 leaves the role-mapping mechanism an explicit "config choice, recorded
per environment": this task picks the allowlist half of that choice (not
a live Snowflake-role lookup at login) -- ``Settings.spcs_sales_users``, a
comma list compared against the SAME sanitized, casefolded username this
provider already computed for the sub (config entries are casefolded too,
so an operator's casing in ``SPCS_SALES_USERS`` never has to match the
header's actual casing byte-for-byte). ``"*"`` means everyone the platform
edge vouches for gets ``Poseidon:Sales``; the empty default means no one
does until an operator configures otherwise (``core/config.py``'s own
field comment) -- both are legitimate environments, never a boot error. A
non-member still resolves successfully -- an authenticated ``UserContext``
with ``roles=()`` -- exactly like ``Auth0Provider``'s own role-less token
(identity and authorization are different questions); ``api/auth.py``'s
``require_sales`` is the ONE place a missing role becomes a 403 (Global
Constraints: "do not build a second role check in the provider").

**email/name: not fabricated.** ``Sf-Context-Current-User`` carries a bare
username and nothing else -- no separate display-name or email claim
exists to read. Judgment call (disclosed in this task's report): both stay
``None`` rather than reusing the username as a synthetic display name the
way ``DisabledProvider`` invents one for its own LOCAL, synthetic act-as
identities (that provider's own docstring calls this out as a dev-only
convenience) -- a production identity path should not assert a display
name or email it cannot actually vouch for.
"""

from collections.abc import Mapping

from poseidon.core.config import Settings
from poseidon.core.identity import AuthError, UserContext, sanitize_username

# Mapping-contract key (core/identity.py's own module docstring): the
# middleware lowercases every real header name before this provider ever
# sees it, so this is the ONE key resolve() ever looks up, regardless of
# how the client or the SPCS platform actually cased the wire header.
_SF_CONTEXT_HEADER = "sf-context-current-user"

# Global Constraints: the one role every route this phase gates requires.
# core/identity*.py cannot import api/auth.py to share ITS OWN
# `_REQUIRED_ROLE` literal (the "providers never import FastAPI/api" seam
# -- see core/identity.py's module docstring), so, like DisabledProvider's
# own DISABLED_DEFAULT_USER literal, this is a second, deliberate copy of
# the same string rather than a cross-layer import.
_SALES_ROLE = "Poseidon:Sales"

# Global Constraints: "* = everyone gets Poseidon:Sales".
_ALLOW_ALL = "*"

_MISSING_HEADER_TITLE = "missing spcs identity header"
_MISSING_HEADER_DETAIL = "no valid Sf-Context-Current-User header"


class SpcsIngressProvider:
    """``identity_mode="spcs_ingress"`` -- see the module docstring for
    the full trust-boundary, sanitize-reuse, allowlist, and email/name
    contract."""

    def __init__(self, settings: Settings) -> None:
        """Raises ``RuntimeError`` immediately -- never merely inside
        ``resolve()`` -- when ``settings.deploy_mode != "spcs"``: see the
        module docstring's "trust boundary" section for why this specific
        header must never be trusted anywhere else, and why that failure
        belongs at construction (``resolve_provider`` calls this once, at
        BOOT) rather than deferred to whichever request happens to be
        first.
        """
        if settings.deploy_mode != "spcs":
            raise RuntimeError(
                "identity_mode=spcs_ingress requires deploy_mode='spcs', got "
                f"{settings.deploy_mode!r}; Sf-Context-Current-User is trustworthy "
                "only behind the Snowflake platform ingress edge and must never be "
                "trusted outside it"
            )
        # Casefolded once, at construction, so every resolve() call
        # compares against an already-normalized set rather than
        # re-folding the same config values on every request.
        self._allowlist = frozenset(name.casefold() for name in settings.spcs_sales_users)

    def resolve(self, headers: Mapping[str, str]) -> UserContext:
        """Resolve one request's identity from its ``Sf-Context-Current-
        User`` header. See the module docstring for the full pinned
        failure/allowlist matrix.
        """
        raw = headers.get(_SF_CONTEXT_HEADER)
        candidate = sanitize_username(raw) if raw is not None else None
        if candidate is None:
            # Absent AND malformed both land here -- see the module
            # docstring's own disclosed resolution for why there is no
            # second, distinct "malformed" bucket the way Auth0Provider
            # has one.
            raise AuthError(401, _MISSING_HEADER_TITLE, _MISSING_HEADER_DETAIL)
        roles = (_SALES_ROLE,) if self._is_allowed(candidate) else ()
        return UserContext(sub=f"sf|{candidate}", email=None, name=None, roles=roles)

    def _is_allowed(self, username: str) -> bool:
        return _ALLOW_ALL in self._allowlist or username in self._allowlist


__all__ = ["SpcsIngressProvider"]
