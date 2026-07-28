import os
from functools import lru_cache
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment contract — docs/architecture/07-infrastructure.md §6.

    Startup crashes on any missing or malformed value: no half-configured
    server ever accepts traffic.
    """

    # POSEIDON_ENV_FILE selects the dotenv to read; set it to "" to read none.
    # Containers bind-mount the repo, so a host `backend/.env` would otherwise
    # shadow the environment the orchestrator passes in — compose sets it empty.
    model_config = SettingsConfigDict(
        env_file=os.getenv("POSEIDON_ENV_FILE", ".env"), extra="ignore")

    deploy_mode: Literal["local", "spcs", "ec2"] = "local"
    database_url: str
    s3_endpoint_url: str | None = None
    s3_bucket: str
    data_backend: Literal["synthetic", "snowflake"] = "synthetic"
    identity_mode: Literal["disabled", "auth0", "spcs_ingress"] = "disabled"
    auth0_domain: str | None = None
    auth0_audience: str | None = None
    auth0_client_id: str | None = None
    llm_profile: Literal["bedrock", "cortex"] = "bedrock"
    llm_mode: Literal["stub", "live"] = "stub"
    tool_transport_perplexity: Literal["direct", "mcp"] = "direct"
    perplexity_api_key: str | None = None
    memory_max_chars: int = 8000
    memory_keep_versions: int = 20

    @field_validator("database_url", "s3_bucket")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @model_validator(mode="after")
    def auth0_fields_required_in_auth0_mode(self) -> "Settings":
        if self.identity_mode == "auth0":
            missing = [
                name for name in ("auth0_domain", "auth0_audience", "auth0_client_id")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"identity_mode=auth0 requires: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
