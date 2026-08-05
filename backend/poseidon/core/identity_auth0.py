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

**Single flight (task review fix round 1, Important #1).** Those two
bounds alone had a hole the moment the middleware started resolving on
worker threads: the winner claims the interval slot BEFORE its HTTP
request runs, so every OTHER request arriving during that fetch found the
slot claimed and was refused -- including requests bearing perfectly valid
tokens for a kid the tenant does serve. Cold start, a deploy, or a key
rotation would hand the first simultaneous burst of real users a spurious
401 and a re-login blip. So a caller that finds the slot claimed now
distinguishes two cases:

- **A fetch is GENUINELY in flight right now** (``_fetch_in_flight``):
  this caller rides it -- waits off-lock on that attempt's own event,
  capped at ``_FETCH_WAIT_SECONDS``, then re-checks the positive cache
  before considering any negative outcome. One outbound fetch still serves
  every one of them.
- **The slot is claimed but nothing is in flight** (the fetch already
  finished): straight to the negative outcome, with no fetch and no wait.
  This is the bogus-kid flood -- the attacker path -- and it must stay
  free of both. An attacker's kids can never park a worker thread outside
  the brief window a real fetch is actually running.

Rotation still heals, in bounded time rather than instantly: a negative
entry expires with its TTL, and any successful fetch that some OTHER kid
triggers repopulates the positive map, which is checked first -- a kid
found there is served immediately and dropped from the negative cache on
the spot. A single resolution attempt still never fetches twice: if the
freshly-fetched JWKS does not carry the kid, resolution fails (401) rather
than looping against the tenant within the same call.

**A fetch that FAILS is reported as what it is, never as a bad kid.**
"unknown signing key" is a statement about the CALLER's credential; a
tenant-side outage is not. Whenever this provider has not actually managed
to check a kid -- a caller riding a fetch that then failed, a caller
riding one that never finished inside ``_FETCH_WAIT_SECONDS``, or any
caller arriving during the interval that follows a FAILED fetch
(``_last_fetch_error``) -- it raises :class:`JwksUnavailable` instead of
the pinned ``AuthError``. That is deliberately NOT an ``AuthError``: it
takes ``api/app.py``'s I-1 containment path, which logs the fault
server-side (the operator's one signal that a JWKS outage, not a wave of
bad tokens, is in progress -- previously the whole interval passed
silently) and answers the caller with the generic ``identity_unavailable``
401 whose detail already says "try again; if this persists, it is not your
credential". Disclosed cost: during an outage that containment logs one
line per affected request. That is exactly the volume this module produced
before any of this work (every request had its own failing fetch to log),
so it is not a new amplifier -- but it is louder than the silent interval
that shipped in the first round of this task, on purpose. A kid already in
the POSITIVE cache is unaffected by any of it: an outage never disturbs
keys this process already holds.

**Every read and write of the cache fields holds ``self._lock``.**
``api/app.py``'s identity middleware calls :meth:`resolve` through
``anyio.to_thread.run_sync`` (the other half of this same fix -- a sync
provider must never block the one event loop thread), so concurrent
requests genuinely execute this code on different worker threads at the
same time, and an unsynchronized read of the interval guard would let them
stampede the tenant exactly as before. The lock is deliberately NOT held
across the HTTP request itself -- nor across a rider's wait: the thread
that wins the interval guard claims the slot, releases the lock, fetches,
and re-acquires only to swap the new map in and clear the in-flight
marker. The outbound request itself carries an explicit, bounded
``httpx.Timeout`` on every phase (httpx's own 5s default is PER PHASE, so
a pathological endpoint could otherwise hold a worker far longer than
that), which is what makes a rider's wait cap meaningful in the first
place.

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
# Fix round 1: httpx's own default timeout is 5s PER PHASE (connect, read,
# write, pool), so a pathological endpoint can hold a worker thread far
# longer than five seconds in total. Bounded explicitly, on every phase,
# because a rider's wait cap below is only as meaningful as the fetch it
# is waiting on is bounded. Auth0's own JWKS endpoint answers in well
# under a second; three is already generous.
_JWKS_TIMEOUT_SECONDS = 3.0
# The longest a single fetch can possibly run: the four phases the timeout
# above bounds. A caller riding an in-flight fetch never parks longer than
# the fetch it is riding could itself take.
_FETCH_WAIT_SECONDS = 4 * _JWKS_TIMEOUT_SECONDS


class JwksUnavailable(RuntimeError):
    """This provider could not check the request's ``kid`` at all, because
    the tenant's JWKS endpoint could not be reached.

    Deliberately NOT an :class:`AuthError`: every ``AuthError`` this module
    raises is a statement about the CALLER's credential, and this is a
    statement about the tenant. It escapes :meth:`Auth0Provider.resolve` on
    purpose, exactly like the raw ``httpx`` fault the caller that actually
    performed the doomed fetch sees, so ``api/app.py``'s I-1 containment
    logs it once server-side and answers with the generic
    ``identity_unavailable`` 401 rather than the misleading "unknown
    signing key". See the module docstring for the full rationale.
    """


class _JwksFetchAttempt:
    """One outbound JWKS fetch, shared with every caller that arrives while
    it is still running (the single-flight seam -- see the module
    docstring). Created by the thread that claims the interval slot and
    published as ``Auth0Provider._fetch_in_flight`` under the lock, so a
    later caller can tell "a fetch is running RIGHT NOW" (ride it) from
    "the slot is claimed but the fetch already finished" (refuse at once).

    ``done`` is set exactly once, in the fetcher's own ``finally``, so no
    rider can be stranded by a fetch that raised. ``error`` carries the
    fault to those riders, since what they must report is the tenant's
    failure, never a verdict on their own kid.
    """

    __slots__ = ("done", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.error: BaseException | None = None


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
        self._http = httpx.Client(transport=transport, timeout=httpx.Timeout(_JWKS_TIMEOUT_SECONDS))
        self._clock = clock
        self._keys_by_kid: dict[str, Any] = {}
        self._negative_kids: dict[str, float] = {}
        self._last_fetch_at: float | None = None
        self._fetch_in_flight: _JwksFetchAttempt | None = None
        self._last_fetch_error: BaseException | None = None
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
        paragraph for the bounds in front of the fetch and why they exist.
        At most ONE outbound fetch happens per interval: this caller either
        performs it, rides one already in flight, or takes an immediate
        no-fetch outcome."""
        jwk, own_attempt, in_flight = self._cached_jwk_or_fetch_attempt(kid)
        if jwk is None and own_attempt is not None:
            self._run_fetch(own_attempt)
            jwk = self._jwk_after_fetch(kid)
        elif jwk is None and in_flight is not None:
            self._await_fetch(in_flight)
            jwk = self._jwk_after_fetch(kid)
        if jwk is None:
            raise AuthError(401, "unknown signing key", f"no JWKS key found for kid={kid!r}")
        return RSAAlgorithm.from_jwk(json.dumps(jwk))

    def _cached_jwk_or_fetch_attempt(
        self, kid: str
    ) -> tuple[Any | None, _JwksFetchAttempt | None, _JwksFetchAttempt | None]:
        """One locked decision about a ``kid`` not yet known to be good:
        the cached JWK if there is one, otherwise the fetch attempt this
        caller must PERFORM, otherwise the one already in flight it should
        RIDE. All three ``None`` means the immediate, no-fetch negative
        outcome; a tenant outage inside the interval raises
        :class:`JwksUnavailable` from here instead.

        Every branch is decided under a SINGLE acquisition on purpose.
        Split across two, two worker threads could each read "no cached
        key, interval free" and both fetch -- the stampede this guard
        exists to prevent. The winner publishes its attempt AND claims
        ``_last_fetch_at`` before releasing the lock, so every concurrent
        caller either rides that exact attempt or takes a no-fetch path.
        """
        now = self._clock()
        with self._lock:
            jwk = self._keys_by_kid.get(kid)
            if jwk is not None:
                # A fetch some OTHER kid triggered has since published this
                # one: the positive map always wins, and a stale negative
                # entry goes now rather than waiting out its TTL.
                self._negative_kids.pop(kid, None)
                return jwk, None, None
            missed_at = self._negative_kids.get(kid)
            if missed_at is not None and now - missed_at < _NEGATIVE_TTL_SECONDS:
                # Checked BEFORE the in-flight branch below: a real fetch
                # already confirmed this kid absent, so there is nothing to
                # wait for -- and a repeated bogus kid must never be able
                # to park a worker thread, however long a fetch is running.
                return None, None, None
            if (
                self._last_fetch_at is not None
                and now - self._last_fetch_at < _MIN_FETCH_INTERVAL_SECONDS
            ):
                if self._fetch_in_flight is not None:
                    # Single flight (fix round 1): a fetch is running RIGHT
                    # NOW and may well be about to publish this very kid --
                    # refusing here is what handed a cold-start burst of
                    # VALID tokens a spurious 401.
                    return None, None, self._fetch_in_flight
                if self._last_fetch_error is not None:
                    due_in = _MIN_FETCH_INTERVAL_SECONDS - (now - self._last_fetch_at)
                    raise JwksUnavailable(
                        "the last JWKS fetch failed "
                        f"({type(self._last_fetch_error).__name__}: "
                        f"{self._last_fetch_error}) and the next is not due for "
                        f"{due_in:.0f}s; this request's kid was never checked"
                    )
                # Suppressed -- and deliberately NOT negative-cached (see
                # the module docstring): this provider never actually
                # looked this kid up, so recording it would block a
                # possibly-legitimate key for a full TTL and would let an
                # attacker's throwaway kids evict genuine entries.
                return None, None, None
            attempt = _JwksFetchAttempt()
            self._fetch_in_flight = attempt
            self._last_fetch_at = now
            return None, attempt, None

    def _run_fetch(self, attempt: _JwksFetchAttempt) -> None:
        """Perform the fetch this caller claimed, then release every rider
        waiting on it -- in a ``finally``, so a fetch that raises (or is
        interrupted) can never strand a rider until its wait cap, nor leave
        ``_fetch_in_flight`` set and send every later caller into a pointless
        wait. The fault itself still propagates to THIS caller unchanged
        (the pre-existing contract: a transport fault escapes ``resolve``
        and is contained by ``api/app.py``'s I-1 handler).
        """
        try:
            self._fetch_jwks()
        except Exception as exc:
            attempt.error = exc
            raise
        finally:
            with self._lock:
                self._last_fetch_error = attempt.error
                self._fetch_in_flight = None
            attempt.done.set()

    def _await_fetch(self, attempt: _JwksFetchAttempt) -> None:
        """Ride a fetch another caller is already performing. Off-lock, so
        the rider blocks nobody, and capped so a hung tenant cannot park
        this worker thread indefinitely. Either failure mode reports the
        TENANT's problem, never a verdict on this request's kid."""
        if not attempt.done.wait(_FETCH_WAIT_SECONDS):
            raise JwksUnavailable(
                f"an in-flight JWKS fetch did not finish within {_FETCH_WAIT_SECONDS:.0f}s; "
                "this request's kid was never checked"
            )
        if attempt.error is not None:
            raise JwksUnavailable(
                "the in-flight JWKS fetch this request was riding failed "
                f"({type(attempt.error).__name__}: {attempt.error}); "
                "this request's kid was never checked"
            )

    def _jwk_after_fetch(self, kid: str) -> Any | None:
        """The post-fetch re-check, under the lock: the freshly published
        JWK if the fetch produced one, otherwise ``None`` with the kid
        recorded as negative so the next request naming it costs nothing.

        Reached by the caller that performed the fetch AND by every rider
        that waited on it -- both have, by this point, seen a real fetch
        complete successfully (a failed one never gets here; it raises
        first), so both are entitled to record the same confirmed absence.
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
        """The outbound request itself. Called ONLY through
        :meth:`_run_fetch`, by the caller that claimed the interval slot in
        :meth:`_cached_jwk_or_fetch_attempt`; the lock is deliberately not
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


__all__ = ["ROLES_CLAIM", "Auth0Provider", "JwksUnavailable"]
