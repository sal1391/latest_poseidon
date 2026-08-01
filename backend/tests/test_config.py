import pytest
from pydantic import ValidationError

REQUIRED = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}


def make_settings(monkeypatch, **overrides):
    from poseidon.core.config import Settings

    # Hermetic: clear EVERY Settings env var (derived, so the list can't drift)
    for key in (name.upper() for name in Settings.model_fields):
        monkeypatch.delenv(key, raising=False)
    env = {**REQUIRED, **overrides}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_defaults_are_local_and_stub(monkeypatch):
    s = make_settings(monkeypatch)
    assert s.deploy_mode == "local"
    assert s.identity_mode == "disabled"
    assert s.llm_mode == "stub"
    assert s.data_backend == "synthetic"
    assert s.memory_max_chars == 8000
    assert s.chat_mode == "mock"
    assert s.database_app_role == "poseidon_app"


def test_database_app_role_accepts_none(monkeypatch):
    """A deploy whose DSN already authenticates as a non-privileged role
    (doc 05 section 4's expected shape) opts out of the SET LOCAL ROLE
    rls_transaction would otherwise issue -- see core/db.py and
    Settings.database_app_role's own comment."""
    s = make_settings(monkeypatch, DATABASE_APP_ROLE="")
    assert s.database_app_role is None


def test_chat_mode_live_is_accepted(monkeypatch):
    s = make_settings(monkeypatch, CHAT_MODE="live")
    assert s.chat_mode == "live"


def test_chat_mode_malformed_crashes(monkeypatch):
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, CHAT_MODE="turbo")


def test_missing_database_url_crashes(monkeypatch):
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, DATABASE_URL="")  # empty string is malformed


def test_malformed_enum_crashes(monkeypatch):
    with pytest.raises(ValidationError):
        make_settings(monkeypatch, DEPLOY_MODE="cloud")


def test_auth0_mode_requires_auth0_fields(monkeypatch):
    with pytest.raises(ValidationError) as err:
        make_settings(monkeypatch, IDENTITY_MODE="auth0")
    assert "auth0_domain" in str(err.value)


def test_auth0_mode_valid_when_fields_present(monkeypatch):
    s = make_settings(
        monkeypatch,
        IDENTITY_MODE="auth0",
        AUTH0_DOMAIN="dev.us.auth0.com",
        AUTH0_AUDIENCE="https://poseidon/api",
        AUTH0_CLIENT_ID="abc123",
    )
    assert s.identity_mode == "auth0"
