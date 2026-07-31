"""Phase 9 Task 2 (doc 05 section 2, decision D22): Auth0Provider -- RS256
JWT verification against the tenant's own JWKS, the first
:class:`~poseidon.core.identity.IdentityProvider` that can actually raise
:class:`~poseidon.core.identity.AuthError` (see that module's own
docstring, "What this task ships vs. what it does not", for why Task 1's
``DisabledProvider`` never does).

**No FastAPI import, same as every other provider** (``core/identity.py``'s
own "providers never import FastAPI" seam): :meth:`Auth0Provider.resolve`
takes the same lowercase-keyed ``Mapping[str, str]`` every provider takes,
and raises the same plain :class:`AuthError`. The api layer
(``api/auth.py``) is the only place either becomes an HTTP status code or a
response body.

**JWKS: fetched via httpx, cached in-process, refetched ONCE on an unknown
kid.** ``httpx.Client`` (a SYNC client -- :meth:`resolve` is a sync method,
matching the ``IdentityProvider`` protocol every mode implements) takes an
INJECTABLE ``transport`` so tests serve a local, in-test JWKS fixture with
zero real network calls (Global Constraints: "ZERO live/network calls").
The cache is a plain ``dict[kid, jwk]``; a ``kid`` not yet in it (the very
first request needing ANY key, or a genuinely rotated-in key) triggers
exactly one fetch. If the freshly-fetched JWKS still does not carry that
``kid``, resolution fails (401) rather than fetching again within the SAME
call -- a caller sending a bad/garbage ``kid`` must never be able to make
this provider loop against the tenant's JWKS endpoint on a single request.
No NEGATIVE caching happens either: a later request retrying the same
once-unknown ``kid`` gets its own fresh fetch, so a key the tenant rotates
in moments after a failed lookup still heals on the very next attempt,
never permanently blocked by this provider's own memory of the earlier
miss.

**Claims:** ``sub`` is unconditionally prefixed ``auth0|`` -- the same "the
CODE constructs the prefix, unconditionally" discipline
``core/identity.py``'s own ``DisabledProvider`` already established for
``dev|...`` (see its docstring), rather than trusting that Auth0's own raw
``sub`` claim happens to already look provider-prefixed (true for the
default database connection's own ``auth0|...`` subs, NOT guaranteed for
every connection type a tenant might federate in -- ``google-oauth2|...``,
``samlp|...``, enterprise SSO, etc.). A double prefix for the one
connection type whose raw sub already starts with the literal string
``auth0|`` is a disclosed, accepted cosmetic cost of a hard, uniform
guarantee that holds regardless of tenant connection configuration this
codebase has no visibility into (see this task's own report for the full
judgment call). ``email``/``name`` are read straight off the token's own
OIDC-standard claims (nullable -- not every token scope requests them).
``roles`` comes from the tenant's own custom-claims namespace
(:data:`ROLES_CLAIM`, wfscorp's own Auth0 Action/Rule convention), empty
when absent -- a role-LESS token resolves successfully (identity and
authorization are different questions); a route that actually requires
``Poseidon:Sales`` enforces that separately (``api/auth.py``'s
``require_sales``).
"""

import json
import threading
from collections.abc import Mapping
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from poseidon.core.config import Settings
from poseidon.core.identity import AuthError, UserContext

# Global Constraints (verbatim): wfscorp's own Auth0 Action/Rule namespace
# for the roles custom claim -- tenant parity, not a Poseidon-invented name.
ROLES_CLAIM = "https://wfscorp.com/custom-claims.roles"

_AUTHORIZATION_HEADER = "authorization"
_BEARER_PREFIX = "Bearer "
# One shared title+detail for every "the credential itself is not usable"
# case EXCEPT "missing entirely" (its own, more specific pinned case, per
# Global Constraints' "missing/malformed header" two-bucket split): no
# Bearer scheme, an empty token after it, or a token that is not even
# structurally a JWT all land here identically -- the caller-actionable
# answer ("get a new, well-formed token") is the same regardless of which
# of the three actually happened.
_MALFORMED_DETAIL = "Authorization header must be 'Bearer <token>' naming a well-formed JWT"


class Auth0Provider:
    """``identity_mode="auth0"`` -- see the module docstring for the full
    JWKS-caching and claims-extraction contract."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        domain = settings.auth0_domain
        # Global Constraints (verbatim): iss = https://{AUTH0_DOMAIN}/ --
        # note the trailing slash, Auth0's own issuer convention.
        self._issuer = f"https://{domain}/"
        self._audience = settings.auth0_audience
        self._jwks_url = f"https://{domain}/.well-known/jwks.json"
        # transport=None here means "no override" to httpx itself -- a
        # real Client(transport=None) opens real connections, exactly what
        # production wants; tests pass an httpx.MockTransport (or the
        # local JwksTransport test double) instead.
        self._http = httpx.Client(transport=transport)
        self._keys_by_kid: dict[str, Any] = {}
        self._lock = threading.Lock()

    def resolve(self, headers: Mapping[str, str]) -> UserContext:
        """Resolve one request's identity from its ``Authorization: Bearer
        <jwt>`` header. See the module docstring for the full pinned
        failure matrix; every failure raises :class:`AuthError` -- never a
        bare exception FastAPI would turn into an opaque 500.
        """
        token = self._extract_bearer_token(headers)
        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.DecodeError:
            raise AuthError(401, "malformed authorization header", _MALFORMED_DETAIL) from None
        kid = unverified_header.get("kid")
        if not kid:
            raise AuthError(401, "malformed authorization header", _MALFORMED_DETAIL)
        public_key = self._public_key_for_kid(kid)
        claims = self._decode(token, public_key)
        roles = tuple(claims.get(ROLES_CLAIM, ()))
        return UserContext(
            sub=f"auth0|{claims['sub']}",
            email=claims.get("email"),
            name=claims.get("name"),
            roles=roles,
        )

    def _extract_bearer_token(self, headers: Mapping[str, str]) -> str:
        raw = headers.get(_AUTHORIZATION_HEADER)
        if raw is None:
            raise AuthError(401, "missing bearer token", "no Authorization header")
        if not raw.startswith(_BEARER_PREFIX):
            raise AuthError(401, "malformed authorization header", _MALFORMED_DETAIL)
        token = raw[len(_BEARER_PREFIX) :].strip()
        if not token:
            raise AuthError(401, "malformed authorization header", _MALFORMED_DETAIL)
        return token

    def _public_key_for_kid(self, kid: str) -> Any:
        if kid not in self._keys_by_kid:
            self._fetch_jwks()
        jwk = self._keys_by_kid.get(kid)
        if jwk is None:
            raise AuthError(401, "unknown signing key", f"no JWKS key found for kid={kid!r}")
        return RSAAlgorithm.from_jwk(json.dumps(jwk))

    def _fetch_jwks(self) -> None:
        response = self._http.get(self._jwks_url)
        response.raise_for_status()
        data = response.json()
        with self._lock:
            self._keys_by_kid = {key["kid"]: key for key in data.get("keys", [])}

    def _decode(self, token: str, public_key: Any) -> dict:
        try:
            return jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.ExpiredSignatureError:
            raise AuthError(401, "token expired", "the token's exp claim is in the past") from None
        except jwt.ImmatureSignatureError:
            raise AuthError(
                401, "token not yet valid", "the token's nbf claim is in the future"
            ) from None
        except jwt.InvalidAudienceError:
            raise AuthError(
                401, "invalid audience", f"token aud does not match {self._audience!r}"
            ) from None
        except jwt.InvalidIssuerError:
            raise AuthError(
                401, "invalid issuer", f"token iss does not match {self._issuer!r}"
            ) from None
        except jwt.InvalidSignatureError:
            raise AuthError(
                401, "invalid token signature", "token signature verification failed"
            ) from None
        except jwt.InvalidTokenError as exc:
            raise AuthError(401, "invalid token", f"token could not be verified: {exc}") from None


__all__ = ["ROLES_CLAIM", "Auth0Provider"]
