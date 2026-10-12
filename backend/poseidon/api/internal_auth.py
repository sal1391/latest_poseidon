"""The shared-secret gate on every ``/internal/v1`` route (spec section 5.2).

Phase 15 shipped ``api/internal.py`` trusting its body's ``{sub, roles}``
completely, protected only by "nothing outside the service network can
reach it". Ruling R25 recorded that as a must-fix before any deploy. This
module is the fix: the web tier presents ``POSEIDON_INTERNAL_TOKEN`` in a
header, compared in constant time.

Three outcomes, all RFC 7807 problem bodies via the ``AuthError`` handler
``app.py`` already installs:

* setting unset          -> 503 "internal contract not configured"
* header missing / wrong -> 401 "internal token invalid"
* match                  -> the route runs

503 rather than a pass-through when unset is the whole point: a
misconfigured service refuses, it never silently trusts.
"""

import secrets

from fastapi import Request

from poseidon.core.identity import AuthError

INTERNAL_TOKEN_HEADER = "X-Poseidon-Internal-Token"


def require_internal_token(request: Request) -> None:
    expected = request.app.state.settings.internal_token
    if expected is None:
        raise AuthError(
            503,
            "internal contract not configured",
            "POSEIDON_INTERNAL_TOKEN is unset on this service, so every "
            "/internal/v1 route refuses every caller until it is set",
        )
    presented = request.headers.get(INTERNAL_TOKEN_HEADER, "")
    if not presented or not secrets.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    ):
        raise AuthError(
            401,
            "internal token invalid",
            f"missing or wrong {INTERNAL_TOKEN_HEADER} header",
        )
