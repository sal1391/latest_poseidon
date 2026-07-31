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
    # Phase 9 Task 3 (doc 05 section 2, decision D22): spcs_ingress's own
    # role allowlist -- the "config choice, recorded per environment" doc
    # 05 section 2 calls for (the alternative it names but this task does
    # NOT implement is a live Snowflake-role lookup at login). Comma-
    # separated in a real .env/compose file -- the same ergonomic string
    # shape cors_allow_origins already uses, split by this field's own
    # split_spcs_sales_users validator below; a Python list literal (a
    # test's direct keyword override) passes through unchanged. "*" is a
    # real, meaningful member (Global Constraints: "* = everyone gets
    # Poseidon:Sales"), so unlike cors_allow_origins there is no
    # wildcard-rejecting validator for this field. Defaults to an empty
    # list: the fail-closed choice -- an operator must explicitly name at
    # least one user (or "*") before ANY spcs_ingress identity is granted
    # Poseidon:Sales; core/identity_spcs.py's SpcsIngressProvider resolves
    # an unconfigured allowlist as "authenticated, role-less" rather than
    # a boot error, since an empty allowlist is a valid, if inert,
    # environment, not a misconfiguration.
    spcs_sales_users: list[str] = []
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
    # ToolServerRegistry (poseidon/mcp/registry.py) to resolve the research
    # tool's transport -- "direct" (the in-house REST adapter, default) or
    # "mcp" (the MCP-transport client). Both fields predate this task
    # (scaffolded in the initial commit); the transports themselves ship in
    # Task 2 (direct) and Task 3 (mcp).
    tool_transport_perplexity: Literal["direct", "mcp"] = "direct"
    perplexity_api_key: str | None = None
    memory_max_chars: int = 8000
    memory_keep_versions: int = 20
    # Phase 6 Task 4: which chat HTTP surface poseidon.api.app.create_app mounts.
    # "mock" is the default so every existing env/test keeps today's scripted
    # demo (mock_chat.py) with zero config changes; an operator opts into the
    # real execute_turn pipeline (live_chat.py) by setting CHAT_MODE=live.
    chat_mode: Literal["mock", "live"] = "mock"
    # Phase 9 Task 2 (doc 05 section 2): the chat-send POST's token-bucket
    # rate limit, keyed by sub (api/auth.py's ChatRateLimiter). ``None``
    # (the unset default) resolves MODE-DEPENDENTLY -- see
    # effective_rate_limit_chat_per_minute below -- rather than a plain
    # numeric default, so "0 = off, and OFF in disabled mode by default"
    # (Global Constraints) can hold at once: 0 in identity_mode="disabled"
    # (no existing dev/test flow is ever rate-limited by surprise), 30 in
    # every other mode. An operator's EXPLICIT override -- including 0,
    # which always means off -- is honored in every mode, not only auth0/
    # spcs_ingress: a judgment call (disclosed in this task's report)
    # favoring one flexible mechanism over a rigid "disabled mode never
    # rate-limits, full stop" rule that would make local rate-limit testing
    # impossible.
    rate_limit_chat_per_minute: int | None = None
    # Phase 9 Task 2: explicit CORS allowlist. Defaults to the Vite dev
    # origin so localhost:5173 keeps working with zero configuration.
    cors_allow_origins: list[str] = ["http://localhost:5173"]

    @field_validator("database_url", "s3_bucket")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def split_cors_origins(cls, v: object) -> object:
        """Env vars (and dotenv values) are always plain strings -- a
        comma-separated ``CORS_ALLOW_ORIGINS=http://a,http://b`` is the
        ergonomic shape for a real ``.env``/compose file, far simpler to
        author than a JSON array literal. A value that is already a list
        (this field's own Python-literal default, or a direct keyword
        override from a test) passes through unchanged.
        """
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @field_validator("cors_allow_origins")
    @classmethod
    def no_wildcard_cors_origin(cls, v: list[str]) -> list[str]:
        """Global Constraints: "never * with credentials". Starlette's own
        CORSMiddleware technically works around a literal "*" origin by
        reflecting the caller's actual Origin header back when
        allow_credentials is True (verified by reading its source) --
        which defeats the allowlist's whole purpose (ANY origin is then
        treated as allowed) even though it avoids the literal browser-side
        "*"-plus-credentials rejection. Rejected here, at boot, rather than
        trusted to never be misconfigured later -- the same fail-fast
        discipline this file's own docstring pins for every other
        malformed value.
        """
        if "*" in v:
            raise ValueError(
                "cors_allow_origins must not contain '*' -- this app always "
                "sends credentials (api/app.py's CORSMiddleware wiring)"
            )
        return v

    @field_validator("spcs_sales_users", mode="before")
    @classmethod
    def split_spcs_sales_users(cls, v: object) -> object:
        """The identical ergonomic string-to-list parse ``split_cors_
        origins`` above already established for env vars (always plain
        strings) -- kept as this field's OWN validator, matching this
        file's one-validator-per-field convention, rather than extracting
        a shared helper neither field asked for. Unlike CORS's origin
        allowlist, "*" is a valid, meaningful member here (Global
        Constraints: "* = everyone gets Poseidon:Sales" -- doc 05 section
        2's "config choice, recorded per environment"), so there is no
        wildcard-rejecting counterpart to ``no_wildcard_cors_origin``
        above for this field.
        """
        if isinstance(v, str):
            return [name.strip() for name in v.split(",") if name.strip()]
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

    @property
    def effective_rate_limit_chat_per_minute(self) -> int:
        """Resolves ``rate_limit_chat_per_minute``'s mode-dependent default
        -- see that field's own comment for the judgment call this method
        implements. An explicit value (including 0) always wins; ``None``
        (unset) resolves to 0 in ``disabled`` mode, 30 otherwise.
        """
        if self.rate_limit_chat_per_minute is not None:
            return self.rate_limit_chat_per_minute
        return 0 if self.identity_mode == "disabled" else 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
