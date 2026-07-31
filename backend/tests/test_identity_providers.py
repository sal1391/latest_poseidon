"""Tests for Phase 9 Task 1 (doc 05 section 2, decision D22):
``core/identity.py``'s ``UserContext``/``AuthError``/``IdentityProvider``
seam, ``DisabledProvider`` (the only provider this task ships), and
``resolve_provider``'s mode selection.

Everything here is OFFLINE and provider-level: a plain dict stands in for
the lowercase-keyed header mapping ``api/app.py``'s identity middleware
builds from a real ``Request`` (``core/identity.py``'s own module
docstring -- "providers never import FastAPI"), so no ASGI app is needed to
prove ``DisabledProvider``'s own contract. The end-to-end threading proof
(a real request, through the real middleware, into a run-log writer
double's ``user_sub``) lives in ``test_api_auth.py`` instead -- this file
is the provider unit matrix underneath it.

Phase 9 Task 2 adds ``Auth0Provider``'s own unit matrix below (still
offline/provider-level, same discipline): a local RSA keypair + an
injectable ``httpx`` transport stand in for the tenant's real JWKS endpoint
(Global Constraints: "ZERO live/network calls... the JWKS fixture is
local"). The JWKS-fixture helpers (:func:`generate_rsa_keypair`,
:func:`jwk_for`, :func:`mint_auth0_token`, :class:`JwksTransport`,
:data:`AUTH0_TEST_DOMAIN`/:data:`AUTH0_TEST_AUDIENCE`) are reused, not
re-derived, by ``test_api_auth.py``'s own HTTP-level Auth0 tests -- the
same cross-test-module reuse discipline that file's own docstring already
established for ``test_chat_orchestrator.py``'s ``FakeDataClient``/
``RecordingWriter``.

Phase 9 Task 3 adds ``SpcsIngressProvider``'s own unit matrix below (same
offline/provider-level discipline -- zero I/O of any kind, so no injectable
transport is even needed this time): the deploy-mode boot gate, the
``Sf-Context-Current-User`` header's ``sf|...`` sub mapping via the SAME
:func:`~poseidon.core.identity.sanitize_username` helper the ``DisabledProvider``
tests above already exercise, and the ``SPCS_SALES_USERS`` allowlist
(member/non-member/wildcard/empty). ``resolve_provider``'s own matrix below
gains the real ``spcs_ingress`` branch and retires the old "not implemented
yet" placeholder test its own docstring promised to retire. The narrow
HTTP-boundary slice (boot fail-fast through the real ``create_app``, ``GET
/api/me``, ``/health/*`` staying open, the ``require_sales`` 403 for a
non-allowlisted user) lives in ``test_api_auth.py`` instead, mirroring
Task 2's own provider-unit-tests-here / HTTP-tests-there split.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from poseidon.core import identity as identity_module
from poseidon.core.config import Settings
from poseidon.core.identity import (
    DISABLED_DEFAULT_USER,
    AuthError,
    DisabledProvider,
    UserContext,
    resolve_provider,
    sanitize_username,
)
from poseidon.core.identity_auth0 import ROLES_CLAIM, Auth0Provider
from poseidon.core.identity_spcs import SpcsIngressProvider

_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"

# Global Constraints: iss = https://{AUTH0_DOMAIN}/ ; aud = AUTH0_AUDIENCE.
# Fake, offline-only values -- no real tenant is ever contacted.
AUTH0_TEST_DOMAIN = "tenant.auth0.test"
AUTH0_TEST_AUDIENCE = "https://poseidon.test/api"
AUTH0_TEST_ISSUER = f"https://{AUTH0_TEST_DOMAIN}/"


def _settings(**overrides) -> Settings:
    """Mirrors every other test module's own minimal ``Settings`` helper
    (e.g. ``test_emit_seam_loop_events.py``'s ``_bare_settings``) -- no
    monkeypatched environment needed, since nothing here touches an LLM or
    reads an ambient env var."""
    defaults: dict = dict(
        _env_file=None, database_url=_PLACEHOLDER_DSN, s3_bucket="poseidon-artifacts"
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _auth0_settings(**overrides) -> Settings:
    """``_settings()`` plus the three ``auth0_*`` fields ``identity_mode=
    "auth0"`` requires (``core/config.py``'s own ``auth0_fields_required_
    in_auth0_mode`` validator) -- ``auth0_client_id`` is a real value only
    because Settings demands SOME non-blank string; ``Auth0Provider``
    itself never reads it (see ``core/identity_auth0.py``'s own module
    docstring: JWKS verification needs only domain + audience)."""
    defaults: dict = dict(
        identity_mode="auth0",
        auth0_domain=AUTH0_TEST_DOMAIN,
        auth0_audience=AUTH0_TEST_AUDIENCE,
        auth0_client_id="test-client-id",
    )
    defaults.update(overrides)
    return _settings(**defaults)


def _spcs_settings(**overrides) -> Settings:
    """``_settings()`` plus the two fields ``identity_mode="spcs_ingress"``
    needs to construct cleanly: ``deploy_mode="spcs"`` (the ONE deploy
    target this header is ever trustworthy under) and a wildcard allowlist
    (harmless default for tests that are not themselves about allowlist
    membership -- overridden per test below where membership is the
    point)."""
    defaults: dict = dict(identity_mode="spcs_ingress", deploy_mode="spcs", spcs_sales_users="*")
    defaults.update(overrides)
    return _settings(**defaults)


def generate_rsa_keypair():
    """A fresh 2048-bit RSA keypair -- ``cryptography`` arrives transitively
    via ``PyJWT[crypto]`` (Global Constraints), no separate dependency
    needed. Reused by ``test_api_auth.py``'s HTTP-level Auth0 tests."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _private_pem(private_key) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def jwk_for(public_key, kid: str) -> dict:
    """One JWKS ``keys[]`` entry for ``public_key`` -- ``RSAAlgorithm.
    to_jwk`` supplies ``kty``/``n``/``e``; ``kid``/``use``/``alg`` are added
    here, matching the shape a real Auth0 JWKS document carries."""
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk.update(kid=kid, use="sig", alg="RS256")
    return jwk


class JwksTransport(httpx.BaseTransport):
    """The injectable httpx transport Global Constraints calls for ("the
    JWKS fixture is local... injectable httpx transport"): serves
    ``{"keys": self.keys}`` at whatever URL ``Auth0Provider`` asks for
    (Auth0's own JWKS path is the only one it ever requests), with zero
    real network I/O. ``self.keys`` is a plain mutable list a test can
    append to mid-test (see the kid-rotation tests below) to prove a
    provider's cache picks up a tenant-side JWKS change on its next fetch.
    ``request_count`` lets a test assert exactly how many fetches happened
    -- the "refetch ONCE on unknown kid" contract is a claim about COUNT,
    not merely "it eventually worked".
    """

    def __init__(self, keys: list[dict]) -> None:
        self.keys = keys
        self.request_count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        return httpx.Response(200, json={"keys": self.keys})


class UnreachableJwksTransport(httpx.BaseTransport):
    """Phase 9 final-review fix wave (I-1): simulates the tenant's JWKS
    endpoint being completely unreachable (a network blip, DNS failure,
    ...) -- raises ``httpx.ConnectError``, the SAME exception class a real
    ``Auth0Provider`` using its default (non-test) transport would see
    against a host that refuses the connection. Reused by
    ``test_api_auth.py``'s own HTTP-boundary proof of this exact case, the
    same cross-test-module reuse discipline ``JwksTransport`` above already
    established."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated JWKS endpoint unreachable", request=request)


class FailingJwksTransport(httpx.BaseTransport):
    """Phase 9 final-review fix wave (I-1): simulates the tenant's JWKS
    endpoint being reachable but answering with a server error (a bad
    tenant-side deploy, a transient 5xx during Auth0's own maintenance,
    ...) -- triggers ``response.raise_for_status()``'s
    ``httpx.HTTPStatusError``. Reused by ``test_api_auth.py`` like
    :class:`UnreachableJwksTransport` above."""

    def __init__(self, status_code: int = 500) -> None:
        self.status_code = status_code

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(self.status_code, request=request, json={"error": "boom"})


def oct_jwk_for(kid: str) -> dict:
    """A non-RSA (symmetric) JWKS ``keys[]`` entry sharing ``kid`` with an
    RSA-signed test token -- M-10 (phase 9 final review): ``Auth0Provider.
    _public_key_for_kid`` hands ANY ``kid``-matching JWK to ``RSAAlgorithm.
    from_jwk`` without checking ``kty``/``use``/``alg`` first, so a
    colliding, non-RSA key must not crash the request (contained by I-1's
    middleware fix -- see ``test_api_auth.py``'s own HTTP-boundary proof).
    The actual key material is irrelevant -- resolution never gets far
    enough to use it -- so a fixed, obviously-fake ``k`` is fine."""
    return {"kty": "oct", "k": "c2VjcmV0", "kid": kid, "use": "sig", "alg": "HS256"}


def mint_auth0_token(private_key, kid: str, **claim_overrides) -> str:
    """A signed RS256 JWT with a realistic Auth0-shaped claim set (valid
    iss/aud/exp/nbf/roles by default -- override any of them per test to
    build the exact malformed/expired/wrong-... variant that test needs).
    """
    now = datetime.now(UTC)
    claims: dict = {
        "sub": "user123",
        "email": "alice@example.com",
        "name": "Alice",
        "iss": AUTH0_TEST_ISSUER,
        "aud": AUTH0_TEST_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=1),
        ROLES_CLAIM: ["Poseidon:Sales"],
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, _private_pem(private_key), algorithm="RS256", headers={"kid": kid})


def mint_auth0_token_without_sub_claim(private_key, kid: str, **claim_overrides) -> str:
    """The same realistic Auth0-shaped claim set :func:`mint_auth0_token`
    mints, EXCEPT ``sub`` is never added at all -- I-1 (phase 9 final
    review): PyJWT's own ``jwt.decode`` already rejects a PRESENT-but-
    wrong-type ``sub`` on its own (``InvalidSubjectError``, caught by
    ``Auth0Provider._decode``'s existing catch-all), so a keyword override
    like ``sub=None`` on :func:`mint_auth0_token` never reaches the
    defensive ``claims.get("sub")`` check ``resolve`` adds -- it has to be
    genuinely ABSENT, which only omitting the key entirely (not merely
    falsy-ing its value) proves. Reused by ``test_api_auth.py``'s own
    HTTP-boundary proof of this exact case, the same cross-test-module
    reuse discipline every other JWKS-fixture helper here already
    established."""
    now = datetime.now(UTC)
    claims: dict = {
        "email": "alice@example.com",
        "name": "Alice",
        "iss": AUTH0_TEST_ISSUER,
        "aud": AUTH0_TEST_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=1),
        ROLES_CLAIM: ["Poseidon:Sales"],
    }
    claims.update(claim_overrides)
    return jwt.encode(claims, _private_pem(private_key), algorithm="RS256", headers={"kid": kid})


def _auth0_provider(transport: httpx.BaseTransport, **settings_overrides) -> Auth0Provider:
    return Auth0Provider(_auth0_settings(**settings_overrides), transport=transport)


def _expect_auth_error(provider, headers, status: int, title: str) -> AuthError:
    with pytest.raises(AuthError) as exc_info:
        provider.resolve(headers)
    assert exc_info.value.status == status
    assert exc_info.value.title == title
    return exc_info.value


@pytest.fixture
def rsa_keys():
    """Two independent RSA keypairs: key1 (published in the served JWKS
    throughout), key2 (NOT published -- used to mint a bad-signature token,
    or to simulate a legitimately rotated-in key the tenant adds later)."""
    key1, pub1 = generate_rsa_keypair()
    key2, pub2 = generate_rsa_keypair()
    return key1, pub1, key2, pub2


# ===========================================================================
# UserContext -- frozen value type
# ===========================================================================


def test_user_context_is_frozen():
    from dataclasses import FrozenInstanceError

    user = UserContext(sub="dev|local", email="dev@local", name="Dev User", roles=())
    with pytest.raises(FrozenInstanceError):
        user.sub = "dev|other"


def test_disabled_default_user_matches_the_pinned_shape():
    """Global Constraints' own pinned literal: ``UserContext("dev|local",
    "dev@local", "Dev User", ("Poseidon:Sales",))`` -- the SAME sub every
    existing run-log row/test in this codebase already pins."""
    assert DISABLED_DEFAULT_USER == UserContext(
        sub="dev|local", email="dev@local", name="Dev User", roles=("Poseidon:Sales",)
    )


# ===========================================================================
# AuthError -- the typed failure the protocol documents (no provider in
# this task raises it; this pins the shape Task 2's Auth0Provider inherits)
# ===========================================================================


def test_auth_error_carries_status_title_and_detail():
    err = AuthError(401, "missing bearer token", "no Authorization header")
    assert err.status == 401
    assert err.title == "missing bearer token"
    assert err.detail == "no Authorization header"
    assert str(err) == "no Authorization header"
    assert isinstance(err, Exception)


# ===========================================================================
# DisabledProvider -- the fixed default
# ===========================================================================


def test_resolve_with_no_headers_returns_the_fixed_default():
    assert DisabledProvider().resolve({}) == DISABLED_DEFAULT_USER


def test_resolve_ignores_unrelated_headers():
    provider = DisabledProvider()
    assert provider.resolve({"x-other-header": "whatever"}) == DISABLED_DEFAULT_USER


# ===========================================================================
# DisabledProvider -- X-Dev-User act-as, valid cases
# ===========================================================================


def test_act_as_lowercase_value_becomes_dev_pipe_value():
    user = DisabledProvider().resolve({"x-dev-user": "alice"})
    assert user == UserContext(
        sub="dev|alice", email="alice@local", name="alice", roles=("Poseidon:Sales",)
    )


def test_act_as_is_casefolded():
    user = DisabledProvider().resolve({"x-dev-user": "ALICE"})
    assert user.sub == "dev|alice"


def test_act_as_casefold_is_not_merely_lowercase():
    """The pinned rule says "casefolded", not "lowercased" -- these differ
    for some Unicode input (``str.casefold`` is the more aggressive,
    locale-independent transform Python recommends for caseless matching).
    German sharp s (U+00DF, built via ``chr()`` so this file stays pure
    ASCII on disk -- the same convention ``orchestrator.py``'s own
    ``_EM_DASH`` constant uses) casefolds to "ss" (still inside
    [a-z0-9_-]) but does NOT lowercase to anything in that set (its
    ``.lower()`` is a no-op) -- proving this module truly calls
    ``.casefold()``, not ``.lower()``."""
    sharp_s = chr(0x00DF)
    assert sharp_s.casefold() == "ss"
    assert sharp_s.lower() == sharp_s

    user = DisabledProvider().resolve({"x-dev-user": sharp_s})

    assert user.sub == "dev|ss"


def test_act_as_accepts_digits_underscore_and_hyphen():
    user = DisabledProvider().resolve({"x-dev-user": "alice_bob-2"})
    assert user.sub == "dev|alice_bob-2"


def test_act_as_accepts_the_maximum_length_of_64_chars():
    value = "a" * 64
    user = DisabledProvider().resolve({"x-dev-user": value})
    assert user.sub == f"dev|{value}"


def test_act_as_roles_and_email_and_name_pattern():
    """Not pinned by the plan (only the sub shape is) -- this task's own
    disclosed judgment call: email/name are derived per act-as identity
    (never the fixed "dev@local"/"Dev User" reused verbatim), roles stay
    the fixed single-role tuple regardless of which dev user is acting."""
    user = DisabledProvider().resolve({"x-dev-user": "bob"})
    assert user.email == "bob@local"
    assert user.name == "bob"
    assert user.roles == ("Poseidon:Sales",)


# ===========================================================================
# DisabledProvider -- X-Dev-User act-as, invalid cases: ignore the header,
# fall back to the fixed default (never a rejection/error)
# ===========================================================================


@pytest.mark.parametrize(
    "raw",
    [
        "",  # too short (min 1)
        "a" * 65,  # too long (max 64)
        "alice bob",  # space is not in [a-z0-9_-]
        "alice!",  # punctuation
        "alice.bob",  # dot
        "alice/bob",  # slash
        "  alice",  # leading whitespace
        "alice\t",  # trailing tab
    ],
)
def test_act_as_invalid_values_fall_back_to_the_fixed_default(raw):
    user = DisabledProvider().resolve({"x-dev-user": raw})
    assert user == DISABLED_DEFAULT_USER


# ===========================================================================
# Auth0Provider -- valid token, prefixed sub, role-less tokens (Phase 9
# Task 2, offline against the local JWKS fixture -- see JwksTransport)
# ===========================================================================


def test_auth0_valid_token_resolves_to_a_prefixed_user_context(rsa_keys):
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))

    user = provider.resolve({"authorization": f"Bearer {mint_auth0_token(key1, 'key-1')}"})

    assert user == UserContext(
        sub="auth0|user123",
        email="alice@example.com",
        name="Alice",
        roles=("Poseidon:Sales",),
    )


def test_auth0_sub_is_always_auth0_prefixed_even_if_already_prefixed(rsa_keys):
    """Judgment call (disclosed in this task's report): unconditional, like
    ``DisabledProvider``'s own ``f"dev|{x}"`` -- even a raw ``sub`` that
    already looks provider-prefixed (Auth0's default database connection's
    own convention) gets a SECOND ``auth0|`` layered on. A double prefix
    for that one connection type is the accepted cosmetic cost of a
    guarantee that holds for every OTHER connection type too (google-
    oauth2, samlp, enterprise SSO, ...), none of which are guaranteed to
    start with the literal string "auth0|" on their own."""
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key1, "key-1", sub="auth0|already-prefixed")

    user = provider.resolve({"authorization": f"Bearer {token}"})

    assert user.sub == "auth0|auth0|already-prefixed"


def test_auth0_role_less_token_resolves_with_empty_roles(rsa_keys):
    """A role-LESS token is not itself an auth failure -- identity and
    authorization are different questions (module docstring). The 403 for
    "valid token, no Poseidon:Sales" is enforced separately, by
    ``api/auth.py``'s ``require_sales`` -- see test_api_auth.py."""
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key1, "key-1", **{ROLES_CLAIM: []})

    user = provider.resolve({"authorization": f"Bearer {token}"})

    assert user.roles == ()


def test_auth0_missing_roles_claim_resolves_with_empty_roles(rsa_keys):
    """Distinct from the empty-list case above: the claim is ABSENT
    entirely (a token minted with no roles claim key at all), not merely
    empty -- both must resolve identically, never raise."""
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    now = datetime.now(UTC)
    claims = {
        "sub": "user123",
        "iss": AUTH0_TEST_ISSUER,
        "aud": AUTH0_TEST_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    token = jwt.encode(claims, _private_pem(key1), algorithm="RS256", headers={"kid": "key-1"})

    user = provider.resolve({"authorization": f"Bearer {token}"})

    assert user.roles == ()


# ===========================================================================
# Auth0Provider -- the pinned 401 failure matrix (Global Constraints:
# "missing/malformed header, bad signature, expired, wrong iss/aud, future
# nbf")
# ===========================================================================


def test_auth0_missing_authorization_header_is_401(rsa_keys):
    _key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))

    err = _expect_auth_error(provider, {}, 401, "missing bearer token")

    assert err.detail == "no Authorization header"


@pytest.mark.parametrize(
    "raw",
    [
        "",  # header present but empty
        "Token abc",  # not the Bearer scheme
        # lowercase scheme -- accepted as a scheme match since M-5 (phase 9
        # final review, RFC 9110 section 11.1), but "abc" is still not a
        # structurally valid JWT, so this row stays malformed either way;
        # see test_auth0_bearer_scheme_is_case_insensitive below for the
        # case that actually proves case-insensitivity with a real token.
        "bearer abc",
        "Bearer",  # scheme with no token at all
        "Bearer   ",  # scheme plus only whitespace
        "Bearer not-a-jwt-at-all",  # not a structurally valid JWT
    ],
)
def test_auth0_malformed_authorization_header_variants(rsa_keys, raw):
    _key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))

    _expect_auth_error(provider, {"authorization": raw}, 401, "malformed authorization header")


def test_auth0_bearer_scheme_is_case_insensitive(rsa_keys):
    """M-5 / T2-M2 (phase 9 final review, RFC 9110 section 11.1): the
    auth-scheme token is case-insensitive -- "bearer", "Bearer", "BEARER"
    all name the identical scheme. No shipped client sends anything but
    the canonical "Bearer" (the Auth0 SDK's own convention), which is why
    T2 originally deferred this; folded into the final-review fix wave
    since it is one line plus one test. The credential itself is never
    casefolded -- only the scheme name gets this treatment."""
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key1, "key-1")

    user = provider.resolve({"authorization": f"bearer {token}"})

    assert user.sub == "auth0|user123"


def test_auth0_expired_token_is_401(rsa_keys):
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    now = datetime.now(UTC)
    token = mint_auth0_token(key1, "key-1", exp=now - timedelta(minutes=1))

    err = _expect_auth_error(provider, {"authorization": f"Bearer {token}"}, 401, "token expired")

    assert err.detail == "the token's exp claim is in the past"


def test_auth0_future_nbf_is_401(rsa_keys):
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    now = datetime.now(UTC)
    token = mint_auth0_token(key1, "key-1", nbf=now + timedelta(minutes=5))

    err = _expect_auth_error(
        provider, {"authorization": f"Bearer {token}"}, 401, "token not yet valid"
    )

    assert err.detail == "the token's nbf claim is in the future"


def test_auth0_wrong_audience_is_401(rsa_keys):
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key1, "key-1", aud="https://someone-else.test/api")

    _expect_auth_error(provider, {"authorization": f"Bearer {token}"}, 401, "invalid audience")


def test_auth0_wrong_issuer_is_401(rsa_keys):
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key1, "key-1", iss="https://not-the-tenant.auth0.test/")

    _expect_auth_error(provider, {"authorization": f"Bearer {token}"}, 401, "invalid issuer")


def test_auth0_bad_signature_via_second_key_is_401(rsa_keys):
    """The header names a REAL, published kid ("key-1"), but the token was
    actually signed with key-2's private key -- proves verification is
    real RS256 math against the published public key, not merely a kid
    lookup that happens to find something."""
    key1, pub1, key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token(key2, "key-1")

    _expect_auth_error(
        provider, {"authorization": f"Bearer {token}"}, 401, "invalid token signature"
    )


def test_auth0_token_with_no_sub_claim_is_401(rsa_keys):
    """I-1 (phase 9 final review): ``Auth0Provider.resolve`` used to read
    ``claims['sub']`` unconditionally -- PyJWT does not require a ``sub``
    claim to exist, so a validly-signed token that simply omits it raised a
    bare ``KeyError`` instead of the pinned :class:`AuthError` every other
    case in this matrix already raises, escaping as an opaque 500 at the
    HTTP boundary (see ``test_api_auth.py``'s own HTTP-level proof of this
    exact case). Fixed to read ``claims.get("sub")`` defensively. Uses
    :func:`mint_auth0_token_without_sub_claim` (see its own docstring for
    why an omitted key, not merely a falsy value, is what this case
    needs) so ``sub`` is genuinely ABSENT."""
    key1, pub1, _key2, _pub2 = rsa_keys
    provider = _auth0_provider(JwksTransport([jwk_for(pub1, "key-1")]))
    token = mint_auth0_token_without_sub_claim(key1, "key-1")

    err = _expect_auth_error(provider, {"authorization": f"Bearer {token}"}, 401, "invalid token")

    assert err.detail == "token has no sub claim"


# ===========================================================================
# Auth0Provider -- JWKS caching: refetch ONCE on an unknown/rotated kid,
# never refetch for an already-cached one (Global Constraints)
# ===========================================================================


def test_auth0_unknown_kid_refetches_once_then_401(rsa_keys):
    key1, pub1, _key2, _pub2 = rsa_keys
    transport = JwksTransport([jwk_for(pub1, "key-1")])
    provider = _auth0_provider(transport)
    # Warm the cache first with a legitimate, known kid.
    provider.resolve({"authorization": f"Bearer {mint_auth0_token(key1, 'key-1')}"})
    assert transport.request_count == 1

    token = mint_auth0_token(key1, "totally-unknown-kid")
    err = _expect_auth_error(
        provider, {"authorization": f"Bearer {token}"}, 401, "unknown signing key"
    )

    assert "totally-unknown-kid" in err.detail
    # Exactly one ADDITIONAL fetch for the unknown kid -- never a second
    # retry against a kid this same resolution attempt already refetched
    # for and still did not find.
    assert transport.request_count == 2


def test_auth0_kid_rotation_refetches_once_and_succeeds(rsa_keys):
    """The positive mirror of the unknown-kid test above: a LEGITIMATE key
    the tenant rotates in after the provider's cache was already warm
    still verifies, via the exact same one-refetch mechanism -- rotation
    must not require restarting the process."""
    key1, pub1, key2, pub2 = rsa_keys
    transport = JwksTransport([jwk_for(pub1, "key-1")])
    provider = _auth0_provider(transport)
    provider.resolve({"authorization": f"Bearer {mint_auth0_token(key1, 'key-1')}"})
    assert transport.request_count == 1

    transport.keys.append(jwk_for(pub2, "key-2"))
    token = mint_auth0_token(key2, "key-2")

    user = provider.resolve({"authorization": f"Bearer {token}"})

    assert user.sub == "auth0|user123"
    assert transport.request_count == 2


def test_auth0_cached_kid_never_refetches(rsa_keys):
    key1, pub1, _key2, _pub2 = rsa_keys
    transport = JwksTransport([jwk_for(pub1, "key-1")])
    provider = _auth0_provider(transport)
    provider.resolve({"authorization": f"Bearer {mint_auth0_token(key1, 'key-1')}"})
    assert transport.request_count == 1

    provider.resolve({"authorization": f"Bearer {mint_auth0_token(key1, 'key-1')}"})

    assert transport.request_count == 1


# ===========================================================================
# core/config.py -- Phase 9 Task 3's new Settings field: spcs_sales_users
# ===========================================================================


def test_spcs_sales_users_defaults_to_empty_list():
    """The fail-closed default (core/config.py's own field comment): an
    operator must explicitly configure this allowlist before ANY
    spcs_ingress identity is granted Poseidon:Sales."""
    assert _settings().spcs_sales_users == []


def test_spcs_sales_users_accepts_a_comma_separated_string():
    """The ergonomic shape for a plain .env/compose file -- the identical
    parse ``cors_allow_origins`` already established (core/config.py's
    ``split_spcs_sales_users`` validator)."""
    settings = _settings(spcs_sales_users="alice,bob")
    assert settings.spcs_sales_users == ["alice", "bob"]


def test_spcs_sales_users_strips_whitespace_around_commas():
    settings = _settings(spcs_sales_users=" alice , bob ")
    assert settings.spcs_sales_users == ["alice", "bob"]


def test_spcs_sales_users_accepts_the_wildcard_without_rejection():
    """Unlike ``cors_allow_origins``, "*" is a real, meaningful member here
    (Global Constraints: "* = everyone gets Poseidon:Sales") -- no
    wildcard-rejecting validator applies to this field."""
    settings = _settings(spcs_sales_users="*")
    assert settings.spcs_sales_users == ["*"]


def test_spcs_sales_users_accepts_a_list_literal_directly():
    settings = _settings(spcs_sales_users=["alice", "bob"])
    assert settings.spcs_sales_users == ["alice", "bob"]


# ===========================================================================
# SpcsIngressProvider -- Sf-Context-Current-User trusted ONLY under
# deploy_mode="spcs" (Phase 9 Task 3, doc 05 section 2's "config choice,
# recorded per environment")
# ===========================================================================


def test_sanitize_username_is_the_one_shared_rule_both_providers_call():
    """Direct proof of the extracted helper itself (core/identity.py),
    independent of which provider calls it -- DisabledProvider's own
    act-as matrix above already exercises it indirectly; this pins the
    function's own contract once, by name."""
    assert sanitize_username("Alice") == "alice"
    assert sanitize_username("not valid!") is None


@pytest.mark.parametrize("deploy_mode", ["local", "ec2"])
def test_spcs_provider_raises_at_construction_outside_spcs_deploy_mode(deploy_mode):
    """Global Constraints: "spcs_ingress outside spcs deploy mode -> hard
    boot error (fail-fast, pinned)". Sf-Context-Current-User is injected
    by the Snowflake platform ingress edge and is otherwise just an
    ordinary, caller-settable header -- trusting it under any other
    deploy_mode would let any caller mint an arbitrary identity by simply
    setting that header themselves. Raised from the CONSTRUCTOR (not
    merely inside resolve()) so this fails at boot, the same call
    resolve_provider makes once from api/app.py's create_app."""
    with pytest.raises(RuntimeError) as exc_info:
        SpcsIngressProvider(_spcs_settings(deploy_mode=deploy_mode))

    assert str(exc_info.value) == (
        "identity_mode=spcs_ingress requires deploy_mode='spcs', got "
        f"{deploy_mode!r}; Sf-Context-Current-User is trustworthy "
        "only behind the Snowflake platform ingress edge and must never be "
        "trusted outside it"
    )


def test_spcs_provider_constructs_cleanly_under_spcs_deploy_mode():
    SpcsIngressProvider(_spcs_settings())  # must not raise


def test_spcs_header_resolves_to_sf_prefixed_sub():
    user = SpcsIngressProvider(_spcs_settings()).resolve({"sf-context-current-user": "alice"})
    assert user.sub == "sf|alice"


def test_spcs_header_is_casefolded():
    user = SpcsIngressProvider(_spcs_settings()).resolve({"sf-context-current-user": "ALICE"})
    assert user.sub == "sf|alice"


def test_spcs_email_and_name_are_not_fabricated():
    """Judgment call (disclosed in this task's report): unlike
    DisabledProvider's synthetic per-act-as email/name, SpcsIngressProvider
    has no real claim source for either -- Sf-Context-Current-User carries
    a bare username and nothing else -- so both stay None rather than
    inventing values this provider cannot actually vouch for."""
    user = SpcsIngressProvider(_spcs_settings()).resolve({"sf-context-current-user": "alice"})
    assert user.email is None
    assert user.name is None


def test_spcs_missing_header_is_401():
    err = _expect_auth_error(
        SpcsIngressProvider(_spcs_settings()), {}, 401, "missing spcs identity header"
    )
    assert err.detail == "no valid Sf-Context-Current-User header"


@pytest.mark.parametrize(
    "raw",
    [
        "",  # too short (min 1)
        "a" * 65,  # too long (max 64)
        "alice bob",  # space is not in [a-z0-9_-]
        "alice!",  # punctuation
        "alice.bob",  # dot
        "alice/bob",  # slash
        "  alice",  # leading whitespace
        "alice\t",  # trailing tab
    ],
)
def test_spcs_malformed_header_is_401_identically_to_missing(raw):
    """This task's disclosed resolution of the brief's stated ambiguity: a
    header that is PRESENT but fails the same [a-z0-9_-]{1,64} sanitize
    rule DisabledProvider's own X-Dev-User act-as uses is a malformed
    identity from a supposedly trusted edge -- treated exactly like the
    header being absent (the SAME AuthError), never a silent fallback
    (there is no default identity to fall back to in this mode -- unlike
    DisabledProvider, whose whole point is "never block local dev")."""
    provider = SpcsIngressProvider(_spcs_settings())
    err = _expect_auth_error(
        provider, {"sf-context-current-user": raw}, 401, "missing spcs identity header"
    )
    assert err.detail == "no valid Sf-Context-Current-User header"


def test_spcs_allowlisted_user_gets_the_sales_role():
    provider = SpcsIngressProvider(_spcs_settings(spcs_sales_users="alice,bob"))
    user = provider.resolve({"sf-context-current-user": "alice"})
    assert user.roles == ("Poseidon:Sales",)


def test_spcs_non_allowlisted_user_gets_no_role():
    """Global Constraints: "non-member -> authenticated UserContext
    WITHOUT the role" -- the existing require_sales dependency (api/
    auth.py) is what turns this into a 403; this provider never builds a
    second role check of its own (proven end to end in test_api_auth.py)."""
    provider = SpcsIngressProvider(_spcs_settings(spcs_sales_users="alice,bob"))
    user = provider.resolve({"sf-context-current-user": "carol"})
    assert user.roles == ()
    assert user.sub == "sf|carol"


def test_spcs_wildcard_allowlist_grants_everyone():
    provider = SpcsIngressProvider(_spcs_settings(spcs_sales_users="*"))
    user = provider.resolve({"sf-context-current-user": "anybody"})
    assert user.roles == ("Poseidon:Sales",)


def test_spcs_empty_allowlist_grants_no_one():
    """The safe, fail-closed default (core/config.py's own
    spcs_sales_users default is an empty list)."""
    provider = SpcsIngressProvider(_spcs_settings(spcs_sales_users=[]))
    user = provider.resolve({"sf-context-current-user": "alice"})
    assert user.roles == ()


def test_spcs_allowlist_membership_is_casefolded():
    """Config entries and the header value are compared casefolded on
    BOTH sides (disclosed judgment call): an operator typing "Alice" in
    SPCS_SALES_USERS must still match the header's actual casing,
    whatever it happens to be."""
    provider = SpcsIngressProvider(_spcs_settings(spcs_sales_users="Alice"))
    user = provider.resolve({"sf-context-current-user": "ALICE"})
    assert user.roles == ("Poseidon:Sales",)
    assert user.sub == "sf|alice"


# ===========================================================================
# resolve_provider -- mode selection, fail-fast on anything not implemented
# ===========================================================================


def test_resolve_provider_disabled_mode_returns_a_disabled_provider():
    provider = resolve_provider(_settings(identity_mode="disabled"))
    assert isinstance(provider, DisabledProvider)


def test_disabled_mode_boots_quietly_under_local_deploy_mode(capsys):
    """M-2 (phase 9 final review): the common, intentional case -- every
    existing test/env -- must stay quiet (no WARNING line) even though the
    new check below now runs on every ``disabled``-mode boot."""
    resolve_provider(_settings(identity_mode="disabled", deploy_mode="local"))
    assert "WARNING" not in capsys.readouterr().out


def test_disabled_mode_boot_warns_outside_local_deploy_mode(capsys):
    """M-2: ``identity_mode="disabled"`` boots in ANY ``deploy_mode``,
    correctly -- an operator may legitimately want it on a throwaway EC2
    box (see this function's own docstring) -- but forgetting
    ``IDENTITY_MODE`` on a real deploy used to produce only the same
    one-line ``"identity mode: disabled"`` boot log as the intentional
    case, with no way to tell them apart. A loud ``WARNING`` line closes
    that asymmetry without failing boot -- unlike ``SpcsIngressProvider``'s
    own hard error for the symmetric ``spcs_ingress``-outside-``spcs``
    mistake, which genuinely cannot be a legitimate configuration."""
    resolve_provider(_settings(identity_mode="disabled", deploy_mode="ec2"))
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "disabled" in out
    assert "ec2" in out


def test_resolve_provider_defaults_to_disabled_when_unset():
    """``identity_mode`` defaults to "disabled" (core/config.py) -- omitting
    it entirely must resolve identically to passing it explicitly."""
    provider = resolve_provider(_settings())
    assert isinstance(provider, DisabledProvider)


def test_resolve_provider_auth0_mode_returns_an_auth0_provider():
    """Phase 9 Task 2's own handoff: replaces the fail-fast stub Task 1
    left for "auth0" with the real branch. ``spcs_ingress`` still fails
    fast below -- that provider ships in Task 3."""
    provider = resolve_provider(_auth0_settings())
    assert isinstance(provider, Auth0Provider)


def test_resolve_provider_spcs_ingress_mode_returns_a_spcs_provider():
    """Phase 9 Task 3's own handoff: replaces the fail-fast stub Task 1/2
    left for "spcs_ingress" with the real branch -- the LAST of the three
    modes resolve_provider's own docstring names."""
    provider = resolve_provider(_spcs_settings())
    assert isinstance(provider, SpcsIngressProvider)


def test_resolve_provider_spcs_ingress_fails_fast_outside_spcs_deploy_mode():
    """The boot fail-fast, exercised through resolve_provider itself (the
    SAME call api/app.py's create_app makes at boot) -- not only through
    SpcsIngressProvider's own constructor test above."""
    with pytest.raises(RuntimeError, match="deploy_mode='spcs'"):
        resolve_provider(_spcs_settings(deploy_mode="local"))


def test_resolve_provider_fails_fast_for_a_genuinely_unknown_mode():
    """Defensive belt for Settings' own Literal suspenders (resolve_
    provider's own docstring). Before Task 3, this SAME fail-fast branch
    was reachable through a real, valid Settings(identity_mode=
    "spcs_ingress") value (the now-retired test this one replaces); as of
    Task 3, every Literal value a real Settings instance can actually
    carry resolves to a real provider, so the branch is reachable only
    through a non-Settings stand-in -- a plain object Settings' own type
    system could never produce."""
    from types import SimpleNamespace

    with pytest.raises(RuntimeError, match="bogus"):
        resolve_provider(SimpleNamespace(identity_mode="bogus"))


# ===========================================================================
# ASCII-only source
# ===========================================================================


def test_identity_module_files_are_ascii_on_disk():
    """Phase 9 Task 2 adds ``identity_auth0.py``; Task 3 adds
    ``identity_spcs.py`` -- both wholly their own task's own, checked in
    full, like ``identity.py`` itself."""
    from poseidon.core import identity_auth0 as identity_auth0_module
    from poseidon.core import identity_spcs as identity_spcs_module

    paths = (
        Path(identity_module.__file__),
        Path(identity_auth0_module.__file__),
        Path(identity_spcs_module.__file__),
        Path(__file__),
    )
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
