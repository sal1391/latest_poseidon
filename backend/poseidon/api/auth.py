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
"""

from fastapi import APIRouter, Depends, Request

from poseidon.core.identity import UserContext

router = APIRouter(prefix="/api", tags=["auth"])


def current_user(request: Request) -> UserContext:
    """The resolved identity for this request. Reads ``request.state.user``
    -- set by ``api/app.py``'s identity middleware, which runs for EVERY
    request ahead of any dependency -- never resolves anything itself: this
    function is the one and only place downstream code reaches for the
    current identity, so every route that needs it (this task's own
    ``GET /api/me``; Task 2's role-gated routes) depends on the SAME
    function rather than each reading ``request.state.user`` directly.

    The ``None`` branch below is unreachable through any real request in
    this task (``DisabledProvider.resolve`` always returns a value, never
    raises -- see its own docstring) and exists only as a loud, honest
    failure for a genuine wiring bug (the middleware not installed, or a
    future code path that bypasses it) -- never a silent 401 that would
    disguise a server misconfiguration as a client auth failure.
    """
    user = getattr(request.state, "user", None)
    if user is None:
        raise RuntimeError(
            "request.state.user is unset - api/app.py's identity middleware "
            "must run before any route depends on current_user"
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
    # routes will need it to be.
    user: UserContext = Depends(current_user),  # noqa: B008
) -> dict:
    """``{sub, name, email, roles, identity_mode}`` -- doc 05 section 2's
    frontend seam: the SPA calls this on boot to learn which identity mode
    is active and, in every mode but ``auth0``-unauthenticated, who the
    current user already is, with no separate login round trip needed.

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


__all__ = ["current_user", "router"]
