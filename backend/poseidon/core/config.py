import os
from functools import lru_cache
from pathlib import Path
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
        env_file=os.getenv("POSEIDON_ENV_FILE", ".env"), extra="ignore"
    )

    deploy_mode: Literal["local", "spcs", "ec2"] = "local"
    database_url: str
    s3_endpoint_url: str | None = None
    s3_bucket: str
    # Static credentials for the local MinIO dev stack only (doc 07 §6 names no
    # such variables for SPCS/EC2 — those authenticate via OAuth token / IAM
    # instance profile). Optional: ``None`` lets boto3 fall back to its normal
    # credential chain, which is exactly what a non-local habitat needs.
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    data_backend: Literal["synthetic", "snowflake"] = "synthetic"
    identity_mode: Literal["disabled", "auth0", "spcs_ingress"] = "disabled"
    auth0_domain: str | None = None
    auth0_audience: str | None = None
    auth0_client_id: str | None = None
    llm_profile: Literal["bedrock", "cortex"] = "bedrock"
    llm_mode: Literal["stub", "live"] = "stub"
    # Phase 5 (doc 03 sections 1-2): LLM role routing. ``None`` defers to the
    # packaged default resolved by the LLM layer itself
    # (core/llm/roles.py's DEFAULT_MODELS_PATH) -- this file stays a pure
    # environment contract with no filesystem knowledge of its own.
    models_path: Path | None = None
    prompts_dir: Path | None = None
    # Agent loop iteration cap (doc 03 section 3): generous enough for a
    # data_qa/research tool chain, finite so a self-correction loop can't
    # spin forever.
    agent_max_iterations: int = 10
    # Phase 7 Task 1 (doc 02 section 7, decision D23): read by
    # ToolServerRegistry (mcp/registry.py) to resolve the research tool's
    # transport -- "direct" (the in-house REST adapter, default) or "mcp"
    # (the MCP-transport client). Both fields predate this task (scaffolded
    # in the initial commit); the transports themselves ship in Task 2
    # (direct) and Task 3 (mcp).
    tool_transport_perplexity: Literal["direct", "mcp"] = "direct"
    perplexity_api_key: str | None = None
    memory_max_chars: int = 8000
    memory_keep_versions: int = 20
    # Phase 6 Task 4: which chat HTTP surface poseidon.api.app.create_app mounts.
    # "mock" is the default so every existing env/test keeps today's scripted
    # demo (mock_chat.py) with zero config changes; an operator opts into the
    # real execute_turn pipeline (live_chat.py) by setting CHAT_MODE=live.
    chat_mode: Literal["mock", "live"] = "mock"

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
                name
                for name in ("auth0_domain", "auth0_audience", "auth0_client_id")
                if not getattr(self, name)
            ]
            if missing:
                raise ValueError(f"identity_mode=auth0 requires: {', '.join(missing)}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
