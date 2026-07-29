"""The config-driven LLM provider layer (doc 03): normalized types, the
model-profile-to-role resolver, and the stub/live provider seam.

Phase 5 builds this package task by task; this ``__init__`` re-exports
whatever each task adds so a caller never needs to know which submodule a
name lives in. As of Task 1: the normalized response types, the role
client, and the stub provider. ``PromptRegistry`` (Task 2), the Bedrock
provider (Task 3), and the agent loop's ``run_turn`` (Task 4) join this
list as their tasks land.

Importing this package has no side effects: ``RoleClient`` only reads
``models.yml`` when constructed, not at import time.
"""

from .roles import LLMProvider, ModelProfileError, RoleClient, RoleConfig, load_model_profiles
from .stub import StubProvider
from .types import LLMResponse, ToolCall

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "ModelProfileError",
    "RoleClient",
    "RoleConfig",
    "StubProvider",
    "ToolCall",
    "load_model_profiles",
]
