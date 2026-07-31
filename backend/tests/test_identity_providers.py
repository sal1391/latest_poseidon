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
"""

from pathlib import Path

import pytest

from poseidon.core import identity as identity_module
from poseidon.core.config import Settings
from poseidon.core.identity import (
    DISABLED_DEFAULT_USER,
    AuthError,
    DisabledProvider,
    UserContext,
    resolve_provider,
)

_PLACEHOLDER_DSN = "postgresql+psycopg://nobody:nope@127.0.0.1:1/void"


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
# resolve_provider -- mode selection, fail-fast on anything not implemented
# ===========================================================================


def test_resolve_provider_disabled_mode_returns_a_disabled_provider():
    provider = resolve_provider(_settings(identity_mode="disabled"))
    assert isinstance(provider, DisabledProvider)


def test_resolve_provider_defaults_to_disabled_when_unset():
    """``identity_mode`` defaults to "disabled" (core/config.py) -- omitting
    it entirely must resolve identically to passing it explicitly."""
    provider = resolve_provider(_settings())
    assert isinstance(provider, DisabledProvider)


@pytest.mark.parametrize("mode", ["auth0", "spcs_ingress"])
def test_resolve_provider_fails_fast_for_modes_with_no_provider_yet(mode, monkeypatch):
    """Auth0Provider/SpcsIngressProvider ship in Phase 9 Tasks 2/3 -- until
    then, selecting either recognized-but-unimplemented mode must fail
    loudly at the SAME call this task's own "disabled" branch succeeds at,
    never silently fall back to the disabled provider. auth0_domain/
    audience/client_id are set so Settings' own validator does not raise
    first for an unrelated reason (this test is about resolve_provider,
    not about Settings' own auth0-fields-required check)."""
    settings = _settings(
        identity_mode=mode,
        auth0_domain="tenant.auth0.test",
        auth0_audience="https://api.test",
        auth0_client_id="client-id",
    )
    with pytest.raises(RuntimeError, match=mode):
        resolve_provider(settings)


# ===========================================================================
# ASCII-only source
# ===========================================================================


def test_identity_module_files_are_ascii_on_disk():
    paths = (Path(identity_module.__file__), Path(__file__))
    for path in paths:
        offending = sorted({byte for byte in path.read_bytes() if byte > 0x7F})
        assert not offending, f"{path.name} holds non-ASCII bytes: {offending}"
