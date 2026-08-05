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

**JWKS: fetched via httpx, cached in-process, and bounded on BOTH sides --
a negative kid cache and a minimum interval between fetches.**
``httpx.Client`` (a SYNC client -- :meth:`resolve` is a sync method,
matching the ``IdentityProvider`` protocol every mode implements) takes an
INJECTABLE ``transport`` so tests serve a local, in-test JWKS fixture with
zero real network calls (Global Constraints: "ZERO live/network calls"),
and an injectable ``clock`` so the two lifetimes below are provable
without a real sleep. The positive cache is a plain ``dict[kid, jwk]``; a
``kid`` already in it costs nothing at all.

Everything else here is shaped by one fact: :meth:`_public_key_for_kid` is
reachable PRE-AUTH. Any uncredentialed caller can name any ``kid`` in a
token header and get this far, before a single signature is ever verified.
This provider originally answered every such request with its own fresh
outbound fetch and did NO negative caching whatsoever -- deliberately, so
that a key the tenant rotated in moments after a failed lookup healed on
the very next attempt rather than being blocked by this provider's memory
of the miss. Phase 9's own final review (I-5) recorded why that trade does
not survive contact with the public internet, and it is SUPERSEDED here: a
flood of distinct bogus kids was a free amplifier aimed at the tenant's
JWKS endpoint, and -- the fetch being blocking I/O on the one event loop
thread at the time -- a pre-auth self-DoS of every route this process
serves. Two bounds now sit in front of every fetch, and either one alone
still leaves the hole open:

- **A bounded negative kid cache** (``_negative_kids``: kid -> the clock
  reading at the failed post-fetch lookup; ``_NEGATIVE_TTL_SECONDS``). A
  kid that a REAL fetch already confirmed absent is refused for the TTL
  without fetching at all. Capped at ``_NEGATIVE_MAX_ENTRIES``, evicting
  oldest-first (plain dict insertion order is exactly that order), because
  the map is attacker-writable and an unbounded one is merely a slower
  denial of service. Only a kid a real fetch confirmed absent is ever
  recorded -- never one whose fetch was suppressed by the interval guard,
  since that is a kid this provider never actually looked up.
- **A minimum fetch interval** (``_last_fetch_at``,
  ``_MIN_FETCH_INTERVAL_SECONDS``): at most one outbound JWKS request per
  interval, whatever kid asks for it. The negative cache alone stops a
  repeat of ONE bogus kid; only this stops a flood of a million DISTINCT
  ones. The slot is claimed under the lock BEFORE the request goes out,
  and claimed on the ATTEMPT rather than on success, so a JWKS endpoint
  that is timing out or 5xx-ing is not re-hammered by every arriving
  request. Disclosed cost of claiming on the attempt: one transient
  tenant-side failure delays recovery by up to the interval -- strictly
  cheaper than every request in that window burning a worker thread on
  its own doomed fetch.

Rotation still heals, in bounded time rather than instantly: a negative
entry expires with its TTL, and any successful fetch that some OTHER kid
triggers repopulates the positive map, which is checked first -- a kid
found there is served immediately and dropped from the negative cache on
the spot. A single resolution attempt still never fetches twice: if the
freshly-fetched JWKS does not carry the kid, resolution fails (401) rather
than looping against the tenant within the same call.

**Every read and write of those three fields holds ``self._lock``.**
``api/app.py``'s identity middleware calls :meth:`resolve` through
``anyio.to_thread.run_sync`` (the other half of this same fix -- a sync
provider must never block the one event loop thread), so concurrent
requests genuinely execute this code on different worker threads at the
same time, and an unsynchronized read of the interval guard would let them
stampede the tenant exactly as before. The lock is deliberately NOT held
across the HTTP request itself: the thread that wins the interval guard
claims the slot, releases the lock, fetches, and re-acquires only to swap
the new map in.

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
import time
from collections.abc import Callable, Mapping
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
# M-5 / T2-M2 (phase 9 final review): compared casefolded against the
# request's own scheme token in _extract_bearer_token -- see that method's
# own comment for RFC 9110 section 11.1's case-insensitivity rule.
_BEARER_SCHEME = "bearer"
# One shared title+detail for every "the credential itself is not usable"
# case EXCEPT "missing entirely" (its own, more specific pinned case, per
# Global Constraints' "missing/malformed header" two-bucket split): no
# Bearer scheme, an empty token after it, or a token that is not even
# structurally a JWT all land here identically -- the caller-actionable
# answer ("get a new, well-formed token") is the same regardless of which
# of the three actually happened.
_MALFORMED_DETAIL = "Authorization header must be 'Bearer <token>' naming a well-formed JWT"

# Phase 14 Task 1 -- see the module docstring's JWKS paragraph for the full
# contract these three tune. Deliberately module constants, NOT Settings
# fields: they are a safety bound on this provider's own outbound behavior,
# not a per-environment choice an operator has any reason to make, and
# every value an operator could plausibly set would only ever weaken it.
_NEGATIVE_TTL_SECONDS = 300.0
_NEGATIVE_MAX_ENTRIES = 1024
_MIN_FETCH_INTERVAL_SECONDS = 60.0


class Auth0Provider:
    """``identity_mode="auth0"`` -- see the module docstring for the full
    JWKS-caching and claims-extraction contract."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
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
        self._clock = clock
        self._keys_by_kid: dict[str, Any] = {}
        self._negative_kids: dict[str, float] = {}
        self._last_fetch_at: float | None = None
        self._lock = threading.Lock()

    def resolve(self, headers: Mapping[str, str]) -> UserContext:
        """Resolve one request's identity from its ``Authorization: Bearer
        <jwt>`` header. See the module docstring for the full pinned
        failure matrix; every PINNED failure case raises :class:`AuthError`.
        A transport/provider fault this matrix does not pin (the JWKS
        endpoint unreachable or 5xx, a non-RSA key colliding with a
        requested ``kid`` -- phase 9 final review, M-10) can still escape
        this method as a plain ``httpx``/``jwt`` exception; ``api/app.py``'s
        identity middleware is the layer that contains ANY such exception
        (I-1) rather than this method itself, so it never reaches FastAPI's
        own generic handler as a bare 500.
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
        # I-1 (phase 9 final review): PyJWT does not require a `sub` claim
        # to exist -- claims['sub'] used to raise a bare KeyError for a
        # validly-signed token that simply omits it, escaping as an opaque
        # 500 instead of the pinned AuthError every other failure here
        # raises. `sub` is the ONE claim this codebase treats as required
        # (see UserContext's own docstring: every mode's sub is globally
        # load-bearing), so its absence is itself an invalid token, not a
        # transport/provider fault -- fixed here, at the root cause, rather
        # than left to I-1's generic middleware containment.
        sub = claims.get("sub")
        if not sub:
            raise AuthError(401, "invalid token", "token has no sub claim")
        roles = tuple(claims.get(ROLES_CLAIM, ()))
        return UserContext(
            sub=f"auth0|{sub}",
            email=claims.get("email"),
            name=claims.get("name"),
            roles=roles,
        )

    def _extract_bearer_token(self, headers: Mapping[str, str]) -> str:
        raw = headers.get(_AUTHORIZATION_HEADER)
        if raw is None:
            raise AuthError(401, "missing bearer token", "no Authorization header")
        scheme, _, rest = raw.partition(" ")
        # M-5 / T2-M2 (phase 9 final review, RFC 9110 section 11.1): the
        # auth-scheme token is case-insensitive -- "bearer"/"Bearer"/
        # "BEARER" all name the identical scheme. Only the SCHEME name is
        # casefolded; the credential itself (`rest`) never is.
        if scheme.casefold() != _BEARER_SCHEME:
            raise AuthError(401, "malformed authorization header", _MALFORMED_DETAIL)
        token = rest.strip()
        if not token:
            raise AuthError(401, "malformed authorization header", _MALFORMED_DETAIL)
        return token

    def _public_key_for_kid(self, kid: str) -> Any:
        """The pre-auth-reachable path -- see the module docstring's JWKS
        paragraph for the two bounds in front of the fetch and why they
        exist. At most ONE outbound fetch happens here, and only for a
        caller that actually won the interval guard."""
        jwk, may_fetch = self._cached_jwk_or_fetch_permit(kid)
        if jwk is None and may_fetch:
            self._fetch_jwks()
            jwk = self._jwk_after_fetch(kid)
        if jwk is None:
            raise AuthError(401, "unknown signing key", f"no JWKS key found for kid={kid!r}")
        return RSAAlgorithm.from_jwk(json.dumps(jwk))

    def _cached_jwk_or_fetch_permit(self, kid: str) -> tuple[Any | None, bool]:
        """One locked decision about a ``kid`` not yet known to be good:
        the cached JWK if there is one, otherwise whether THIS caller holds
        the interval slot and may go fetch.

        Both halves are decided under a SINGLE acquisition on purpose.
        Split across two, two worker threads could each read "no cached
        key, interval free" and both fetch -- the stampede this guard
        exists to prevent. The winner claims ``_last_fetch_at`` before
        releasing the lock, so every concurrent loser sees a fresh
        interval and takes the no-fetch path.
        """
        now = self._clock()
        with self._lock:
            jwk = self._keys_by_kid.get(kid)
            if jwk is not None:
                # A fetch some OTHER kid triggered has since published this
                # one: the positive map always wins, and a stale negative
                # entry goes now rather than waiting out its TTL.
                self._negative_kids.pop(kid, None)
                return jwk, False
            missed_at = self._negative_kids.get(kid)
            if missed_at is not None and now - missed_at < _NEGATIVE_TTL_SECONDS:
                return None, False
            if (
                self._last_fetch_at is not None
                and now - self._last_fetch_at < _MIN_FETCH_INTERVAL_SECONDS
            ):
                # Suppressed -- and deliberately NOT negative-cached (see
                # the module docstring): this provider never actually
                # looked this kid up, so recording it would block a
                # possibly-legitimate key for a full TTL and would let an
                # attacker's throwaway kids evict genuine entries.
                return None, False
            self._last_fetch_at = now
            return None, True

    def _jwk_after_fetch(self, kid: str) -> Any | None:
        """The post-fetch re-check, under the lock: the freshly published
        JWK if the fetch produced one, otherwise ``None`` with the kid
        recorded as negative so the next request naming it costs nothing.
        """
        now = self._clock()
        with self._lock:
            jwk = self._keys_by_kid.get(kid)
            if jwk is not None:
                self._negative_kids.pop(kid, None)
                return jwk
            # Popped-then-reinserted rather than assigned in place: a
            # re-recorded kid (its earlier entry having aged out) must move
            # to the END of the eviction order too, since dict insertion
            # order is exactly what "oldest" means below.
            self._negative_kids.pop(kid, None)
            while len(self._negative_kids) >= _NEGATIVE_MAX_ENTRIES:
                del self._negative_kids[next(iter(self._negative_kids))]
            self._negative_kids[kid] = now
            return None

    def _fetch_jwks(self) -> None:
        """The outbound request itself. Called ONLY by a caller that
        already claimed the interval slot in
        :meth:`_cached_jwk_or_fetch_permit`; the lock is deliberately not
        held around the request, so one worker thread blocking on the
        tenant's endpoint never stalls another thread's cached-key hit.
        """
        response = self._http.get(self._jwks_url)
        response.raise_for_status()
        data = response.json()
        keys = {key["kid"]: key for key in data.get("keys", [])}
        with self._lock:
            self._keys_by_kid = keys

    def _decode(self, token: str, public_key: Any) -> dict:
        try:
            return jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=60,
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
