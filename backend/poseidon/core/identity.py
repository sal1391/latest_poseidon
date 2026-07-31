"""Phase 9 Task 1 (doc 05 section 2, decision D22): the IdentityProvider
seam every downstream module threads a real caller identity through, and
the one provider this task ships -- ``DisabledProvider``, the default mode
every existing test/env already boots under.

**Provider-blindness (D22).** Everything BELOW the seam -- ``api/live_chat.
py``, ``core/chat/orchestrator.py``, ``core/runlog.py``'s writer calls,
``core/skills/context.py``'s ``SkillContext.user`` -- reads exactly one
already-resolved :class:`UserContext` and never branches on
``settings.identity_mode`` or imports anything from this module except that
one type. The mode switch lives in exactly one place: :func:`resolve_
provider`, called once at app construction (``api/app.py``'s ``create_app``)
so a misconfigured or not-yet-implemented mode fails at BOOT, the same
"no half-configured server ever accepts traffic" discipline ``core/config.
py``'s own module docstring already states for a malformed ``Settings``
value.

**Providers never import FastAPI.** :meth:`IdentityProvider.resolve` takes
a plain ``Mapping[str, str]`` -- a header bag with LOWERCASE keys, never a
``starlette.requests.Request`` -- so this whole module (and every provider
that will ever implement this protocol: ``Auth0Provider``/
``SpcsIngressProvider``, Phase 9 Tasks 2/3) stays free of any web-framework
import. ``api/auth.py``'s identity middleware is the ONE adapter: it
lowercases a real request's headers (``{k.lower(): v for k, v in request.
headers.items()}``) and calls ``provider.resolve(headers)``, storing the
result on ``request.state.user`` for every downstream dependency/handler to
read back. A unit test exercises a provider directly with a plain dict
built the same way -- no ASGI app, no ``Request`` construction, needed (see
``test_identity_providers.py``).

**Why a mapping, not a request.** Every mode this plan ships resolves
identity from exactly one HTTP header (``X-Dev-User`` here; ``Authorization``
for Auth0; ``Sf-Context-Current-User`` for the SPCS ingress trust boundary)
-- a mapping is the smallest honest shape that need, and it is what makes a
provider testable in complete isolation from the web framework carrying it.

**What this task ships vs. what it does not.** :class:`UserContext`,
:class:`AuthError`, the :class:`IdentityProvider` protocol, :class:`
DisabledProvider` and :func:`resolve_provider` all ship now. ``AuthError``
is defined here because the PROTOCOL needs a pinned failure shape to
document, but no provider in this task ever raises it: ``DisabledProvider``
never rejects a request (an invalid ``X-Dev-User`` header is IGNORED, not
an error -- see that class's own docstring), so the AuthError-to-HTTP-
response mapping (401/403 RFC-7807 problem shapes) is deliberately left for
Phase 9 Task 2, the first task whose provider (``Auth0Provider``) can
actually raise it. Building that mapping now, against no real raiser and no
pinned problem shape yet, would be speculative code this task cannot prove
with a test.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from poseidon.core.config import Settings


@dataclass(frozen=True)
class UserContext:
    """The one identity shape every mode (``disabled``/``auth0``/
    ``spcs_ingress``) resolves down to (doc 05 section 2). ``sub`` is always
    provider-prefixed (``dev|...`` here; ``auth0|...``/``sf|...`` for the
    other two modes) so a sub is globally unambiguous about which identity
    system minted it, even after Phase 10 starts keying persisted rows off
    it. Frozen, like every other value type in this codebase -- a caller
    cannot rewrite the identity a request resolved to out from under
    whatever already read it.
    """

    sub: str
    email: str | None
    name: str | None
    roles: tuple[str, ...]


class AuthError(Exception):
    """Raised by :meth:`IdentityProvider.resolve` when a request cannot be
    resolved to a :class:`UserContext` -- an invalid/expired/tampered
    credential (Auth0), or a trust violation (SPCS ingress outside the
    trusted edge). Carries the same three fields ``core.skills.result.
    problem()`` renders into an RFC-7807 body, so the api layer can map one
    straight onto the other without this module (or any provider) importing
    FastAPI or knowing anything about HTTP response shapes -- see the
    module docstring's "providers never import FastAPI" seam.

    No provider THIS task ships (:class:`DisabledProvider`) ever raises
    this; see the module docstring's "What this task ships" for why the
    api-layer mapping is deliberately deferred to Phase 9 Task 2, the first
    task with a provider that can actually raise it.
    """

    def __init__(self, status: int, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail


class IdentityProvider(Protocol):
    """The one seam every identity mode implements. See the module
    docstring for the full provider-blindness/no-FastAPI-import contract
    this protocol exists to enforce."""

    def resolve(self, headers: Mapping[str, str]) -> UserContext:
        """Resolve one request's identity from its (lowercase-keyed)
        headers. Returns a :class:`UserContext`, or raises :class:`AuthError`
        for a request that cannot be resolved -- see that class's own
        docstring for which modes actually take the error path."""
        ...


# ---------------------------------------------------------------------------
# disabled mode (the default): fixed identity + the X-Dev-User act-as header
# ---------------------------------------------------------------------------

# The plan's own pinned fixed context (Global Constraints), generalized from
# Phase 6-8's bare DEV_USER_SUB = "dev|local" string constant into the full
# UserContext shape this seam carries everywhere now. The sub value itself
# is unchanged -- every existing row/test that pinned "dev|local" stays
# coherent (Task 1's own contract pin) -- it now arrives THROUGH
# DisabledProvider rather than as a constant orchestrator.py reached for
# directly.
DISABLED_DEFAULT_USER = UserContext(
    sub="dev|local", email="dev@local", name="Dev User", roles=("Poseidon:Sales",)
)

# wfs's own act-as convention for multi-user local testing (Global
# Constraints): a browser tab (or curl call) can pose as a named dev user
# without any real login flow existing yet. Lowercase -- the header-mapping
# contract this whole seam relies on (see the module docstring's "providers
# never import FastAPI"): api/auth.py's middleware lowercases every header
# name before calling in, so this key is always found regardless of the
# case the client actually sent ("X-Dev-User", "x-dev-user", ... all land
# here identically).
_ACT_AS_HEADER = "x-dev-user"

# Sanitize rule (Task 1's own pinned contract): casefold the raw header
# value FIRST, then require the WHOLE string to match this character class,
# 1-64 of them. Anything that does not match means "ignore the header", not
# "reject the request" -- disabled mode never 4xxs on a bad act-as value; it
# just falls back to the fixed default, since the entire point of this mode
# is "never blocks local dev, ever". fullmatch (not search/match) is what
# makes this a WHOLE-STRING contract: "alice!" must fail even though its
# "alice" prefix alone would match.
_ACT_AS_PATTERN = re.compile(r"[a-z0-9_-]{1,64}")


class DisabledProvider:
    """``identity_mode="disabled"`` -- the default, and the ONLY mode this
    task implements (Global Constraints: "every existing test/env boots
    unchanged"). Every request resolves to :data:`DISABLED_DEFAULT_USER`,
    unless the caller sends a valid ``X-Dev-User`` header, in which case a
    sanitized value ``X`` resolves to sub ``"dev|X"`` instead (see the
    module-level constants above for the exact sanitize rule).

    ``email``/``name`` for an act-as identity are NOT pinned by the plan --
    only the sub shape (``"dev|alice"``) is a contract pin. Derived here as
    ``"{X}@local"``/``X`` (a judgment call, disclosed in this task's report)
    so a UI rendering ``{email, name, roles}`` for an act-as user shows
    something obviously synthetic and DISTINCT per act-as identity, rather
    than reusing ``"dev@local"``/``"Dev User"`` verbatim for every one of
    them -- which would make every ``dev|X`` sub look like the exact same
    person everywhere except the one field a UI is least likely to surface
    prominently. ``roles`` stays the fixed ``("Poseidon:Sales",)`` for every
    act-as identity regardless: disabled mode has no role system of its own
    to differentiate against, and ``Poseidon:Sales`` is the one role every
    route this phase gates actually requires.
    """

    def resolve(self, headers: Mapping[str, str]) -> UserContext:
        raw = headers.get(_ACT_AS_HEADER)
        if raw is None:
            return DISABLED_DEFAULT_USER
        candidate = raw.casefold()
        if _ACT_AS_PATTERN.fullmatch(candidate) is None:
            # Invalid -- pinned as "ignore the header", never a rejection;
            # the fixed default answers instead, identically to no header.
            return DISABLED_DEFAULT_USER
        return UserContext(
            sub=f"dev|{candidate}",
            email=f"{candidate}@local",
            name=candidate,
            roles=("Poseidon:Sales",),
        )


def resolve_provider(settings: Settings) -> IdentityProvider:
    """Select the :class:`IdentityProvider` named by ``settings.
    identity_mode``. Called once, at app construction (``api/app.py``'s
    ``create_app``) -- never lazily, on first request -- so a misconfigured
    or not-yet-implemented mode fails at BOOT rather than serving even one
    request under the wrong identity story.

    ``"disabled"``/``"auth0"`` resolve today. ``"spcs_ingress"`` is a real
    ``Settings.identity_mode`` value (the ``Literal`` already accepts all
    three -- doc 05 section 2's full design), but its provider ships in
    Phase 9 Task 3; selecting it before then hits the exact same fail-fast
    branch a genuinely unrecognized string would (impossible to reach
    through ``Settings`` alone, whose ``Literal`` already rejects anything
    else at config-load time -- this is the defensive belt for that
    already-enforced suspenders, doubling as the honest answer for a mode
    this task has not implemented yet).
    """
    if settings.identity_mode == "disabled":
        return DisabledProvider()
    if settings.identity_mode == "auth0":
        # Imported here, not at module level: identity_auth0.py itself
        # imports AuthError/UserContext FROM this module, so a top-level
        # import here would be a genuine circular import (this module
        # would try to import identity_auth0 before its own AuthError/
        # UserContext names exist yet for identity_auth0 to import back).
        # Deferring to call time -- long after both modules are fully
        # loaded -- sidesteps that entirely. Phase 9 Task 3 will add the
        # identical one-line branch + deferred import for spcs_ingress.
        from poseidon.core.identity_auth0 import Auth0Provider

        return Auth0Provider(settings)
    raise RuntimeError(
        f"identity_mode={settings.identity_mode!r} has no IdentityProvider implemented yet"
    )


__all__ = [
    "AuthError",
    "DISABLED_DEFAULT_USER",
    "DisabledProvider",
    "IdentityProvider",
    "UserContext",
    "resolve_provider",
]
