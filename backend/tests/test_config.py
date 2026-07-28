import pytest
from pydantic import ValidationError


REQUIRED = {
    "DATABASE_URL": "postgresql+psycopg://x:x@localhost:5432/poseidon",
    "S3_BUCKET": "poseidon-artifacts",
}


def make_settings(monkeypatch, **overrides):
    from poseidon.core.config import Settings

    for key in ("DATABASE_URL", "S3_BUCKET", "IDENTITY_MODE", "AUTH0_DOMAIN",
                "AUTH0_AUDIENCE", "AUTH0_CLIENT_ID", "LLM_MODE", "DEPLOY_MODE"):
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
        monkeypatch, IDENTITY_MODE="auth0", AUTH0_DOMAIN="dev.us.auth0.com",
        AUTH0_AUDIENCE="https://poseidon/api", AUTH0_CLIENT_ID="abc123",
    )
    assert s.identity_mode == "auth0"
