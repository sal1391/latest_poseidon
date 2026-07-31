"""Phase 9 Task 1: the ``current_user`` dependency and ``GET /api/me`` --
the api-layer half of ``core/identity.py``'s seam (see that module's
docstring for the full provider-blindness contract). ``api/app.py``'s
identity middleware resolves ``request.state.user`` for EVERY request
before any route handler runs; everything here just reads that back.

Mounted UNCONDITIONALLY by ``create_app`` (like ``health.router``), not only
under ``chat_mode="live"``: ``GET /api/me`` is how the frontend discovers
``identity_mode`` and the current identity on boot (doc 05 section 2's
frontend seam), which has to work the same way whether the chat surface
behind it is the scripted mock or the real pipeline -- an identity concept
that only existed under one chat mode would be a real regression the moment
Phase 9's frontend task (Task 4) tries to call it before chat has even
loaded.

Phase 9 Task 2 (Global Constraints) adds the pieces Task 1 deliberately
left to the first provider that could exercise them (see core/identity.py's
"What this task ships vs. what it does not"):

- ``current_user`` now distinguishes a genuine wiring bug (the middleware
  never ran at all -- unchanged ``RuntimeError``) from an ``AuthError`` a
  provider raised (``auth0`` mode: a bad/missing/expired token) -- the
  middleware (``api/app.py``) catches the latter and records it on
  ``request.state.auth_error`` rather than failing the WHOLE request
  outright, so a route that never depends on identity (``/health/*``)
  stays open even when the caller's credential is bad or absent.
  ``current_user`` is the ONE place that distinction turns into an
  HTTP-visible failure.
- ``require_sales``: the require-user + require-role guard (Global
  Constraints) Task 2 exports for ``/api/skills``, ``/api/dev/*``, and
  (Controller's Round 0 correction, cf401b1) every ``/api/conversations*``/
  ``/api/messages*`` route ``live_chat.py`` already serves today --
  composes ``current_user``'s 401 with its own 403 when the resolved
  identity lacks ``Poseidon:Sales``.
- ``AuthError`` -> RFC-7807: :func:`auth_error_response`, registered by
  ``api/app.py`` as the ``AuthError`` exception handler, is the ONE place
  that mapping happens, so every raiser (any provider's ``resolve``,
  ``require_sales``'s own 403) converges on identical wire bytes.
- The chat-send rate limiter (:class:`ChatRateLimiter`, :func:`
  rate_limit_chat_send`, :class:`RateLimitExceeded` +
  :func:`rate_limit_exceeded_response`): a config-driven token bucket
  keyed by sub (fallback client IP), attached only to ``live_chat.py``'s
  real ``POST /api/conversations/{cid}/messages``.

Keeps ``core/identity*.py`` free of FastAPI imports (that module's own
"providers never import FastAPI" seam): every HTTP shape -- status code,
RFC-7807 body, the ``Retry-After`` header -- is built HERE, never in a
provider.
"""

import math
import threading
import time
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from poseidon.core.identity import AuthError, UserContext
from poseidon.core.skills.result import problem

router = APIRouter(prefix="/api", tags=["auth"])

# Global Constraints: the one role every route this phase gates requires.
_REQUIRED_ROLE = "Poseidon:Sales"


def current_user(request: Request) -> UserContext:
    """The resolved identity for this request. Reads ``request.state.user``
    -- set by ``api/app.py``'s identity middleware, which runs for EVERY
    request ahead of any dependency.

    Three outcomes, checked in order:

    1. ``request.state.user`` is a real :class:`UserContext` -- the common
       case in every mode -- return it.
    2. It is unset, but the middleware recorded an :class:`AuthError` on
       ``request.state.auth_error`` (``auth0`` mode: a missing/malformed/
       expired/... token) -- RE-RAISE that same error. It propagates up
       through FastAPI's normal dependency-resolution exception handling to
       ``api/app.py``'s registered ``AuthError`` handler
       (:func:`auth_error_response`), which renders the pinned 401 RFC-7807
       body. Deferring the raise to HERE, rather than failing inside the
       middleware itself, is what keeps a route that never depends on
       identity (``/health/*``) open even when the caller's credential is
       bad: the middleware records the failure but never rejects the
       request outright; only a route that actually asks (via this
       dependency) can fail on it.
    3. Neither -- a genuine wiring bug (the middleware never ran at all:
       unreachable through any real request, since ``api/app.py`` installs
       it for every one). Loud and honest, never a silent 401 that would
       disguise a server misconfiguration as a client auth failure.
    """
    user = getattr(request.state, "user", None)
    if user is not None:
        return user
    auth_error = getattr(request.state, "auth_error", None)
    if auth_error is not None:
        raise auth_error
    raise RuntimeError(
        "request.state.user is unset - api/app.py's identity middleware "
        "must run before any route depends on current_user"
    )


def require_sales(user: UserContext = Depends(current_user)) -> UserContext:  # noqa: B008
    """The require-user + require-role dependency Global Constraints pins
    for ``/api/skills``, ``/api/dev/*``, and every ``/api/conversations*``/
    ``/api/messages*`` route (``live_chat.py`` serves all six of those
    today; guarded as of the Controller's Round 0 correction, cf401b1):
    ``Depends(current_user)`` already supplies the require-USER half (401
    for no/bad credential -- see that function's own docstring); this
    layers the require-ROLE half (403) on top for the one role every
    gated route in this phase needs.

    A route attaches this ONE dependency (``dependencies=[Depends(
    require_sales)]``, or for a whole router at once, ``include_router(...,
    dependencies=[Depends(require_sales)])`` -- see ``api/app.py``'s own
    ``dev_runner.router`` inclusion) rather than composing the two halves
    itself every time.
    """
    if _REQUIRED_ROLE not in user.roles:
        raise AuthError(
            403,
            "insufficient role",
            f"caller lacks required role {_REQUIRED_ROLE!r}",
        )
    return user


@router.get("/me")
def get_me(
    request: Request,
    # noqa: B008 -- ruff's "no function call in a default" warning is a
    # false positive for FastAPI's own Depends() idiom: FastAPI intercepts
    # this sentinel at route-registration time and resolves it fresh PER
    # REQUEST, never sharing one mutable object across calls the way the
    # rule's real target (e.g. `def f(x=[])`) would. The standard FastAPI
    # pattern, kept exactly as idiomatic here as Task 2's own role-gated
    # routes need it to be.
    user: UserContext = Depends(current_user),  # noqa: B008
) -> dict:
    """``{sub, name, email, roles, identity_mode}`` -- doc 05 section 2's
    frontend seam: the SPA calls this on boot to learn which identity mode
    is active and, in every mode but ``auth0``-unauthenticated, who the
    current user already is, with no separate login round trip needed.

    Depends on ``current_user`` alone -- never ``require_sales`` -- since
    Task 2's own handoff pins this endpoint reachable for ANY authenticated
    (or disabled-mode) caller regardless of role: it is how the frontend
    discovers identity itself, including a caller who turns out to lack
    ``Poseidon:Sales`` and needs the response's own ``roles`` list to
    render a clear "no access" screen rather than a 403 with no context.

    ``identity_mode`` comes from ``settings`` (not from ``user`` -- a
    ``UserContext`` carries no notion of which mode produced it), read off
    ``request.app.state.settings`` the same way every other route in this
    codebase reads shared app state.
    """
    settings = request.app.state.settings
    return {
        "sub": user.sub,
        "name": user.name,
        "email": user.email,
        "roles": list(user.roles),
        "identity_mode": settings.identity_mode,
    }


def auth_error_response(request: Request, exc: AuthError) -> JSONResponse:
    """``api/app.py``'s registered handler for :class:`AuthError` -- the
    ONE place any 401 (a provider's ``resolve``, via ``current_user``) or
    403 (``require_sales``) becomes an RFC-7807 body, via the SAME
    :func:`~poseidon.core.skills.result.problem` constructor every skill
    failure in this codebase already renders through (byte-identical
    shape, not a second hand-rolled dict).
    """
    return JSONResponse(status_code=exc.status, content=problem(exc.status, exc.title, exc.detail))


class RateLimitExceeded(Exception):
    """Raised by :func:`rate_limit_chat_send` when a caller's chat-send
    token bucket is empty. Deliberately NOT an :class:`AuthError` -- a rate
    limit is not an identity/authorization failure, and a 429 needs a
    ``Retry-After`` header an :class:`AuthError` (401/403, header-free) has
    no slot for -- but ``api/app.py`` registers its own handler
    (:func:`rate_limit_exceeded_response`) the exact same way, so both
    failure families converge on the identical RFC-7807 body shape.
    """

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("chat send rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


def rate_limit_exceeded_response(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """The 429 RFC-7807 body plus ``Retry-After`` (Global Constraints).

    The body's own ``detail`` text deliberately does NOT embed the exact
    retry-after seconds value: that number depends on real wall-clock
    timing (how close to empty the bucket was, how long this request took
    to reach the check), so it can never be byte-pinned the way the
    status/title/detail triple for every other problem response in this
    task can. The precise, timing-dependent number lives ONLY in the
    ``Retry-After`` header, exactly where RFC 9110 sec 10.2.3 says a client
    should look for it.
    """
    body = problem(
        429,
        "rate limit exceeded",
        "too many chat messages; retry after the interval in the Retry-After header",
    )
    return JSONResponse(
        status_code=429, content=body, headers={"Retry-After": str(exc.retry_after_seconds)}
    )


@dataclass
class _TokenBucket:
    """Mutable per-key bookkeeping :class:`ChatRateLimiter` owns -- NOT a
    frozen value type like every other dataclass in this codebase, because
    its entire job is to mutate in place as time passes and tokens are
    spent (see that class's own docstring)."""

    tokens: float
    last_refill: float


class ChatRateLimiter:
    """A config-driven token bucket, one per distinct key (Global
    Constraints: "keyed by sub, fallback client IP"), shared by every
    request through one app/process (built once in ``api/app.py``'s
    ``_wire_live_chat``, stored on ``app.state.chat_rate_limiter``) -- the
    same "build once, share" discipline every other per-process
    ``app.state`` object in this codebase already follows.

    Classic token bucket, not a fixed window: a key's bucket starts FULL
    (``per_minute`` tokens -- an operator's first ``per_minute`` requests
    in ANY window never wait) and refills CONTINUOUSLY at ``per_minute /
    60`` tokens per second, so "N per minute" means what it says for any
    rolling 60-second window, not just a window aligned to the clock.

    ``time.monotonic()`` (never ``time.time()``): immune to wall-clock
    adjustments (NTP sync, DST, an operator changing the system clock) that
    would otherwise let a bucket's refill math go backwards or jump --
    Python's own docs recommend it for exactly this "measuring elapsed
    time" use, never for telling calendar time.

    Thread-safe (Global Constraints): one lock guards every bucket's
    read-modify-write, coarse-grained rather than per-key, since this
    endpoint's traffic volume does not warrant finer-grained locking and
    coarse locking is simpler to prove correct.

    Precondition: ``per_minute`` is a positive int. The ``limit=0`` ("off")
    case is handled by the CALLER never constructing this class at all
    (``api/app.py``'s ``_build_chat_rate_limiter`` stores ``None``
    instead) -- a bucket literally built with zero capacity would reject
    EVERY request forever, the opposite of "off", so "off" is its own code
    path, not a degenerate instance of this one.
    """

    def __init__(self, per_minute: int) -> None:
        self._capacity = float(per_minute)
        self._refill_per_second = per_minute / 60.0
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """``None`` if ``key`` may proceed (a token was spent); otherwise
        the number of seconds until at least one token will be available
        again."""
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _TokenBucket(tokens=self._capacity, last_refill=now)
                self._buckets[key] = bucket
            else:
                elapsed = now - bucket.last_refill
                refilled = bucket.tokens + elapsed * self._refill_per_second
                bucket.tokens = min(self._capacity, refilled)
                bucket.last_refill = now
            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return None
            deficit = 1.0 - bucket.tokens
            return deficit / self._refill_per_second


def rate_limit_chat_send(request: Request) -> None:
    """The FastAPI dependency Task 2 attaches to ``live_chat.py``'s real
    chat-send route ONLY (``POST /api/conversations/{cid}/messages`` --
    Global Constraints: "on the chat-send POST"; the identically-shaped
    ``mock_chat.py`` route is untouched, per the same Global Constraints'
    "Mock-chat router (test-only) untouched").

    No-ops (returns ``None``, allowing the request through) whenever
    ``app.state.chat_rate_limiter`` is ``None`` OR UNSET ENTIRELY -- limit
    0/off, the mode-dependent default resolving to 0 (``core/config.py``'s
    ``effective_rate_limit_chat_per_minute`` -- ``disabled`` mode, by
    default, so no existing dev/test flow is ever rate-limited by
    surprise), or a mock-mode app, which never runs ``_wire_live_chat`` at
    all and so never sets this attribute in the first place (M-6, phase 9
    final review): a bare ``request.app.state.chat_rate_limiter`` access
    would raise ``AttributeError`` instead of no-opping the moment ANY
    mock-mode-reachable route ever attaches this dependency --
    ``getattr(..., None)`` defuses that landmine for good.

    CORRECTED (Controller's Round 0 correction, cf401b1): as of that
    commit, ``live_chat.py``'s chat-send route lists
    ``dependencies=[Depends(require_sales), Depends(rate_limit_chat_send)]``
    -- ``require_sales`` ALWAYS runs first and short-circuits (401/403)
    before this dependency is even invoked (FastAPI resolves a route's
    ``dependencies=[...]`` as a plain, sequential, short-circuiting loop),
    so by the time this function actually runs, ``request.state.user`` is
    guaranteed set to a real, role-checked :class:`~poseidon.core.identity.
    UserContext` -- an unauthenticated or role-less caller gets 401/403
    from ``require_sales`` and never reaches the rate limiter at all, so a
    429 can never mask an auth failure. Keyed by ``request.state.user.sub``
    accordingly (Global Constraints: "keyed by sub"); the ``fallback
    client IP`` branch below is defense-in-depth for a hypothetical future
    caller of this same dependency on a route that does NOT carry
    ``require_sales`` ahead of it (unreachable on the one route this
    dependency is attached to today, per the guarantee just described, but
    kept rather than removed so this function's own contract does not
    silently assume every future caller replicates that ordering).
    """
    limiter = getattr(request.app.state, "chat_rate_limiter", None)
    if limiter is None:
        return
    user = getattr(request.state, "user", None)
    if user is not None:
        key = user.sub
    else:
        key = request.client.host if request.client else "unknown"
    retry_after = limiter.check(key)
    if retry_after is not None:
        raise RateLimitExceeded(retry_after_seconds=max(1, math.ceil(retry_after)))


__all__ = [
    "ChatRateLimiter",
    "RateLimitExceeded",
    "auth_error_response",
    "current_user",
    "rate_limit_chat_send",
    "rate_limit_exceeded_response",
    "require_sales",
    "router",
]
